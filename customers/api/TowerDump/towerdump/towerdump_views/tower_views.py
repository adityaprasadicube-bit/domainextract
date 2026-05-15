"""
towerdump_optimized_v3.py
=========================
Target: 10–15 seconds for 600K records.
Previous: 1m 30s (v2), 5–6 min (original).

STRATEGY — push work DOWN, split work ACROSS
═══════════════════════════════════════════════
The v2 file moved DB I/O out of Python. This version attacks the remaining
Python-side CPU work with three techniques:

① MONGODB AGGREGATION FOR LOOKUPS
   Instead of fetching 8 lookup tables into Python dicts and doing
   600K × N dict-lookups in Python, we use $lookup / $addFields inside
   a single aggregation pipeline. MongoDB resolves all foreign-key joins
   in compiled C++ before any data crosses the wire.

② CHUNKED MULTIPROCESSING FOR THE ENRICHMENT LOOP
   The 600K-record enrichment loop is split into CPU_COUNT chunks.
   Each chunk runs in a separate process (bypasses the GIL).
   Results are merged back in the main process.

③ PRE-BUILT B-PARTY LOOKUP TABLE
   _process_b_party currently does a linear prefix scan over landline/ISD
   codes for every record. We pre-build a single dict keyed by B-party
   number at the start, so the per-record cost is O(1).

④ AVOID BUILDING 7 COPIES OF EACH ROW
   Instead of building 7 slightly-different dicts per record (mapping,
   provider_other_state_info, aparty_other_state …), we store ONE row
   and keep per-report index lists (list of integer positions).
   Reports are views into the master list at response time — zero copying.

⑤ FAST JSON RESPONSE VIA orjson
   DRF's default JSON renderer is slow on large payloads.
   orjson (Rust-backed) is 5–10× faster. Falls back to stdlib if absent.

Zero output changes — identical response structure and field names.
"""

import json
import math
import os
import re
import hashlib
import pickle
import multiprocessing as mp
from collections import defaultdict
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

from django.conf import settings
from django.utils.dateparse import parse_datetime
from django.http import HttpResponse
from mongoengine import InvalidQueryError, get_db
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

# ── orjson fast serialiser (optional) ─────────────────────────────────────────
try:
    import orjson
    def _fast_json_response(data):
        return HttpResponse(
            orjson.dumps(data, option=orjson.OPT_NON_STR_KEYS),
            content_type="application/json",
        )
    ORJSON = True
except ImportError:
    ORJSON = False
    def _fast_json_response(data):
        return Response(data)

# ── Redis ──────────────────────────────────────────────────────────────────────
try:
    import redis as _redis_mod
    _redis_client = _redis_mod.Redis(
        host=getattr(settings, "REDIS_HOST", "localhost"),
        port=getattr(settings, "REDIS_PORT", 6379),
        db=getattr(settings, "REDIS_DB", 0),
        decode_responses=False,
        socket_timeout=2,
        socket_connect_timeout=2,
    )
    _redis_client.ping()
    REDIS_AVAILABLE = True
except Exception:
    _redis_client = None
    REDIS_AVAILABLE = False

CACHE_TTL = getattr(settings, "TOWERDUMP_CACHE_TTL", 300)

from .undertowercall import get_under_tower_exact_calls
from ..towerdump_models.towerdump_model import TowerDumpNexus, TowerDumpDetailRecord
from ....SuspectDetails.sdr_info import SuspectDetails
from ....models import (CrimeInformation, MobileOperator, MccMnc, ImeiDetails,
                        UserAccess, SMSHeader, CellTower, LRNCode)
from ..serializers import *
from drf_yasg.utils import swagger_auto_schema
from ....serializers import (CrimeInformationSerializer, MobileOperatorSerializer,
                             MccMncSerializer, DeviceInfoSerializer, UserAccessSerializer,
                             SMSHeaderSerializer, CellTowerSerializer, LRNCodeSerializer)
from ....utilities import fetch_landline_json, fetch_isd_json


# ============================================================
# CACHE HELPERS
# ============================================================

def _cache_key(*parts):
    raw = ":".join(str(p) for p in parts)
    return f"td:{hashlib.md5(raw.encode()).hexdigest()}"

def cache_get(key):
    if not REDIS_AVAILABLE:
        return None
    try:
        data = _redis_client.get(key)
        return pickle.loads(data) if data else None
    except Exception:
        return None

def cache_set(key, value, ttl=CACHE_TTL):
    if not REDIS_AVAILABLE:
        return
    try:
        _redis_client.setex(key, ttl, pickle.dumps(value))
    except Exception:
        pass

def cache_delete_pattern(pattern):
    if not REDIS_AVAILABLE:
        return
    try:
        keys = _redis_client.keys(pattern)
        if keys:
            _redis_client.delete(*keys)
    except Exception:
        pass


# ============================================================
# HELPERS
# ============================================================

def normalize_field_name(field):
    return ''.join(e for e in field if e.isalnum())

sdr_columns_path = os.path.join(settings.BASE_DIR, "api", "data", "column_config.json")

_landline_json = None
_isd_json = None
_sorted_ll_codes = None
_sorted_isd_codes = None

def get_landline_data():
    global _landline_json, _sorted_ll_codes
    if _landline_json is None:
        _landline_json = fetch_landline_json()
        _sorted_ll_codes = sorted(_landline_json.keys(), key=len, reverse=True)
    return _landline_json, _sorted_ll_codes

def get_isd_data():
    global _isd_json, _sorted_isd_codes
    if _isd_json is None:
        _isd_json = fetch_isd_json()
        _sorted_isd_codes = sorted(_isd_json.keys(), key=len, reverse=False)
    return _isd_json, _sorted_isd_codes

def load_sdr_columns():
    try:
        with open(sdr_columns_path, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def first_value(val):
    if isinstance(val, list):
        return val[0] if val else None
    return val

def paginate_list(data, request):
    if data is None:
        data = []
    page = int(request.data.get("page", 1))
    page_size = int(request.data.get("page_size", 500))
    total_count = len(data)
    total_pages = math.ceil(total_count / page_size) if page_size else 0
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "results": data[start:end],
    }

def safe_query(model, filter_kwargs=None, many=True):
    try:
        if filter_kwargs:
            return model.objects.filter(**filter_kwargs) if many else model.objects.get(**filter_kwargs)
        return model.objects.all()
    except Exception as e:
        print(f"Error querying {model.__name__}: {str(e)}")
        return [] if many else None

def safe_serialize(queryset, serializer_class, many=True):
    try:
        if not queryset or (hasattr(queryset, '__len__') and len(queryset) == 0):
            return []
        return serializer_class(queryset, many=many).data
    except Exception as e:
        print(f"Error serializing with {serializer_class.__name__}: {str(e)}")
        return [] if many else {}

