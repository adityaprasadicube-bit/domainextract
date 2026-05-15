"""
ipdr_mapping.py

BUG FIXES IN THIS VERSION
──────────────────────────
  Fix 1 — 478-second hang ($sort on 3cr records, no covering index)
    fetch_ipdr_page_pipeline() now uses find().sort("_id", 1) — the default
    clustered index, always O(1). $sort removed from scan_keys_pipeline too.

  Fix 2 — TypeError: unhashable type: 'list'
    IPDRRecord.seq_id is stored as a LIST ["e2addf26…"] in MongoDB.
    dict.get(list) raises TypeError. _normalise_seq_id() converts it to a
    plain string at every read point (page fetch, raw_page, key scan).

FETCH STRATEGY
──────────────
  IPDRRecord  → pymongo find().sort("_id") — no $sort on SDateTime
  IPDRNexus   → MongoDB aggregation pipeline
  All others  → Django ORM models
"""

import json
import os
import time
import threading

from mongoengine import get_db
from django.conf import settings

from .ipdr_report_gen import (
    build_raw_mapping,
    apply_enrichment_to_record,
    strip_internal_fields,
    _now,
    _elapsed,
)
from ...SuspectDetails.sdr_info import SuspectDetails
from ...ipdr.ip_serializers import PortInfoSerializer
from ...ipdr.ipdr_models.ip_model import IPDRNexus, IPDRRecord, PortInfo
from ...models import CellTower, MccMnc, ImeiDetails, CrimeInformation
from ...searchengine import search_ip
from ...serializers import (
    CellTowerSerializer,
    MccMncSerializer,
    DeviceInfoSerializer,
    CrimeInformationSerializer,
)

_sdr_columns_path = os.path.join(settings.BASE_DIR, "api", "data", "column_config.json")


