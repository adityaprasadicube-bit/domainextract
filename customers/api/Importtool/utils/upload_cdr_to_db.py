# dao/upload_cdr_to_db.py
# FLAT COLLECTION VERSION (no monthly partitioning)
#
# ARCHITECTURE:
# ─────────────────────────────────────────────────────────────────────────────
#   CDR/IMEI data  → cdr_db.CallDetailRecords
#   TowerDump data → tower_dump.TowerDumpRecords
#   ISD data       → tower_dump.TowerDumpRecords
# ─────────────────────────────────────────────────────────────────────────────

import traceback
from datetime import date, datetime
from typing import Dict, List, Optional

import polars as pl
import xxhash
from mongoengine import get_db
from pymongo import ASCENDING, UpdateOne
from pymongo.errors import BulkWriteError

from ..utils.updating_columnnames import data_seq
from ..utils.user_data import UserDetails

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SIMPLE_TYPES  = frozenset([int, float, str, bool, type(None)])
IGNORE_VALUES = frozenset({None, "", "-", "0", 0, "null", "NULL", "None", "--", " "})

# Flat collection names
CDR_COLLECTION   = "CallDetailRecords"
TOWER_COLLECTION = "TowerDumpRecords"

_INSERT_BATCH = 2_000   # bulk_write batch size

# Sentinel for "unset" dates
_EPOCH = datetime(1970, 1, 1)

# Fields whose zero / empty values must NOT be stripped
_KEEP_ZERO_FIELDS: frozenset = frozenset({"Duration"})

# Columns used to compute content-based _id hash (order is reproducible)
_ID_COLS = [
    "A_Party", "B_Party", "SDateTime", "Duration",
    "FileCallType", "First_CGI", "IMEI", "IMSI",
]


# ---------------------------------------------------------------------------
# User-ID cache  (process-level)
# ---------------------------------------------------------------------------
_USER_ID_CACHE: Optional[str] = None


def get_user_id() -> str:
    global _USER_ID_CACHE
    if _USER_ID_CACHE is not None:
        return _USER_ID_CACHE
    _USER_ID_CACHE = UserDetails().user()
    return _USER_ID_CACHE


def set_user_id(user_id: str) -> None:
    global _USER_ID_CACHE
    _USER_ID_CACHE = user_id


# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------
INDEX_DEFINITIONS = [
    [("A_Party",    ASCENDING)],
    [("B_Party",    ASCENDING)],
    [("SDateTime",  ASCENDING)],
    [("seq_id",     ASCENDING)],
    [("First_CGI",  ASCENDING)],
    [("IMEI",       ASCENDING)],
    [("IMSI",       ASCENDING)],
]


def manage_indexes(action: str = "drop") -> None:
    """Drop or rebuild indexes on the flat CDR and TowerDump collections."""
    cdr_db   = get_db("cdr_db")
    tower_db = get_db("tower_dump")

    targets = [
        (cdr_db,   CDR_COLLECTION),
        (tower_db, TOWER_COLLECTION),
    ]

    if action == "drop":
        print("\n[INDEX] Dropping indexes …")
        for db, coll_name in targets:
            coll    = db[coll_name]
            dropped = 0
            for idx in list(coll.list_indexes()):
                if idx["name"] != "_id_":
                    try:
                        coll.drop_index(idx["name"])
                        dropped += 1
                    except Exception as exc:
                        print(f"  ⚠  {coll_name}: drop {idx['name']}: {exc}")
            print(f"  ✓ {coll_name}: dropped {dropped} indexes")

    elif action == "rebuild":
        print("\n[INDEX] Rebuilding indexes …")
        for db, coll_name in targets:
            coll          = db[coll_name]
            existing_keys = [
                info["key"] for info in coll.index_information().values()
            ]
            created = skipped = 0
            for idx_def in INDEX_DEFINITIONS:
                if idx_def in existing_keys:
                    skipped += 1
                    continue
                try:
                    coll.create_index(idx_def, background=True)
                    created += 1
                except Exception as exc:
                    print(f"  ⚠  {coll_name}: create {idx_def}: {exc}")
            print(f"  ✓ {coll_name}: created {created}, skipped {skipped}")

    print("[INDEX] Done.\n")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_scientific_notation(value: str) -> str:
    """Convert scientific notation like 9.71509E+11 to 971509000000"""
    if not value:
        return value
    value_str = str(value).strip()
    if 'E' in value_str.upper():
        try:
            return str(int(float(value_str)))
        except Exception:
            return value_str
    return value_str


