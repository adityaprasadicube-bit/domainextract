"""
ipdr_pipeline.py
════════════════
Pure MongoDB aggregation pipeline — replaces IPDRRecord model + IPDRRecordSerializer
for all read operations on IPDetailRecords.

WHY THIS IS FASTER
──────────────────
Old approach:
  1. MongoEngine model hydrates every document into a Python object
  2. DRF Serializer converts every Python object field-by-field
  3. For 500 records × 40 fields = 20,000+ Python operations per page

New approach:
  1. MongoDB server does the field selection, sorting, and pagination natively
  2. pymongo returns plain Python dicts — no ORM, no serializer
  3. For 500 records = 500 dict lookups, that's it

WHAT THE PIPELINE DOES
───────────────────────
Stage 1  $match         — filter by seq_id (uses index), optional date/duration filters
Stage 2  $sort          — sort by SDateTime + _id (required for stable cursor pagination)
Stage 3  $match         — cursor gate: _id > last_id  (this is what replaces skip())
Stage 4  $limit         — take only this page's records
Stage 5  $project       — return only the 15 fields we actually use (reduces network I/O)
Stage 6  $addFields     — convert datetime objects to ISO strings inside MongoDB
                          (avoids doing this conversion in Python per-record)

COLLECTION NAMES
─────────────────
  IPDetailRecords  → ipdr_record  (IPDRRecord MongoEngine collection)
  IPdrNexus        → ipdr_nexus   (IPDRNexus  MongoEngine collection)

KEY INDEXES REQUIRED (add if missing)
──────────────────────────────────────
  db.ipdr_record.createIndex({ seq_id: 1, SDateTime: 1, _id: 1 })

  Without this index, $match on seq_id will do a full collection scan.
  With it, MongoDB jumps directly to the matching documents.
"""

import time
from datetime import datetime
from bson import ObjectId
from mongoengine import get_db

from ..ipdr_workspace.ipdr_report_gen import _now, _elapsed

# ── Collection name constants ─────────────────────────────────────────────────
# These must match the actual MongoDB collection names.
# Check with: db.getCollectionNames() in mongosh
IPDR_RECORD_COLLECTION = "ip_detail_records"  # IPDetailRecords
IPDR_NEXUS_COLLECTION = "ipdr_nexus"  # IPdrNexus

# ── Field projection — only fetch what we use ─────────────────────────────────
# Excluding large/unused fields cuts network transfer significantly at 3cr records.
_RECORD_FIELDS = [
    "seq_id", "SDateTime", "EDateTime", "Duration",
    "MSISDN", "IMEI", "IMEI_TAC", "IMSI", "IMSI_CODE",
    "TowerID", "RoamCode",
    "Destination_ip", "Destination_port",
    "Source_ip", "Source_port",
    "Translated_ip", "Translated_port",
    "DataUpload", "DataDownload",
    "Contact No", "Name of Person/Organization",
]

_NEXUS_FIELDS = [
    "id", "IPDR", "RecordType", "CrimeID",
]


def _get_db():
    """Return raw pymongo database instance."""
    return get_db()


def _get_record_collection():
    return _get_db()[IPDR_RECORD_COLLECTION]


def _get_nexus_collection():
    return _get_db()[IPDR_NEXUS_COLLECTION]


# ─────────────────────────────────────────────────────────────────────────────
# Nexus pipeline — fetch nexus records by seq_ids
# ─────────────────────────────────────────────────────────────────────────────

def fetch_nexus_by_ids(seq_ids: list) -> list:
    """
    Fetch IPdrNexus records by id list using raw aggregation pipeline.
    Returns list of plain dicts with fields: id, IPDR, RecordType, CrimeID.

    Replaces:
        IPDRNexus.objects.filter(id__in=seq_ids)
        + IPDRNexusSerializer(nexus, many=True)
    """
    t0 = time.monotonic()
    col = _get_nexus_collection()

    pipeline = [
        {"$match": {"_id": {"$in": [ObjectId(s) if not isinstance(s, ObjectId) else s for s in seq_ids]}}},
        {"$project": {
            "_id": 0,
            "id": {"$toString": "$_id"},
            "IPDR": 1,
            "RecordType": 1,
            "CrimeID": {"$toString": "$CrimeID"},
        }},
    ]

    results = list(col.aggregate(pipeline))
    print(f"[{_now()}]   Nexus pipeline: {len(results)} records in {_elapsed(t0)}")
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Count pipeline — fast count using index
# ─────────────────────────────────────────────────────────────────────────────

