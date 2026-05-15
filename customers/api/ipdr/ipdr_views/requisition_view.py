"""
IP Requisition API  –  v3
==========================

Payload fields
--------------
seq_ids          list   Required. List of nexus IDs.
from_date        str    Required. ISO 8601 or Unix timestamp.
to_date          str    Required. ISO 8601 or Unix timestamp.
filter_type      str    Required. requisition | tsp_session | tsp_session_4_5 | tsp_session_5
msisdn           bool   true  → MSISDN column populated:
                                 if IPDR is a numeric mobile number → MSISDN = IPDR value (int)
                                 otherwise → MSISDN = DB value
                         false → MSISDN column omitted entirely.
is_indian        bool   true  → only records where Country == "India".
ext_time         int    Extend session window (minutes, split equally start/end).
                         Applied globally unless session_op scopes it.
split_time       int    Reserved / future use unless adjust_session=true.
                         When adjust_session=true: split threshold in minutes.
isp              bool   true  → include records where Usage == "isp"  (case-insensitive).
tsp              bool   true  → include records where TSP/Broadband/Satellite == "TSP".
                         isp=true  tsp=false  → Usage=="isp" only
                         isp=false tsp=true   → TSP/Broadband/Satellite=="TSP" only
                         isp=true  tsp=true   → EITHER condition satisfied  (OR logic)
                         isp=false tsp=false  → no Usage/TSP filter applied
network_wise     bool   true  → response contains per-operator buckets
                                 (airtel / bsnl_mtnl / reliance_jio / vi / broadband / other)
                                 data[] is NOT included when network_wise=true
                         false → response contains data:[] (flat, paginated)  [DEFAULT]
operator         str    requisition (default, network_wise=false only) |
                         airtel | bsnl_mtnl | reliance_jio | vi | broadband |
                         comma-separated combo e.g. "airtel,bsnl_mtnl" (network_wise=true only)
                         "requisition"            → applies custom date/time format only to
                                                    data[] records (network_wise=false only)
                         single/multi operator    → scopes custom format + operator bucket count
                                                    (network_wise=true only)
split_datetime   bool   true  → Session Start/End Date + Time separately.
                         false → Session Start/End DateTime combined.
date_f           str    Date token format  e.g. "dd/MM/yyyy"
time_f           str    Time token format  e.g. "HH:mm:ss"
datetime_format  str    Combined format    e.g. "dd/MM/yyyy HH:mm:ss"
remove_ipinfo    bool   true  → strip all IP-detail fields from each record.
                         false → include all fields (default).
adjust_session   bool   true  → enable session splitting via split_time.
                         false → no splitting (default).
session_op       str    Comma-separated operator keys to apply ext_time + split_time to.
                         e.g. "airtel" | "airtel,reliance_jio" | "all"
                         When omitted or "all" → applies to every operator.
page             int    Page number (default 1).
limit            int    Records per page (default 1000).

operator behaviour
------------------
network_wise=false
  • operator="requisition" (default) → custom datetime format applied to ALL records in data[]
  • any other value ignored / treated as requisition

network_wise=true
  • operator="airtel"                    → single-operator scope
  • operator="airtel,bsnl_mtnl"          → multi-operator scope (OR match)
  • operator="requisition" is ignored    → no scoping (all operators returned)

network_wise behaviour
-----------------------
• false (default) → flat paginated data[] list
• true            → per-operator buckets: airtel / bsnl_mtnl / reliance_jio / vi /
                    broadband / other  (full, not paginated)

Operator bucketing rules
------------------------
• Domain match                            → airtel | bsnl_mtnl | reliance_jio | vi
• TSP/Broadband/Satellite == "BROADBAND"  → broadband bucket
• No match                                → other bucket

adjust_session / session_op / split_time rules
----------------------------------------------
• adjust_session=true activates session splitting.
• split_time (int, minutes) is the per-chunk duration cap.
• A record whose Minutes > split_time is split into N chunks of split_time each
  (the last chunk takes the remainder).
• ext_time is applied BEFORE splitting and only to operators listed in session_op.
• session_op="airtel,reliance_jio" → ext_time + split_time applied only to
  airtel and reliance_jio records; other operators are left untouched.
• session_op="all" or omitted → applies to every operator.

Record field order
------------------
IPDR → MSISDN (if requested) → Type → Value → Port →
Session Start Date → Session Start Time → Session End Date → Session End Time
  — or —
Session Start DateTime → Session End DateTime  (when split_datetime=false),
Minutes,
[Port Info → Port Category → Port Type → Usage → TSP/Broadband/Satellite →
 Isp/Org → App/Hostname → Domains → Location → Country]

MSISDN rules (when msisdn=true)
--------------------------------
• IPDR value is a pure numeric string of 8–15 digits → MSISDN = int(IPDR value)
• Otherwise → MSISDN = DB value
"""

from __future__ import annotations