def is_indian_number(phone_number) -> bool:
    """
    Check if a phone number is an Indian number.

    Indian numbers:
    - 10 digits starting with 6,7,8,9
    - 12 digits starting with 91 (country code)
    - 11 digits starting with 0  (STD code)
    """
    if not phone_number:
        return False
    try:
        phone_str = str(phone_number).strip()
        if 'E' in phone_str.upper():
            try:
                phone_str = str(int(float(phone_str)))
            except Exception:
                pass
        if phone_str.startswith('+'):
            phone_str = phone_str[1:]
        if phone_str.startswith('00'):
            phone_str = phone_str[2:]
        clean_number = ''.join(c for c in phone_str if c.isdigit())
        if not clean_number:
            return False
        if len(clean_number) == 10 and clean_number[0] in '6789':
            return True
        if len(clean_number) == 12 and clean_number.startswith('91') and clean_number[2] in '6789':
            return True
        if len(clean_number) == 11 and clean_number.startswith('0'):
            return True
        return False
    except Exception:
        return False


def is_isd_number(phone_number) -> bool:
    """Detect if a phone number is an International (ISD) call."""
    if not phone_number:
        return False
    if is_indian_number(phone_number):
        return False
    try:
        phone_str = str(phone_number).strip()
        if 'E' in phone_str.upper():
            try:
                phone_str = str(int(float(phone_str)))
            except Exception:
                pass
        clean_number = ''.join(c for c in phone_str if c.isdigit() or c == '+')
        if not clean_number:
            return False
        if clean_number.startswith('+'):
            return True
        if clean_number.startswith('00'):
            return True
        if len(clean_number) > 10:
            return True
        return False
    except Exception:
        return False


def detect_isd_record(seq_data: dict, top_rows_dict: dict = None, df=None) -> tuple:
    """
    Check if the record contains ISD calls.

    Returns:
        (is_isd, detected_number)
    """
    isd_numbers = set()

    numbers = seq_data.get('number', set())
    for num in numbers:
        if num and is_isd_number(str(num)):
            cleaned_num = _clean_scientific_notation(str(num))
            isd_numbers.add(cleaned_num)
            print(f"[ISD] 📞 ISD number found: {cleaned_num}")

    if df is not None:
        for col in ['A_Party', 'B_Party', 'Other_Party_No', 'Mobile_No']:
            if col in df.columns:
                try:
                    sample_values = df[col].drop_nulls().head(10).to_list()
                    for val in sample_values:
                        if val and is_isd_number(str(val)):
                            cleaned_val = _clean_scientific_notation(str(val))
                            isd_numbers.add(cleaned_val)
                            print(f"[ISD] 📞 ISD found in {col}: {cleaned_val}")
                except Exception:
                    pass

    if top_rows_dict:
        for col_name, col_data in top_rows_dict.items():
            col_lower = col_name.lower()
            if col_lower in ['mobile_no', 'other_party_no', 'b_party', 'b_party_no', 'a_party']:
                try:
                    values = col_data.drop_nulls().to_list() if hasattr(col_data, 'drop_nulls') else [col_data]
                    for val in values[:10]:
                        if val and is_isd_number(str(val)):
                            cleaned_val = _clean_scientific_notation(str(val))
                            isd_numbers.add(cleaned_val)
                            print(f"[ISD] 📞 ISD found in {col_name}: {cleaned_val}")
                except Exception:
                    pass

    if isd_numbers:
        return True, sorted(isd_numbers)[0]
    return False, None


# ---------------------------------------------------------------------------
# Record-type detection
# ---------------------------------------------------------------------------

