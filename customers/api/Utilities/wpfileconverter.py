"""
Universal Record Extractor API  (v2)
=====================================
Auto-discovers every column that exists in the file and returns
a full-fidelity JSON record for each row — no hardcoded field names.

Supported file formats
──────────────────────
1. HTML with <table>  (e.g. Google Takeout / Subscriber Info)
   → Headers taken from <th> cells; one dict per data row.

2. HTML with Meta/WhatsApp CSS key-value layout (.t .i / .m divs)
   → Sections auto-detected; repeating key groups become individual records.
   → Page-break orphan fix: anonymous wrapper divs that appear after a
     page boundary are detected and their value is backfilled into the
     preceding empty field.
   → Concatenation fix: value extraction uses only the direct first-child
     NavigableString so that sibling record blocks are never pulled into
     the current field value (critical for IPv6 addresses).

3. Plain-text WhatsApp / Meta exports (.txt)
   → State-machine parser that captures every field in every block.

4. Archives (.zip / .rar) containing any of the above.

Django view:  POST /api/records/extract/
              multipart/form-data  →  field name: "file"

Response shape:
{
  "success": true,
  "file":    "export.html",
  "count":   42,
  "columns": ["IP Address", "Time", ...],
  "records": [
    {"IP Address": "119.42.58.120", "Time": "2024-07-14 12:22:43 UTC"},
    ...
  ]
}
"""

from __future__ import annotations

import io
import ipaddress as _ipaddress
import os
import re
import zipfile
import shutil
from typing import Generator

from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

# ── optional RAR support ─────────────────────────────────────────────────────
try:
    import rarfile as _rarfile_module
    _RAR_AVAILABLE = True
except ImportError:
    _rarfile_module = None
    _RAR_AVAILABLE = False

# ── BeautifulSoup (required for HTML; txt/zip still work without it) ─────────
try:
    from bs4 import BeautifulSoup as _BS

    def _make_soup(markup: str):
        try:
            return _BS(markup, "lxml")
        except Exception:
            return _BS(markup, "html.parser")

except ImportError:
    _make_soup = None  # type: ignore[assignment]


# ═══════════════════════════════════════════════════════════════════════════
#  NOISE / PAGINATION HELPERS
#  Unified with wp_utils._METADATA_PATTERNS — single source of truth.
# ═══════════════════════════════════════════════════════════════════════════

# Matches any pagination header produced by Meta or WhatsApp exports.
_META_RE = re.compile(
    r"(WhatsApp Business Record|Meta Platforms Business Record)\s+Page\s+\d+"
    r"|^Page \d+$"
    r"|^Record Page$"
    r"|^Business Record$",
    re.IGNORECASE | re.MULTILINE,
)

# Used by _clean_meta_value to strip inline page-header noise from values.
_PAGE_HEADER_RE = re.compile(
    r"(?:Meta Platforms Business Record|WhatsApp Business Record)\s+Page\s+\d+\s*"
    r"|^Page\s+\d+\s*",
    re.IGNORECASE,
)


def _is_noise(line: str) -> bool:
    """Return True if *line* is a pagination/header artefact, not real data."""
    return bool(_META_RE.search(line.strip()))


def _clean_meta_value(value: str, key: str = "") -> str:
    """
    Strip page-header noise that may be embedded inside a field value,
    then for Time fields extract just the timestamp portion.
    """
    if not value or not isinstance(value, str):
        return value

    # Remove any embedded page-header strings.
    value = _PAGE_HEADER_RE.sub("", value)
    value = re.sub(r"\s+", " ", value).strip()

    if key == "Time":
        patterns = [
            r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+(?:UTC|Z|GMT|[+\-]\d{2}:?\d{2})?",
            r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}",
            r"\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}:\d{2}\s+(?:AM|PM)?",
        ]
        for pattern in patterns:
            m = re.search(pattern, value, re.IGNORECASE)
            if m:
                return m.group(0).strip()

    return value


# ═══════════════════════════════════════════════════════════════════════════
#  IP CLEANER
# ═══════════════════════════════════════════════════════════════════════════

