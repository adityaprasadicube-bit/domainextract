"""
ip_views.py
"""

import hashlib
import json
import threading
import time
from bson import ObjectId

from django.utils.dateparse import parse_datetime
from drf_yasg.utils import swagger_auto_schema
from mongoengine import InvalidQueryError, get_db
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.core.cache import cache

from ..ip_serializers import IPDRNexusSerializer, IPDRRecordSerializer
from ..ipdr_models.ip_model import IPDRNexus, IPDRRecord
from ..ipdr_workspace.ipdr_mapping import (
    _get_ipdr_collection,
    fetch_nexus_map_pipeline,
    fetch_ipdr_page_pipeline,
    get_count_fast,
    scan_keys_pipeline,
    ipdr_raw_page,
    extract_page_keys,
    build_enrichment_lookups,
    enrich_records,
    build_crime_map_pipeline,
    build_crime_map,
)
from ..ipdr_workspace.ipdr_report_gen import strip_internal_fields, _now, _elapsed
from ...models import CrimeInformation, UserAccess, ImeiDetails, MccMnc, MobileOperator
from ...serializers import (
    CrimeInformationSerializer, UserAccessSerializer,
    MccMncSerializer, MobileOperatorSerializer, DeviceInfoSerializer,
)

_STATUS_KEY  = "status"
_LOOKUPS_KEY = "lookups"
_META_KEY    = "meta"

_PAGE_LIMIT_MAX = 500
_BG_SCAN_CHUNK  = 50_000
_STREAM_BATCH   = 5_000


# ─────────────────────────────────────────────────────────────────────────────
# CommonMethodMixin
# ─────────────────────────────────────────────────────────────────────────────

class CommonMethodMixin:
    def common_method(self, nexus_data):
        imsi_code_numbers = set()
        ap_code_numbers   = set()
        tac_numbers       = set()
        crime_ids         = set()
        user_ids          = set()

        for ipdr in nexus_data:
            crime_ids.add(ipdr["CrimeID"])
            user_ids.add(ipdr["UserAccessID"])
            rtype = ipdr["RecordType"]
            if rtype == "Mobile":
                ap_code_numbers.add(ipdr["IPDR"][:4])
            elif rtype == "IMEI":
                tac_numbers.add(ipdr["IPDR"][:4])
            elif rtype == "Tower":
                raw    = ipdr["IPDR"]
                mccmnc = raw[:6] if len(raw) > 5 else raw[:5]
                if mccmnc.isdigit():
                    if len(mccmnc) == 6 and int(mccmnc) < 405750 and not (405025 <= int(mccmnc) <= 405047):
                        mccmnc = mccmnc[:5]
                    ipdr["ImsiCode"] = mccmnc
                    imsi_code_numbers.add(mccmnc)

        lookupAp    = {i["id"]: i          for i in MobileOperatorSerializer(MobileOperator.objects.filter(id__in=ap_code_numbers),     many=True).data} if ap_code_numbers   else {}
        lookupImsi  = {i["mccmnc_temp"]: i for i in MccMncSerializer(MccMnc.objects.filter(mccmnc_temp__in=imsi_code_numbers),          many=True).data} if imsi_code_numbers else {}
        lookupTac   = {i["id"]: i          for i in DeviceInfoSerializer(ImeiDetails.objects.filter(id__in=tac_numbers),                many=True).data} if tac_numbers       else {}
        lookupCrime = {i["id"]: i          for i in CrimeInformationSerializer(CrimeInformation.objects.filter(id__in=crime_ids),       many=True).data} if crime_ids         else {}
        lookupUser  = {i["id"]: i          for i in UserAccessSerializer(UserAccess.objects.filter(id__in=user_ids),                    many=True).data} if user_ids          else {}

        for ipdr in nexus_data:
            uid = ipdr.get("UserAccessID")
            if uid and uid in lookupUser:
                ipdr["UserID"] = lookupUser[uid]["UserID"]
            cid = ipdr.get("CrimeID")
            if cid and cid in lookupCrime:
                ipdr["Crime"]        = lookupCrime[cid]["Crime"]
                ipdr["AreaLocation"] = lookupCrime[cid]["AreaLocation"]
            ic = ipdr.get("ImsiCode")
            if ic and ic in lookupImsi:
                c = lookupImsi[ic]["circle"]
                o = lookupImsi[ic]["operator"]
                ipdr.update({"Provider": f"{c}-{o}", "Circle": c, "Operator": o})
            if ipdr["RecordType"] == "Mobile" and not ipdr.get("Provider"):
                mc = ipdr.get("Tac_Or_Mobile_Code")
                if mc and mc in lookupAp:
                    c = lookupAp[mc]["Circle"]
                    o = lookupAp[mc]["Operator"]
                    ipdr.update({"Provider": f"{c}-{o}", "Circle": c, "Operator": o})

        return nexus_data