def _detect_from_top_rows(top_rows_dict: dict, seq_data: dict) -> tuple:
    record_type    = "UnKnown"
    detected_value = None

    if not top_rows_dict or not seq_data:
        return record_type, detected_value

    try:
        clean_data = {
            col: s.drop_nulls().to_list() if hasattr(s, "drop_nulls") else s
            for col, s in top_rows_dict.items()
        }
    except Exception:
        return record_type, detected_value

    numbers  = {str(x) for x in seq_data.get("number",    set()) if x}
    imeis    = {str(x) for x in seq_data.get("imei",      set()) if x}
    firstcgi = {str(x) for x in seq_data.get("first_cgi", set()) if x}

    if len(firstcgi) == 1:
        return "TowerDump", sorted(firstcgi)[0]
    if len(numbers) == 1:
        return "CDR", sorted(numbers)[0]
    if len(imeis) == 1:
        return "IMEI", sorted(imeis)[0]

    for values in clean_data.values():
        if not isinstance(values, (list, tuple)):
            values = [values]
        for val in values:
            if not val:
                continue
            text = str(val).lower()
            for cgi in firstcgi:
                if cgi and cgi in text:
                    return "TowerDump", cgi
            for im in imeis:
                if im and im in text:
                    return "IMEI", im
            for num in numbers:
                if num and num in text:
                    return "CDR", num

    return record_type, detected_value


def _detect_from_filename(seq_data: dict, filename: str, top_rows_dict: dict = None) -> tuple:
    if not filename or not seq_data:
        return "UnKnown", None

    fn_clean = str(filename).lower().replace(" ", "")
    numbers  = {str(x).strip() for x in seq_data.get("number",    set()) if x}
    imeis    = {str(x).strip() for x in seq_data.get("imei",      set()) if x}
    firstcgi = {str(x).strip() for x in seq_data.get("first_cgi", set()) if x}

    try:
        if len(firstcgi) == 1:
            cgi = next(iter(firstcgi))
            if cgi and cgi.replace(" ", "").lower() in fn_clean:
                return "TowerDump", cgi
        if len(imeis) == 1:
            im = next(iter(imeis))
            if im and im.replace(" ", "").lower() in fn_clean:
                return "IMEI", im
        if len(numbers) == 1:
            num = next(iter(numbers))
            if num and num.replace(" ", "").lower() in fn_clean:
                return "CDR", num
    except Exception as exc:
        print(f"[FILENAME-DETECT] {exc}")

    return "UnKnown", None


def _detect_record_type_seqdata(seq_data: dict) -> tuple:
    numbers = seq_data.get("number",    set()) or set()
    imeis   = seq_data.get("imei",      set()) or set()
    cgis    = seq_data.get("first_cgi", set()) or set()

    if len(cgis) == 1:
        return "TowerDump", next(iter(cgis))
    if len(numbers) >= 1:
        return "CDR", next(iter(numbers))
    if len(numbers) == 0 and len(imeis) == 1:
        return "IMEI", next(iter(imeis))
    return "Unknown", None


def _check_isd_override(
    seq_data: dict,
    top_rows: dict,
    df:       Optional[pl.DataFrame],
) -> bool:
    is_isd, isd_number = detect_isd_record(
        seq_data      = seq_data,
        top_rows_dict = top_rows,
        df            = df,
    )
    if is_isd:
        print(f"[ISD] 🌍 ISD call detected — number={isd_number}")
    return is_isd