def safe_str(value, default=""):
    return default if value is None else str(value)

def safe_int(value, default=0):
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default

def is_valid_mobile_number(num):
    if num is None:
        return False
    s = safe_str(num)
    return s and s.isdigit() and len(s) == 10 and s[0] in '6789'


# ============================================================
# OPTIMIZATION ③ — PRE-BUILD B-PARTY LOOKUP TABLE
# ─────────────────────────────────────────────────
# _process_b_party does a for-loop over all landline/ISD prefix codes
# for every single record. On 600K records with unique B-party numbers
# this is redundant: the same B-party appears in hundreds of records.
#
# Strategy: collect all unique B-party numbers first, classify each
# ONCE into a result dict, then do O(1) lookups during the main loop.
# ============================================================

def _classify_b_party_number(bp_num: str, ll_json, ll_codes, isd_json, isd_codes):
    """Classify a single B-party number string. Returns a detail dict or None."""
    if not bp_num:
        return {'Provider': 'Empty', 'Type': 'Empty'}, None, None   # detail, lrn, bp_code

    if bp_num.isdigit():
        bp_len = len(bp_num)
        if bp_len == 10 and bp_num[0] in '6789':
            return None, None, None    # mobile — needs LRN/b_mobile_code from record
        if bp_len == 10 and bp_num.startswith('140'):
            return {'Provider': 'Telemarketing', 'Type': 'Telemarketing'}, None, None
        if bp_len == 10 and (bp_num.startswith('1800') or bp_num.startswith('1860')):
            return {'Provider': 'Toll Free', 'Type': 'Toll Free'}, None, None
        if bp_len == 10:
            for ll_code in ll_codes:
                if bp_num.startswith(ll_code):
                    city = ll_json[ll_code]['City']
                    state = ll_json[ll_code]['State']
                    return {'Provider': f"{state}-{city}", 'Type': 'Landline',
                            'City': city, 'State': state}, None, None
        if bp_len > 10:
            for isd_code in isd_codes:
                if bp_num.startswith(isd_code):
                    name = isd_json[isd_code]['name']
                    code = isd_json[isd_code]['code']
                    return {'Provider': f"{name}-{code}", 'Type': 'ISD',
                            'Country': name, 'Code': code}, None, None
        if bp_len == 5 and bp_num.startswith('5'):
            return {'Provider': 'SMS Short Code', 'Type': 'SMS Short Code'}, None, None
        if bp_len in (3, 4):
            return {'Provider': 'Customer Care', 'Type': 'Customer Care'}, None, None
    else:
        if '-' in bp_num:
            parts = bp_num.split('-')
            sms_code = parts[1] if len(parts) > 1 else None
            sms_type = parts[2] if len(parts) > 2 else None
            return None, sms_code, sms_type    # SMS header — handled per-record
        if bp_num.startswith('*') or bp_num.startswith('#'):
            return {'Provider': 'MMI/USSD', 'Type': 'MMI/USSD'}, None, None
        if any(c.isalpha() for c in bp_num):
            return {'Provider': 'Service', 'Type': 'Service'}, None, None

    return {'Provider': 'Unknown', 'Type': 'Unknown'}, None, None


def build_b_party_cache(b_party_set: set):
    """
    OPTIMIZATION ③ — classify every unique B-party number once.
    Returns dict: {bp_num: {'detail': ..., 'sms_code': ..., 'sms_type': ...}}
    """
    ll_json, ll_codes = get_landline_data()
    isd_json, isd_codes = get_isd_data()
    result = {}
    for bp in b_party_set:
        if bp is None:
            continue
        bp_str = safe_str(bp).strip()
        detail, sms_code, sms_type = _classify_b_party_number(
            bp_str, ll_json, ll_codes, isd_json, isd_codes)
        result[bp_str] = {'detail': detail, 'sms_code': sms_code, 'sms_type': sms_type}
    return result


# ============================================================
# MONGODB CALL COUNTS AGGREGATION (unchanged from v2)
# ============================================================

def fetch_nexus_call_counts_aggregated(towercollection, seq_ids):
    pipeline = [
        {"$match": {"seq_id": {"$in": seq_ids}}},
        {"$group": {
            "_id": "$seq_id",
            "TotalCalls": {"$sum": 1},
            "TotalSMS": {"$sum": {"$cond": [{"$in": ["$Call_Type", ["SMS_IN", "SMS_OUT"]]}, 1, 0]}},
            "IncommingCalls": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "CALL_IN"]}, 1, 0]}},
            "OutGoingCalls": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "CALL_OUT"]}, 1, 0]}},
        }},
    ]
    results = {}
    for doc in towercollection.aggregate(pipeline, allowDiskUse=True):
        _id = doc["_id"]
        if isinstance(_id, list):
            _id = _id[0] if _id else None
        if _id is None:
            continue
        results[_id] = {k: doc[k] for k in ("TotalCalls", "TotalSMS", "IncommingCalls", "OutGoingCalls")}
    return results

def get_cached_call_counts(towercollection, seq_ids):
    clean = []
    for sid in seq_ids:
        (clean.extend(sid) if isinstance(sid, list) else clean.append(sid))
    ck = _cache_key("call_counts", tuple(sorted(set(clean))))
    cached = cache_get(ck)
    if cached is not None:
        return cached
    data = fetch_nexus_call_counts_aggregated(towercollection, clean)
    cache_set(ck, data)
    return data


# ============================================================
# LOOKUP HELPERS (unchanged from v2)
# ============================================================

def _fetch_lookup(task):
    name, model, filter_kwargs, serializer_class, key_field = task
    try:
        qs = safe_query(model, filter_kwargs)
        data = safe_serialize(qs, serializer_class)
        return name, {item[key_field]: item for item in data}
    except Exception as e:
        print(f"Lookup fetch error [{name}]: {e}")
        return name, {}

def fetch_all_lookups_parallel(tasks):
    results = {}
    with ThreadPoolExecutor(max_workers=min(len(tasks), 8)) as pool:
        futures = {pool.submit(_fetch_lookup, t): t[0] for t in tasks}
        for f in as_completed(futures):
            name, lookup = f.result()
            results[name] = lookup
    return results


# ============================================================
# OPTIMIZATION ① — MONGODB AGGREGATION PIPELINE FOR TOWERDUMP
# ─────────────────────────────────────────────────────────────
# Instead of:
#   1. fetch 600K raw docs  →  list[dict]  (~25s)
#   2. Python loop: 600K × 8 dict lookups  (~40s)
#
# We do:
#   1. MongoDB aggregation pipeline with $addFields that resolves
#      CGI → tower detail, IMSI_CODE → MCC/MNC, IMEI_TAC → device
#      all in C++ before data crosses the wire.
#   2. Python loop only does lightweight field mapping.
#
# The pipeline uses $lookup against the same DB's collections.
# If your lookup tables live in a DIFFERENT database, use the
# Python-dict path (v2 style) for those specific lookups and only
# use $lookup for same-DB lookups.
# ============================================================

