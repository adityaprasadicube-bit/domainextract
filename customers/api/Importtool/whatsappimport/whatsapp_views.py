"""
WhatsApp File Upload View Handler - OPTIMIZED VERSION

Key performance improvements over merged version:
  1. lxml parser replaces html.parser  — 3-5x faster BeautifulSoup on large files
  2. No FileSystemStorage disk I/O     — uploaded bytes stay in RAM end-to-end
  3. soup + get_text() computed once   — passed into detect_content_type() and
                                         msg processor, never re-parsed
  4. WhatsAppNexus.objects().first()   — hoisted out of the connection loop
  5. _flush_nexus_totals()             — single implementation on a shared mixin,
                                         no copy-paste between the two view classes
  6. Archive streaming                 — content files processed as they are
                                         extracted; list() accumulation removed
  7. xxhash IDs precomputed once       — nexus_id / crime_hash not recomputed
                                         inside per-record loops
"""

import os
import re
import zipfile
import io
import shutil
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from .db_connect import client as mongo_client, bulk_insert
from ...Whatsapp.whatsapp_models.whatsapp_models import WhatsAppNexus

try:
    import rarfile as _rarfile_module
    _RAR_AVAILABLE = True
except ImportError:
    _rarfile_module = None
    _RAR_AVAILABLE = False

import xxhash
from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

# ── lxml is the critical speed-up for large HTML files ──────────────────────
try:
    from bs4 import BeautifulSoup as _BS
    def BeautifulSoup(markup, _parser='lxml'):
        """Always use lxml; fall back to html.parser if lxml is not installed."""
        try:
            return _BS(markup, 'lxml')
        except Exception:
            return _BS(markup, 'html.parser')
except ImportError:
    raise ImportError("beautifulsoup4 is required: pip install beautifulsoup4 lxml")

from .wp_msg import WhatsAppMessageProcessor
from .wp_call import WhatsAppCallProcessor
from .wp_txtcall import WhatsAppTXTCallProcessor
from .wp_txtmsg import (
    _extract_messages_from_txt_lines,
    _apply_message_target_swapping,
)
from ..utils.user_data import UserDetails


def get_user_id():
    return UserDetails().user()


# ======================================================================
#  PHONE NUMBER PARSING UTILITY
# ======================================================================

def clean_phone_number(phone: str) -> str:
    """
    Clean phone numbers by removing .0 suffix and non-digit characters.
    Handles 13-digit numbers caused by .0 suffix conversion.
    """
    if not phone:
        return ''

    # Convert to string and remove .0 suffix
    phone = str(phone)
    if phone.endswith('.0'):
        phone = phone[:-2]

    # Remove all non-digit characters
    digits = re.sub(r'\D', '', phone)

    # Handle 13-digit numbers (91xxxxxxxxx + trailing 0 from .0)
    if len(digits) == 13 and digits.startswith('91'):
        digits = digits[:-1]

    # Handle 11-digit numbers starting with 0
    if len(digits) == 11 and digits.startswith('0'):
        digits = digits[1:]

    return digits

def parse_phone_number(phone: str) -> tuple:
    """
    Parse a single phone number and return (cleaned_number, 4digit_code)

    Returns:
        tuple: (cleaned_number, four_digit_code)
    """
    if not phone:
        return '', ''

    # Clean the number first
    cleaned = clean_phone_number(phone)

    # Extract 4-digit code if it's a valid Indian number
    code = ''
    if len(cleaned) == 10 and cleaned.isdigit() and cleaned[0] in '6789':
        code = cleaned[:4]
    elif len(cleaned) == 12 and cleaned.startswith('91') and cleaned[2] in '6789':
        code = cleaned[2:6]

    return cleaned, code

def parse_number_series(numbers: list) -> tuple:
    """
    Parse a list of phone numbers and return (cleaned_numbers_list, codes_list)
    Useful for batch processing of contact lists.

    Args:
        numbers: List of phone number strings

    Returns:
        tuple: (list of cleaned numbers, list of 4-digit codes)
    """
    cleaned_numbers = []
    codes = []

    for number in numbers:
        cleaned, code = parse_phone_number(number)
        cleaned_numbers.append(cleaned)
        codes.append(code)

    return cleaned_numbers, codes


# ======================================================================
#  NEXUS UPSERT  (unchanged logic — already correct in merged v4)
# ======================================================================

def upsert_nexus(db_name: str, collection: str, nexus_id: str,
                 set_fields: dict, inc_inserted: int = 0, inc_duplicates: int = 0):
    if mongo_client is None:
        print(f"   upsert_nexus: mongo_client is None — nexus NOT written for _id={nexus_id}")
        return
    try:
        coll = mongo_client[db_name][collection]
        on_insert_keys = {
            'CrimeName', 'RecordType', 'TargetNo', 'Target_code',
            'Day', 'Month', 'Year', 'CreatedAt', 'AreaLocation', 'CrimeID',
        }
        on_insert_fields = {k: v for k, v in set_fields.items() if k in on_insert_keys}
        always_set_fields = {k: v for k, v in set_fields.items()
                             if k not in on_insert_keys and k not in ('_id', 'FromDate', 'ToDate')}

        update = {}
        if on_insert_fields:
            update['$setOnInsert'] = on_insert_fields
        if always_set_fields:
            update['$set'] = always_set_fields
        from_date_val = set_fields.get('FromDate')
        if from_date_val is not None:
            update.setdefault('$min', {})['FromDate'] = from_date_val
        to_date_val = set_fields.get('ToDate')
        if to_date_val is not None:
            update.setdefault('$max', {})['ToDate'] = to_date_val
        if inc_inserted or inc_duplicates:
            update['$inc'] = {}
            if inc_inserted:
                update['$inc']['Inserted'] = inc_inserted
            if inc_duplicates:
                update['$inc']['Duplicates'] = inc_duplicates

        if not update:
            return
        result = coll.update_one({'_id': nexus_id}, update, upsert=True)
        action = 'created' if result.upserted_id else 'updated'
        print(f"   Nexus {action} (_id={nexus_id} +{inc_inserted} inserted +{inc_duplicates} dupes)")
    except Exception as exc:
        import traceback
        print(f"   upsert_nexus FAILED (_id={nexus_id}): {exc}")
        traceback.print_exc()


# ======================================================================
#  HELPERS
# ======================================================================

def generate_nexus_id(crime_name=None, target_no=None, user_id=None):
    base = f"{crime_name or ''}{target_no or ''}_{user_id or ''}"
    return xxhash.xxh64(base.lower().strip()).hexdigest()


def detect_file_format(file_content):
    lower = file_content[:2000].lower()  # only scan the header, not the whole file
    if '<html' in lower or '<!doctype' in lower:
        return 'html'
    txt_indicators = [
        'Service\tWhatsApp', 'Account Identifier\t',
        'Message Log\t', 'Call Log\t', 'Message\t', 'Call\t',
    ]
    for indicator in txt_indicators:
        if indicator in file_content:
            return 'txt'
    return 'html'


