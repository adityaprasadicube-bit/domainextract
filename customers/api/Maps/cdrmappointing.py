from collections import defaultdict
from datetime import datetime

from mongoengine import get_db
from rest_framework.views import APIView
from rest_framework.response import Response

from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from ..models import CallDetailRecord, CellTower
from ..searchengine import search_ip
from ..serializers import CellTowerSerializer


# ============================================================
# API 1: MAP PLOT
# ============================================================
from collections import defaultdict
from rest_framework.views import APIView
from rest_framework.response import Response
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

class CDRMappingReportView(APIView):

    @swagger_auto_schema(
        operation_summary="CDR/IPDR Map Plot (Lightweight - Tower coordinates only)",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                "seq_ids": openapi.Schema(
                    type=openapi.TYPE_ARRAY,
                    items=openapi.Items(type=openapi.TYPE_STRING),
                    description="Used when filter_value is 'cdr' or 'ipdr'"
                ),
                "cdrseq_ids": openapi.Schema(
                    type=openapi.TYPE_ARRAY,
                    items=openapi.Items(type=openapi.TYPE_STRING),
                    description="CDR seq_ids — used when filter_value is 'both'"
                ),
                "ipdrseq_ids": openapi.Schema(
                    type=openapi.TYPE_ARRAY,
                    items=openapi.Items(type=openapi.TYPE_STRING),
                    description="IPDR seq_ids — used when filter_value is 'both'"
                ),
                "filter_value": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="'cdr', 'ipdr', or 'both'"
                ),
                "ipdr_type": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="'towerid' or 'ip'"
                ),
            },
        )
    )
    def post(self, request):
        try:
            filter_value = request.data.get("filter_value")
            ipdr_type = request.data.get("ipdr_type", "towerid")

            # ------------------- CDR DATA -------------------
            def get_cdr_plot_data(seq_ids):

                cdr_rows = CallDetailRecord.objects.filter(
                    seq_id__in=seq_ids
                ).only(
                    "First_CGI",
                    "Call_Type",
                    "A_Party",
                    "SDateTime",
                    "EDateTime"
                )

                if not cdr_rows:
                    return {}

                party_tower_stats = defaultdict(
                    lambda: defaultdict(
                        lambda: {
                            "total": 0,
                            "Call_IN": 0,
                            "Call_OUT": 0,
                            "SMS_IN": 0,
                            "SMS_OUT": 0,
                            "records": []
                        }
                    )
                )

                all_cgis = set()

                for row in cdr_rows:

                    cgi = row.First_CGI
                    a_party = row.A_Party
                    s_time = row.SDateTime
                    e_time = row.EDateTime

                    if not cgi or not a_party:
                        continue

                    all_cgis.add(cgi)

                    call_type = (row.Call_Type or "").upper()

                    party_tower_stats[a_party][cgi]["total"] += 1

                    if call_type == "SMS_IN":
                        party_tower_stats[a_party][cgi]["SMS_IN"] += 1

                    elif call_type == "SMS_OUT":
                        party_tower_stats[a_party][cgi]["SMS_OUT"] += 1

                    elif "CALL_IN" in call_type:
                        party_tower_stats[a_party][cgi]["Call_IN"] += 1

                    elif "CALL_OUT" in call_type:
                        party_tower_stats[a_party][cgi]["Call_OUT"] += 1

                    # movement records
                    party_tower_stats[a_party][cgi]["records"].append({
                        "SDateTime": s_time,
                        "EDateTime": e_time
                    })

                towers = CellTower.objects.filter(id__in=list(all_cgis))
                tower_data = CellTowerSerializer(towers, many=True).data
                tower_lookup = {item["id"]: item for item in tower_data}

                result = {}

                for a_party, tower_stats in party_tower_stats.items():

                    towers_list = {}

                    for cgi, stat in tower_stats.items():

                        tower = tower_lookup.get(cgi)

                        if not tower:
                            continue

                        towers_list[cgi] = {
                            "Latitude": tower.get("LATITUDE", ""),
                            "Longitude": tower.get("LONGITUDE", ""),
                            "Call_IN": stat["Call_IN"],
                            "Call_OUT": stat["Call_OUT"],
                            "SMS_IN": stat["SMS_IN"],
                            "SMS_OUT": stat["SMS_OUT"],
                            "total": stat["total"],
                            "records": stat["records"]  # <-- added
                        }

                    if towers_list:
                        result[a_party] = {"towers": towers_list}

                return result

            # ------------------- IPDR DATA -------------------
            def get_ipdr_plot_data(seq_ids, ipdr_type="towerid"):

                db = get_db("ipdr_db")
                nexus_collection = db["IPdrNexus"]
                records_collection = db["IPDetailRecords"]

                clean_seq_ids = []

                for s in seq_ids:
                    if isinstance(s, list):
                        clean_seq_ids.extend([str(x) for x in s])
                    else:
                        clean_seq_ids.append(str(s))

                if not clean_seq_ids:
                    return {}

                seq_to_ipdr = {}

                for seq_id in clean_seq_ids:
                    nexus = nexus_collection.find_one({"_id": seq_id})

                    if nexus:
                        ipdr_val = nexus.get("IPDR", "Unknown")

                        if isinstance(ipdr_val, list):
                            ipdr_val = ipdr_val[0] if ipdr_val else "Unknown"

                        seq_to_ipdr[seq_id] = str(ipdr_val)

                if not seq_to_ipdr:
                    return {}

                ipdr_to_seq_ids = defaultdict(list)

                for seq_id, ipdr_val in seq_to_ipdr.items():
                    ipdr_to_seq_ids[ipdr_val].append(seq_id)

                # ----------- TOWER ID MODE -----------
                if ipdr_type == "towerid":

                    all_tower_ids = set()
                    ipdr_tower_totals = defaultdict(lambda: defaultdict(int))

                    for ipdr_val, s_ids in ipdr_to_seq_ids.items():

                        pipeline = [
                            {"$match": {"seq_id": {"$in": s_ids}}},
                            {"$group": {"_id": "$TowerID", "total": {"$sum": 1}}}
                        ]

                        results = list(records_collection.aggregate(pipeline))

                        for row in results:

                            tower_id = row["_id"]

                            if tower_id is None:
                                continue

                            if isinstance(tower_id, list):
                                tower_id = tower_id[0] if tower_id else None

                            if not tower_id:
                                continue

                            tower_id = str(tower_id)

                            all_tower_ids.add(tower_id)

                            ipdr_tower_totals[ipdr_val][tower_id] += row["total"]

                    if not all_tower_ids:
                        return {}

                    towers = CellTower.objects.filter(id__in=list(all_tower_ids))
                    tower_data = CellTowerSerializer(towers, many=True).data
                    tower_lookup = {item["id"]: item for item in tower_data}

                    result = {}

                    for ipdr_val, tower_totals in ipdr_tower_totals.items():

                        towers_list = {}

                        for tower_id, total in tower_totals.items():

                            tower = tower_lookup.get(tower_id)

                            if not tower:
                                continue

                            towers_list[tower_id] = {
                                "Latitude": tower.get("LATITUDE", ""),
                                "Longitude": tower.get("LONGITUDE", ""),
                                "total": total,
                            }

                        if towers_list:
                            result[ipdr_val] = {"towers": towers_list}

                    return result

                return {}

            # ------------------- ROUTING -------------------

            if filter_value == "cdr":

                seq_ids = request.data.get("seq_ids", [])

                if not seq_ids:
                    return Response({"error": "seq_ids required"}, status=400)

                data = get_cdr_plot_data(seq_ids)

                if not data:
                    return Response({"error": "No CDR records found"}, status=404)

                return Response({"data": data})

            elif filter_value == "ipdr":

                seq_ids = request.data.get("seq_ids", [])

                if not seq_ids:
                    return Response({"error": "seq_ids required"}, status=400)

                data = get_ipdr_plot_data(seq_ids, ipdr_type)

                if not data:
                    return Response({"error": "No IPDR records found"}, status=404)

                return Response({"data": data})

            elif filter_value == "both":

                cdr_seq_ids = request.data.get("cdrseq_ids", [])
                ipdr_seq_ids = request.data.get("ipdrseq_ids", [])

                if not cdr_seq_ids and not ipdr_seq_ids:
                    return Response(
                        {"error": "At least one of cdrseq_ids or ipdrseq_ids must be provided"},
                        status=400
                    )

                response_data = {"cdr": {}, "ipdr": {}}

                if cdr_seq_ids:
                    response_data["cdr"] = get_cdr_plot_data(cdr_seq_ids)

                if ipdr_seq_ids:
                    response_data["ipdr"] = get_ipdr_plot_data(ipdr_seq_ids, ipdr_type)

                if not response_data["cdr"] and not response_data["ipdr"]:
                    return Response({"error": "No records found"}, status=404)

                return Response({"data": response_data})

            else:
                return Response(
                    {"error": "Invalid filter_value. Must be 'cdr', 'ipdr', or 'both'."},
                    status=400
                )

        except Exception as e:
            return Response({"error": str(e)}, status=400)