def count_ipdr_records(seq_ids: list, from_date=None, to_date=None,
                       min_duration=None, max_duration=None) -> int:
    """
    Count matching IPDetailRecords using MongoDB countDocuments with index hint.

    Replaces:
        IPDRRecord.objects.filter(...).count()

    At 3cr records, MongoEngine .count() does a full scan (~60s).
    This uses the seq_id index and returns in milliseconds.
    """
    t0 = time.monotonic()
    col = _get_record_collection()

    mongo_filter = _build_filter(seq_ids, from_date, to_date, min_duration, max_duration)

    try:
        # hint value must match the index name in MongoDB
        # If your index is named differently, remove the hint= parameter
        count = col.count_documents(mongo_filter, hint="seq_id_1_SDateTime_1__id_1")
    except Exception:
        # hint failed (index may not exist or have different name) — fall back
        count = col.count_documents(mongo_filter)

    print(f"[{_now()}]   Count pipeline: {count} records in {_elapsed(t0)}")
    return count


# ─────────────────────────────────────────────────────────────────────────────
# Page pipeline — cursor-based pagination (no skip)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_ipdr_page(seq_ids: list, limit: int, last_id=None,
                    from_date=None, to_date=None,
                    min_duration=None, max_duration=None) -> tuple:
    """
    Fetch one page of IPDetailRecords using cursor-based pagination.

    WHY NOT skip()
    ──────────────
    MongoDB skip(N) reads and discards N documents. At page 60,000 with limit=500,
    skip = 29,999,500 — MongoDB reads 30 million docs and throws them away.
    Page response time grows linearly with page number, becoming unusable.

    CURSOR-BASED PAGINATION
    ────────────────────────
    We sort by (SDateTime, _id) and use _id > last_id as the cursor gate.
    MongoDB uses the compound index to jump directly to the cursor position.
    Every page takes the same time regardless of page number.

    HOW TO USE
    ───────────
    First page:   last_id = None
    Next pages:   last_id = the 'last_id' value returned by this function

    Returns:
        (list_of_dicts, new_last_id_str | None)

    Replaces:
        list(qs[start:end])
        + IPDRRecordSerializer(chunk, many=True).data
    """
    t0 = time.monotonic()
    col = _get_record_collection()

    base_filter = _build_filter(seq_ids, from_date, to_date, min_duration, max_duration)

    # Cursor gate: only documents after the last seen _id
    if last_id:
        cursor_filter = {
            **base_filter,
            "_id": {"$gt": ObjectId(last_id) if isinstance(last_id, str) else last_id}
        }
    else:
        cursor_filter = base_filter

    pipeline = [
        # Stage 1: filter (hits index on seq_id + SDateTime + _id)
        {"$match": cursor_filter},

        # Stage 2: stable sort — required for cursor pagination to work correctly
        {"$sort": {"SDateTime": 1, "_id": 1}},

        # Stage 3: take only this page
        {"$limit": limit},

        # Stage 4: project only the fields we need — reduces document size
        {"$project": _build_projection()},

        # Stage 5: convert datetime fields to ISO strings inside MongoDB
        # This avoids doing datetime.isoformat() per-record in Python
        {"$addFields": {
            "SDateTime": {
                "$cond": {
                    "if": {"$ifNull": ["$SDateTime", False]},
                    "then": {"$dateToString": {"format": "%Y-%m-%dT%H:%M:%S", "date": "$SDateTime"}},
                    "else": None,
                }
            },
            "EDateTime": {
                "$cond": {
                    "if": {"$ifNull": ["$EDateTime", False]},
                    "then": {"$dateToString": {"format": "%Y-%m-%dT%H:%M:%S", "date": "$EDateTime"}},
                    "else": None,
                }
            },
            # Convert _id to string cursor for the client
            "_cursor_id": {"$toString": "$_id"},
        }},

        # Stage 6: remove the ObjectId _id from the output
        {"$unset": "_id"},
    ]

    docs = list(col.aggregate(pipeline, allowDiskUse=True))

    # Extract the cursor for the next page from the last document
    new_last_id = docs[-1].get("_cursor_id") if docs else None

    # Clean up the helper field from each doc
    for d in docs:
        d.pop("_cursor_id", None)

    print(
        f"[{_now()}]   Page pipeline: {len(docs)} docs | "
        f"cursor={'None' if not last_id else str(last_id)[:8] + '...'} | "
        f"{_elapsed(t0)}"
    )
    return docs, new_last_id