def _detect_record_type(
    seq_data: dict,
    filename: str,
    top_rows: dict = None,
    df:       Optional[pl.DataFrame] = None,
) -> dict:
    """
    Populate seq_data with RecordType and CDRNo_Or_ImeiNo / Tower_id.

    Type routing:
        CDR       → cdr_db.CallDetailRecords
        IMEI      → cdr_db.CallDetailRecords
        TowerDump → tower_dump.TowerDumpRecords
        ISD       → tower_dump.TowerDumpRecords  (ISD override)
    """
    record_type    = "UnKnown"
    detected_value = None

    if top_rows:
        record_type, detected_value = _detect_from_top_rows(top_rows, seq_data)
    if record_type == "UnKnown":
        record_type, detected_value = _detect_from_filename(seq_data, filename, top_rows)
    if record_type == "UnKnown":
        record_type, detected_value = _detect_record_type_seqdata(seq_data)

    # ISD override — only when not already TowerDump
    if record_type != "TowerDump" and _check_isd_override(seq_data, top_rows or {}, df):
        print(f"[DETECTION] 🌍 ISD CALL DETECTED — overriding '{record_type}' → ISD")
        record_type    = "ISD"
        cgis           = seq_data.get("first_cgi", set()) or set()
        detected_value = sorted(cgis)[0] if cgis else None
        print(f"[DETECTION] ✅ ISD file → Tower_id will use CGI={detected_value}")

    if filename:
        seq_data["FileName"] = filename

    seq_data["RecordType"] = record_type
    seq_data.pop("CDRNo_Or_ImeiNo", None)
    seq_data.pop("Tower_id",        None)

    if record_type in ("CDR", "IMEI"):
        src  = "number" if record_type == "CDR" else "imei"
        pool = seq_data.get(src, set())
        final_value = (
            str(detected_value) if detected_value
            else str(sorted(pool)[0]) if pool
            else "ISD_UNKNOWN"
        )
        seq_data["CDRNo_Or_ImeiNo"] = final_value
        print(f"[DETECT] {record_type} = {final_value}")

    elif record_type in ("TowerDump", "ISD"):
        cgts = seq_data.get("first_cgi", set())
        final_value = (
            str(detected_value) if detected_value
            else str(sorted(cgts)[0]) if cgts
            else "ISD_UNKNOWN"
        )
        seq_data["Tower_id"] = final_value
        print(f"[DETECT] {record_type} = {final_value}")

    else:
        print("[DETECT] ⚠ UnKnown record type")

    print(f"[DETECT] RecordType = {seq_data['RecordType']}")
    return seq_data


# ---------------------------------------------------------------------------
# Vectorised Polars cleaning + _id hashing
# ---------------------------------------------------------------------------

def _build_id_column(df: pl.DataFrame) -> pl.DataFrame:
    """Compute content-based _id via map_batches (xxhash, batch-level)."""
    id_exprs = [
        pl.col(c).cast(pl.Utf8).fill_null("").str.to_lowercase()
        if c in df.columns
        else pl.lit("")
        for c in _ID_COLS
    ]
    df = df.with_columns(pl.concat_str(id_exprs, separator="").alias("_id_string"))

    def _hash_series(s: pl.Series) -> pl.Series:
        return pl.Series(
            [xxhash.xxh64(v.encode()).hexdigest() for v in s.to_list()],
            dtype=pl.Utf8,
        )

    df = df.with_columns(
        pl.col("_id_string")
          .map_batches(_hash_series, return_dtype=pl.Utf8)
          .alias("_id")
    ).drop("_id_string")

    return df


def _clean_cgi_columns(df: pl.DataFrame) -> pl.DataFrame:
    """Uppercase CGI columns in-place (vectorised)."""
    for col in ("First_CGI", "Last_CGI"):
        if col in df.columns:
            df = df.with_columns(
                pl.col(col).cast(pl.Utf8).str.to_uppercase().alias(col)
            )
    return df


# ---------------------------------------------------------------------------
# Core: upsert into flat collection
# ---------------------------------------------------------------------------

