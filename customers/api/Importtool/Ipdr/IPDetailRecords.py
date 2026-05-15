import polars as pl
from datetime import timedelta
import logging
import json

from django.conf import settings

from .rename_columns import renamecolumns
from ..utils.parsers import parse_datetime_adaptive, parse_number_series, parse_imei_series, \
    parse_imsi_series, parse_cgi_series
from ..utils.updating_columnnames import data_seq
import os
import json

json_path = os.path.join(
    settings.BASE_DIR,
    "api", "data", "ipdr", "IPdrHeaders.json"
)
with open(json_path, 'r') as f:
    COLUMN_MAPPINGS = json.load(f)


def str_parse(value, parse_type):
    if value is None or value == '':
        return ''
    value_str = str(value).strip()
    if parse_type == 'number':
        if value_str.endswith('.0'):
            value_str = value_str[:-2]
        clean = ''.join(c for c in value_str if c.isdigit())
        code = clean[:4] if clean and clean[0] in '6789' else ''
        return clean, code
    elif parse_type == 'ip':
        clean = value_str.replace('"', '').replace("'", '').strip()
        return clean if clean else ''
    elif parse_type == 'port':
        clean = ''.join(c for c in value_str if c.isdigit())
        return clean if clean else ''
    return value_str


def _find_column(df: pl.DataFrame, standard_key: str):
    # 1) Check if the column already exists exactly
    if standard_key in df.columns:
        return standard_key

    # 2) Check case-insensitive match of the key itself
    df_cols_lower = {col.lower(): col for col in df.columns}
    if standard_key.lower() in df_cols_lower:
        return df_cols_lower[standard_key.lower()]

    # 3) Proceed with existing COLUMN_MAPPINGS logic
    if standard_key not in COLUMN_MAPPINGS:
        return None

    candidates = COLUMN_MAPPINGS[standard_key]

    for candidate in candidates:
        # exact match
        if candidate in df.columns:
            return candidate

        # case-insensitive match
        if candidate.lower() in df_cols_lower:
            return df_cols_lower[candidate.lower()]

    return None