# ─────────────────────────────────────────────────────────────────────────────
# Key-scan pipeline — scan all records to collect unique lookup keys
# ─────────────────────────────────────────────────────────────────────────────

def scan_unique_keys(seq_ids: list, from_date=None, to_date=None,
                     min_duration=None, max_duration=None) -> dict:
    """
    Scan all matching records and return sets of unique lookup keys.

    Uses a MongoDB $group aggregation to collect unique values server-side —
    far more efficient than streaming 3cr documents to Python and doing set.add().

    MongoDB does the deduplication, Python only receives the small result set.

    Returns dict with keys:
        msisdn_set, dest_ip_set, dest_port_set, cell_id_set,
        tac_set, imsi_set, roam_set

    Replaces:
        The entire for-loop key collection in _start_background_key_scan()
    """
    t0 = time.monotonic()
    col = _get_record_collection()

    mongo_filter = _build_filter(seq_ids, from_date, to_date, min_duration, max_duration)

    # Minimal projection — only fields needed to build lookup keys
    pipeline = [
        {"$match": mongo_filter},
        {"$project": {
            "_id": 0,
            "seq_id": 1,
            "MSISDN": 1,
            "Destination_ip": 1,
            "Destination_port": 1,
            "TowerID": 1,
            "IMEI_TAC": 1,
            "IMSI_CODE": 1,
            "RoamCode": 1,
        }},
        # Group all documents into a single result, collecting unique values
        {"$group": {
            "_id": None,
            "msisdn_set": {"$addToSet": "$MSISDN"},
            "dest_ip_set": {"$addToSet": "$Destination_ip"},
            "dest_port_set": {"$addToSet": "$Destination_port"},
            "cell_id_set": {"$addToSet": "$TowerID"},
            "tac_set": {"$addToSet": "$IMEI_TAC"},
            "imsi_set": {"$addToSet": "$IMSI_CODE"},
            "roam_set": {"$addToSet": "$RoamCode"},
            "seq_id_set": {"$addToSet": "$seq_id"},
        }},
    ]

    results = list(col.aggregate(pipeline, allowDiskUse=True))

    if not results:
        empty = {k: set() for k in
                 ["msisdn_set", "dest_ip_set", "dest_port_set",
                  "cell_id_set", "tac_set", "imsi_set", "roam_set"]}
        print(f"[{_now()}]   Key scan pipeline: no records in {_elapsed(t0)}")
        return empty

    r = results[0]

    def _clean(vals):
        """Remove None/empty from MongoDB $addToSet results."""
        return {str(v).strip() for v in (vals or []) if v and str(v).strip()}

    keys = {
        "msisdn_set": _clean(r.get("msisdn_set")),
        "dest_ip_set": _clean(r.get("dest_ip_set")),
        "dest_port_set": _clean(r.get("dest_port_set")),
        "cell_id_set": {str(v).upper().strip() for v in (r.get("cell_id_set") or []) if v},
        "tac_set": _clean(r.get("tac_set")),
        "imsi_set": _clean(r.get("imsi_set")),
        "roam_set": _clean(r.get("roam_set")),
    }

    print(
        f"[{_now()}]   Key scan pipeline: done in {_elapsed(t0)} — "
        f"msisdn={len(keys['msisdn_set'])} ips={len(keys['dest_ip_set'])} "
        f"ports={len(keys['dest_port_set'])} towers={len(keys['cell_id_set'])} "
        f"imei={len(keys['tac_set'])} imsi={len(keys['imsi_set'])} "
        f"roam={len(keys['roam_set'])}"
    )
    return keys


