# Ipdr/ipdr_format_handling.py
# Updated: Handle 'updated' count + ZIP/RAR Archive Support

import os
import io
import shutil
import zipfile
import polars as pl
from multiprocessing import Pool, cpu_count
import logging
from datetime import datetime
from concurrent.futures import as_completed, ThreadPoolExecutor

from django.conf import settings

from .IPDetailRecords import process_chunk_optimized
from .columncheckfun import column_check
from .upload_ipdr_to_db import insert_ipdr_file
from ..utils.file_handlers import file_handler_factory_memory, FileHandler

logger = logging.getLogger(__name__)

# Global in-memory cache for header validation
HEADER_CACHE = {}

# Try to import RAR support
try:
    import rarfile as _rarfile_module
    _RAR_AVAILABLE = True
except ImportError:
    _rarfile_module = None
    _RAR_AVAILABLE = False

# Archive extraction constants
_ARCHIVE_EXTS = {'.zip', '.rar'}
_IPDR_EXTS = {'.xlsx', '.xls', '.csv', '.txt', '.parquet'}


# ============================================================
# Environment Detection
# ============================================================

def is_docker():
    """Detect if running inside Docker container"""
    return os.path.exists('/.dockerenv') or os.path.exists('/run/.containerenv')


# ============================================================
# RAR / Archive Support
# ============================================================

def get_rarfile():
    """Lazy load rarfile with UnRAR tool detection"""
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

    # Try to find UnRAR executable
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


def is_archive(filename):
    """Check if file is an archive (ZIP or RAR)"""
    return any(filename.lower().endswith(ext) for ext in _ARCHIVE_EXTS)


def is_ipdr_file(filename):
    """Check if file is a valid IPDR format"""
    return any(filename.lower().endswith(ext) for ext in _IPDR_EXTS)


def extract_files_from_archive(archive_bytes, archive_name, _depth=0):
    """
    Extract files from ZIP/RAR archive recursively.
    Yields (filename, file_bytes) for IPDR files only.

    Args:
        archive_bytes: BytesIO or bytes of the archive
        archive_name:  Original archive filename
        _depth:        Recursion depth (prevents infinite loops)

    Yields:
        (filename, file_bytes) for each IPDR file found
    """
    MAX_DEPTH = 10
    indent = '   ' * (_depth + 1)

    if _depth > MAX_DEPTH:
        logger.warning(f"{indent}Max archive nesting depth ({MAX_DEPTH}) reached — skipping '{archive_name}'")
        return

    # Normalise to BytesIO
    if isinstance(archive_bytes, bytes):
        file_obj = io.BytesIO(archive_bytes)
    else:
        file_obj = archive_bytes
        file_obj.seek(0)

    archive_lower = archive_name.lower()

    try:
        # ── RAR ──────────────────────────────────────────────
        if archive_lower.endswith('.rar'):
            rf_mod = get_rarfile()
            if rf_mod is None:
                logger.error(f"{indent}RAR support not available — skipping '{archive_name}'")
                return

            with rf_mod.RarFile(file_obj) as rf:
                for entry in rf.infolist():
                    if entry.is_dir():
                        continue
                    try:
                        raw_bytes = rf.read(entry.filename)
                    except Exception as e:
                        logger.error(f"{indent}Could not read RAR entry '{entry.filename}': {e}")
                        continue

                    base_name = os.path.basename(entry.filename)
                    if is_archive(base_name):
                        logger.debug(f"{indent}Found nested archive: {base_name}")
                        yield from extract_files_from_archive(raw_bytes, base_name, _depth + 1)
                    elif is_ipdr_file(base_name):
                        logger.info(f"{indent}Extracted IPDR file: {base_name}")
                        yield base_name, raw_bytes

        # ── ZIP ──────────────────────────────────────────────
        elif archive_lower.endswith('.zip'):
            with zipfile.ZipFile(file_obj, 'r') as zf:
                for entry in zf.infolist():
                    if entry.is_dir():
                        continue
                    try:
                        raw_bytes = zf.read(entry.filename)
                    except Exception as e:
                        logger.error(f"{indent}Could not read ZIP entry '{entry.filename}': {e}")
                        continue

                    base_name = os.path.basename(entry.filename)
                    if is_archive(base_name):
                        logger.debug(f"{indent}Found nested archive: {base_name}")
                        yield from extract_files_from_archive(raw_bytes, base_name, _depth + 1)
                    elif is_ipdr_file(base_name):
                        logger.info(f"{indent}Extracted IPDR file: {base_name}")
                        yield base_name, raw_bytes

    except zipfile.BadZipFile as e:
        logger.error(f"{indent}Bad ZIP file '{archive_name}': {e}")
    except Exception as e:
        logger.error(f"{indent}Could not open archive '{archive_name}': {e}")


