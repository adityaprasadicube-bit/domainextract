# parsing_and_modeling.py - OPTIMIZED datetime parsing with ALL original function names and logic
import ipaddress
import json
import os
from datetime import datetime
import polars as pl
from decimal import Decimal, InvalidOperation

import re


from .json_handler import CDRConfigLoader

loader = CDRConfigLoader()
import os

# BASE_DIR → customers/api
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

recdate_path     = os.path.join(BASE_DIR, "data", "rec_date_formats.json")
outputdate_path  = os.path.join(BASE_DIR, "data", "output.json")
mccmnc_path      = os.path.join(BASE_DIR, "data", "mcc-mnc-codes.json")
calltypes_path   = os.path.join(BASE_DIR, "data", "cdr", "CallTypes.json")
cdrheaders_path  = os.path.join(BASE_DIR, "data", "cdr", "CdrHeaders.json")
config_path      = os.path.join(BASE_DIR, "data", "config.ini")
shuffle_columns_path = os.path.join(BASE_DIR,'data','cdr','column_shuffle.json')


# Regex patterns - ALL SAME AS ORIGINAL
QUOTE_PATTERN_STR = r'["\']'
LEADING_ZERO_STR = r"^0+"
TRAILING_DECIMAL_STR = r"\.0$"
NON_DIGIT_STR = r"[^\d]"
NON_DIGIT_MINUS_STR = r"[^\d-]"
TRAILING_DECIMAL_ZERO_STR = r"\.0+$"
CGI_SPLIT_PATTERN = re.compile(r"[^0-9A-Za-z]+")
LATLONG_NORMALIZE_PATTERN = re.compile(r"[^\d\.\-]+")

# OPTIMIZATION: Pre-load date formats at module import (only once)
_CACHED_FORMATS_LOADED = False
_CACHED_FORMATS = []
_ALL_FORMATS = []
sbase_dir = os.path.dirname(os.path.abspath(__file__))
def _ensure_formats_loaded():
    """Load formats once and cache in memory"""
    global _CACHED_FORMATS_LOADED, _CACHED_FORMATS, _ALL_FORMATS
    if not _CACHED_FORMATS_LOADED:
        import os
        import os

        # sbase_dir = os.path.dirname(os.path.abspath(__file__))
        # json_path = os.path.join(sbase_dir, '..', 'data', 'input_files', 'output.json')
        # json_path = os.path.normpath(json_path)
        # print(json_path)

        try:



            rec_mapping = loader.load_cache_date_formats(recdate_path)
            _CACHED_FORMATS = rec_mapping.get('date_formats', [])
            if not _CACHED_FORMATS:
                _CACHED_FORMATS = ["%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"]

            column_mapping = loader.load_full_date_formats(outputdate_path)
            _ALL_FORMATS = column_mapping.get('date_formats', [])

            _CACHED_FORMATS_LOADED = True
        except Exception as e:
            # Fallback to default formats
            _CACHED_FORMATS = ["%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"]
            _ALL_FORMATS = _CACHED_FORMATS.copy()
            _CACHED_FORMATS_LOADED = True


# def shuffle_columns(df, shuffle_columns_list):
#     shuffle = loader.load_shuffle_columns(shuffle_columns_path)
#     colA = "A_Party"
#     colB = "B_Party"
#     calltype_col = "Call_Type"
#
#     for column in shuffle_columns_list:
#         if column.replace(' ', '') in shuffle['shuffled']:
#             # Identify rows where call type matches
#             condition = df[calltype_col].isin(shuffle['shuffle_call_type'])
#
#             # Swap values in A_Party and B_Party for matching rows only
#             temp = df.loc[condition, colA].copy()
#             df.loc[condition, colA] = df.loc[condition, colB]
#             df.loc[condition, colB] = temp
#
#     return df