def _clean_ip(raw: str) -> str:
    """
    Normalise an IP address string:
      [2401:4900:...:b639]:49493  →  2401:4900:...:b639   (IPv6 literal + port)
      91.209.212.204:51147        →  91.209.212.204        (IPv4 + port)
      2401:4900:...:b639          →  2401:4900:...:b639    (already clean)
    Returns the original string unchanged if it cannot be parsed.
    """
    if not raw or not isinstance(raw, str):
        return raw
    candidate = raw.strip()

    # Strip IPv6 bracket notation:  [addr]:port  or  [addr]
    m = re.match(r"^\[(.+)\](?::\d+)?$", candidate)
    if m:
        candidate = m.group(1)
    else:
        # Strip port from IPv4:  x.x.x.x:port
        m4 = re.match(r"^(\d{1,3}(?:\.\d{1,3}){3}):\d+$", candidate)
        if m4:
            candidate = m4.group(1)

    try:
        return str(_ipaddress.ip_address(candidate))
    except ValueError:
        return raw


def _apply_clean_ip(record: dict) -> dict:
    """Apply _clean_ip to the 'IP Address' key of a record dict if present."""
    if "IP Address" in record:
        record = dict(record)
        record["IP Address"] = _clean_ip(record["IP Address"])
    return record


# ═══════════════════════════════════════════════════════════════════════════
#  ARCHIVE HELPERS
# ═══════════════════════════════════════════════════════════════════════════

_ARCHIVE_EXTS = {".zip", ".rar"}
_CONTENT_EXTS = {".html", ".htm", ".txt"}


def _is_archive(name: str) -> bool:
    return any(name.endswith(e) for e in _ARCHIVE_EXTS)


def _is_content(name: str) -> bool:
    return any(name.endswith(e) for e in _CONTENT_EXTS)


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
    for candidate in (
        "unrar", "UnRAR",
        r"C:\Program Files\WinRAR\UnRAR.exe",
        r"C:\Program Files (x86)\WinRAR\UnRAR.exe",
    ):
        path = shutil.which(candidate) or (candidate if os.path.isfile(candidate) else None)
        if path:
            _rarfile_module.UNRAR_TOOL = path
            return _rarfile_module
    return _rarfile_module


def _extract_from_archive(
    archive_obj, archive_type: str = "zip", _depth: int = 0
) -> Generator[tuple[str, str], None, None]:
    MAX_DEPTH = 10
    if _depth > MAX_DEPTH:
        return

    if isinstance(archive_obj, (bytes, bytearray)):
        file_obj = io.BytesIO(archive_obj)
        raw_archive = archive_obj
    else:
        file_obj = archive_obj
        raw_archive = None

    try:
        if archive_type == "rar":
            if raw_archive is None:
                file_obj.seek(0)
                raw_archive = file_obj.read()
            rf_mod = _get_rarfile()
            if rf_mod is None:
                return
            with rf_mod.RarFile(io.BytesIO(raw_archive)) as rf:
                for entry in rf.infolist():
                    if entry.is_dir():
                        continue
                    try:
                        raw_bytes = rf.read(entry.filename)
                    except Exception:
                        continue
                    base = os.path.basename(entry.filename)
                    nl = base.lower()
                    if _is_archive(nl):
                        ct = "rar" if nl.endswith(".rar") else "zip"
                        yield from _extract_from_archive(io.BytesIO(raw_bytes), ct, _depth + 1)
                    elif _is_content(nl):
                        yield base, raw_bytes.decode("utf-8", errors="ignore")
        else:
            file_obj.seek(0)
            with zipfile.ZipFile(file_obj, "r") as zf:
                for entry in zf.infolist():
                    if entry.is_dir():
                        continue
                    try:
                        raw_bytes = zf.read(entry.filename)
                    except Exception:
                        continue
                    base = os.path.basename(entry.filename)
                    nl = base.lower()
                    if _is_archive(nl):
                        ct = "rar" if nl.endswith(".rar") else "zip"
                        yield from _extract_from_archive(io.BytesIO(raw_bytes), ct, _depth + 1)
                    elif _is_content(nl):
                        yield base, raw_bytes.decode("utf-8", errors="ignore")
    except Exception:
        return