def expand_archive_files(file_data):
    """
    Expand archive files (ZIP/RAR) in file_data into their IPDR file contents.
    Non-archive files are preserved as-is.

    Args:
        file_data: List of (file_obj, filename) tuples

    Returns:
        List of (file_bytes, filename) for all IPDR files ready to process
    """
    expanded_files = []

    for file_obj, filename in file_data:
        # Read content once
        if hasattr(file_obj, 'read'):
            file_obj.seek(0)
            file_bytes = file_obj.read()
        else:
            file_bytes = file_obj

        if is_archive(filename):
            logger.info(f"\n{'=' * 60}")
            logger.info(f"ARCHIVE DETECTED: {filename}")
            logger.info(f"Extracting IPDR files...")
            logger.info(f"{'=' * 60}")

            extracted_count = 0
            try:
                for inner_name, inner_bytes in extract_files_from_archive(file_bytes, filename):
                    expanded_files.append((inner_bytes, inner_name))
                    extracted_count += 1

                logger.info(f"✅ Extracted {extracted_count} IPDR file(s) from {filename}")
                if extracted_count == 0:
                    logger.warning(f"⚠️  No IPDR files found in archive: {filename}")

            except Exception as e:
                logger.error(f"❌ Failed to extract archive {filename}: {e}")
                # Skip this archive entirely
        else:
            # Not an archive — keep as-is
            expanded_files.append((file_bytes, filename))

    return expanded_files


# ============================================================
# Column-check Cache
# ============================================================

def fast_column_check_with_cache(sample_values, filename):
    """Cache column check results by filename"""
    if filename in HEADER_CACHE:
        logger.debug(f"[CACHE HIT] Using cached header position for {filename}")
        return HEADER_CACHE[filename]

    ipdrjson_path = os.path.join(
        settings.BASE_DIR, "api", "data", "ipdr", "IPdrHeaders.json"
    )
    mandatory_path = os.path.join(
        settings.BASE_DIR, "api", "data", "ipdr", "mandatory_ipdr_headers.json"
    )

    column_status = column_check(sample_values, ipdrjson_path, mandatory_path)

    if column_status.get('status') == 'matched':
        HEADER_CACHE[filename] = column_status
        logger.debug(f"[CACHE STORE] Cached header info for {filename}")

    return column_status


# ============================================================
# Chunk Processing
# ============================================================

def process_and_insert_chunk(args):
    """
    Process one data chunk and insert into DB.
    Returns a dict with keys: inserted, duplicates, updated, skipped.
    """
    df_chunk, crime_name, area_location, filename, top_meta = args
    chunk_id = id(df_chunk)

    _empty = {'inserted': 0, 'duplicates': 0, 'updated': 0, 'skipped': 0}

    try:
        logger.debug(f"[CHUNK {chunk_id}] Starting - {df_chunk.height} rows")

        start_time = datetime.now()
        result = process_chunk_optimized(df_chunk)
        process_time = (datetime.now() - start_time).total_seconds()

        if result is None:
            logger.warning(f"[CHUNK {chunk_id}] process_chunk_optimized returned None")
            return _empty

        if not isinstance(result, tuple) or len(result) != 2:
            logger.error(f"[CHUNK {chunk_id}] Unexpected return type: {type(result)}")
            return _empty

        processed_df, seqdata = result

        if processed_df is None or processed_df.is_empty():
            logger.warning(f"[CHUNK {chunk_id}] Empty DataFrame after processing")
            return _empty

        if not seqdata or not isinstance(seqdata, dict):
            logger.warning(f"[CHUNK {chunk_id}] Invalid seqdata: {seqdata}")
            seqdata = {}

        logger.debug(f"[CHUNK {chunk_id}] Processed in {process_time:.2f}s")

        insert_start = datetime.now()
        insert_result = insert_ipdr_file(
            processed_df,
            crime_name,
            area_location,
            filename,
            top_meta,
            seqdata
        )
        insert_time = (datetime.now() - insert_start).total_seconds()

        logger.info(
            f"[CHUNK {chunk_id}] Insert: {insert_time:.2f}s - "
            f"Inserted: {insert_result.get('inserted', 0)}, "
            f"Duplicates: {insert_result.get('duplicates', 0)}, "
            f"Updated: {insert_result.get('updated', 0)}"
        )

        return insert_result

    except Exception as e:
        logger.error(f"[CHUNK {chunk_id}] FAILED: {str(e)}", exc_info=True)
        return {**_empty, 'error': str(e)}


