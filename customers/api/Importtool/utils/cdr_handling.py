# optimized_cdr_data_handling.py
# UPDATED: Handle 'updated' count from Option 1

import polars as pl
from multiprocessing import Pool, cpu_count
import traceback
import logging
from datetime import datetime
from concurrent.futures import as_completed, ThreadPoolExecutor
import os

from django.conf import settings

from .CallDetailRecords import process_chunk_optimized
from .file_handlers import file_handler_factory_memory, FileHandler, expand_archive_files, is_archive
from .upload_cdr_to_db import insert_cdr_file, manage_indexes
from .validators import column_check

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

HEADER_CACHE = {}


def is_docker():
    """Detect if running inside Docker container"""
    return os.path.exists('/.dockerenv') or os.path.exists('/run/.containerenv')


def process_and_insert_chunk(args):
    """Process CDR chunk - UPDATED to handle 'updated' count"""
    df_chunk, crime_name, area_location, filename, h_df = args
    chunk_id = id(df_chunk)

    try:
        logger.debug(f"[CHUNK {chunk_id}] Starting - {df_chunk.height} rows")

        start_time = datetime.now()
        records = process_chunk_optimized(df_chunk)
        process_time = (datetime.now() - start_time).total_seconds()

        if records is None or (hasattr(records, 'height') and records.height == 0):
            logger.warning(f"[CHUNK {chunk_id}] No valid records after processing")
            return {'inserted': 0, 'duplicates': 0, 'updated': 0, 'skipped': 0}

        logger.debug(f"[CHUNK {chunk_id}] Processed in {process_time:.2f}s")

        insert_start = datetime.now()
        result = insert_cdr_file(records, crime_name, area_location, filename, h_df)
        insert_time = (datetime.now() - insert_start).total_seconds()

        logger.info(f"[CHUNK {chunk_id}] Insert: {insert_time:.2f}s - "
                    f"Inserted: {result.get('inserted', 0)}, "
                    f"Duplicates: {result.get('duplicates', 0)}, "
                    f"Updated: {result.get('updated', 0)}")

        return result

    except Exception as e:
        logger.error(f"[CHUNK {chunk_id}] FAILED: {str(e)}", exc_info=True)
        return {'inserted': 0, 'duplicates': 0, 'updated': 0, 'skipped': 0, 'error': str(e)}


def fast_column_check_with_cache(sample_values, filename):
    """Column validation with caching"""
    cache_key = filename
    if cache_key in HEADER_CACHE:
        logger.debug(f"[CACHE HIT] Using cached header position for {filename}")
        return HEADER_CACHE[cache_key]

    cdrjson_path = os.path.join(
        settings.BASE_DIR,
        "api", "data", "cdr", "CdrHeaders.json"
    )
    mandatory_path = os.path.join(
        settings.BASE_DIR,
        "api", "data", "cdr", "mandatory_cdr_headers.json"
    )
    column_status = column_check(
        sample_values,
        cdrjson_path,
        mandatory_path
    )

    if column_status.get('status') == 'matched':
        HEADER_CACHE[cache_key] = column_status
        logger.debug(f"[CACHE STORE] Cached header info for {filename}")

    return column_status


def deduplicate_headers(headers):
    """
    Deduplicate column header names by appending a numeric suffix to any repeats.
    e.g. ['A', 'B', 'A', 'A'] → ['A', 'B', 'A_1', 'A_2']

    This is required because some TOWER/SUMMARY sheets contain repeated CGI values
    or numeric placeholders (e.g. '0', '-') as column headers, which Polars rejects.
    """
    seen = {}
    result = []
    for h in headers:
        if h in seen:
            seen[h] += 1
            result.append(f"{h}_{seen[h]}")
        else:
            seen[h] = 0
            result.append(h)
    return result