# ═══════════════════════════════════════════════════════════════════════════
#  FORMAT DETECTION
# ═══════════════════════════════════════════════════════════════════════════

def _detect_format(content: str) -> str:
    lower = content[:2000].lower()
    if "<html" in lower or "<!doctype" in lower:
        return "html"
    txt_indicators = [
        "Service\tWhatsApp", "Account Identifier\t",
        "Message Log\t", "Call Log\t", "Message\t", "Call\t",
    ]
    for indicator in txt_indicators:
        if indicator in content:
            return "txt"
    return "html"


# ═══════════════════════════════════════════════════════════════════════════
#  FORMAT 1 — HTML <table>  (Google Subscriber Info, etc.)
# ═══════════════════════════════════════════════════════════════════════════

def _extract_html_tables(soup) -> list[dict]:
    """
    Find every <table> in the document.
    Headers = <th> text in the first row.
    Records = one dict per subsequent <tr>, keyed by those headers.
    """
    records: list[dict] = []

    for table in soup.find_all("table"):
        all_rows = table.find_all("tr")
        if not all_rows:
            continue

        headers: list[str] = [
            th.get_text(strip=True)
            for th in all_rows[0].find_all(["th", "td"])
        ]
        if not headers:
            continue

        for tr in all_rows[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if not cells:
                continue
            while len(cells) < len(headers):
                cells.append("")
            record: dict = dict(zip(headers, cells[: len(headers)]))
            records.append(record)

    return records


def _nearest_heading(tag) -> str:
    """Walk backwards through siblings & ancestors to find the nearest heading."""
    heading_tags = {"h1", "h2", "h3", "h4", "h5", "h6", "b", "strong"}
    for sibling in tag.find_previous_siblings():
        if sibling.name and sibling.name.lower() in heading_tags:
            text = sibling.get_text(strip=True)
            if text:
                return text
    for parent in tag.parents:
        for sibling in parent.find_previous_siblings():
            if sibling.name and sibling.name.lower() in heading_tags:
                text = sibling.get_text(strip=True)
                if text:
                    return text
    return "Unknown"


# ═══════════════════════════════════════════════════════════════════════════
#  FORMAT 2 — HTML key-value  (Meta / WhatsApp CSS class layout)
#
#  DOM structure:
#    <div class="t o">
#      <div class="t i">
#        LABEL_TEXT
#        <div class="m"><div>VALUE_TEXT<div class="p"></div></div></div>
#      </div>
#    </div>
#
#  Known failure modes handled
#  ───────────────────────────
#  A. Page-break orphan
#     A page boundary falls between a label ("Time") and its value.
#     Meta emits an anonymous .t.i wrapper (no label) after the break
#     whose nested content IS the missing value.  Detected by:
#       label == ""  AND  raw_value != ""  AND  last_label field is empty.
#     The value is backfilled into the pending empty field.
#
#  B. Value concatenation (IPv6 + subsequent records)
#     get_text(recursive=True) on a .m div pulls ALL descendant text,
#     including text from sibling .t.o record blocks that happen to be
#     nested inside the same .m container.  Fixed by reading only the
#     direct first-child <div>'s own NavigableString (via _first_text),
#     which contains only the field value and nothing else.
# ═══════════════════════════════════════════════════════════════════════════

def _first_text(tag) -> str:
    """
    Return the first direct NavigableString child of *tag* that is non-empty.
    Does NOT recurse into child elements — this is intentional so that
    sibling record blocks nested inside the same container are never pulled
    into the current field's value.
    """
    from bs4 import NavigableString
    for child in tag.children:
        if isinstance(child, NavigableString):
            t = child.strip()
            if t:
                return t
    return ""


def _extract_html_keyvalue(soup) -> list[dict]:
    records: list[dict] = []

    all_ti_divs = soup.find_all(
        "div",
        class_=lambda c: c and "t" in c.split() and "i" in c.split(),
    )

    current_record: dict = {}
    last_label: str | None = None

    for ti in all_ti_divs:
        label = _first_text(ti)

        m_div = ti.find("div", class_="m", recursive=False)
        raw_value = ""
        if m_div:
            first_child = m_div.find("div", recursive=False)
            if first_child:
                raw_value = _first_text(first_child)
            else:
                raw_value = _first_text(m_div)
        raw_value = raw_value.strip()

        # ── Anonymous div → page-break orphan handler ─────────────────────
        if not label:
            if (
                raw_value
                and last_label is not None
                and current_record.get(last_label, "") == ""
            ):
                if last_label == "IP Address":
                    current_record[last_label] = _clean_ip(raw_value)
                else:
                    current_record[last_label] = _clean_meta_value(raw_value, last_label)

                # FIX 2: backfill may have completed the pair — flush now.
                if "IP Address" in current_record and current_record.get("Time"):
                    records.append(current_record.copy())
                    current_record = {}

                last_label = None
            continue

        # ── Labeled field ──────────────────────────────────────────────────
        if label == "IP Address":
            if "IP Address" in current_record:
                records.append(current_record.copy())
                current_record = {}
            current_record["IP Address"] = _clean_ip(raw_value)
            last_label = "IP Address"

        elif label == "Time":
            current_record["Time"] = _clean_meta_value(raw_value, "Time")
            last_label = "Time"
            # FIX 1: only flush when Time is non-empty.
            # If empty, keep the record open so the orphan handler above
            # can backfill the value when the anonymous wrapper div arrives.
            if "IP Address" in current_record and current_record["Time"]:
                records.append(current_record.copy())
                current_record = {}
                last_label = None

    if "IP Address" in current_record:
        records.append(current_record.copy())

    return records


# ═══════════════════════════════════════════════════════════════════════════
#  FORMAT 3 — Plain-text  (WhatsApp / Meta TXT exports)
# ═══════════════════════════════════════════════════════════════════════════

_BLOCK_MARKERS: set[str] = {"Message", "Call"}

_KNOWN_KEYS: set[str] = {
    # WhatsApp message fields
    "Timestamp", "Message Id", "Sender", "Recipients",
    "Sender Ip", "Sender Port", "Sender Device",
    "Type", "Message Style", "Message Size",
    "Encrypted Message Content", "Group Id",
    "Target Ip", "Target Port", "Target Device",
    # WhatsApp call fields
    "Call Id", "Call Creator", "Events",
    "From", "To", "From Ip", "From Port", "Media Type",
    "Participants", "Phone Number", "State", "Platform",
    # Meta / Facebook / Instagram fields
    "Service", "Account Identifier", "Account Type",
    "Internal Ticket Number", "Target", "Alternate Target IDs",
    "Generated", "Date Range",
    "IP Address", "Time",
    "Registration Date", "Registration Ip",
    "Account Closure Date", "Account Still Active",
    "First", "Name", "Vanity Name",
    "Registered Email Addresses",
    "Phone Numbers",
}

_LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 \-/()]{1,50}$")


