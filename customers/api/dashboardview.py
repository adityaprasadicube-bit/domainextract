import json
import os


from mongoengine import get_db
from rest_framework.response import Response
from rest_framework.views import APIView
from torch.xpu import device, device_count

from .searchengine import search_ip


from mongoengine import get_db
from rest_framework.response import Response
from rest_framework.views import APIView
from collections import Counter
from datetime import timedelta


class DashBoardApi(APIView):
    def post(self, request):
        db = get_db(alias="cdr_db")
        collection = db['CallDetailRecords']

        watchlistdb = get_db(alias="watchlist_db")
        watchlistcollection = watchlistdb["WatchList_data"]

        cell_db = get_db(alias="cell_id")
        cell_collection = cell_db["cellid_info"]

        seq_id = request.data.get("seq_id")

        # -------------------------------
        # Handle seq_id
        # -------------------------------
        if isinstance(seq_id, list):
            seq_filter = {"$in": seq_id}
        else:
            seq_filter = seq_id

        data = list(collection.find({"seq_id": seq_filter}))

        if not data:
            return Response({"error": "No data found"}, status=400)

        total_calls = len(data)

        # =====================================================
        # SUMMARY + CALL TYPE
        # =====================================================
        incoming = 0
        outgoing = 0
        missed = 0

        imei_counter = Counter()
        caller_counter = Counter()
        hourly_counter = Counter()
        day_counter = Counter()
        night_counter = Counter()
        tower_counter = Counter()

        Aparty_numbers = set()
        Bparty_numbers = set()

        for row in data:

            # Call Type
            call_type = (row.get("Call_Type") or "").upper()

            if call_type in ["CALL_IN", "SMS_IN"]:
                incoming += 1
            elif call_type in ["CALL_OUT", "SMS_OUT"]:
                outgoing += 1
            elif "MISS" in call_type:
                missed += 1

            # A_Party & B_Party
            A_party = row.get("A_Party")
            if A_party:
                Aparty_numbers.add(A_party)

            B_party = row.get("B_Party")
            if B_party:
                Bparty_numbers.add(B_party)

            # IMEI
            imei = row.get("IMEI")
            if imei:
                imei_counter[imei] += 1

            # Top Callers (B_Party based)
            number = row.get("B_Party")
            if number:
                caller_counter[number] += 1

            # Tower / Location
            tower_id = row.get("First_CGI")
            if tower_id:
                tower_counter[tower_id] += 1

            # Time
            ts = row.get("SDateTime")
            if ts:
                try:
                    ts_ist = ts + timedelta(hours=5, minutes=30)
                    hour = ts_ist.hour

                    hourly_counter[hour] += 1

                    if 6 <= hour < 18:
                        day_counter[number] += 1
                    else:
                        night_counter[number] += 1
                except:
                    pass

        # =====================================================
        # SUMMARY
        # =====================================================
        total_imei = len(imei_counter)

        # =====================================================
        # TOP CALLERS
        # =====================================================
        top_callers = [
            {"number": k, "calls": v}
            for k, v in caller_counter.most_common(10)
        ]

        # =====================================================
        # CALL SCENARIO
        # =====================================================
        call_scenario = {
            "incoming": incoming,
            "outgoing": outgoing,
            "missed": missed
        }

        def percent(val):
            return round((val / total_calls) * 100, 2) if total_calls else 0

        call_scenario_percent = {
            "incoming": percent(incoming),
            "outgoing": percent(outgoing),
            "missed": percent(missed)
        }

        # =====================================================
        # CALL ACTIVITY
        # =====================================================
        call_activity = [
            {"hour": h, "calls": c}
            for h, c in sorted(hourly_counter.items())
        ]

        # =====================================================
        # PEAK USAGE
        # =====================================================
        peak_hour = hourly_counter.most_common(1)
        peak_hour_value = peak_hour[0][0] if peak_hour else None

        peak_usage = (
            f"{peak_hour_value}:00 - {peak_hour_value + 1}:00"
            if peak_hour_value is not None else None
        )

        # =====================================================
        # IMEI USAGE
        # =====================================================
        imei_usage = [
            {"imei": k, "calls": v}
            for k, v in imei_counter.items()
        ]

        # =====================================================
        # DAY / NIGHT
        # =====================================================
        top_day_callers = [
            {"number": k, "calls": v}
            for k, v in day_counter.most_common(5)
        ]

        top_night_callers = [
            {"number": k, "calls": v}
            for k, v in night_counter.most_common(5)
        ]

        # =====================================================
        # BEHAVIOR
        # =====================================================
        night_calls = sum(night_counter.values())

        long_calls = sum(
            1 for r in data
            if r.get("Duration") and r.get("Duration") > 600
        )

        behavior = {
            "night_call_percent": percent(night_calls),
            "long_call_percent": percent(long_calls),
            "missed_call_percent": percent(missed)
        }

        # =====================================================
        # TOP 5 LOCATIONS
        # =====================================================
        top5_tower_ids = [tid for tid, _ in tower_counter.most_common(5)]

        cell_records = list(cell_collection.find({"_id": {"$in": top5_tower_ids}}))
        cell_map = {str(rec["_id"]): rec for rec in cell_records}

        top_locations = []
        for tid, hits in tower_counter.most_common(5):
            cell = cell_map.get(str(tid), {})
            top_locations.append({
                "tower_id": tid,
                "hits": hits,
                "lat": cell.get("LATITUDE"),
                "long": cell.get("LONGITUDE"),
                "address": cell.get("ADDRESS"),
                "city": cell.get("MAIN_CITY"),
                "operator": cell.get("OPERATOR")
            })

        # =====================================================
        # ALERTS
        # =====================================================
        alerts = []

        if behavior["night_call_percent"] > 20:
            alerts.append(f"Frequent Night Calls ({behavior['night_call_percent']}%)")

        if behavior["long_call_percent"] > 10:
            alerts.append(f"Frequent Long Calls ({behavior['long_call_percent']}%)")

        if behavior["missed_call_percent"] > 5:
            alerts.append(f"High Missed Calls ({behavior['missed_call_percent']}%)")

        if total_calls > 1000:
            alerts.append(f"High Communication Volume ({total_calls} calls)")

        if caller_counter:
            top_number, top_calls = caller_counter.most_common(1)[0]
            if top_calls > (total_calls * 0.3):
                alerts.append(f"Frequent contact with {top_number} ({top_calls} calls)")

        if total_imei > 3:
            alerts.append(f"Multiple devices used ({total_imei} IMEIs)")

        # =====================================================
        # WATCHLIST — A_Party and B_Party separately
        # =====================================================
        all_numbers = list(Aparty_numbers | Bparty_numbers)

        watchlist_docs = list(watchlistcollection.find(
            {"Number": {"$in": all_numbers}},
            {"_id": 0, "Number": 1, "Name": 1, "Reason": 1}
        ))

        watchlist_number_set = set(doc["Number"] for doc in watchlist_docs)
        watchlist_map = {doc["Number"]: doc for doc in watchlist_docs}

        aparty_hits = [
            watchlist_map[n] for n in Aparty_numbers if n in watchlist_number_set
        ]
        bparty_hits = [
            watchlist_map[n] for n in Bparty_numbers if n in watchlist_number_set
        ]

        if aparty_hits:
            alerts.append(f"Watchlist match in A_Party: {len(aparty_hits)} number(s) flagged")
        if bparty_hits:
            alerts.append(f"Watchlist match in B_Party: {len(bparty_hits)} number(s) flagged")

        # =====================================================
        # FINAL RESPONSE
        # =====================================================
        return Response({
            "summary": {
                "total_calls": total_calls,
                "total_imei": total_imei,
                "incoming": incoming,
                "outgoing": outgoing,
                "missed": missed
            },

            "top_callers": top_callers,

            "call_scenario": call_scenario,
            "call_scenario_percent": call_scenario_percent,

            "call_activity": call_activity,
            "peak_usage": peak_usage,

            "imei_usage": imei_usage,

            "top_day_callers": top_day_callers,
            "top_night_callers": top_night_callers,

            "behavior": behavior,
            "alerts": alerts,

            "top_locations": top_locations,   # Top 5 towers with geo info

            "watchlist_hits": {
                "aparty": aparty_hits,    # [{Number, Name, Reason}, ...]
                "bparty": bparty_hits     # [{Number, Name, Reason}, ...]
            }

        }, status=200)