# ============================================================
# Public Entry Point
# ============================================================

def file_categorisation_ipdr(file_data: list, crime_name: str, area_location: str):
    """
    Entry point: process all provided IPDR files from memory.
    Supports plain files as well as ZIP/RAR archives (extracted automatically).
    """
    logger.info(f"\n{'=' * 80}")
    logger.info(f"IPDR FILE PROCESSING STARTED")
    logger.info(f"Crime: {crime_name}, Area: {area_location}")
    logger.info(f"Total items received: {len(file_data)}")
    logger.info(f"Running in Docker: {is_docker()}")
    logger.info(f"{'=' * 80}")

    # ── Step 1: Expand any archive files ────────────────────
    expanded_files = expand_archive_files(file_data)

    if not expanded_files:
        logger.error("❌ No IPDR files found after processing archives")
        return {"status": "failed", "message": "No valid IPDR files found"}

    logger.info(f"\n📁 Total IPDR files to process: {len(expanded_files)}")

    # ── Step 2: Optionally drop indexes for bulk inserts ────
    if len(expanded_files) > 5:
        logger.info("\n[OPTIMIZATION] Dropping MongoDB indexes for faster insert...")
        from ..utils.upload_cdr_to_db import manage_indexes

    def process_single_file(file_info):
        idx, (file_bytes, filename) = file_info
        logger.info(f"\n[FILE {idx}/{len(expanded_files)}] Processing: {filename}")
        file_start = datetime.now()
        result_list = []

        try:
            file_obj = io.BytesIO(file_bytes)

            handler = file_handler_factory_memory(file_obj, filename)
            context = FileHandler(handler) if not isinstance(handler, pl.DataFrame) else None
            data = context.read_file(file_obj) if context else handler

            logger.debug(f"[FILE {idx}] File read successfully from memory")

            if isinstance(data, dict):
                # Excel with multiple sheets
                logger.info(f"[FILE {idx}] Excel with {len(data)} sheets")
                for sheet_name, df in data.items():
                    logger.info(f"[FILE {idx}] Processing sheet: {sheet_name}")
                    result = _process_file(df, crime_name, area_location, filename)
                    result_list.append((filename, result))
                    logger.info(f"[FILE {idx}] Sheet '{sheet_name}' result: {result}")
                file_time = (datetime.now() - file_start).total_seconds()
                logger.info(f"[FILE {idx}] Completed in {file_time:.2f}s")
            else:
                result = _process_file(data, crime_name, area_location, filename)
                file_time = (datetime.now() - file_start).total_seconds()
                logger.info(f"[FILE {idx}] Completed in {file_time:.2f}s - {result}")
                result_list.append((filename, result))

        except Exception as e:
            file_time = (datetime.now() - file_start).total_seconds()
            logger.error(f"[FILE {idx}] FAILED after {file_time:.2f}s: {str(e)}")
            logger.error(f"[FILE {idx}] Full traceback:", exc_info=True)
            result_list.append((filename, {"status": "failed", "message": str(e)}))

        from collections import defaultdict
        result_dict = defaultdict(list)
        for k, v in result_list:
            result_dict[k].append(v)
        return dict(result_dict)

    total_start = datetime.now()

    # ── Step 3: Process files (serial or parallel) ──────────
    if len(expanded_files) == 1:
        result = process_single_file((1, expanded_files[0]))
    else:
        max_workers = min(4, len(expanded_files))
        logger.info(f"\n[PARALLEL FILES] Using {max_workers} threads for file reading")

        all_results = {}
        file_infos = [(idx + 1, fd) for idx, fd in enumerate(expanded_files)]

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(process_single_file, info): info for info in file_infos}
            for future in as_completed(futures):
                try:
                    all_results.update(future.result())
                except Exception as e:
                    logger.error(f"File processing exception: {e}")

        result = all_results

    total_time = (datetime.now() - total_start).total_seconds()

    # ── Step 4: Rebuild indexes ──────────────────────────────
    logger.info("\n[OPTIMIZATION] Rebuilding MongoDB indexes...")
    from ..utils.upload_cdr_to_db import manage_indexes
    manage_indexes(action='rebuild')

    # ── Step 5: Compute grand totals across all files/sheets ────
    grand_inserted   = 0
    grand_duplicates = 0
    grand_updated    = 0
    grand_skipped    = 0

    for file_results in result.values():
        for entry in (file_results if isinstance(file_results, list) else [file_results]):
            if isinstance(entry, dict) and entry.get('status') == 'success':
                grand_inserted   += entry.get('inserted',   0)
                grand_duplicates += entry.get('duplicates', 0)
                grand_updated    += entry.get('updated',    0)
                grand_skipped    += entry.get('skipped',    0)

    logger.info(f"\n{'=' * 80}")
    logger.info(f"ALL IPDR FILES PROCESSED")
    logger.info(f"Total time: {total_time:.2f}s")
    logger.info(f"Average per file: {total_time / len(expanded_files):.2f}s")
    logger.info(
        f"Grand Total — Inserted: {grand_inserted}, Duplicates: {grand_duplicates}, "
        f"Updated: {grand_updated}, Skipped: {grand_skipped}"
    )
    logger.info(f"{'=' * 80}\n")

    return {
        "inserted":   grand_inserted,
        "duplicates": grand_duplicates,
        "updated":    grand_updated,
        "skipped":    grand_skipped,
        # "result":     result,
        **result,
    }