def _process_file_optimized(df: pl.DataFrame, crime_name: str, area_location: str, filename: str):
    """OPTIMIZED file processing - UPDATED for Option 1"""
    try:
        logger.info(f"\n{'=' * 70}")
        logger.info(f"CDR FILE PROCESSING START: {filename}")
        logger.info(f"{'=' * 70}")
        logger.info(f"Initial DataFrame: {df.height} rows x {df.width} cols")

        if df.is_empty():
            logger.error("DataFrame is empty")
            return {"status": "failed", "message": "Data Not Found"}

        sample_size = min(30, df.height)
        sample_values = [list(row) for row in df.head(sample_size).rows()]

        column_status = fast_column_check_with_cache(sample_values, filename)

        if column_status['status'] != 'matched':
            logger.error(f"COLUMN CHECK FAILED: {column_status['message']}")
            return {"status": "failed", "message": column_status.get('message')}

        header_row_idx = column_status.get('row_index', 1) - 1
        logger.info(f"Header at row {header_row_idx}")

        if header_row_idx < 0 or header_row_idx >= df.height:
            logger.error(f"Invalid header index {header_row_idx}")
            return {"status": "failed", "message": "invalid header index"}

        top_rows = df[:header_row_idx]
        logger.debug(f"Extracted {top_rows.height} metadata rows")

        header_row = df.row(header_row_idx)
        clean_header_names = [
            str(h).strip() if h and str(h).lower() not in ['none', 'nan', ''] else f"col_{i}"
            for i, h in enumerate(header_row)
        ]
        logger.info(f"Extracted {len(clean_header_names)} headers")

        # ── FIX: deduplicate headers before renaming ──────────────────────────
        clean_header_names = deduplicate_headers(clean_header_names)
        # ─────────────────────────────────────────────────────────────────────

        data_start = header_row_idx + 1
        if data_start >= df.height:
            logger.error("No data rows after header")
            return {"status": "failed", "message": "Data Not Found"}

        df = df.slice(data_start).rename(dict(zip(df.columns, clean_header_names)))
        logger.info(f"Data after header: {df.height} rows")

        before_filter = df.height
        df = df.filter(
            pl.fold(
                acc=pl.lit(False),
                function=lambda acc, s: acc | s.is_not_null(),
                exprs=pl.all()
            )
        )
        after_filter = df.height
        logger.info(f"After cleaning: {after_filter} rows (removed {before_filter - after_filter})")

        if df.is_empty():
            logger.warning("DataFrame empty after filtering")
            return {"status": "failed", "message": "No Valid Data Rows After Cleaning"}

        in_docker = is_docker()

        if df.height < 10000:
            chunk_size = df.height
            use_parallel = False
            num_workers = 1
        elif df.height < 50000:
            chunk_size = 6000
            num_workers = min(4, cpu_count()) if not in_docker else 2
            use_parallel = True
        else:
            chunk_size = 10000
            num_workers = min(cpu_count(), 8) if not in_docker else 4
            use_parallel = True

        top_rows_dict = top_rows.to_dict() if hasattr(top_rows, 'to_dict') else {}

        chunks = [
            (df[start:start + chunk_size], crime_name, area_location, filename, top_rows_dict)
            for start in range(0, df.height, chunk_size)
        ]
        logger.info(f"Split into {len(chunks)} chunks (size: {chunk_size})")

        if not chunks:
            return {"status": "failed", "message": "Data Not Found"}

        chunk_start = datetime.now()

        if in_docker or not use_parallel:
            logger.info(f"Using ThreadPoolExecutor with {num_workers} threads (Docker mode)")
            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                results = list(executor.map(process_and_insert_chunk, chunks))
        else:
            logger.info(f"Using multiprocessing with {num_workers} processes (Local mode)")
            with Pool(processes=num_workers) as pool:
                results = pool.map(process_and_insert_chunk, chunks)

        chunk_time = (datetime.now() - chunk_start).total_seconds()
        logger.info(f"All chunks processed in {chunk_time:.2f}s")

        total_inserted = sum(r.get('inserted', 0) for r in results if r)
        total_duplicates = sum(r.get('duplicates', 0) for r in results if r)
        total_updated = sum(r.get('updated', 0) for r in results if r)
        total_skipped = sum(r.get('skipped', 0) for r in results if r)
        total_errors = sum(1 for r in results if r and r.get('error'))

        logger.info(f"\n{'=' * 70}")
        logger.info(f"CDR PROCESSING COMPLETE")
        logger.info(f"   Inserted:    {total_inserted}")
        logger.info(f"   Duplicates:  {total_duplicates}")
        logger.info(f"   Updated:     {total_updated}")
        logger.info(f"   Skipped:     {total_skipped}")
        logger.info(f"   Errors:      {total_errors}")
        logger.info(f"   Total Time:  {chunk_time:.2f}s")
        logger.info(f"{'=' * 70}\n")

        return {
            "status": "success",
            "inserted": total_inserted,
            "duplicates": total_duplicates,
            "updated": total_updated,
            "skipped": total_skipped
        }

    except Exception as e:
        logger.error(f"\nERROR during CDR processing: {e}", exc_info=True)
        return {"status": "failed", "message": "processing failed"}