def process_chunk_optimized(df_input):
    """
    Process IPDR data chunk
    ✅ FIX: Always returns tuple (df, seqdata) - NEVER returns None
    ✅ FIX: Properly handles combined datetime columns (Session Start date & time)
    """
    print("the column names are before rename", df_input.columns)
    # Convert to Polars if needed
    df = df_input if isinstance(df_input, pl.DataFrame) else pl.DataFrame(df_input)
    if df.is_empty():
        print("⚠️ Empty DataFrame received in process_chunk_optimized")
        return pl.DataFrame(), {}

    initial_count = df.height
    print(f"[PROCESS_CHUNK] Initial DataFrame size: {initial_count} rows")

    # Standardize column names
    try:
        df = renamecolumns(df)
        print("columns after rename", df.columns)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return pl.DataFrame(), {}

    # =======================
    # CREATE SDateTime Column - FIXED LOGIC
    # =======================
    try:
        parsed_datetimes = None
        parsed_dates = None

        # 🔧 FIX: Check if start_date contains BOTH date and time
        # To this (adding a fallback to 'start_time'):
        start_date_col = _find_column(df, "start_date") or ("start_time" if "start_time" in df.columns else None)

        if start_date_col and start_date_col in df.columns:
            # Check if column has data
            non_empty = df[start_date_col].cast(pl.Utf8).str.strip_chars().str.len_bytes().sum()

            if non_empty > 0:
                print(f"[PROCESS_CHUNK] ✓ Found datetime column: {start_date_col}")
                sample_values = df[start_date_col].head(3).to_list()
                print(f"[PROCESS_CHUNK] Sample values: {sample_values}")

                # 🔧 Parse as combined datetime (no separate time column needed)
                try:
                    parsed_datetimes, parsed_dates = parse_datetime_adaptive(df[start_date_col])

                    if parsed_datetimes and any(dt is not None for dt in parsed_datetimes):
                        print(
                            f"[PROCESS_CHUNK] ✓ Successfully parsed {sum(1 for dt in parsed_datetimes if dt is not None)}/{len(parsed_datetimes)} datetimes")
                    else:
                        print(f"[PROCESS_CHUNK] ⚠️ parse_datetime_adaptive returned None/empty values")
                        print(f"[PROCESS_CHUNK] Trying alternative parsing...")

                        # 🔧 Fallback: Try parsing with Polars directly
                        try:
                            df_temp = df.with_columns([
                                pl.col(start_date_col)
                                .str.strptime(pl.Datetime, format="%d-%m-%Y %H:%M:%S", strict=False)
                                .alias("sdatetime_temp")
                            ])

                            if df_temp["sdatetime_temp"].null_count() < df.height:
                                parsed_datetimes = df_temp["sdatetime_temp"].to_list()
                                print(f"[PROCESS_CHUNK] ✓ Fallback parsing successful")
                            else:
                                # Inside the datetime parsing block, update the format check:
                                for fmt in ["%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S"]:
                                    try:
                                        # Try a direct cast first for speed
                                        df_temp = df.with_columns([
                                            pl.col(start_date_col).str.to_datetime(format=fmt, strict=False).alias(
                                                "sdatetime_temp")
                                        ])

                                        if df_temp["sdatetime_temp"].null_count() < df.height:
                                            parsed_datetimes = df_temp["sdatetime_temp"]  # Keep it as a Polars Series
                                            print(f"[PROCESS_CHUNK] ✓ Parsed with Polars format: {fmt}")
                                            break
                                    except Exception:
                                        continue
                        except Exception as e:
                            print(f"[PROCESS_CHUNK] Fallback parsing failed: {e}")

                except Exception as e:
                    print(f"[PROCESS_CHUNK] Error in parse_datetime_adaptive: {e}")
                    import traceback
                    traceback.print_exc()

        # Final check
        if parsed_datetimes is None or all(dt is None for dt in parsed_datetimes):
            print(f"[PROCESS_CHUNK] ❌ Could not parse datetime data")
            print(f"[PROCESS_CHUNK] Available columns: {df.columns}")
            print(f"[PROCESS_CHUNK] Sample data from first row:")
            for col in df.columns[:10]:
                print(f"  {col}: {df[col].head(1).to_list()}")
            return pl.DataFrame(), {}

        print(
            f"[PROCESS_CHUNK] ✓ Final parsed count: {sum(1 for dt in parsed_datetimes if dt is not None)}/{len(parsed_datetimes)}")

        df = df.with_columns([
            pl.Series("sdatetime", parsed_datetimes, dtype=pl.Datetime)
        ])
    except Exception as e:
        print(f"[PROCESS_CHUNK] ❌ Error parsing datetime: {e}")
        import traceback
        traceback.print_exc()
        return pl.DataFrame(), {}

    # Filter rows with null SDateTime
    before_filter = df.height
    df = df.filter(pl.col("sdatetime").is_not_null())
    print(f"[PROCESS_CHUNK] After filtering nulls: {df.height} rows (removed {before_filter - df.height})")

    if df.is_empty():
        print("[PROCESS_CHUNK] ⚠️ DataFrame empty after filtering null SDateTime")
        return pl.DataFrame(), {}

    # =======================
    # Compute EDateTime / duration - FIXED
    # =======================
    end_date_col = _find_column(df, "end_date")

    if end_date_col and end_date_col in df.columns:
        try:
            print(f"[PROCESS_CHUNK] Parsing end datetime from: {end_date_col}")
            parsed_end_datetimes, _ = parse_datetime_adaptive(df[end_date_col])

            if not parsed_end_datetimes or all(dt is None for dt in parsed_end_datetimes):
                # Fallback parsing for end_date
                for fmt in ["%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"]:
                    try:
                        df_temp = df.with_columns([
                            pl.col(end_date_col)
                            .str.strptime(pl.Datetime, format=fmt, strict=False)
                            .alias("edatetime_temp")
                        ])
                        if df_temp["edatetime_temp"].null_count() < df.height:
                            parsed_end_datetimes = df_temp["edatetime_temp"].to_list()
                            break
                    except:
                        continue

            if parsed_end_datetimes:
                df = df.with_columns([
                    pl.Series("edatetime", parsed_end_datetimes, dtype=pl.Datetime)
                ])
        except Exception as e:
            print(f"[PROCESS_CHUNK] ⚠️ Could not parse end datetime: {e}")

    if "edatetime" in df.columns:
        df = df.with_columns([
            pl.when(pl.col("edatetime").is_not_null())
            .then(((pl.col("edatetime") - pl.col("sdatetime")).cast(pl.Int64) // 1_000_000))
            .otherwise(0)
            .alias("duration")
        ])
    else:
        df = df.with_columns([pl.lit(0).alias("duration")])

    # =======================
    # Parse MSISDN
    # =======================
    if "msisdn" in df.columns:
        b_nums, b_codes = parse_number_series(df["msisdn"])
        df = df.with_columns([
            pl.Series("msisdn_code", b_codes, dtype=pl.Utf8)
        ])

    # =======================
    # Parse IMEI/IMSI/CGI
    # =======================
    imei_col = _find_column(df, "imei")
    if imei_col:
        imei_tac, imei_clean = parse_imei_series(df[imei_col])
        df = df.with_columns([
            pl.Series("imei", imei_clean, dtype=pl.Utf8),
            pl.Series("imei_tac", imei_tac, dtype=pl.Utf8),
        ])

    imsi_col = _find_column(df, "imsi")
    if imsi_col:
        imsi_clean, imsi_code = parse_imsi_series(df[imsi_col])
        df = df.with_columns([
            pl.Series("imsi_code", imsi_code, dtype=pl.Utf8)
        ])

    cgi_col = _find_column(df, "first_cgi")
    if cgi_col:
        mcc, mnc, lac, cell, cgi = parse_cgi_series(df[cgi_col])
        cgi_clean = [str(c).replace("-", "") if c else "" for c in cgi]
        df = df.with_columns([
            pl.Series("first_cgi", cgi_clean, dtype=pl.Utf8)
        ])

    # =======================
    # Merge IPv4/IPv6 columns
    # =======================
    ip4_cols = ["source_ip", "destination_ip", "translated_ip"]
    ip6_cols = ["source_ipv6", "destination_ipv6", "translated_ipv6"]

    for v4, v6 in zip(ip4_cols, ip6_cols):
        if v4 not in df.columns:
            df = df.with_columns([pl.lit(None, dtype=pl.Utf8).alias(v4)])
        if v6 not in df.columns:
            df = df.with_columns([pl.lit(None, dtype=pl.Utf8).alias(v6)])

        df = df.with_columns([
            pl.when(pl.col(v4).is_not_null() & (pl.col(v4) != ""))
            .then(pl.col(v4))
            .otherwise(pl.col(v6).fill_null(""))
            .alias(v4)
        ])

    # =======================
    # Parse User Info
    # =======================
    username_col = _find_column(df, "username")
    if username_col:
        df = df.with_columns([pl.col(username_col).alias("username")])

    contact_col = _find_column(df, "user_contact")
    if contact_col:
        df = df.with_columns([pl.col(contact_col).alias("user_contact")])

    address_fields = []
    for key in ["user_address", "user_alternate_contact", "user_mail_address"]:
        col = _find_column(df, key)
        if col:
            address_fields.append(pl.col(col).fill_null(""))
    if address_fields:
        df = df.with_columns([
            pl.concat_str(address_fields, separator=" ").str.strip_chars().alias("user_address")
        ])

    # =======================
    # Filter negative durations
    # =======================
    if "duration" in df.columns:
        df = df.with_columns([pl.col("duration").cast(pl.Int64, strict=False).fill_null(0).alias("duration")])
        df = df.filter(pl.col("duration") >= 0)

    # =======================
    # Drop raw columns not needed
    # =======================
    raw_cols_to_drop = ["end_time", "start_time", "end_date", "start_date", "start_datetime1",
                        "source_ipv6", "destination_ipv6", "translated_ipv6", "roaming"]
    df = df.drop([c for c in raw_cols_to_drop if c in df.columns])

    # =======================
    # Generate sequence data
    # =======================
    try:
        seqdata = data_seq(df.to_dicts())
    except Exception as e:
        print(f"[PROCESS_CHUNK] ⚠️ Could not generate seqdata: {e}")
        seqdata = {}

    print(f"[PROCESS_CHUNK] ✅ Processing complete: {df.height} rows, {df.width} columns")
    return df, seqdata