def _insert_to_collection(
    collection,
    coll_name:  str,
    df:         pl.DataFrame,
    nexus_hash: str,
) -> tuple:
    """
    Upsert all rows in df into *collection* (a flat pymongo Collection).

    Returns
    ───────
    inserted_count  : int
    duplicate_count : int
    updated_count   : int
    skipped_no_dt   : int
    """
    inserted_count  = 0
    duplicate_count = 0
    updated_count   = 0
    skipped_no_dt   = 0

    if "SDateTime" not in df.columns:
        print(f"[INSERT] SDateTime missing — entire chunk skipped")
        return 0, 0, 0, len(df)

    valid_mask    = df["SDateTime"].is_not_null()
    skipped_no_dt = int((~valid_mask).sum())
    df            = df.filter(valid_mask)

    if df.is_empty():
        return 0, 0, 0, skipped_no_dt

    docs = df.to_dicts()

    cleaned_docs = []
    for doc in docs:
        doc["seq_id"] = nexus_hash

        # Convert bare date → datetime
        for k, v in list(doc.items()):
            if isinstance(v, date) and not isinstance(v, datetime):
                doc[k] = datetime.combine(v, datetime.min.time())

        # Drop ignore-values (keep zero-protected fields)
        keys_to_drop = [
            k for k, v in doc.items()
            if k not in _KEEP_ZERO_FIELDS
            and (v is None or (isinstance(v, str) and v in IGNORE_VALUES))
        ]
        for k in keys_to_drop:
            del doc[k]

        cleaned_docs.append(doc)

    # Upsert: $setOnInsert on first write, $addToSet merges seq_id
    ops = [
        UpdateOne(
            {"_id": doc["_id"]},
            {
                "$setOnInsert": {
                    k: v for k, v in doc.items()
                    if k not in ("_id", "seq_id")
                },
                # Change this to use $set instead of $addToSet since seq_id is now a string
                "$set": {"seq_id": nexus_hash},  # ✅ Overwrite with latest nexus_hash
                # Or if you want to keep multiple references:
                # "$addToSet": {"seq_ids": nexus_hash}  # Use a different field for multiple
            },
            upsert=True,
        )
        for doc in cleaned_docs
    ]

    for i in range(0, len(ops), _INSERT_BATCH):
        batch = ops[i: i + _INSERT_BATCH]
        try:
            result = collection.bulk_write(batch, ordered=False)
            inserted_count  += result.upserted_count
            updated_count   += result.modified_count
            duplicate_count += (
                len(batch) - result.upserted_count - result.modified_count
            )
            print(
                f"[INSERT] {coll_name}: "
                f"+{result.upserted_count} new, "
                f"{result.modified_count} seq_id-merged"
            )
        except BulkWriteError as bwe:
            det = bwe.details
            inserted_count  += det.get("nUpserted", 0)
            updated_count   += det.get("nModified", 0)
            print(
                f"[INSERT] BulkWriteError in {coll_name}: "
                f"{det.get('writeErrors', [])[:3]}"
            )
        except Exception as exc:
            print(f"[INSERT] Fatal error → {coll_name}: {exc}")
            traceback.print_exc()

    return inserted_count, duplicate_count, updated_count, skipped_no_dt


# ---------------------------------------------------------------------------
# Nexus / CrimeRegistry / UserAccessMapping upsert
# ---------------------------------------------------------------------------