# ─────────────────────────────────────────────────────────────────────────────
# Streaming pipeline — for enriched filter path (stream + enrich + match)
# ─────────────────────────────────────────────────────────────────────────────

def stream_all_records(seq_ids: list, batch_size: int = 5000,
                       from_date=None, to_date=None,
                       min_duration=None, max_duration=None):
    """
    Generator that streams all matching records in batches.
    Used in the enriched filter path where we need all records enriched.

    Yields lists of plain dicts (batch_size at a time).

    Replaces the chunk-fetch loop in _handle_enriched_filtering().
    """
    t0 = time.monotonic()
    col = _get_record_collection()

    mongo_filter = _build_filter(seq_ids, from_date, to_date, min_duration, max_duration)

    pipeline = [
        {"$match": mongo_filter},
        {"$sort": {"SDateTime": 1, "_id": 1}},
        {"$project": _build_projection()},
        {"$addFields": {
            "SDateTime": {
                "$cond": {
                    "if": {"$ifNull": ["$SDateTime", False]},
                    "then": {"$dateToString": {"format": "%Y-%m-%dT%H:%M:%S", "date": "$SDateTime"}},
                    "else": None,
                }
            },
            "EDateTime": {
                "$cond": {
                    "if": {"$ifNull": ["$EDateTime", False]},
                    "then": {"$dateToString": {"format": "%Y-%m-%dT%H:%M:%S", "date": "$EDateTime"}},
                    "else": None,
                }
            },
        }},
        {"$unset": "_id"},
    ]

    cursor = col.aggregate(pipeline, allowDiskUse=True, batchSize=batch_size)

    batch = []
    total_sent = 0
    batch_count = 0

    for doc in cursor:
        batch.append(doc)
        if len(batch) >= batch_size:
            batch_count += 1
            total_sent += len(batch)
            print(
                f"[{_now()}]   Stream batch {batch_count}: {total_sent} docs sent | "
                f"{_elapsed(t0)} elapsed"
            )
            yield batch
            batch = []

    if batch:
        total_sent += len(batch)
        print(f"[{_now()}]   Stream final batch: {total_sent} docs total | {_elapsed(t0)}")
        yield batch


# ─────────────────────────────────────────────────────────────────────────────
# Lookup pipelines — replace Django ORM lookups for reference tables
# ─────────────────────────────────────────────────────────────────────────────

def fetch_crime_info(crime_ids: list) -> dict:
    """
    Fetch CrimeInformation records by id list.
    Returns { crime_id_str: {'Crime': ..., 'AreaLocation': ...} }

    Replaces:
        CrimeInformation.objects.get(id=crime_id)
        + CrimeInformationSerializer(info).data
    """
    if not crime_ids:
        return {}

    t0 = time.monotonic()
    col = _get_db()["crime_information"]  # adjust collection name if different

    pipeline = [
        {"$match": {"_id": {"$in": [
            ObjectId(c) if isinstance(c, str) else c for c in crime_ids
        ]}}},
        {"$project": {
            "_id": 0,
            "id": {"$toString": "$_id"},
            "Crime": 1,
            "AreaLocation": 1,
        }},
    ]

    results = {r["id"]: r for r in col.aggregate(pipeline)}
    print(f"[{_now()}]   Crime pipeline: {len(results)} records in {_elapsed(t0)}")
    return results


def fetch_cell_towers(tower_ids: set) -> dict:
    """
    Returns { tower_id_upper: {ADDRESS, MAIN_CITY, SUB_CITY, Lat, Long, Azimuth} }
    Replaces: CellTower.objects.filter + CellTowerSerializer
    """
    if not tower_ids:
        return {}

    t0 = time.monotonic()
    col = _get_db()["cell_tower"]  # adjust if different

    pipeline = [
        {"$match": {"_id": {"$in": list(tower_ids)}}},
        {"$project": {
            "_id": 0,
            "id": {"$toUpper": "$_id"},
            "ADDRESS": 1,
            "MAIN_CITY": 1,
            "SUB_CITY": 1,
            "Lat": 1,
            "Long": 1,
            "Azimuth": 1,
        }},
    ]

    results = {r["id"]: r for r in col.aggregate(pipeline)}
    print(f"[{_now()}]   Tower pipeline: {len(results)} records in {_elapsed(t0)}")
    return results


