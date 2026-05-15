# ============================================================================
# FILE: rename_columns.py - FIXED WITH DEBUGGING
# ============================================================================

import json
import os

import polars as pl
import re
import math
from datetime import timedelta

from django.conf import settings

# Add missing imports
#logger = logging.get#logger(__name__)

# Load JSON mapping
try:
    # -------- FIXED ABSOLUTE PATH ----------
    ipdrjson_path = os.path.join(
        settings.BASE_DIR,
        "api", "data", "ipdr", "IPdrHeaders.json"
    )

    with open(ipdrjson_path, "r") as f:
        HEADER_MAPPING = json.load(f)

except Exception as e:
    HEADER_MAPPING = {}

# List of standardized columns you want to keep
DESIRED_COLUMNS = list(HEADER_MAPPING.keys())
#logger.debug(f"Desired columns: {DESIRED_COLUMNS[:10]}...")


def normalize(name: str) -> str:
    """Normalize strings for matching"""
    normalized = str(name).strip().upper()
    #logger.debug(f"Normalized '{name}' -> '{normalized}'")
    return normalized


# Build reverse lookup: normalized synonym -> standardized key
reverse_lookup = {}
for key, synonyms in HEADER_MAPPING.items():
    for syn in synonyms:
        reverse_lookup[normalize(syn)] = key

#logger.info(f"Built reverse lookup with {len(reverse_lookup)} entries")


def renamecolumns(df: pl.DataFrame) -> pl.DataFrame:
    """
    Rename columns using HEADER_MAPPING and keep only desired columns.
    Handles duplicate column names that may arise during renaming.
    """
    #logger.info(f"[RENAME] Starting column rename and filter")
    #logger.debug(f"[RENAME] Input columns: {df.columns}")

    # Step 1: Map old columns to new names
    new_columns = []
    mapping_stats = {'matched': 0, 'unmatched': 0}

    for col in df.columns:
        col_norm = normalize(col)
        if col_norm in reverse_lookup:
            mapped_name = reverse_lookup[col_norm]
            new_columns.append(mapped_name)
            mapping_stats['matched'] += 1
            #logger.debug(f"[RENAME] Mapped: '{col}' -> '{mapped_name}'")
        else:
            new_columns.append(col)  # Keep original if no match
            mapping_stats['unmatched'] += 1
            #logger.debug(f"[RENAME] No mapping for: '{col}', keeping original")

    #logger.info(f"[RENAME] Mapping stats: {mapping_stats}")
    #logger.debug(f"[RENAME] Mapped columns (may have duplicates): {new_columns}")

    # Step 2: Check for duplicates and make them unique BEFORE renaming
    seen = {}
    unique_new_columns = []

    for col in new_columns:
        if col not in seen:
            seen[col] = 0
            unique_new_columns.append(col)
        else:
            seen[col] += 1
            unique_name = f"{col}_dup{seen[col]}"
            unique_new_columns.append(unique_name)
            #logger.debug(f"[RENAME] Duplicate found: '{col}' renamed to '{unique_name}'")

    if len(new_columns) != len(set(new_columns)):
        duplicates = [c for c in new_columns if new_columns.count(c) > 1]
        #logger.warning(f"[RENAME] Found duplicate columns: {set(duplicates)}")
        #logger.info(f"[RENAME] Made unique: {unique_new_columns}")

    # Step 3: Now safely rename with unique names
    rename_dict = dict(zip(df.columns, unique_new_columns))
    #logger.debug(f"[RENAME] Rename dictionary: {list(rename_dict.items())[:10]}...")

    df = df.rename(rename_dict)
    #logger.info(f"[RENAME] Columns after initial rename: {df.columns}")

    # Step 4: Keep only the FIRST occurrence of each desired column
    columns_to_keep = []
    seen_desired = set()

    for col in df.columns:
        # Extract base name (remove _dup suffix if present)
        base_col = col.split('_dup')[0]

        # Keep if it's a desired column and we haven't seen it yet
        if base_col in DESIRED_COLUMNS and base_col not in seen_desired:
            columns_to_keep.append(col)
            seen_desired.add(base_col)
            #logger.debug(f"[RENAME] Keeping column: {col} (base: {base_col})")

    #logger.info(f"[RENAME] Columns to keep: {columns_to_keep}")
    #logger.info(f"[RENAME] Total desired columns found: {len(columns_to_keep)}/{len(DESIRED_COLUMNS)}")

    # Step 5: Rename back to standard names (remove _dup suffixes)
    final_rename = {col: col.split('_dup')[0] for col in columns_to_keep}
    #logger.debug(f"[RENAME] Final rename dict: {final_rename}")

    if columns_to_keep:
        result = df.select(columns_to_keep).rename(final_rename)
    else:
        #logger.warning("[RENAME] No desired columns found, returning original DataFrame")
        result = df

    #logger.info(f"[RENAME] Final columns: {result.columns}")
    #logger.info(f"[RENAME] Final shape: {result.height} rows x {result.width} cols")

    return result