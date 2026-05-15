"""
IP Requisition Report API  –  optimised
========================================
POST /api/ip/requisition/

Supported Format Tokens
------------------------
Date tokens:
    yyyy     → 4-digit year                    2025
    yy       → 2-digit year                    25
    MMMM     → full month name                 September
    MMM      → 3-letter month abbreviation     Sep
    MM       → month zero-padded               09
    dd       → day zero-padded                 05
    d        → day no padding                  5

Time tokens:
    HH       → hour 24h zero-padded            17
    H        → hour 24h no padding             17
    hh       → hour 12h zero-padded            05
    h        → hour 12h no padding             5
    mm       → minute zero-padded              06
    m        → minute no padding               6
    ss       → second zero-padded              50
    s        → second no padding               5
    fff      → milliseconds zero-padded        000
    tt       → AM/PM                           PM

Separators / literals: any character not matching a token is kept as-is
(e.g. /, -, :, T, space, comma).

Operator-scoped formatting
--------------------------
  operator == 'all'
      Custom format (date_format / time_format / datetime_format) is
      applied to every session in every bucket and in sessions[].

  operator == '<key>'  e.g. 'airtel'
      Custom format applied ONLY to sessions whose operator tag matches
      the key.  All other sessions (in every bucket and in sessions[])
      use the default format (yyyyMMdd / HHmmss / yyyyMMdd HHmmss).

Response contract
-----------------
  sessions[]        → ALWAYS contains ALL records (every operator).
                      Each session is formatted according to the scoping
                      rule above.
  returned_sessions → count of sessions in the matched operator bucket
                      (used by the UI tab badge).  Does NOT gate sessions[].
  airtel / bsnl_mtnl / reliance_jio / vi / broadband / other
                    → per-operator lists, each session formatted by scope.

Endpoint
--------
POST /api/ip/requisition/
Content-Type: application/json

Request body
------------
{
  "records":         [{"IP Address": "...", "Time": "2025-01-13 11:45:51 UTC"}, ...],
  "file_timezone":   "UTC +00:00",
  "output_timezone": "India Standard Time +05:30",
  "session_mins":    5,
  "operator":        "all",
  "split_datetime":  true,
  "date_format":     "yyyyMMdd",
  "time_format":     "HHmmss",
  "datetime_format": "yyyyMMdd HHmmss"
}

Response shape (split_datetime=true, operator='airtel')
--------------------------------------------------------
{
  "success": true,
  "total_sessions": 3,
  "returned_sessions": 1,          ← airtel bucket count
  "sessions": [                    ← ALL 3 sessions; airtel→custom, rest→default
    {"Value": "...", "Type": "IPv6",
     "S Date": "05-02-2025",       ← custom  (airtel session)
     "S Time": "51650", ...},
    {"Value": "...", "Type": "IPv4",
     "S Date": "20250304",         ← default (vi session)
     "S Time": "211206", ...},
    ...
  ],
  "airtel":      [...],            ← custom format
  "vi":          [...],            ← default format
  "other":       [...],            ← default format
  ...
}
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from functools import lru_cache

from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from ..searchengine import search_ip


# ═══════════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════

OPERATOR_KEYS = ('airtel', 'bsnl_mtnl', 'reliance_jio', 'vi', 'broadband', 'other')

_DEFAULT_DATE_FMT     = 'yyyyMMdd'
_DEFAULT_TIME_FMT     = 'HHmmss'
_DEFAULT_DATETIME_FMT = 'yyyyMMdd HHmmss'


# ═══════════════════════════════════════════════════════════════════════════
#  OPERATOR → DOMAIN MAPPING  +  PRECOMPILED LOOKUP
# ═══════════════════════════════════════════════════════════════════════════

OPERATOR_DOMAINS: dict[str, list[str]] = {
    'airtel':       ['airtel.in', 'airtel.com', 'airtelbroadband.in'],
    'bsnl_mtnl':    ['bsnl.in', 'bsnl.co.in', 'mtnl.in', 'mtnl.net.in'],
    'reliance_jio': ['jio.com', 'reliancejio.com', 'jionet.com'],
    'vi':           ['idea.adityabirla.com', 'vodafone.in', 'myvi.in',
                     'vilive.in', 'idea.net.in', 'vodafone.com'],
    'broadband':    [],
}

# One compiled regex per operator — O(1) match vs O(n_domains) loop.
_OPERATOR_DOMAIN_RE: list[tuple[str, re.Pattern]] = [
    (op, re.compile('|'.join(re.escape(d) for d in domains), re.IGNORECASE))
    for op, domains in OPERATOR_DOMAINS.items()
    if op != 'broadband' and domains
]


def _operator_for_session(domains_str: str, tsp_str: str) -> str:
    """
    Resolve operator key from pre-normalised strings.
    domains_str must be lowercased, tsp_str must be uppercased by caller.
    """
    for op_key, pattern in _OPERATOR_DOMAIN_RE:
        if pattern.search(domains_str):
            return op_key
    return 'broadband' if tsp_str == 'BROADBAND' else 'other'


# ═══════════════════════════════════════════════════════════════════════════
#  DATETIME FORMAT ENGINE
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

# ORDER IS CRITICAL — longest / most-specific token must precede any token
# sharing its prefix so the alternation regex matches greedily.
#   MMMM > MMM > MM
#   HH   > H
#   hh   > h
#   dd   > d
#   mm   > m
#   ss   > s
#   fff  (no shorter variant)
_TOKENS: list[tuple[str, object]] = [
    # ── Year ──────────────────────────────────────────────────────────────
    ('yyyy', lambda dt: f'{dt.year:04d}'),
    ('yy',   lambda dt: f'{dt.year % 100:02d}'),
    # ── Month ─────────────────────────────────────────────────────────────
    ('MMMM', lambda dt: _MONTH_FULL[dt.month]),
    ('MMM',  lambda dt: _MONTH_ABBR[dt.month]),
    ('MM',   lambda dt: f'{dt.month:02d}'),
    # ── Day ───────────────────────────────────────────────────────────────
    ('dd',   lambda dt: f'{dt.day:02d}'),
    ('d',    lambda dt: f'{dt.day}'),
    # ── Hour 24 h ─────────────────────────────────────────────────────────
    ('HH',   lambda dt: f'{dt.hour:02d}'),
    ('H',    lambda dt: f'{dt.hour}'),
    # ── Hour 12 h ─────────────────────────────────────────────────────────
    ('hh',   lambda dt: f'{(dt.hour % 12) or 12:02d}'),
    ('h',    lambda dt: f'{(dt.hour % 12) or 12}'),
    # ── Minute ────────────────────────────────────────────────────────────
    ('mm',   lambda dt: f'{dt.minute:02d}'),
    ('m',    lambda dt: f'{dt.minute}'),
    # ── Second ────────────────────────────────────────────────────────────
    ('ss',   lambda dt: f'{dt.second:02d}'),
    ('s',    lambda dt: f'{dt.second}'),
    # ── Milliseconds (Python datetime has no sub-second; always "000") ────
    ('fff',  lambda dt: '000'),
    # ── AM / PM ───────────────────────────────────────────────────────────
    ('tt',   lambda dt: 'AM' if dt.hour < 12 else 'PM'),
]

# Single regex — alternation order mirrors _TOKENS so longest match wins.
_TOKEN_PATTERN: re.Pattern = re.compile(
    '(' + '|'.join(re.escape(tok) for tok, _ in _TOKENS) + ')'
)
_TOKEN_MAP: dict[str, object] = {tok: fn for tok, fn in _TOKENS}


def _apply_format(dt: datetime, fmt: str) -> str:
    """
    Render *dt* using a user-supplied token-based format string.

    The format string is split by _TOKEN_PATTERN.  Each fragment is either:
      • a recognised token → replaced by its computed value for dt
      • a literal          → passed through unchanged (separators, T, …)

    Replacement values are never re-scanned (split, not substitute).

    Examples  (dt = 2025-02-05 17:16:50)
    --------------------------------------
    'yyyy-MM-ddTHH:mm:ss'       → '2025-02-05T17:16:50'
    'dd MMMM yyyy hh:mm:ss tt'  → '05 February 2025 05:16:50 PM'
    'yyyyMMddHHmmssfff'         → '20250205171650000'
    'dd-MM-yyyy HH:mm:ss'       → '05-02-2025 17:16:50'
    'MMM dd, yyyy hh:mm tt'     → 'Feb 05, 2025 05:16 PM'
    'hms'                       → '5' + 'm_literal' + … use hh/mm/ss instead
    """
    return ''.join(
        fn(dt) if (fn := _TOKEN_MAP.get(p)) is not None else p
        for p in _TOKEN_PATTERN.split(fmt)
    )


@lru_cache(maxsize=64)
def _validate_format_string(fmt: str, context: str) -> str | None:
    """
    Return an error string if *fmt* contains no recognised tokens, else None.
    Cached — same format/context pair validated on every request.
    """
    if not _TOKEN_PATTERN.search(fmt):
        known = ', '.join(tok for tok, _ in _TOKENS)
        return (
            f'"{fmt}" supplied for {context} contains no recognised format '
            f'tokens. Known tokens: {known}.'
        )
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  TIMEZONE OFFSET TABLE  (derived from TimeZones.csv)
# ═══════════════════════════════════════════════════════════════════════════

_TZ_RAW: list[tuple[str, str]] = [
    ('Dateline Standard Time',             '-12:00'),
    ('UTC-11',                             '-11:00'),
    ('Aleutian Standard Time',             '-10:00'),
    ('Hawaiian Standard Time',             '-10:00'),
    ('Marquesas Standard Time',            '-09:30'),
    ('Alaskan Standard Time',              '-09:00'),
    ('UTC-09',                             '-09:00'),
    ('Pacific Standard Time (Mexico)',     '-08:00'),
    ('UTC-08',                             '-08:00'),
    ('Pacific Standard Time',             '-08:00'),
    ('US Mountain Standard Time',          '-07:00'),
    ('Mountain Standard Time (Mexico)',    '-07:00'),
    ('Mountain Standard Time',             '-07:00'),
    ('Central America Standard Time',      '-06:00'),
    ('Central Standard Time',              '-06:00'),
    ('Easter Island Standard Time',        '-05:00'),
    ('Central Standard Time (Mexico)',     '-06:00'),
    ('Canada Central Standard Time',       '-06:00'),
    ('SA Pacific Standard Time',           '-05:00'),
    ('Eastern Standard Time (Mexico)',     '-05:00'),
    ('Eastern Standard Time',              '-05:00'),
    ('Haiti Standard Time',                '-05:00'),
    ('Cuba Standard Time',                 '-05:00'),
    ('US Eastern Standard Time',           '-05:00'),
    ('Turks And Caicos Standard Time',     '-05:00'),
    ('Paraguay Standard Time',             '-03:00'),
    ('Atlantic Standard Time',             '-04:00'),
    ('Venezuela Standard Time',            '-04:00'),
    ('Central Brazilian Standard Time',    '-03:00'),
    ('SA Western Standard Time',           '-04:00'),
    ('Pacific SA Standard Time',           '-03:00'),
    ('Newfoundland Standard Time',         '-03:30'),
    ('Tocantins Standard Time',            '-03:00'),
    ('E. South America Standard Time',     '-02:00'),
    ('SA Eastern Standard Time',           '-03:00'),
    ('Argentina Standard Time',            '-03:00'),
    ('Greenland Standard Time',            '-03:00'),
    ('Montevideo Standard Time',           '-03:00'),
    ('Magallanes Standard Time',           '-03:00'),
    ('Saint Pierre Standard Time',         '-03:00'),
    ('Bahia Standard Time',                '-03:00'),
    ('UTC-02',                             '-02:00'),
    ('Mid-Atlantic Standard Time',         '-02:00'),
    ('Azores Standard Time',               '-01:00'),
    ('Cape Verde Standard Time',           '-01:00'),
    ('UTC',                                '+00:00'),
    ('GMT Standard Time',                  '+00:00'),
    ('Greenwich Standard Time',            '+00:00'),
    ('W. Europe Standard Time',            '+01:00'),
    ('Central Europe Standard Time',       '+01:00'),
    ('Romance Standard Time',              '+01:00'),
    ('Morocco Standard Time',              '+01:00'),
    ('Sao Tome Standard Time',             '+01:00'),
    ('Central European Standard Time',     '+01:00'),
    ('W. Central Africa Standard Time',    '+01:00'),
    ('Jordan Standard Time',               '+02:00'),
    ('GTB Standard Time',                  '+02:00'),
    ('Middle East Standard Time',          '+02:00'),
    ('Egypt Standard Time',                '+02:00'),
    ('E. Europe Standard Time',            '+02:00'),
    ('Syria Standard Time',                '+02:00'),
    ('West Bank Standard Time',            '+02:00'),
    ('South Africa Standard Time',         '+02:00'),
    ('FLE Standard Time',                  '+02:00'),
    ('Israel Standard Time',               '+02:00'),
    ('Kaliningrad Standard Time',          '+02:00'),
    ('Sudan Standard Time',                '+02:00'),
    ('Libya Standard Time',                '+02:00'),
    ('Namibia Standard Time',              '+02:00'),
    ('Arabic Standard Time',               '+03:00'),
    ('Turkey Standard Time',               '+03:00'),
    ('Arab Standard Time',                 '+03:00'),
    ('Belarus Standard Time',              '+03:00'),
    ('Russian Standard Time',              '+03:00'),
    ('E. Africa Standard Time',            '+03:00'),
    ('Iran Standard Time',                 '+03:30'),
    ('Arabian Standard Time',              '+04:00'),
    ('Astrakhan Standard Time',            '+04:00'),
    ('Azerbaijan Standard Time',           '+04:00'),
    ('Russia Time Zone 3',                 '+04:00'),
    ('Mauritius Standard Time',            '+04:00'),
    ('Saratov Standard Time',              '+04:00'),
    ('Georgian Standard Time',             '+04:00'),
    ('Volgograd Standard Time',            '+04:00'),
    ('Caucasus Standard Time',             '+04:00'),
    ('Afghanistan Standard Time',          '+04:30'),
    ('West Asia Standard Time',            '+05:00'),
    ('Ekaterinburg Standard Time',         '+05:00'),
    ('Pakistan Standard Time',             '+05:00'),
    ('India Standard Time',                '+05:30'),
    ('Sri Lanka Standard Time',            '+05:30'),
    ('Nepal Standard Time',                '+05:45'),
    ('Central Asia Standard Time',         '+06:00'),
    ('Bangladesh Standard Time',           '+06:00'),
    ('Omsk Standard Time',                 '+06:00'),
    ('Myanmar Standard Time',              '+06:30'),
    ('SE Asia Standard Time',              '+07:00'),
    ('Altai Standard Time',                '+07:00'),
    ('W. Mongolia Standard Time',          '+07:00'),
    ('North Asia Standard Time',           '+07:00'),
    ('N. Central Asia Standard Time',      '+07:00'),
    ('Tomsk Standard Time',                '+07:00'),
    ('China Standard Time',                '+08:00'),
    ('North Asia East Standard Time',      '+08:00'),
    ('Singapore Standard Time',            '+08:00'),
    ('W. Australia Standard Time',         '+08:00'),
    ('Taipei Standard Time',               '+08:00'),
    ('Ulaanbaatar Standard Time',          '+08:00'),
    ('Aus Central W. Standard Time',       '+08:45'),
    ('Transbaikal Standard Time',          '+09:00'),
    ('Tokyo Standard Time',                '+09:00'),
    ('North Korea Standard Time',          '+09:00'),
    ('Korea Standard Time',                '+09:00'),
    ('Yakutsk Standard Time',              '+09:00'),
    ('Cen. Australia Standard Time',       '+10:30'),
    ('AUS Central Standard Time',          '+09:30'),
    ('E. Australia Standard Time',         '+10:00'),
    ('AUS Eastern Standard Time',          '+11:00'),
    ('West Pacific Standard Time',         '+10:00'),
    ('Tasmania Standard Time',             '+11:00'),
    ('Vladivostok Standard Time',          '+10:00'),
    ('Lord Howe Standard Time',            '+11:00'),
    ('Bougainville Standard Time',         '+11:00'),
    ('Russia Time Zone 10',                '+11:00'),
    ('Magadan Standard Time',              '+11:00'),
    ('Norfolk Standard Time',              '+11:00'),
    ('Sakhalin Standard Time',             '+11:00'),
    ('Central Pacific Standard Time',      '+11:00'),
    ('Russia Time Zone 11',                '+12:00'),
    ('New Zealand Standard Time',          '+13:00'),
    ('UTC+12',                             '+12:00'),
    ('Fiji Standard Time',                 '+12:00'),
    ('Kamchatka Standard Time',            '+12:00'),
    ('Chatham Islands Standard Time',      '+13:45'),
    ('UTC+13',                             '+13:00'),
    ('Tonga Standard Time',                '+13:00'),
    ('Samoa Standard Time',                '+14:00'),
    ('Line Islands Standard Time',         '+14:00'),
]


def _offset_str_to_minutes(offset_str: str) -> int:
    """Convert '+05:30' or '-03:30' to signed total minutes."""
    m = re.match(r'^([+-])(\d{1,2}):(\d{2})$', offset_str.strip())
    if not m:
        return 0
    sign = 1 if m.group(1) == '+' else -1
    return sign * (int(m.group(2)) * 60 + int(m.group(3)))


def _build_tz_lookup() -> dict[str, int]:
    """
    Build a lookup with multiple key variants per entry.

    Variants per entry (e.g. 'India Standard Time', '+05:30'):
      'India Standard Time'         → 330
      'India Standard Time +05:30'  → 330
      'UTC +05:30'                  → 330
      'UTC+05:30'                   → 330
      '+05:30'                      → 330
      (lower-case copies of all above)
    """
    lookup: dict[str, int] = {}
    for name, offset in _TZ_RAW:
        minutes = _offset_str_to_minutes(offset)
        for key in (
            name.strip(),
            f'{name.strip()} {offset}',
            f'UTC {offset}',
            f'UTC{offset}',
            offset.strip(),
        ):
            lookup[key]         = minutes
            lookup[key.lower()] = minutes
    return lookup


_TZ_LOOKUP: dict[str, int] = _build_tz_lookup()

_TZ_TRAILING_OFFSET_RE = re.compile(r'^(.+?)\s*([+-]\d{1,2}:\d{2})\s*$')
_TZ_BARE_OFFSET_RE     = re.compile(r'([+-]\d{1,2}:\d{2})')


@lru_cache(maxsize=256)
def resolve_offset_minutes(tz_string: str) -> int | None:
    """
    Resolve any timezone string to a signed UTC-offset in minutes.
    Returns None if resolution fails.  Cached — same strings sent every request.

    Resolution order:
      1. Direct lookup (and lower-case variant).
      2. Strip trailing ±HH:MM, look up name, fall back to offset token.
      3. Find any ±HH:MM anywhere in the string.
    """
    s = tz_string.strip()

    if (v := _TZ_LOOKUP.get(s)) is not None:
        return v
    if (v := _TZ_LOOKUP.get(s.lower())) is not None:
        return v

    m = _TZ_TRAILING_OFFSET_RE.match(s)
    if m:
        name_part, offset_part = m.group(1).strip(), m.group(2)
        if (v := _TZ_LOOKUP.get(name_part)) is not None:
            return v
        if (v := _TZ_LOOKUP.get(name_part.lower())) is not None:
            return v
        return _offset_str_to_minutes(offset_part)

    m2 = _TZ_BARE_OFFSET_RE.search(s)
    if m2:
        return _offset_str_to_minutes(m2.group(1))

    return None


# ═══════════════════════════════════════════════════════════════════════════
#  TIME PARSING  (input records)
# ═══════════════════════════════════════════════════════════════════════════

_UTC_SUFFIX_RE = re.compile(r'\s+UTC\s*$', re.IGNORECASE)
_TZ_ABBR_RE    = re.compile(r'\s+[A-Z]{2,5}\s*$')

_STRPTIME_FMTS: tuple[str, ...] = (
    '%Y-%m-%d %H:%M:%S',
    '%Y-%m-%dT%H:%M:%SZ',
    '%Y-%m-%dT%H:%M:%S',
    '%d-%m-%Y %H:%M:%S',
    '%Y/%m/%d %H:%M:%S',
)


def parse_time(time_str: str) -> datetime | None:
    """Parse a record Time string to a naive datetime (no TZ info)."""
    s = _UTC_SUFFIX_RE.sub('', time_str.strip()).strip()
    for fmt in _STRPTIME_FMTS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    s2 = _TZ_ABBR_RE.sub('', s).strip()
    if s2 != s:
        for fmt in _STRPTIME_FMTS:
            try:
                return datetime.strptime(s2, fmt)
            except ValueError:
                pass
    return None


# ═══════════════════════════════════════════════════════════════════════════
#  IP TYPE DETECTION
# ═══════════════════════════════════════════════════════════════════════════

_IPV4_RE = re.compile(r'^\d{1,3}(\.\d{1,3}){3}$')


def detect_ip_type(ip: str) -> str:
    """Return 'IPv4', 'IPv6', or 'Unknown'."""
    c = ip.strip()
    if _IPV4_RE.match(c):
        return 'IPv4'
    if ':' in c:
        return 'IPv6'
    return 'Unknown'


# ═══════════════════════════════════════════════════════════════════════════
#  SESSION BUILDER
# ═══════════════════════════════════════════════════════════════════════════

_ENRICH_KEYS: tuple[str, ...] = (
    'Isp/Org', 'Domains', 'Usage',
    'TSP/Broadband/Satellite', 'App/Hostname',
    'Country', 'Location', 'VPN/Proxy/Tor',
)


def _build_session(
    ip: str,
    ip_type: str,
    start: datetime,
    raw_end: datetime,
    session_mins: int,
    min_dur: timedelta,
    info: dict,
    domains_str: str,
    tsp_str: str,
    split_datetime: bool,
    # Custom formats — applied when this session's operator matches filter
    date_format: str,
    time_format: str,
    datetime_format: str,
    # Default formats — applied to all non-matching sessions
    default_date_fmt: str,
    default_time_fmt: str,
    default_datetime_fmt: str,
    # Scope control
    operator_filter: str,
) -> dict:
    """
    Build one fully-formatted, field-ordered session dict in a single pass.

    Operator-scoped formatting
    --------------------------
    operator_filter == 'all'
        Custom format applied to every session regardless of operator.

    operator_filter == '<key>'  e.g. 'airtel'
        Custom format applied ONLY when this session's resolved operator
        matches the key.  All other sessions use the default format.

    The _sort_key stores the raw start datetime so sessions sort correctly
    regardless of output format (avoids lexicographic bugs with formats
    like 'dd-MMM-yyyy' or 'dd-MM-yyyy').
    """
    end      = max(raw_end, start + min_dur)
    operator = _operator_for_session(domains_str, tsp_str)

    # Determine format set for this session
    use_custom = (operator_filter == 'all' or operator == operator_filter)

    d_fmt  = date_format     if use_custom else default_date_fmt
    t_fmt  = time_format     if use_custom else default_time_fmt
    dt_fmt = datetime_format if use_custom else default_datetime_fmt

    # Build in canonical display order — no separate reorder step needed
    s: dict = {'_sort_key': start, 'Value': ip, 'Type': ip_type}

    if split_datetime:
        s['S Date'] = _apply_format(start, d_fmt)
        s['S Time'] = _apply_format(start, t_fmt)
        s['E Date'] = _apply_format(end,   d_fmt)
        s['E Time'] = _apply_format(end,   t_fmt)
    else:
        s['S DateTime'] = _apply_format(start, dt_fmt)
        s['E DateTime'] = _apply_format(end,   dt_fmt)

    s['Minutes'] = session_mins
    for key in _ENRICH_KEYS:
        s[key] = info.get(key)
    s['operator'] = operator
    return s


def build_sessions(
    records: list[dict],
    file_offset_minutes: int,
    out_offset_minutes: int,
    session_mins: int,
    split_datetime: bool = True,
    date_format: str     = _DEFAULT_DATE_FMT,
    time_format: str     = _DEFAULT_TIME_FMT,
    datetime_format: str = _DEFAULT_DATETIME_FMT,
    operator_filter: str = 'all',
) -> list[dict]:
    """
    Parse records → group by IP → merge into sessions → enrich → format.

    Returns ALL sessions (every operator), each formatted according to
    the operator-scoped rule described in _build_session.

    Output shape
    ------------
    split_datetime=True  → S Date, S Time, E Date, E Time
    split_datetime=False → S DateTime, E DateTime
    """
    # ── 1. Parse & convert all record times ───────────────────────────────
    # Both tz shifts collapsed into one precomputed delta.
    tz_delta = timedelta(minutes=out_offset_minutes - file_offset_minutes)

    ip_hits: defaultdict[str, list[datetime]] = defaultdict(list)
    for rec in records:
        ip       = str(rec.get('IP Address', '')).strip()
        time_raw = str(rec.get('Time', '')).strip()
        if not ip or not time_raw:
            continue
        naive = parse_time(time_raw)
        if naive is not None:
            ip_hits[ip].append(naive + tz_delta)

    if not ip_hits:
        return []

    # ── 2. Bulk IP enrichment ─────────────────────────────────────────────
    try:
        ip_search_result = search_ip(list(ip_hits))
        ip_info_lookup: dict[str, dict] = {
            item['ip']: item
            for item in ip_search_result.get('results', ())
            if isinstance(item, dict) and 'ip' in item
        }
    except Exception as exc:
        print(f'⚠️  search_ip failed: {exc}')
        ip_info_lookup = {}

    # ── 3. Merge hits → sessions, tag operator, apply format ──────────────
    window  = timedelta(minutes=session_mins)
    min_dur = timedelta(minutes=session_mins)

    # Shared kwargs passed to every _build_session call
    _skw = dict(
        session_mins         = session_mins,
        min_dur              = min_dur,
        split_datetime       = split_datetime,
        date_format          = date_format,
        time_format          = time_format,
        datetime_format      = datetime_format,
        default_date_fmt     = _DEFAULT_DATE_FMT,
        default_time_fmt     = _DEFAULT_TIME_FMT,
        default_datetime_fmt = _DEFAULT_DATETIME_FMT,
        operator_filter      = operator_filter,
    )

    sessions: list[dict] = []

    for ip, hits in ip_hits.items():
        hits.sort()
        ip_type     = detect_ip_type(ip)
        info        = ip_info_lookup.get(ip) or {}
        # Normalise once per IP, not once per session
        domains_str = (info.get('Domains') or '').lower()
        tsp_str     = (info.get('TSP/Broadband/Satellite') or '').upper()

        s_start = s_end = hits[0]
        for hit in hits[1:]:
            if hit - s_end <= window:
                s_end = hit
            else:
                sessions.append(_build_session(
                    ip, ip_type, s_start, s_end,
                    info=info, domains_str=domains_str, tsp_str=tsp_str,
                    **_skw,
                ))
                s_start = s_end = hit

        sessions.append(_build_session(
            ip, ip_type, s_start, s_end,
            info=info, domains_str=domains_str, tsp_str=tsp_str,
            **_skw,
        ))

    # ── 4. Sort by raw datetime (correct for all output formats) ──────────
    sessions.sort(key=lambda s: s.pop('_sort_key'))
    return sessions


# ═══════════════════════════════════════════════════════════════════════════
#  DJANGO VIEW
# ═══════════════════════════════════════════════════════════════════════════

def _parse_bool_field(value: object, default: bool = True) -> bool:
    """Coerce a JSON field that may be bool or string to bool."""
    if isinstance(value, str):
        return value.strip().lower() not in ('false', '0', 'no')
    return bool(value)


@method_decorator(csrf_exempt, name='dispatch')
class IPRequisitionViews(View):
    """
    POST /api/ip/requisition/
    Content-Type: application/json

    Required
    --------
    records          list   Non-empty list of {IP Address, Time} objects.
    file_timezone    str    Timezone of the Time values in records.
    output_timezone  str    Timezone for output dates/times.

    Optional
    --------
    session_mins     int    Session window in minutes. Default: 5.
    operator         str    all | airtel | bsnl_mtnl | reliance_jio
                            | vi | broadband | other.  Default: 'all'.
    split_datetime   bool   true  → S Date / S Time / E Date / E Time
                            false → S DateTime / E DateTime. Default: true.
    date_format      str    Format for date columns (split_datetime=true).
                            Default: 'yyyyMMdd'.
    time_format      str    Format for time columns (split_datetime=true).
                            Default: 'HHmmss'.
    datetime_format  str    Format for merged datetime (split_datetime=false).
                            Default: 'yyyyMMdd HHmmss'.

    Response contract
    -----------------
    sessions[]          Always contains ALL records (every operator),
                        each formatted per the operator-scoping rule.
    returned_sessions   Count of sessions in the matched operator bucket
                        (UI tab badge).  Does NOT gate sessions[].
    airtel / vi / …     Per-operator lists, formatted by scope.
    """

    def post(self, request):

        # ── 1. Parse JSON body ─────────────────────────────────────────────
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return JsonResponse(
                {'success': False, 'error': f'Invalid JSON body: {exc}'},
                status=400,
            )

        # ── 2. Extract fields ──────────────────────────────────────────────
        records         = body.get('records')
        file_tz_str     = body.get('file_timezone',   '')
        out_tz_str      = body.get('output_timezone', '')
        session_mins    = int(body.get('session_mins', 5))
        operator_filter = (body.get('operator') or 'all').lower().strip()
        split_datetime  = _parse_bool_field(body.get('split_datetime', True))
        date_format     = body.get('date_format')     or _DEFAULT_DATE_FMT
        time_format     = body.get('time_format')     or _DEFAULT_TIME_FMT
        datetime_format = body.get('datetime_format') or _DEFAULT_DATETIME_FMT

        # ── 3. Validate format strings ─────────────────────────────────────
        if split_datetime:
            for fmt, ctx in (
                (date_format,  'date_format'),
                (time_format,  'time_format'),
            ):
                if err := _validate_format_string(fmt, ctx):
                    return JsonResponse({'success': False, 'error': err}, status=400)
        else:
            if err := _validate_format_string(datetime_format, 'datetime_format'):
                return JsonResponse({'success': False, 'error': err}, status=400)

        # ── 4. Validate required fields ────────────────────────────────────
        if not isinstance(records, list) or not records:
            return JsonResponse(
                {'success': False,
                 'error': '"records" must be a non-empty list of '
                          '{IP Address, Time} objects.'},
                status=400,
            )
        if not file_tz_str:
            return JsonResponse(
                {'success': False, 'error': '"file_timezone" is required.'},
                status=400,
            )
        if not out_tz_str:
            return JsonResponse(
                {'success': False, 'error': '"output_timezone" is required.'},
                status=400,
            )
        if session_mins < 1:
            return JsonResponse(
                {'success': False, 'error': '"session_mins" must be >= 1.'},
                status=400,
            )

        # ── 5. Resolve timezone offsets ────────────────────────────────────
        file_offset = resolve_offset_minutes(file_tz_str)
        if file_offset is None:
            return JsonResponse(
                {'success': False,
                 'error': f'Cannot resolve file_timezone: "{file_tz_str}". '
                          'Use a timezone name or a bare ±HH:MM offset.'},
                status=400,
            )
        out_offset = resolve_offset_minutes(out_tz_str)
        if out_offset is None:
            return JsonResponse(
                {'success': False,
                 'error': f'Cannot resolve output_timezone: "{out_tz_str}". '
                          'Use a timezone name or a bare ±HH:MM offset.'},
                status=400,
            )

        # ── 6. Build sessions ──────────────────────────────────────────────
        try:
            all_sessions = build_sessions(
                records             = records,
                file_offset_minutes = file_offset,
                out_offset_minutes  = out_offset,
                session_mins        = session_mins,
                split_datetime      = split_datetime,
                date_format         = date_format,
                time_format         = time_format,
                datetime_format     = datetime_format,
                operator_filter     = operator_filter,
            )
        except Exception as exc:
            import traceback
            traceback.print_exc()
            return JsonResponse(
                {'success': False, 'error': f'Session building failed: {exc}'},
                status=500,
            )

        # ── 7. Bucket sessions by operator ─────────────────────────────────
        operator_buckets: defaultdict[str, list] = defaultdict(list)
        for s in all_sessions:
            operator_buckets[s['operator']].append(s)

        operator_counts = {op: len(operator_buckets[op]) for op in OPERATOR_KEYS}

        # returned_sessions → operator bucket count (UI tab badge)
        # sessions[]        → always ALL sessions (never filtered)
        returned_count = (
            len(all_sessions)
            if operator_filter == 'all' or operator_filter not in OPERATOR_KEYS
            else len(operator_buckets[operator_filter])
        )

        # ── 8. Return response ─────────────────────────────────────────────
        return JsonResponse({
            'success':               True,
            'total_sessions':        len(all_sessions),
            'returned_sessions':     returned_count,
            'operator_filter':       operator_filter,
            'operator_counts':       operator_counts,
            'file_timezone':         file_tz_str,
            'output_timezone':       out_tz_str,
            'file_offset_minutes':   file_offset,
            'output_offset_minutes': out_offset,
            'session_mins':          session_mins,
            'split_datetime':        split_datetime,
            'date_format':           date_format     if split_datetime     else None,
            'time_format':           time_format     if split_datetime     else None,
            'datetime_format':       datetime_format if not split_datetime else None,
            # sessions → ALL records, each formatted by operator-scope rule
            'sessions':              all_sessions,
            # per-operator buckets
            **{op: list(operator_buckets[op]) for op in OPERATOR_KEYS},
        })

    # ── GET  (health-check / usage hint) ──────────────────────────────────
    def get(self, request):
        return JsonResponse({
            'endpoint':    'POST /api/ip/requisition/',
            'description': 'Generate IP session requisition report.',
            'body_fields': {
                'records':         'list of {IP Address, Time} — required',
                'file_timezone':   'str — timezone of input Times — required',
                'output_timezone': 'str — timezone for output dates — required',
                'session_mins':    'int — session window in minutes, default 5 — optional',
                'operator':        'str — all|airtel|bsnl_mtnl|reliance_jio|vi|broadband|other — optional',
                'split_datetime':  'bool — true: S Date/S Time/E Date/E Time | false: S DateTime/E DateTime — optional',
                'date_format':     'str — date format when split_datetime=true, default "yyyyMMdd" — optional',
                'time_format':     'str — time format when split_datetime=true, default "HHmmss" — optional',
                'datetime_format': 'str — datetime format when split_datetime=false, default "yyyyMMdd HHmmss" — optional',
            },
            'format_tokens': {
                'yyyy': '4-digit year → 2025',
                'yy':   '2-digit year → 25',
                'MMMM': 'full month name → February',
                'MMM':  'month abbreviation → Feb',
                'MM':   'month zero-padded → 02',
                'dd':   'day zero-padded → 05',
                'd':    'day no padding → 5',
                'HH':   'hour 24h zero-padded → 17',
                'H':    'hour 24h no padding → 17',
                'hh':   'hour 12h zero-padded → 05',
                'h':    'hour 12h no padding → 5',
                'mm':   'minute zero-padded → 16',
                'm':    'minute no padding → 6',
                'ss':   'second zero-padded → 50',
                's':    'second no padding → 5',
                'fff':  'milliseconds → 000',
                'tt':   'AM/PM → PM',
            },
            'format_examples': {
                'yyyyMMdd':                  '20250205',
                'yyyy-MM-dd':                '2025-02-05',
                'dd-MM-yyyy':                '05-02-2025',
                'yyyy/MM/dd HH:mm:ss':       '2025/02/05 17:16:50',
                'dd-MM-yyyy HH:mm:ss':       '05-02-2025 17:16:50',
                'dd-MMM-yyyy':               '05-Feb-2025',
                'dd MMMM yyyy':              '05 February 2025',
                'MMM dd, yyyy hh:mm:ss tt':  'Feb 05, 2025 05:16:50 PM',
                'yyyy-MM-ddTHH:mm:ss':       '2025-02-05T17:16:50',
                'yyyyMMddHHmmssfff':         '20250205171650000',
                'yyyyMMdd HHmmss':           '20250205 171650',
            },
            'operator_scoping': {
                'all':      'Custom format applied to every session in every bucket.',
                '<key>':    'Custom format applied only to sessions matching the key; '
                            'all others use default format (yyyyMMdd / HHmmss / yyyyMMdd HHmmss).',
                'sessions': 'Always contains ALL records regardless of operator filter.',
            },
        })


# ── URL registration ──────────────────────────────────────────────────────
#
#   from .ip_requisition_views import IPRequisitionViews
#
#   urlpatterns = [
#       path('api/ip/requisition/', IPRequisitionViews.as_view()),
#   ]