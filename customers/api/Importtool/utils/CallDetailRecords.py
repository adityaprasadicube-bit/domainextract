# CallDetailRecords.py - Fully optimized with adaptive datetime parsing and validation
import time
import polars as pl
import logging

from .parsers import shuffle_columns_path
# ---------------------------
# Logger Setup
# ---------------------------
logger = logging.getLogger(__name__)

from ..utils.parsers import (
    parse_datetime_adaptive, parse_number_series, parse_imei_series,
    parse_imsi_series, parse_cgi_series, parse_latlong_series, parse_call_type_series
)
from ..utils.updating_columnnames import rename_and_filter_columns


def process_chunk_optimized(df_input):
    """Fully optimized chunk processing with adaptive datetime parsing and validation"""
    chunk_start = time.time()
    logger.info("[PROCESS_CHUNK] Started processing chunk")


    file_columns =  df_input.columns

    try:
        # Convert to Polars
        if isinstance(df_input, pl.DataFrame):
            df = df_input
        else:
            logger.info(f"[PROCESS_CHUNK] Converting input type: {type(df_input)}")
            df = pl.DataFrame(df_input)

        logger.info(f"[PROCESS_CHUNK] Input shape: {df.shape}")
    except Exception as e:
        logger.error("[ERROR] Failed during conversion to Polars", exc_info=True)
        return pl.DataFrame()

    # Column rename
    try:
        t1 = time.time()
        df = rename_and_filter_columns(df)
        logger.info(f"[PROCESS_CHUNK] Column rename OK in {time.time() - t1:.3f}s")
    except Exception as e:
        logger.error("[ERROR] Failed in rename_and_filter_columns()", exc_info=True)
        return pl.DataFrame()

    if df.is_empty():
        logger.warning("[PROCESS_CHUNK] Empty dataframe after rename — returning empty")
        return pl.DataFrame()

    # DATETIME PARSING
    try:
        t2 = time.time()

        if "SDate" in df.columns and "STime" in df.columns:
            logger.info("[PROCESS_CHUNK] Parsing Date + Time columns")
            parsed_datetimes, parsed_dates = parse_datetime_adaptive(df["SDate"], df["STime"])

            df = df.with_columns([
                pl.Series("SDateTime", parsed_datetimes, dtype=pl.Datetime),
                pl.Series("SDate", parsed_dates, dtype=pl.Date)
            ])

        elif "SDate" in df.columns:
            logger.info("[PROCESS_CHUNK] Parsing SDate as combined datetime")
            parsed_datetimes, parsed_dates = parse_datetime_adaptive(df["SDate"])
            df = df.with_columns([
                pl.Series("SDateTime", parsed_datetimes, dtype=pl.Datetime),
                pl.Series("SDate", parsed_dates, dtype=pl.Date)
            ])

        else:
            datetime_cols = [col for col in df.columns if 'date' in col.lower() or 'time' in col.lower()]
            if datetime_cols:
                logger.info(f"[PROCESS_CHUNK] Parsing datetime column: {datetime_cols[0]}")
                parsed_datetimes, parsed_dates = parse_datetime_adaptive(datetime_cols[0])
                df = df.with_columns([
                    pl.Series("SDateTime", parsed_datetimes, dtype=pl.Datetime),
                    pl.Series("SDate", parsed_dates, dtype=pl.Date)
                ])

        logger.info(f"[PROCESS_CHUNK] Datetime parsing OK in {time.time() - t2:.3f}s")

    except Exception as e:
        logger.error("[ERROR] Failed during datetime parsing", exc_info=True)
        return pl.DataFrame()

    # VALIDATE SDateTime
    try:
        if "SDateTime" not in df.columns:
            logger.error("[ERROR] SDateTime column missing after parsing")
            return pl.DataFrame()

        t_validate = time.time()
        initial_count = df.height

        if df["SDateTime"].dtype != pl.Datetime:
            logger.error(f"[ERROR] SDateTime incorrect dtype: {df['SDateTime'].dtype}")
            return pl.DataFrame()

        df = df.filter(pl.col("SDateTime").is_not_null())

        final_count = df.height
        removed_count = initial_count - final_count

        logger.info(
            f"[PROCESS_CHUNK] Valid datetime records: {final_count}/{initial_count} "
            f"(removed {removed_count}) in {time.time() - t_validate:.3f}s"
        )

        if df.is_empty():
            logger.warning("[PROCESS_CHUNK] All rows invalid after datetime validation")
            return pl.DataFrame()
    except Exception as e:
        logger.error("[ERROR] Failed during datetime validation", exc_info=True)
        return pl.DataFrame()

    # FIELD PARSING
    try:
        t3 = time.time()



        if "IMEI" in df.columns:
            imei_tac, imei_clean = parse_imei_series(df["IMEI"])
            df = df.with_columns([
                pl.Series("IMEI_TAC", imei_tac, dtype=pl.Utf8),
                pl.Series("IMEI", imei_clean, dtype=pl.Utf8)
            ])

        if "IMSI" in df.columns:
            imsi_clean, imsi_code = parse_imsi_series(df["IMSI"])
            df = df.with_columns([
                pl.Series("IMSI", imsi_clean, dtype=pl.Utf8),
                pl.Series("IMSI_CODE", imsi_code, dtype=pl.Utf8)
            ])

        if "First_CGI" in df.columns:
            mcc, mnc, lac, cell, cgi = parse_cgi_series(df["First_CGI"])
            df = df.with_columns([pl.Series("First_CGI", cgi, dtype=pl.Utf8)])

        if "Last_CGI" in df.columns:
            mcc, mnc, lac, cell, cgi = parse_cgi_series(df["Last_CGI"])
            df = df.with_columns([pl.Series("Last_CGI", cgi, dtype=pl.Utf8)])

        if "FirstLatLong" in df.columns and "LastLatLong" in df.columns:
            first_lat, first_long = parse_latlong_series(df["FirstLatLong"])
            last_lat, last_long = parse_latlong_series(df["LastLatLong"])
            if first_lat and first_long:
                df = df.with_columns([
                    pl.Series("First_Lat", first_lat, dtype=pl.Float64),
                    pl.Series("First_Long", first_long, dtype=pl.Float64),
                    pl.Series("Last_Lat", last_lat, dtype=pl.Float64),
                    pl.Series("Last_Long", last_long, dtype=pl.Float64)
                ])
        if "A_Party" in df.columns:
            b_nums, b_codes = parse_number_series(df["A_Party"])
            df = df.with_columns([
                pl.Series("A_Party", b_nums, dtype=pl.Utf8),
                pl.Series("a_mobile_code", b_codes, dtype=pl.Utf8)
            ])

        if "B_Party" in df.columns:
            b_nums,b_codes = parse_number_series(df["B_Party"])
            df = df.with_columns([
                pl.Series("B_Party", b_nums, dtype=pl.Utf8)
            ])
        # if "Call Type" in df.columns and " Service Type":


        if "FileCallType" in df.columns and "FileServiceType" in df.columns:
            call_types = parse_call_type_series(df["FileCallType"],df["FileServiceType"])
            df = df.with_columns([pl.Series("Call_Type", call_types, dtype=pl.Utf8)])
        else:
            call_types = parse_call_type_series(df["FileCallType"],None)
            df = df.with_columns([pl.Series("Call_Type", call_types, dtype=pl.Utf8)])

        # -------------------------------------------------
        # 🔥 AUTO-CORRECT SWAPPED (A_Party ↔ B_Party) NUMBERS
        # -------------------------------------------------
        file_headers =[
        "target",
        "targetno",
        "aparty",
        "targetnumber",
        "targetapartynumber",
        "mobileno",
        "target/apartynumber"
        ]

        file_header = any(
            header.lower() == cdr_head.lower().replace(' ', '').replace('_', '')
            for header in file_headers
            for cdr_head in file_columns
        )
        if not file_header:
            try:
                if "A_Party" in df.columns and "B_Party" in df.columns and "Call_Type" in df.columns:
                    # Valid 10-digit Indian number recognition
                    valid_mobile = pl.col("*").str.contains(r"^[6-9]\d{9}$")

                    df = df.with_columns([
                        # Identify bad assignment:
                        # B looks like correct caller, but A not valid -> Swap needed
                        (
                                (~pl.col("A_Party").str.contains(r"^[6-9]\d{9}$")) &
                                (pl.col("B_Party").str.contains(r"^[6-9]\d{9}$"))
                        ).alias("swap_flag")
                    ])

                    # Additional rule: Incoming/SMS_IN/Call Forward cases
                    df = df.with_columns([
                        pl.when(
                            (pl.col("Call_Type").is_in(["CALL_IN", "SMS_IN", "CALL_FORWARD"])) &
                            pl.col("B_Party").str.contains(r"^[6-9]\d{9}$")
                        ).then(True).otherwise(pl.col("swap_flag")).alias("swap_flag")
                    ])

                    # 🟢 Perform swap only where swap_flag = True
                    df = df.with_columns([
                        pl.when(pl.col("swap_flag")).then(pl.col("B_Party")).otherwise(pl.col("A_Party")).alias("A_Party"),
                        pl.when(pl.col("swap_flag")).then(pl.col("A_Party")).otherwise(pl.col("B_Party")).alias("B_Party")
                    ])

                    df = df.drop("swap_flag")

                    logger.info("[PROCESS_CHUNK] A/B Party Swap Correction Applied")

            except Exception as e:
                logger.error("[ERROR] Failed during A/B Party correction", exc_info=True)


        if "Duration" in df.columns:
            df = df.with_columns([
                pl.col("Duration")
                .cast(pl.Utf8)
                .str.strip_chars()
                .str.replace_all(r"[^\d]", "")
                .fill_null("0")
                .str.replace("", "0")
                .cast(pl.Int64, strict=False)
                .fill_null(0)
                .alias("Duration")
            ])
        if "B_Party" in df.columns:
            b_nums, b_codes = parse_number_series(df["B_Party"])
            df = df.with_columns([
                pl.Series("b_mobile_code", b_codes, dtype=pl.Utf8)
            ])
        if "LRN" in df.columns:
            LRN, _ = parse_number_series(df["LRN"])
            df = df.with_columns([
                pl.Series("LRN", LRN, dtype=pl.Utf8)
            ])
        if "Vowifi" in df.columns:
            df = df.with_columns([
                pl.col("Vowifi")
                .cast(pl.Utf8)
                .str.replace_all("'", "")  # remove quotes
                .str.strip_chars()  # remove spaces
                .alias("Vowifi")
            ])

            # Convert invalid values to null
            df = df.with_columns([
                pl.when(pl.col("Vowifi").is_in(["", "-", "null", "None"]))
                .then(None)
                .otherwise(pl.col("Vowifi"))
                .alias("Vowifi")
            ])

            # Drop column if all values are null
            if df.select(pl.col("Vowifi").null_count()).item() == df.height:
                df = df.drop("Vowifi")


        logger.info(f"[PROCESS_CHUNK] Field parsing OK in {time.time() - t3:.3f}s")

    except Exception as e:
        logger.error("[ERROR] Failed during field parsing", exc_info=True)
        return pl.DataFrame()

    # EDateTime calculation
    try:
        t4 = time.time()
        df = df.with_columns([
            pl.when(pl.col("SDateTime").is_not_null())
            .then(pl.col("SDateTime") + pl.duration(seconds=pl.col("Duration")))
            .otherwise(pl.lit(None).cast(pl.Datetime))
            .alias("EDateTime")
        ])
        logger.info(f"[PROCESS_CHUNK] EDateTime calc OK in {time.time() - t4:.3f}s")

    except Exception as e:
        logger.error("[ERROR] Failed during EDateTime calculation", exc_info=True)
        return pl.DataFrame()

    total_time = time.time() - chunk_start
    logger.info(f"[PROCESS_CHUNK] Completed in {total_time:.3f}s, Records: {df.height}")

    return df