def detect_content_type(file_content, file_format, text_cache=None):
    """
    OPTIMIZED: accepts pre-extracted text so soup.get_text() is never called twice.
    text_cache should be the string already extracted by the caller.
    """
    result = {
        'has_messages':    False,
        'has_calls':       False,
        'has_connections': False,
        'has_groups':      False,
        'has_contacts':    False,
    }
    text = text_cache if text_cache is not None else file_content

    if file_format == 'txt':
        result['has_messages']    = 'Message Log' in text or '\nMessage\n' in text or '\tMessage\t' in text
        result['has_calls']       = 'Call Log' in text or '\nCall\n' in text or '\tCall\t' in text
        result['has_connections'] = 'Connection' in text and 'Device Id' in text
        result['has_groups']      = 'Owned Groups' in text or ('Groups' in text and 'Subject' in text)
        result['has_contacts']    = 'Symmetric contacts' in text or 'Asymmetric contacts' in text
    else:
        result['has_messages']    = 'Message Log' in text or 'Message\n' in text
        result['has_calls']       = 'Call Log' in text or 'Call\n' in text
        result['has_connections'] = 'Connection' in text and 'Device Id' in text
        result['has_groups']      = 'Owned Groups' in text or ('Groups' in text and 'Subject' in text)
        result['has_contacts']    = 'Symmetric contacts' in text or 'Asymmetric contacts' in text
    return result


class MockSoup:
    def __init__(self, text_lines):
        self.text_lines = text_lines

    def get_text(self, separator='\n'):
        return separator.join(self.text_lines)

    def find_all(self, *args, **kwargs):
        return []


# ======================================================================
#  ARCHIVE HELPERS  (ZIP + RAR, fully recursive / nested)
# ======================================================================

_ARCHIVE_EXTS = {'.zip', '.rar'}
_CONTENT_EXTS = {'.html', '.htm', '.txt'}


def _is_archive(name_lower):
    return any(name_lower.endswith(ext) for ext in _ARCHIVE_EXTS)


def _is_content(name_lower):
    return any(name_lower.endswith(ext) for ext in _CONTENT_EXTS)


def _get_rarfile():
    global _RAR_AVAILABLE, _rarfile_module
    if not _RAR_AVAILABLE:
        try:
            import rarfile as _rf
            _rarfile_module = _rf
            _RAR_AVAILABLE = True
        except ImportError:
            return None
    try:
        import unrar.cffi
        _rarfile_module.UNRAR_LIB_PATH = unrar.cffi.lib_path()
        return _rarfile_module
    except Exception:
        pass
    candidates = (
        'unrar', 'UnRAR',
        r'C:\Program Files\WinRAR\UnRAR.exe',
        r'C:\Program Files (x86)\WinRAR\UnRAR.exe',
    )
    for candidate in candidates:
        path = shutil.which(candidate) or (candidate if os.path.isfile(candidate) else None)
        if path:
            _rarfile_module.UNRAR_TOOL = path
            return _rarfile_module
    return _rarfile_module


def extract_files_from_archive(archive_obj, _depth=0, _parent_name='root', _archive_type='zip'):
    """
    OPTIMIZED: yields (name, content) one at a time instead of accumulating
    a full list — callers that process inline don't need to hold everything in RAM.
    Still supports nested archives up to MAX_DEPTH.
    """
    MAX_DEPTH = 10
    indent = '   ' * (_depth + 1)

    if _depth > MAX_DEPTH:
        print(f"{indent}Max archive nesting depth ({MAX_DEPTH}) reached — skipping '{_parent_name}'")
        return

    if isinstance(archive_obj, (bytes, bytearray)):
        raw_archive = archive_obj
        file_obj = io.BytesIO(raw_archive)
    else:
        file_obj = archive_obj
        raw_archive = None

    try:
        if _archive_type == 'rar':
            if raw_archive is None:
                file_obj.seek(0)
                raw_archive = file_obj.read()
            rf_mod = _get_rarfile()
            if rf_mod is None:
                print(f"{indent}RAR '{_parent_name}' skipped — rarfile not installed.")
                return
            with rf_mod.RarFile(io.BytesIO(raw_archive)) as rf:
                entries = rf.infolist()
                for entry in entries:
                    if entry.is_dir():
                        continue
                    try:
                        raw_bytes = rf.read(entry.filename)
                    except Exception as e:
                        print(f"{indent}Could not read RAR entry '{entry.filename}': {e}")
                        continue
                    base_name = os.path.basename(entry.filename)
                    name_lower = base_name.lower()
                    if _is_archive(name_lower):
                        child_type = 'rar' if name_lower.endswith('.rar') else 'zip'
                        yield from extract_files_from_archive(
                            io.BytesIO(raw_bytes), _depth + 1, base_name, child_type)
                    elif _is_content(name_lower):
                        try:
                            yield base_name, raw_bytes.decode('utf-8', errors='ignore')
                        except Exception as e:
                            print(f"{indent}Could not decode '{entry.filename}': {e}")
        else:
            file_obj.seek(0)
            with zipfile.ZipFile(file_obj, 'r') as zf:
                for entry in zf.infolist():
                    if entry.is_dir():
                        continue
                    try:
                        raw_bytes = zf.read(entry.filename)
                    except Exception as e:
                        print(f"{indent}Could not read ZIP entry '{entry.filename}': {e}")
                        continue
                    base_name = os.path.basename(entry.filename)
                    name_lower = base_name.lower()
                    if _is_archive(name_lower):
                        child_type = 'rar' if name_lower.endswith('.rar') else 'zip'
                        yield from extract_files_from_archive(
                            io.BytesIO(raw_bytes), _depth + 1, base_name, child_type)
                    elif _is_content(name_lower):
                        try:
                            yield base_name, raw_bytes.decode('utf-8', errors='ignore')
                        except Exception as e:
                            print(f"{indent}Could not decode '{entry.filename}': {e}")
    except zipfile.BadZipFile as e:
        print(f"{indent}Bad ZIP file '{_parent_name}': {e}")
    except Exception as e:
        print(f"{indent}Could not open archive '{_parent_name}': {e}")


# ======================================================================
#  NEXUS FLUSH MIXIN  — single implementation shared by both views
# ======================================================================

