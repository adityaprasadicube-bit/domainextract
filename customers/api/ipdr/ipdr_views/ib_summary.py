import ipaddress
from collections import defaultdict
from datetime import time, timedelta
import math

from mongoengine import get_db
from rest_framework.views import APIView
from rest_framework.response import Response
from django.utils.dateparse import parse_datetime

from ..ipdr_models.ip_model import IPDRNexus, PortInfo
from ..ip_serializers import IPDRNexusSerializer, PortInfoSerializer
from ...models import ImeiDetails, MccMnc, CellTower
from ...searchengine import search_ip
from ...serializers import DeviceInfoSerializer, MccMncSerializer, CellTowerSerializer


class Summary(APIView):
    # VoIP Service Mapping
    VOIP_PORT_MAP = {
        "5060": "SIP (Standard VoIP)", "5061": "SIPS (Secure VoIP)",
        "5222": "WhatsApp", "5223": "WhatsApp",
        "5228": "WhatsApp", "4244": "WhatsApp",
        "5242": "WhatsApp", "3478": "WhatsApp/Skype",
        "1719": "H.323", "1720": "H.323",
        "5004": "RTP", "8000": "Internet Radio/VoIP",
    }

    def _parse_dt(self, val):
        try:
            return parse_datetime(val)
        except:
            return None

    def _normalize_seq_id(self, raw_seq_id):
        if raw_seq_id is None: return None
        return str(raw_seq_id[0]) if isinstance(raw_seq_id, list) and len(raw_seq_id) > 0 else str(raw_seq_id)

    def _format_duration(self, seconds):
        """Formats seconds into HR MIN SEC string for reports"""
        hrs = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        parts = []
        if hrs > 0: parts.append(f"{hrs:02} hr")
        if mins > 0 or hrs > 0: parts.append(f"{mins:02} min")
        parts.append(f"{secs:02} sec")
        return " ".join(parts)

    def post(self, request):
        try:
            seq_ids = request.data.get("seq_ids", [])
            from_date = self._parse_dt(request.data.get("from_date"))
            to_date = self._parse_dt(request.data.get("to_date"))

            # Pagination params
            page = int(request.data.get("page", 1))
            page_size = int(request.data.get("page_size", 500))

            if not seq_ids or not from_date or not to_date:
                return Response({"error": "seq_ids, from_date, to_date are required"}, status=400)

            db = get_db("ipdr_db")
            collections = db["IPDetailRecords"]
            nexus_collection = db["IPdrNexus"]

            # 1. Role Categorization (Mobile vs IP Reference)
            nexus_data = list(nexus_collection.find({"_id": {"$in": seq_ids}}))
            mobile_data_map, target_ip_metadata = {}, {}
            mobile_seq_ids = []

            for row in nexus_data:
                sid = row.get("_id")
                rtype = row.get("RecordType")
                ipdr_val = str(row.get("IPDR", ""))

                if rtype == "Mobile":
                    mobile_seq_ids.append(sid)
                    mobile_data_map[sid] = {
                        "ipdr": ipdr_val, "imei": None, "imsi": None, "imei_tac": None,
                        "cgi_frequency": defaultdict(lambda: {"count": 0, "first_date": None, "last_date": None}),
                        "cgi_day_freq": defaultdict(lambda: {"count": 0, "first_date": None, "last_date": None}),
                        "cgi_night_freq": defaultdict(lambda: {"count": 0, "first_date": None, "last_date": None}),
                        "ip_frequency": defaultdict(int),
                        "day_ip_frequency": defaultdict(int),
                        "night_ip_frequency": defaultdict(int),
                        "country_freq": defaultdict(int), "category_freq": defaultdict(int),
                        "day_count": 0, "night_count": 0, "idle_report": [], "active_report": [],
                        "voip_groups": defaultdict(lambda: {"count": 0, "start": None, "duration": 0}),
                        "unique_cgi_set": set()
                    }
                elif rtype in ["IP", "Public IP", "Destination IP"]:
                    target_ip_metadata[ipdr_val] = {"sid": sid, "display": row.get("IPDR") or ipdr_val}

            # 2. Fetch Mobile-based Records
            data = list(collections.find({
                "seq_id": {"$in": mobile_seq_ids},
                "SDateTime": {"$gte": from_date, "$lte": to_date}
            }).sort("SDateTime", 1))

            day_start, day_end = time(6, 0, 0), time(22, 59, 59)
            last_end_map, active_session_map = {}, {}

            for row in data:
                sid = self._normalize_seq_id(row.get("seq_id"))
                if sid not in mobile_data_map: continue
                m_data = mobile_data_map[sid]

                d_ip, d_port = str(row.get("Destination_ip") or ""), int(row.get("Destination_port") or 0)
                t_ip, t_port = str(row.get("Translated_ip") or ""), int(row.get("Translated_port") or 0)
                s_dt, e_dt, tower_id = row.get("SDateTime"), row.get("EDateTime"), str(row.get("TowerID") or "")

                is_day = day_start <= s_dt.time() <= day_end
                if is_day:
                    m_data["day_count"] += 1
                else:
                    m_data["night_count"] += 1

                # Idle and Active Analysis Tracking
                if sid in last_end_map:
                    gap = (s_dt - last_end_map[sid]).total_seconds()
                    if gap > 0:
                        m_data["idle_report"].append({"S. No": len(m_data["idle_report"]) + 1,
                                                      "Start Time Date": last_end_map[sid].strftime(
                                                          "%d-%m-%Y %H:%M:%S"),
                                                      "Start Time": s_dt.strftime("%d-%m-%Y %H:%M:%S"),
                                                      "Duration": self._format_duration(gap)})
                    if gap <= 60:
                        active_session_map[sid]["last_end"] = e_dt
                    else:
                        sess = active_session_map[sid]
                        m_data["active_report"].append({"S. No": len(m_data["active_report"]) + 1,
                                                        "Start Time": sess["start"].strftime("%d-%m-%Y %H:%M:%S"),
                                                        "End Time": sess["last_end"].strftime("%d-%m-%Y %H:%M:%S"),
                                                        "Duration": self._format_duration(
                                                            (sess["last_end"] - sess["start"]).total_seconds())})
                        active_session_map[sid] = {"start": s_dt, "last_end": e_dt}
                else:
                    active_session_map[sid] = {"start": s_dt, "last_end": e_dt}
                last_end_map[sid] = e_dt

                # Strict VoIP Cross Match (+/- 30 mins)
                target_ip = d_ip if d_ip in target_ip_metadata else (t_ip if t_ip in target_ip_metadata else None)
                if target_ip:
                    t_info = target_ip_metadata[target_ip]
                    b_rec = collections.find_one({
                        "seq_id": t_info["sid"],
                        "SDateTime": {"$gte": s_dt - timedelta(minutes=30), "$lte": s_dt + timedelta(minutes=30)},
                        "$or": [{"Translated_ip": d_ip, "Translated_port": d_port},
                                {"Destination_ip": t_ip, "Destination_port": t_port}]
                    })
                    if b_rec:
                        other_p = b_rec.get("MSISDN") or t_info["display"]
                        g_key = (other_p, self.VOIP_PORT_MAP.get(str(d_port), "Potential VoIP"), f"{d_ip}({d_port})",
                                 f"{t_ip}({t_port})", tower_id)
                        m_data["voip_groups"][g_key]["count"] += 1
                        m_data["voip_groups"][g_key]["duration"] += int(row.get("Duration") or 0)
                        if not m_data["voip_groups"][g_key]["start"] or s_dt < m_data["voip_groups"][g_key]["start"]:
                            m_data["voip_groups"][g_key]["start"] = s_dt

                # Tower Analytics
                if tower_id:
                    m_data["unique_cgi_set"].add(tower_id)
                    m_data["cgi_frequency"][tower_id]["count"] += 1
                    t_map = m_data["cgi_day_freq"] if is_day else m_data["cgi_night_freq"]
                    t_map[tower_id]["count"] += 1
                    if not t_map[tower_id]["first_date"] or s_dt < t_map[tower_id]["first_date"]: t_map[tower_id][
                        "first_date"] = s_dt
                    if not t_map[tower_id]["last_date"] or s_dt > t_map[tower_id]["last_date"]: t_map[tower_id][
                        "last_date"] = s_dt

                # IP Analytics
                if d_ip:
                    m_data["ip_frequency"][d_ip] += 1
                    if is_day:
                        m_data["day_ip_frequency"][d_ip] += 1
                    else:
                        m_data["night_ip_frequency"][d_ip] += 1

                if m_data["imei"] is None: m_data["imei"], m_data["imei_tac"] = row.get("IMEI"), row.get("IMEI_TAC")
                if m_data["imsi"] is None: m_data["imsi"] = row.get("IMSI")

            # Finalize open active sessions
            for sid, sess in active_session_map.items():
                m_info = mobile_data_map[sid]
                m_info["active_report"].append({"S. No": len(m_info["active_report"]) + 1,
                                                "Start Time": sess["start"].strftime("%d-%m-%Y %H:%M:%S"),
                                                "End Time": sess["last_end"].strftime("%d-%m-%Y %H:%M:%S"),
                                                "Duration": self._format_duration(
                                                    (sess["last_end"] - sess["start"]).total_seconds())})

            # DB Lookups for IPs and Towers
            all_ips = set().union(*(m["ip_frequency"].keys() for m in mobile_data_map.values()))
            ip_lookup = {}
            try:
                ip_res = search_ip(list(all_ips))
                for doc in ip_res.get("results", []):
                    if doc.get('ip'): ip_lookup[doc['ip']] = doc
            except:
                pass

            cgi_lookup = {c.id: CellTowerSerializer(c).data for c in CellTower.objects.filter(
                id__in=set().union(*(m["unique_cgi_set"] for m in mobile_data_map.values())))}

            # 3. Final Report Assembly
            final_res = []
            for sid, m_info in mobile_data_map.items():
                m_num = m_info["ipdr"]
                total_hits = sum(m_info["ip_frequency"].values())

                resolved_ips, unresolved_ips = [], []
                day_app_map = defaultdict(lambda: {"records": 0, "category": "General"})
                night_app_map = defaultdict(lambda: {"records": 0, "category": "General"})
                app_summary_map = defaultdict(lambda: {"records": 0, "category": "General"})

                # Categorize IPs
                for ip, freq in m_info["ip_frequency"].items():
                    info = ip_lookup.get(ip, {})
                    app_name, cat = info.get('App/Hostname', 'Unknown'), info.get('Category', 'General')
                    key = (ip, app_name)

                    m_info["country_freq"][info.get("Country", "Unknown")] += freq
                    m_info["category_freq"][cat] += freq

                    # Usage Maps for App Summaries
                    app_summary_map[key]["records"] += freq
                    app_summary_map[key]["category"] = cat

                    if ip in m_info["day_ip_frequency"]:
                        day_app_map[key]["records"] += m_info["day_ip_frequency"][ip]
                        day_app_map[key]["category"] = cat
                    if ip in m_info["night_ip_frequency"]:
                        night_app_map[key]["records"] += m_info["night_ip_frequency"][ip]
                        night_app_map[key]["category"] = cat

                    # Resolved vs Unresolved
                    if info.get('Isp/Org'):
                        resolved_ips.append({'Destination Ips': ip, 'App Name': app_name, 'Category': cat,
                                             'Isp/Org': info.get('Isp/Org'), 'Freq': freq,
                                             'Country': info.get('Country')})
                    else:
                        unresolved_ips.append({'Destination Ips': ip, 'Freq': freq})

                # Process CGI & Unmapped
                cgi_list, unmapped_cgi = [], []
                for cid, f_data in m_info["cgi_frequency"].items():
                    addr = cgi_lookup.get(cid, {}).get('ADDRESS', "Address Not Available")
                    entry = {'CGI ID': cid, 'Address': addr, 'Freq': f_data["count"]}
                    cgi_list.append(entry)
                    if addr == "Address Not Available": unmapped_cgi.append(entry)

                # Process VoIP with Pagination
                full_voip = [
                    {"S L N O": i + 1, "OTHER PARTY": k[0], "CUSTOMER DETAIL": "SDR/Watchlist", "CALL TYPE": k[1],
                     "PARTY A IP": k[2], "PARTY B IP": k[3], "START TIME": v["start"].strftime("%d-%m-%Y %H:%M:%S"),
                     "CALL DURATION": v["duration"], "CGI": cgi_lookup.get(k[4], {}).get("ADDRESS", k[4]),
                     "RECORDS": v["count"]} for i, (k, v) in enumerate(m_info["voip_groups"].items())]
                start_idx = (page - 1) * page_size
                paginated_voip = sorted(full_voip, key=lambda x: x['RECORDS'], reverse=True)[
                    start_idx: start_idx + page_size]

                # Assemble Final Master Dictionary
                final_res.append({
                    m_num: {
                        "1.Mobiledetails": [{
                            'Mobile No': m_num, 'IMEI': m_info["imei"], 'IMSI': m_info["imsi"],
                            'Mobile Model': DeviceInfoSerializer(
                                ImeiDetails.objects.filter(id=int(m_info["imei_tac"] or 0)).first()).data.get(
                                "brand") if m_info["imei_tac"] else "Unknown"
                        }],
                        "2.CGIDetails": cgi_list,
                        "3.UnmappedCellIDs": unmapped_cgi,
                        "4.ResolvedIPs": sorted(resolved_ips, key=lambda x: x['Freq'], reverse=True),
                        "5.UnresolvedIPs": sorted(unresolved_ips, key=lambda x: x['Freq'], reverse=True),
                        "6.TopAppUsageInDay[06:00Hrs-22:59Hrs]": [
                            {"SL.NO.": i + 1, "IP": k[0], "APP NAME": k[1], "CATEGORY": v["category"],
                             "RECORDS": v["records"]}
                            for i, (k, v) in
                            enumerate(sorted(day_app_map.items(), key=lambda x: x[1]["records"], reverse=True))
                        ],
                        "7.TopAppUsageInNight[23:00Hrs-05:59Hrs]": [
                            {"SL.NO.": i + 1, "IP": k[0], "APP NAME": k[1], "CATEGORY": v["category"],
                             "RECORDS": v["records"]}
                            for i, (k, v) in
                            enumerate(sorted(night_app_map.items(), key=lambda x: x[1]["records"], reverse=True))
                        ],
                        "8.VoIPCallReport": paginated_voip,
                        "9.AppSummary": [
                            {"SL.NO.": i + 1, "IP": k[0], "APP NAME": k[1], "CATEGORY": v["category"],
                             "RECORDS": v["records"]}
                            for i, (k, v) in
                            enumerate(sorted(app_summary_map.items(), key=lambda x: x[1]["records"], reverse=True))
                        ],
                        "10.IdleTimeReport": m_info["idle_report"],
                        "11.ActiveTimeAnalysis": m_info["active_report"],
                        "12.DayNightLocationAnalysis": [
                            {
                                "Day Period Location": f"{round((m_info['day_count'] / (m_info['day_count'] + m_info['night_count'])) * 100, 2)}%",
                                "Night Period Location": f"{round((m_info['night_count'] / (m_info['day_count'] + m_info['night_count'])) * 100, 2)}%"}
                        ] if (m_info['day_count'] + m_info['night_count']) > 0 else [],
                        "13.TopLocationsDay[06:00Hrs-22:59Hrs]": [
                            {"S. No": i + 1, "Location/CGI ID": cgi_lookup.get(cid, {}).get('ADDRESS', cid),
                             "Percentage (%)": round((v['count'] / m_info['day_count']) * 100, 2),
                             "First Date": v['first_date'].strftime("%d-%m-%Y"),
                             "Last Date": v['last_date'].strftime("%d-%m-%Y")}
                            for i, (cid, v) in enumerate(
                                sorted(m_info["cgi_day_freq"].items(), key=lambda x: x[1]['count'], reverse=True)[:5])
                        ] if m_info['day_count'] > 0 else [],
                        "14.TopLocationsNight[23:00Hrs-05:59Hrs]": [
                            {"S. No": i + 1, "Location/CGI ID": cgi_lookup.get(cid, {}).get('ADDRESS', cid),
                             "Percentage (%)": round((v['count'] / m_info['night_count']) * 100, 2),
                             "First Date": v['first_date'].strftime("%d-%m-%Y"),
                             "Last Date": v['last_date'].strftime("%d-%m-%Y")}
                            for i, (cid, v) in enumerate(
                                sorted(m_info["cgi_night_freq"].items(), key=lambda x: x[1]['count'], reverse=True)[:5])
                        ] if m_info['night_count'] > 0 else [],
                        "15.CountryWiseIPSummary": [
                            {"S. No": i + 1, "Country Wise Ips": c,
                             "Percentage": f"{round((f / total_hits) * 100, 2)}%"}
                            for i, (c, f) in
                            enumerate(sorted(m_info["country_freq"].items(), key=lambda x: x[1], reverse=True))
                        ],
                        "16.UsageWiseSummary": [
                            {"S.No": i + 1, "App Usage": a, "Percentage": f"{round((f / total_hits) * 100, 2)}%"}
                            for i, (a, f) in
                            enumerate(sorted(m_info["category_freq"].items(), key=lambda x: x[1], reverse=True))
                        ],
                        "pagination": {"current_page": page, "total_pages": math.ceil(len(full_voip) / page_size),
                                       "total_records": len(full_voip)}
                    }
                })

            return Response({"data": final_res}, status=200)

        except Exception as e:
            return Response({"error": str(e)}, status=500)