class IPDRNexusListView(CommonMethodMixin, APIView):
    @swagger_auto_schema(
        operation_description="Retrieve all Nexus records",
        responses={200: IPDRNexusSerializer(many=True)},
    )
    def get(self, request):
        try:
            nexus = IPDRNexus.objects.all()
        except IPDRNexus.DoesNotExist:
            return Response({"error": "Nexus records not found"}, status=status.HTTP_404_NOT_FOUND)
        nexus_data = self.common_method(IPDRNexusSerializer(nexus, many=True).data)
        return Response(nexus_data)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_mongo_filter(seq_ids, from_date, to_date, min_duration, max_duration):
    f = {"seq_id": {"$in": seq_ids}}
    if from_date and to_date:
        f["SDateTime"] = {"$gte": from_date, "$lte": to_date}
    if min_duration is not None and max_duration is not None:
        try:
            f["Duration"] = {"$gte": int(min_duration), "$lte": int(max_duration)}
        except (ValueError, TypeError):
            f["Duration"] = {"$gte": min_duration, "$lte": max_duration}
    return f


# ─────────────────────────────────────────────────────────────────────────────
# Main IPDR detail view
# ─────────────────────────────────────────────────────────────────────────────

class IPDRRecordDetailView(APIView):

    @staticmethod
    def _parse_bool(value, default=False):
        if isinstance(value, bool): return value
        if isinstance(value, int):  return bool(value)
        if isinstance(value, str):  return value.strip().lower() in ("true", "1", "yes")
        return default

    @staticmethod
    def _cget(key):
        try:    return cache.get(key)
        except: return None

    @staticmethod
    def _cset(key, value, timeout=7200):
        try:    cache.set(key, value, timeout=timeout)
        except Exception as e:
            print(f"[{_now()}] cache set failed '{key}': {e}")

    @staticmethod
    def _cdel(key):
        try:    cache.delete(key)
        except: pass

    @staticmethod
    def _cache_key(seq_ids, params: dict) -> str:
        h = hashlib.md5(
            f"{'_'.join(sorted(map(str, seq_ids)))}_{json.dumps(params, sort_keys=True)}".encode()
        ).hexdigest()
        return f"ipdr_{h}"

    def post(self, request):
        t_request = time.monotonic()
        try:
            print(f"\n[{_now()}] {'='*60}")
            print(f"[{_now()}] INCOMING REQUEST")
            print(f"[{_now()}] {'='*60}")

            seq_ids = request.data.get("seq_ids", [])
            if not seq_ids or not isinstance(seq_ids, list):
                return Response({"error": "seq_ids must be a non-empty list"}, status=400)

            page        = int(request.data.get("page", 1))
            limit       = min(int(request.data.get("limit", 500)), _PAGE_LIMIT_MAX)
            filtervalue = request.data.get("filter", False)
            include_sdr = self._parse_bool(request.data.get("include_sdr", True))
            last_id_raw = request.data.get("last_id")
            last_id     = ObjectId(last_id_raw) if last_id_raw else None

            params_for_cache = self._qs_params(request, seq_ids, include_sdr)
            cache_key        = self._cache_key(seq_ids, params_for_cache)
            meta_cache_key   = f"{cache_key}_meta"

            from_date    = parse_datetime(request.data.get("from_date")) if filtervalue else None
            to_date      = parse_datetime(request.data.get("to_date"))   if filtervalue else None
            min_duration = request.data.get("min_duration")              if filtervalue else None
            max_duration = request.data.get("max_duration")              if filtervalue else None
            collection   = _get_ipdr_collection()
            mongo_filter = _build_mongo_filter(seq_ids, from_date, to_date, min_duration, max_duration)

            cached_meta = self._cget(meta_cache_key)

            from concurrent.futures import ThreadPoolExecutor

            if cached_meta:
                nexus_map = cached_meta["nexus_map"]
                crime_map = cached_meta["crime_map"]
                docs, new_last_id = fetch_ipdr_page_pipeline(collection, mongo_filter, last_id, limit)
            else:
                _nexus_result = {}
                _crime_result = {}
                _page_result  = {}
                _errors       = []

                def _fetch_nexus_and_crime():
                    try:
                        nm = fetch_nexus_map_pipeline(seq_ids)
                        _nexus_result["data"] = nm
                        _crime_result["data"] = build_crime_map_pipeline(nm)
                    except Exception as e:
                        _errors.append(("nexus_crime", e))

                def _fetch_ipdr_page():
                    try:
                        d, nli = fetch_ipdr_page_pipeline(collection, mongo_filter, last_id, limit)
                        _page_result["docs"] = d
                        _page_result["new_last_id"] = nli
                    except Exception as e:
                        _errors.append(("ipdr_page", e))

                with ThreadPoolExecutor(max_workers=2) as ex:
                    ex.submit(_fetch_nexus_and_crime).result()
                    ex.submit(_fetch_ipdr_page).result()

                if _errors:
                    raise Exception(f"Parallel fetch failed: {_errors}")

                nexus_map   = _nexus_result.get("data", {})
                crime_map   = _crime_result.get("data", {})
                docs        = _page_result.get("docs", [])
                new_last_id = _page_result.get("new_last_id")

                if not nexus_map:
                    return Response({"error": "No IPDR Nexus records found.", "seq_ids_received": seq_ids}, status=404)

                self._cset(meta_cache_key, {"nexus_map": nexus_map, "crime_map": crime_map}, timeout=7200)

            count_info  = get_count_fast(collection, mongo_filter, cache_key, cache)
            total_db    = count_info["count"]
            count_ready = count_info["count_ready"]
            count_exact = count_info["count_exact"]
            total_pages = ((total_db + limit - 1) // limit) if total_db > 0 else -1

            if count_ready and total_db == 0:
                return Response({"page": page, "limit": limit, "total_records": 0, "total_pages": 0, "count": 0, "data": [], "export_data": [], "last_id": None}, status=200)

            enriched_filters = self._collect_filters(request)
            if any(enriched_filters.values()):
                return self._handle_enriched_filtering(request, collection, mongo_filter, nexus_map, crime_map, enriched_filters, page, limit, seq_ids, include_sdr, total_db, t_request)

            if not docs:
                return Response({"page": page, "limit": limit, "total_records": total_db, "total_pages": total_pages, "count": 0, "data": [], "export_data": [], "last_id": None, "count_ready": count_ready, "count_exact": count_exact}, status=200)

            mapped_records = ipdr_raw_page(docs, nexus_map, crime_map)
            bg_status      = self._cget(f"{cache_key}_{_STATUS_KEY}")
            export_ready   = False

            if bg_status in ("done", "pending"):
                cached_lookups = self._cget(f"{cache_key}_{_LOOKUPS_KEY}")
                if cached_lookups:
                    mapped_records = enrich_records(mapped_records, cached_lookups)
                    export_ready   = (bg_status == "done")
                else:
                    page_lookups   = build_enrichment_lookups(extract_page_keys(mapped_records), nexus_map, include_sdr)
                    mapped_records = enrich_records(mapped_records, page_lookups)
            else:
                page_lookups   = build_enrichment_lookups(extract_page_keys(mapped_records), nexus_map, include_sdr)
                mapped_records = enrich_records(mapped_records, page_lookups)
                self._cset(f"{cache_key}_{_LOOKUPS_KEY}", page_lookups, timeout=7200)
                self._cset(f"{cache_key}_{_STATUS_KEY}", "pending",     timeout=7200)
                self._start_background_key_scan(cache_key, collection, mongo_filter, nexus_map, crime_map, include_sdr, total_db)

            return Response({
                "page": page, "limit": limit,
                "total_records": total_db, "total_pages": total_pages,
                "count_ready": count_ready, "count_exact": count_exact,
                "count": len(mapped_records),
                "data": mapped_records, "export_data": [],
                "export_ready": export_ready,
                "last_id": str(new_last_id) if new_last_id else None,
                "enriched": export_ready or (bg_status == "pending" and bool(self._cget(f"{cache_key}_{_LOOKUPS_KEY}"))),
            }, status=200)

        except InvalidQueryError as e:
            return Response({"error": f"Invalid query: {str(e)}"}, status=400)
        except Exception as e:
            import traceback
            print(f"[{_now()}] Exception:\n{traceback.format_exc()}")
            return Response({"error": str(e)}, status=500)

    def _start_background_key_scan(self, cache_key, collection, mongo_filter, nexus_map, crime_map, include_sdr, total_db):
        status_key  = f"{cache_key}_{_STATUS_KEY}"
        lookups_key = f"{cache_key}_{_LOOKUPS_KEY}"
        self._cset(status_key, "pending", timeout=7200)

        def _worker():
            t0 = time.monotonic()
            try:
                keys    = scan_keys_pipeline(collection, mongo_filter, _BG_SCAN_CHUNK)
                lookups = build_enrichment_lookups(keys, nexus_map, include_sdr)
                self._cset(lookups_key, lookups, timeout=7200)
                self._cset(status_key, "done",   timeout=7200)
                print(f"[{_now()}] [BG] COMPLETE in {_elapsed(t0)}")
            except Exception as exc:
                import traceback
                print(f"[{_now()}] [BG] FAILED: {exc}\n{traceback.format_exc()}")
                self._cdel(status_key)

        threading.Thread(target=_worker, daemon=True).start()

    _MONGO_FILTER_MAP = {
        "msisdn": "MSISDN", "destination_ip": "Destination_ip",
        "destination_port": "Destination_port", "source_ip": "Source_ip",
        "source_port": "Source_port", "translated_ip": "Translated_ip",
        "translated_port": "Translated_port", "imei": "IMEI", "imsi": "IMSI",
        "imsi_mccmnc": "IMSI_CODE", "tower_id": "TowerID",
        "upload_data": "DataUpload", "download_data": "DataDownload",
        "contact_no": "Contact No", "name_of_person_org": "Name of Person/Organization",
    }

    _ENRICHED_FILTER_MAP = {
        "full_name": "FullName", "father_name": "FatherName", "local_address": "LocalAddress",
        "isp": "Isp/Org", "isp_org": "Isp/Org", "domain": "Domains", "usage": "Usage",
        "vpn_proxy_tor": "VPN/Proxy/Tor", "tsp_type": "TSP/Broadband/Satellite",
        "app_hostname": "App/Hostname", "location": "Location", "country": "Country",
        "ip_lat": "IPLat", "ip_long": "IPLong", "port_info": "Port Info",
        "port_category": "Port Category", "port_type": "Port Type",
        "tower_address": "TowerID Address", "main_city": "Main City(TowerID)",
        "sub_city": "Sub City(TowerID)", "lat_long_azimuth": "Lat-Long-Azimuth(TowerID)",
        "imei_manufacturer": "IMEI Manufacturer", "device_type": "Device Type",
        "imsi_circle": "IMSI Circle", "imsi_operator": "IMSI Operator",
        "crime": "Crime", "area_location": "AreaLocation",
        "roaming": "Roaming", "circle": "Circle", "operator": "Operator",
    }

    _FILTER_MAP = {
        **_ENRICHED_FILTER_MAP,
        "msisdn": "MSISDN", "destination_ip": "Destination IP",
        "destination_port": "Destination Port", "source_ip": "Source IP",
        "source_port": "Source Port", "translated_ip": "Translated IP",
        "translated_port": "Translated Port", "imei": "IMEI", "imsi": "IMSI",
        "imsi_mccmnc": "IMSI MccMnc", "tower_id": "TowerID",
        "upload_data": "Upload Data", "download_data": "Download Data",
        "contact_no": "Contact No", "name_of_person_org": "Name of Person/Organization",
    }

    def _collect_filters(self, request):
        def s(k): return str(request.data.get(k) or "").strip()
        keys = [
            "msisdn", "destination_ip", "destination_port", "source_ip", "source_port",
            "translated_ip", "translated_port", "imei", "imsi", "imsi_mccmnc", "tower_id",
            "upload_data", "download_data", "contact_no", "name_of_person_org",
            "full_name", "father_name", "local_address", "isp", "isp_org", "domain",
            "usage", "vpn_proxy_tor", "tsp_type", "app_hostname", "location", "country",
            "ip_lat", "ip_long", "port_info", "port_category", "port_type",
            "tower_address", "main_city", "sub_city", "lat_long_azimuth",
            "imei_manufacturer", "device_type", "imsi_circle", "imsi_operator",
            "crime", "area_location", "roaming", "circle", "operator",
        ]
        return {k: s(k) for k in keys}

    def _split_filters(self, active_filters):
        mongo_extra, enriched = {}, {}
        for fk, fv in active_filters.items():
            if fk in self._MONGO_FILTER_MAP:     mongo_extra[fk] = fv
            elif fk in self._ENRICHED_FILTER_MAP: enriched[fk]   = fv
        return mongo_extra, enriched

    _NUMERIC_MONGO_FIELDS = {"destination_port", "source_port", "translated_port", "upload_data", "download_data"}

    def _apply_mongo_filters(self, base_filter, mongo_extra):
        import re
        combined = dict(base_filter)
        for fk, fv in mongo_extra.items():
            field = self._MONGO_FILTER_MAP[fk]
            if fk in self._NUMERIC_MONGO_FIELDS:
                try:
                    combined[field] = int(fv)
                    continue
                except (ValueError, TypeError):
                    pass
            combined[field] = {"$regex": re.escape(str(fv)), "$options": "i"}
        return combined

    def _record_matches(self, record, active_filters, fmap):
        for fk, fv in active_filters.items():
            fn  = fmap.get(fk)
            fld = record.get(fn) if fn else None
            if fld is None or str(fv).lower() not in str(fld).lower():
                return False
        return True

    def _handle_enriched_filtering(self, request, collection, mongo_filter, nexus_map, crime_map, filters, page, limit, seq_ids, include_sdr, total_db, t_request):
        from ..ipdr_workspace.ipdr_mapping import _IPDR_PROJECTION

        params         = self._qs_params(request, seq_ids, include_sdr)
        cache_key      = self._cache_key(seq_ids, params)
        lookups_key    = f"{cache_key}_{_LOOKUPS_KEY}"
        status_key     = f"{cache_key}_{_STATUS_KEY}"
        active         = {k: v for k, v in filters.items() if v}
        mongo_extra, enriched_active = self._split_filters(active)
        filtered_mongo = self._apply_mongo_filters(mongo_filter, mongo_extra)

        lookups = self._cget(lookups_key)
        if not lookups:
            keys    = scan_keys_pipeline(collection, filtered_mongo, _BG_SCAN_CHUNK)
            lookups = build_enrichment_lookups(keys, nexus_map, include_sdr)
            self._cset(lookups_key, lookups, timeout=7200)
            self._cset(status_key, "done",   timeout=7200)

        pipeline   = [{"$match": filtered_mongo}, {"$project": {**_IPDR_PROJECTION, "_id": 0}}]
        batch_docs = []
        matched    = []

        for doc in collection.aggregate(pipeline, allowDiskUse=False, batchSize=_STREAM_BATCH):
            from ..ipdr_workspace.ipdr_mapping import _normalise_seq_id
            doc["seq_id"] = _normalise_seq_id(doc.get("seq_id"))
            for field in ("SDateTime", "EDateTime"):
                val = doc.get(field)
                if val and not isinstance(val, str):
                    doc[field] = val.isoformat()
            batch_docs.append(doc)
            if len(batch_docs) >= _STREAM_BATCH:
                enr = enrich_records(ipdr_raw_page(batch_docs, nexus_map, crime_map), lookups)
                matched.extend(r for r in enr if not enriched_active or self._record_matches(r, enriched_active, self._ENRICHED_FILTER_MAP))
                batch_docs = []

        if batch_docs:
            enr = enrich_records(ipdr_raw_page(batch_docs, nexus_map, crime_map), lookups)
            matched.extend(r for r in enr if not enriched_active or self._record_matches(r, enriched_active, self._ENRICHED_FILTER_MAP))

        total_matched = len(matched)
        if total_matched == 0:
            return Response({"page": page, "limit": limit, "total_records": 0, "total_pages": 0, "count": 0, "data": [], "export_data": [], "message": "No records match.", "applied_filters": active}, status=200)

        total_pages = max(1, (total_matched + limit - 1) // limit)
        page_data   = matched[(page - 1) * limit : page * limit]
        return Response({
            "page": page, "limit": limit,
            "total_records": total_matched, "total_pages": total_pages,
            "count": len(page_data), "data": page_data, "export_data": matched,
            "export_ready": True, "applied_filters": active,
            "mongo_filters": list(mongo_extra.keys()),
            "enriched_filters": list(enriched_active.keys()),
        }, status=200)

    def _apply_filters(self, records, filters):
        active = {k: v for k, v in filters.items() if v}
        return [r for r in records if self._record_matches(r, active, self._FILTER_MAP)]

    @staticmethod
    def _qs_params(request, seq_ids, include_sdr):
        return {
            "seq_ids": sorted(seq_ids), "from_date": request.data.get("from_date"),
            "to_date": request.data.get("to_date"), "min_duration": request.data.get("min_duration"),
            "max_duration": request.data.get("max_duration"), "include_sdr": include_sdr,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Count poll
# ─────────────────────────────────────────────────────────────────────────────

class IPDRCountPollView(APIView):
    @staticmethod
    def _cache_key(seq_ids, params):
        h = hashlib.md5(
            f"{'_'.join(sorted(map(str, seq_ids)))}_{json.dumps(params, sort_keys=True)}".encode()
        ).hexdigest()
        return f"ipdr_{h}"

    def post(self, request):
        seq_ids     = request.data.get("seq_ids", [])
        include_sdr = IPDRRecordDetailView._parse_bool(request.data.get("include_sdr", True))
        params = {
            "seq_ids": sorted(seq_ids), "from_date": request.data.get("from_date"),
            "to_date": request.data.get("to_date"), "min_duration": request.data.get("min_duration"),
            "max_duration": request.data.get("max_duration"), "include_sdr": include_sdr,
        }
        cache_key    = self._cache_key(seq_ids, params)
        cached_count = cache.get(f"{cache_key}_count")
        if cached_count is None:
            return Response({"total_records": -1, "total_pages": -1, "count_ready": False, "count_exact": False}, status=200)

        limit       = min(int(request.data.get("limit", 500)), 500)
        is_exact    = bool(cache.get(f"{cache_key}_count_exact"))
        total_pages = (cached_count + limit - 1) // limit if cached_count > 0 else 0
        return Response({"total_records": cached_count, "total_pages": total_pages, "count_ready": True, "count_exact": is_exact}, status=200)


# ─────────────────────────────────────────────────────────────────────────────
# Export column order
# ─────────────────────────────────────────────────────────────────────────────

_EXPORT_COLUMNS = [
    "IPDR", "MSISDN", "FullName", "FatherName", "LocalAddress",
    "Destination IP", "Isp/Org", "Domains", "Usage", "VPN/Proxy/Tor",
    "TSP/Broadband/Satellite", "App/Hostname", "Location", "Country",
    "IPLat", "IPLong",
    "Destination Port", "Port Info", "Port Category", "Port Type",
    "Session Start Date", "Session Start Time",
    "Session End Date", "Session End Time",
    "Duration", "Session Timespan",
    "TowerID", "TowerID Address", "Main City(TowerID)", "Sub City(TowerID)",
    "Lat-Long-Azimuth(TowerID)",
    "IMEI", "IMEI Manufacturer", "Device Type",
    "IMSI", "IMSI MccMnc", "IMSI Circle", "IMSI Operator",
    "Upload Data", "Download Data",
    "Source IP", "Source Port", "Translated IP", "Translated Port",
    "Crime", "AreaLocation", "Roaming", "Circle", "Operator",
    "Contact No", "Name of Person/Organization",
]


# ─────────────────────────────────────────────────────────────────────────────
# CSV builder  — ~1-2s for 5L records
#
# csv.writer writes plain text rows in C with zero object overhead.
# UTF-8 BOM prefix ensures Excel opens it correctly without an import wizard.
# ─────────────────────────────────────────────────────────────────────────────

def _build_csv_file(records: list, active_filters: dict) -> bytes:
    import csv
    import io
    from datetime import datetime as _dt

    buf    = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)

    # Metadata header block
    writer.writerow(["IPDR Export"])
    writer.writerow(["Generated At", _dt.now().strftime("%Y-%m-%d %H:%M:%S")])
    writer.writerow(["Total Records", len(records)])
    if active_filters:
        writer.writerow(["Applied Filters", " | ".join(f"{k}: {v}" for k, v in active_filters.items())])
    writer.writerow([])  # blank separator before data

    # Column header
    writer.writerow(_EXPORT_COLUMNS)

    # Data rows — single list comprehension per row, no per-cell overhead
    for record in records:
        writer.writerow([record.get(col, "") for col in _EXPORT_COLUMNS])

    # UTF-8 BOM so Excel auto-detects encoding
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# XLSX builder — xlsxwriter, ~4-8s for 5L records
#
# xlsxwriter streams rows to a zip buffer using C extensions.
# Format objects are registered once and referenced by index — no per-cell
# style object creation. write_row() is a single C call per row.
#
# Requires:  pip install xlsxwriter
# ─────────────────────────────────────────────────────────────────────────────

def _build_xlsx_file(records: list, active_filters: dict) -> bytes:
    import xlsxwriter
    import io
    from datetime import datetime as _dt

    buf = io.BytesIO()
    wb  = xlsxwriter.Workbook(buf, {
        "in_memory":       True,   # keep buffer in RAM — fastest for server responses
        "strings_to_urls": False,  # skip URL detection — saves ~20% on string-heavy cols
    })

    # ── Register formats once — reused by index, never recreated per cell ─────
    hdr_fmt  = wb.add_format({
        "bold": True, "font_name": "Arial", "font_size": 10,
        "font_color": "#FFFFFF", "bg_color": "#1F3864",
        "align": "center", "valign": "vcenter",
        "border": 1, "border_color": "#CCCCCC", "text_wrap": True,
    })
    even_fmt = wb.add_format({
        "font_name": "Arial", "font_size": 9, "bg_color": "#EAF0FB",
        "valign": "vcenter", "border": 1, "border_color": "#CCCCCC",
    })
    odd_fmt  = wb.add_format({
        "font_name": "Arial", "font_size": 9, "bg_color": "#FFFFFF",
        "valign": "vcenter", "border": 1, "border_color": "#CCCCCC",
    })

    # ── Sheet 1: IPDR Data ────────────────────────────────────────────────────
    ws = wb.add_worksheet("IPDR Data")
    ws.freeze_panes(1, 0)
    ws.autofilter(0, 0, 0, len(_EXPORT_COLUMNS) - 1)
    ws.set_row(0, 30)  # header row height

    # Column widths from header labels only — O(cols), eliminates O(rows×cols) scan
    for ci, col in enumerate(_EXPORT_COLUMNS):
        ws.set_column(ci, ci, min(len(col) + 4, 40))

    # Header in one C call
    ws.write_row(0, 0, _EXPORT_COLUMNS, hdr_fmt)

    # Data rows — write_row() is a single C call per row
    for ri, record in enumerate(records, start=1):
        ws.write_row(ri, 0, [record.get(col, "") for col in _EXPORT_COLUMNS],
                     even_fmt if ri % 2 == 0 else odd_fmt)

    # ── Sheet 2: Summary ──────────────────────────────────────────────────────
    ws2       = wb.add_worksheet("Summary")
    title_fmt = wb.add_format({"bold": True, "font_name": "Arial", "font_size": 13, "font_color": "#1F3864"})
    label_fmt = wb.add_format({"bold": True, "font_name": "Arial", "font_size": 10})
    value_fmt = wb.add_format({"font_name": "Arial", "font_size": 10})
    sect_fmt  = wb.add_format({"bold": True, "font_name": "Arial", "font_size": 10, "bg_color": "#D6E4F7"})

    ws2.set_column(0, 0, 28)
    ws2.set_column(1, 1, 55)
    ws2.merge_range("A1:B1", "IPDR Export Summary", title_fmt)

    summary_rows = [
        ("Generated At",    _dt.now().strftime("%Y-%m-%d %H:%M:%S"), label_fmt, value_fmt),
        ("Total Records",   len(records),                            label_fmt, value_fmt),
        ("",                "",                                      label_fmt, value_fmt),
        ("Applied Filters", "",                                      sect_fmt,  sect_fmt),
    ] + [(f"  {k}", str(v), label_fmt, value_fmt) for k, v in active_filters.items()]

    for ri, (label, value, lf, vf) in enumerate(summary_rows, start=1):
        ws2.write(ri, 0, label, lf)
        ws2.write(ri, 1, value, vf)

    wb.close()
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────────────────────────────────────
# PDF builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_pdf_file(records: list, active_filters: dict) -> bytes:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import ParagraphStyle
    from datetime import datetime as _dt
    import io

    PDF_COLS = [
        "MSISDN", "FullName", "Destination IP", "Isp/Org", "Country",
        "Destination Port", "Port Info", "Session Start Date", "Session Start Time",
        "Duration", "TowerID", "IMEI", "IMSI", "Circle", "Operator",
        "Source IP", "Crime",
    ]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=10*mm, rightMargin=10*mm,
                            topMargin=15*mm, bottomMargin=15*mm, title="IPDR Export")

    TITLE_S = ParagraphStyle("t", fontSize=14, fontName="Helvetica-Bold", spaceAfter=4, textColor=colors.HexColor("#1F3864"))
    SUB_S   = ParagraphStyle("s", fontSize=8,  fontName="Helvetica",      spaceAfter=3, textColor=colors.grey)
    CELL_S  = ParagraphStyle("c", fontSize=6.5, fontName="Helvetica",     leading=9)

    story = [
        Paragraph("IPDR Export Report", TITLE_S),
        Paragraph(f"Generated: {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}  |  Total Records: {len(records)}", SUB_S),
    ]
    if active_filters:
        story.append(Paragraph("Filters: " + "  |  ".join(f"{k}: {v}" for k, v in active_filters.items()), SUB_S))
    story.append(Spacer(1, 6*mm))

    hdr   = [Paragraph(f"<b>{c}</b>", CELL_S) for c in PDF_COLS]
    tdata = [hdr] + [[Paragraph(str(r.get(c) or ""), CELL_S) for c in PDF_COLS] for r in records]
    tbl   = Table(tdata, colWidths=[doc.width / len(PDF_COLS)] * len(PDF_COLS), repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  colors.HexColor("#1F3864")),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor("#EAF0FB")]),
        ("FONTSIZE",      (0, 0), (-1, -1), 6.5),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#CCCCCC")),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(tbl)
    doc.build(story)
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────────────────────────────────────
# IPDRExportView
#
# POST body: { "format": "xlsx" | "csv" | "pdf", "seq_ids": [...], ... }
#
# Format     Library       ~Time for 5L rows   Notes
# ─────────  ────────────  ──────────────────  ───────────────────────────────
# csv        stdlib csv    1-2s                Plain text, opens in Excel fine
# xlsx       xlsxwriter    4-8s                Styled Excel, freeze/autofilter
# pdf        reportlab     varies              Landscape, key columns only
#
# pip install xlsxwriter reportlab
# ─────────────────────────────────────────────────────────────────────────────