class NexusFlushMixin:
    """
    Provides _flush_nexus_totals() so the logic lives in exactly one place.
    Both WhatsAppFilePathUploadView and WhatsAppInfoloadView inherit from this.
    """

    def _flush_nexus_totals(self, results: list):
        buckets = defaultdict(lambda: {
            'set_fields': {}, 'inc_inserted': 0, 'inc_duplicates': 0,
            'db_name': None, 'collection': None, 'file_names': [],
            'from_date': None, 'to_date': None,
        })

        for result in results:
            payload = result.pop('_nexus_payload', None)
            if not payload:
                continue
            nid = payload['nexus_id']
            bucket = buckets[nid]
            if not bucket['set_fields']:
                bucket['set_fields'] = payload['set_fields']
                bucket['db_name']    = payload['db_name']
                bucket['collection'] = payload['collection']
            bucket['inc_inserted']   += payload.get('inc_inserted',   0)
            bucket['inc_duplicates'] += payload.get('inc_duplicates', 0)
            bucket['file_names'].append(payload.get('file_name', ''))

            fd = payload['set_fields'].get('FromDate')
            td = payload['set_fields'].get('ToDate')
            if fd is not None:
                bucket['from_date'] = fd if bucket['from_date'] is None else min(bucket['from_date'], fd)
            if td is not None:
                bucket['to_date']   = td if bucket['to_date']   is None else max(bucket['to_date'],   td)

        for nid, bucket in buckets.items():
            if not bucket['db_name']:
                continue
            bucket['set_fields']['FileNames'] = bucket['file_names']
            bucket['set_fields']['FileName']  = ', '.join(bucket['file_names'])
            if bucket['from_date'] is not None:
                bucket['set_fields']['FromDate'] = bucket['from_date']
            if bucket['to_date'] is not None:
                bucket['set_fields']['ToDate'] = bucket['to_date']

            print(f"\nFlushing nexus _id={nid} files={len(bucket['file_names'])} "
                  f"total_inserted={bucket['inc_inserted']} "
                  f"from={bucket['from_date']} to={bucket['to_date']}")

            upsert_nexus(
                db_name=bucket['db_name'],
                collection=bucket['collection'],
                nexus_id=nid,
                set_fields=bucket['set_fields'],
                inc_inserted=bucket['inc_inserted'],
                inc_duplicates=bucket['inc_duplicates'],
            )


# ======================================================================
#  INFO PROCESSOR
# ======================================================================

class WhatsAppInfoProcessor:

    def parse(self, html_content: str) -> dict:
        soup     = BeautifulSoup(html_content)          # lxml via wrapper
        all_text = soup.get_text(separator='\n')        # computed ONCE
        lines    = [l.strip() for l in all_text.split('\n') if l.strip()]

        account   = self._extract_account_info(soup)
        target_no = self._process_single_number(self._clean_phone(account.get('Account Identifier', '')))
        from_date, to_date = self._parse_date_range(account.get('Date Range', ''))
        emails    = self._extract_emails(lines)

        connections = self._extract_connections(lines)
        groups      = self._extract_groups(lines)
        sym, asym   = self._extract_contacts(lines)

        sym_codes  = [self._extract_4digit_code(n) for n in sym]
        asym_codes = [self._extract_4digit_code(n) for n in asym]

        print(f"Target: {target_no}  Date: {from_date} -> {to_date}  "
              f"Connections: {len(connections)}  Groups: {len(groups)}  "
              f"Sym: {len(sym)}  Asym: {len(asym)}")

        return {
            'target_no':               target_no,
            'from_date':               from_date,
            'to_date':                 to_date,
            'emails':                  emails,
            'connections':             connections,
            'groups':                  groups,
            'symmetric_contacts':      sym,
            'symmetric_contact_codes': sym_codes,
            'asymmetric_contacts':     asym,
            'asymmetric_contact_codes': asym_codes,
        }

    def _extract_account_info(self, soup) -> dict:
        info = {}
        for outer in soup.find_all('div', class_='t o'):
            inner = outer.find('div', class_='t i')
            if not inner:
                continue
            key_text = inner.get_text(strip=True)
            val_div  = inner.find_next('div', class_='m')
            if not val_div:
                continue
            val_text = val_div.get_text(strip=True)
            for label in ['Account Identifier', 'Service', 'Internal Ticket Number',
                          'Account Type', 'Date Range', 'Generated']:
                if label in key_text:
                    info[label] = val_text
        return info

    def _clean_phone(self, raw: str) -> str:
        """Clean phone number using the utility function"""
        return clean_phone_number(raw)

    def _process_single_number(self, text):
        """Process a single phone number and return cleaned version"""
        if not text:
            return ''
        cleaned, _ = parse_phone_number(text)
        return cleaned if cleaned else text

    def _parse_date_range(self, date_range_str: str):
        if not date_range_str or ' to ' not in date_range_str:
            return None, None
        try:
            from_raw, to_raw = date_range_str.split(' to ', 1)
            return self.convert_utc_to_ist(from_raw.strip()), self.convert_utc_to_ist(to_raw.strip())
        except Exception:
            return None, None

    def convert_utc_to_ist(self, utc_str: str) -> str:
        utc_str = re.sub(r'\s*UTC\s*', '', utc_str).strip()
        for fmt in ['%Y-%m-%d %H:%M:%S', '%d-%m-%Y %H:%M:%S']:
            try:
                dt = datetime.strptime(utc_str, fmt)
                dt = dt.replace(tzinfo=timezone.utc) + timedelta(hours=5, minutes=30)
                return dt.strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                continue
        return utc_str

    def extract_mobile_code(self, phone_number: str) -> str:
        if not phone_number:
            return ''
        results = []
        country_codes = ['1', '44', '91', '92', '93', '86', '81', '49',
                         '33', '7', '39', '34', '55', '52', '234']
        for number in [n.strip() for n in str(phone_number).split(',') if n.strip()]:
            _, code = parse_phone_number(number)
            if code:
                results.append(code)
            else:
                # Fallback to country code extraction
                cleaned = clean_phone_number(number)
                if cleaned:
                    if len(cleaned) == 10 and cleaned[0] in '6789':
                        results.append(cleaned[:4])
                    else:
                        found = ''
                        for size in [3, 2, 1]:
                            if len(cleaned) >= size and cleaned[:size] in country_codes:
                                found = cleaned[:size]
                                break
                        results.append(found or (cleaned[:3] if len(cleaned) >= 3 else cleaned))
        return ', '.join(results)

    def _extract_4digit_code(self, number: str) -> str:
        if not number:
            return ''
        _, code = parse_phone_number(number)
        if code:
            return code
        # Fallback to original logic
        clean = clean_phone_number(number)
        if not clean:
            return ''
        if len(clean) == 12 and clean.startswith('91'):
            return clean[2:6]
        if len(clean) == 12 and clean.startswith('11'):
            return clean[2:6]
        if len(clean) == 11 and clean.startswith('0'):
            return clean[1:5]
        if len(clean) == 10 and clean[0] in '6789':
            return clean[:4]
        return clean[:4] if len(clean) >= 4 else clean

    def _extract_code(self, number: str) -> str:
        if not number:
            return ''
        clean = clean_phone_number(number)
        if not clean:
            return ''
        if clean.startswith('91') and len(clean) >= 12:
            return clean[2:6]
        if clean.startswith('0') and len(clean) == 11:
            return clean[1:5]
        if len(clean) == 10 and clean[0] in '6789':
            return clean[:4]
        country_codes = ['1', '44', '92', '93', '86', '81', '49',
                         '33', '7', '39', '34', '55', '52', '234']
        for size in [3, 2, 1]:
            if len(clean) >= size and clean[:size] in country_codes:
                return clean[:size]
        return clean[:3] if len(clean) >= 3 else clean

    def _extract_emails(self, lines: list) -> list:
        emails   = []
        in_email = False
        section_headers = {
            'Registered Email Addresses', 'Emails', 'NCMEC CyberTips',
            'Connections', 'Groups', 'Address Book', 'Owned Groups',
        }
        email_re = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
        for line in lines:
            if 'Registered Email Addresses' in line or 'Registered Email' in line:
                in_email = True
                continue
            if in_email:
                if line in section_headers or self._is_metadata_line(line):
                    if line in section_headers:
                        break
                    continue
                if email_re.match(line):
                    emails.append(line)
                elif line.lower() == 'no responsive records':
                    break
        return emails

    def _extract_connections(self, lines: list) -> list:
        FIELD_NAMES = {
            'Device Id', 'Service start', 'Device Type', 'App Version',
            'Device OS Build Number', 'Connection State', 'Last seen', 'Last IP',
        }
        SECTION_STOP = {
            'Groups', 'Owned Groups', 'Address Book',
            'NCMEC CyberTips', 'Registered Email Addresses',
        }
        connections = []
        i = 0
        while i < len(lines):
            if lines[i] == 'Connection':
                conn, i = self._parse_connection_block(lines, i + 1, FIELD_NAMES, SECTION_STOP)
                if conn:
                    connections.append(conn)
                continue
            i += 1
        return connections

    def _parse_connection_block(self, lines, start, field_names, stop_sections):
        conn = {}
        i    = start
        while i < len(lines):
            line = lines[i]
            if self._is_metadata_line(line):
                i += 1
                continue
            if line in stop_sections or line == 'Connection':
                break
            for field in field_names:
                if re.fullmatch(field, line, re.IGNORECASE):
                    i += 1
                    while i < len(lines) and self._is_metadata_line(lines[i]):
                        i += 1
                    if i < len(lines):
                        value = lines[i]
                        if field in ('Service start', 'Last seen'):
                            value = self.convert_utc_to_ist(value)
                        conn[field] = value
                        i += 1
                    break
            else:
                i += 1
        return conn, i

    def _extract_groups(self, lines: list) -> list:
        GROUP_FIELDS = {'ID', 'Creation', 'Size', 'Subject', 'Description', 'Picture'}
        SECTION_STOP = {
            'Address Book', 'NCMEC CyberTips', 'Registered Email Addresses',
            'Connections', 'Symmetric contacts', 'Asymmetric contacts',
        }
        groups    = []
        i         = 0
        in_groups = False
        while i < len(lines):
            line = lines[i]
            if line in ('Owned Groups', 'Groups'):
                in_groups = True
                i += 1
                continue
            if in_groups and line in SECTION_STOP:
                break
            if in_groups and line == 'ID':
                group, i = self._parse_group_block(lines, i, GROUP_FIELDS, SECTION_STOP)
                if group.get('ID'):
                    groups.append(group)
                continue
            i += 1
        return groups

    def _parse_group_block(self, lines, start, field_names, stop_sections):
        group = {}
        i     = start + 1
        while i < len(lines) and self._is_metadata_line(lines[i]):
            i += 1
        if i < len(lines) and lines[i] not in field_names and lines[i] not in stop_sections:
            group['ID'] = lines[i]
            i += 1
        while i < len(lines):
            line = lines[i]
            if self._is_metadata_line(line):
                i += 1
                continue
            if line.startswith('Linked MMMMM') or line.startswith('linked_MMMMM'):
                i += 1
                continue
            if line in stop_sections:
                break
            if line == 'ID' and group.get('ID'):
                break
            if line in field_names:
                i += 1
                while i < len(lines) and self._is_metadata_line(lines[i]):
                    i += 1
                if (i < len(lines)
                        and lines[i] not in field_names
                        and lines[i] not in stop_sections
                        and not self._is_metadata_line(lines[i])):
                    value = lines[i]
                    if line == 'Creation':
                        value = self.convert_utc_to_ist(value)
                    group[line] = value
                    i += 1
                continue
            i += 1
        return group, i

    def _extract_contacts(self, lines: list):
        SECTION_LABELS = {'Symmetric contacts': 'sym', 'Asymmetric contacts': 'asym'}
        STOP = {'Groups', 'Owned Groups', 'Connections', 'NCMEC CyberTips',
                'Registered Email Addresses'}
        sym          = []
        asym         = []
        current_list = None
        for line in lines:
            for label, key in SECTION_LABELS.items():
                if label in line:
                    current_list = sym if key == 'sym' else asym
                    break
            if current_list is None:
                continue
            if line in STOP or self._is_metadata_line(line):
                continue
            if len(line) > 40:
                continue
            if re.match(r'^\d+ Total$', line):
                continue
            # Use the phone number parser
            cleaned, _ = parse_phone_number(line)
            if len(cleaned) >= 7:
                current_list.append(cleaned)
        return sym, asym

    def _is_metadata_line(self, line: str) -> bool:
        patterns = [
            r'WhatsApp Business Record Page \d+',
            r'^Page \d+$',
            r'^Record Page$',
            r'^Business Record$',
        ]
        return any(re.search(p, line, re.IGNORECASE) for p in patterns)