import ipaddress
import re
from collections import defaultdict
from datetime import datetime, timedelta
from math import ceil

from mongoengine import get_db
from django.utils.dateparse import parse_datetime
from rest_framework.views import APIView
from rest_framework.response import Response

from ..ipdr_models.ip_model import IPDRRecord, IPDataBase, PortInfo, IPDRNexus
from ..ip_serializers import (
    IPDRRecordSerializer,
    IPDataBaseSerializer,
    PortInfoSerializer,
    IPDRNexusSerializer,
)
from ...searchengine import search_ip


# ═══════════════════════════════════════════════════════════════════════════
#  OPERATOR KEYS
#  'other' is intentionally NOT in OPERATOR_KEYS — handled separately
# ═══════════════════════════════════════════════════════════════════════════

OPERATOR_KEYS = ('airtel', 'bsnl_mtnl', 'reliance_jio', 'vi', 'broadband')

OPERATOR_DOMAINS: dict[str, list[str]] = {
    'airtel':       ['airtel.in', 'airtel.com', 'airtelbroadband.in'],
    'bsnl_mtnl':    ['bsnl.in', 'bsnl.co.in', 'mtnl.in', 'mtnl.net.in'],
    'reliance_jio': ['jio.com', 'reliancejio.com', 'jionet.com'],
    'vi':           ['idea.adityabirla.com', 'vodafone.in', 'myvi.in',
                     'vilive.in', 'idea.net.in', 'vodafone.com'],
    'broadband':    [],
}

# Pre-compiled regex per operator — O(1) match
_OPERATOR_DOMAIN_RE: list[tuple[str, re.Pattern]] = [
    (op, re.compile('|'.join(re.escape(d) for d in domains), re.IGNORECASE))
    for op, domains in OPERATOR_DOMAINS.items()
    if op != 'broadband' and domains
]

_DEFAULT_DATE_FMT     = 'yyyyMMdd'
_DEFAULT_TIME_FMT     = 'HHmmss'
_DEFAULT_DATETIME_FMT = 'yyyyMMdd HHmmss'

# Regex to detect a mobile-number-like IPDR value (8–15 pure digits)
_MOBILE_RE = re.compile(r'^\d{8,15}$')

# Sentinel value replacing "all" — means flat data[] with custom format for all records
_REQUISITION = 'requisition'


# ═══════════════════════════════════════════════════════════════════════════
#  OPERATOR RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════

def _operator_for_record(domains_str: str, tsp_str: str) -> str:
    """
    Resolve operator bucket key.

    Priority
    --------
    1. Domain pattern match                   → airtel | bsnl_mtnl | reliance_jio | vi
    2. TSP/Broadband/Satellite == 'BROADBAND' → broadband
    3. No match                               → 'other'
    """
    for op_key, pattern in _OPERATOR_DOMAIN_RE:
        if pattern.search(domains_str):
            return op_key
    if tsp_str == 'BROADBAND':
        return 'broadband'
    return 'other'


# ═══════════════════════════════════════════════════════════════════════════
#  OPERATOR FILTER PARSING
# ═══════════════════════════════════════════════════════════════════════════

def _parse_operator_filter(raw) -> tuple[str, set[str]]:
    """
    Parse the 'operator' request field.

    Returns
    -------
    (mode, op_set)

    mode
      'requisition'  → network_wise=false default; custom fmt applies to all data[]
      'multi'        → one or more named operator keys (network_wise=true only)

    op_set
      Empty set when mode == 'requisition'.
      Set of validated operator key strings when mode == 'multi'.

    Unknown / invalid individual keys are silently dropped.
    If none of the supplied keys are valid, falls back to ('requisition', set()).
    """
    if not raw:
        return _REQUISITION, set()

    val = str(raw).strip().lower()

    if val == _REQUISITION or val == 'all':   # treat legacy "all" as requisition
        return _REQUISITION, set()

    # Parse comma-separated list
    keys = {k.strip() for k in val.split(',') if k.strip()}
    valid = keys & set(OPERATOR_KEYS)          # intersect with known keys

    if not valid:
        return _REQUISITION, set()

    return 'multi', valid


# ═══════════════════════════════════════════════════════════════════════════
#  TOKEN-BASED DATETIME FORMAT ENGINE
# ═══════════════════════════════════════════════════════════════════════════

_MONTH_ABBR: dict[int, str] = {
    1: 'Jan',  2: 'Feb',  3: 'Mar',  4: 'Apr',
    5: 'May',  6: 'Jun',  7: 'Jul',  8: 'Aug',
    9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dec',
}
_MONTH_FULL: dict[int, str] = {
    1: 'January',   2: 'February',  3: 'March',    4: 'April',
    5: 'May',       6: 'June',      7: 'July',      8: 'August',
    9: 'September', 10: 'October', 11: 'November', 12: 'December',
}