# ============================================================
# Internal: Single-file Processing
# ============================================================

def _process_file(df: pl.DataFrame, crime_name: str, area_location: str, filename: str):
    """
    Detect headers, clean, split into chunks, and process a single DataFrame.
    """
    try:
        logger.info(f"\n{'=' * 70}")
        logger.info(f"IPDR FILE PROCESSING START: {filename}")
        logger.info(f"{'=' * 70}")
        logger.info(f"Initial DataFrame: {df.height} rows x {df.width} cols")

        if df.is_empty():
            logger.error("DataFrame is empty")
            return {"status": "failed", "message": "Data Not Found"}

        # ── Column check (cached) ────────────────────────────
        sample_size = min(50, df.height)
        sample_values = [list(row) for row in df.head(sample_size).rows()]
        column_status = fast_column_check_with_cache(sample_values, filename)

        if column_status.get('status') != 'matched':
            logger.error(f"COLUMN CHECK FAILED: {column_status.get('message')}")
            return {"status": "failed", "message": column_status.get('message')}

        header_row_idx = column_status.get('row_index', 1) - 1
        logger.info(f"Header at row {header_row_idx}")

        if header_row_idx < 0 or header_row_idx >= df.height:
            logger.error(f"Invalid header index {header_row_idx}")
            return {"status": "failed", "message": "invalid header index"}

        # ── Extract metadata and header ──────────────────────
        top_rows = df[:header_row_idx]
        logger.debug(f"Extracted {top_rows.height} metadata rows")

        header_row = df.row(header_row_idx)
        clean_header_names = [
            str(h).strip() if h and str(h).lower() not in ['none', 'nan', ''] else f"col_{i}"
            for i, h in enumerate(header_row)
        ]
        logger.info(f"Extracted {len(clean_header_names)} headers")

        # ── Slice to data rows ───────────────────────────────
        data_start = header_row_idx + 1
        if data_start >= df.height:
            logger.error("No data rows after header")
            return {"status": "failed", "message": "Data Not Found"}

        df = df.slice(data_start).rename(dict(zip(df.columns, clean_header_names)))
        logger.info(f"Data after header: {df.height} rows")

        # ── Remove empty rows ────────────────────────────────
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

        # ── Chunk strategy ───────────────────────────────────
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

        # ── Execute chunks ───────────────────────────────────
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

        # ── Aggregate results ────────────────────────────────
        total_inserted   = sum(r.get('inserted',   0) for r in results if r)
        total_duplicates = sum(r.get('duplicates', 0) for r in results if r)
        total_updated    = sum(r.get('updated',    0) for r in results if r)
        total_skipped    = sum(r.get('skipped',    0) for r in results if r)
        total_errors     = sum(1 for r in results if r and r.get('error'))

        logger.info(f"\n{'=' * 70}")
        logger.info(f"IPDR PROCESSING COMPLETE")
        logger.info(f"   Inserted:    {total_inserted}")
        logger.info(f"   Duplicates:  {total_duplicates}")
        logger.info(f"   Updated:     {total_updated}")
        logger.info(f"   Skipped:     {total_skipped}")
        logger.info(f"   Errors:      {total_errors}")
        logger.info(f"   Total Time:  {chunk_time:.2f}s")
        logger.info(f"{'=' * 70}\n")

        return {
            "status": "success",
            "inserted":   total_inserted,
            "duplicates": total_duplicates,
            "updated":    total_updated,
            "skipped":    total_skipped,
        }

    except Exception as e:
        logger.error(f"\nERROR during IPDR processing: {e}", exc_info=True)
        return {"status": "failed", "message": "processing failed"}