# ============================================================
# API 2: DETAIL — Full records when user clicks a marker
# Handles: CDR tower, IPDR tower, IPDR IP
# ============================================================
class CDRTowerDetailView(APIView):

    @swagger_auto_schema(
        operation_summary="CDR/IPDR Detail (Full records for clicked marker)",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["seq_ids", "filter_value"],
            properties={
                "tower_id": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="Tower ID — required when ipdr_type is 'towerid' or filter_value is 'cdr'"
                ),
                "dest_ip": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="Destination IP — required when ipdr_type is 'ip'"
                ),
                "seq_ids": openapi.Schema(
                    type=openapi.TYPE_ARRAY,
                    items=openapi.Items(type=openapi.TYPE_STRING),
                    description="The original seq_ids to scope the records"
                ),
                "filter_value": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="'cdr' or 'ipdr'"
                ),
                "ipdr_type": openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="'towerid' or 'ip' — used when filter_value is 'ipdr'"
                ),
            },
        )
    )
    def post(self, request):
        try:
            seq_ids = request.data.get("seq_ids", [])
            filter_value = request.data.get("filter_value")
            tower_id = request.data.get("tower_id")
            dest_ip = request.data.get("dest_ip")
            ipdr_type = request.data.get("ipdr_type", "towerid")

            if not seq_ids:
                return Response({"error": "seq_ids is required"}, status=400)
            if not filter_value:
                return Response({"error": "filter_value is required"}, status=400)

            # ----------------------------------------------------------------
            # CDR DETAIL
            # ----------------------------------------------------------------
            if filter_value == "cdr":
                if not tower_id:
                    return Response({"error": "tower_id is required for cdr"}, status=400)

                cdr_rows = CallDetailRecord.objects.filter(
                    seq_id__in=seq_ids,
                    First_CGI=tower_id
                ).order_by("SDateTime")

                if not cdr_rows:
                    return Response({"error": "No CDR records found for this tower"}, status=404)

                try:
                    tower = CellTower.objects.get(id=tower_id)
                    tower_info = CellTowerSerializer(tower).data
                except CellTower.DoesNotExist:
                    tower_info = {}

                records = []
                for row in cdr_rows:
                    sdt = row.SDateTime
                    if isinstance(sdt, str):
                        try:
                            sdt = datetime.strptime(sdt, "%Y-%m-%dT%H:%M:%SZ")
                        except ValueError:
                            sdt = None

                    records.append({
                        "CDRNo": row.A_Party or "",
                        "B Party": row.B_Party or "",
                        "Date": sdt.date().strftime("%Y-%m-%d") if sdt else "",
                        "Time": sdt.time().strftime("%H:%M:%S") if sdt else "",
                        "Duration": row.Duration or 0,
                        "Call Type": row.Call_Type or "",
                        "First Cell ID": row.First_CGI or "",
                        "Last Cell ID": row.Last_CGI or "",
                        "IMEI": row.IMEI or "",
                        "IMSI": row.IMSI or "",
                        "Con Type": row.Con_Type or "",
                        "LRN": row.LRN or "",
                    })

                return Response({
                    "tower": {
                        "tower_id": tower_id,
                        "Latitude": tower_info.get("LATITUDE", ""),
                        "Longitude": tower_info.get("LONGITUDE", ""),
                        "Address": tower_info.get("ADDRESS", ""),
                        "Circle": tower_info.get("CIRCLE", ""),
                        "Operator": tower_info.get("OPERATOR", ""),
                        "Azimuth": tower_info.get("AZIMUTH", ""),
                    },
                    "total": len(records),
                    "records": records
                })

            # ----------------------------------------------------------------
            # IPDR DETAIL
            # ----------------------------------------------------------------
            elif filter_value == "ipdr":

                db = get_db("ipdr_db")
                records_collection = db["IPDetailRecords"]

                # ---- TOWER BASED ----
                if ipdr_type == "towerid":
                    if not tower_id:
                        return Response({"error": "tower_id is required when ipdr_type is 'towerid'"}, status=400)

                    ipdrs = list(
                        records_collection.find({
                            "seq_id": {"$in": seq_ids},
                            "TowerID": tower_id
                        }).sort("SDateTime", 1)
                    )

                    if not ipdrs:
                        return Response({"error": "No IPDR records found for this tower"}, status=404)

                    try:
                        tower = CellTower.objects.get(id=tower_id)
                        tower_info = CellTowerSerializer(tower).data
                    except CellTower.DoesNotExist:
                        tower_info = {}

                    records = []
                    for record in ipdrs:
                        sdt = record.get("SDateTime")
                        edt = record.get("EDateTime")

                        if isinstance(sdt, dict):
                            sdt = datetime.strptime(sdt["$date"], "%Y-%m-%dT%H:%M:%S.%fZ")
                        elif isinstance(sdt, str):
                            try:
                                sdt = datetime.strptime(sdt, "%Y-%m-%dT%H:%M:%S.%fZ")
                            except ValueError:
                                sdt = None

                        if isinstance(edt, dict):
                            edt = datetime.strptime(edt["$date"], "%Y-%m-%dT%H:%M:%S.%fZ")
                        elif isinstance(edt, str):
                            try:
                                edt = datetime.strptime(edt, "%Y-%m-%dT%H:%M:%S.%fZ")
                            except ValueError:
                                edt = None

                        records.append({
                            "IMEI": record.get("IMEI", ""),
                            "IMSI": record.get("IMSI", ""),
                            "MSISDN": record.get("MSISDN", ""),
                            "DataDownload": record.get("DataDownload", ""),
                            "DataUpload": record.get("DataUpload", ""),
                            "Translated_IP": record.get("Translated_ip", ""),
                            "Translated_Port": record.get("Translated_port", ""),
                            "Destination_IP": record.get("Destination_ip", ""),
                            "Destination_Port": record.get("Destination_port", ""),
                            "Source_Port": record.get("Source_port", ""),
                            "Duration": record.get("Duration", 0),
                            "Date": sdt.date().strftime("%Y-%m-%d") if sdt else "",
                            "Start Time": sdt.time().strftime("%H:%M:%S") if sdt else "",
                            "End Time": edt.time().strftime("%H:%M:%S") if edt else "",
                            "IMEI_TAC": record.get("IMEI_TAC", ""),
                            "IMSI_CODE": record.get("IMSI_CODE", ""),
                        })

                    return Response({
                        "tower": {
                            "tower_id": tower_id,
                            "Latitude": tower_info.get("LATITUDE", ""),
                            "Longitude": tower_info.get("LONGITUDE", ""),
                            "Address": tower_info.get("ADDRESS", ""),
                            "Circle": tower_info.get("CIRCLE", ""),
                            "Operator": tower_info.get("OPERATOR", ""),
                            "Azimuth": tower_info.get("AZIMUTH", ""),
                        },
                        "total": len(records),
                        "records": records
                    })

                # ---- IP BASED ----
                elif ipdr_type == "ip":
                    if not dest_ip:
                        return Response({"error": "dest_ip is required when ipdr_type is 'ip'"}, status=400)

                    ipdrs = list(
                        records_collection.find({
                            "seq_id": {"$in": seq_ids},
                            "Destination_ip": dest_ip
                        }).sort("SDateTime", 1)
                    )

                    if not ipdrs:
                        return Response({"error": "No IPDR records found for this IP"}, status=404)

                    # Fetch IP info
                    ip_info = {}
                    try:
                        ip_result = search_ip([dest_ip])
                        results = ip_result.get("results", [])
                        if results:
                            ip_info = results[0]
                    except Exception:
                        pass

                    records = []
                    for record in ipdrs:
                        sdt = record.get("SDateTime")
                        edt = record.get("EDateTime")

                        if isinstance(sdt, dict):
                            sdt = datetime.strptime(sdt["$date"], "%Y-%m-%dT%H:%M:%S.%fZ")
                        elif isinstance(sdt, str):
                            try:
                                sdt = datetime.strptime(sdt, "%Y-%m-%dT%H:%M:%S.%fZ")
                            except ValueError:
                                sdt = None

                        if isinstance(edt, dict):
                            edt = datetime.strptime(edt["$date"], "%Y-%m-%dT%H:%M:%S.%fZ")
                        elif isinstance(edt, str):
                            try:
                                edt = datetime.strptime(edt, "%Y-%m-%dT%H:%M:%S.%fZ")
                            except ValueError:
                                edt = None

                        records.append({
                            "IMEI": record.get("IMEI", ""),
                            "IMSI": record.get("IMSI", ""),
                            "MSISDN": record.get("MSISDN", ""),
                            "DataDownload": record.get("DataDownload", ""),
                            "DataUpload": record.get("DataUpload", ""),
                            "Translated_IP": record.get("Translated_ip", ""),
                            "Translated_Port": record.get("Translated_port", ""),
                            "Destination_IP": record.get("Destination_ip", ""),
                            "Destination_Port": record.get("Destination_port", ""),
                            "Source_Port": record.get("Source_port", ""),
                            "Duration": record.get("Duration", 0),
                            "Date": sdt.date().strftime("%Y-%m-%d") if sdt else "",
                            "Start Time": sdt.time().strftime("%H:%M:%S") if sdt else "",
                            "End Time": edt.time().strftime("%H:%M:%S") if edt else "",
                            "IMEI_TAC": record.get("IMEI_TAC", ""),
                            "IMSI_CODE": record.get("IMSI_CODE", ""),
                        })

                    return Response({
                        "ip_info": {
                            "dest_ip": dest_ip,
                            "Latitude": ip_info.get("IPLat", ""),
                            "Longitude": ip_info.get("IPLong", ""),
                            "ISP": ip_info.get("Isp/Org", ""),
                            "Domains": ip_info.get("Domains", ""),
                            "Country": ip_info.get("Country", ""),
                            "VPN_Proxy_Tor": ip_info.get("VPN/Proxy/Tor", ""),
                        },
                        "total": len(records),
                        "records": records
                    })

                else:
                    return Response({"error": "ipdr_type must be 'towerid' or 'ip'"}, status=400)

            else:
                return Response({"error": "Invalid filter_value. Must be 'cdr' or 'ipdr'."}, status=400)

        except Exception as e:
            return Response({"error": str(e)}, status=400)