from django.http import HttpResponse as _HttpResponse


class IPDRExportView(IPDRRecordDetailView):

    _FORMAT_CONFIG = {
        "xlsx": {
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "extension":    "xlsx",
            "builder":      _build_xlsx_file,
        },
        "csv": {
            "content_type": "text/csv; charset=utf-8",
            "extension":    "csv",
            "builder":      _build_csv_file,
        },
        "pdf": {
            "content_type": "application/pdf",
            "extension":    "pdf",
            "builder":      _build_pdf_file,
        },
    }

    def post(self, request):
        t_request = time.monotonic()
        try:
            fmt = request.data.get("format", "xlsx").lower().strip()
            if fmt not in self._FORMAT_CONFIG:
                return Response(
                    {"error": f"format must be one of: {', '.join(self._FORMAT_CONFIG)}"},
                    status=400,
                )

            seq_ids = request.data.get("seq_ids", [])
            if not seq_ids or not isinstance(seq_ids, list):
                return Response({"error": "seq_ids must be a non-empty list"}, status=400)

            include_sdr = self._parse_bool(request.data.get("include_sdr", True))
            filtervalue = request.data.get("filter", False)

            print(f"\n[{_now()}] {'='*60}")
            print(f"[{_now()}] EXPORT REQUEST  format={fmt}  seq_ids={seq_ids}")
            print(f"[{_now()}] {'='*60}")

            from_date    = parse_datetime(request.data.get("from_date")) if filtervalue else None
            to_date      = parse_datetime(request.data.get("to_date"))   if filtervalue else None
            min_duration = request.data.get("min_duration")              if filtervalue else None
            max_duration = request.data.get("max_duration")              if filtervalue else None

            collection   = _get_ipdr_collection()
            mongo_filter = _build_mongo_filter(seq_ids, from_date, to_date, min_duration, max_duration)

            params_for_cache = self._qs_params(request, seq_ids, include_sdr)
            cache_key        = self._cache_key(seq_ids, params_for_cache)
            meta_cache_key   = f"{cache_key}_meta"
            lookups_key      = f"{cache_key}_{_LOOKUPS_KEY}"
            status_key       = f"{cache_key}_{_STATUS_KEY}"

            # ── nexus + crime ─────────────────────────────────────────────────
            cached_meta = self._cget(meta_cache_key)
            if cached_meta:
                nexus_map = cached_meta["nexus_map"]
                crime_map = cached_meta["crime_map"]
                print(f"[{_now()}] [EXPORT] Meta from cache")
            else:
                nexus_map = fetch_nexus_map_pipeline(seq_ids)
                if not nexus_map:
                    return Response({
                        "error": "No IPDR Nexus records found for the given seq_ids.",
                        "seq_ids_received": seq_ids,
                        "hint": "Check server console for SAMPLE _id log lines.",
                    }, status=404)
                crime_map = build_crime_map_pipeline(nexus_map)
                self._cset(meta_cache_key, {"nexus_map": nexus_map, "crime_map": crime_map}, timeout=7200)

            # ── Filters ───────────────────────────────────────────────────────
            all_filters                  = self._collect_filters(request)
            active                       = {k: v for k, v in all_filters.items() if v}
            mongo_extra, enriched_active = self._split_filters(active)
            filtered_mongo               = self._apply_mongo_filters(mongo_filter, mongo_extra)

            print(f"[{_now()}] [EXPORT] mongo={list(mongo_extra.keys())} enriched={list(enriched_active.keys())}")

            # ── Lookup tables ─────────────────────────────────────────────────
            lookups = self._cget(lookups_key)
            if not lookups:
                t_scan  = time.monotonic()
                keys    = scan_keys_pipeline(collection, filtered_mongo, _BG_SCAN_CHUNK)
                lookups = build_enrichment_lookups(keys, nexus_map, include_sdr)
                self._cset(lookups_key, lookups, timeout=7200)
                self._cset(status_key, "done",   timeout=7200)
                print(f"[{_now()}] [EXPORT] Lookups built in {_elapsed(t_scan)}")
            else:
                print(f"[{_now()}] [EXPORT] Lookups from cache")

            # ── Stream + enrich ───────────────────────────────────────────────
            from ..ipdr_workspace.ipdr_mapping import _IPDR_PROJECTION, _normalise_seq_id

            pipeline    = [{"$match": filtered_mongo}, {"$project": {**_IPDR_PROJECTION, "_id": 0}}]
            batch_docs  = []
            all_records = []
            streamed    = 0
            t_stream    = time.monotonic()

            for doc in collection.aggregate(pipeline, allowDiskUse=True, batchSize=_STREAM_BATCH):
                doc["seq_id"] = _normalise_seq_id(doc.get("seq_id"))
                for field in ("SDateTime", "EDateTime"):
                    val = doc.get(field)
                    if val and not isinstance(val, str):
                        doc[field] = val.isoformat()
                batch_docs.append(doc)
                streamed += 1

                if len(batch_docs) >= _STREAM_BATCH:
                    enr = enrich_records(ipdr_raw_page(batch_docs, nexus_map, crime_map), lookups)
                    all_records.extend(
                        r for r in enr
                        if not enriched_active or self._record_matches(r, enriched_active, self._ENRICHED_FILTER_MAP)
                    )
                    batch_docs = []
                    print(f"[{_now()}] [EXPORT]   streamed={streamed} matched={len(all_records)} — {_elapsed(t_stream)}")

            if batch_docs:
                enr = enrich_records(ipdr_raw_page(batch_docs, nexus_map, crime_map), lookups)
                all_records.extend(
                    r for r in enr
                    if not enriched_active or self._record_matches(r, enriched_active, self._ENRICHED_FILTER_MAP)
                )

            total = len(all_records)
            print(f"[{_now()}] [EXPORT] Streamed={streamed} matched={total} in {_elapsed(t_stream)}")

            if total == 0:
                return Response({"total_records": 0, "message": "No records match.", "applied_filters": active}, status=200)

            # ── Build file ────────────────────────────────────────────────────
            from datetime import datetime as _dt
            timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")
            cfg       = self._FORMAT_CONFIG[fmt]

            print(f"[{_now()}] [EXPORT] Building {fmt.upper()} — {total} rows...")
            t_build = time.monotonic()
            raw     = cfg["builder"](all_records, active)
            print(f"[{_now()}] [EXPORT] {fmt.upper()} ready {len(raw)//1024}KB in {_elapsed(t_build)}")

            http_resp = _HttpResponse(raw, content_type=cfg["content_type"])
            http_resp["Content-Disposition"] = f'attachment; filename="ipdr_export_{timestamp}.{cfg["extension"]}"'
            http_resp["X-Total-Records"]      = str(total)
            http_resp["X-Applied-Filters"]    = ",".join(active.keys())
            http_resp["X-Elapsed-Ms"]         = str(round((time.monotonic() - t_request) * 1000, 1))

            print(f"[{_now()}] [EXPORT] DONE  total={_elapsed(t_request)}")
            return http_resp

        except InvalidQueryError as e:
            return Response({"error": f"Invalid query: {str(e)}"}, status=400)
        except Exception as e:
            import traceback
            print(f"[{_now()}] [EXPORT] Exception:\n{traceback.format_exc()}")
            return Response({"error": str(e)}, status=500)