def _upsert_nexus(
    seqdata:        dict,
    record_type:    str,
    cdr_no_or_imei: str,
    tac_or_code:    str,
    nexus_hash:     str,
    crime_hash:     str,
    user_hash:      str,
    crime_name:     str,
    area_location:  str,
    filename:       str,
    UserId:         str,
    inserted_count: int,
    duplicate_count: int,
    updated_count:  int,
    skipped_count:  int,
    cdr_db,
    tower_db,
) -> dict:
    """Upsert nexus metadata and related registry docs."""
    try:
        from cdrapp.models import (
            CallRecord, CrimeRecord, UserRecord, DataNexus, TowerDumpNexus,
        )
    except ImportError:
        try:
            from cdrapi.models import CallRecord
            from model.CrimeRegistry import CrimeRecord
            from model.UserAccessMapping import UserRecord
            from model.DataNexus import DataNexus, TowerDumpNexus
        except ImportError:
            class _Simple:
                def __init__(self, **kw): self.__dict__.update(kw)
                def to_dict(self): return {k: v for k, v in self.__dict__.items() if v is not None}
            CrimeRecord = TowerDumpNexus = DataNexus = UserRecord = _Simple

    now = datetime.now()

    raw_from = seqdata.get("FromDate", _EPOCH)
    raw_to   = seqdata.get("ToDate",   _EPOCH)
    has_from = isinstance(raw_from, datetime) and raw_from > _EPOCH
    has_to   = isinstance(raw_to,   datetime) and raw_to   > _EPOCH

    # Determine which nexus collection to use
    # TowerDump and ISD both go to tower_dump.TowerDumpNexus
    is_tower_type = record_type in ("TowerDump", "ISD")

    nexus_model: dict = {
        "_id":          nexus_hash,
        "RecordType":   record_type,
        "MinDur":       seqdata.get("min_dur", 0),
        "MaxDur":       seqdata.get("max_dur", 0),
        "FileName":     filename,
        "Inserted":     inserted_count,
        "Duplicate":    duplicate_count,
        "Updated":      updated_count,
        "Skipped":      skipped_count,
        "InsertedAt":   now,
        "Year":         now.year,
        "Month":        now.month,
        "Day":          now.day,
        "CrimeID":      crime_hash,
        "UserAccessID": user_hash,
    }

    if has_from:
        nexus_model["FromDate"] = raw_from
    if has_to:
        nexus_model["ToDate"] = raw_to

    if is_tower_type:
        nexus_model["Tower_id"] = cdr_no_or_imei.upper()
        nexus_coll = tower_db["TowerDumpNexus"]
    else:
        nexus_model["CDRNo_Or_ImeiNo"]    = cdr_no_or_imei
        nexus_model["Tac_Or_Mobile_Code"] = tac_or_code
        nexus_model["ImsiCode"]           = seqdata.get("ImsiCode", "")
        nexus_coll = cdr_db["DataNexus"]

    # Read-modify-write for metadata accumulation
    existing = nexus_coll.find_one({"_id": nexus_hash})
    if existing:
        ex_from = existing.get("FromDate")
        ex_to   = existing.get("ToDate")

        if has_from and isinstance(ex_from, datetime):
            nexus_model["FromDate"] = min(raw_from, ex_from)
        elif isinstance(ex_from, datetime):
            nexus_model["FromDate"] = ex_from

        if has_to and isinstance(ex_to, datetime):
            nexus_model["ToDate"] = max(raw_to, ex_to)
        elif isinstance(ex_to, datetime):
            nexus_model["ToDate"] = ex_to

        nexus_model["MinDur"]    = min(nexus_model["MinDur"], existing.get("MinDur", nexus_model["MinDur"]))
        nexus_model["MaxDur"]    = max(nexus_model["MaxDur"], existing.get("MaxDur", nexus_model["MaxDur"]))
        nexus_model["Inserted"]  = existing.get("Inserted",  0) + inserted_count
        nexus_model["Duplicate"] = existing.get("Duplicate", 0) + duplicate_count
        nexus_model["Updated"]   = existing.get("Updated",   0) + updated_count
        nexus_model["Skipped"]   = existing.get("Skipped",   0) + skipped_count

    try:
        model_cls   = TowerDumpNexus if is_tower_type else DataNexus
        final_nexus = model_cls(**nexus_model).to_dict()
    except Exception:
        final_nexus = nexus_model

    nexus_coll.update_one({"_id": nexus_hash}, {"$set": final_nexus}, upsert=True)

    # ── CrimeRegistry ─────────────────────────────────────────────────────
    crime_doc = {
        "_id":          crime_hash,
        "Crime":        crime_name,
        "AreaLocation": area_location,
        "seq_id":       nexus_hash,
    }
    try:
        crime_doc = CrimeRecord(
            **{f: crime_doc.get(f) for f in CrimeRecord.__dataclass_fields__}
        ).to_dict()
    except Exception:
        pass

    cdr_db["CrimeRegistry"].update_one(
        {"_id": crime_hash},
        {"$setOnInsert": crime_doc},
        upsert=True,
    )

    # ── UserAccessMapping ──────────────────────────────────────────────────
    user_doc = {"_id": user_hash, "UserID": UserId, "seq_id": nexus_hash}
    try:
        user_doc = UserRecord(
            **{f: user_doc.get(f) for f in UserRecord.__dataclass_fields__}
        ).to_dict()
    except Exception:
        pass

    cdr_db["UserAccessMapping"].update_one(
        {"_id": user_hash},
        {"$setOnInsert": user_doc},
        upsert=True,
    )

    print(
        f"[NEXUS] inserted={nexus_model['Inserted']}  "
        f"dup={nexus_model['Duplicate']}  updated={nexus_model['Updated']}"
    )
    return final_nexus


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def query_cdr(
    nexus_hash:   str,
    record_type:  str,
    date_from:    Optional[datetime] = None,
    date_to:      Optional[datetime] = None,
    extra_filter: Optional[dict]     = None,
    projection:   Optional[dict]     = None,
) -> List[dict]:
    """
    Query CDR / Tower records from flat collections.

        CDR / IMEI  → cdr_db.CallDetailRecords
        TowerDump / ISD → tower_dump.TowerDumpRecords
    """
    is_tower_type = record_type in ("TowerDump", "ISD")
    db            = get_db("tower_dump") if is_tower_type else get_db("cdr_db")
    coll_name     = TOWER_COLLECTION    if is_tower_type else CDR_COLLECTION
    collection    = db[coll_name]

    base_filter: dict = {"seq_id": nexus_hash}
    if date_from or date_to:
        dt_clause: dict = {}
        if date_from: dt_clause["$gte"] = date_from
        if date_to:   dt_clause["$lte"] = date_to
        base_filter["SDateTime"] = dt_clause
    if extra_filter:
        base_filter.update(extra_filter)

    find_kwargs: dict = {"filter": base_filter}
    if projection:
        find_kwargs["projection"] = projection

    return list(collection.find(**find_kwargs))