# Longest / most-specific token MUST precede any token sharing its prefix
_TOKENS: list[tuple[str, object]] = [
    ('yyyy', lambda dt: f'{dt.year:04d}'),
    ('yy',   lambda dt: f'{dt.year % 100:02d}'),
    ('MMMM', lambda dt: _MONTH_FULL[dt.month]),
    ('MMM',  lambda dt: _MONTH_ABBR[dt.month]),
    ('MM',   lambda dt: f'{dt.month:02d}'),
    ('dd',   lambda dt: f'{dt.day:02d}'),
    ('d',    lambda dt: f'{dt.day}'),
    ('HH',   lambda dt: f'{dt.hour:02d}'),
    ('H',    lambda dt: f'{dt.hour}'),
    ('hh',   lambda dt: f'{(dt.hour % 12) or 12:02d}'),
    ('h',    lambda dt: f'{(dt.hour % 12) or 12}'),
    ('mm',   lambda dt: f'{dt.minute:02d}'),
    ('m',    lambda dt: f'{dt.minute}'),
    ('ss',   lambda dt: f'{dt.second:02d}'),
    ('s',    lambda dt: f'{dt.second}'),
    ('fff',  lambda dt: '000'),
    ('tt',   lambda dt: 'AM' if dt.hour < 12 else 'PM'),
]

_TOKEN_PATTERN: re.Pattern = re.compile(
    '(' + '|'.join(re.escape(tok) for tok, _ in _TOKENS) + ')'
)
_TOKEN_MAP: dict[str, object] = {tok: fn for tok, fn in _TOKENS}


def _apply_format(dt: datetime, fmt: str) -> str:
    return ''.join(
        fn(dt) if (fn := _TOKEN_MAP.get(p)) is not None else p
        for p in _TOKEN_PATTERN.split(fmt)
    )


def _has_tokens(fmt: str) -> bool:
    return bool(_TOKEN_PATTERN.search(fmt))


# ═══════════════════════════════════════════════════════════════════════════
#  SMALL HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _split_dt(dt: datetime | None):
    if not dt:
        return None, None
    return dt.date().isoformat(), dt.time().isoformat(timespec='seconds')


def _ip_version(value: str) -> str:
    try:
        return 'IPv4' if ipaddress.ip_address(value).version == 4 else 'IPv6'
    except Exception:
        return ''


def _normalize_seq_id(raw) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        return str(raw[0]) if raw else None
    return str(raw)


def _safe_parse_datetime(value) -> datetime | None:
    if isinstance(value, str):
        try:
            return datetime.fromtimestamp(float(value))
        except ValueError:
            pass
        try:
            parsed = parse_datetime(value)
            if parsed:
                return parsed
        except Exception:
            pass
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value)
        except Exception:
            return None
    return None


def _parse_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ('false', '0', 'no', 'null', 'none', '')
    if value is None:
        return default
    return bool(value)


def _resolve_msisdn(ipdr_val, db_msisdn):
    """
    Return the correct MSISDN value:
      • ipdr_val is a pure numeric string of 8–15 digits → return int(ipdr_val)
      • otherwise → return db_msisdn (raw DB value, may be None)
    """
    if ipdr_val and _MOBILE_RE.fullmatch(str(ipdr_val).strip()):
        return str(ipdr_val).strip()
    return db_msisdn


def _parse_session_op(raw) -> set[str]:
    """
    Parse session_op into a set of operator keys.
    Returns an empty set which means 'apply to all'.
    """
    if not raw:
        return set()
    val = str(raw).strip().lower()
    if val == 'all':
        return set()
    keys = {k.strip() for k in val.split(',') if k.strip()}
    return keys


# ═══════════════════════════════════════════════════════════════════════════
#  ISP / TSP FILTER
# ═══════════════════════════════════════════════════════════════════════════

def _passes_isp_tsp(dest: dict, isp: bool, tsp: bool) -> bool:
    """
    isp=True  tsp=False  → Usage must equal "isp"  (case-insensitive)
    isp=False tsp=True   → TSP/Broadband/Satellite must equal "TSP"
    isp=True  tsp=True   → EITHER condition satisfied  (OR logic)
    isp=False tsp=False  → no filter — always passes
    """
    if not isp and not tsp:
        return True

    usage_match = (dest.get('Usage') or '').strip().lower() == 'isp'
    tsp_match   = (dest.get('TSP/Broadband/Satellite') or '').strip().upper() == 'TSP'

    if isp and tsp:
        return usage_match or tsp_match
    if isp:
        return usage_match
    return tsp_match


# ═══════════════════════════════════════════════════════════════════════════
#  SESSION SPLITTER
# ═══════════════════════════════════════════════════════════════════════════