class DashBoardMaxstayApi(APIView):
    def post(self, request):
        db = get_db(alias="cdr_db")
        collection = db['CallDetailRecords']
        seq_id = request.data.get("seq_id")

        # Handle both single seq_id and list of seq_ids
        if isinstance(seq_id, list):
            seq_id_filter = {"$in": seq_id}
        else:
            seq_id_filter = seq_id

        # --- Step 1: Get Top 10 First_CGI from CDR Records ---
        pipeline = [
            {
                "$match": {
                    "seq_id": seq_id_filter
                }
            },
            {
                "$group": {
                    "_id": "$First_CGI",
                    "TotalCalls": {"$sum": 1}
                }
            },
            {
                "$sort": {"TotalCalls": -1}
            },
            {
                "$limit": 10
            },
            {
                "$project": {
                    "_id": 0,
                    "First_CGI": "$_id",
                    "TotalCalls": "$TotalCalls"
                }
            }
        ]

        cdr_results = list(collection.aggregate(pipeline))

        # --- Step 2: Extract CGI values to lookup in cell_id collection ---
        cgi_list = [row["First_CGI"] for row in cdr_results]

        # --- Step 3: Fetch Lat/Long from cell_id collection ---
        source_db = get_db(alias='cell_id')
        cell_collection = source_db['cellid_info']

        cell_records = list(cell_collection.find(
            {"_id": {"$in": cgi_list}},
            {
                "_id": 1,
                "LATITUDE": 1,
                "LONGITUDE": 1,
                "ADDRESS": 1,
                "MAIN_CITY": 1,
                "OPERATOR": 1
            }
        ))

        # --- Step 4: Build a lookup map { First_CGI -> cell info } ---
        cell_map = {str(record["_id"]): record for record in cell_records}

        # --- Step 5: Merge CDR results with cell_id info ---
        final_results = []
        for row in cdr_results:
            cgi = row["First_CGI"]
            cell_info = cell_map.get(str(cgi), {})
            final_results.append({
                "First_CGI":  cgi,
                "TotalCalls": row["TotalCalls"],
                "LATITUDE":   cell_info.get("LATITUDE", None),
                "LONGITUDE":  cell_info.get("LONGITUDE", None),
                "ADDRESS":    cell_info.get("ADDRESS", None),
                "MAIN_CITY":  cell_info.get("MAIN_CITY", None),
                "OPERATOR":   cell_info.get("OPERATOR", None),
            })

        return Response({
            "top_10_cell_ids": final_results
        }, status=200)