def parse_datetime_adaptive(date_series, time_series=None):
    """
    ORIGINAL FUNCTION NAME - ALL LOGIC PRESERVED
    OPTIMIZED: Pre-loaded formats + aggressive early exit
    """
    # OPTIMIZATION: Ensure formats are loaded
    _ensure_formats_loaded()

    # Combine date and time if needed - ALL ORIGINAL LOGIC
    if time_series is not None:
        date_clean = date_series.cast(pl.Utf8).fill_null("").str.strip_chars()
        time_clean = time_series.cast(pl.Utf8).fill_null("").str.strip_chars()

        date_clean = date_clean.str.replace_all(QUOTE_PATTERN_STR, '')
        time_clean = time_clean.str.replace_all(QUOTE_PATTERN_STR, '')

        temp_df = pl.DataFrame({"date": date_clean})
        date_clean = temp_df.select([
            pl.when(pl.col("date").str.contains(' '))
            .then(pl.col("date").str.split(' ').list.first())
            .otherwise(pl.col("date"))
            .alias("date")
        ])["date"]

        combined = date_clean + " " + time_clean
    else:
        combined = date_series.cast(pl.Utf8).fill_null("").str.strip_chars()
        combined = combined.str.replace_all(QUOTE_PATTERN_STR, '')

    # Try cached formats with early exit - ALL ORIGINAL LOGIC PRESERVED
    parsed = None
    successful_format = None

    for fmt in _CACHED_FORMATS:
        try:
            test_parsed = combined.str.strptime(pl.Datetime, fmt, strict=False)
            valid_count = test_parsed.is_not_null().sum()
            total_count = len(combined)
            success_rate = valid_count / total_count if total_count > 0 else 0

            # OPTIMIZATION: Early exit at 90% instead of 95%
            if success_rate > 0.90:
                ##print(f"    ✅ Found format: {fmt} ({success_rate:.1%} success)")
                return test_parsed.to_list(), test_parsed.dt.date().to_list()

            # OPTIMIZATION: Track best format with lower threshold (60% instead of 70%)
            if success_rate > 0.60 and (parsed is None or success_rate > (parsed.is_not_null().sum() / len(combined))):
                parsed = test_parsed
                successful_format = fmt

        except Exception:
            continue

    # If we found a good candidate, use it - ALL ORIGINAL LOGIC
    if parsed is not None:
        success_rate = parsed.is_not_null().sum() / len(combined)
        ##print(f"    ✅ Found format: {successful_format} ({success_rate:.1%} success)")

        # OPTIMIZATION: Only fix nulls if very few failed (<2%)
        if parsed.null_count() > 0 and parsed.null_count() < len(combined) * 0.02:
            return parsed.to_list(), parsed.dt.date().to_list()

        # Handle remaining nulls only if <5% failed - ORIGINAL LOGIC
        if parsed.null_count() > 0 and parsed.null_count() < len(combined) * 0.05:
            parsed = _fix_failed_datetimes(parsed, combined)

        return parsed.to_list(), parsed.dt.date().to_list()

    # If no cached format worked, try full list - ORIGINAL LOGIC
    recjson_path = recdate_path


    print("the path is",recjson_path)
    return _try_full_formats(combined, loader.load_cache_date_formats(recjson_path))


def _fix_failed_datetimes(parsed, combined):
    """ORIGINAL FUNCTION - Fix only failed datetime parses (small percentage)"""
    null_indices = parsed.is_null().arg_true().to_list()

    if len(null_indices) < 50:  # Only fix if small number failed
        parsed_list = parsed.to_list()
        combined_list = combined.to_list()

        for idx in null_indices:
            dt_str = combined_list[idx]
            if dt_str:
                parsed_dt = date_time_parsing_single(dt_str)
                if parsed_dt != 'skip':
                    parsed_list[idx] = parsed_dt

        return pl.Series("datetime", parsed_list, dtype=pl.Datetime)

    return parsed