def _split_session(record: dict, split_time: int, d_fmt: str, t_fmt: str,
                   dt_fmt: str, split_datetime: bool) -> list[dict]:
    """
    Split a single record into multiple chunks of at most split_time minutes.

    The original record must contain '_start_dt' and '_end_dt' internal keys
    (set by _build_record before formatting). Each chunk gets its own
    start/end datetime and Minutes value. All other fields are copied as-is.

    Returns a list of records (length 1 when no split needed).
    """
    start_dt: datetime = record['_start_dt']
    end_dt:   datetime = record['_end_dt']
    total_minutes: int = record['Minutes']

    # No split needed
    if total_minutes <= split_time:
        return [record]

    chunks: list[dict] = []
    chunk_start = start_dt
    remaining   = total_minutes

    while remaining > 0:
        chunk_mins  = min(remaining, split_time)
        chunk_end   = chunk_start + timedelta(minutes=chunk_mins)

        chunk = {k: v for k, v in record.items()
                 if not k.startswith('_')}          # copy, drop internals
        chunk['Minutes'] = chunk_mins

        if split_datetime:
            chunk['Start Date'] = _apply_format(chunk_start, d_fmt)
            chunk['Start Time'] = _apply_format(chunk_start, t_fmt)
            chunk['End Date']   = _apply_format(chunk_end,   d_fmt)
            chunk['End Time']   = _apply_format(chunk_end,   t_fmt)
        else:
            chunk['Start Date & Time'] = _apply_format(chunk_start, dt_fmt)
            chunk['End Date & Time']   = _apply_format(chunk_end,   dt_fmt)

        # Preserve internal operator key for bucketing
        chunk['_operator']  = record['_operator']
        chunk['_start_dt']  = chunk_start
        chunk['_end_dt']    = chunk_end

        chunks.append(chunk)
        chunk_start = chunk_end
        remaining  -= chunk_mins

    return chunks


# ═══════════════════════════════════════════════════════════════════════════
#  RECORD BUILDER
# ═══════════════════════════════════════════════════════════════════════════