def fetch_port_info(port_ids: set) -> dict:
    """
    Returns { port_id: {Description, Category, Type} }
    Replaces: PortInfo.objects.filter + PortInfoSerializer
    """
    if not port_ids:
        return {}

    t0 = time.monotonic()
    col = _get_db()["port_info"]  # adjust if different

    pipeline = [
        {"$match": {"_id": {"$in": list(port_ids)}}},
        {"$project": {
            "_id": 0,
            "id": "$_id",
            "Description": 1,
            "Category": 1,
            "Type": 1,
        }},
    ]

    results = {str(r["id"]): r for r in col.aggregate(pipeline)}
    print(f"[{_now()}]   Port pipeline: {len(results)} records in {_elapsed(t0)}")
    return results


def fetch_imei_details(tac_ids: set) -> dict:
    """
    Returns { tac_id: {manufacturer, devicetype} }
    Replaces: ImeiDetails.objects.filter + DeviceInfoSerializer
    """
    if not tac_ids:
        return {}

    t0 = time.monotonic()
    col = _get_db()["imei_details"]  # adjust if different

    pipeline = [
        {"$match": {"_id": {"$in": list(tac_ids)}}},
        {"$project": {
            "_id": 0,
            "id": "$_id",
            "manufacturer": 1,
            "devicetype": 1,
        }},
    ]

    results = {str(r["id"]): r for r in col.aggregate(pipeline)}
    print(f"[{_now()}]   IMEI pipeline: {len(results)} records in {_elapsed(t0)}")
    return results


def fetch_mccmnc(mccmnc_codes: set) -> dict:
    """
    Returns { mccmnc_temp: {circle, operator} }
    Replaces: MccMnc.objects.filter + MccMncSerializer
    Used for both IMSI and Roaming lookups.
    """
    if not mccmnc_codes:
        return {}

    t0 = time.monotonic()
    col = _get_db()["mcc_mnc"]  # adjust if different

    pipeline = [
        {"$match": {"mccmnc_temp": {"$in": list(mccmnc_codes)}}},
        {"$project": {
            "_id": 0,
            "mccmnc_temp": 1,
            "circle": 1,
            "operator": 1,
        }},
    ]

    results = {r["mccmnc_temp"]: r for r in col.aggregate(pipeline)}
    print(f"[{_now()}]   MccMnc pipeline: {len(results)} records in {_elapsed(t0)}")
    return results


def fetch_mobile_operators(op_codes: set) -> dict:
    """
    Returns { id: {Circle, Operator} }
    Replaces: MobileOperator.objects.filter + MobileOperatorSerializer
    """
    if not op_codes:
        return {}

    t0 = time.monotonic()
    col = _get_db()["mobile_operator"]  # adjust if different

    pipeline = [
        {"$match": {"_id": {"$in": list(op_codes)}}},
        {"$project": {
            "_id": 0,
            "id": "$_id",
            "Circle": 1,
            "Operator": 1,
        }},
    ]

    results = {str(r["id"]): r for r in col.aggregate(pipeline)}
    print(f"[{_now()}]   MobileOp pipeline: {len(results)} records in {_elapsed(t0)}")
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_filter(seq_ids, from_date, to_date, min_duration, max_duration) -> dict:
    """Build a pymongo filter dict from request parameters."""
    f = {"seq_id": {"$in": seq_ids}}
    if from_date and to_date:
        f["SDateTime"] = {"$gte": from_date, "$lte": to_date}
    if min_duration is not None and max_duration is not None:
        f["Duration"] = {"$gte": float(min_duration), "$lte": float(max_duration)}
    return f


def _build_projection() -> dict:
    """Build a $project stage that includes only the fields we need."""
    proj = {"_id": 1}  # keep _id for cursor pagination (removed later)
    for field in _RECORD_FIELDS:
        proj[field] = 1
    return proj