# ======================================================================
#  MAIN UPLOAD VIEW
# ======================================================================

@method_decorator(csrf_exempt, name='dispatch')
class WhatsAppFilePathUploadView(NexusFlushMixin, View):

    def get(self, request):
        file_path     = request.GET.get('file_path')
        crime_name    = request.GET.get('crime_name') or request.GET.get('crimename', 'Unknown Crime')
        area_location = request.GET.get('arealocation', '') or request.GET.get('area_location', '')
        if not file_path:
            return JsonResponse({'success': False, 'error': 'file_path parameter is required'}, status=400)
        return self.process_file(file_path, crime_name, area_location)

    def post(self, request):
        crime_name    = request.POST.get('crime_name') or request.POST.get('crimename', 'Unknown Crime')
        area_location = request.POST.get('arealocation', '') or request.POST.get('area_location', '')

        uploaded_files = (
            request.FILES.getlist('file') or
            request.FILES.getlist('files[]') or
            request.FILES.getlist('files')
        )
        if not uploaded_files:
            return JsonResponse({
                'success': False,
                'error': 'No files uploaded. Use "file", "files[]" or "files" parameter.',
            }, status=400)

        results          = []
        total_inserted   = 0
        total_duplicates = 0
        total_records    = 0

        for uploaded_file in uploaded_files:
            try:
                for file_result in self.process_single_file(uploaded_file, crime_name, area_location):
                    results.append(file_result)
                    if file_result.get('success'):
                        total_inserted   += file_result['data'].get('inserted', 0)
                        total_duplicates += file_result['data'].get('duplicates', 0)
                        total_records    += file_result['data'].get('total_records', 0)
            except Exception as e:
                results.append({'file_name': uploaded_file.name, 'success': False, 'error': str(e)})

        successful = sum(1 for r in results if r.get('success'))
        return JsonResponse({
            'success': True,
            'message': f'Processed {len(results)} files ({successful} successful, {len(results)-successful} failed)',
            'summary': {
                'total_files':      len(results),
                'successful_files': successful,
                'failed_files':     len(results) - successful,
                'total_inserted':   total_inserted,
                'total_duplicates': total_duplicates,
                'total_records':    total_records,
            },
            'file_results': results,
        })

    def process_single_file(self, uploaded_file, crime_name, area_location=''):
        lower = uploaded_file.name.lower()

        if lower.endswith('.zip') or lower.endswith('.rar'):
            archive_type = 'rar' if lower.endswith('.rar') else 'zip'
            print(f"\n{'='*60}\n{archive_type.upper()} FILE: {uploaded_file.name}\n{'='*60}")
            try:
                raw = uploaded_file.read()
            except Exception as e:
                return [{'file_name': uploaded_file.name, 'success': False,
                         'error': f'Failed to read {archive_type.upper()}: {e}'}]

            results = []
            try:
                # OPTIMIZED: generator — no full list() in RAM
                for inner_name, inner_content in extract_files_from_archive(
                        io.BytesIO(raw), _parent_name=uploaded_file.name, _archive_type=archive_type):
                    print(f"\n--- Processing {archive_type.upper()} entry: {inner_name} ---")
                    result = self._process_file_content(
                        inner_content, crime_name, inner_name, area_location,
                        skip_nexus_upsert=True,
                    )
                    results.append(result)
            except Exception as e:
                return [{'file_name': uploaded_file.name, 'success': False,
                         'error': f'Failed to open {archive_type.upper()}: {e}'}]

            if not results:
                return [{'file_name': uploaded_file.name, 'success': False,
                         'error': f'{archive_type.upper()} archive contains no .html or .txt files'}]

            self._flush_nexus_totals(results)
            return results

        # OPTIMIZED: read directly from InMemoryUploadedFile — no FileSystemStorage disk I/O
        try:
            uploaded_file.seek(0)
            file_content = uploaded_file.read().decode('utf-8', errors='ignore')
            return [self._process_file_content(
                file_content, crime_name, uploaded_file.name, area_location
            )]
        except Exception as e:
            return [{'file_name': uploaded_file.name, 'success': False,
                     'error': f'Error processing file: {e}'}]

    def process_file(self, file_path, crime_name, area_location):
        try:
            if not os.path.exists(file_path):
                return JsonResponse({'success': False, 'error': f'File not found: {file_path}'}, status=404)
            if not os.path.isfile(file_path):
                return JsonResponse({'success': False, 'error': f'Path is not a file: {file_path}'}, status=400)

            if file_path.lower().endswith('.zip') or file_path.lower().endswith('.rar'):
                archive_type = 'rar' if file_path.lower().endswith('.rar') else 'zip'
                with open(file_path, 'rb') as af:
                    raw = af.read()

                results = []
                for inner_name, inner_content in extract_files_from_archive(
                        io.BytesIO(raw), _parent_name=os.path.basename(file_path),
                        _archive_type=archive_type):
                    result = self._process_file_content(
                        inner_content, crime_name, inner_name, area_location,
                        skip_nexus_upsert=True,
                    )
                    results.append(result)

                if not results:
                    return JsonResponse(
                        {'success': False,
                         'error': f'{archive_type.upper()} archive contains no .html or .txt files'},
                        status=400)

                self._flush_nexus_totals(results)
                total_inserted   = sum(r.get('data', {}).get('inserted', 0)      for r in results if r.get('success'))
                total_duplicates = sum(r.get('data', {}).get('duplicates', 0)    for r in results if r.get('success'))
                total_records    = sum(r.get('data', {}).get('total_records', 0) for r in results if r.get('success'))
                successful       = sum(1 for r in results if r.get('success'))

                return JsonResponse({
                    'success': True,
                    'message': f'Processed {archive_type.upper()} with {len(results)} files ({successful} successful)',
                    'summary': {
                        'total_files':      len(results),
                        'successful_files': successful,
                        'failed_files':     len(results) - successful,
                        'total_inserted':   total_inserted,
                        'total_duplicates': total_duplicates,
                        'total_records':    total_records,
                    },
                    'file_results': results,
                })

            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                file_content = f.read()
            result = self._process_file_content(
                file_content, crime_name, os.path.basename(file_path), area_location
            )
            return JsonResponse(result, status=200 if result.get('success') else 400)

        except Exception as e:
            return JsonResponse({'success': False, 'error': f'Error processing file: {e}'}, status=500)

    # ------------------------------------------------------------------
    #  CORE PROCESSING
    # ------------------------------------------------------------------

    def _process_file_content(self, file_content, crime_name, file_name,
                               area_location, skip_nexus_upsert=False):
        try:
            file_format = detect_file_format(file_content)
            print(f"\n{'='*60}\nDetected: {file_format.upper()}  file={file_name}\n{'='*60}")

            # OPTIMIZED: parse soup once and extract text once — both
            # detect_content_type and the msg processor share the same objects.
            if file_format == 'html':
                soup      = BeautifulSoup(file_content)   # lxml via wrapper above
                all_text  = soup.get_text(separator='\n') # computed ONCE
            else:
                soup      = None
                all_text  = file_content

            content_flags = detect_content_type(file_content, file_format, text_cache=all_text)
            has_msg_call = content_flags['has_messages'] or content_flags['has_calls']
            has_info     = (content_flags['has_connections']
                            or content_flags['has_groups']
                            or content_flags['has_contacts'])

            if not has_msg_call and not has_info:
                has_msg_call = True

            combined_result = {
                'file_name':     file_name,
                'success':       True,
                'file_format':   file_format.upper(),
                'crime_name':    crime_name,
                'area_location': area_location,
                'data':          {},
            }

            if has_msg_call:
                # Pass the pre-parsed soup and text so the pipeline never re-parses
                r = self._process_msg_call_records(
                    file_content, file_format, soup, all_text,
                    crime_name, file_name, area_location,
                    skip_nexus_upsert=skip_nexus_upsert,
                )
                if '_nexus_payload' in r:
                    combined_result['_nexus_payload'] = r.pop('_nexus_payload')
                combined_result['data'].update(r)
                if not r.get('_success', True):
                    combined_result['msg_call_error'] = r.get('_error', 'Unknown error')

            if has_info:
                # Re-use all_text / soup already computed above
                r = self._process_info_records(
                    file_content, file_format, crime_name, file_name,
                    area_location, skip_nexus_upsert=skip_nexus_upsert,
                    _prebuilt_soup=soup,
                )
                if '_nexus_payload' in r:
                    combined_result['_nexus_payload'] = r.pop('_nexus_payload')
                combined_result['data'].update(r)
                if not r.get('_success', True):
                    combined_result['info_error'] = r.get('_error', 'Unknown error')

            return combined_result

        except Exception as e:
            import traceback
            print(traceback.format_exc())
            return {'file_name': file_name, 'success': False,
                    'error': f'Error processing file content: {e}'}

    # ------------------------------------------------------------------
    #  MSG / CALL PIPELINE
    # ------------------------------------------------------------------

    def _process_msg_call_records(self, file_content, file_format, soup, all_text,
                                  crime_name, file_name, area_location,
                                  skip_nexus_upsert=False):
        try:
            message_processor  = WhatsAppMessageProcessor()
            call_processor     = WhatsAppCallProcessor()
            txt_call_processor = WhatsAppTXTCallProcessor()

            if file_format == 'txt':
                account_info, message_lines, call_lines = self._parse_txt_file(file_content)
            else:
                # OPTIMIZED: soup already parsed, all_text already extracted
                account_info  = message_processor.extract_account_info(soup)
                call_lines    = [line.strip() for line in all_text.split('\n') if line.strip()]
                message_lines = call_lines

            raw_target = account_info.get('Account Identifier', '')
            # Use the phone number parser
            target_no, _ = parse_phone_number(raw_target)
            target_no = message_processor.extract_phone_number(target_no) if target_no else None

            if target_no:
                message_processor.current_target_no = target_no
                call_processor.current_target_no     = target_no
                txt_call_processor.current_target_no = target_no

            date_range = account_info.get('Date Range', '')
            from_date = to_date = None
            if date_range and ' to ' in date_range:
                parts    = date_range.split(' to ', 1)
                fromdate = message_processor.convert_utc_to_ist(parts[0].strip())
                todate   = message_processor.convert_utc_to_ist(parts[1].strip())
                if fromdate and todate:
                    from_date = fromdate
                    to_date   = todate
                    for proc in (message_processor, call_processor, txt_call_processor):
                        proc.fromdate = fromdate
                        proc.todate   = todate

            if file_format == 'txt':
                message_records = _extract_messages_from_txt_lines(message_lines, message_processor, target_no)
                call_records    = txt_call_processor.extract_call_records_from_txt(call_lines, target_no)
            else:
                message_records = message_processor.extract_message_records(soup, target_no)
                call_records    = call_processor.extract_call_records_group_format(call_lines, target_no)

            all_records = message_records + call_records
            seen_ids    = set()
            for r in call_records:
                call_id = r.get('ID')
                if r.get('Call_Type', '').startswith('Call') and call_id:
                    seen_ids.add(call_id)

            message_count   = len([r for r in all_records if r.get('Call_Type', '').startswith('Msg')])
            call_count      = len([r for r in all_records if r.get('Call_Type', '').startswith('Call')])
            group_upd_count = len({r.get('ID') for r in all_records
                                   if r.get('Status') == 'group_update' and r.get('ID')})
            unique_call_ids = len(seen_ids)
            logical_total   = message_count + unique_call_ids

            if not all_records:
                return {'_success': False, '_error': 'No valid WhatsApp records found in the file'}

            # OPTIMIZED: compute IDs once, not per-record inside a closure
            target_code = message_processor.extract_mobile_code(target_no) if target_no else ''
            from_date   = getattr(message_processor, 'fromdate', from_date)
            to_date     = getattr(message_processor, 'todate',   to_date)
            user_id     = get_user_id()
            nexus_id    = generate_nexus_id(crime_name=crime_name, target_no=target_no, user_id=user_id)
            crime_hash  = xxhash.xxh64(f"{crime_name}{area_location}".lower()).hexdigest()

            try:
                bulk_insert("CDR", "CrimeRegistry", [{
                    '_id': crime_hash, 'Crime': crime_name,
                    'AreaLocation': area_location, 'seq_id': nexus_id,
                }], "insert")
            except Exception as e:
                print(f"   CrimeRegistry insert error: {e}")

            # Build standardized records — nexus_id and per-record hash pre-computed once
            standardized = []
            for idx, record in enumerate(all_records):
                sr = message_processor.standardize_message_keys(record)
                sr['seq_id'] = nexus_id
                sr['_id']    = xxhash.xxh64(
                    f"WA_{sr.get('DateTimeUTC','')}_{idx:06d}_{sr.get('ID','')}"
                ).hexdigest()
                standardized.append(sr)

            try:
                result     = bulk_insert("WhatsApp", "WhatsAppRecords", standardized, "insert")
                inserted   = result.get('inserted',   0) if result else 0
                duplicates = result.get('duplicates', 0) if result else 0
            except Exception as db_error:
                return {'_success': False, '_error': f'Database insertion failed: {db_error}'}

            now = datetime.now()
            nexus_set = {
                'CrimeName':   crime_name,
                'CrimeID':     crime_hash,
                'FileName':    file_name,
                'FileFormat':  file_format.upper(),
                'RecordType':  'WhatsApp',
                'TargetNo':    target_no,
                'Target_code': target_code,
                'FromDate':    datetime.strptime(from_date, '%Y-%m-%d %H:%M:%S') if from_date else None,
                'ToDate':      datetime.strptime(to_date,   '%Y-%m-%d %H:%M:%S') if to_date   else None,
                'Day':         now.day,
                'Month':       now.month,
                'Year':        now.year,
                'CreatedAt':   now,
            }
            if area_location:
                nexus_set['AreaLocation'] = area_location

            ret = {
                'nexus_id':             nexus_id,
                'total_records':        logical_total,
                'logical_total':        logical_total,
                'inserted':             inserted,
                'duplicates':           duplicates,
                'message_records':      message_count,
                'call_records':         call_count,
                'unique_call_ids':      unique_call_ids,
                'group_update_records': group_upd_count,
            }

            if skip_nexus_upsert:
                ret['_nexus_payload'] = {
                    'nexus_id':       nexus_id,
                    'db_name':        'WhatsApp',
                    'collection':     'WhatsAppNexus',
                    'set_fields':     nexus_set,
                    'inc_inserted':   logical_total,
                    'inc_duplicates': duplicates,
                    'file_name':      file_name,
                }
            else:
                upsert_nexus(
                    db_name='WhatsApp', collection='WhatsAppNexus',
                    nexus_id=nexus_id, set_fields=nexus_set,
                    inc_inserted=logical_total, inc_duplicates=duplicates,
                )

            return ret

        except Exception as e:
            import traceback
            print(traceback.format_exc())
            return {'_success': False, '_error': str(e)}

    # ------------------------------------------------------------------
    #  INFO PIPELINE
    # ------------------------------------------------------------------

    def _process_info_records(self, file_content, file_format, crime_name,
                               file_name, area_location, skip_nexus_upsert=False,
                               _prebuilt_soup=None):
        """
        OPTIMIZED: accepts _prebuilt_soup so WhatsAppInfoProcessor.parse()
        does not call BeautifulSoup() a second time for the same file.
        """
        try:
            processor = WhatsAppInfoProcessor()

            if _prebuilt_soup is not None:
                # Re-use the already-parsed soup; bypass parse() to avoid re-parsing
                soup     = _prebuilt_soup
                all_text = soup.get_text(separator='\n')
                lines    = [l.strip() for l in all_text.split('\n') if l.strip()]
                account  = processor._extract_account_info(soup)
                target_no = processor._process_single_number(
                    processor._clean_phone(account.get('Account Identifier', '')))
                from_date, to_date = processor._parse_date_range(account.get('Date Range', ''))
                emails        = processor._extract_emails(lines)
                connections   = processor._extract_connections(lines)
                groups        = processor._extract_groups(lines)
                sym, asym     = processor._extract_contacts(lines)
                sym_codes     = [processor._extract_4digit_code(n) for n in sym]
                asym_codes    = [processor._extract_4digit_code(n) for n in asym]
            else:
                parsed = processor.parse(file_content)
                if not parsed:
                    return {'_success': False, '_error': 'Could not parse info records from file.'}
                target_no     = parsed.get('target_no', '')
                from_date     = parsed.get('from_date')
                to_date       = parsed.get('to_date')
                emails        = parsed.get('emails', [])
                connections   = parsed.get('connections', [])
                groups        = parsed.get('groups', [])
                sym           = parsed.get('symmetric_contacts', [])
                asym          = parsed.get('asymmetric_contacts', [])
                sym_codes     = parsed.get('symmetric_contact_codes', [])
                asym_codes    = parsed.get('asymmetric_contact_codes', [])

            user_id  = get_user_id()
            nexus_id = generate_nexus_id(crime_name=crime_name, target_no=target_no, user_id=user_id)
            now      = datetime.now()

            conn_inserted = conn_dupes = 0
            if connections:
                for conn in connections:
                    uid = f"{target_no}_{conn.get('Device Id','')}_{conn.get('Service start','')}"
                    conn['_id']      = xxhash.xxh64(uid).hexdigest()
                    conn['TargetNo'] = target_no
                    conn['seq_id']   = nexus_id
                try:
                    res = bulk_insert("WhatsApp", "WhatsAppConnections", connections, "insert")
                    conn_inserted = res.get('inserted',   0) if res else 0
                    conn_dupes    = res.get('duplicates', 0) if res else 0
                except Exception as e:
                    print(f"   Connection insert error: {e}")

            grp_inserted = grp_dupes = 0
            if groups:
                docs = [{'_id':         xxhash.xxh64(f"{target_no}_{g.get('ID','')}").hexdigest(),
                         'TargetNo':    target_no, 'ID': g.get('ID', ''),
                         'Creation':    g.get('Creation'), 'Size': g.get('Size', ''),
                         'Subject':     g.get('Subject', ''), 'Description': g.get('Description', ''),
                         'seq_id':      nexus_id} for g in groups]
                try:
                    res = bulk_insert("WhatsApp", "WhatsAppGroups", docs, "insert")
                    grp_inserted = res.get('inserted',   0) if res else 0
                    grp_dupes    = res.get('duplicates', 0) if res else 0
                except Exception as e:
                    print(f"   Group insert error: {e}")

            contact_inserted = contact_dupes = 0
            if sym or asym:
                doc = {'_id':                      xxhash.xxh64(f"{target_no}_CONTACTS").hexdigest(),
                       'TargetNo':                 target_no,
                       'symmetric_contacts':       sym,
                       'symmetric_contact_codes':  sym_codes,
                       'asymmetric_contacts':       asym,
                       'asymmetric_contact_codes': asym_codes,
                       'seq_id':                   nexus_id}
                try:
                    res = bulk_insert("WhatsApp", "WhatsAppContacts", [doc], "insert")
                    contact_inserted = res.get('inserted',   0) if res else 0
                    contact_dupes    = res.get('duplicates', 0) if res else 0
                except Exception as e:
                    print(f"   Contact insert error: {e}")

            total_inserted   = conn_inserted + grp_inserted + contact_inserted
            total_duplicates = conn_dupes + grp_dupes + contact_dupes

            nexus_set = {
                'CrimeName':   crime_name,
                'FileName':    file_name,
                'RecordType':  'WhatsAppInfo',
                'TargetNo':    target_no,
                'Target_code': processor._extract_code(target_no) if target_no else '',
                'Emails':      emails,
                'FromDate':    datetime.strptime(from_date, '%Y-%m-%d %H:%M:%S') if from_date else None,
                'ToDate':      datetime.strptime(to_date,   '%Y-%m-%d %H:%M:%S') if to_date   else None,
                'Day':         now.day,
                'Month':       now.month,
                'Year':        now.year,
                'CreatedAt':   now,
            }
            if area_location:
                nexus_set['AreaLocation'] = area_location

            ret = {
                'info_nexus_id':             nexus_id,
                'target_no':                 target_no,
                'emails':                    emails,
                'connections_inserted':      conn_inserted,
                'connections_duplicates':    conn_dupes,
                'groups_inserted':           grp_inserted,
                'groups_duplicates':         grp_dupes,
                'contacts_inserted':         contact_inserted,
                'contacts_duplicates':       contact_dupes,
                'symmetric_contacts_count':  len(sym),
                'asymmetric_contacts_count': len(asym),
            }

            if skip_nexus_upsert:
                ret['_nexus_payload'] = {
                    'nexus_id':       nexus_id,
                    'db_name':        'WhatsApp',
                    'collection':     'WhatsAppNexus',
                    'set_fields':     nexus_set,
                    'inc_inserted':   total_inserted,
                    'inc_duplicates': total_duplicates,
                    'file_name':      file_name,
                }
            else:
                upsert_nexus(
                    db_name='WhatsApp', collection='WhatsAppNexus',
                    nexus_id=nexus_id, set_fields=nexus_set,
                    inc_inserted=total_inserted, inc_duplicates=total_duplicates,
                )

            return ret

        except Exception as e:
            import traceback
            print(traceback.format_exc())
            return {'_success': False, '_error': str(e)}

    # ------------------------------------------------------------------
    #  TXT FILE PARSER  (unchanged logic)
    # ------------------------------------------------------------------

    def _parse_txt_file(self, txt_content):
        lines           = txt_content.split('\n')
        account_info    = {}
        message_lines   = []
        call_lines      = []
        current_section = 'header'

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue

            if '\t' in line and current_section == 'header':
                parts = line.split('\t', 1)
                if len(parts) == 2:
                    key, value = parts
                    account_info[key] = value
                    if key == 'Message Log':
                        current_section = 'message_definitions'
                    elif key in ('Call Logs Definition', 'Call Log'):
                        current_section = 'call_definitions'
            elif stripped == 'Message':
                current_section = 'messages'
            elif stripped == 'Call':
                current_section = 'calls'

            if current_section == 'messages':
                if '\t' in line:
                    k, v = line.split('\t', 1)
                    message_lines += [k, v]
                else:
                    message_lines.append(stripped)
            elif current_section == 'calls':
                if '\t' in line:
                    k, v = line.split('\t', 1)
                    call_lines += [k, v]
                else:
                    call_lines.append(stripped)

        return account_info, message_lines, call_lines