def iter_cdr(
    nexus_hash:   str,
    record_type:  str,
    date_from:    Optional[datetime] = None,
    date_to:      Optional[datetime] = None,
    extra_filter: Optional[dict]     = None,
    projection:   Optional[dict]     = None,
):
    """Memory-efficient generator version of query_cdr."""
    is_tower_type = record_type in ("TowerDump", "ISD")
    db            = get_db("tower_dump") if is_tower_type else get_db("cdr_db")
    coll_name     = TOWER_COLLECTION    if is_tower_type else CDR_COLLECTION
    collection    = db[coll_name]

    base_filter: dict = {"seq_id": nexus_hash}
    if date_from or date_to:
        dt_clause: dict = {}
        if date_from: dt_clause["$gte"] = date_from
        if date_to:   dt_clause["$lte"] = date_to
        base_filter["SDateTime"] = dt_clause
    if extra_filter:
        base_filter.update(extra_filter)

    find_kwargs: dict = {"filter": base_filter}
    if projection:
        find_kwargs["projection"] = projection

    yield from collection.find(**find_kwargs)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def insert_cdr_file(
    df:            pl.DataFrame,
    crime_name:    str,
    area_location: str,
    filename:      str,
    top_rows:      dict,
) -> dict:
    """
    Main entry point called per-chunk from CDR format handler.

    Flow
    ────
    1.  Detect record type  (CDR / IMEI / TowerDump / ISD).
    2.  Build content-based _id inside Polars (vectorised, map_batches).
    3.  CGI uppercase + drop unused columns.
    4.  Upsert into flat collection:
            CDR / IMEI  → cdr_db.CallDetailRecords
            TowerDump / ISD → tower_dump.TowerDumpRecords
    5.  Upsert DataNexus / TowerDumpNexus / CrimeRegistry / UserAccessMapping.
    """
    if top_rows is None:
        top_rows = {}

    if df.is_empty():
        return {"inserted": 0, "duplicates": 0, "updated": 0, "seqdata": {}}

    UserId = get_user_id()
    print(f"\n[INSERT] file={filename}  crime={crime_name}  area={area_location}")

    # ── 1. Sequence metadata + record-type detection ──────────────────────
    from .updating_columnnames import cdr_seq_data
    seqdata = cdr_seq_data(df.to_dicts())

    if filename:
        seqdata["filename"] = filename

    seqdata     = _detect_record_type(seqdata, filename, top_rows, df=df)
    record_type = seqdata.get("RecordType", "UnKnown")

    # ── 2. Primary identifier + hashes ───────────────────────────────────
    cdr_no_or_imei = "UNKNOWN"
    if record_type in ("CDR", "IMEI"):
        cdr_no_or_imei = seqdata.get("CDRNo_Or_ImeiNo", "UNKNOWN")
    elif record_type in ("TowerDump", "ISD"):
        cdr_no_or_imei = seqdata.get("Tower_id", "UNKNOWN")

    file_hash = xxhash.xxh64(filename.lower()).hexdigest()
    if record_type == "IMEI":
        tac_or_code = cdr_no_or_imei[:8]
        imsi_code   = seqdata.get("ImsiCode", "")
        nexus_id    = f"{crime_name}{area_location}{UserId}{imsi_code}{cdr_no_or_imei}"
    elif record_type == "CDR":
        tac_or_code = cdr_no_or_imei[:4]
        imsi_code   = seqdata.get("ImsiCode", "")
        nexus_id    = f"{crime_name}{area_location}{UserId}{imsi_code}{cdr_no_or_imei}"

    elif record_type == "ISD":
        tac_or_code = "UNKNOWN"
        imsi_code   = ""
        nexus_id    = f"{crime_name}{area_location}{UserId}{file_hash}"
    else:
        # TowerDump / UnKnown
        tac_or_code = "UNKNOWN"
        imsi_code   = ""
        nexus_id    = f"{crime_name}{area_location}{UserId}{cdr_no_or_imei}"

    nexus_hash = xxhash.xxh64(nexus_id.lower()).hexdigest()
    crime_hash = xxhash.xxh64(f"{crime_name}{area_location}".lower()).hexdigest()
    user_hash  = xxhash.xxh64(str(UserId).lower()).hexdigest()

    print(f"[INSERT] nexus_hash={nexus_hash[:16]}…  identifier={cdr_no_or_imei}")

    # ── 3. Vectorised _id, CGI normalisation, column drops ───────────────
    df = _build_id_column(df)
    df = _clean_cgi_columns(df)

    drop_cols = [
        c for c in (
            "FirstLatLong", "LastLatLong",
            "First_CGI_ADDRESS", "Last_CGI_ADDRESS", "Roaming",
        )
        if c in df.columns
    ]
    if drop_cols:
        df = df.drop(drop_cols)

    # ── 4. Choose target DB + flat collection ─────────────────────────────
    #   CDR / IMEI  → cdr_db.CallDetailRecords
    #   TowerDump / ISD → tower_dump.TowerDumpRecords
    is_tower_type = record_type in ("TowerDump", "ISD")
    db            = get_db("tower_dump") if is_tower_type else get_db("cdr_db")
    coll_name     = TOWER_COLLECTION    if is_tower_type else CDR_COLLECTION
    collection    = db[coll_name]

    print(f"[INSERT] routing → db={'tower_dump' if is_tower_type else 'cdr_db'}  collection={coll_name}")

    # ── 5. Upsert ─────────────────────────────────────────────────────────
    inserted_count, duplicate_count, updated_count, skipped_no_dt = _insert_to_collection(
        collection, coll_name, df, nexus_hash
    )

    skipped_total = skipped_no_dt + (len(top_rows) if top_rows else 0)

    print(
        f"[INSERT] ✅ inserted={inserted_count}  "
        f"duplicates={duplicate_count}  updated={updated_count}  "
        f"skipped_no_dt={skipped_no_dt}"
    )

    # ── 6. Nexus / registry upsert ────────────────────────────────────────
    cdr_db   = get_db("cdr_db")
    tower_db = get_db("tower_dump")

    final_nexus = _upsert_nexus(
        seqdata         = seqdata,
        record_type     = record_type,
        cdr_no_or_imei  = cdr_no_or_imei,
        tac_or_code     = tac_or_code,
        nexus_hash      = nexus_hash,
        crime_hash      = crime_hash,
        user_hash       = user_hash,
        crime_name      = crime_name,
        area_location   = area_location,
        filename        = filename,
        UserId          = UserId,
        inserted_count  = inserted_count,
        duplicate_count = duplicate_count,
        updated_count   = updated_count,
        skipped_count   = skipped_total,
        cdr_db          = cdr_db,
        tower_db        = tower_db,
    )

    return {
        "inserted":   inserted_count,
        "duplicates": duplicate_count,
        "updated":    updated_count,
        "skipped":    skipped_total,
        "seqdata":    final_nexus,
    }