from mongoengine import get_db
from rest_framework.response import Response
from rest_framework.views import APIView
from collections import defaultdict, Counter
from datetime import timedelta

from .searchengine import search_ip

from mongoengine import get_db
from rest_framework.response import Response
from rest_framework.views import APIView
from collections import defaultdict, Counter
from datetime import timedelta

from .searchengine import search_ip
from .models import CellTower, ImeiDetails


class IpdrDashboardApi(APIView):
    def post(self, request):
        db = get_db(alias="ipdr_db")
        nexus_collection = db['IPdrNexus']
        ipdr_reccollection = db['IPDetailRecords']

        # ==========================
        # WATCHLIST DB
        # ==========================
        watchlistdb = get_db(alias="watchlist_db")
        watchlistcollection = watchlistdb["WatchList_data"]

        seq_id = request.data.get("seq_id")

        # -------------------------------
        # Validate
        # -------------------------------
        nexus_data = list(nexus_collection.find({"_id": seq_id}))
        for record in nexus_data:
            if record.get('RecordType') != "Mobile":
                return Response({
                    "status": "error",
                    "message": "Please Select Mobile IPDR"
                }, status=400)

        ipdr_data = list(ipdr_reccollection.find({"seq_id": seq_id}))
        if not ipdr_data:
            return Response({"error": "No IPDR Data Found"}, status=400)

        # =====================================================
        # IP ENRICHMENT
        # =====================================================
        ips = list(set(d.get("Destination_ip") for d in ipdr_data if d.get("Destination_ip")))
        ip_info = search_ip(ips) or {}
        results = ip_info.get("results", [])

        lookupIP = {rec.get("ip"): rec for rec in results if rec.get("ip")}

        for row in ipdr_data:
            ip = row.get("Destination_ip")
            ip_detail = lookupIP.get(ip, {})
            row['country'] = ip_detail.get("Country", "Unknown")
            row['ip_App'] = ip_detail.get("App/Hostname") or "Unknown"

        # =====================================================
        # IMEI → DEVICE DETAILS
        # =====================================================
        tacs = set()
        for row in ipdr_data:
            imei = row.get("IMEI")
            if imei and len(imei) >= 8:
                try:
                    tacs.add(int(imei[:8]))
                except:
                    pass

        imei_records = ImeiDetails.objects(id__in=list(tacs))
        imei_map = {int(rec.id): rec for rec in imei_records}

        # =====================================================
        # WATCHLIST — MSISDN CHECK
        # =====================================================
        msisdn_set = set(
            row.get("MSISDN") for row in ipdr_data if row.get("MSISDN")
        )

        watchlist_hits = list(
            watchlistcollection.find(
                {"Number": {"$in": list(msisdn_set)}},
                {"_id": 0, "Number": 1, "Name": 1, "Reason": 1}
            )
        )

        watchlist_alert = (
            f"Watchlist match found: {len(watchlist_hits)} number(s) flagged"
            if watchlist_hits else None
        )

        # =====================================================
        # DASHBOARD CALCULATIONS
        # =====================================================
        total_logs = len(ipdr_data)

        # -------------------------------
        # Device Analysis
        # -------------------------------
        device_counter = Counter()
        device_details_map = {}

        for row in ipdr_data:
            imei = row.get("IMEI")
            if imei and len(imei) >= 8:
                try:
                    tac = int(imei[:8])
                    imei_info = imei_map.get(tac)
                    if imei_info:
                        device_name = imei_info.brand or "Unknown"
                        device_details_map[device_name] = {
                            "brand": imei_info.brand,
                            "os": imei_info.os,
                            "type": imei_info.devicetype,
                            "simslots": imei_info.simslots
                        }
                    else:
                        device_name = "Unknown"
                except:
                    device_name = "Unknown"
            else:
                device_name = "Unknown"

            device_counter[device_name] += 1

        device_analysis = [
            {
                "device": device,
                "count": count,
                "details": device_details_map.get(device, {})
            }
            for device, count in device_counter.items()
        ]

        device_count = len(set(row.get("IMEI") for row in ipdr_data if row.get("IMEI")))

        # -------------------------------
        # Country Alerts
        # -------------------------------
        country_stats = defaultdict(lambda: {
            "hits": 0,
            "first_seen": None,
            "last_seen": None
        })

        for row in ipdr_data:
            country = row.get("country", "Unknown")
            ts = row.get("SDateTime")

            if ts:
                ts = ts + timedelta(hours=5, minutes=30)

            stat = country_stats[country]
            stat["hits"] += 1

            if ts:
                if not stat["first_seen"] or ts < stat["first_seen"]:
                    stat["first_seen"] = ts
                if not stat["last_seen"] or ts > stat["last_seen"]:
                    stat["last_seen"] = ts

        def format_dt(dt):
            return dt.strftime("%d/%m/%Y") if dt else None

        country_alerts = [
            {
                "country": k,
                "hits": v["hits"],
                "first_seen": format_dt(v["first_seen"]),
                "last_seen": format_dt(v["last_seen"])
            }
            for k, v in country_stats.items()
        ]

        # -------------------------------
        # Suspicious Hits
        # -------------------------------
        SUSPICIOUS = ["China", "Pakistan", "UAE"]

        suspicious_hits = sum(
            1 for row in ipdr_data if row.get("country") in SUSPICIOUS
        )

        # -------------------------------
        # App Usage
        # -------------------------------
        app_usage_counter = Counter(
            row.get("ip_App") or "Unknown"
            for row in ipdr_data
        )

        app_usage = [{"app": k, "count": v} for k, v in app_usage_counter.items()]

        # -------------------------------
        # Peak Usage
        # -------------------------------
        hours = []
        for row in ipdr_data:
            ts = row.get("SDateTime")
            if ts:
                ts = ts + timedelta(hours=5, minutes=30)
                hours.append(ts.hour)

        peak_hour = Counter(hours).most_common(1)
        peak_hour_value = peak_hour[0][0] if peak_hour else None
        peak_label = f"{peak_hour_value}:00 - {peak_hour_value + 1}:00" if peak_hour_value is not None else None

        # =====================================================
        # TOP 5 LOCATIONS
        # =====================================================
        tower_counter = Counter()

        for row in ipdr_data:
            tid = row.get("TowerID")
            if tid:
                tower_counter[tid] += 1

        top5_tower_ids = [tid for tid, _ in tower_counter.most_common(5)]
        cell_records = CellTower.objects(id__in=top5_tower_ids)
        cell_map = {str(cell.id): cell for cell in cell_records}

        top_locations = []
        for tid, hits in tower_counter.most_common(5):
            cell = cell_map.get(str(tid))
            top_locations.append({
                "tower_id": tid,
                "hits": hits,
                "lat": float(cell.LATITUDE) if cell and cell.LATITUDE else None,
                "long": float(cell.LONGITUDE) if cell and cell.LONGITUDE else None,
                "address": cell.ADDRESS if cell else None,
                "city": cell.MAIN_CITY if cell else None,
                "operator": cell.OPERATOR if cell else None
            })

        # -------------------------------
        # Risk Score
        # -------------------------------
        risk_score = min(
            suspicious_hits * 6 +
            device_count * 4 +
            (5 if peak_hour_value else 0) +
            (len(watchlist_hits) * 10),
            100
        )

        # -------------------------------
        # Insights
        # -------------------------------
        insights = []

        if device_count > 2:
            insights.append("Device IMEI changed multiple times")

        if suspicious_hits > 0:
            insights.append("Connected to suspicious countries")

        if peak_hour_value:
            insights.append(f"Peak usage between {peak_label}")

        if app_usage_counter.get("YouTube", 0) > 50:
            insights.append("Frequent usage of YouTube")

        if watchlist_hits:
            insights.append(f"MSISDN matched watchlist: {len(watchlist_hits)} hit(s)")

        # -------------------------------
        # Alerts
        # -------------------------------
        alerts = []

        if suspicious_hits:
            alerts.append(f"Suspicious country connections: {suspicious_hits}")

        if device_count > 2:
            alerts.append("Frequent device switching detected")

        if watchlist_alert:
            alerts.append(watchlist_alert)

        # -------------------------------
        # FINAL RESPONSE
        # -------------------------------
        return Response({
            "summary": {
                "devices": device_count,
                "suspicious_hits": suspicious_hits,
                "risk_score": risk_score,
                "total_logs": total_logs
            },
            "device_analysis": device_analysis,
            "country_alerts": country_alerts,
            "app_usage": app_usage,
            "peak_usage": peak_label,
            "top_locations": top_locations,    # Top 5 towers with geo info
            "insights": insights,
            "alerts": alerts,
            "watchlist_hits": watchlist_hits   # [{Number, Name, Reason}, ...]
        }, status=200)