def _looks_like_key(line: str) -> bool:
    """Return True if *line* looks like a field-label rather than a value."""
    return line in _KNOWN_KEYS or bool(_LABEL_RE.match(line))


def _extract_txt_records(lines: list[str]) -> list[dict]:
    """
    State-machine parser that auto-collects all key-value pairs per block.

    Page-break noise lines (matched by _is_noise) are silently skipped
    both as block boundaries and as pending-key values, so a timestamp
    that appears right after a page header is still correctly assigned
    to the preceding empty field.
    """
    records: list[dict] = []
    current: dict[str, str] = {}
    block_type: str | None = None
    pending_key: str | None = None

    def flush():
        nonlocal current, block_type
        if current:
            for key in list(current.keys()):
                if "IP" in key or "ip" in key:
                    current[key] = _clean_ip(current[key])
                if "Time" in key or "time" in key:
                    current[key] = _clean_meta_value(current[key], "Time")
            records.append(dict(current))
        current.clear()

    for raw in lines:
        line = raw.strip()

        # Skip blank lines and pagination noise entirely.
        if not line or _is_noise(line):
            continue

        # ── Block boundary ───────────────────────────────────────────────
        if line in _BLOCK_MARKERS:
            flush()
            block_type = line
            pending_key = None
            continue

        # ── Value capture (follows a pending key) ────────────────────────
        if pending_key is not None:
            if current.get(pending_key):
                # Key already has a value → flush, start a new sub-record.
                flush()
            current[pending_key] = line
            pending_key = None
            continue

        # ── Key detection ─────────────────────────────────────────────────
        if _looks_like_key(line):
            pending_key = line
            continue

        # Fallback: unrecognised line after no pending key → ignore.

    flush()
    return records