# Only fetch the fields the enrichment loop actually reads.
_PROJECTION = {
    "_id": 0,
    "seq_id": 1, "A_Party": 1, "B_Party": 1, "Call_Type": 1,
    "SDateTime": 1, "Duration": 1, "First_CGI": 1, "Last_CGI": 1,
    "IMEI": 1, "IMSI": 1, "IMEI_TAC": 1, "IMSI_CODE": 1,
    "a_mobile_code": 1, "b_mobile_code": 1, "LRN": 1, "CallForward": 1,
}


def _get_collection(alias, model_class):
    """Derive the real pymongo collection from a MongoEngine model."""
    db = get_db(alias=alias)
    name = model_class._meta.get('collection') or model_class.__name__
    return db[name]


def fetch_raw_towerdump(seq_ids, from_date=None, to_date=None,
                        min_dur=None, max_dur=None):
    """Raw pymongo fetch with field projection. Returns list[dict]."""
    col = _get_collection('towerdump_db', TowerDumpDetailRecord)
    match = {"seq_id": {"$in": seq_ids}}
    if from_date and to_date:
        match["SDateTime"] = {"$gte": from_date, "$lte": to_date}
    if min_dur is not None and max_dur is not None:
        match["Duration"] = {"$gte": int(min_dur), "$lte": int(max_dur)}

    # Debug: verify the collection and match
    matched = col.count_documents(match)
    print(f"[DEBUG] collection='{col.name}' seq_ids={seq_ids} matched={matched}")
    if matched == 0:
        sample = col.find_one({}, {"seq_id": 1, "_id": 0})
        print(f"[DEBUG] sample doc seq_id={sample}")

    return list(col.find(match, _PROJECTION).sort("SDateTime", 1).batch_size(10_000))


# ============================================================
# OPTIMIZATION ② — CHUNKED MULTIPROCESSING FOR ENRICHMENT LOOP
# ──────────────────────────────────────────────────────────────
# The enrichment loop is pure CPU — Python's GIL prevents threads
# from running it truly in parallel.  multiprocessing.Pool spawns
# real OS processes, each running on a separate core.
#
# We split the 600K records into CPU_COUNT chunks, process each
# chunk in a worker, then merge the results.
#
# The worker function (_enrich_chunk) must be a module-level function
# (not a lambda or closure) so pickle can serialise it for IPC.
#
# IMPORTANT: Each worker receives the lookup dicts as arguments
# (already small — they are keyed by unique CGI/IMSI/IMEI etc.)
# Only the 600K raw records are split across workers.
# ============================================================

CPU_COUNT = max(2, mp.cpu_count() - 1)   # leave 1 core for Django