from rest_framework.views import APIView
from rest_framework.response import Response
from collections import Counter
from datetime import timedelta
import os
import json


from .models import CellTower, ImeiDetails
from collections import Counter
from datetime import timedelta
import os

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
COUNTRY_FILE = os.path.join(CURRENT_DIR, "data", "country_codes.json")


class TowerDumpDashboard(APIView):


    def post(self, request):
        try:
            # ==========================
            # DB CONNECTIONS
            # ==========================
            db = get_db(alias="towerdump_db")
            collection = db["TowerDumpRecords"]
            nexus_collection = db["TowerDumpNexus"]

            watchlistdb = get_db(alias="watchlist_db")
            watchlistcollection = watchlistdb["WatchList_data"]

            cell_db = get_db(alias="cell_id")
            cell_collection = cell_db["cellid_info"]

            # ==========================
            # INPUT
            # ==========================
            seq_id = request.data.get("seq_id")
            if not seq_id:
                return Response({"error": "seq_id required"}, status=400)

            # ==========================
            # TOWER INFO
            # ==========================
            nexus_data = nexus_collection.find_one({"_id": seq_id})
            tower_id = nexus_data.get("Tower_id") if nexus_data else None

            raw_tower = cell_collection.find_one({"_id": tower_id}) if tower_id else None

            tower_location = {}
            if raw_tower:
                tower_location = {
                    "tower_id": tower_id,
                    "lat": raw_tower.get("LATITUDE"),
                    "lng": raw_tower.get("LONGITUDE"),
                    "address": raw_tower.get("ADDRESS"),
                    "city": raw_tower.get("MAIN_CITY"),
                    "operator": raw_tower.get("OPERATOR"),
                }

            # ==========================
            # FETCH DATA
            # ==========================
            seq_filter = {"$in": seq_id} if isinstance(seq_id, list) else seq_id
            data = list(collection.find({"seq_id": seq_filter}))

            if not data:
                return Response({"error": "No data found"}, status=400)

            # ==========================
            # INITIALIZE
            # ==========================
            incoming_calls = outgoing_calls = missed_calls = 0
            incoming_sms = outgoing_sms = wifi_calls = 0

            Aparty = set()
            Bparty = set()
            imei_set = set()

            number_freq = Counter()
            caller_stats = defaultdict(lambda: {"incoming": 0, "outgoing": 0, "sms": 0})

            imei_map = defaultdict(set)
            imsi_map = defaultdict(set)

            tower_switch_counter = Counter()
            hourly_in = Counter()
            hourly_out = Counter()

            # ==========================
            # LOOP
            # ==========================
            for row in data:

                A = row.get("A_Party")
                B = row.get("B_Party")
                call_type = (row.get("Call_Type") or "").upper()

                # SETS
                if A:
                    Aparty.add(A)
                    number_freq[A] += 1
                if B:
                    Bparty.add(B)
                    number_freq[B] += 1

                # ======================
                # WIFI DETECTION
                # ======================
                is_wifi = row.get("Vowifi") not in [None, "", "0"]

                if is_wifi:
                    wifi_calls += 1

                # ======================
                # CALL / SMS SPLIT
                # ======================
                if call_type == "CALL_IN":
                    incoming_calls += 1
                    caller_stats[A]["incoming"] += 1

                    if not is_wifi:
                        pass

                elif call_type == "CALL_OUT":
                    outgoing_calls += 1
                    caller_stats[A]["outgoing"] += 1

                elif call_type == "SMS_IN":
                    incoming_sms += 1
                    caller_stats[A]["sms"] += 1

                elif call_type == "SMS_OUT":
                    outgoing_sms += 1
                    caller_stats[A]["sms"] += 1

                elif "MISS" in call_type:
                    missed_calls += 1

                # ======================
                # IMEI / IMSI
                # ======================
                imei = row.get("IMEI")
                imsi = row.get("IMSI")

                if imei and A:
                    imei_map[A].add(imei)
                    imei_set.add(imei)

                if imsi and A:
                    imsi_map[A].add(imsi)

                # ======================
                # TIME TREND
                # ======================
                ts = row.get("SDateTime")
                if ts:
                    try:
                        hour = (ts + timedelta(hours=5, minutes=30)).hour

                        if call_type == "CALL_IN":
                            hourly_in[hour] += 1
                        elif call_type == "CALL_OUT":
                            hourly_out[hour] += 1

                    except:
                        pass

                # ======================
                # TRAVEL
                # ======================
                if row.get("First_CGI") != row.get("Last_CGI") and A:
                    tower_switch_counter[A] += 1

            # ==========================
            # METRICS
            # ==========================
            total_calls = incoming_calls + outgoing_calls
            total_sms = incoming_sms + outgoing_sms

            unique_numbers = len(Aparty | Bparty)
            unique_imei = len(imei_set)

            one_time_users = sum(1 for v in number_freq.values() if v == 1)
            traveling_numbers = sum(1 for v in tower_switch_counter.values() if v > 0)

            multi_device_numbers = [k for k, v in imei_map.items() if len(v) > 1]
            imsi_changed_numbers = [k for k, v in imsi_map.items() if len(v) > 1]

            multi_device = len(multi_device_numbers)
            imsi_changed = len(imsi_changed_numbers)

            # ==========================
            # TOP 5 TABLE
            # ==========================
            top5 = []
            for num, stats in sorted(caller_stats.items(), key=lambda x: sum(x[1].values()), reverse=True)[:5]:
                top5.append({
                    "number": num,
                    "total_calls": stats["incoming"] + stats["outgoing"],
                    "incoming": stats["incoming"],
                    "outgoing": stats["outgoing"],
                    "sms": stats["sms"]
                })

            # ==========================
            # TREND GRAPH
            # ==========================
            trend = []
            for h in range(24):
                trend.append({
                    "hour": h,
                    "incoming": hourly_in[h],
                    "outgoing": hourly_out[h]
                })

            # ==========================
            # ALERTS
            # ==========================
            alerts = []

            if one_time_users > 100:
                alerts.append("High number of one-time users detected")

            if traveling_numbers > 50:
                alerts.append("Traveling numbers detected")

            if imsi_changed > 20:
                alerts.append("IMSI changes detected")

            if multi_device > 20:
                alerts.append("Multi-device usage detected")

            if not alerts:
                alerts.append("No unusual activity detected")

            # ==========================
            # RESPONSE
            # ==========================
            return Response({

                "tower": tower_location,

                "overview": {
                    "total_numbers": unique_numbers,
                    "unique_numbers": len(Aparty),
                    "total_devices": unique_imei
                },

                "call_sms_summary": {
                    "incoming_calls": incoming_calls,
                    "outgoing_calls": outgoing_calls,
                    "incoming_sms": incoming_sms,
                    "outgoing_sms": outgoing_sms,
                    "wifi_calls": wifi_calls,

                },

                "activity": {
                    "total_calls": total_calls,
                    "total_sms": total_sms,
                    "wifi_calls": wifi_calls,
                    "missed_calls": missed_calls
                },

                "user_classification": {
                    "one_time_users": one_time_users,
                    "traveling_numbers": traveling_numbers,
                    "imsi_changed": imsi_changed,
                    "multi_device": multi_device
                },

                "top5_numbers": top5,

                "call_trend": trend,

                "alerts": alerts,

                "watchlist_hits": list(
                    watchlistcollection.find(
                        {"Number": {"$in": list(Aparty | Bparty)}},
                        {"_id": 0, "Number": 1}
                    )
                )

            }, status=200)

        except Exception as e:
            return Response({"error": str(e)}, status=500)