# ═══════════════════════════════════════════════════════════════════════════
#  PUBLIC ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

def extract_all_records(content: str) -> list[dict]:
    """
    Parse any supported WhatsApp / Meta / Google export and return
    a list of record dicts.  For HTML exports only {IP Address, Time}
    pairs are returned; for table-based exports all columns are returned.
    """
    fmt = _detect_format(content)

    if fmt == "html":
        if _make_soup is None:
            # BeautifulSoup unavailable — strip tags and fall back to TXT parser.
            stripped = re.sub(r"<[^>]+>", "\n", content)
            lines = [l.strip() for l in stripped.splitlines() if l.strip()]
            return _extract_txt_records(lines)

        soup = _make_soup(content)

        # Strategy 1: HTML tables (Google Takeout / Subscriber Info format).
        if soup.find("table"):
            return [_apply_clean_ip(r) for r in _extract_html_tables(soup)]

        # Strategy 2: CSS key-value tree (Meta / WhatsApp HTML format).
        raw = _extract_html_keyvalue(soup)
        return [
            {"IP Address": _clean_ip(rec["IP Address"]), "Time": rec.get("Time", "")}
            for rec in raw
            if "IP Address" in rec
        ]

    else:  # txt
        lines = content.splitlines()
        records = _extract_txt_records(lines)
        return [rec for rec in records if "IP Address" in rec]


# ═══════════════════════════════════════════════════════════════════════════
#  DJANGO VIEW
# ═══════════════════════════════════════════════════════════════════════════

@method_decorator(csrf_exempt, name="dispatch")
class RecordExtractorView(View):
    """
    POST /api/records/extract/
    Content-Type: multipart/form-data
    Field: "file"  (.html / .txt / .zip / .rar)
    """

    def post(self, request):
        uploaded = request.FILES.get("file")
        if not uploaded:
            return JsonResponse(
                {"success": False, "error": "No file uploaded. Use field name 'file'."},
                status=400,
            )

        file_name = uploaded.name
        lower_name = file_name.lower()
        all_records: list[dict] = []

        try:
            raw = uploaded.read()
        except Exception as exc:
            return JsonResponse(
                {"success": False, "error": f"Could not read file: {exc}"},
                status=400,
            )

        # ── Archive (.zip / .rar) ────────────────────────────────────────
        if lower_name.endswith(".zip") or lower_name.endswith(".rar"):
            archive_type = "rar" if lower_name.endswith(".rar") else "zip"
            found_any = False
            for inner_name, content in _extract_from_archive(io.BytesIO(raw), archive_type):
                found_any = True
                records = extract_all_records(content)
                for r in records:
                    r["source_file"] = inner_name
                all_records.extend(records)

            if not found_any:
                return JsonResponse(
                    {"success": False, "error": "Archive contains no .html or .txt files."},
                    status=400,
                )

        # ── Plain file (.html / .txt) ────────────────────────────────────
        else:
            try:
                content = raw.decode("utf-8", errors="ignore")
            except Exception as exc:
                return JsonResponse(
                    {"success": False, "error": f"Could not decode file: {exc}"},
                    status=400,
                )
            all_records = extract_all_records(content)

        # ── Collect all column names actually present ────────────────────
        all_columns: list[str] = []
        seen_cols: set[str] = set()
        for rec in all_records:
            for k in rec:
                if k not in seen_cols:
                    seen_cols.add(k)
                    all_columns.append(k)

        return JsonResponse({
            "success": True,
            "file": file_name,
            "count": len(all_records),
            "columns": all_columns,
            "records": all_records,
        })


# ── URL registration ─────────────────────────────────────────────────────────
#
#   from .wp_ip_time_api_v2 import RecordExtractorView
#
#   urlpatterns = [
#       path("api/records/extract/", RecordExtractorView.as_view()),
#   ]