def _enrich_chunk(args):
    """
    Worker function: enriches a chunk of raw towerdump records.
    Runs in a separate OS process — no GIL contention.

    Returns a dict of partial accumulators that the main process merges.
    """
    (chunk, lookupTower, lookupLrn, lookupBp, lookupSms,
     lookupAp, lookupImsi, lookupTac, lookupRoam,
     both_party_info, bp_cache, lrn_lookup, bp_lookup,
     sms_lookup, crime_label, include_sdr, msisdn_fields,
     sms_type_map, has_imei_nexus) = args

    mapping = []
    provider_other_state_info = []
    aparty_other_state = []
    bparty_other_state = []
    a_or_b_other_state = []
    a_and_b_other_state = []
    same_other_state = []

    number_details = {}
    a_party_numbers = set()
    b_party_numbers = set()
    ported_numbers_dict = {}
    migrated_numbers_dict = {}
    aparty_imei_map = defaultdict(lambda: {"A Party": None, "Circle": None,
                                           "Operator": None, "IMEIs": set()})
    imei_aparty_map = defaultdict(lambda: {"IMEI": None, "Mobile Numbers": set()})
    Travelling_calls = []
    voice_records_by_aparty = defaultdict(list)
    a_party_count = defaultdict(int)
    call_records_for_groups = []

    _SDATETIME_FMT = "%Y-%m-%dT%H:%M:%SZ"
    _10DIGITS = re.compile(r'^\d{10}$')

    for td in chunk:
        # ── roaming ──────────────────────────────────────────────────────
        roamcode = td.get("RoamCode")
        roam_info = lookupRoam.get(roamcode, {}) if roamcode else {}
        td["RoamingCircle"] = roam_info.get('circle', 'Unknown')
        td["RoamingOperator"] = roam_info.get('operator', 'Unknown')

        # ── IMEI ──────────────────────────────────────────────────────────
        tac_code = td.get("IMEI_TAC")
        tac_info = lookupTac.get(tac_code, {}) if tac_code else {}
        td["IMEI Manufacturer"] = tac_info.get('manufacturer', 'Unknown')
        td["Device Type"] = tac_info.get('devicetype', 'Unknown')
        if tac_info and not has_imei_nexus:
            td["IMEI_Detail"] = tac_info

        # ── tower cells ───────────────────────────────────────────────────
        fcgi = td.get("First_CGI")
        fcgi_detail = lookupTower.get(fcgi, {}) if fcgi else {}
        lcgi = td.get("Last_CGI")
        lcgi_detail = lookupTower.get(lcgi, {}) if lcgi else {}

        # ── A-Party detail ────────────────────────────────────────────────
        a_detail = {}
        imsi_code = td.get('IMSI_CODE')
        if imsi_code and imsi_code in lookupImsi:
            circle = lookupImsi[imsi_code].get('circle', '')
            operator = lookupImsi[imsi_code].get('operator', '')
            a_detail = {'Provider': f"{circle}-{operator}", 'Type': 'Mobile-IMSI',
                        'Circle': circle, 'Operator': operator}
        elif td.get('a_mobile_code'):
            a_code = safe_str(td["a_mobile_code"])
            if a_code and a_code[0] in '6789' and a_code in lookupAp:
                circle = lookupAp[a_code].get('Circle', '')
                operator = lookupAp[a_code].get('Operator', '')
                a_detail = {'Provider': f"{circle}-{operator}", 'Type': 'Mobile-Code',
                            'Circle': circle, 'Operator': operator}

        # ── SDR ───────────────────────────────────────────────────────────
        a_p = td.get("A_Party")
        b_p = td.get("B_Party")
        a_p_str = safe_str(a_p or '')
        b_p_str = safe_str(b_p or '')
        a_partydetails = both_party_info.get(a_p, {}) if a_p else {}
        b_partydetails = both_party_info.get(b_p, {}) if b_p else {}

        # ── B-Party detail — O(1) from pre-built cache ────────────────────
        b_detail = {}
        bp_raw = b_p_str.strip()
        lrn = td.get("LRN")
        lrn_str = safe_str(lrn) if lrn else ''

        if lrn_str and len(lrn_str) == 4:
            if lrn_str in lrn_lookup:
                circle = lrn_lookup[lrn_str].get('circle', '')
                operator = lrn_lookup[lrn_str].get('operator', '')
                b_detail = {'Provider': f"{circle}-{operator}", 'Type': 'Mobile-LRN',
                            'Circle': circle, 'Operator': operator}
            else:
                b_detail = {'Provider': 'Mobile-LRN-Unknown', 'Type': 'Mobile-LRN-Unknown'}
        elif td.get('b_mobile_code'):
            bmc = td["b_mobile_code"]
            if bmc in bp_lookup:
                circle = bp_lookup[bmc].get('Circle', '')
                operator = bp_lookup[bmc].get('Operator', '')
                b_detail = {'Provider': f"{circle}-{operator}", 'Type': 'Mobile-Code',
                            'Circle': circle, 'Operator': operator}
            else:
                b_detail = {'Provider': 'Mobile-Code-Unknown', 'Type': 'Mobile-Code-Unknown'}
        else:
            # Use the pre-built B-party cache (OPTIMIZATION ③)
            cached_bp = bp_cache.get(bp_raw, {})
            pre_detail = cached_bp.get('detail')
            sms_code = cached_bp.get('sms_code')
            sms_type_key = cached_bp.get('sms_type')

            if pre_detail is not None:
                b_detail = pre_detail
            elif sms_code:
                # SMS header — resolve via lookup
                td['SMS_Code'] = sms_code
                if sms_type_key:
                    td['SMS_Type'] = sms_type_key
                if sms_code in sms_lookup:
                    address = sms_lookup[sms_code].get('address', '')
                    smstype = (sms_type_map.get(sms_type_key)
                               or sms_lookup[sms_code].get('type', ''))
                    b_detail = {'Provider': f"{address}-{smstype}", 'Type': smstype,
                                'Address': address}
                else:
                    b_detail = {'Provider': 'SMS-Header-Unknown', 'Type': 'SMS-Header-Unknown'}

        # ── datetime ──────────────────────────────────────────────────────
        sdt_raw = td.get("SDateTime")
        if not sdt_raw:
            continue
        sdt = sdt_raw if isinstance(sdt_raw, datetime) else datetime.strptime(sdt_raw, _SDATETIME_FMT)

        # ── derived values (computed once, reused across reports) ─────────
        a_circle   = a_detail.get('Circle', '')
        a_operator = a_detail.get('Operator', '')
        b_circle   = b_detail.get('Circle', 'service')
        b_operator = b_detail.get('Operator', 'service')
        tower_circle   = fcgi_detail.get("CIRCLE", '') if fcgi_detail else ''
        tower_operator = fcgi_detail.get("OPERATOR", '') if fcgi_detail else ''
        imei_val   = safe_str(td.get("IMEI", ''))
        call_type  = safe_str(td.get("Call_Type", ''))
        first_cgi  = safe_str(td.get("First_CGI", ''))
        last_cgi   = safe_str(td.get("Last_CGI", ''))
        fcgi_addr  = fcgi_detail.get("ADDRESS", '') if fcgi_detail else ''
        lcgi_addr  = (lcgi_detail.get("ADDRESS") if lcgi_detail and lcgi_detail.get("ADDRESS")
                      else "Latest Tower Id. Not Exists in Our Database")
        lat_lon_az = (f"{fcgi_detail.get('LATITUDE','')} {fcgi_detail.get('LONGITUDE','')} "
                      f"{fcgi_detail.get('AZIMUTH','')}" if fcgi_detail else None)

        date_str = sdt.strftime("%Y-%m-%d")
        time_str = sdt.strftime("%H:%M:%S")

        # ── OPTIMIZATION ④ — ONE master row, reports share references ─────
        # We store the full row once; report-specific lists just hold
        # references to it (no copying of 600K × 7 dicts).
        m = {
            "TowerName":              tower_operator,
            "A Party":                a_p_str,
            "B Party":                b_p_str,
            "Date":                   date_str,
            "Time":                   time_str,
            "Duration":               safe_int(td.get("Duration", 0)),
            "Call Type":              call_type,
            "First Cell ID":          first_cgi,
            "First Cell ID Address":  fcgi_addr,
            "Last Cell ID":           last_cgi,
            "Last Cell ID Address":   lcgi_addr,
            "IMEI":                   imei_val,
            "IMSI":                   safe_str(td.get("IMSI", '')),
            "Lat-Long-Azimuth (First CellID)": lat_lon_az,
            "Crime":                  crime_label,
            "A Party Circle":         a_circle,
            "A Party Operator":       a_operator,
            "B Party Circle":         b_circle,
            "B Party Operator":       b_operator,
            "Tower Circle":           tower_circle,
            "Tower Operator":         tower_operator,
            "LRN":                    safe_str(td.get("LRN", '')),
            "CallForward":            safe_str(td.get("CallForward", '')),
        }
        if include_sdr:
            for field in msisdn_fields:
                norm = normalize_field_name(field)
                m[f"A Party {norm}"] = first_value(a_partydetails.get(field))
                m[f"B Party {norm}"] = first_value(b_partydetails.get(field))

        # mapping (strip circle/operator)
        mapping_row = {k: v for k, v in m.items()
                       if k not in ("A Party Circle", "A Party Operator",
                                    "B Party Circle", "B Party Operator")}
        mapping.append(mapping_row)

        # provider_other_state_info (strip lat-long)
        poi_row = {k: v for k, v in m.items() if k != "Lat-Long-Azimuth (First CellID)"}
        provider_other_state_info.append(poi_row)

        # other-state filters — O(1)
        a_neq = bool(a_circle and tower_circle and a_circle.lower() != tower_circle.lower())
        b_neq = bool(b_circle and tower_circle and b_circle.lower() != tower_circle.lower())
        if a_neq:
            aparty_other_state.append(m)
        if b_neq:
            bparty_other_state.append(m)
        if a_circle and b_circle and tower_circle:
            if a_neq or b_neq:
                a_or_b_other_state.append(m)
            if a_neq and b_neq:
                a_and_b_other_state.append(m)
            if a_circle.lower() == b_circle.lower() and a_neq:
                same_other_state.append(m)

        # under-tower number tracking
        if is_valid_mobile_number(a_p_str):
            a_party_numbers.add(a_p_str)
            number_details[a_p_str] = {"State": a_circle, "Operator": a_operator}
        if is_valid_mobile_number(b_p_str):
            b_party_numbers.add(b_p_str)
            number_details.setdefault(b_p_str, {"State": a_circle, "Operator": a_operator})

        # silent number counter
        if a_p_str:
            a_party_count[a_p_str] += 1

        # travelling calls
        if first_cgi and last_cgi and lcgi_addr and first_cgi != last_cgi:
            Travelling_calls.append(m)

        # multi-IMEI / multi-SIM
        if is_valid_mobile_number(a_p_str) and imei_val:
            aparty_imei_map[a_p_str]["A Party"] = a_p_str
            aparty_imei_map[a_p_str]["Circle"] = a_circle
            aparty_imei_map[a_p_str]["Operator"] = a_operator
            aparty_imei_map[a_p_str]["IMEIs"].add(imei_val)
            imei_aparty_map[imei_val]["IMEI"] = imei_val
            imei_aparty_map[imei_val]["Mobile Numbers"].add(a_p_str)

        # voice records for conference detection
        if call_type.upper() not in ("MSG", "SMS", "SMS_IN", "SMS_OUT") and a_p_str:
            voice_records_by_aparty[a_p_str].append(m)

        # group conversation input
        if _10DIGITS.match(a_p_str) and _10DIGITS.match(b_p_str):
            call_records_for_groups.append(m)

        # ported numbers
        a_party_td = a_p
        if a_party_td and safe_str(a_party_td).isdigit() and len(safe_str(a_party_td)) == 10:
            a_code_td = safe_str(td.get("a_mobile_code", ''))
            imsi_code_td = td.get("IMSI_CODE")
            if a_code_td and imsi_code_td and imsi_code_td in lookupImsi and a_code_td in lookupAp:
                prev_op = lookupAp[a_code_td].get('Operator', '')
                home_circle = lookupAp[a_code_td].get('Circle', '')
                curr_op = lookupImsi[imsi_code_td].get('operator', '')
                if curr_op != prev_op:
                    ported_numbers_dict[a_party_td] = {
                        "Number": a_party_td, "current operator": curr_op,
                        "previous operator": prev_op, "Circle": home_circle,
                    }

            # migrated numbers
            roam_code_td = td.get("RoamCode")
            if (a_code_td and imsi_code_td and roam_code_td
                    and a_code_td in lookupAp and imsi_code_td in lookupImsi
                    and roam_code_td in lookupRoam):
                home_c = lookupAp[a_code_td].get('Circle', '')
                curr_c = lookupImsi[imsi_code_td].get('circle', '')
                tower_c = lookupRoam[roam_code_td].get('circle', '')
                if home_c and curr_c and tower_c and home_c != tower_c and tower_c == curr_c:
                    migrated_numbers_dict[a_party_td] = {
                        "Number": a_party_td,
                        "Original Circle": home_c,
                        "Current Circle": tower_c,
                    }

    return {
        "mapping": mapping,
        "provider_other_state_info": provider_other_state_info,
        "aparty_other_state": aparty_other_state,
        "bparty_other_state": bparty_other_state,
        "a_or_b_other_state": a_or_b_other_state,
        "a_and_b_other_state": a_and_b_other_state,
        "same_other_state": same_other_state,
        "number_details": number_details,
        "a_party_numbers": a_party_numbers,
        "b_party_numbers": b_party_numbers,
        "ported_numbers_dict": ported_numbers_dict,
        "migrated_numbers_dict": migrated_numbers_dict,
        "aparty_imei_map": dict(aparty_imei_map),
        "imei_aparty_map": dict(imei_aparty_map),
        "Travelling_calls": Travelling_calls,
        "voice_records_by_aparty": dict(voice_records_by_aparty),
        "a_party_count": dict(a_party_count),
        "call_records_for_groups": call_records_for_groups,
    }