def file_categorisation_optimized(file_data: list, crime_name: str, area_location: str):
    """Process files from memory - Docker compatible - UPDATED for Option 1"""
    logger.info(f"=" * 80)
    logger.info(f"CDR FILE PROCESSING STARTED")
    logger.info(f"Crime: {crime_name}, Area: {area_location}")
    logger.info(f"Total files: {len(file_data)}")
    logger.info(f"Running in Docker: {is_docker()}")
    logger.info(f"=" * 80)

    # ── Expand any ZIP/RAR archives into individual files ────────────────────
    # After this step every entry is a plain (file_bytes, filename) tuple so
    # results are always keyed by the real inner filename, not the archive name.
    expanded_file_data = expand_archive_files(file_data)
    had_archives = any(is_archive(filename) for _, filename in file_data)

    if len(expanded_file_data) > 5:
        logger.info("\n[OPTIMIZATION] Dropping MongoDB indexes for faster insert...")

    def process_single_file(file_info):
        idx, (file_obj, filename) = file_info
        logger.info(f"\n[FILE {idx}/{len(expanded_file_data)}] Processing: {filename}")
        file_start = datetime.now()

        try:
            import io as _io
            # expanded_file_data entries are raw bytes — wrap in BytesIO
            if isinstance(file_obj, (bytes, bytearray)):
                file_obj = _io.BytesIO(file_obj)

            handler = file_handler_factory_memory(file_obj, filename)
            context = FileHandler(handler) if not isinstance(handler, pl.DataFrame) else None
            data = context.read_file(file_obj) if context else handler

            logger.debug(f"[FILE {idx}] File read successfully from memory")

            if isinstance(data, dict):
                # Excel: aggregate all sheets into ONE result entry per file.
                # Sheets where every counter is zero (empty/irrelevant sheets)
                # are silently skipped so they don't pollute the response.
                logger.info(f"[FILE {idx}] Excel with {len(data)} sheets")
                agg = {"inserted": 0, "duplicates": 0, "updated": 0, "skipped": 0}
                has_success = False
                fail_msg = None

                for sheet_name, df in data.items():
                    logger.info(f"[FILE {idx}] Processing sheet: {sheet_name}")
                    sheet_result = _process_file_optimized(df, crime_name, area_location, filename)
                    logger.info(f"[FILE {idx}] Sheet '{sheet_name}' result: {sheet_result}")

                    if sheet_result.get("status") == "success":
                        ins = sheet_result.get("inserted",   0)
                        dup = sheet_result.get("duplicates", 0)
                        upd = sheet_result.get("updated",    0)
                        skp = sheet_result.get("skipped",    0)
                        # Skip completely empty sheets
                        if ins == 0 and dup == 0 and upd == 0 and skp == 0:
                            logger.debug(f"[FILE {idx}] Sheet '{sheet_name}' all-zero — skipping")
                            continue
                        agg["inserted"]   += ins
                        agg["duplicates"] += dup
                        agg["updated"]    += upd
                        agg["skipped"]    += skp
                        has_success = True
                    elif sheet_result.get("status") == "failed":
                        if fail_msg is None:
                            fail_msg = sheet_result.get("message", "processing failed")

                file_time = (datetime.now() - file_start).total_seconds()
                logger.info(f"[FILE {idx}] Completed in {file_time:.2f}s")

                if has_success:
                    merged = {"status": "success", **agg}
                elif fail_msg:
                    merged = {"status": "failed", "message": fail_msg}
                else:
                    merged = {"status": "failed", "message": "No valid sheet data found"}

                return {filename: [merged]}

            else:
                # Plain CSV / single DataFrame
                result = _process_file_optimized(data, crime_name, area_location, filename)
                file_time = (datetime.now() - file_start).total_seconds()
                logger.info(f"[FILE {idx}] Completed in {file_time:.2f}s - {result}")
                return {filename: [result]}

        except Exception as e:
            file_time = (datetime.now() - file_start).total_seconds()
            logger.error(f"[FILE {idx}] FAILED after {file_time:.2f}s: {str(e)}")
            logger.error(f"[FILE {idx}] Full traceback:", exc_info=True)
            return {filename: [{"status": "failed", "message": str(e)}]}

    total_start = datetime.now()

    if len(expanded_file_data) == 1:
        per_file_result = process_single_file((1, expanded_file_data[0]))
    else:
        max_workers = min(4, len(expanded_file_data))
        logger.info(f"\n[PARALLEL FILES] Using {max_workers} threads for file reading")

        per_file_result = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            file_infos = [(idx + 1, fd) for idx, fd in enumerate(expanded_file_data)]
            futures = {executor.submit(process_single_file, info): info for info in file_infos}

            for future in as_completed(futures):
                try:
                    per_file_result.update(future.result())
                except Exception as e:
                    logger.error(f"File processing exception: {e}")

    total_time = (datetime.now() - total_start).total_seconds()

    logger.info("\n[OPTIMIZATION] Rebuilding MongoDB indexes...")
    manage_indexes(action='rebuild')

    # ── Grand totals ─────────────────────────────────────────────────────────
    grand_inserted   = 0
    grand_duplicates = 0
    grand_updated    = 0
    grand_skipped    = 0

    for file_results in per_file_result.values():
        for entry in (file_results if isinstance(file_results, list) else [file_results]):
            if isinstance(entry, dict) and entry.get('status') == 'success':
                grand_inserted   += entry.get('inserted',   0)
                grand_duplicates += entry.get('duplicates', 0)
                grand_updated    += entry.get('updated',    0)
                grand_skipped    += entry.get('skipped',    0)

    logger.info(f"\n{'=' * 80}")
    logger.info(f"ALL FILES PROCESSED")
    logger.info(f"Total time: {total_time:.2f}s")
    logger.info(f"Average per file: {total_time / len(expanded_file_data):.2f}s")
    logger.info(
        f"Grand Total — Inserted: {grand_inserted}, Duplicates: {grand_duplicates}, "
        f"Updated: {grand_updated}, Skipped: {grand_skipped}"
    )
    logger.info(f"{'=' * 80}\n")

    # ── Build response ───────────────────────────────────────────────────────
    # Archives → totals appear inside result too (filenames are inner files)
    # Plain files → clean per-file dict only
    if had_archives:
        result = {
            "inserted":   grand_inserted,
            "duplicates": grand_duplicates,
            "updated":    grand_updated,
            "skipped":    grand_skipped,
            **per_file_result,
        }
    else:
        result = dict(per_file_result)

    return {
        "inserted":   grand_inserted,
        "duplicates": grand_duplicates,
        "updated":    grand_updated,
        "skipped":    grand_skipped,
        # "result":     result,
        **result,
    }