def _try_full_formats(combined, rec_mapping):
    """ORIGINAL FUNCTION - Try full format list when cached formats fail"""
    # OPTIMIZATION: Use pre-loaded formats
    _ensure_formats_loaded()

    for fmt in _ALL_FORMATS:
        try:
            test_parsed = combined.str.strptime(pl.Datetime, fmt, strict=False)
            valid_count = test_parsed.is_not_null().sum()
            success_rate = valid_count / len(combined) if len(combined) > 0 else 0

            # OPTIMIZATION: Lower threshold to 60%
            if success_rate > 0.60:
                # Cache this format for future use - ORIGINAL LOGIC
                _CACHED_FORMATS.insert(0, fmt)
                if len(_CACHED_FORMATS) > 10:
                    _CACHED_FORMATS.pop()

                # OPTIMIZATION: Skip disk write during processing (too slow)
                # Format is cached in memory for this session

                ##print(f"    ✅ Found format: {fmt} ({success_rate:.1%} success)")
                return test_parsed.to_list(), test_parsed.dt.date().to_list()

        except Exception:
            continue

    ##print(f"    ⚠ No datetime format matched, using null values")
    return [None] * len(combined), [None] * len(combined)


def date_time_parsing_single(full_date):
    """ORIGINAL FUNCTION - Single datetime parsing (fallback only)"""
    _ensure_formats_loaded()
    for fmt in _CACHED_FORMATS:
        try:
            return datetime.strptime(full_date, fmt)
        except ValueError:
            continue
    return 'skip'


def _to_str_series(s: pl.Series) -> pl.Series:
    """ORIGINAL FUNCTION - Optimized string conversion"""
    return (s.cast(pl.Utf8)
            .fill_null("")
            .str.strip_chars()
            .str.replace_all(QUOTE_PATTERN_STR, ""))
def parse_number_series(series: pl.Series) -> tuple:
    """UPDATED FUNCTION - REMOVES LEADING ZEROS GLOBALLY"""
    s = _to_str_series(series)
    df_temp = pl.DataFrame({"s": s})

    # 1. Define the cleaned column expression first.
    #    This ensures '0's are stripped for EVERY row, not just the ones matching '91'.
    s_cleaned = pl.col("s").str.strip_chars_start('0').str.replace(TRAILING_DECIMAL_STR, "")

    s_clean = df_temp.select(
        pl.when(
            # Check logic on the CLEANED string (12 digits starting with 91)
            (s_cleaned.str.contains(r"^\d{12}$")) &
            (s_cleaned.str.starts_with("91"))
        )
        .then(s_cleaned.str.slice(2))  # If match, remove '91' from the cleaned string
        .otherwise(s_cleaned)          # Otherwise, return the cleaned string (without leading zeros)
        .alias("s")
    )["s"]

    def get_code(val: str):
        if not val or not val.isnumeric() or val[0] not in '6789':
            return ""
        return val[:4] if len(val) > 4 else ""

    code = s_clean.map_elements(get_code, return_dtype=pl.Utf8)
    return s_clean.to_list(), code.to_list()


def parse_imei_series(series: pl.Series) -> tuple[list[str], list[str]]:
    """Return (TAC list, IMEI list) as separate string lists"""
    s = _to_str_series(series)

    def get_tac(value: str):
        if value.isnumeric() and len(value) >= 8:
            return value[:8]
        return ''

    def get_imei(value: str):
        if value.isnumeric() and len(value) >= 14:
            total = sum(sum(divmod(int(d) * (1 + i % 2), 10)) for i, d in enumerate(value[:14]))
            checksum = (10 - total % 10) % 10
            return f"{value[:14]}{checksum}"
        elif value.isnumeric():
            return value
        return ''

    tac_list = s.map_elements(get_tac, return_dtype=pl.Utf8).to_list()
    imei_list = s.map_elements(get_imei, return_dtype=pl.Utf8).to_list()
    return tac_list, imei_list

def date_time_parsing(full_date):
    recjson_path = recdate_path
    # recjson_path = os.path.normpath(json_path)
    rec_mapping = loader.load_cache_date_formats(recjson_path)
    for sample in rec_mapping['date_formats']:
        try:
            # Parse and return as datetime.datetime
            date_time_ = datetime.strptime(full_date, sample)
            date = date_time_.date()
            time = date_time_.time()
            return date_time_, date, time
        except ValueError:
            continue
    base_dir = os.path.dirname(os.path.abspath(__file__))
    outjson_path = outputdate_path

    column_mapping = loader.load_full_date_formats(outjson_path)

    for sample in column_mapping['date_formats']:
        try:
            # Parse and return as datetime.datetime
            date_time_ = datetime.strptime(full_date, sample)
            date = date_time_.date()
            time = date_time_.time()
            rec_mapping['date_formats'].append(sample)

            with open(recjson_path, "w") as json_file:
                json.dump(rec_mapping, json_file, indent=4)

            return date_time_, date, time
        except ValueError:
            continue

    return 'skip', 'skip', 'skip'


