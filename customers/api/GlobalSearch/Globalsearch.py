from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from mongoengine import get_db
from bson import ObjectId
import ipaddress
from datetime import datetime


class GlobalSearchApi(APIView):
    MODULE_MAP = {
        "CDR": ("cdr_db", "CallDetailRecords"),
        "TowerDump": ("tower_dump", "TowerDumpRecords"),
        "WhatsApp": ("whatsapp_db", "WhatsAppRecords"),
        "IPDR": ("ipdr_db", "IPDetailRecords"),
    }

    FIELD_MAPPINGS = {
        "CDR": {
            "mob_no": ["A_Party"],
            "imei": ["IMEI"],
            "imsi": ["IMSI"],
            # CDR has no IP fields, so dest_ip not added
        },
        "TowerDump": {
            "mob_no": ["A_Party"],
            "imei": ["IMEI"],
            "imsi": ["IMSI"],
            # TowerDump has no IP fields
        },
        "WhatsApp": {
            "mob_no": ["Target"],
            "imei": ["IMEI"],
            "imsi": ["IMSI"],
            "dest_ip": ["Participant IP", "Target IP"],  # ✅ WhatsApp IP fields
        },
        "IPDR": {
            "mob_no": ["MSISDN"],
            "imei": ["IMEI"],
            "imsi": ["IMSI"],
            "dest_ip": ["Destination_ip", "Source_ip", "Translated_ip"],  # ✅ IPDR IP fields
        },
    }
    DATETIME_FIELDS = {
        "CDR": ["SDateTime", "EDateTime", "SDate", "DateTime", "Timestamp", "Call_Date", "Date", "Time"],
        "TowerDump": ["DateTime", "Timestamp", "Date", "Time"],
        "WhatsApp": ["DateTimeIST", "DateTimeUTC", "DateTime", "Timestamp", "Message_Date", "Date", "Time"],
        "IPDR": ["SDateTime", "EDateTime", "DateTime", "Timestamp", "Session_Date", "Date", "Time"],
    }

    # ✅ UPDATED: Complete column order with IP enrichment fields
    REPORT_COLUMN_ORDER = [
        "RecordType",
        "Target",
        "Other Party",
        "Start Date",
        "Start Time",
        "End Date",
        "End Time",
        "Duration",
        "Call Type",
        "DateTimeIST",  # WhatsApp IST datetime
        "DateTimeUTC",  # WhatsApp UTC datetime
        "First Cell ID",
        "First Cell ID Address",
        "Destination IP",
        "Participant IP",
        "Status",
        "ID",
        "Group ID",
        "IMEI",
        "Crime",
        "Target IP",
        "Source IP",
        "Source Port",
        "Translated IP",
        "Translated Port",
        "Destination Port",
        "Target Port",
        "Participant Port",
        "Last Cell ID",
        "Last Cell ID Address",
        "IMSI",
        "Circle",
        "Operator",
        "LRN",
        "Call Forward",
        "Upload",
        "Download",
        "Target Device",
        "Participant Device",
        "Main City(First CellID)",
        "Sub City(First CellID)",
        "Lat-Long-Azimuth (First CellID)",
        "Type",
        "Style",
        "Size",
        "HashCode",

        # ✅ IP Enrichment Fields - Source IP
        "Source_IP_Type",
        "Source_ISP/Org",
        "Source_Country",
        "Source_Location",
        "Source_Usage",
        "Source_Domains",
        "Source_VPN/Proxy/Tor",
        "Source_TSP/Broadband/Satellite",
        "Source_App/Hostname",
        "Source_IPLat",
        "Source_IPLong",
        "Source_IP_Map_Link",

        # ✅ IP Enrichment Fields - Destination IP
        "Destination_IP_Type",
        "Destination_ISP/Org",
        "Destination_Country",
        "Destination_Location",
        "Destination_Usage",
        "Destination_Domains",
        "Destination_VPN/Proxy/Tor",
        "Destination_TSP/Broadband/Satellite",
        "Destination_App/Hostname",
        "Destination_IPLat",
        "Destination_IPLong",
        "Destination_IP_Map_Link",

        # ✅ IP Enrichment Fields - Translated IP
        "Translated_IP_Type",
        "Translated_ISP/Org",
        "Translated_Country",
        "Translated_Location",
        "Translated_Usage",
        "Translated_Domains",
        "Translated_VPN/Proxy/Tor",
        "Translated_TSP/Broadband/Satellite",
        "Translated_App/Hostname",
        "Translated_IPLat",
        "Translated_IPLong",
        "Translated_IP_Map_Link",

        # ✅ WhatsApp Specific - Participant IP
        "Participant_IP_Type",
        "Participant_ISP/Org",
        "Participant_Country",
        "Participant_Location",
        "Participant_Usage",
        "Participant_Domains",
        "Participant_VPN/Proxy/Tor",
        "Participant_TSP/Broadband/Satellite",
        "Participant_App/Hostname",
        "Participant_IPLat",
        "Participant_IPLong",
        "Participant_IP_Map_Link",

        # ✅ WhatsApp Specific - Target IP
        "Target_IP_Type",
        "Target_ISP/Org",
        "Target_Country",
        "Target_Location",
        "Target_Usage",
        "Target_Domains",
        "Target_VPN/Proxy/Tor",
        "Target_TSP/Broadband/Satellite",
        "Target_App/Hostname",
        "Target_IPLat",
        "Target_IPLong",
        "Target_IP_Map_Link",

        # ✅ Port Enrichment Fields
        "Destination_Port_Type",
        "Destination_Port_Description",
        "Destination_Port_Category",
        "Source_Port_Type",
        "Source_Port_Description",
        "Source_Port_Category",
        "Participant_Port_Type",
        "Participant_Port_Description",
        "Participant_Port_Category",
        "Target_Port_Type",
        "Target_Port_Description",
        "Target_Port_Category",
    ]

    # ======================================================
    # PARTY NORMALIZATION
    # ======================================================
    def _add_party_fields(self, record, module):
        if module == "CDR":
            record["Target"] = record.get("A_Party")
            record["Other Party"] = record.get("B_Party")

        elif module == "IPDR":
            record["Target"] = record.get("MSISDN")
            record["Other Party"] = None

        elif module == "WhatsApp":
            record["Target"] = record.get("Target")
            record["Other Party"] = record.get("Participant")

        elif module == "TowerDump":
            record["Target"] = record.get("A_Party")
            record["Other Party"] = None

        # 🚫 Remove raw telecom fields forever
        for f in ["A_Party", "B_Party", "MSISDN", "Participant"]:
            record.pop(f, None)

    # ======================================================
    # DATETIME EXTRACTION
    # ======================================================
    def _extract_datetime(self, record, module):
        for field in self.DATETIME_FIELDS.get(module, []):
            val = record.get(field)
            if not val:
                continue
            if isinstance(val, datetime):
                return val
            if isinstance(val, str):
                # Try ISO format first (most common in your data)
                for fmt in (
                        "%Y-%m-%dT%H:%M:%S.%f+00:00",  # ISO with timezone
                        "%Y-%m-%dT%H:%M:%S.%f",  # ISO with milliseconds
                        "%Y-%m-%dT%H:%M:%S",  # ISO format
                        "%Y-%m-%d %H:%M:%S",
                        "%d-%m-%Y %H:%M:%S",
                        "%Y/%m/%d %H:%M:%S",
                        "%d/%m/%Y %H:%M:%S",
                        "%Y-%m-%d",
                        "%d-%m-%Y",
                ):
                    try:
                        # Remove timezone suffix for simpler parsing
                        val_clean = val.replace('+00:00', '').replace('Z', '')
                        return datetime.strptime(val_clean, fmt.replace('+00:00', ''))
                    except:
                        pass
        return None

    def _parse_datetime_string(self, val):
        """Parse a datetime string in various formats"""
        if not val:
            return None

        if isinstance(val, datetime):
            return val

        for fmt in (
                "%Y-%m-%dT%H:%M:%S.%f+00:00",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%d-%m-%Y %H:%M:%S",
                "%Y/%m/%d %H:%M:%S",
                "%d/%m/%Y %H:%M:%S",
                "%Y-%m-%d",
                "%d-%m-%Y",
        ):
            try:
                val_clean = val.replace('+00:00', '').replace('Z', '')
                return datetime.strptime(val_clean, fmt.replace('+00:00', ''))
            except:
                pass
        return None

    # ======================================================
    # IP TYPE HELPER
    # ======================================================
    def _ip_type(self, ip_str):
        """Determine if IP is IPv4 or IPv6"""
        try:
            ip = ipaddress.ip_address(ip_str)
            return "IPv6" if isinstance(ip, ipaddress.IPv6Address) else "IPv4"
        except:
            return None

    # ======================================================
    # DATETIME SORTING HELPER
    # ======================================================
    def _get_sort_datetime(self, record):
        """Extract datetime from formatted record for sorting (returns datetime object)"""
        dt = record.get("_sort_datetime")
        if dt and isinstance(dt, datetime):
            return dt
        return datetime.min

    # ======================================================
    # ✅ UPDATED: COMPLETE REPORT FORMATTER (ALL 44 COLUMNS)
    # ======================================================
    def _format_report_record(self, record, module):
        out = {}

        # Basic Info
        out["RecordType"] = module
        out["Target"] = record.get("Target")
        out["Other Party"] = record.get("Other Party")

        # Date / Time split
        dt = self._extract_datetime(record, module)
        if dt:
            out["Start Date"] = dt.strftime("%d/%b/%Y %H:%M:%S")
            out["Start Time"] = dt.strftime("%H:%M:%S")
            out["End Date"] = dt.strftime("%d/%b/%Y %H:%M:%S")
            out["End Time"] = dt.strftime("%H:%M:%S")
            # Store datetime object for sorting (will be removed before returning)
            out["_sort_datetime"] = dt
        else:
            out["_sort_datetime"] = datetime.min

        out["Duration"] = record.get("Duration")
        out["Call Type"] = record.get("Call_Type") or record.get("Call Type")

        # ✅ WhatsApp DateTime Fields (ISO format for both IST and UTC)
        if module == "WhatsApp":
            ist_dt = self._parse_datetime_string(record.get("DateTimeIST"))
            utc_dt = self._parse_datetime_string(record.get("DateTimeUTC"))

            if ist_dt:
                out["DateTimeIST"] = ist_dt.strftime("%Y-%m-%dT%H:%M:%S")
            if utc_dt:
                out["DateTimeUTC"] = utc_dt.strftime("%Y-%m-%dT%H:%M:%S")
        else:
            out["DateTimeIST"] = None
            out["DateTimeUTC"] = None

        # Cell ID (string to avoid scientific notation)
        if record.get("First_CGI"):
            out["First Cell ID"] = str(record.get("First_CGI"))

        out["First Cell ID Address"] = record.get("First Cell ID Address")

        # IP Fields
        out["Destination IP"] = record.get("Destination_ip") or record.get("Destination IP")
        out["Participant IP"] = record.get("Participant IP")
        out["Status"] = record.get("Status")
        out["ID"] = record.get("ID")
        out["Group ID"] = record.get("Group ID")

        # Subscriber
        out["IMEI"] = record.get("IMEI")
        out["Crime"] = record.get("Crime")

        # Network IPs and Ports
        out["Target IP"] = record.get("Target IP")
        out["Source IP"] = record.get("Source_ip") or record.get("Source IP")
        out["Source Port"] = record.get("Source_port") or record.get("Source Port")
        out["Translated IP"] = record.get("Translated_ip") or record.get("Translated IP")
        out["Translated Port"] = record.get("Translated_port") or record.get("Translated Port")
        out["Destination Port"] = record.get("Destination_port") or record.get("Destination Port")
        out["Target Port"] = record.get("Target Port")
        out["Participant Port"] = record.get("Participant Port")

        # Last Cell ID
        out["Last Cell ID"] = str(record.get("Last_CGI")) if record.get("Last_CGI") else None
        out["Last Cell ID Address"] = record.get("Last Cell ID Address")

        # Subscriber Info
        out["IMSI"] = record.get("IMSI")
        out["Circle"] = record.get("Circle")
        out["Operator"] = record.get("Operator")
        out["LRN"] = record.get("LRN")
        out["Call Forward"] = record.get("Call Forward")

        # Data Usage
        out["Upload"] = record.get("Upload")
        out["Download"] = record.get("Download")

        # Devices
        out["Target Device"] = record.get("Target Device")
        out["Participant Device"] = record.get("Participant Device")

        # Location
        out["Main City(First CellID)"] = record.get("Main City (First CellID)") or record.get("Main City(First CellID)")
        out["Sub City(First CellID)"] = record.get("Sub City (First CellID)") or record.get("Sub City(First CellID)")
        out["Lat-Long-Azimuth (First CellID)"] = record.get("Lat-Long-Azimuth (First CellID)")

        # File meta
        out["Type"] = record.get("Type")
        out["Style"] = record.get("Style")
        out["Size"] = record.get("Size")
        out["HashCode"] = record.get("HashCode")

        # ======================================================
        # ✅ IP ENRICHMENT FIELDS - Source IP
        # ======================================================
        out["Source_IP_Type"] = record.get("Source_IP_Type")
        out["Source_ISP/Org"] = record.get("Source_ISP/Org")
        out["Source_Country"] = record.get("Source_Country")
        out["Source_Location"] = record.get("Source_Location")
        out["Source_Usage"] = record.get("Source_Usage")
        out["Source_Domains"] = record.get("Source_Domains")
        out["Source_VPN/Proxy/Tor"] = record.get("Source_VPN/Proxy/Tor")
        out["Source_TSP/Broadband/Satellite"] = record.get("Source_TSP/Broadband/Satellite")
        out["Source_App/Hostname"] = record.get("Source_App/Hostname")
        out["Source_IPLat"] = record.get("Source_IPLat")
        out["Source_IPLong"] = record.get("Source_IPLong")
        out["Source_IP_Map_Link"] = record.get("Source_IP_Map_Link")

        # ======================================================
        # ✅ IP ENRICHMENT FIELDS - Destination IP
        # ======================================================
        out["Destination_IP_Type"] = record.get("Destination_IP_Type")
        out["Destination_ISP/Org"] = record.get("Destination_ISP/Org")
        out["Destination_Country"] = record.get("Destination_Country")
        out["Destination_Location"] = record.get("Destination_Location")
        out["Destination_Usage"] = record.get("Destination_Usage")
        out["Destination_Domains"] = record.get("Destination_Domains")
        out["Destination_VPN/Proxy/Tor"] = record.get("Destination_VPN/Proxy/Tor")
        out["Destination_TSP/Broadband/Satellite"] = record.get("Destination_TSP/Broadband/Satellite")
        out["Destination_App/Hostname"] = record.get("Destination_App/Hostname")
        out["Destination_IPLat"] = record.get("Destination_IPLat")
        out["Destination_IPLong"] = record.get("Destination_IPLong")
        out["Destination_IP_Map_Link"] = record.get("Destination_IP_Map_Link")

        # ======================================================
        # ✅ IP ENRICHMENT FIELDS - Translated IP
        # ======================================================
        out["Translated_IP_Type"] = record.get("Translated_IP_Type")
        out["Translated_ISP/Org"] = record.get("Translated_ISP/Org")
        out["Translated_Country"] = record.get("Translated_Country")
        out["Translated_Location"] = record.get("Translated_Location")
        out["Translated_Usage"] = record.get("Translated_Usage")
        out["Translated_Domains"] = record.get("Translated_Domains")
        out["Translated_VPN/Proxy/Tor"] = record.get("Translated_VPN/Proxy/Tor")
        out["Translated_TSP/Broadband/Satellite"] = record.get("Translated_TSP/Broadband/Satellite")
        out["Translated_App/Hostname"] = record.get("Translated_App/Hostname")
        out["Translated_IPLat"] = record.get("Translated_IPLat")
        out["Translated_IPLong"] = record.get("Translated_IPLong")
        out["Translated_IP_Map_Link"] = record.get("Translated_IP_Map_Link")

        # ======================================================
        # ✅ IP ENRICHMENT FIELDS - Participant IP (WhatsApp)
        # ======================================================
        out["Participant_IP_Type"] = record.get("Participant_IP_Type")
        out["Participant_ISP/Org"] = record.get("Participant_ISP/Org")
        out["Participant_Country"] = record.get("Participant_Country")
        out["Participant_Location"] = record.get("Participant_Location")
        out["Participant_Usage"] = record.get("Participant_Usage")
        out["Participant_Domains"] = record.get("Participant_Domains")
        out["Participant_VPN/Proxy/Tor"] = record.get("Participant_VPN/Proxy/Tor")
        out["Participant_TSP/Broadband/Satellite"] = record.get("Participant_TSP/Broadband/Satellite")
        out["Participant_App/Hostname"] = record.get("Participant_App/Hostname")
        out["Participant_IPLat"] = record.get("Participant_IPLat")
        out["Participant_IPLong"] = record.get("Participant_IPLong")
        out["Participant_IP_Map_Link"] = record.get("Participant_IP_Map_Link")

        # ======================================================
        # ✅ IP ENRICHMENT FIELDS - Target IP (WhatsApp)
        # ======================================================
        out["Target_IP_Type"] = record.get("Target_IP_Type")
        out["Target_ISP/Org"] = record.get("Target_ISP/Org")
        out["Target_Country"] = record.get("Target_Country")
        out["Target_Location"] = record.get("Target_Location")
        out["Target_Usage"] = record.get("Target_Usage")
        out["Target_Domains"] = record.get("Target_Domains")
        out["Target_VPN/Proxy/Tor"] = record.get("Target_VPN/Proxy/Tor")
        out["Target_TSP/Broadband/Satellite"] = record.get("Target_TSP/Broadband/Satellite")
        out["Target_App/Hostname"] = record.get("Target_App/Hostname")
        out["Target_IPLat"] = record.get("Target_IPLat")
        out["Target_IPLong"] = record.get("Target_IPLong")
        out["Target_IP_Map_Link"] = record.get("Target_IP_Map_Link")

        # ======================================================
        # ✅ PORT ENRICHMENT FIELDS
        # ======================================================
        out["Destination_Port_Type"] = record.get("Destination_Port_Type")
        out["Destination_Port_Description"] = record.get("Destination_Port_Description")
        out["Destination_Port_Category"] = record.get("Destination_Port_Category")
        out["Source_Port_Type"] = record.get("Source_Port_Type")
        out["Source_Port_Description"] = record.get("Source_Port_Description")
        out["Source_Port_Category"] = record.get("Source_Port_Category")
        out["Participant_Port_Type"] = record.get("Participant_Port_Type")
        out["Participant_Port_Description"] = record.get("Participant_Port_Description")
        out["Participant_Port_Category"] = record.get("Participant_Port_Category")
        out["Target_Port_Type"] = record.get("Target_Port_Type")
        out["Target_Port_Description"] = record.get("Target_Port_Description")
        out["Target_Port_Category"] = record.get("Target_Port_Category")

        # 🔒 Enforce strict order
        ordered = {k: out.get(k) for k in self.REPORT_COLUMN_ORDER}

        # ✅ IMPORTANT: Keep None values to maintain column structure
        return ordered

    # ======================================================
    # POST
    # ======================================================
    def post(self, request):
        mob_no = request.data.get("mob_no")
        imei = request.data.get("imei")
        imsi = request.data.get("imsi")
        dest_ip = request.data.get("dest_ip")
        modules = request.data.get("modules", "all")


        if not any([mob_no, imei, imsi,dest_ip]):
            return Response({"error": "Provide one search parameter"}, status=400)

        filters = {
            "mob_no": mob_no,
            "imei": imei,
            "imsi": imsi,
            "dest_ip": dest_ip
        }

        filter_key, filter_value = next(
            ((k, v) for k, v in filters.items() if v),
            (None, None)
        )

        if modules == "all":
            modules = list(self.MODULE_MAP.keys())

        final_records = []

        for module in modules:
            db_alias, collection_name = self.MODULE_MAP[module]
            db = get_db(alias=db_alias)
            collection = db[collection_name]

            fields = self.FIELD_MAPPINGS[module].get(filter_key)
            if not fields:
                continue

            query = {"$or": [{f: str(filter_value)} for f in fields]}
            records = list(collection.find(query))

            # Clean records
            for r in records:
                r.pop("_id", None)
                r.pop("seq_id", None)

            # ✅ CRITICAL FIX: Enrich records BEFORE formatting
            if records:
                records = self.enrich_records(records, module)

            # Format and add to results
            for r in records:
                self._add_party_fields(r, module)
                formatted = self._format_report_record(r, module)
                final_records.append(formatted)

        # ✅ Sort records by datetime (newest first)
        # This works across all record types (CDR, IPDR, WhatsApp, TowerDump)
        # WhatsApp uses DateTimeIST which is converted to datetime object for comparison
        # All datetimes are normalized to datetime objects before sorting
        final_records.sort(key=lambda x: self._get_sort_datetime(x), reverse=True)

        # Remove internal sorting field
        for record in final_records:
            record.pop("_sort_datetime", None)

        return Response({
            "status": "success",
            "total_records": len(final_records),
            "records": final_records
        })

    # ======================================================
    # SEARCH ALL COLLECTIONS
    # ======================================================
    def search_all_collections(self, filter_key, filter_value, seq_ids_filter, modules):
        output = {}
        for key in modules:
            db_alias, col_name = self.MODULE_MAP[key]
            output[key] = self.search_mongodb_collection(
                key, db_alias, col_name, filter_key, filter_value, seq_ids_filter
            )
        return output

    # ======================================================
    # SEARCH SINGLE COLLECTION
    # ======================================================
    def search_mongodb_collection(self, collection_key, db_alias, collection_name,
                                  filter_key, filter_value, seq_ids_filter):
        try:
            db = get_db(alias=db_alias)
            collection = db[collection_name]

            fields = self.FIELD_MAPPINGS.get(collection_key, {}).get(filter_key)
            if not fields:
                return {"collection": collection_name, "count": 0, "records": []}

            query = {"$or": [{f: str(filter_value)} for f in fields]}
            if seq_ids_filter:
                query["seq_id"] = {"$in": seq_ids_filter}

            records = list(collection.find(query))

            for r in records:
                r.pop("_id", None)
                r.pop("seq_id", None)

            if records:
                records = self.enrich_records(records, collection_key)

            if collection_key == "WhatsApp":
                records = [self._order_whatsapp_record(r) for r in records]
            for r in records:
                r.pop('Module', None)

            return {
                "collection": collection_name,
                "count": len(records),
                "records": records
            }

        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"collection": collection_name, "count": 0, "records": [], "error": str(e)}

    def _get_sort_datetime(self, record):
        """
        Unified sorting across ALL modules.
        Priority:
        1. Start Date + Start Time
        2. WhatsApp DateTimeIST
        3. WhatsApp DateTimeUTC
        """

        # 1️⃣ Start Date + Start Time
        sd = record.get("Start Date")
        st = record.get("Start Time")
        if sd and st:
            try:
                return datetime.strptime(
                    f"{sd} {st}", "%d/%b/%Y %H:%M:%S"
                )
            except:
                pass

        # 2️⃣ WhatsApp IST
        ist = record.get("DateTimeIST")
        if ist:
            try:
                return datetime.strptime(ist, "%Y-%m-%dT%H:%M:%S")
            except:
                pass

        # 3️⃣ WhatsApp UTC
        utc = record.get("DateTimeUTC")
        if utc:
            try:
                return datetime.strptime(utc, "%Y-%m-%dT%H:%M:%S")
            except:
                pass

        return datetime.min

    # ======================================================
    # ✅ ENRICH RECORDS (WITH IP INFORMATION)
    # ======================================================
    def enrich_records(self, records, collection_key):
        from ..models import CellTower, MobileOperator, MccMnc, ImeiDetails
        from ..ipdr.ipdr_models.ip_model import PortInfo
        from ..serializers import (
            CellTowerSerializer,
            MobileOperatorSerializer,
            MccMncSerializer,
            DeviceInfoSerializer
        )
        from ..ipdr.ip_serializers import PortInfoSerializer
        from ..searchengine import search_ip

        cell_ids, ap_codes, imsi_codes, tac_numbers = set(), set(), set(), set()
        dest_ips, source_ips, translated_ips, dest_ports = set(), set(), set(), set()

        # ======================================================
        # PHASE 1 → COLLECT KEYS
        # ======================================================
        for r in records:
            if r.get("First_CGI"):
                cell_ids.add(str(r["First_CGI"]))

            a = r.get("A_Party") or r.get("MSISDN") or r.get("Target")
            if a and str(a).isdigit() and len(str(a)) >= 4:
                r["a_mobile_code"] = int(str(a)[:4])
                ap_codes.add(r["a_mobile_code"])

            if r.get("IMSI_CODE"):
                imsi_codes.add(str(r["IMSI_CODE"]))

            imei = r.get("IMEI")
            if imei and len(str(imei)) >= 8:
                r["IMEI_TAC"] = int(str(imei)[:8])
                tac_numbers.add(r["IMEI_TAC"])

            if collection_key in ["IPDR", "WhatsApp"]:
                if r.get("Participant IP"):
                    dest_ips.add(r["Participant IP"])
                if r.get("Destination_ip"):
                    dest_ips.add(r["Destination_ip"])
                if r.get("Source_ip"):
                    source_ips.add(r["Source_ip"])
                if r.get("Translated_ip") or r.get("Target IP"):
                    translated_ips.add(r.get("Translated_ip") or r.get("Target IP"))
                if r.get("Destination_port") or r.get("Participant Port"):
                    dest_ports.add(r.get("Destination_port") or r.get("Participant Port"))

        # ======================================================
        # PHASE 2 → LOOKUPS
        # ======================================================
        lookupTower = {
            t["id"]: t for t in
            CellTowerSerializer(CellTower.objects(id__in=list(cell_ids)), many=True).data
        } if cell_ids else {}

        lookupAp = {
            o["id"]: o for o in
            MobileOperatorSerializer(MobileOperator.objects(id__in=list(ap_codes)), many=True).data
        } if ap_codes else {}

        lookupImsi = {
            i["mccmnc_temp"]: i for i in
            MccMncSerializer(MccMnc.objects(mccmnc_temp__in=list(imsi_codes)), many=True).data
        } if imsi_codes else {}

        lookupTac = {
            t["id"]: t for t in
            DeviceInfoSerializer(ImeiDetails.objects(id__in=list(tac_numbers)), many=True).data
        } if tac_numbers else {}

        lookupIP = {}
        if collection_key in ["IPDR", "WhatsApp"] and (dest_ips | source_ips | translated_ips):
            ip_info = search_ip(list(dest_ips | source_ips | translated_ips))
            lookupIP = {rec["ip"]: rec for rec in ip_info.get("results", [])}

        lookupPort = {}
        if collection_key in ["IPDR", "WhatsApp"] and dest_ports:
            lookupPort = {
                p.Port: PortInfoSerializer(p).data
                for p in PortInfo.objects.filter(Port__in=list(dest_ports))
            }

        # ======================================================
        # PHASE 3 → ENRICH RECORDS
        # ======================================================
        for r in records:

            # ---------------- CGI ----------------
            tower = lookupTower.get(str(r.get("First_CGI")))
            if tower:
                r.update({
                    "First Cell ID Address": tower.get("ADDRESS"),
                    "Main City (First CellID)": tower.get("MAIN_CITY"),
                    "Sub City (First CellID)": tower.get("SUB_CITY"),
                })

            # ---------------- IMEI ----------------
            if r.get("IMEI_TAC") in lookupTac:
                dev = lookupTac[r["IMEI_TAC"]]
                r["IMEI Manufacturer"] = dev.get("manufacturer")
                r["Device Type"] = dev.get("devicetype")

            # ---------------- OPERATOR ----------------
            imsi = r.get("IMSI_CODE")
            if imsi and imsi in lookupImsi:
                r["Circle"] = lookupImsi[imsi].get("circle")
                r["Operator"] = lookupImsi[imsi].get("operator")
            elif r.get("a_mobile_code") in lookupAp:
                r["Circle"] = lookupAp[r["a_mobile_code"]].get("Circle")
                r["Operator"] = lookupAp[r["a_mobile_code"]].get("Operator")

            r["Provider"] = (
                f"{r.get('Circle')}-{r.get('Operator')}"
                if r.get("Circle") and r.get("Operator") else None
            )

            # ==================================================
            # ✅ IP ENRICHMENT FOR ALL MODULES (IPDR, WhatsApp, etc.)
            # ==================================================
            if collection_key in ["IPDR", "WhatsApp"]:

                # -------- Source IP Enrichment --------
                source_ip = r.get("Source_ip") or r.get("Source IP")
                if source_ip and source_ip in lookupIP:
                    ip = lookupIP[source_ip]
                    r.update({
                        "Source_IP_Type": self._ip_type(source_ip),
                        "Source_ISP/Org": ip.get("Isp/Org"),
                        "Source_Country": ip.get("Country"),
                        "Source_Location": ip.get("Location"),
                        "Source_Usage": ip.get("Usage"),
                        "Source_Domains": ip.get("Domains"),
                        "Source_VPN/Proxy/Tor": ip.get("VPN/Proxy/Tor"),
                        "Source_TSP/Broadband/Satellite": ip.get("TSP/Broadband/Satellite"),
                        "Source_App/Hostname": (
                            ip.get("Domains", "").split(",")[0] if ip.get("Domains") else None),
                        "Source_IPLat": ip.get("IPLat"),
                        "Source_IPLong": ip.get("IPLong"),
                    })
                    if ip.get("IPLat") and ip.get("IPLong"):
                        r[
                            "Source_IP_Map_Link"] = f"https://maps.google.com/maps/?q={ip.get('IPLat')},{ip.get('IPLong')}"

                # -------- Destination IP Enrichment --------
                dest_ip = r.get("Destination_ip") or r.get("Destination IP")
                if dest_ip and dest_ip in lookupIP:
                    ip = lookupIP[dest_ip]
                    r.update({
                        "Destination_IP_Type": self._ip_type(dest_ip),
                        "Destination_ISP/Org": ip.get("Isp/Org"),
                        "Destination_Country": ip.get("Country"),
                        "Destination_Location": ip.get("Location"),
                        "Destination_Usage": ip.get("Usage"),
                        "Destination_Domains": ip.get("Domains"),
                        "Destination_VPN/Proxy/Tor": ip.get("VPN/Proxy/Tor"),
                        "Destination_TSP/Broadband/Satellite": ip.get("TSP/Broadband/Satellite"),
                        "Destination_App/Hostname": (
                            ip.get("Domains", "").split(",")[0] if ip.get("Domains") else None),
                        "Destination_IPLat": ip.get("IPLat"),
                        "Destination_IPLong": ip.get("IPLong"),
                    })
                    if ip.get("IPLat") and ip.get("IPLong"):
                        r[
                            "Destination_IP_Map_Link"] = f"https://maps.google.com/maps/?q={ip.get('IPLat')},{ip.get('IPLong')}"

                # -------- Translated IP Enrichment --------
                trans_ip = r.get("Translated_ip") or r.get("Translated IP")
                if trans_ip and trans_ip in lookupIP:
                    ip = lookupIP[trans_ip]
                    r.update({
                        "Translated_IP_Type": self._ip_type(trans_ip),
                        "Translated_ISP/Org": ip.get("Isp/Org"),
                        "Translated_Country": ip.get("Country"),
                        "Translated_Location": ip.get("Location"),
                        "Translated_Usage": ip.get("Usage"),
                        "Translated_Domains": ip.get("Domains"),
                        "Translated_VPN/Proxy/Tor": ip.get("VPN/Proxy/Tor"),
                        "Translated_TSP/Broadband/Satellite": ip.get("TSP/Broadband/Satellite"),
                        "Translated_App/Hostname": (
                            ip.get("Domains", "").split(",")[0] if ip.get("Domains") else None),
                        "Translated_IPLat": ip.get("IPLat"),
                        "Translated_IPLong": ip.get("IPLong"),
                    })
                    if ip.get("IPLat") and ip.get("IPLong"):
                        r[
                            "Translated_IP_Map_Link"] = f"https://maps.google.com/maps/?q={ip.get('IPLat')},{ip.get('IPLong')}"

                # -------- Port Enrichment (Destination & Source) --------
                dest_port = r.get("Destination_port") or r.get("Destination Port")
                if dest_port and dest_port in lookupPort:
                    p = lookupPort[dest_port]
                    r.update({
                        "Destination_Port_Type": p.get("Type"),
                        "Destination_Port_Description": p.get("Description"),
                        "Destination_Port_Category": p.get("Category"),
                    })

                source_port = r.get("Source_port") or r.get("Source Port")
                if source_port and source_port in lookupPort:
                    p = lookupPort[source_port]
                    r.update({
                        "Source_Port_Type": p.get("Type"),
                        "Source_Port_Description": p.get("Description"),
                        "Source_Port_Category": p.get("Category"),
                    })

            # ==================================================
            # ✅ WHATSAPP-SPECIFIC IP + PORT ENRICHMENT
            # ==================================================
            if collection_key == "WhatsApp":

                # -------- Participant IP --------
                pip = r.get("Participant IP")
                if pip and pip in lookupIP:
                    ip = lookupIP[pip]
                    r.update({
                        "Participant_IP_Type": self._ip_type(pip),
                        "Participant_ISP/Org": ip.get("Isp/Org"),
                        "Participant_Country": ip.get("Country"),
                        "Participant_Location": ip.get("Location"),
                        "Participant_Usage": ip.get("Usage"),
                        "Participant_Domains": ip.get("Domains"),
                        "Participant_VPN/Proxy/Tor": ip.get("VPN/Proxy/Tor"),
                        "Participant_TSP/Broadband/Satellite": ip.get("TSP/Broadband/Satellite"),
                        "Participant_App/Hostname": (
                            ip.get("Domains", "").split(",")[0] if ip.get("Domains") else None),
                        "Participant_IPLat": ip.get("IPLat"),
                        "Participant_IPLong": ip.get("IPLong"),
                    })

                    if ip.get("IPLat") and ip.get("IPLong"):
                        r[
                            "Participant_IP_Map_Link"] = f"https://maps.google.com/maps/?q={ip.get('IPLat')},{ip.get('IPLong')}"

                # -------- Target IP --------
                tip = r.get("Target IP") or r.get("Translated_ip")
                if tip and tip in lookupIP:
                    ip = lookupIP[tip]
                    r.update({
                        "Target_IP_Type": self._ip_type(tip),
                        "Target_ISP/Org": ip.get("Isp/Org"),
                        "Target_Country": ip.get("Country"),
                        "Target_Location": ip.get("Location"),
                        "Target_Usage": ip.get("Usage"),
                        "Target_Domains": ip.get("Domains"),
                        "Target_VPN/Proxy/Tor": ip.get("VPN/Proxy/Tor"),
                        "Target_TSP/Broadband/Satellite": ip.get("TSP/Broadband/Satellite"),
                        "Target_App/Hostname": (
                            ip.get("Domains", "").split(",")[0] if ip.get("Domains") else None),
                        "Target_IPLat": ip.get("IPLat"),
                        "Target_IPLong": ip.get("IPLong"),
                    })

                    if ip.get("IPLat") and ip.get("IPLong"):
                        r[
                            "Target_IP_Map_Link"] = f"https://maps.google.com/maps/?q={ip.get('IPLat')},{ip.get('IPLong')}"

                # -------- Ports --------
                port = r.get("Participant Port")
                if port and port in lookupPort:
                    p = lookupPort[port]
                    r.update({
                        "Participant_Port_Type": p.get("Type"),
                        "Participant_Port_Description": p.get("Description"),
                        "Participant_Port_Category": p.get("Category"),
                        "Target_Port_Type": p.get("Type"),
                        "Target_Port_Description": p.get("Description"),
                        "Target_Port_Category": p.get("Category"),
                    })

                # Set WhatsApp-specific mappings
                r["Target IP"] = pip
                r["Target Port"] = port
                r["Target Device"] = r.get("Participant Device")
                r["Status"] = "Completed" if r.get("Call_Type") else "Unknown"

            r.pop("seq_id", None)
            r.pop("_id", None)

        return records

    # ======================================================
    # WHATSAPP RECORD ORDERING (if needed)
    # ======================================================
    def _order_whatsapp_record(self, record):
        """Optional: Reorder WhatsApp-specific fields"""
        # Implement if you have specific ordering requirements for WhatsApp
        return record