def _load_sdr_columns():
    try:
        with open(_sdr_columns_path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _extract_mccmnc(tower_id):
    if not tower_id:
        return None
    s = str(tower_id)
    mccmnc = s[:6] if len(s) > 5 else s[:5]
    if not mccmnc.isdigit():
        return None
    if len(mccmnc) == 6 and int(mccmnc) < 405750 and not (405025 <= int(mccmnc) <= 405047):
        mccmnc = mccmnc[:5]
    return mccmnc


def _normalise_seq_id(raw) -> str:
    """
    Convert any seq_id shape to a plain hashable string.

    IPDRRecord.seq_id is stored as a LIST in MongoDB: ["e2addf26e621086a"]
    Passing a list to dict.get() raises: TypeError: unhashable type: 'list'
    This helper is called at every point where seq_id is read from a raw doc.
    """
    if isinstance(raw, list):
        return raw[0] if raw else ""
    return str(raw) if raw is not None else ""


# ─────────────────────────────────────────────────────────────────────────────
# MongoDB collection helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_ipdr_collection():
    db = get_db("ipdr_db")
    return db[IPDRRecord._get_collection_name()]


def _get_nexus_collection():
    db = get_db("ipdr_db")
    return db[IPDRNexus._get_collection_name()]


# ─────────────────────────────────────────────────────────────────────────────
# Projections
# ─────────────────────────────────────────────────────────────────────────────

_IPDR_PROJECTION = {
    "seq_id": 1, "SDateTime": 1, "EDateTime": 1, "Duration": 1,
    "MSISDN": 1, "IMEI": 1, "IMEI_TAC": 1, "IMSI": 1, "IMSI_CODE": 1,
    "TowerID": 1, "Destination_ip": 1, "Destination_port": 1,
    "Source_ip": 1, "Source_port": 1, "Translated_ip": 1, "Translated_port": 1,
    "DataUpload": 1, "DataDownload": 1,
    "Contact No": 1, "Name of Person/Organization": 1,
}

_KEY_PROJECTION = {
    "seq_id": 1, "MSISDN": 1, "Destination_ip": 1,
    "Destination_port": 1, "TowerID": 1, "IMEI_TAC": 1, "IMSI_CODE": 1,
}


# ─────────────────────────────────────────────────────────────────────────────
# 1. FAST PATH — raw page mapping (zero external calls)
# ─────────────────────────────────────────────────────────────────────────────

def ipdr_raw_page(ip_data: list, nexus_map: dict, crime_map: dict) -> list:
    """
    Map a page of raw DB records to the output shape.
    Zero external lookups — purely in-memory field assignment.

    nexus_map : {seq_id_str -> nexus_doc_dict}
    crime_map : {seq_id_str -> {'Crime': ..., 'AreaLocation': ...}}
    """
    t0 = time.monotonic()
    print(f"[{_now()}] ▶ ipdr_raw_page() — mapping {len(ip_data)} records")

    fallback_nexus = next(iter(nexus_map.values()), {}) if nexus_map else {}
    fallback_crime = {"Crime": "Unknown", "AreaLocation": "Unknown"}

    records = []
    for ipdr in ip_data:
        # FIX 2: seq_id is stored as a list — normalise to string before dict lookup
        seq_id     = _normalise_seq_id(ipdr.get("seq_id"))
        ns         = nexus_map.get(seq_id, fallback_nexus)
        crime_info = crime_map.get(seq_id, fallback_crime)

        ipdr_type  = ns.get("RecordType", "")
        ipdr_value = (
            ns.get("IPDR")
            if ipdr_type in {
                "Mobile", "IMEI", "Public IP", "Tower",
                "Destination IP", "Source IP",
                "Public Port", "Source Port", "Destination Port",
            }
            else "Unknown"
        )

        if ipdr.get("TowerID"):
            mccmnc = _extract_mccmnc(ipdr["TowerID"])
            if mccmnc:
                ipdr["RoamCode"] = mccmnc
        elif ipdr_type == "Tower":
            ipdr["TowerID"] = ipdr_value
            mccmnc = _extract_mccmnc(ipdr_value)
            if mccmnc:
                ipdr["RoamCode"] = mccmnc

        if ipdr_type == "Destination IP" and not ipdr.get("Destination_ip"):
            ipdr["Destination_ip"] = ipdr_value
        if ipdr_type == "Destination Port" and not ipdr.get("Destination_port"):
            ipdr["Destination_port"] = ipdr_value
        if ipdr_type == "IMEI" and not ipdr.get("IMEI_TAC"):
            ipdr["IMEI"]     = ipdr_value
            ipdr["IMEI_TAC"] = ipdr_value[:8]

        records.append(build_raw_mapping(ipdr, crime_info, ipdr_value, ipdr_type))

    print(f"[{_now()}] ✅ ipdr_raw_page() done — {len(records)} records in {_elapsed(t0)}")
    return records


# ─────────────────────────────────────────────────────────────────────────────
# 2. MongoDB fetch helpers
# ─────────────────────────────────────────────────────────────────────────────

def fetch_nexus_map_pipeline(seq_ids: list) -> dict:
    """
    Fetch IPDRNexus documents for the given seq_ids via aggregation pipeline.
    Returns: {seq_id_str -> nexus_doc_dict}

    IPDRNexus._id is the seq_id stored as a plain string (e.g. "e2addf26e621086a").
    We match {"_id": {"$in": seq_ids}} and also try the first element of each
    seq_id if it happens to be a list, so the function works regardless of
    how the caller constructs seq_ids.
    """
    t0  = time.monotonic()
    col = _get_nexus_collection()

    # Normalise: seq_ids may arrive as plain strings or single-element lists
    normalised = []
    for s in seq_ids:
        if isinstance(s, list):
            normalised.extend(s)
        else:
            normalised.append(str(s))
    normalised = list(set(normalised))  # deduplicate

    print(f"[{_now()}]   fetch_nexus_map_pipeline — querying {len(normalised)} seq_ids: {normalised[:3]}…")

    pipeline = [
        {"$match": {"_id": {"$in": normalised}}},
        {"$project": {
            "_id": 1, "IPDR": 1, "RecordType": 1, "CrimeID": 1,
            "UserAccessID": 1, "Tac_Or_Mobile_Code": 1,
        }},
    ]

    nexus_map = {}
    for doc in col.aggregate(pipeline):
        doc_id = doc.pop("_id", None)
        nexus_map[_normalise_seq_id(doc_id)] = doc

    print(
        f"[{_now()}]   Nexus pipeline fetch: {len(nexus_map)} docs "
        f"for {len(normalised)} seq_ids in {_elapsed(t0)}"
    )

    # ── Debug: if nothing found, sample the collection so we can see actual _id format ──
    if not nexus_map:
        print(f"[{_now()}]   ⚠️  NEXUS EMPTY — sampling collection to diagnose _id format")
        for sample in col.find({}, {"_id": 1}).limit(3):
            print(f"[{_now()}]   SAMPLE _id: type={type(sample['_id']).__name__} val={repr(sample['_id'])}")

    return nexus_map


def fetch_ipdr_page_pipeline(
    collection,
    mongo_filter: dict,
    last_id,
    limit: int,
) -> tuple:
    """
    Cursor-based page fetch — NO skip(), NO $sort on SDateTime.

    WHY NOT $sort:{SDateTime, _id}
    ───────────────────────────────
    Without a compound index covering {seq_id, SDateTime, _id}, MongoDB must
    load and sort the ENTIRE matching dataset in memory before $limit fires.
    On 3cr records this causes the 478-second hang. allowDiskUse makes it
    worse — gigabytes spill to disk.

    FIX: sort only by _id (the default clustered index). find().sort("_id")
    uses the index directly — $limit fires after reading exactly `limit` docs.
    Every page is equally fast regardless of page number.

    Returns (list_of_plain_dicts, new_last_id).
    """
    t0 = time.monotonic()

    cursor_filter = {**mongo_filter, "_id": {"$gt": last_id}} if last_id else mongo_filter

    cursor = (
        collection
        .find(cursor_filter, {**_IPDR_PROJECTION, "_id": 1})
        .sort("_id", 1)   # _id index — O(1), no sort stage
        .limit(limit)
    )
    docs = list(cursor)

    new_last_id = docs[-1].pop("_id", None) if docs else None

    for doc in docs:
        doc.pop("_id", None)
        # FIX 2: normalise seq_id list → string in every fetched document
        doc["seq_id"] = _normalise_seq_id(doc.get("seq_id"))
        for field in ("SDateTime", "EDateTime"):
            val = doc.get(field)
            if val and not isinstance(val, str):
                doc[field] = val.isoformat()

    print(
        f"[{_now()}]   IPDR page fetch: {len(docs)} docs | "
        f"cursor={'None' if not last_id else str(last_id)[:8]+'...'} | {_elapsed(t0)}"
    )
    return docs, new_last_id


def scan_keys_pipeline(collection, mongo_filter: dict, chunk_size: int = 50_000) -> dict:
    """
    Stream through ALL matching records to collect unique lookup keys.

    No $sort — order doesn't matter for key collection, and sorting 3cr
    records without a covering index causes a multi-minute disk spill.
    """
    t0      = time.monotonic()
    scanned = 0

    keys = {
        "msisdn": set(), "dest_ip": set(), "dest_port": set(),
        "cell_id": set(), "tac": set(), "imsi": set(), "roam": set(),
    }

    pipeline = [
        {"$match": mongo_filter},
        # No $sort here — not needed for key collection
        {"$project": _KEY_PROJECTION},
    ]

    for doc in collection.aggregate(pipeline, allowDiskUse=False, batchSize=chunk_size):
        msisdn = doc.get("MSISDN")
        if msisdn:
            keys["msisdn"].add(str(msisdn))

        dip = doc.get("Destination_ip")
        if dip:
            keys["dest_ip"].add(dip)

        dp = doc.get("Destination_port")
        if dp:
            keys["dest_port"].add(str(dp))

        tower = doc.get("TowerID")
        if tower:
            keys["cell_id"].add(str(tower).upper())
            mccmnc = _extract_mccmnc(tower)
            if mccmnc:
                keys["roam"].add(mccmnc)

        tac = doc.get("IMEI_TAC")
        if tac:
            keys["tac"].add(str(tac))

        imsi = doc.get("IMSI_CODE")
        if imsi:
            keys["imsi"].add(str(imsi))

        scanned += 1
        if scanned % chunk_size == 0:
            print(
                f"[{_now()}] [BG]   Key-scan: {scanned} scanned "
                f"| msisdn={len(keys['msisdn'])} ips={len(keys['dest_ip'])} "
                f"towers={len(keys['cell_id'])} | {_elapsed(t0)}"
            )

    print(
        f"[{_now()}] [BG] ✅ Key-scan complete — {scanned} docs in {_elapsed(t0)}\n"
        f"[{_now()}] [BG]    msisdn={len(keys['msisdn'])} ips={len(keys['dest_ip'])} "
        f"ports={len(keys['dest_port'])} towers={len(keys['cell_id'])} "
        f"tac={len(keys['tac'])} imsi={len(keys['imsi'])} roam={len(keys['roam'])}"
    )
    return keys


# ─────────────────────────────────────────────────────────────────────────────
# Count helpers — fully non-blocking (0 ms on request thread)
# ─────────────────────────────────────────────────────────────────────────────

def get_count_fast(collection, mongo_filter: dict, cache_key: str, cache_obj) -> dict:
    """
    Tier 1 — cached:    return instantly (<1 ms).
    Tier 2 — no cache:  spawn bg thread, return -1 now (0 ms).
    """
    COUNT_CACHE_KEY = f"{cache_key}_count"
    COUNT_EXACT_KEY = f"{cache_key}_count_exact"
    t0 = time.monotonic()

    cached_count = _cache_get(cache_obj, COUNT_CACHE_KEY)
    if cached_count is not None:
        is_exact = bool(_cache_get(cache_obj, COUNT_EXACT_KEY))
        print(f"[{_now()}]   Count tier-1 (cached, exact={is_exact}): {cached_count} in {_elapsed(t0)}")
        return {"count": cached_count, "count_ready": True, "count_exact": is_exact}

    _start_bg_exact_count(collection, mongo_filter, cache_key, cache_obj)
    print(f"[{_now()}]   Count tier-2 (bg spawned, returning -1) in {_elapsed(t0)}")
    return {"count": -1, "count_ready": False, "count_exact": False}


def _start_bg_exact_count(collection, mongo_filter: dict, cache_key: str, cache_obj):
    COUNT_CACHE_KEY = f"{cache_key}_count"
    COUNT_EXACT_KEY = f"{cache_key}_count_exact"
    BG_COUNT_KEY    = f"{cache_key}_count_bg_running"

    if _cache_get(cache_obj, BG_COUNT_KEY):
        return
    _cache_set(cache_obj, BG_COUNT_KEY, True, timeout=600)

    def _worker():
        t0 = time.monotonic()
        print(f"[{_now()}] [COUNT-BG] Starting exact count...")
        try:
            result = list(collection.aggregate(
                [{"$match": mongo_filter}, {"$count": "total"}],
                allowDiskUse=True,
            ))
            exact = result[0]["total"] if result else 0
            _cache_set(cache_obj, COUNT_CACHE_KEY, exact, timeout=7200)
            _cache_set(cache_obj, COUNT_EXACT_KEY, True,  timeout=7200)
            print(f"[{_now()}] [COUNT-BG] ✅ Exact count={exact} in {_elapsed(t0)}")
        except Exception as e:
            print(f"[{_now()}] [COUNT-BG] ❌ Failed: {e}")
        finally:
            _cache_set(cache_obj, BG_COUNT_KEY, False, timeout=10)

    threading.Thread(target=_worker, daemon=True).start()


def _cache_get(cache_obj, key):
    try:    return cache_obj.get(key)
    except: return None


def _cache_set(cache_obj, key, value, timeout=7200):
    try:    cache_obj.set(key, value, timeout=timeout)
    except: pass


# ─────────────────────────────────────────────────────────────────────────────
# 3. BACKGROUND — build enrichment lookup tables (ALL external calls here)
# ─────────────────────────────────────────────────────────────────────────────

def build_enrichment_lookups(keys: dict, nexus_map: dict, include_sdr: bool) -> dict:
    t_total = time.monotonic()
    print(f"[{_now()}] ▶ build_enrichment_lookups() — building lookup tables")

    msisdn_set    = set(keys.get("msisdn",    set()))
    dest_ip_set   = set(keys.get("dest_ip",   set()))
    dest_port_set = set(keys.get("dest_port", set()))
    cell_id_set   = set(keys.get("cell_id",   set()))
    tac_set       = set(keys.get("tac",       set()))
    imsi_set      = set(keys.get("imsi",      set()))
    roam_set      = set(keys.get("roam",      set()))

    for ns in nexus_map.values():
        rtype    = ns.get("RecordType", "")
        ipdr_val = ns.get("IPDR", "") or ""
        if rtype == "Mobile" and include_sdr and ipdr_val:
            msisdn_set.add(str(ipdr_val))
        elif rtype == "Tower" and ipdr_val:
            m = _extract_mccmnc(ipdr_val)
            if m: roam_set.add(m)
        elif rtype == "IMEI" and ipdr_val:
            tac_set.add(ipdr_val[:8])
        elif rtype == "Destination IP" and ipdr_val:
            dest_ip_set.add(ipdr_val)
        elif rtype == "Destination Port" and ipdr_val:
            dest_port_set.add(ipdr_val)

    lookups = {
        "msisdn": {}, "dest_ip": {}, "dest_port": {},
        "tower":  {}, "imei":    {}, "imsi":      {}, "roam": {},
    }

    if include_sdr and msisdn_set:
        t0 = time.monotonic()
        print(f"[{_now()}]   SDR lookup — {len(msisdn_set)} MSISDNs...")
        msisdn_ints = [int(m) for m in msisdn_set if str(m).isdigit()]
        if msisdn_ints:
            try:
                info = SuspectDetails(msisdn_ints).fetch_all_mapped_details()
                lookups["msisdn"] = info
                print(f"[{_now()}]   ✅ SDR done — {len(info)} records in {_elapsed(t0)}")
            except Exception as e:
                print(f"[{_now()}]   ❌ SDR FAILED: {e}")
        else:
            print(f"[{_now()}]   ⏭  SDR skipped — no numeric MSISDNs")
    else:
        print(f"[{_now()}]   ⏭  SDR skipped ({'disabled' if not include_sdr else 'no MSISDNs'})")

    if dest_ip_set:
        t0 = time.monotonic()
        print(f"[{_now()}]   IP lookup — {len(dest_ip_set)} IPs...")
        try:
            results = search_ip(list(dest_ip_set))
            if results and isinstance(results.get("results"), list):
                for r in results["results"]:
                    if isinstance(r, dict) and r.get("ip") in dest_ip_set:
                        lookups["dest_ip"][r["ip"]] = r
                print(f"[{_now()}]   ✅ IP done — {len(lookups['dest_ip'])}/{len(dest_ip_set)} in {_elapsed(t0)}")
            else:
                print(f"[{_now()}]   ⚠️  IP lookup no results in {_elapsed(t0)}")
        except Exception as e:
            print(f"[{_now()}]   ❌ IP FAILED: {e}")
    else:
        print(f"[{_now()}]   ⏭  IP lookup skipped")

    if dest_port_set:
        t0 = time.monotonic()
        print(f"[{_now()}]   Port lookup — {len(dest_port_set)} ports...")
        try:
            qs = PortInfo.objects.filter(id__in=dest_port_set)
            lookups["dest_port"] = {i["id"]: i for i in PortInfoSerializer(qs, many=True).data}
            print(f"[{_now()}]   ✅ Port done — {len(lookups['dest_port'])} in {_elapsed(t0)}")
        except Exception as e:
            print(f"[{_now()}]   ❌ Port FAILED: {e}")
    else:
        print(f"[{_now()}]   ⏭  Port lookup skipped")

    if cell_id_set:
        t0 = time.monotonic()
        print(f"[{_now()}]   Tower lookup — {len(cell_id_set)} tower IDs...")
        try:
            qs = CellTower.objects.filter(id__in=cell_id_set)
            lookups["tower"] = {i["id"]: i for i in CellTowerSerializer(qs, many=True).data}
            print(f"[{_now()}]   ✅ Tower done — {len(lookups['tower'])} in {_elapsed(t0)}")
        except Exception as e:
            print(f"[{_now()}]   ❌ Tower FAILED: {e}")
    else:
        print(f"[{_now()}]   ⏭  Tower lookup skipped")

    if tac_set:
        t0 = time.monotonic()
        print(f"[{_now()}]   IMEI lookup — {len(tac_set)} TACs...")
        try:
            qs = ImeiDetails.objects.filter(id__in=tac_set)
            lookups["imei"] = {i["id"]: i for i in DeviceInfoSerializer(qs, many=True).data}
            print(f"[{_now()}]   ✅ IMEI done — {len(lookups['imei'])} in {_elapsed(t0)}")
        except Exception as e:
            print(f"[{_now()}]   ❌ IMEI FAILED: {e}")
    else:
        print(f"[{_now()}]   ⏭  IMEI lookup skipped")

    if imsi_set:
        t0 = time.monotonic()
        print(f"[{_now()}]   IMSI lookup — {len(imsi_set)} codes...")
        try:
            qs = MccMnc.objects.filter(mccmnc_temp__in=imsi_set)
            lookups["imsi"] = {i["mccmnc_temp"]: i for i in MccMncSerializer(qs, many=True).data}
            print(f"[{_now()}]   ✅ IMSI done — {len(lookups['imsi'])} in {_elapsed(t0)}")
        except Exception as e:
            print(f"[{_now()}]   ❌ IMSI FAILED: {e}")
    else:
        print(f"[{_now()}]   ⏭  IMSI lookup skipped")

    if roam_set:
        t0 = time.monotonic()
        print(f"[{_now()}]   Roam lookup — {len(roam_set)} codes...")
        try:
            qs = MccMnc.objects.filter(mccmnc_temp__in=roam_set)
            lookups["roam"] = {i["mccmnc_temp"]: i for i in MccMncSerializer(qs, many=True).data}
            print(f"[{_now()}]   ✅ Roam done — {len(lookups['roam'])} in {_elapsed(t0)}")
        except Exception as e:
            print(f"[{_now()}]   ❌ Roam FAILED: {e}")
    else:
        print(f"[{_now()}]   ⏭  Roam lookup skipped")

    print(f"[{_now()}] ✅ build_enrichment_lookups() complete — total {_elapsed(t_total)}")
    return lookups


# ─────────────────────────────────────────────────────────────────────────────
# 3b. PAGE-LEVEL inline key extraction — used for immediate first-page enrichment
# ─────────────────────────────────────────────────────────────────────────────

def extract_page_keys(raw_records: list) -> dict:
    """
    Extract unique lookup keys from a single page of raw-mapped records
    (records that still have their _* internal fields).

    This is called on the ~500 records of the current page so we can
    enrich them immediately without waiting for the full background scan
    of 3cr records.  Runs in microseconds on 500 records.

    Returns the same key-dict shape as scan_keys_pipeline().
    """
    keys = {
        "msisdn": set(), "dest_ip": set(), "dest_port": set(),
        "cell_id": set(), "tac": set(), "imsi": set(), "roam": set(),
    }
    for r in raw_records:
        if r.get("_msisdn_raw"):
            keys["msisdn"].add(str(r["_msisdn_raw"]))
        if r.get("_dest_ip_raw"):
            keys["dest_ip"].add(r["_dest_ip_raw"])
        if r.get("_dest_port_raw"):
            keys["dest_port"].add(str(r["_dest_port_raw"]))
        if r.get("_tower_id_raw"):
            keys["cell_id"].add(str(r["_tower_id_raw"]).upper())
            mccmnc = _extract_mccmnc(r["_tower_id_raw"])
            if mccmnc:
                keys["roam"].add(mccmnc)
        if r.get("_imei_tac_raw"):
            keys["tac"].add(str(r["_imei_tac_raw"]))
        if r.get("_imsi_code_raw"):
            keys["imsi"].add(str(r["_imsi_code_raw"]))
        if r.get("_roam_code_raw"):
            keys["roam"].add(str(r["_roam_code_raw"]))
    return keys


# ─────────────────────────────────────────────────────────────────────────────
# 4. Apply lookups (pure in-memory)
# ─────────────────────────────────────────────────────────────────────────────

def enrich_records(raw_records: list, lookups: dict) -> list:
    t0 = time.monotonic()
    print(f"[{_now()}] ▶ enrich_records() — {len(raw_records)} records")
    result = [
        strip_internal_fields(apply_enrichment_to_record(dict(r), lookups))
        for r in raw_records
    ]
    print(f"[{_now()}] ✅ enrich_records() done in {_elapsed(t0)}")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 5. Crime map — single bulk ORM query
# ─────────────────────────────────────────────────────────────────────────────

def build_crime_map_pipeline(nexus_map: dict) -> dict:
    t0 = time.monotonic()
    print(f"[{_now()}] ▶ build_crime_map_pipeline() — {len(nexus_map)} nexus records")

    crime_id_to_seq = {}
    for seq_id, ns in nexus_map.items():
        cid = ns.get("CrimeID")
        if cid:
            crime_id_to_seq.setdefault(cid, []).append(seq_id)

    crime_map = {}
    fallback  = {"Crime": "Unknown", "AreaLocation": "Unknown"}

    if crime_id_to_seq:
        try:
            qs         = CrimeInformation.objects.filter(id__in=list(crime_id_to_seq.keys()))
            crime_data = {
                item["id"]: item
                for item in CrimeInformationSerializer(qs, many=True).data
            }
            for cid, seq_ids in crime_id_to_seq.items():
                info = crime_data.get(cid, fallback)
                for sid in seq_ids:
                    crime_map[sid] = info
        except Exception as e:
            print(f"[{_now()}]   ❌ CrimeInformation bulk fetch failed: {e}")

    for seq_id in nexus_map:
        crime_map.setdefault(seq_id, fallback)

    print(f"[{_now()}] ✅ build_crime_map_pipeline() done — {len(crime_map)} entries in {_elapsed(t0)}")
    return crime_map


def build_crime_map(nexus_serializers: list) -> dict:
    """Backwards-compat shim."""
    nexus_map = {ns.data.get("id"): dict(ns.data) for ns in nexus_serializers}
    return build_crime_map_pipeline(nexus_map)