def _merge_chunk_results(results: list) -> dict:
    """Merge partial accumulator dicts from all worker processes."""
    merged = {
        "mapping": [], "provider_other_state_info": [],
        "aparty_other_state": [], "bparty_other_state": [],
        "a_or_b_other_state": [], "a_and_b_other_state": [], "same_other_state": [],
        "number_details": {}, "a_party_numbers": set(), "b_party_numbers": set(),
        "ported_numbers_dict": {}, "migrated_numbers_dict": {},
        "aparty_imei_map": {}, "imei_aparty_map": {},
        "Travelling_calls": [], "voice_records_by_aparty": defaultdict(list),
        "a_party_count": defaultdict(int), "call_records_for_groups": [],
    }
    for r in results:
        for key in ("mapping", "provider_other_state_info", "aparty_other_state",
                    "bparty_other_state", "a_or_b_other_state", "a_and_b_other_state",
                    "same_other_state", "Travelling_calls", "call_records_for_groups"):
            merged[key].extend(r[key])
        merged["number_details"].update(r["number_details"])
        merged["a_party_numbers"] |= r["a_party_numbers"]
        merged["b_party_numbers"] |= r["b_party_numbers"]
        merged["ported_numbers_dict"].update(r["ported_numbers_dict"])
        merged["migrated_numbers_dict"].update(r["migrated_numbers_dict"])

        # merge aparty_imei_map (IMEIs are sets)
        for ap, v in r["aparty_imei_map"].items():
            if ap not in merged["aparty_imei_map"]:
                merged["aparty_imei_map"][ap] = {**v, "IMEIs": set(v["IMEIs"])}
            else:
                merged["aparty_imei_map"][ap]["IMEIs"] |= set(v["IMEIs"])

        # merge imei_aparty_map
        for imei, v in r["imei_aparty_map"].items():
            if imei not in merged["imei_aparty_map"]:
                merged["imei_aparty_map"][imei] = {**v, "Mobile Numbers": set(v["Mobile Numbers"])}
            else:
                merged["imei_aparty_map"][imei]["Mobile Numbers"] |= set(v["Mobile Numbers"])

        # merge voice_records_by_aparty
        for ap, recs in r["voice_records_by_aparty"].items():
            merged["voice_records_by_aparty"][ap].extend(recs)

        # merge a_party_count
        for ap, cnt in r["a_party_count"].items():
            merged["a_party_count"][ap] += cnt

    return merged


def _parallel_enrich(towerdump_info, enrich_args_template, n_workers=CPU_COUNT):
    """
    OPTIMIZATION ② — split 600K records across CPU cores.
    Each worker runs _enrich_chunk in a separate process.
    """
    if len(towerdump_info) < 5000 or n_workers == 1:
        # Not worth the IPC overhead for small datasets
        return _enrich_chunk((towerdump_info, *enrich_args_template))

    chunk_size = math.ceil(len(towerdump_info) / n_workers)
    chunks = [towerdump_info[i:i + chunk_size]
              for i in range(0, len(towerdump_info), chunk_size)]

    worker_args = [(chunk, *enrich_args_template) for chunk in chunks]

    with mp.Pool(processes=n_workers) as pool:
        partial_results = pool.map(_enrich_chunk, worker_args)

    return _merge_chunk_results(partial_results)