def _build_record(
    ipdr: dict,
    nexus: dict,
    *,
    port_gte: int,
    port_lte: int,
    inc_msisdn: bool,
    is_indian: bool,
    is_tsp: bool,
    ext_time: int,
    isp: bool,
    tsp: bool,
    split_datetime: bool,
    date_fmt: str,
    time_fmt: str,
    datetime_fmt: str,
    default_date_fmt: str,
    default_time_fmt: str,
    default_datetime_fmt: str,
    # operator_filter replaced by op_mode + op_set
    op_mode: str,          # 'requisition' | 'multi'
    op_set: set[str],      # empty = requisition / all; non-empty = scoped operators
    network_wise: bool,
    remove_ipinfo: bool,
    # session adjustment
    adjust_session: bool,
    session_op_keys: set[str],   # empty set = all operators
    split_time: int,
) -> list[dict]:
    """
    Build one or more output record dicts for a single IPDR row.
    Returns [] if the record is filtered out.
    Returns a list of 1+ dicts (split chunks when adjust_session=true).

    Custom-format scoping
    ---------------------
    network_wise=False + op_mode='requisition'
      → custom format applied to ALL records in data[]

    network_wise=True  + op_mode='multi' + op_set={'airtel','vi'}
      → custom format applied only to records whose operator is in op_set
      → records NOT in op_set use default format

    Filter order
    ------------
    1. Port range
    2. Country       (is_indian)
    3. TSP flag      (is_tsp — driven by filter_type)
    4. ISP/TSP usage (isp / tsp payload flags)

    Field order in output record
    ----------------------------
    IPDR → MSISDN (if requested) → Type → Value → Port →
    Session Start Date + Session Start Time + Session End Date + Session End Time
      — or —
    Session Start DateTime + Session End DateTime  (split_datetime=false) →
    Minutes →
    [Port Info → Port Category → Port Type → Usage → TSP/Broadband/Satellite →
     Isp/Org → App/Hostname → Domains → Location → Country]
    """
    # ── 1. Port filter ────────────────────────────────────────────────────
    dest_port = ipdr.get('Destination_port')
    if dest_port is not None and not (port_gte <= dest_port <= port_lte):
        return []

    # ── 2. Country filter ─────────────────────────────────────────────────
    dest = ipdr.get('Destination_Detail') or {}
    if is_indian and dest.get('Country', '').lower() != 'india':
        return []

    # ── 3. TSP filter (driven by filter_type) ────────────────────────────
    usage_type = dest.get('TSP/Broadband/Satellite')
    if is_tsp and usage_type not in ('TSP', 'BROADBAND'):
        return []

    # ── 4. ISP / TSP usage filter (isp / tsp payload flags) ──────────────
    if not _passes_isp_tsp(dest, isp, tsp):
        return []

    # ── Datetime ──────────────────────────────────────────────────────────
    sdate, stime = _split_dt(ipdr.get('SDateTime'))
    edate, etime = _split_dt(ipdr.get('EDateTime'))

    try:
        start_dt = datetime.strptime(f'{sdate} {stime}', '%Y-%m-%d %H:%M:%S')
        end_dt   = datetime.strptime(f'{edate} {etime}', '%Y-%m-%d %H:%M:%S')
    except Exception as e:
        print(f'⚠️  Date parsing error: {e}')
        return []

    duration_minutes = round(int(ipdr.get('Duration', 0)) / 60)

    # ── Operator resolution ───────────────────────────────────────────────
    domains_str = (dest.get('Domains') or '').lower()
    tsp_str     = (usage_type or '').upper()
    operator    = _operator_for_record(domains_str, tsp_str)

    # ── Determine whether this operator is in session_op scope ───────────
    # session_op_keys empty = all operators; otherwise scoped
    in_session_scope = (not session_op_keys) or (operator in session_op_keys)

    # ── Apply ext_time (only within session_op scope) ─────────────────────
    effective_ext = ext_time if in_session_scope else 0
    if effective_ext > 0:
        delta             = timedelta(minutes=effective_ext / 2)
        start_dt         -= delta
        end_dt           += delta
        duration_minutes += effective_ext

    # ── Custom format scoping ─────────────────────────────────────────────
    #
    # network_wise=False + op_mode='requisition'
    #   → use_custom=True for ALL records (custom fmt applied to all data[])
    #
    # network_wise=True  + op_mode='multi'
    #   → use_custom=True only when this record's operator is in op_set
    #
    # Any other combination falls back to default format.
    #
    if not network_wise and op_mode == _REQUISITION:
        use_custom = True
    elif network_wise and op_mode == 'multi' and op_set:
        use_custom = operator in op_set
    else:
        use_custom = False

    d_fmt  = date_fmt     if use_custom else default_date_fmt
    t_fmt  = time_fmt     if use_custom else default_time_fmt
    dt_fmt = datetime_fmt if use_custom else default_datetime_fmt

    # ── Port info ─────────────────────────────────────────────────────────
    port = ipdr.get('Destination_port_Detail') or {}

    # ── IPDR value ────────────────────────────────────────────────────────
    ipdr_val = nexus.get('IPDR') or ipdr.get('Destination_ip')

    # ── Build record (strict field order) ─────────────────────────────────
    record: dict = {}

    # 1. IPDR
    record['IPDR'] = ipdr_val

    # 2. MSISDN (immediately after IPDR, only when requested)
    if inc_msisdn:
        record['MSISDN'] = _resolve_msisdn(ipdr_val, ipdr.get('MSISDN'))

    # 3. Type + Value + Port
    record['Type']  = _ip_version(ipdr.get('Destination_ip', ''))
    record['Value'] = ipdr.get('Destination_ip')
    record['Port']  = dest_port

    # 4. Session datetime fields
    if split_datetime:
        record['Start Date'] = _apply_format(start_dt, d_fmt)
        record['Start Time'] = _apply_format(start_dt, t_fmt)
        record['End Date']   = _apply_format(end_dt,   d_fmt)
        record['End Time']   = _apply_format(end_dt,   t_fmt)
    else:
        record['Start Date & Time'] = _apply_format(start_dt, dt_fmt)
        record['End Date & Time']   = _apply_format(end_dt,   dt_fmt)

    # 5. Duration
    record['Minutes'] = duration_minutes

    # 6. IP detail fields — omitted entirely when remove_ipinfo=True
    if not remove_ipinfo:
        record['Port Info']               = port.get('Description')
        record['Port Category']           = port.get('Category')
        record['Port Type']               = port.get('Type')
        record['Usage']                   = dest.get('Usage')
        record['TSP/Broadband/Satellite'] = usage_type
        record['Isp/Org']                 = dest.get('Isp/Org')
        record['App/Hostname']            = dest.get('App/Hostname')
        record['Domains']                 = dest.get('Domains')
        record['Location']                = dest.get('Location')
        record['Country']                 = dest.get('Country')

    # 7. Internal keys — stripped before response by _strip_internal()
    record['_operator'] = operator
    record['_start_dt'] = start_dt
    record['_end_dt']   = end_dt

    # ── Session splitting (only within session_op scope) ──────────────────
    if adjust_session and in_session_scope and split_time > 0:
        return _split_session(record, split_time, d_fmt, t_fmt, dt_fmt, split_datetime)

    return [record]


def _strip_internal(records: list[dict]) -> list[dict]:
    """Remove internal keys (prefixed with _) before sending response."""
    return [{k: v for k, v in r.items() if not k.startswith('_')} for r in records]


# ═══════════════════════════════════════════════════════════════════════════
#  FILTER TYPE → (port_gte, port_lte, is_tsp)
# ═══════════════════════════════════════════════════════════════════════════

_FILTER_CFG: dict[str, tuple[int, int, bool]] = {
    'tsp_session_4_5': (1000,  9999,     True),
    'tsp_session_5':   (10000, 99999,    True),
    'tsp_session':     (0,     10000000, True),
    'requisition':     (0,     10000000, False),
}