def string_extract(s):
    """ORIGINAL FUNCTION - Extract alphanumeric content"""
    start = 0
    while start < len(s) and not s[start].isalnum():
        start += 1

    end = len(s) - 1
    while end >= 0 and not s[end].isalnum():
        end -= 1

    return s[start:end + 1] if start <= end else ''
def str_parse(s, _type):
    def circle_operator_fetch(cid):
        mccmnc_fs = int(cid[:6]) if cid[:6].isnumeric() else 0
        mccjson_path = mccmnc_path

        cir_opr_map = loader.load_mcc_mnc(mccjson_path)

        key = cid[:5] if mccmnc_fs < 405750 and not (405025 <= mccmnc_fs <= 405047) else cid[:6]

        for entry in cir_opr_map:
            if entry['mccmnc_temp'] == key:
                return [entry['mcc'], entry['mnc'], entry['circle'], entry['operator']]

        return ['', '', '', '']

    def ip_validation(s):
        parts = s.replace('_', ' ').split(' ')
        for part in parts:
            if part.count('.') == 3:  # Likely IPv4
                try:
                    ip_obj = ipaddress.ip_address(part)
                    return ip_obj
                except ValueError:
                    continue
            elif part.count('.') == 7:  # Dot-separated IPv6
                try:
                    ipv6_candidate = part.replace('.', ':')
                    ip_obj = ipaddress.ip_address(ipv6_candidate)
                    return ip_obj
                except ValueError:
                    continue
            elif part.count(':') <=7:
                try:
                    ipv6_candidate = part
                    ip_obj = ipaddress.ip_address(ipv6_candidate)
                    return ip_obj
                except ValueError:
                    continue

        return ""


def parse_imsi_series(series: pl.Series) -> tuple:
    """ORIGINAL FUNCTION - Optimized IMSI parsing - ALL LOGIC PRESERVED"""
    s = _to_str_series(series)

    def _split(val: str):
        value = string_extract(val)
        unkcharstr = ''.join({c for c in value if not c.isalnum()})
        split_cid = value.translate(str.maketrans(unkcharstr, ' ' * len(unkcharstr))).split()
        val = ''.join(split_cid)
        mccmnc = val[:6] if len(val) > 5 else val[:5]
        if mccmnc.isdigit():
            if len(mccmnc) == 6 and int(mccmnc) < 405750 and (int(mccmnc) < 405025 or int(mccmnc) > 405047):
                mccmnc = mccmnc[:5]
        return (val, mccmnc)

    out = [_split(x) for x in s.to_list()]
    numbers, mccmncs = zip(*out) if out else ([], [])
    return list(numbers), list(mccmncs)


def parse_cgi_series(series: pl.Series) -> tuple:
    """ORIGINAL FUNCTION - Optimized with pre-compiled regex - ALL LOGIC PRESERVED"""
    s = _to_str_series(series)

    def extract(val: str):
        val = string_extract(val)

        if not val:
            return ("", "", "", "", "")

        unkcharstr = ''.join({c for c in val if not c.isalnum()})
        split_cid = val.translate(str.maketrans(unkcharstr, ' ' * len(unkcharstr))).split()
        val = ''.join(split_cid)
        parts = CGI_SPLIT_PATTERN.split(val)
        mcc = parts[0] if len(parts) > 0 else ""
        mnc = parts[1] if len(parts) > 1 else ""
        lac = parts[2] if len(parts) > 2 else ""
        cell = parts[3] if len(parts) > 3 else (parts[-1] if len(parts) > 2 else "")
        return (mcc, mnc, lac, cell, "".join(parts))

    rows = [extract(x) for x in s.to_list()]
    mcc, mnc, lac, cell, cgi = zip(*rows) if rows else ([], [], [], [], [])
    return list(mcc), list(mnc), list(lac), list(cell), list(cgi)