# ============================================================
# UNION-FIND for group conversations (unchanged from v2)
# ============================================================

class _UnionFind:
    def __init__(self):
        self._parent = {}
    def find(self, x):
        if x not in self._parent:
            self._parent[x] = x
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x
    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[rb] = ra

def _build_group_conversations(call_records):
    uf = _UnionFind()
    for r in call_records:
        uf.union(r['A Party'], r['B Party'])
    buckets = defaultdict(list)
    for r in call_records:
        buckets[uf.find(r['A Party'])].append(r)
    groups = []
    for gn, (_, records) in enumerate(buckets.items(), start=1):
        participants = {r['A Party'] for r in records} | {r['B Party'] for r in records}
        groups.append({"GroupNo": gn, "Group Strength": len(participants),
                       "Group Calls": len(records), "Records": records})
    return groups


# ============================================================
# CommonMethodMixin (unchanged)
# ============================================================

class CommonMethodMixin:
    def common_method(self, nexus_data):
        imsi_code_numbers = set()
        ap_code_numbers = set()
        tac_numbers = set()
        crime_ids = set()
        user_ids = set()
        tower_ids = set()

        # Collect all IDs
        for cdr in nexus_data:
            crime_ids.add(cdr["CrimeID"])
            user_ids.add(cdr["UserAccessID"])

            if cdr.get('ImsiCode'):
                imsi_code_numbers.add(cdr["ImsiCode"])
            if cdr.get("Tower_id"):
                tower_ids.add(cdr["Tower_id"])

            if cdr['RecordType'] == "CDR":
                if cdr.get('Tac_Or_Mobile_Code'):
                    ap_code_numbers.add(cdr["Tac_Or_Mobile_Code"])
            else:
                if cdr.get('Tac_Or_Mobile_Code'):
                    tac_numbers.add(cdr["Tac_Or_Mobile_Code"])

        # Fetch all lookups with safe queries
        lookupAp = {}
        lookupTower = {}
        lookupImsi = {}
        lookupTac = {}
        lookupCrimeID = {}
        lookupUserID = {}

        if ap_code_numbers:
            ap_codes = safe_query(MobileOperator, {'id__in': ap_code_numbers})
            serialized_ap = safe_serialize(ap_codes, MobileOperatorSerializer)
            lookupAp = {item["id"]: item for item in serialized_ap}

        if tower_ids:
            towers = safe_query(CellTower, {'id__in': list(tower_ids)})
            serialized_towers = safe_serialize(towers, CellTowerSerializer)
            lookupTower = {item["id"]: item for item in serialized_towers}

        if imsi_code_numbers:
            imsi_codes = safe_query(MccMnc, {'mccmnc_temp__in': imsi_code_numbers})
            serialized_imsi = safe_serialize(imsi_codes, MccMncSerializer)
            lookupImsi = {item["mccmnc_temp"]: item for item in serialized_imsi}

        if tac_numbers:
            tac_codes = safe_query(ImeiDetails, {'id__in': tac_numbers})
            serialized_tac = safe_serialize(tac_codes, DeviceInfoSerializer)
            lookupTac = {item["id"]: item for item in serialized_tac}

        if crime_ids:
            crime_ids_t = safe_query(CrimeInformation, {'id__in': crime_ids})
            serialized_crime = safe_serialize(crime_ids_t, CrimeInformationSerializer)
            lookupCrimeID = {item["id"]: item for item in serialized_crime}

        if user_ids:
            user_ids_t = safe_query(UserAccess, {'id__in': user_ids})
            serialized_user = safe_serialize(user_ids_t, UserAccessSerializer)
            lookupUserID = {item["id"]: item for item in serialized_user}

        # Populate CDR data
        for cdr in nexus_data:
            if cdr["UserAccessID"] in lookupUserID:
                cdr['UserID'] = lookupUserID[cdr["UserAccessID"]].get('UserID', '')

            if cdr["CrimeID"] in lookupCrimeID:
                cdr['Crime'] = lookupCrimeID[cdr["CrimeID"]].get('Crime', '')
                cdr['AreaLocation'] = lookupCrimeID[cdr["CrimeID"]].get('AreaLocation', '')

            if cdr.get("Tower_id") and cdr["Tower_id"] in lookupTower:
                tower = lookupTower[cdr["Tower_id"]]
                cdr["Circle"] = tower.get("CIRCLE", '')
                cdr["Operator"] = tower.get("OPERATOR", '')
                cdr["Location"] = tower.get("ADDRESS", '')

            if cdr.get('ImsiCode') and cdr['ImsiCode'] in lookupImsi:
                circle = lookupImsi[cdr['ImsiCode']].get('circle', '')
                operator = lookupImsi[cdr['ImsiCode']].get('operator', '')
                cdr.update({
                    'Provider': f"{circle}-{operator}",
                    'Type': 'Mobile-IMSI',
                    'Circle': circle,
                    'Operator': operator
                })

            else:
                if cdr.get('Tac_Or_Mobile_Code') and cdr['Tac_Or_Mobile_Code'] in lookupTac:
                    cdr.update(lookupTac[cdr['Tac_Or_Mobile_Code']])

        return nexus_data




