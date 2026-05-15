"""
ild_to_db.py
────────────────────────────────────────────────────────────────────────────
ILD bulk-insert logic.

Collections
──────────────────────────────────────────────────────────────────────
  ild_db → ILDRecords      (call detail rows)
  ild_db → ILDNexus        (per-file metadata)
  cdr_db → CrimeRegistry   (shared with CDR pipeline)
  cdr_db → UserAccessMapping

Mandatory fields: A_Party | B_Party | Call_Type
"""

from __future__ import annotations

import xxhash
from datetime import datetime, date
from typing import Any

import polars as pl
from mongoengine import get_db
from pymongo import ASCENDING
from pymongo.errors import BulkWriteError

from ..ild.ild_utils import (
    is_indian_number,
    extract_country_code,
    derive_country_codes,
    normalize_indian_cc,
    _RE_INDIAN_91,
    _INDIAN_PREFIX_LEN,
)

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

_SIMPLE_TYPES = frozenset([int, float, str, bool, type(None)])

# Always stored as str to prevent BSON $numberLong coercion.
_STRING_ONLY_FIELDS = frozenset({"A_Party", "B_Party"})

# Always written to the document even when the value is "" or 0.
_KEEP_FIELDS = frozenset({
    "Duration",
    "a_mobile_code", "b_mobile_code",
    "a_country_code", "b_country_code",
})

# JSON key → canonical MongoDB field name.
ILD_COLUMN_MAP: dict[str, str] = {
    "A_Party":      "A_Party",
    "B_Party":      "B_Party",
    "SDate":        "SDate",
    "STime":        "STime",
    "Duration":     "Duration",
    "FileCallType": "Call_Type",
    "First_CGI":    "First_CGI",
    "Last_CGI":     "Last_CGI",
    "IMEI":         "IMEI",
    "IMSI":         "IMSI",
    "Roaming":      "Roaming_Circle",
    "LRN":          "LRN",
}

MANDATORY_FIELDS: set[str] = {"A_Party", "B_Party", "Call_Type"}

_ILD_INDEX_DEFINITIONS = [
    [("A_Party",        ASCENDING)],
    [("B_Party",        ASCENDING)],
    [("SDate",          ASCENDING)],
    [("seq_id",         ASCENDING)],
    [("First_CGI",      ASCENDING)],
    [("IMEI",           ASCENDING)],
    [("Call_Type",      ASCENDING)],
    [("a_country_code", ASCENDING)],
    [("b_country_code", ASCENDING)],
]

_IGNORE_VALUES: frozenset[Any] = frozenset(
    [None, "", "-", "0", 0, "null", "NULL", "None", "--"]
)


# ══════════════════════════════════════════════════════════════════════════════
# COLUMN MAPPING
# ══════════════════════════════════════════════════════════════════════════════

def build_reverse_lookup(ild_headers: dict) -> dict[str, str]:
    """Raw header → canonical column name lookup from ILDHeaders.json."""
    reverse: dict[str, str] = {}
    for json_key, raw_variants in ild_headers.items():
        canonical = ILD_COLUMN_MAP.get(json_key) or json_key.replace(" ", "_").replace("/", "_")
        for variant in raw_variants:
            reverse[variant.strip()]         = canonical
            reverse[variant.strip().lower()] = canonical
    return reverse


def rename_columns(df: pl.DataFrame, ild_headers: dict) -> tuple[pl.DataFrame, set[str]]:
    """
    Rename DataFrame columns to canonical names.
    Returns (renamed_df, set_of_canonical_columns_present).
    """
    lookup        = build_reverse_lookup(ild_headers)
    existing_cols = set(df.columns)
    rename_map:     dict[str, str] = {}
    seen_canonical: dict[str, str] = {}
    cols_to_drop:   list[str]      = []

    for raw_col in df.columns:
        canonical = lookup.get(raw_col) or lookup.get(raw_col.lower())
        if not canonical:
            continue
        if raw_col == canonical:
            seen_canonical.setdefault(canonical, raw_col)
            continue
        if canonical in existing_cols and canonical != raw_col:
            cols_to_drop.append(raw_col)
            seen_canonical.setdefault(canonical, canonical)
            continue
        if canonical in seen_canonical:
            cols_to_drop.append(raw_col)
            continue
        seen_canonical[canonical] = raw_col
        rename_map[raw_col]       = canonical

    if cols_to_drop:
        df = df.drop([c for c in cols_to_drop if c in df.columns])
    if rename_map:
        df = df.rename({k: v for k, v in rename_map.items() if k in df.columns})

    matched = {col for col in df.columns if col in set(ILD_COLUMN_MAP.values())}
    return df, matched