# ═══════════════════════════════════════════════════════════════════════════
#  EMPTY RESPONSE SKELETON
# ═══════════════════════════════════════════════════════════════════════════

def _empty_response(
    page: int,
    limit: int,
    op_mode: str,
    op_set: set[str],
    network_wise: bool,
    message: str,
) -> dict:
    # operator_filter in response: comma-joined op_set or 'requisition'
    op_label = ','.join(sorted(op_set)) if op_set else op_mode
    base = {
        'page':            page,
        'limit':           limit,
        'total_records':   0,
        'total_pages':     0,
        'count':           0,
        'operator_filter': op_label,
        'operator_counts': {**{op: 0 for op in OPERATOR_KEYS}, 'other': 0},
        'returned_count':  0,
        'message':         message,
    }
    if network_wise:
        base.update({op: [] for op in OPERATOR_KEYS})
        base['other'] = []
    else:
        base['data'] = []
    return base


# ═══════════════════════════════════════════════════════════════════════════
#  API VIEW
# ═══════════════════════════════════════════════════════════════════════════

class IPRequisitionAPIView(APIView):

    def post(self, request):
        try:
            print(f"\n{'=' * 60}")
            print("INCOMING REQUEST DATA:")
            print(f"{'=' * 60}")
            print(f"Request body: {request.data}")
            print(f"{'=' * 60}\n")

            data = request.data

            # ── seq_ids ───────────────────────────────────────────────────
            seq_ids = data.get('seq_ids', [])
            if not seq_ids or not isinstance(seq_ids, list):
                return Response(
                    {'error': 'seq_ids must be provided as a list'}, status=400
                )
            print(f"📋 seq_ids: {seq_ids}")

            # ── Date range ────────────────────────────────────────────────
            from_date = _safe_parse_datetime(data.get('from_date'))
            to_date   = _safe_parse_datetime(data.get('to_date'))
            if not from_date or not to_date:
                return Response({
                    'error': 'Valid from_date and to_date are required.',
                    'received': {
                        'from_date': str(data.get('from_date')),
                        'to_date':   str(data.get('to_date')),
                    },
                }, status=400)

            # ── filter_type ───────────────────────────────────────────────
            filter_type = data.get('filter_type')
            if not filter_type:
                return Response({'error': 'filter_type is required'}, status=400)
            if filter_type not in _FILTER_CFG:
                return Response({
                    'error': f'Unknown filter_type: {filter_type!r}. '
                             f'Valid values: {list(_FILTER_CFG)}',
                }, status=400)
            port_gte, port_lte, is_tsp = _FILTER_CFG[filter_type]

            # ── Scalar / boolean fields ───────────────────────────────────
            inc_msisdn    = _parse_bool(data.get('msisdn'),        False)
            is_indian     = _parse_bool(data.get('is_indian'),     False)
            remove_ipinfo = _parse_bool(data.get('remove_ipinfo'), False)
            ext_time      = int(data.get('ext_time', 0))
            split_time    = int(data.get('split_time', 0))
            page          = int(data.get('page',  1))
            limit         = int(data.get('limit', 1000))

            # ── Session adjustment fields ─────────────────────────────────
            adjust_session  = _parse_bool(data.get('adjust_session'), False)
            session_op_keys = _parse_session_op(data.get('session_op'))

            print(f"✂️  adjust_session={adjust_session}  split_time={split_time}  "
                  f"session_op={session_op_keys or 'all'}")

            # ── ISP / TSP usage filters ───────────────────────────────────
            isp_filter = _parse_bool(data.get('isp'), False)
            tsp_filter = _parse_bool(data.get('tsp'), False)
            print(f"🔍 isp={isp_filter}  tsp={tsp_filter}")

            # ── network_wise toggle ───────────────────────────────────────
            network_wise = _parse_bool(data.get('network_wise'), False)
            print(f"🌐 network_wise={network_wise}")

            # ── Operator filter parsing ───────────────────────────────────
            #
            # op_mode='requisition' (default)
            #   → valid only when network_wise=False
            #   → custom datetime format applied to ALL records in data[]
            #
            # op_mode='multi', op_set={'airtel','vi',...}
            #   → valid only when network_wise=True
            #   → custom format scoped to operators in op_set
            #   → if network_wise=False with a multi value, treat as requisition
            #
            op_mode, op_set = _parse_operator_filter(data.get('operator'))

            # Enforce mode / network_wise compatibility
            if not network_wise and op_mode == 'multi':
                # multi-operator only makes sense with network_wise=true; fall back
                print(f"⚠️  operator={op_set} ignored: multi-operator requires network_wise=true; "
                      "falling back to requisition mode")
                op_mode = _REQUISITION
                op_set  = set()

            if network_wise and op_mode == _REQUISITION:
                # requisition mode doesn't apply when network_wise=true;
                # no custom-format scoping — default formats used for all buckets
                print("ℹ️  operator=requisition ignored for network_wise=true "
                      "(no custom-format scoping applied)")

            op_label = ','.join(sorted(op_set)) if op_set else op_mode
            print(f"🔀 operator_mode={op_mode!r}  op_set={op_set or 'n/a'}  "
                  f"op_label={op_label!r}")

            # ── Format fields ─────────────────────────────────────────────
            split_datetime   = _parse_bool(data.get('split_datetime'), True)

            date_fmt_raw     = data.get('date_f')          or _DEFAULT_DATE_FMT
            time_fmt_raw     = data.get('time_f')          or _DEFAULT_TIME_FMT
            datetime_fmt_raw = data.get('datetime_format') or _DEFAULT_DATETIME_FMT

            print(f"📆 date_f={date_fmt_raw!r}  time_f={time_fmt_raw!r}  "
                  f"datetime_format={datetime_fmt_raw!r}")
            print(f"🔀 split_datetime={split_datetime}")
            print(f"🧹 remove_ipinfo={remove_ipinfo}  inc_msisdn={inc_msisdn}")

            # ── Validate format tokens ────────────────────────────────────
            if split_datetime:
                for fmt_str, label in (
                    (date_fmt_raw, 'date_f'),
                    (time_fmt_raw, 'time_f'),
                ):
                    if not _has_tokens(fmt_str):
                        return Response(
                            {'error': f'"{fmt_str}" for {label} contains no recognised '
                                      'format tokens (e.g. dd, MM, yyyy, HH, mm, ss).'},
                            status=400,
                        )
            else:
                if not _has_tokens(datetime_fmt_raw):
                    return Response(
                        {'error': f'"{datetime_fmt_raw}" for datetime_format contains no '
                                  'recognised format tokens.'},
                        status=400,
                    )

            # ── Fetch nexus records ───────────────────────────────────────
            try:
                nexus_qs = IPDRNexus.objects.filter(id__in=seq_ids)
                if not nexus_qs:
                    return Response({
                        'error': 'No IPDR Nexus records found for provided seq_ids',
                        'seq_ids_provided': seq_ids,
                    }, status=404)
                print(f"✅ Found {len(nexus_qs)} nexus records")
            except Exception as e:
                return Response({
                    'error': f'Error fetching IPDR Nexus records: {e}',
                    'seq_ids_provided': seq_ids,
                }, status=404)

            nexus_map = {str(n.id): IPDRNexusSerializer(n).data for n in nexus_qs}

            # ── MongoDB connection ────────────────────────────────────────
            try:
                db         = get_db('ipdr_db')
                collection = db['IPDetailRecords']
            except Exception as e:
                return Response({'error': f'Database connection error: {e}'}, status=500)

            # ── Aggregation pipeline ──────────────────────────────────────
            pipeline = [
                {
                    '$match': {
                        'seq_id':    {'$in': seq_ids},
                        'SDateTime': {'$gte': from_date, '$lte': to_date},
                    }
                },
                {'$sort': {'SDateTime': 1}},
                {
                    '$group': {
                        '_id': {
                            'Destination_ip': '$Destination_ip',
                            'SDateTime':      '$SDateTime',
                        },
                        'doc': {'$first': '$$ROOT'},
                    }
                },
                {'$replaceRoot': {'newRoot': '$doc'}},
                {'$sort': {'SDateTime': 1}},
            ]

            try:
                results = list(collection.aggregate(pipeline))
            except Exception as e:
                return Response({'error': f'MongoDB aggregation error: {e}'}, status=500)

            total_db_records = len(results)
            print(f"📊 Total DB records: {total_db_records}")

            if total_db_records == 0:
                return Response(_empty_response(
                    page, limit, op_mode, op_set, network_wise,
                    'No records found in database for the given criteria',
                ), status=200)

            # ── IP / Port lookups ─────────────────────────────────────────
            dest_ips, dest_ports = set(), set()
            for doc in results:
                if doc.get('Destination_ip'):
                    dest_ips.add(doc['Destination_ip'])
                if doc.get('Destination_port') is not None:
                    dest_ports.add(doc['Destination_port'])

            try:
                ip_info = search_ip(list(dest_ips))
            except Exception as e:
                print(f"⚠️  IP lookup error: {e}")
                ip_info = {'results': []}

            lookup_dest_ip = {item['ip']: item for item in ip_info.get('results', [])}

            try:
                lookup_dest_port = {
                    item['id']: item
                    for item in PortInfoSerializer(
                        PortInfo.objects.filter(id__in=dest_ports), many=True
                    ).data
                }
            except Exception as e:
                print(f"⚠️  Port lookup error: {e}")
                lookup_dest_port = {}

            # ── Shared kwargs for _build_record ───────────────────────────
            build_kw = dict(
                port_gte             = port_gte,
                port_lte             = port_lte,
                inc_msisdn           = inc_msisdn,
                is_indian            = is_indian,
                is_tsp               = is_tsp,
                ext_time             = ext_time,
                isp                  = isp_filter,
                tsp                  = tsp_filter,
                split_datetime       = split_datetime,
                date_fmt             = date_fmt_raw,
                time_fmt             = time_fmt_raw,
                datetime_fmt         = datetime_fmt_raw,
                default_date_fmt     = _DEFAULT_DATE_FMT,
                default_time_fmt     = _DEFAULT_TIME_FMT,
                default_datetime_fmt = _DEFAULT_DATETIME_FMT,
                op_mode              = op_mode,
                op_set               = op_set,
                network_wise         = network_wise,
                remove_ipinfo        = remove_ipinfo,
                adjust_session       = adjust_session,
                session_op_keys      = session_op_keys,
                split_time           = split_time,
            )

            # ── Process records ───────────────────────────────────────────
            print(f"🔄 Processing {len(results)} records…")
            all_records: list[dict] = []

            for ipdr in results:
                seq_id_str = _normalize_seq_id(ipdr.get('seq_id'))
                nexus      = nexus_map.get(seq_id_str, {})

                ipdr['Destination_Detail']      = lookup_dest_ip.get(ipdr.get('Destination_ip'))
                ipdr['Destination_port_Detail'] = lookup_dest_port.get(ipdr.get('Destination_port'))

                records = _build_record(ipdr, nexus, **build_kw)
                all_records.extend(records)

            total_filtered = len(all_records)

            if total_filtered == 0:
                print("❌ No records match filter criteria")
                return Response(_empty_response(
                    page, limit, op_mode, op_set, network_wise,
                    'No records found matching the specified filters',
                ), status=200)

            # ── Bucket by operator ────────────────────────────────────────
            operator_buckets: defaultdict[str, list] = defaultdict(list)
            for rec in all_records:
                operator_buckets[rec['_operator']].append(rec)

            operator_counts = {op: len(operator_buckets[op]) for op in OPERATOR_KEYS}
            operator_counts['other'] = len(operator_buckets['other'])

            # ── returned_count scoping ────────────────────────────────────
            #
            # network_wise=False + op_mode='requisition'
            #   → returned_count = total_filtered  (all records in data[])
            #
            # network_wise=True  + op_mode='multi'
            #   → returned_count = sum of records in op_set operators only
            #
            # network_wise=True  + op_mode='requisition'
            #   → returned_count = total_filtered  (no scoping)
            #
            if network_wise and op_mode == 'multi' and op_set:
                returned_count = sum(len(operator_buckets[op]) for op in op_set)
            else:
                returned_count = total_filtered

            # ── Pagination (flat list only) ────────────────────────────────
            total_pages  = (total_filtered + limit - 1) // limit
            start_index  = (page - 1) * limit
            page_records = all_records[start_index: start_index + limit]

            print(f"\n{'=' * 60}")
            print("RESULTS:")
            print(f"  Total DB records : {total_db_records}")
            print(f"  Total filtered   : {total_filtered}")
            print(f"  Operator mode    : {op_mode}  op_set={op_set or 'n/a'}")
            print(f"  Returned count   : {returned_count}")
            print(f"  network_wise     : {network_wise}")
            print(f"  remove_ipinfo    : {remove_ipinfo}")
            print(f"  adjust_session   : {adjust_session}  split_time={split_time}")
            print(f"  session_op       : {session_op_keys or 'all'}")
            print(f"  isp={isp_filter}  tsp={tsp_filter}")
            print(f"  Operator counts  : {operator_counts}")
            print(f"  Page {page}/{total_pages}, limit {limit} "
                  f"→ {len(page_records)} records")
            print(f"{'=' * 60}\n")

            # ── Build response ────────────────────────────────────────────
            response: dict = {
                'page':            page,
                'limit':           limit,
                'total_records':   total_filtered,
                'total_pages':     total_pages,
                'count':           len(page_records),
                'operator_filter': op_label,
                'operator_counts': operator_counts,
                'returned_count':  returned_count,
            }

            if network_wise:
                # network_wise=True → per-operator buckets (full, not paginated)
                # 'data' key is NOT included
                response.update({
                    op: _strip_internal(list(operator_buckets[op]))
                    for op in OPERATOR_KEYS
                })
                response['other'] = _strip_internal(list(operator_buckets['other']))
            else:
                # network_wise=False (default) → flat paginated data[] list
                response['data'] = _strip_internal(page_records)

            return Response(response, status=200)

        except ValueError as e:
            print(f"❌ ValueError: {e}")
            return Response({'error': f'Invalid value: {e}'}, status=400)
        except Exception as e:
            import traceback
            print(f"\n{'!' * 60}\nCRITICAL ERROR:\n{'!' * 60}")
            print(traceback.format_exc())
            print(f"{'!' * 60}\n")
            return Response({'error': f'Internal server error: {e}'}, status=500)