# ======================================================================
#  STANDALONE INFO UPLOAD VIEW
# ======================================================================

@method_decorator(csrf_exempt, name='dispatch')
class WhatsAppInfoloadView(NexusFlushMixin, View):
    """
    OPTIMIZED: now inherits NexusFlushMixin so _flush_nexus_totals lives
    in exactly one place.  WhatsAppNexus.objects().first() is hoisted out
    of the per-connection loop in _process_content().
    """

    def post(self, request):
        crime_name     = request.POST.get('crime_name', 'Unknown Crime')
        uploaded_files = request.FILES.getlist('files[]') or request.FILES.getlist('files')
        if not uploaded_files:
            return JsonResponse({'success': False, 'error': 'No files uploaded.'}, status=400)

        results = []
        for uploaded_file in uploaded_files:
            try:
                results.extend(self.process_single_file(uploaded_file, crime_name))
            except Exception as e:
                results.append({'file_name': uploaded_file.name, 'success': False, 'error': str(e)})

        successful = sum(1 for r in results if r.get('success'))
        return JsonResponse({
            'success': True,
            'message': f'Processed {len(results)} files ({successful} successful, '
                       f'{len(results)-successful} failed)',
            'file_results': results,
        })

    def process_single_file(self, uploaded_file, crime_name):
        lower = uploaded_file.name.lower()

        if lower.endswith('.zip') or lower.endswith('.rar'):
            archive_type = 'rar' if lower.endswith('.rar') else 'zip'
            try:
                raw      = uploaded_file.read()
                results  = []
                for name, content in extract_files_from_archive(
                        io.BytesIO(raw), _parent_name=uploaded_file.name, _archive_type=archive_type):
                    results.append(self._process_content(content, name, crime_name, skip_nexus_upsert=True))
            except Exception as e:
                return [{'file_name': uploaded_file.name, 'success': False,
                         'error': f'Failed to open {archive_type.upper()}: {e}'}]
            if not results:
                return [{'file_name': uploaded_file.name, 'success': False,
                         'error': f'{archive_type.upper()} archive contains no .html or .txt files'}]
            self._flush_nexus_totals(results)
            return results

        # OPTIMIZED: read directly — no FileSystemStorage
        try:
            uploaded_file.seek(0)
            file_content = uploaded_file.read().decode('utf-8', errors='ignore')
        except Exception as e:
            return [{'file_name': uploaded_file.name, 'success': False,
                     'error': f'Could not read file: {e}'}]
        return [self._process_content(file_content, uploaded_file.name, crime_name)]

    def _process_content(self, file_content, file_name, crime_name, skip_nexus_upsert=False):
        processor = WhatsAppInfoProcessor()
        parsed    = processor.parse(file_content)  # lxml inside
        if not parsed:
            return {'file_name': file_name, 'success': False, 'error': 'Could not parse file.'}

        target_no     = parsed.get('target_no', '')
        from_date     = parsed.get('from_date')
        to_date       = parsed.get('to_date')
        emails        = parsed.get('emails', [])
        connections   = parsed.get('connections', [])
        groups        = parsed.get('groups', [])
        sym_contacts  = parsed.get('symmetric_contacts', [])
        asym_contacts = parsed.get('asymmetric_contacts', [])
        sym_codes     = parsed.get('symmetric_contact_codes', [])
        asym_codes    = parsed.get('asymmetric_contact_codes', [])

        nexus_id = generate_nexus_id(crime_name=crime_name, target_no=target_no, user_id=get_user_id())
        now      = datetime.now()

        # OPTIMIZED: fetch nexus emails once, not per-connection-record
        nexus_obj    = WhatsAppNexus.objects(seq_id=nexus_id).first()
        nexus_emails = nexus_obj.Emails if nexus_obj and nexus_obj.Emails else []

        conn_inserted = conn_dupes = 0
        if connections:
            for conn in connections:
                uid = f"{target_no}_{conn.get('Device Id','')}_{conn.get('Service start','')}"
                conn.update({
                    '_id':      xxhash.xxh64(uid).hexdigest(),
                    'TargetNo': target_no,
                    'seq_id':   nexus_id,
                    'Emails':   '  '.join(nexus_emails),   # same value for all — set once
                })
            try:
                res = bulk_insert("WhatsApp", "WhatsAppConnections", connections, "insert")
                conn_inserted = res.get('inserted',   0) if res else 0
                conn_dupes    = res.get('duplicates', 0) if res else 0
            except Exception as e:
                print(f"Connection insert error: {e}")

        grp_inserted = grp_dupes = 0
        if groups:
            docs = [{'_id':     xxhash.xxh64(f"{target_no}_{g.get('ID','')}").hexdigest(),
                     'TargetNo': target_no, 'seq_id': nexus_id,
                     'ID':       g.get('ID', ''), 'Creation': g.get('Creation'),
                     'Size':     g.get('Size', ''), 'Subject': g.get('Subject', ''),
                     'Description': g.get('Description', '')} for g in groups]
            try:
                res = bulk_insert("WhatsApp", "WhatsAppGroups", docs, "insert")
                grp_inserted = res.get('inserted',   0) if res else 0
                grp_dupes    = res.get('duplicates', 0) if res else 0
            except Exception as e:
                print(f"Group insert error: {e}")

        contact_inserted = contact_dupes = 0
        if sym_contacts or asym_contacts:
            doc = {'_id':                      xxhash.xxh64(f"{target_no}_CONTACTS").hexdigest(),
                   'TargetNo':                 target_no, 'seq_id': nexus_id,
                   'symmetric_contacts':       sym_contacts,
                   'symmetric_contact_codes':  sym_codes,
                   'asymmetric_contacts':       asym_contacts,
                   'asymmetric_contact_codes': asym_codes}
            try:
                res = bulk_insert("WhatsApp", "WhatsAppContacts", [doc], "insert")
                contact_inserted = res.get('inserted',   0) if res else 0
                contact_dupes    = res.get('duplicates', 0) if res else 0
            except Exception as e:
                print(f"Contact insert error: {e}")

        total_ins  = conn_inserted + grp_inserted + contact_inserted
        total_dups = conn_dupes + grp_dupes + contact_dupes

        nexus_set = {
            'CrimeName':   crime_name,
            'FileName':    file_name,
            'RecordType':  'WhatsAppInfo',
            'TargetNo':    target_no,
            'Target_code': processor.extract_mobile_code(target_no) if target_no else '',
            'Emails':      emails,
            'FromDate':    datetime.strptime(from_date, '%Y-%m-%d %H:%M:%S') if from_date else None,
            'ToDate':      datetime.strptime(to_date,   '%Y-%m-%d %H:%M:%S') if to_date   else None,
            'Day':         now.day,
            'Month':       now.month,
            'Year':        now.year,
            'CreatedAt':   now,
        }

        result = {
            'file_name': file_name,
            'success':   True,
            'data': {
                'nexus_id':                  nexus_id,
                'target_no':                 target_no,
                'from_date':                 from_date,
                'to_date':                   to_date,
                'emails':                    emails,
                'connections_inserted':      conn_inserted,
                'connections_duplicates':    conn_dupes,
                'groups_inserted':           grp_inserted,
                'groups_duplicates':         grp_dupes,
                'contacts_inserted':         contact_inserted,
                'contacts_duplicates':       contact_dupes,
                'symmetric_contacts_count':  len(sym_contacts),
                'asymmetric_contacts_count': len(asym_contacts),
            },
        }

        if skip_nexus_upsert:
            result['_nexus_payload'] = {
                'nexus_id':       nexus_id,
                'db_name':        'WhatsApp',
                'collection':     'WhatsAppNexus',
                'set_fields':     nexus_set,
                'inc_inserted':   total_ins,
                'inc_duplicates': total_dups,
                'file_name':      file_name,
            }
        else:
            upsert_nexus(
                db_name='WhatsApp', collection='WhatsAppNexus',
                nexus_id=nexus_id, set_fields=nexus_set,
                inc_inserted=total_ins, inc_duplicates=total_dups,
            )


        return result