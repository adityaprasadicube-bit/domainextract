import json
import os
from collections import defaultdict

from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from mongoengine import get_db
from bson import ObjectId
import ipaddress
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

sdr_clomns = os.path.join(settings.BASE_DIR, "api", "data", "column_config.json")


class TrioMappingApi(APIView):
    MODULE_MAP = {
        "CDR": ("cdr_db", "CallDetailRecords"),
        "TowerDump": ("tower_dump", "TowerDumpRecords"),
        "WhatsApp": ("whatsapp_db", "WhatsAppRecords"),
        "IPDR": ("ipdr_db", "IPDetailRecords"),
    }

    FIELD_MAPPINGS = {
        "CDR": {"mob_no": ["A_Party"], "imei": ["IMEI"], "imsi": ["IMSI"]},
        "TowerDump": {"mob_no": ["A_Party"], "imei": ["IMEI"], "imsi": ["IMSI"]},
        "WhatsApp": {"mob_no": ["Target"], "imei": ["IMEI"], "imsi": ["IMSI"]},
        "IPDR": {"mob_no": ["MSISDN"], "imei": ["IMEI"], "imsi": ["IMSI"]},
    }

    DATETIME_FIELDS = {
        "CDR": ["SDateTime", "EDateTime", "SDate", "DateTime", "Timestamp", "Call_Date", "Date", "Time"],
        "TowerDump": ["DateTime", "Timestamp", "Date", "Time"],
        "WhatsApp": ["DateTimeIST", "DateTimeUTC", "DateTime", "Timestamp", "Message_Date", "Date", "Time"],
        "IPDR": ["SDateTime", "EDateTime", "DateTime", "Timestamp", "Session_Date", "Date", "Time"],
    }

    REPORT_COLUMN_ORDER = [
        "RecordType", "Target", "Other Party",
        # --- SDR: Target (A-Party) ---
        "TargetName", "TargetFatherName", "TargetGender", "TargetNationality",
        "TargetNumberSDR", "TargetAlternateNo", "TargetEmail",
        "TargetAddressSDR", "TargetLocalAddress", "TargetPOAAddress",
        "TargetPOINo", "TargetPOANo", "TargetPOAID",
        "TargetPOAName", "TargetPOANAME", "TargetPOANAME_ID",
        "TargetDOB", "TargetDOA",
        "TargetIMSI", "TargetConnectionType", "TargetPointOfSale", "TargetADDDB",
        # --- SDR: Other Party (B-Party) ---
        "OtherPartyName", "OtherPartyFatherName", "OtherPartyGender", "OtherPartyNationality",
        "OtherPartyNumberSDR", "OtherPartyAlternateNo", "OtherPartyEmail",
        "OtherPartyAddressSDR", "OtherPartyLocalAddress", "OtherPartyPOAAddress",
        "OtherPartyPOINo", "OtherPartyPOANo", "OtherPartyPOAID",
        "OtherPartyPOAName", "OtherPartyPOANAME", "OtherPartyPOANAME_ID",
        "OtherPartyDOB", "OtherPartyDOA",
        "OtherPartyIMSI", "OtherPartyConnectionType", "OtherPartyPointOfSale", "OtherPartyADDDB",
        # --- Existing fields ---
        "Start Date", "Start Time", "End Date", "End Time",
        "Duration", "Call Type",
        "DateTimeIST", "DateTimeUTC",
        "First Cell ID", "First Cell ID Address",
        "Destination IP", "Participant IP",
        "Status", "ID", "Group ID", "IMEI", "Crime",
        "Target IP", "Source IP", "Source Port",
        "Translated IP", "Translated Port",
        "Destination Port", "Target Port", "Participant Port",
        "Last Cell ID", "Last Cell ID Address",
        "IMSI", "Circle", "Operator", "LRN", "Call Forward",
        "Upload", "Download",
        "Target Device", "Participant Device",
        "Main City(First CellID)", "Sub City(First CellID)",
        "Lat-Long-Azimuth (First CellID)",
        "Type", "Style", "Size", "HashCode",
        "Source_IP_Type", "Source_ISP/Org", "Source_Country", "Source_Location",
        "Source_Usage", "Source_Domains", "Source_VPN/Proxy/Tor",
        "Source_TSP/Broadband/Satellite", "Source_App/Hostname",
        "Source_IPLat", "Source_IPLong", "Source_IP_Map_Link",
        "Destination_IP_Type", "Destination_ISP/Org", "Destination_Country", "Destination_Location",
        "Destination_Usage", "Destination_Domains", "Destination_VPN/Proxy/Tor",
        "Destination_TSP/Broadband/Satellite", "Destination_App/Hostname",
        "Destination_IPLat", "Destination_IPLong", "Destination_IP_Map_Link",
        "Translated_IP_Type", "Translated_ISP/Org", "Translated_Country", "Translated_Location",
        "Translated_Usage", "Translated_Domains", "Translated_VPN/Proxy/Tor",
        "Translated_TSP/Broadband/Satellite", "Translated_App/Hostname",
        "Translated_IPLat", "Translated_IPLong", "Translated_IP_Map_Link",
        "Participant_IP_Type", "Participant_ISP/Org", "Participant_Country", "Participant_Location",
        "Participant_Usage", "Participant_Domains", "Participant_VPN/Proxy/Tor",
        "Participant_TSP/Broadband/Satellite", "Participant_App/Hostname",
        "Participant_IPLat", "Participant_IPLong", "Participant_IP_Map_Link",
        "Target_IP_Type", "Target_ISP/Org", "Target_Country", "Target_Location",
        "Target_Usage", "Target_Domains", "Target_VPN/Proxy/Tor",
        "Target_TSP/Broadband/Satellite", "Target_App/Hostname",
        "Target_IPLat", "Target_IPLong", "Target_IP_Map_Link",
        "Destination_Port_Type", "Destination_Port_Description", "Destination_Port_Category",
        "Source_Port_Type", "Source_Port_Description", "Source_Port_Category",
        "Participant_Port_Type", "Participant_Port_Description", "Participant_Port_Category",
        "Target_Port_Type", "Target_Port_Description", "Target_Port_Category",
    ]

    # Column order for Summary report - matches the image layout
    SUMMARY_COLUMN_ORDER = [
        "RecordType",
        "Target",
        "TargetName",
        "TargetAddress",
        "TargetDOB",
        "Other Party",
        "OtherPartyName",
        "OtherPartyAddress",
        "OtherPartyDOB",
        "ExistsIn",
        "Total Freq",
        "Call Freq",
        "Call In",
        "Call Out",
        "Sms In",
        "Sms Out",
        "Other Calls",
        "Cdr DateRange",
        "Ipdr Freq",
        "Ipdr DateRange",
        "Wap Freq",
        "Msg",
        "Group Msg",
        "Video Call",
        "Audio Call",
        "Group Call",
        "Other Types",
        "Wap DateRange",
    ]

    # ======================================================
    # SDR LOOKUP — fetch from MongoDB subscribers collection
    # ======================================================
    def _fetch_sdr_details(self, numbers: set, sdr_collection, msisdn_fields: list) -> dict:
        """
        Fetch SDR subscriber details for a set of phone numbers.
        Returns dict keyed by MSISDN string → subscriber doc.
        Mirrors the same pattern used in TowerDumpDetailRecordDetailView.
        """
        if not numbers:
            return {}

        projection = {field: 1 for field in msisdn_fields}
        msisdn_list = [str(n) for n in numbers]

        logger.info(f"🔍 SDR lookup for {len(msisdn_list)} numbers...")

        cursor = sdr_collection.find(
            {"_id": {"$in": msisdn_list}},
            projection
        )

        result = {}
        for doc in cursor:
            key = doc.get("_id")
            if key:
                doc.pop("_id", None)
                result[str(key)] = doc

        logger.info(f"✅ SDR found {len(result)} records")
        return result

    @staticmethod
    def _add_if_present(target_dict, key, value):
        """Only add key to dict if value is non-empty (mirrors TowerDump helper)."""
        if value not in (None, "", [], {}):
            target_dict[key] = value

    def _attach_sdr_fields(self, record: dict, sdr_info: dict, prefix: str):
        """
        Attach SDR subscriber fields to a record using the given prefix.
        prefix = 'Target' → TargetName, TargetDOB, etc.
        prefix = 'OtherParty' → OtherPartyName, OtherPartyDOB, etc.

        Field mapping mirrors the Apartyinfo/Bpartyinfo pattern in
        TowerDumpDetailRecordDetailView exactly.
        """
        if not sdr_info:
            return

        add = self._add_if_present

        # Basic Details
        add(record, f"{prefix}Name", sdr_info.get("Name"))
        add(record, f"{prefix}FatherName", sdr_info.get("Father"))
        add(record, f"{prefix}Gender", sdr_info.get("Gender"))
        add(record, f"{prefix}Nationality", sdr_info.get("Nationality"))

        # Contact Details
        add(record, f"{prefix}AlternateNo", sdr_info.get("Alternate No"))
        add(record, f"{prefix}Email", sdr_info.get("Email"))

        # Address Details
        add(record, f"{prefix}AddressSDR", sdr_info.get("Address"))
        add(record, f"{prefix}Address", sdr_info.get("Address"))  # For summary
        add(record, f"{prefix}LocalAddress", sdr_info.get("Local Address"))
        add(record, f"{prefix}POAAddress", sdr_info.get("POAAddress"))

        # Identity Details
        add(record, f"{prefix}POINo", sdr_info.get("POI No"))
        add(record, f"{prefix}POANo", sdr_info.get("POA No"))
        add(record, f"{prefix}POAID", sdr_info.get("POA ID"))

        # POA / POI Name
        add(record, f"{prefix}POAName", sdr_info.get("POA Name"))
        add(record, f"{prefix}POANAME", sdr_info.get("POANAME"))
        add(record, f"{prefix}POANAME_ID", sdr_info.get("POANAME_id"))

        # Dates
        add(record, f"{prefix}DOB", sdr_info.get("DOB"))
        add(record, f"{prefix}DOA", sdr_info.get("DOA"))

        # Technical Details
        add(record, f"{prefix}IMSI", sdr_info.get("IMSI"))
        add(record, f"{prefix}ConnectionType", sdr_info.get("Connection Type"))
        add(record, f"{prefix}PointOfSale", sdr_info.get("Point of Sale"))

        # Extra
        add(record, f"{prefix}ADDDB", sdr_info.get("ADDDB"))

    @staticmethod
    def _parse_bool(value, default=False):
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in ('true', '1', 'yes')
        return default

    # ======================================================
    # INTEGRATED B-PARTY IDENTIFICATION LOGIC
    # ======================================================

    def _normalize_ips(self, ip):
        if not ip:
            return []
        if isinstance(ip, list):
            return [str(i).strip() for i in ip if i]
        if isinstance(ip, str):
            return [ip.strip()]
        return [str(ip).strip()]

    def _build_ipdr_bparty_map_mobile_vs_others(self, ipdr_records=None):
        from datetime import timedelta

        db = get_db(alias="ipdr_db")
        nexus = db["IPdrNexus"]
        ipdr = db["IPDetailRecords"]

        mobile_sessions = []
        for r in ipdr_records:
            dip = r.get("Destination_ip")
            dport = r.get("Destination_port")
            dt = r.get("SDateTime")
            if dip and dport and dt:
                mobile_sessions.append((str(dip).strip(), int(dport), dt))

        if not mobile_sessions:
            return {}

        mobile_seq_ids = set()
        for r in ipdr_records:
            sid = r.get("seq_id")
            if isinstance(sid, list):
                mobile_seq_ids.update(sid)
            elif sid:
                mobile_seq_ids.add(sid)

        mobile_nexus = list(nexus.find({"_id": {"$in": list(mobile_seq_ids)}}))
        crime_ids = {n.get("CrimeID") for n in mobile_nexus if n.get("CrimeID")}

        public_nexus = list(nexus.find({
            "CrimeID": {"$in": list(crime_ids)},
            "RecordType": {"$in": ["Public IP", "IP", "Destination IP"]}
        }))
        public_seq_ids = [n["_id"] for n in public_nexus]

        public_records = list(ipdr.find(
            {"seq_id": {"$in": public_seq_ids}},
            {"MSISDN": 1, "IMSI": 1, "IMEI": 1, "Translated_ip": 1, "Translated_port": 1, "SDateTime": 1}
        ))

        index = defaultdict(list)
        for r in public_records:
            ip = r.get("Translated_ip")
            port = r.get("Translated_port")
            dt = r.get("SDateTime")
            if ip and port and dt:
                index[(str(ip).strip(), int(port))].append(r)

        bparty_map = {}
        for ip_val, port_val, time_val in mobile_sessions:
            key = (ip_val, port_val)
            if key not in index:
                continue
            start_window = time_val - timedelta(minutes=60)
            end_window = time_val + timedelta(minutes=60)
            for rec in index[key]:
                rec_time = rec.get("SDateTime")
                if start_window <= rec_time <= end_window:
                    b_party = rec.get("MSISDN") or rec.get("IMSI") or rec.get("IMEI")
                    if b_party:
                        bparty_map[ip_val] = str(b_party)
                        break

        return bparty_map

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
            if not hasattr(self, "ipdr_bparty_map") or not self.ipdr_bparty_map:
                return
            dest_ip_raw = (
                    record.get("Destination_ip") or
                    record.get("Destination IP") or
                    record.get("Destination_ip_original")
            )
            if not dest_ip_raw:
                return
            for ip in self._normalize_ips(dest_ip_raw):
                ip_clean = str(ip).strip() if ip else None
                if ip_clean and ip_clean in self.ipdr_bparty_map:
                    record["Other Party"] = self.ipdr_bparty_map[ip_clean]
                    break
        elif module == "WhatsApp":
            record["Target"] = record.get("Target")
            record["Other Party"] = record.get("Participant")
        elif module == "TowerDump":
            record["Target"] = record.get("A_Party")
            record["Other Party"] = None

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
                for fmt in (
                        "%Y-%m-%dT%H:%M:%S.%f+00:00", "%Y-%m-%dT%H:%M:%S.%f",
                        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                        "%d-%m-%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S",
                        "%d/%m/%Y %H:%M:%S", "%Y-%m-%d", "%d-%m-%Y",
                ):
                    try:
                        return datetime.strptime(val.replace('+00:00', '').replace('Z', ''), fmt.replace('+00:00', ''))
                    except:
                        pass
        return None

    def _parse_datetime_string(self, val):
        if not val:
            return None
        if isinstance(val, datetime):
            return val
        for fmt in (
                "%Y-%m-%dT%H:%M:%S.%f+00:00", "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%d-%m-%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S",
                "%d/%m/%Y %H:%M:%S", "%Y-%m-%d", "%d-%m-%Y",
        ):
            try:
                return datetime.strptime(val.replace('+00:00', '').replace('Z', ''), fmt.replace('+00:00', ''))
            except:
                pass
        return None

    def _extract_raw_datetime(self, record, module):
        for field in self.DATETIME_FIELDS.get(module, []):
            val = record.get(field)
            if not val:
                continue
            if isinstance(val, datetime):
                return val
            if isinstance(val, str):
                parsed = self._parse_datetime_string(val)
                if parsed:
                    return parsed
        return None

    # ======================================================
    # IP TYPE HELPER
    # ======================================================
    def _ip_type(self, ip_str):
        try:
            ip = ipaddress.ip_address(ip_str)
            return "IPv6" if isinstance(ip, ipaddress.IPv6Address) else "IPv4"
        except:
            return None

    # ======================================================
    # DATETIME SORTING HELPER
    # ======================================================
    def _get_sort_datetime(self, record):
        sd = record.get("Start Date")
        st = record.get("Start Time")
        if sd and st:
            try:
                return datetime.strptime(f"{sd} {st}", "%d/%b/%Y %H:%M:%S %H:%M:%S")
            except:
                try:
                    return datetime.strptime(sd, "%d/%b/%Y %H:%M:%S")
                except:
                    pass
        for field in ("DateTimeIST", "DateTimeUTC"):
            val = record.get(field)
            if val:
                try:
                    return datetime.strptime(val, "%Y-%m-%dT%H:%M:%S")
                except:
                    pass
        return datetime.min

    # ======================================================
    # REPORT FORMATTER — now injects SDR fields
    # ======================================================
    def _format_report_record(self, record, module):
        out = {}

        out["RecordType"] = module
        out["Target"] = record.get("Target")
        out["Other Party"] = record.get("Other Party")

        # ── SDR: Target ──────────────────────────────────────
        target_number = record.get("Target")
        target_sdr = self.sdr_lookup_target.get(str(target_number)) if target_number else None
        self._attach_sdr_fields(out, target_sdr, prefix="Target")

        # ── SDR: Other Party ─────────────────────────────────
        other_number = record.get("Other Party")
        # Only look up SDR for 10-digit mobile numbers (skip IPs, short codes, etc.)
        other_sdr = None
        if other_number and str(other_number).isdigit() and len(str(other_number)) == 10:
            other_sdr = self.sdr_lookup_other.get(str(other_number))
        self._attach_sdr_fields(out, other_sdr, prefix="OtherParty")

        # ── Datetime ─────────────────────────────────────────
        dt = self._extract_datetime(record, module)
        if dt:
            out["Start Date"] = dt.strftime("%d/%b/%Y %H:%M:%S")
            out["Start Time"] = dt.strftime("%H:%M:%S")
            out["End Date"] = dt.strftime("%d/%b/%Y %H:%M:%S")
            out["End Time"] = dt.strftime("%H:%M:%S")
            out["_sort_datetime"] = dt
        else:
            out["_sort_datetime"] = datetime.min

        out["Duration"] = record.get("Duration")
        out["Call Type"] = record.get("Call_Type") or record.get("Call Type")

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

        if record.get("First_CGI"):
            out["First Cell ID"] = str(record.get("First_CGI"))

        out["First Cell ID Address"] = record.get("First Cell ID Address")
        out["Destination IP"] = record.get("Destination_ip") or record.get("Destination IP")
        out["Participant IP"] = record.get("Participant IP")
        out["Status"] = record.get("Status")
        out["ID"] = record.get("ID")
        out["Group ID"] = record.get("Group ID")
        out["IMEI"] = record.get("IMEI")
        out["Crime"] = record.get("Crime")
        out["Target IP"] = record.get("Target IP")
        out["Source IP"] = record.get("Source_ip") or record.get("Source IP")
        out["Source Port"] = record.get("Source_port") or record.get("Source Port")
        out["Translated IP"] = record.get("Translated_ip") or record.get("Translated IP")
        out["Translated Port"] = record.get("Translated_port") or record.get("Translated Port")
        out["Destination Port"] = record.get("Destination_port") or record.get("Destination Port")
        out["Target Port"] = record.get("Target Port")
        out["Participant Port"] = record.get("Participant Port")
        out["Last Cell ID"] = str(record.get("Last_CGI")) if record.get("Last_CGI") else None
        out["Last Cell ID Address"] = record.get("Last Cell ID Address")
        out["IMSI"] = record.get("IMSI")
        out["Circle"] = record.get("Circle")
        out["Operator"] = record.get("Operator")
        out["LRN"] = record.get("LRN")
        out["Call Forward"] = record.get("Call Forward")
        out["Upload"] = record.get("Upload")
        out["Download"] = record.get("Download")
        out["Target Device"] = record.get("Target Device")
        out["Participant Device"] = record.get("Participant Device")
        out["Main City(First CellID)"] = record.get("Main City (First CellID)") or record.get("Main City(First CellID)")
        out["Sub City(First CellID)"] = record.get("Sub City (First CellID)") or record.get("Sub City(First CellID)")
        out["Lat-Long-Azimuth (First CellID)"] = record.get("Lat-Long-Azimuth (First CellID)")
        out["Type"] = record.get("Type")
        out["Style"] = record.get("Style")
        out["Size"] = record.get("Size")
        out["HashCode"] = record.get("HashCode")

        # IP Enrichment — Source
        for key in [
            "Source_IP_Type", "Source_ISP/Org", "Source_Country", "Source_Location",
            "Source_Usage", "Source_Domains", "Source_VPN/Proxy/Tor",
            "Source_TSP/Broadband/Satellite", "Source_App/Hostname",
            "Source_IPLat", "Source_IPLong", "Source_IP_Map_Link",
        ]:
            out[key] = record.get(key)

        # IP Enrichment — Destination
        for key in [
            "Destination_IP_Type", "Destination_ISP/Org", "Destination_Country", "Destination_Location",
            "Destination_Usage", "Destination_Domains", "Destination_VPN/Proxy/Tor",
            "Destination_TSP/Broadband/Satellite", "Destination_App/Hostname",
            "Destination_IPLat", "Destination_IPLong", "Destination_IP_Map_Link",
        ]:
            out[key] = record.get(key)

        # IP Enrichment — Translated
        for key in [
            "Translated_IP_Type", "Translated_ISP/Org", "Translated_Country", "Translated_Location",
            "Translated_Usage", "Translated_Domains", "Translated_VPN/Proxy/Tor",
            "Translated_TSP/Broadband/Satellite", "Translated_App/Hostname",
            "Translated_IPLat", "Translated_IPLong", "Translated_IP_Map_Link",
        ]:
            out[key] = record.get(key)

        # IP Enrichment — Participant
        for key in [
            "Participant_IP_Type", "Participant_ISP/Org", "Participant_Country", "Participant_Location",
            "Participant_Usage", "Participant_Domains", "Participant_VPN/Proxy/Tor",
            "Participant_TSP/Broadband/Satellite", "Participant_App/Hostname",
            "Participant_IPLat", "Participant_IPLong", "Participant_IP_Map_Link",
        ]:
            out[key] = record.get(key)

        # IP Enrichment — Target
        for key in [
            "Target_IP_Type", "Target_ISP/Org", "Target_Country", "Target_Location",
            "Target_Usage", "Target_Domains", "Target_VPN/Proxy/Tor",
            "Target_TSP/Broadband/Satellite", "Target_App/Hostname",
            "Target_IPLat", "Target_IPLong", "Target_IP_Map_Link",
        ]:
            out[key] = record.get(key)

        # Port Enrichment
        for key in [
            "Destination_Port_Type", "Destination_Port_Description", "Destination_Port_Category",
            "Source_Port_Type", "Source_Port_Description", "Source_Port_Category",
            "Participant_Port_Type", "Participant_Port_Description", "Participant_Port_Category",
            "Target_Port_Type", "Target_Port_Description", "Target_Port_Category",
        ]:
            out[key] = record.get(key)

        ordered = {k: out.get(k) for k in self.REPORT_COLUMN_ORDER}
        return ordered

    def _fmt_dt(self, dt):
        if not dt:
            return None
        return dt.strftime("%d/%b/%Y %H:%M:%S")

    # ======================================================
    # TRIO SUMMARY BUILDER - WITH SDR DETAILS IN PROPER ORDER
    # ======================================================
    def _build_trio_summary(self, raw_records_by_module):
        summary = defaultdict(lambda: {
            "ExistsIn": set(),
            "Call Freq": 0, "Call In": 0, "Call Out": 0,
            "Sms In": 0, "Sms Out": 0, "Other Calls": 0,
            "Cdr Start": None, "Cdr End": None,
            "Ipdr Freq": 0, "Ipdr Start": None, "Ipdr End": None,
            "Wap Freq": 0, "Msg": 0, "Group Msg": 0,
            "Video Call": 0, "Audio Call": 0, "Group Call": 0, "Other Types": 0,
            "Wap Start": None, "Wap End": None,
        })

        for module, records in raw_records_by_module.items():
            for r in records:
                if module == "CDR":
                    other = r.get("B_Party")
                    target = r.get("A_Party")
                elif module == "WhatsApp":
                    other = r.get("Participant") or r.get("Group ID")
                    target = r.get("Target")
                elif module == "IPDR":
                    target = r.get("MSISDN")
                    other = None
                    dest_ip_raw = (
                            r.get("Destination_ip") or
                            r.get("Destination IP") or
                            r.get("Destination_ip_original")
                    )
                    if dest_ip_raw and hasattr(self, "ipdr_bparty_map"):
                        for ip in self._normalize_ips(dest_ip_raw):
                            ip_clean = str(ip).strip() if ip else None
                            if ip_clean and ip_clean in self.ipdr_bparty_map:
                                other = self.ipdr_bparty_map[ip_clean]
                                break
                    if not other:
                        other = r.get("Destination_ip") or r.get("Destination IP")
                else:
                    target = r.get("A_Party")
                    other = r.get("B_Party")

                key = (target, other)
                s = summary[key]
                call_type = str(r.get("Call_Type", "")).lower()
                dt = self._extract_raw_datetime(r, module)
                s["ExistsIn"].add(module)

                if module == "CDR":
                    s["Call Freq"] += 1
                    if "in" in call_type:
                        s["Call In"] += 1
                    elif "out" in call_type:
                        s["Call Out"] += 1
                    elif "sms" in call_type and "in" in call_type:
                        s["Sms In"] += 1
                    elif "sms" in call_type and "out" in call_type:
                        s["Sms Out"] += 1
                    else:
                        s["Other Calls"] += 1
                    if dt:
                        if not s["Cdr Start"] or dt < s["Cdr Start"]:
                            s["Cdr Start"] = dt
                        if not s["Cdr End"] or dt > s["Cdr End"]:
                            s["Cdr End"] = dt

                if module == "IPDR":
                    s["Ipdr Freq"] += 1
                    if dt:
                        if not s["Ipdr Start"] or dt < s["Ipdr Start"]:
                            s["Ipdr Start"] = dt
                        if not s["Ipdr End"] or dt > s["Ipdr End"]:
                            s["Ipdr End"] = dt

                if module == "WhatsApp":
                    s["Wap Freq"] += 1
                    gid = r.get("Group ID")
                    if "msg" in call_type:
                        if gid:
                            s["Group Msg"] += 1
                        else:
                            s["Msg"] += 1
                    elif "video" in call_type:
                        s["Video Call"] += 1
                    elif "audio" in call_type:
                        s["Audio Call"] += 1
                    elif gid:
                        s["Group Call"] += 1
                    else:
                        s["Other Types"] += 1
                    if dt:
                        if not s["Wap Start"] or dt < s["Wap Start"]:
                            s["Wap Start"] = dt
                        if not s["Wap End"] or dt > s["Wap End"]:
                            s["Wap End"] = dt

        output = []
        for (t, o), v in summary.items():
            # Build summary record with all data
            summary_record = {}

            # Add RecordType (always "Summary" or empty for summary rows)
            summary_record["RecordType"] = ""

            # Add Target
            summary_record["Target"] = t

            # Add Target SDR details immediately after Target
            target_sdr = self.sdr_lookup_target.get(str(t)) if t else None
            self._attach_sdr_fields(summary_record, target_sdr, prefix="Target")

            # Add Other Party
            summary_record["Other Party"] = o

            # Add Other Party SDR details immediately after Other Party
            other_sdr = None
            if o and str(o).isdigit() and len(str(o)) == 10:
                other_sdr = self.sdr_lookup_other.get(str(o))
            self._attach_sdr_fields(summary_record, other_sdr, prefix="OtherParty")

            # Add all other summary fields
            summary_record["ExistsIn"] = ",".join(v["ExistsIn"])
            summary_record["Total Freq"] = v["Call Freq"] + v["Ipdr Freq"] + v["Wap Freq"]
            summary_record["Call Freq"] = v["Call Freq"]
            summary_record["Call In"] = v["Call In"]
            summary_record["Call Out"] = v["Call Out"]
            summary_record["Sms In"] = v["Sms In"]
            summary_record["Sms Out"] = v["Sms Out"]
            summary_record["Other Calls"] = v["Other Calls"]
            summary_record["Cdr DateRange"] = (
                f'{self._fmt_dt(v["Cdr Start"])} - {self._fmt_dt(v["Cdr End"])}'
                if v["Cdr Start"] else None
            )
            summary_record["Ipdr Freq"] = v["Ipdr Freq"]
            summary_record["Ipdr DateRange"] = (
                f'{self._fmt_dt(v["Ipdr Start"])} - {self._fmt_dt(v["Ipdr End"])}'
                if v["Ipdr Start"] else None
            )
            summary_record["Wap Freq"] = v["Wap Freq"]
            summary_record["Msg"] = v["Msg"]
            summary_record["Group Msg"] = v["Group Msg"]
            summary_record["Video Call"] = v["Video Call"]
            summary_record["Audio Call"] = v["Audio Call"]
            summary_record["Group Call"] = v["Group Call"]
            summary_record["Other Types"] = v["Other Types"]
            summary_record["Wap DateRange"] = (
                f'{self._fmt_dt(v["Wap Start"])} - {self._fmt_dt(v["Wap End"])}'
                if v["Wap Start"] else None
            )

            # Order the output according to SUMMARY_COLUMN_ORDER
            ordered_record = {}
            for col in self.SUMMARY_COLUMN_ORDER:
                if col in summary_record:
                    ordered_record[col] = summary_record[col]

            # Add any remaining SDR fields that might not be in SUMMARY_COLUMN_ORDER
            for key, value in summary_record.items():
                if key not in ordered_record:
                    ordered_record[key] = value

            output.append(ordered_record)
        return output

    def load_sdr_columns(self):
        try:
            with open(sdr_clomns, "r") as f:
                return json.load(f)
        except Exception:
            return {}

    # ======================================================
    # POST - MAIN ENTRY POINT
    # ======================================================
    def post(self, request):
        mob_no = request.data.get("mob_no")
        modules = request.data.get("modules", "all")
        use_advanced_bparty = request.data.get("use_advanced_bparty", True)
        include_sdr = self._parse_bool(request.data.get('include_sdr', True))

        if not mob_no:
            return Response({"error": "mob_no is required"}, status=400)

        if modules == "all":
            modules = list(self.MODULE_MAP.keys())

        # ── SDR setup ────────────────────────────────────────────────────────────
        # These dicts are populated AFTER all records are fetched (two-pass approach):
        #   Pass 1 → collect all Target and OtherParty numbers across every module
        #   Pass 2 → single bulk MongoDB query per party role, then attach to records
        self.sdr_lookup_target = {}  # keyed by MSISDN string
        self.sdr_lookup_other = {}  # keyed by MSISDN string

        column_config = self.load_sdr_columns()
        msisdn_fields = column_config.get("SDR", [])

        # SDR collection handle (same alias used in TowerDumpDetailRecordDetailView)
        sdr_db = get_db(alias='sdr_db')
        sdr_collection = sdr_db['subscribers']

        # ── IPDR B-party map ─────────────────────────────────────────────────────
        ipdr_raw_records = []
        self.ipdr_bparty_map = {}

        if "IPDR" in modules:
            db = get_db(alias="ipdr_db")
            ipdr_collection = db["IPDetailRecords"]
            ipdr_raw_records = list(ipdr_collection.find({"MSISDN": str(mob_no)}))
            logger.info(f"Fetched {len(ipdr_raw_records)} IPDR records for MSISDN {mob_no}")

            if use_advanced_bparty and ipdr_raw_records:
                self.ipdr_bparty_map = self._build_ipdr_bparty_map_mobile_vs_others(
                    ipdr_records=ipdr_raw_records
                )

        # ── Pass 1: fetch & enrich all records, collect party numbers ─────────
        MOBILE_FIELD_MAP = {
            "CDR": "A_Party",
            "TowerDump": "A_Party",
            "WhatsApp": "Target",
            "IPDR": "MSISDN",
        }

        raw_records_by_module = {}
        enriched_by_module = {}  # stores (module, records) after enrich + _add_party_fields

        target_numbers = set()  # all Target numbers across all modules
        other_numbers = set()  # all Other Party numbers (10-digit mobiles only)

        for module in modules:
            db_alias, collection_name = self.MODULE_MAP[module]
            db = get_db(alias=db_alias)
            collection = db[collection_name]

            mobile_field = MOBILE_FIELD_MAP.get(module)
            if not mobile_field:
                continue

            records = ipdr_raw_records if module == "IPDR" else list(
                collection.find({mobile_field: str(mob_no)})
            )

            for r in records:
                r.pop("_id", None)

            if records:
                raw_records_by_module[module] = [dict(r) for r in records]
                records = self.enrich_records(records, module)

            # Attach party fields so we can read Target / Other Party
            for r in records:
                self._add_party_fields(r, module)

            enriched_by_module[module] = records

            # Collect numbers for bulk SDR lookup
            for r in records:
                t = r.get("Target")
                o = r.get("Other Party")

                if t:
                    target_numbers.add(str(t))

                # Only mobile numbers for Other Party SDR
                if o and str(o).isdigit() and len(str(o)) == 10:
                    other_numbers.add(str(o))

        # ── SDR bulk lookup (one query per party role) ────────────────────────
        if include_sdr:
            if target_numbers:
                self.sdr_lookup_target = self._fetch_sdr_details(
                    target_numbers, sdr_collection, msisdn_fields
                )
            if other_numbers:
                self.sdr_lookup_other = self._fetch_sdr_details(
                    other_numbers, sdr_collection, msisdn_fields
                )
        else:
            logger.info("⏭️  SDR disabled — skipping subscriber lookup")

        # ── Pass 2: format records (SDR lookups are now populated) ────────────
        final_records = []
        for module, records in enriched_by_module.items():
            for r in records:
                formatted = self._format_report_record(r, module)
                final_records.append(formatted)

        # ── Sort & summary ────────────────────────────────────────────────────
        final_records.sort(key=lambda x: self._get_sort_datetime(x), reverse=True)
        for r in final_records:
            r.pop("_sort_datetime", None)

        communication_summary = self._build_trio_summary(raw_records_by_module)

        return Response({
            "Mapping": final_records,
            "Summary": communication_summary
        })

    # ======================================================
    # ENRICH RECORDS (unchanged from original)
    # ======================================================
    def enrich_records(self, records, collection_key):
        from ..models import CellTower, MobileOperator, MccMnc, ImeiDetails
        from ..ipdr.ipdr_models.ip_model import PortInfo
        from ..serializers import (
            CellTowerSerializer, MobileOperatorSerializer,
            MccMncSerializer, DeviceInfoSerializer
        )
        from ..ipdr.ip_serializers import PortInfoSerializer
        from ..searchengine import search_ip

        cell_ids, ap_codes, imsi_codes, tac_numbers = set(), set(), set(), set()
        dest_ips, source_ips, translated_ips, dest_ports = set(), set(), set(), set()

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

        for r in records:
            tower = lookupTower.get(str(r.get("First_CGI")))
            if tower:
                r.update({
                    "First Cell ID Address": tower.get("ADDRESS"),
                    "Main City (First CellID)": tower.get("MAIN_CITY"),
                    "Sub City (First CellID)": tower.get("SUB_CITY"),
                })

            if r.get("IMEI_TAC") in lookupTac:
                dev = lookupTac[r["IMEI_TAC"]]
                r["IMEI Manufacturer"] = dev.get("manufacturer")
                r["Device Type"] = dev.get("devicetype")

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

            if collection_key in ["IPDR", "WhatsApp"]:
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
                        r["Source_IP_Map_Link"] = f"https://maps.google.com/maps/?q={ip['IPLat']},{ip['IPLong']}"

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
                        r["Destination_IP_Map_Link"] = f"https://maps.google.com/maps/?q={ip['IPLat']},{ip['IPLong']}"

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
                        r["Translated_IP_Map_Link"] = f"https://maps.google.com/maps/?q={ip['IPLat']},{ip['IPLong']}"

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

            if collection_key == "WhatsApp":
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
                        r["Participant_IP_Map_Link"] = f"https://maps.google.com/maps/?q={ip['IPLat']},{ip['IPLong']}"

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
                        r["Target_IP_Map_Link"] = f"https://maps.google.com/maps/?q={ip['IPLat']},{ip['IPLong']}"

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

                r["Target IP"] = pip
                r["Target Port"] = port
                r["Target Device"] = r.get("Participant Device")
                r["Status"] = "Completed" if r.get("Call_Type") else "Unknown"

            r.pop("seq_id", None)
            r.pop("_id", None)

        return records