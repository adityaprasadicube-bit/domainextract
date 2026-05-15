import ipaddress
from mongoengine import get_db
from rest_framework.views import APIView
from rest_framework.response import Response
from django.utils.dateparse import parse_datetime
from collections import defaultdict
from ..ipdr_models.ip_model import IPDRNexus, PortInfo
from ..ip_serializers import IPDRNexusSerializer, PortInfoSerializer
from ...searchengine import search_ip
from ...models import CellTower, MccMnc
from ...serializers import CellTowerSerializer, MccMncSerializer


class MaxStaySessionAPIView(APIView):

    def _parse_dt(self, val):
        try:
            return parse_datetime(val)
        except:
            return None

    def _ip_type(self, ip):
        try:
            return "IPv4" if ipaddress.ip_address(ip).version == 4 else "IPv6"
        except:
            return ""

    def _extract_mccmnc_from_cgi(self, cgi):
        """Extract MCCMNC from CGI similar to the previous CDR code"""
        if not cgi:
            return None

        if len(cgi) >= 5:
            if len(cgi) > 5:
                mccmnc = cgi[:6]
            else:
                mccmnc = cgi[:5]

            if mccmnc.isdigit():
                if len(mccmnc) == 6:
                    if int(mccmnc) < 405750 and (int(mccmnc) < 405025 or int(mccmnc) > 405047):
                        mccmnc = mccmnc[:5]
                return mccmnc
        return None

    def post(self, request):
        # Required fields
        seq_id = request.data.get("seq_id")
        from_date = self._parse_dt(request.data.get("from_date"))
        to_date = self._parse_dt(request.data.get("to_date"))
        filter_type = request.data.get("filter_type", "maxstay")

        # Pagination parameters
        page = int(request.data.get("page", 1))
        page_size = int(request.data.get("page_size", 500))

        if not seq_id or not from_date or not to_date:
            return Response({"error": "seq_id, from_date, to_date are required"}, status=400)

        if filter_type not in ["maxstay", "maxstaysession"]:
            return Response({"error": "filter_type must be 'maxstay' or 'maxstaysession'"}, status=400)

        nexus = IPDRNexus.objects.get(id=seq_id)
        nexus_data = IPDRNexusSerializer(nexus).data

        # Mongo Aggregation
        db = get_db("ipdr_db")
        collection = db["IPDetailRecords"]

        pipeline = [
            {"$match": {"seq_id": seq_id, "SDateTime": {"$gte": from_date, "$lte": to_date}}},
            {"$sort": {"SDateTime": 1}}
        ]

        data = list(collection.aggregate(pipeline))

        # Set Destination IP if needed
        if nexus_data.get("RecordType") == "Destination IP":
            for d in data:
                d["Destination_ip"] = nexus_data.get("IPDR")

        # Collect unique IPs, Ports, and Tower IDs
        ips = set(d.get("Destination_ip") for d in data if d.get("Destination_ip"))
        ports = set(d.get("Destination_port") for d in data if d.get("Destination_port"))

        # Collect all unique Tower IDs from the data
        tower_ids = set()
        roam_code_numbers = set()  # To collect roaming codes for lookup

        for d in data:
            tower_id = d.get("TowerID")
            if tower_id:
                tower_ids.add(tower_id)

                # Extract roaming code from TowerID (similar to CGI in CDR)
                mccmnc = self._extract_mccmnc_from_cgi(tower_id)
                if mccmnc:
                    roam_code_numbers.add(mccmnc)
                    d["RoamCode"] = mccmnc  # Store for later use

        # Bulk IP lookup
        ip_info = search_ip(list(ips))
        lookupIP = {rec["ip"]: rec for rec in ip_info.get("results", [])}

        # Bulk Port lookup
        port_docs = PortInfo.objects.filter(Port__in=ports)
        lookupPort = {p.Port: PortInfoSerializer(p).data for p in port_docs}

        # Bulk Tower lookup
        lookupTower = {}
        if len(tower_ids) > 0:
            towers = CellTower.objects.filter(id__in=tower_ids)
            towersdata = CellTowerSerializer(towers, many=True).data
            lookupTower = {item["id"]: item for item in towersdata}

        # Bulk Roaming code lookup (similar to CDR code)
        lookupRoam = {}
        if len(roam_code_numbers) > 0:
            roam_codes = MccMnc.objects.filter(mccmnc_temp__in=roam_code_numbers)
            roam_codesdata = MccMncSerializer(roam_codes, many=True).data
            lookupRoam = {item["mccmnc_temp"]: item for item in roam_codesdata}

        if filter_type == "maxstay":
            # Original maxstay logic...
            # Group sessions by Destination IP with tower details
            ip_sessions = {}

            for row in data:
                dest_ip = row.get("Destination_ip")
                if not dest_ip:
                    continue

                # Get tower ID
                tower_id = row.get("TowerID", "")

                # Determine roaming status
                roaming_circle = "Unknown"
                roaming_operator = "Unknown"
                roaming = "Unknown"  # For backward compatibility

                # Check if we have RoamCode and lookupRoam data
                if len(lookupRoam) > 0:
                    roamcode = row.get("RoamCode")
                    if roamcode:
                        if roamcode in lookupRoam:
                            roaming_circle = lookupRoam[roamcode]['circle']
                            roaming_operator = lookupRoam[roamcode]['operator']
                            roaming = f"{roaming_circle} ({roaming_operator})"
                        else:
                            roaming_circle = 'Unknown'
                            roaming_operator = 'Unknown'
                            roaming = 'Unknown'
                    else:
                        roaming_circle = 'Unknown'
                        roaming_operator = 'Unknown'
                        roaming = 'Unknown'
                else:
                    roaming_circle = 'Unknown'
                    roaming_operator = 'Unknown'
                    roaming = 'Unknown'

                # Initialize tower data variables
                tower_address = ""
                main_city = ""
                sub_city = ""
                latitude = ""
                longitude = ""
                azimuth = ""
                state = ""

                # Fetch tower details from lookupTower
                if tower_id and tower_id in lookupTower:
                    tower_detail = lookupTower[tower_id]
                    tower_address = tower_detail.get("ADDRESS") or ""
                    main_city = tower_detail.get("MAIN_CITY") or ""
                    sub_city = tower_detail.get("SUB_CITY") or ""
                    latitude = tower_detail.get("LATITUDE") or ""
                    longitude = tower_detail.get("LONGITUDE") or ""
                    azimuth = tower_detail.get("AZIMUTH") or ""
                    state = tower_detail.get("STATE") or ""
                else:
                    # Mark as not in database
                    if tower_id:
                        tower_address = "Latest Tower Id. Not Exists in Our Database"

                # Create unique key for IP + Tower combination
                tower_key = f"{tower_id}|{tower_address}" if tower_id or tower_address else "Unknown"

                if dest_ip not in ip_sessions:
                    ip_sessions[dest_ip] = {}

                if tower_key not in ip_sessions[dest_ip]:
                    ip_sessions[dest_ip][tower_key] = {
                        "tower_id": tower_id,
                        "roaming": roaming,
                        "roaming_circle": roaming_circle,
                        "roaming_operator": roaming_operator,
                        "tower_address": tower_address,
                        "main_city": main_city,
                        "sub_city": sub_city,
                        "latitude": latitude,
                        "longitude": longitude,
                        "azimuth": azimuth,
                        "state": state,
                        "session_count": 0
                    }

                ip_sessions[dest_ip][tower_key]["session_count"] += 1

            # Build report - Group by TowerID and combine all sessions
            tower_combined = {}

            for dest_ip, towers in ip_sessions.items():
                for tower_key, tower_data in towers.items():
                    tower_id = tower_data["tower_id"] or "Unknown"

                    if tower_id not in tower_combined:
                        # Format Lat-Long-Azimuth (handle missing tower data)
                        lat_long_azimuth = ""
                        if tower_data["latitude"] or tower_data["longitude"] or tower_data["azimuth"]:
                            lat = str(tower_data["latitude"]).strip()
                            lon = str(tower_data["longitude"]).strip()
                            azi = str(tower_data["azimuth"]).strip()
                            lat_long_azimuth = f"{lat} {lon} {azi}".strip()

                        tower_combined[tower_id] = {
                            "IPDR": nexus_data.get("IPDR", ""),
                            "TowerID": tower_data["tower_id"] or "",
                            "Roaming": tower_data["roaming"] or "",
                            "RoamingCircle": tower_data["roaming_circle"] or "",
                            "RoamingOperator": tower_data["roaming_operator"] or "",
                            "State": tower_data["state"] or "",
                            "TowerID_Address": tower_data["tower_address"] or "",
                            "Main_City": tower_data["main_city"] or "",
                            "Sub_City": tower_data["sub_city"] or "",
                            "Lat_Long_Azimuth": lat_long_azimuth,
                            "Total_Sessions": 0,
                            "Unique_Destination_IPs": set()
                        }

                    # Add sessions for this tower
                    tower_combined[tower_id]["Total_Sessions"] += tower_data["session_count"]
                    tower_combined[tower_id]["Unique_Destination_IPs"].add(dest_ip)

            # Convert to list and add IP count
            report = []
            for tower_id, tower_info in tower_combined.items():
                report.append({
                    "IPDR": tower_info["IPDR"],
                    "Total_Sessions": tower_info["Total_Sessions"],
                    "TowerID": tower_info["TowerID"],
                    "Roaming": tower_info["Roaming"],
                    # "RoamingCircle": tower_info["RoamingCircle"],
                    # "RoamingOperator": tower_info["RoamingOperator"],
                    "TowerID_Address": tower_info["TowerID_Address"],
                    "Main_City": tower_info["Main_City"],
                    "Sub_City": tower_info["Sub_City"],
                    "Lat_Long_Azimuth": tower_info["Lat_Long_Azimuth"],

                })

            # Sort by Total_Sessions (descending)
            sorted_report = sorted(report, key=lambda x: -x["Total_Sessions"])

        elif filter_type == "maxstaysession":
            # New logic for grouping by MSISDN and TowerID
            maxstaysession_map = defaultdict(lambda: {
                "count": 0,
                "tower_info": None
            })

            for row in data:
                msisdn = row.get("MSISDN") or row.get("msisdn") or row.get("SubscriberMSISDN")
                tower_id = row.get("TowerID")

                if not msisdn or not tower_id:
                    continue

                # Create unique key for MSISDN + TowerID combination
                key = f"{msisdn}|{tower_id}"

                # Initialize if first time
                if maxstaysession_map[key]["count"] == 0:
                    # Determine roaming status for this tower
                    roaming_circle = "Unknown"
                    roaming_operator = "Unknown"
                    roaming = "Unknown"

                    # Check roaming code
                    roamcode = row.get("RoamCode")
                    if roamcode and roamcode in lookupRoam:
                        roaming_circle = lookupRoam[roamcode]['circle']
                        roaming_operator = lookupRoam[roamcode]['operator']
                        roaming = f"{roaming_circle} ({roaming_operator})"

                    # Get tower details
                    tower_address = ""
                    main_city = ""
                    sub_city = ""
                    latitude = ""
                    longitude = ""
                    azimuth = ""
                    state = ""

                    if tower_id in lookupTower:
                        tower_detail = lookupTower[tower_id]
                        tower_address = tower_detail.get("ADDRESS") or ""
                        main_city = tower_detail.get("MAIN_CITY") or ""
                        sub_city = tower_detail.get("SUB_CITY") or ""
                        latitude = tower_detail.get("LATITUDE") or ""
                        longitude = tower_detail.get("LONGITUDE") or ""
                        azimuth = tower_detail.get("AZIMUTH") or ""
                        state = tower_detail.get("STATE") or ""

                    # Format Lat-Long-Azimuth
                    lat_long_azimuth = ""
                    if latitude or longitude:
                        coords = f"{latitude} {longitude}"
                        if azimuth:
                            coords += f" {azimuth}"
                        lat_long_azimuth = coords.strip()

                    maxstaysession_map[key]["tower_info"] = {
                        "tower_id": tower_id,
                        "roaming": roaming,
                        "roaming_circle": roaming_circle,
                        "roaming_operator": roaming_operator,
                        "tower_address": tower_address,
                        "main_city": main_city,
                        "sub_city": sub_city,
                        "state": state,
                        "lat_long_azimuth": lat_long_azimuth
                    }

                # Increment count
                maxstaysession_map[key]["count"] += 1

            # Convert to report format with the desired field structure
            report = []
            for key, info in maxstaysession_map.items():
                msisdn, tower_id = key.split("|", 1)

                # Get tower info
                tower_info = info["tower_info"]

                # Create report entry with consistent field structure
                report.append({
                    "IPDR": nexus_data.get("IPDR", ""),  # Use IPDR instead of IP
                    "MSISDN": msisdn,
                    "Total_Sessions": info["count"],  # Renamed from Count to Total_Sessions
                    "TowerID": tower_info["tower_id"] if tower_info else "",
                    "Roaming": tower_info["roaming"] if tower_info else "Unknown",
                    "RoamingCircle": tower_info["roaming_circle"] if tower_info else "Unknown",
                    "RoamingOperator": tower_info["roaming_operator"] if tower_info else "Unknown",
                    "TowerID_Address": tower_info["tower_address"] if tower_info else "",
                    "Main_City": tower_info["main_city"] if tower_info else "",
                    "Sub_City": tower_info["sub_city"] if tower_info else "",
                    # "State": tower_info["state"] if tower_info else "",
                    "Lat_Long_Azimuth": tower_info["lat_long_azimuth"] if tower_info else "",
                })

            # Sort by Total_Sessions descending
            sorted_report = sorted(report, key=lambda x: -x["Total_Sessions"])

        # Apply pagination
        total_records = len(sorted_report)
        start_index = (page - 1) * page_size
        end_index = start_index + page_size
        paginated_report = sorted_report[start_index:end_index]

        return Response({
            "Report": paginated_report,
            "Pagination": {
                "TotalRecords": total_records,
                "Page": page,
                "PageSize": page_size,
                "TotalPages": (total_records + page_size - 1) // page_size
            },
            "FilterType": filter_type
        }, status=200)