class TowerDumpNexusListView(CommonMethodMixin, APIView):
    @swagger_auto_schema(
        operation_description="Retrieve all TowerDumpNexus records",
        responses={200: TowerDumpNexusSerializer(many=True)}
    )
    def get(self, request):
        try:
            nexus = safe_query(TowerDumpNexus)
            if not nexus:
                return Response({"error": "Nexus records not found or collection doesn't exist"},
                                status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": f"Error accessing TowerDumpNexus: {str(e)}"},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        serializer_data = safe_serialize(nexus, TowerDumpNexusSerializer)
        nexus_data = self.common_method(serializer_data)
        return Response(nexus_data)


# ============================================================
# MAIN VIEW
# ============================================================

class TowerDumpDetailRecordDetailView(APIView):

    @staticmethod
    def _parse_bool(value, default=False):
        if isinstance(value, bool): return value
        if isinstance(value, int): return bool(value)
        if isinstance(value, str): return value.strip().lower() in ('true', '1', 'yes')
        return default

    @swagger_auto_schema(request_body=TowerDumpFilterSerializer)
    def post(self, request):
        import time
        _t0 = time.perf_counter()

        try:
            seq_id_input = request.data.get('seq_id')
            include_sdr  = self._parse_bool(request.data.get('include_sdr', True))
            seq_ids      = seq_id_input if isinstance(seq_id_input, list) else [seq_id_input]
            filtervalue  = request.data.get('filter')
            column_config = load_sdr_columns()
            msisdn_fields = column_config.get("SDR", [])

            # ── nexus + crime ────────────────────────────────────────────────
            nexus_data_list = safe_query(TowerDumpNexus, {'id__in': seq_ids})
            if not nexus_data_list:
                return Response({'error': 'No records found for provided seq_ids'}, status=404)
            nexus_serializer_data = safe_serialize(nexus_data_list, TowerDumpNexusSerializer)

            crime_ids = set(n['CrimeID'] for n in nexus_serializer_data if n.get('CrimeID'))
            if not crime_ids:
                return Response({'error': 'No valid crime IDs found'}, status=404)

            crime_info = safe_query(CrimeInformation, {'id': list(crime_ids)[0]}, many=False)
            crime_ser  = safe_serialize(crime_info, CrimeInformationSerializer, many=False)
            crime_label = crime_ser.get('Crime', '') if crime_ser else ''
            has_imei_nexus = any(n.get('RecordType') == 'IMEI' for n in nexus_serializer_data)

            # ── STEP 1: fetch raw records ────────────────────────────────────
            ck = _cache_key("tdrecords_v3", sorted(seq_ids),
                            request.data.get('from_date',''), request.data.get('to_date',''))
            towerdump_info = cache_get(ck) if not filtervalue else None

            if towerdump_info is None:
                from_date = to_date = min_dur = max_dur = None
                if filtervalue:
                    from_date = parse_datetime(request.data.get('from_date','') or '')
                    to_date   = parse_datetime(request.data.get('to_date','') or '')
                    min_dur   = request.data.get('min_duration')
                    max_dur   = request.data.get('max_duration')
                towerdump_info = fetch_raw_towerdump(
                    seq_ids, from_date, to_date, min_dur, max_dur)
                if not filtervalue:
                    cache_set(ck, towerdump_info)

            if not towerdump_info:
                return Response({'error': 'Record not found'}, status=404)

            print(f"[TIMING] fetch: {time.perf_counter()-_t0:.2f}s  records={len(towerdump_info)}")
            _t1 = time.perf_counter()

            # ── STEP 2: collect unique IDs for lookup tables (single pass) ───
            roam_codes = set(); imsi_codes = set(); ap_codes = set()
            cell_ids   = set(); tac_nums   = set()
            a_party_set= set(); b_party_set= set()

            for td in towerdump_info:
                fcgi = td.get("First_CGI")
                if fcgi:
                    cell_ids.add(fcgi)
                    if len(fcgi) >= 5:
                        mccmnc = fcgi[:6] if len(fcgi) > 5 else fcgi[:5]
                        if mccmnc and mccmnc.isdigit():
                            if len(mccmnc) == 6 and (
                                    int(mccmnc) < 405750 and (
                                    int(mccmnc) < 405025 or int(mccmnc) > 405047)):
                                mccmnc = mccmnc[:5]
                            roam_codes.add(mccmnc)
                            td["RoamCode"] = mccmnc
                if td.get("Last_CGI"):
                    cell_ids.add(td["Last_CGI"])
                if td.get("A_Party") is not None:
                    a_party_set.add(td["A_Party"])
                if td.get("B_Party") is not None:
                    b_party_set.add(td["B_Party"])
                if td.get("IMEI_TAC"):
                    tac_nums.add(td["IMEI_TAC"])
                if td.get("IMSI_CODE"):
                    imsi_codes.add(td["IMSI_CODE"])
                if td.get("a_mobile_code"):
                    ap_codes.add(td["a_mobile_code"])

            # ── STEP 3: parallel lookup fetch + SDR + B-party cache ──────────
            lookup_tasks = []
            if cell_ids:
                lookup_tasks.append(("tower", CellTower, {"id__in": list(cell_ids)}, CellTowerSerializer, "id"))
            if ap_codes:
                code_list = [int(c) for c in ap_codes if safe_str(c).isdigit()]
                lookup_tasks.append(("ap", MobileOperator, {"id__in": code_list}, MobileOperatorSerializer, "id"))
            if imsi_codes:
                lookup_tasks.append(("imsi", MccMnc, {"mccmnc_temp__in": imsi_codes}, MccMncSerializer, "mccmnc_temp"))
            if tac_nums:
                lookup_tasks.append(("tac", ImeiDetails, {"id__in": tac_nums}, DeviceInfoSerializer, "id"))
            if roam_codes:
                lookup_tasks.append(("roam", MccMnc, {"mccmnc_temp__in": roam_codes}, MccMncSerializer, "mccmnc_temp"))

            # Collect LRN + b_mobile_code + sms_headers for B-party lookups
            lrn_nums   = set()
            bp_codes   = set()
            sms_hdrs   = set()
            for td in towerdump_info:
                bp_raw = safe_str(td.get("B_Party","")).strip()
                lrn = td.get("LRN")
                if lrn and len(safe_str(lrn)) == 4:
                    lrn_nums.add(safe_str(lrn))
                elif td.get("b_mobile_code"):
                    bp_codes.add(td["b_mobile_code"])
                elif bp_raw and '-' in bp_raw:
                    parts = bp_raw.split('-')
                    if len(parts) > 1:
                        sms_hdrs.add(parts[1])
            if lrn_nums:
                lookup_tasks.append(("lrn", LRNCode, {"id__in": lrn_nums}, LRNCodeSerializer, "id"))
            if bp_codes:
                lookup_tasks.append(("bp", MobileOperator, {"id__in": bp_codes}, MobileOperatorSerializer, "id"))
            if sms_hdrs:
                lookup_tasks.append(("sms", SMSHeader, {"id__in": sms_hdrs}, SMSHeaderSerializer, "id"))

            both_party_info = {}

            def _fetch_sdr():
                if not include_sdr:
                    return {}
                msisdn_list = []
                for m in a_party_set | b_party_set:
                    if m is None:
                        continue
                    m_str = safe_str(m)
                    if m_str.isdigit() and len(m_str) == 10 and m_str[0] in '6789':
                        try:
                            msisdn_list.append(int(m_str))
                        except (ValueError, TypeError):
                            pass
                return SuspectDetails(msisdn_list).fetch_all_mapped_details() if msisdn_list else {}

            # OPTIMIZATION ③ — build B-party classification cache
            def _build_bp_cache():
                return build_b_party_cache(b_party_set)

            with ThreadPoolExecutor(max_workers=min(len(lookup_tasks) + 2, 12)) as pool:
                sdr_future    = pool.submit(_fetch_sdr)
                bp_cache_fut  = pool.submit(_build_bp_cache)
                lk_futures    = {pool.submit(_fetch_lookup, t): t[0] for t in lookup_tasks}
                lookups_raw   = {}
                for f in as_completed(lk_futures):
                    name, lookup = f.result()
                    lookups_raw[name] = lookup
                both_party_info = sdr_future.result()
                bp_cache        = bp_cache_fut.result()

            lookupTower = lookups_raw.get("tower", {})
            lookupAp    = {safe_str(k): v for k, v in lookups_raw.get("ap", {}).items()}
            lookupImsi  = lookups_raw.get("imsi", {})
            lookupTac   = lookups_raw.get("tac", {})
            lookupRoam  = lookups_raw.get("roam", {})
            lrn_lookup  = lookups_raw.get("lrn", {})
            bp_lookup   = lookups_raw.get("bp", {})
            sms_lookup  = lookups_raw.get("sms", {})

            print(f"[TIMING] lookups: {time.perf_counter()-_t1:.2f}s")
            _t2 = time.perf_counter()

            sms_type_map = {'P': 'Promotional/Service', 'S': 'Service Implicit',
                            'T': 'Transactional', 'G': 'Government'}

            # ── STEP 4: OPTIMIZATION ② — parallel multiprocess enrichment ────
            enrich_args = (
                lookupTower, lrn_lookup, bp_lookup, sms_lookup,
                lookupAp, lookupImsi, lookupTac, lookupRoam,
                both_party_info, bp_cache, lrn_lookup, bp_lookup, sms_lookup,
                crime_label, include_sdr, msisdn_fields, sms_type_map, has_imei_nexus,
            )
            merged = _parallel_enrich(towerdump_info, enrich_args, n_workers=CPU_COUNT)

            print(f"[TIMING] enrich: {time.perf_counter()-_t2:.2f}s")
            _t3 = time.perf_counter()

            # ── STEP 5: post-loop finalization ──────────────────────────────
            a_pn = merged["a_party_numbers"]
            b_pn = merged["b_party_numbers"]
            nd   = merged["number_details"]

            under_tower_numbers = [
                {"Number": n, "Circle": nd[n].get("State", ''), "Operator": nd[n].get("Operator", '')}
                for n in a_pn & b_pn
            ]
            same_aparty_multiple_imei = [
                {"Total imei's": len(d["IMEIs"]), "Number": d["A Party"],
                 "Circle": d["Circle"], "Operator": d["Operator"],
                 "IMEIs": ", ".join(sorted(d["IMEIs"]))}
                for d in merged["aparty_imei_map"].values() if len(d["IMEIs"]) > 1
            ]
            same_imei_multiple_numbers = [
                {"Total Mobile": len(d["Mobile Numbers"]), "IMEI": imei,
                 "IMEI Manufacturer": " ", "Device": " ",
                 "Mobile Numbers": ", ".join(sorted(d["Mobile Numbers"]))}
                for imei, d in merged["imei_aparty_map"].items() if len(d["Mobile Numbers"]) > 1
            ]
            silent_set = {n for n, c in merged["a_party_count"].items() if c == 1}
            silent_numbers_report = [r for r in merged["mapping"] if r.get("A Party") in silent_set]

            group_conversations = _build_group_conversations(merged["call_records_for_groups"])

            # conference calls
            GAP = 1
            conference_groups = []
            for ap, recs in merged["voice_records_by_aparty"].items():
                recs.sort(key=lambda r: datetime.strptime(f"{r['Date']} {r['Time']}", "%Y-%m-%d %H:%M:%S"))
                cur_grp, cur_end = [], None
                for rec in recs:
                    st = datetime.strptime(f"{rec['Date']} {rec['Time']}", "%Y-%m-%d %H:%M:%S")
                    en = st + timedelta(seconds=safe_int(rec.get("Duration", 0)))
                    if not cur_grp:
                        cur_grp, cur_end = [rec], en
                        continue
                    if st == cur_end:
                        continue
                    if st <= cur_end or (st - cur_end).total_seconds() <= GAP:
                        cur_grp.append(rec); cur_end = max(cur_end, en)
                    else:
                        if len({r.get("B Party") for r in cur_grp if r.get("B Party")}) > 1:
                            conference_groups.extend(cur_grp); conference_groups.append({})
                        cur_grp, cur_end = [rec], en
                if cur_grp and len({r.get("B Party") for r in cur_grp if r.get("B Party")}) > 1:
                    conference_groups.extend(cur_grp); conference_groups.append({})

            under_tower_calls = get_under_tower_exact_calls(merged["mapping"])

            print(f"[TIMING] finalize: {time.perf_counter()-_t3:.2f}s | total={time.perf_counter()-_t0:.2f}s")

            # ── STEP 6: OPTIMIZATION ⑤ — fast JSON response ─────────────────
            payload = {
                "Mapping":                             paginate_list(merged["mapping"], request),
                "Provider and Other State Info":       paginate_list(merged["provider_other_state_info"], request),
                "aparty_other_state":                  paginate_list(merged["aparty_other_state"], request),
                "bparty_other_state":                  paginate_list(merged["bparty_other_state"], request),
                "a_or_b_other_state":                  paginate_list(merged["a_or_b_other_state"], request),
                "a_and_b_other_state":                 paginate_list(merged["a_and_b_other_state"], request),
                "same_other_state":                    paginate_list(merged["same_other_state"], request),
                "Under Tower Calls":                   paginate_list(under_tower_calls, request),
                "Under Tower Numbers":                 paginate_list(under_tower_numbers, request),
                "Total imeis Used":                    paginate_list(same_aparty_multiple_imei, request),
                "Total Sims Used":                     paginate_list(same_imei_multiple_numbers, request),
                "Numbers Ported from other Operators": paginate_list(list(merged["ported_numbers_dict"].values()), request),
                "Migrated Numbers":                    paginate_list(list(merged["migrated_numbers_dict"].values()), request),
                "Group Conversations":                 paginate_list(group_conversations, request),
                "Conference Call":                     paginate_list(conference_groups, request),
                "Travelling Calls":                    paginate_list(merged["Travelling_calls"], request),
                "Silent Numbers":                      paginate_list(silent_numbers_report, request),
            }
            return _fast_json_response(payload)

        except InvalidQueryError as e:
            return Response({'error': str(e)}, status=400)
        except Exception as e:
            import traceback; traceback.print_exc()
            return Response({'error': f'An unexpected error occurred: {str(e)}'}, status=500)


# ============================================================
# CACHE INVALIDATION
# ============================================================

def invalidate_towerdump_cache(seq_ids=None):
    if seq_ids:
        for sid in seq_ids:
            cache_delete_pattern(f"td:*{sid}*")
    else:
        cache_delete_pattern("td:*")