def validate_mandatory(matched_columns: set[str]) -> list[str]:
    return sorted(MANDATORY_FIELDS - matched_columns)


# ══════════════════════════════════════════════════════════════════════════════
# INDEX MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

def manage_ild_indexes(action: str = "rebuild") -> None:
    collection = get_db("ild_db")["ILDRecords"]
    if action == "drop":
        for idx in list(collection.list_indexes()):
            if idx["name"] != "_id_":
                try:
                    collection.drop_index(idx["name"])
                except Exception as exc:
                    print(f"  ⚠ Could not drop {idx['name']}: {exc}")
    elif action == "rebuild":
        existing = [info["key"] for info in collection.index_information().values()]
        for idx_def in _ILD_INDEX_DEFINITIONS:
            if idx_def not in existing:
                try:
                    collection.create_index(idx_def, background=True)
                except Exception as exc:
                    print(f"  ⚠ Could not create {idx_def}: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# RECORD SERIALISER
# ══════════════════════════════════════════════════════════════════════════════

def _clean_record(record: dict, nexus_hash: str) -> dict:
    """
    Serialise one row for MongoDB insertion.

    • _STRING_ONLY_FIELDS (A_Party, B_Party) → always str.
    • _KEEP_FIELDS (Duration, *_mobile_code, *_country_code) → always present,
      even when value is "" or 0, so Indian numbers always carry b_mobile_code
      and international numbers always carry a/b_country_code.
    • Everything else → filtered by _IGNORE_VALUES.
    """
    out: dict = {}

    for k, v in record.items():
        if k in _STRING_ONLY_FIELDS:
            out[k] = str(v) if v is not None else ""
            continue
        if isinstance(v, date) and not isinstance(v, datetime):
            out[k] = datetime.combine(v, datetime.min.time())
        elif isinstance(v, datetime):
            out[k] = v
        elif type(v) in _SIMPLE_TYPES:
            if k not in _KEEP_FIELDS and v in _IGNORE_VALUES:
                continue
            out[k] = v
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v

    # Guarantee all four code fields are always present in every document.
    for field in ("a_mobile_code", "b_mobile_code", "a_country_code", "b_country_code"):
        if field not in out:
            out[field] = ""

    out["seq_id"] = [nexus_hash]
    return out


# ══════════════════════════════════════════════════════════════════════════════
# SEQUENCE DATA EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def _extract_seq_data(raw_dicts: list[dict]) -> dict:
    from collections import Counter

    a_counter: Counter = Counter()
    dates:     list[datetime] = []
    durations: list[int]      = []

    for row in raw_dicts:
        if ap := row.get("A_Party"):
            num = str(ap).strip()
            if num:
                a_counter[num] += 1
        for key in ("SDateTime", "SDate"):
            sd = row.get(key)
            if sd is not None:
                dates.append(sd if isinstance(sd, datetime)
                             else datetime.combine(sd, datetime.min.time()))
                break
        if (dur := row.get("Duration")) is not None:
            try:
                durations.append(int(dur))
            except (TypeError, ValueError):
                pass

    # Pick the most-frequent A_Party as the primary subscriber number (ILDNo).
    #
    # After the swap pass in ild_views._apply_party_swap, A_Party ALWAYS
    # contains the target/dominant number — Indian or international.
    # Therefore the correct ILDNo is simply whichever A_Party number appears
    # most often.  We no longer prefer Indian numbers: if an international
    # number (e.g. "971554596491") is the target it will dominate A_Party and
    # must be used as the primary, even when some rows still contain a stray
    # Indian number in A_Party (edge-cases or parse artefacts).
    #
    # Tie-break: higher frequency wins; equal frequency → lexicographically
    # smallest (preserves the old deterministic behaviour).
    if a_counter:
        primary_number = max(a_counter, key=lambda n: (a_counter[n], n))
    else:
        primary_number = "UNKNOWN"

    return {
        "a_numbers": set(a_counter.keys()),
        "FromDate":  min(dates)     if dates     else datetime(1970, 1, 1),
        "ToDate":    max(dates)     if dates     else datetime(1970, 1, 1),
        "min_dur":   min(durations) if durations else 0,
        "max_dur":   max(durations) if durations else 0,
        "primary_number": primary_number,
    }


# ══════════════════════════════════════════════════════════════════════════════
# PARTY NUMBER NORMALISATION  (safety gate before insert)
# ══════════════════════════════════════════════════════════════════════════════

def _normalise_b_party(df: pl.DataFrame) -> pl.DataFrame:
    """
    Final normalisation of B_Party and its derived code columns.

    This is a safety gate that runs after _apply_ild_field_parsing.  In normal
    flow the views pipeline already handles all of this; the gate defends
    against direct calls to insert_ild_file or edge-cases in the views parse.

    Actions (per row, using position-safe enumerate — never .index()):
    ─────────────────────────────────────────────────────────────────
    1. Strip residual leading zeros and float artefacts from B_Party.
    2. Strip a '91' dial-out prefix from any 12-digit Indian number
       ("916313103860" → "6313103860") so country/mobile codes are correct.
    3. Re-derive b_country_code from the cleaned number.
    4. Re-derive b_mobile_code:
         • International (has country code) → ""
         • Indian with a valid existing code → keep it
         • Indian with a stale/empty code   → first _INDIAN_PREFIX_LEN digits
    """
    if "B_Party" not in df.columns:
        return df

    try:
        # Step 1: string cleaning via Polars expressions
        df = df.with_columns(
            pl.col("B_Party")
            .cast(pl.Utf8)
            .str.strip_chars("'\" ")
            .str.replace_all(r"\.0+$", "")
            .str.replace(r"^0+", "")
            .alias("B_Party")
        )

        b_list      = df["B_Party"].to_list()
        existing_mc = (
            df["b_mobile_code"].to_list()
            if "b_mobile_code" in df.columns
            else [""] * len(b_list)
        )

        new_b:  list[str] = []
        new_cc: list[str] = []
        new_mc: list[str] = []

        # Step 2-4: per-row normalisation using enumerate (never .index())
        for idx, (raw_num, old_mc) in enumerate(zip(b_list, existing_mc)):
            num = raw_num or ""

            # 2. Strip 91 prefix if still present (views pipeline may have missed it)
            num = normalize_indian_cc(num)

            # 3. Re-derive country code
            cc = extract_country_code(num) if num else ""

            # 4. Re-derive mobile code
            if cc:
                # International: country_code is the identifier, mobile_code is blank
                mc = ""
            elif num and is_indian_number(num):
                # Indian mobile: keep existing code only if it looks clean
                # (does not start with "91", meaning it was computed from the
                # un-normalised number).
                clean_old = (old_mc or "").lstrip()
                stale     = clean_old.startswith("91") or not clean_old
                mc        = (
                    clean_old
                    if not stale
                    else (num[:_INDIAN_PREFIX_LEN] if len(num) >= _INDIAN_PREFIX_LEN else "")
                )
            else:
                mc = ""

            new_b.append(num)
            new_cc.append(cc or "")
            new_mc.append(mc)

        df = df.with_columns([
            pl.Series("B_Party",        new_b,  dtype=pl.Utf8),
            pl.Series("b_country_code", new_cc, dtype=pl.Utf8),
            pl.Series("b_mobile_code",  new_mc, dtype=pl.Utf8),
        ])

    except Exception as exc:
        print(f"[ILD-INSERT] ⚠ B_Party normalisation failed: {exc}")

    return df


# ══════════════════════════════════════════════════════════════════════════════
# MAIN INSERT
# ══════════════════════════════════════════════════════════════════════════════

def insert_ild_file(
    df: pl.DataFrame,
    crime_name: str,
    area_location: str,
    filename: str,
    user_id: str,
    ild_headers: dict,
    top_rows_skipped: int = 0,
) -> dict:
    errors: list[str] = []

    # ── 1. Column renaming & validation ──────────────────────────────────────
    df, matched = rename_columns(df, ild_headers)
    missing     = validate_mandatory(matched)
    if missing:
        return {"inserted": 0, "duplicates": 0, "updated": 0,
                "errors": [f"Missing mandatory columns: {', '.join(missing)}"], "nexus": {}}

    # ── 2. CGI uppercase ─────────────────────────────────────────────────────
    for cgi_col in ("First_CGI", "Last_CGI"):
        if cgi_col in df.columns:
            df = df.with_columns(pl.col(cgi_col).cast(pl.Utf8).str.to_uppercase().alias(cgi_col))

    # ── 3. A_Party: re-derive a_country_code ─────────────────────────────────
    # Safety gate for A_Party only.  B_Party country + mobile codes are handled
    # together in Step 4 (_normalise_b_party) to keep them consistent.
    if "A_Party" in df.columns:
        try:
            cc_a = derive_country_codes(df["A_Party"].to_list())
            df   = df.with_columns(pl.Series("a_country_code", cc_a, dtype=pl.Utf8))
        except Exception as exc:
            print(f"[ILD-INSERT] ⚠ a_country_code derivation failed: {exc}")

    # ── 4. B_Party: full normalisation (number + country code + mobile code) ──
    df = _normalise_b_party(df)

    # ── 5. Ensure mobile-code columns exist (default "") ─────────────────────
    # Defends against source files that had no party columns at all.
    for mc_col in ("a_mobile_code", "b_mobile_code"):
        if mc_col not in df.columns:
            df = df.with_columns(pl.lit("").alias(mc_col))

    # ── 6. Sequence metadata ──────────────────────────────────────────────────
    raw_dicts = df.to_dicts()
    if not raw_dicts:
        return {"inserted": 0, "duplicates": 0, "updated": 0,
                "errors": ["DataFrame empty after renaming."], "nexus": {}}

    seq_data       = _extract_seq_data(raw_dicts)
    primary_number = seq_data["primary_number"]   # most-frequent Indian A_Party → ILDNo

    # ── 7. Hashes ─────────────────────────────────────────────────────────────
    nexus_hash = xxhash.xxh64(f"{crime_name}{area_location}{user_id}{primary_number}".lower()).hexdigest()
    crime_hash = xxhash.xxh64(f"{crime_name}{area_location}".lower()).hexdigest()
    user_hash  = xxhash.xxh64(str(user_id).lower()).hexdigest()

    # ── 8. Content-based _id ─────────────────────────────────────────────────
    id_exprs = [
        (pl.col(c).cast(pl.Utf8).fill_null("").str.to_lowercase() if c in df.columns else pl.lit(""))
        for c in ("A_Party", "B_Party", "SDate", "STime", "Duration", "Call_Type", "First_CGI", "IMEI")
    ]
    df = df.with_columns(pl.concat_str(id_exprs, separator="").alias("_id_raw"))
    hashed_ids = [xxhash.xxh64(s.encode()).hexdigest() for s in df["_id_raw"].to_list()]
    df = df.with_columns(pl.Series("_id", hashed_ids)).drop("_id_raw")

    # ── 9. Serialise ──────────────────────────────────────────────────────────
    clean_records = [_clean_record(r, nexus_hash) for r in df.to_dicts()]

    # ── 10. Bulk insert (chunked to stay under MongoDB's 16 MB BSON limit) ───
    collection     = get_db("ild_db")["ILDRecords"]
    inserted_count = 0
    duplicate_ids: list[str] = []
    updated_count  = 0

    _CHUNK_SIZE = 5_000          # ~5 000 rows ≈ well under 16 MB per batch

    for chunk_start in range(0, len(clean_records), _CHUNK_SIZE):
        chunk = clean_records[chunk_start : chunk_start + _CHUNK_SIZE]
        try:
            result          = collection.insert_many(chunk, ordered=False)
            inserted_count += len(result.inserted_ids)
        except BulkWriteError as bwe:
            details         = bwe.details
            inserted_count += details.get("nInserted", 0)
            for err in details.get("writeErrors", []):
                if err.get("code") == 11000:
                    duplicate_ids.append(err["op"]["_id"])
                else:
                    errors.append(f"Write error {err.get('code')}: {err.get('errmsg', '')}")
        except Exception as exc:
            import traceback; traceback.print_exc()
            errors.append(str(exc))

    # ── 11. Update duplicates ─────────────────────────────────────────────────
    if duplicate_ids:
        try:
            upd = collection.update_many(
                {"_id": {"$in": duplicate_ids}},
                {"$addToSet": {"seq_id": nexus_hash}},
            )
            updated_count = upd.modified_count
        except Exception as exc:
            errors.append(f"Duplicate update error: {exc}")

    # ── 12. ILDNexus upsert ───────────────────────────────────────────────────
    now       = datetime.now()
    nexus_doc = {
        "_id":          nexus_hash,
        "RecordType":   "ILD",
        "CaseName":     crime_name,
        "ILDNo":        primary_number,
        "FileName":     filename,
        "FromDate":     seq_data["FromDate"],
        "ToDate":       seq_data["ToDate"],
        "MinDur":       seq_data["min_dur"],
        "MaxDur":       seq_data["max_dur"],
        "Inserted":     inserted_count,
        "Duplicate":    len(duplicate_ids),
        "Updated":      updated_count,
        "Skipped":      top_rows_skipped,
        "InsertedAt":   now,
        "Year":         now.year,
        "Month":        now.month,
        "Day":          now.day,
        "CrimeID":      crime_hash,
        "UserAccessID": user_hash,
    }

    nexus_coll = get_db("ild_db")["ILDNexus"]
    existing   = nexus_coll.find_one({"_id": nexus_hash})
    if existing:
        def _earlier(a, b): return a if a and b and a < b else b or a
        def _later(a, b):   return a if a and b and a > b else b or a
        nexus_doc["FromDate"]  = _earlier(existing.get("FromDate"),  nexus_doc["FromDate"])
        nexus_doc["ToDate"]    = _later(existing.get("ToDate"),      nexus_doc["ToDate"])
        nexus_doc["MinDur"]    = min(existing.get("MinDur", 0),      nexus_doc["MinDur"])
        nexus_doc["MaxDur"]    = max(existing.get("MaxDur", 0),      nexus_doc["MaxDur"])
        nexus_doc["Inserted"]  += existing.get("Inserted",  0)
        nexus_doc["Duplicate"] += existing.get("Duplicate", 0)
        nexus_doc["Updated"]   += existing.get("Updated",   0)
        nexus_doc["Skipped"]   += existing.get("Skipped",   0)

    nexus_coll.update_one({"_id": nexus_hash}, {"$set": nexus_doc}, upsert=True)

    # ── 13. Crime & User registries ───────────────────────────────────────────
    cdr_db = get_db("cdr_db")
    for coll, doc in [
        (cdr_db["CrimeRegistry"], {"_id": crime_hash, "Crime": crime_name,
                                    "AreaLocation": area_location, "seq_id": nexus_hash}),
        (cdr_db["UserAccessMapping"], {"_id": user_hash, "UserID": user_id, "seq_id": nexus_hash}),
    ]:
        if not coll.find_one({"_id": doc["_id"]}):
            coll.insert_one(doc)

    return {
        "inserted":   inserted_count,
        "duplicates": len(duplicate_ids),
        "updated":    updated_count,
        "errors":     errors,
        "nexus":      nexus_doc,
    }