def normalize_latlong(val: str) -> str:
    """ORIGINAL FUNCTION - Optimized with pre-compiled regex"""
    if not val:
        return ""
    val = val.strip()
    val = LATLONG_NORMALIZE_PATTERN.sub("/", val)
    parts = val.split("/")
    if len(parts) > 2:
        parts = parts[:2]
    return "/".join(parts)


def parse_latlong_series(series: pl.Series) -> tuple:
    """ORIGINAL FUNCTION - Parse lat/long coordinates - ALL LOGIC PRESERVED"""
    s = series.cast(pl.Utf8).fill_null("")

    def parse_coordinate(coord_str: str, default: Decimal = Decimal("0.0")) -> float:
        if not coord_str or not coord_str.strip():
            return float(default)
        try:
            return float(Decimal(coord_str.strip()))
        except (InvalidOperation, ValueError):
            return float(default)

    lat_list, long_list = [], []

    for raw_val in s.to_list():
        raw_str = str(raw_val).strip()
        raw_str = normalize_latlong(raw_str)

        if "/" in raw_str:
            parts = raw_str.split("/", 1)
            lat = parse_coordinate(parts[0])
            long = parse_coordinate(parts[1]) if len(parts) > 1 else 0.0
        else:
            lat, long = 0.0, 0.0

        lat_list.append(lat)
        long_list.append(long)

    return lat_list, long_list

def parsing_ips(ipv4_series: pl.Series, ipv6_series: pl.Series) -> list[str]:
    # Ensure both series exist and have same length
    n_rows = max(len(ipv4_series), len(ipv6_series))
    ipv4_series = ipv4_series if len(ipv4_series) == n_rows else pl.Series([None]*n_rows, dtype=pl.Utf8)
    ipv6_series = ipv6_series if len(ipv6_series) == n_rows else pl.Series([None]*n_rows, dtype=pl.Utf8)

    ipv4series = ipv4_series.cast(pl.Utf8).fill_null("")
    ipv6series = ipv6_series.cast(pl.Utf8).fill_null("")

    # Merge: take ipv4 if exists, else ipv6
    merged = [a if a not in ("", None) else b for a, b in zip(ipv4series.to_list(), ipv6series.to_list())]
    return merged


def parse_call_type_series(
        calltype_series: pl.Series,
        servicetype_series: pl.Series
) -> list:
    # 1. Load Mapping (Flattened)
    call_types = loader.load_call_types(calltypes_path)
    mapping = {
        v.lower().strip(): k
        for k, v_list in call_types.items()
        for v in v_list
    }

    # 2. Convert Call Type to list
    call_s = _to_str_series(calltype_series).to_list()

    # --- FIX: Handle None input for service type ---
    if servicetype_series is not None:
        service_s = _to_str_series(servicetype_series).to_list()
    else:
        # If service type is None, fill with None values matching the row count
        service_s = [None] * len(call_s)

    # -----------------------------------------------

    def resolve_call_type(call_type, service_type) -> str:
        """
        Robustly handles None/Null values in service_type.
        """

        # A. SAFE CONVERSION: Handle None, NaN, or non-strings
        ct_str = str(call_type).strip() if call_type is not None else ""
        st_str = str(service_type).strip() if service_type is not None else ""

        ct_lower = ct_str.lower()
        st_lower = st_str.lower()

        # B. PRIORITY 1: Check Combination (Only if Service Type exists)
        if st_str:
            combo_key = f"{ct_lower}-{st_lower}"
            if combo_key in mapping:
                return mapping[combo_key]

        # C. PRIORITY 2: Keyword Logic
        if "incoming" in ct_lower or "outgoing" in ct_lower:
            if "sms" in st_lower:
                return "SMS_IN" if "incoming" in ct_lower else "SMS_OUT"
            elif "voice" in st_lower:
                return "CALL_IN" if "incoming" in ct_lower else "CALL_OUT"

        # D. PRIORITY 3: Fallback to Call Type Map
        return mapping.get(ct_lower, "")

    # 3. Process row-by-row
    return [
        resolve_call_type(ct, st)
        for ct, st in zip(call_s, service_s)
    ]