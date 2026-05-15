import ast
from datetime import datetime
from functools import lru_cache
from django.db.models import Q
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from .wp_reports import build_summary_from_mapping, generate_target_ip_port_summary, generate_target_ip_summary, \
    generate_ip_wise_summary
from ..whatsapp_models.serializers import (
    WhatsappNexusSerializer,
    WhatsAppDetailsRecordSerializer,
    WhatsAppFilterSerializer,
    WhatsAppConnectionSerializer,
    WhatsAppGroupsSerializer,
    WhatsAppContactsSerializer,
)
from ..whatsapp_models.whatsapp_models import (
    WhatsAppNexus, WhatsAppDetailsRecord,
    WhatsAppConnection, WhatsAppGroups, WhatsAppContacts, WhatsAppInfoNexus,
)
from ...models import MobileOperator
from ...searchengine import search_ip


# Constants for reuse
COUNTRY_CODES = {
    '1': 'USA/Canada', '44': 'UK', '91': 'India', '86': 'China', '81': 'Japan',
    '49': 'Germany', '33': 'France', '7': 'Russia', '39': 'Italy', '34': 'Spain',
    '55': 'Brazil', '52': 'Mexico', '234': 'Nigeria', '93': 'Afghanistan',
    '92': 'Pakistan', '94': 'Sri Lanka', '95': 'Myanmar'
}

DATETIME_FORMATS = [
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f%z",
    "%Y-%m-%d %H:%M:%S%z"
]


# ──────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def clean_number(number):
    """Normalise a phone number to 10 digits (Indian format)."""
    if not number:
        return number
    number = str(number).strip().replace(" ", "").replace("-", "")
    if (
        number.startswith("+91") or
        number.startswith("91") or
        number.startswith("11") or
        len(number) == 12
    ):
        return number[-10:]
    return number


# ──────────────────────────────────────────────────────────────────────────────
#  IP LOOKUP SERVICE
# ──────────────────────────────────────────────────────────────────────────────

class IPLookupService:
    """Service for handling IP lookups with caching and normalization"""

    def __init__(self):
        self.cache = {}
        self.failed_lookups = set()

    def normalize_ip(self, ip_str):
        """Normalize IP address string for consistent matching"""
        if not ip_str or ip_str in ["", "null", None, "None"]:
            return None

        ip_str = str(ip_str).strip().strip('"\'')

        # Handle IPv4 normalization
        if '.' in ip_str and ':' not in ip_str:
            try:
                parts = ip_str.split('.')
                if len(parts) == 4:
                    normalized_parts = []
                    for part in parts:
                        try:
                            normalized_parts.append(str(int(part)))
                        except ValueError:
                            normalized_parts.append(part)
                    return '.'.join(normalized_parts)
            except Exception:
                pass

        return ip_str

    def batch_lookup(self, ip_list, force_refresh=False):
        """Perform batch IP lookup with caching"""
        if not ip_list:
            return {}

        normalized_ips = []
        ip_mapping = {}

        for ip in ip_list:
            normalized = self.normalize_ip(ip)
            if normalized:
                normalized_ips.append(normalized)
                ip_mapping[normalized] = ip

        results = {}
        ips_to_lookup = []

        for norm_ip in normalized_ips:
            if force_refresh or norm_ip not in self.cache:
                if norm_ip not in self.failed_lookups:
                    ips_to_lookup.append(norm_ip)
            else:
                results[norm_ip] = self.cache[norm_ip]

        if ips_to_lookup:
            try:
                lookup_result = search_ip(ips_to_lookup)

                if lookup_result and isinstance(lookup_result, dict) and "results" in lookup_result:
                    for ip_data in lookup_result.get("results", []):
                        if isinstance(ip_data, dict) and "ip" in ip_data:
                            response_ip = self.normalize_ip(ip_data.get("ip"))
                            if response_ip:
                                self.cache[response_ip] = {
                                    "ip": response_ip,
                                    "isp_org": ip_data.get("Isp/Org", "Unknown"),
                                    "domains": ip_data.get("Domains", "Unknown"),
                                    "usage": ip_data.get("Usage", "Unknown"),
                                    "country": ip_data.get("Country", "Unknown"),
                                    "location": ip_data.get("Location", "Unknown"),
                                    "latitude": ip_data.get("IPLat"),
                                    "longitude": ip_data.get("IPLong"),
                                    "vpn_proxy_tor": ip_data.get("VPN/Proxy/Tor")
                                }
                                results[response_ip] = self.cache[response_ip]

                    successful_ips = set(self.cache.keys())
                    for ip in ips_to_lookup:
                        if ip not in successful_ips:
                            self.failed_lookups.add(ip)
                            self.cache[ip] = self._create_fallback_entry(ip)
                            results[ip] = self.cache[ip]
                else:
                    for ip in ips_to_lookup:
                        self.failed_lookups.add(ip)
                        self.cache[ip] = self._create_fallback_entry(ip)
                        results[ip] = self.cache[ip]

            except Exception as e:
                for ip in ips_to_lookup:
                    self.failed_lookups.add(ip)
                    self.cache[ip] = self._create_fallback_entry(ip)
                    results[ip] = self.cache[ip]

        final_results = {}
        for norm_ip, details in results.items():
            original_ip = ip_mapping.get(norm_ip, norm_ip)
            final_results[original_ip] = details
            final_results[norm_ip]     = details

        return final_results

    def _create_fallback_entry(self, ip):
        """Create a fallback entry for IPs that can't be looked up"""
        is_ipv6 = ":" in ip
        return {
            "ip": ip,
            "isp_org": "IPv6 Address" if is_ipv6 else "Not Found in IP Lookup",
            "domains": "Unknown",
            "usage": "Unknown",
            "country": "Unknown",
            "location": "Unknown",
            "latitude": None,
            "longitude": None,
            "vpn_proxy_tor": None
        }

    def get_ip_details(self, ip):
        """Get details for a single IP"""
        normalized = self.normalize_ip(ip)
        if not normalized:
            return self._create_fallback_entry(ip)

        if normalized in self.cache:
            return self.cache[normalized]

        result = self.batch_lookup([normalized])
        return result.get(normalized, self._create_fallback_entry(normalized))


# ──────────────────────────────────────────────────────────────────────────────
#  PARTICIPANT DETAILS SERVICE
# ──────────────────────────────────────────────────────────────────────────────

class ParticipantDetailsService:
    """Service for handling participant details with consistent logic"""

    def __init__(self):
        self._mobile_operator_cache = {}

    def get_participant_details(self, participant, participant_code, phone_number=None):
        """Get participant details - main entry point for all record types"""
        if not participant or participant in ["", "null", "None", "N/A"]:
            return self._get_default_details()

        # Parse participants and codes
        participants, codes = self._parse_participant_strings(participant, participant_code, phone_number)

        if not participants:
            return self._get_default_details()

        # Get details for each participant
        return self._get_details_for_participants(participants, codes)

    def _parse_participant_strings(self, participant, participant_code, phone_number=None):
        """Parse participant and code strings into lists"""
        participants = []
        codes = []

        # If we have a phone number directly, use it
        if phone_number and phone_number not in ["", "null", "None", "N/A"]:
            participants.append(str(phone_number).strip())
            if len(str(phone_number).strip()) >= 4:
                codes.append(str(phone_number).strip()[:4])
            else:
                codes.append("N/A")
            return participants, codes

        # Parse based on separator
        if not participant or participant in ["", "null", "None", "N/A"]:
            return [], []

        participant_str = str(participant).strip()
        participant_code_str = str(participant_code).strip() if participant_code else ""

        # Check for pipe separator (merged format)
        if '|' in participant_str:
            participants = [p.strip() for p in participant_str.split('|') if p.strip()]
            if participant_code_str and '|' in participant_code_str:
                codes = [c.strip() for c in participant_code_str.split('|') if c.strip()]
            else:
                codes = self._extract_codes_from_phones(participants, participant_code_str)

        # Check for comma separator
        elif ',' in participant_str:
            participants = [p.strip() for p in participant_str.split(',') if p.strip()]
            if participant_code_str and ',' in participant_code_str:
                codes = [c.strip() for c in participant_code_str.split(',') if c.strip()]
            else:
                codes = self._extract_codes_from_phones(participants, participant_code_str)

        # Single participant
        else:
            participants = [participant_str]
            if participant_code_str and participant_code_str not in ["", "null", "None", "N/A"]:
                codes = [participant_code_str]
            else:
                codes = self._extract_codes_from_phones(participants, "N/A")

        return participants, codes

    def _extract_codes_from_phones(self, phones, default_code="N/A"):
        """Extract codes from phone numbers when codes are not provided"""
        codes = []
        for phone in phones:
            phone_str = str(phone).strip()
            if len(phone_str) >= 4:
                codes.append(phone_str[:4])
            else:
                codes.append(default_code)
        return codes

    def _get_details_for_participants(self, participants, codes):
        """Get details for multiple participants"""
        providers = []
        circles = []
        operators = []
        types = []

        # Ensure codes list matches participants length
        if len(codes) != len(participants):
            codes = self._extract_codes_from_phones(participants)

        for code in codes:
            details = self._get_single_participant_details(code)
            providers.append(details["Provider"])
            circles.append(details["Circle"])
            operators.append(details["Operator"])
            types.append(details["Type"])

        # Format output based on number of participants
        if len(participants) == 1:
            return [{
                "Provider": providers[0] if providers else "Unknown",
                "Circle": circles[0] if circles else "Unknown",
                "Operator": operators[0] if operators else "Unknown",
                "Participant Type": types[0] if types else "Unknown"
            }]
        else:
            return [{
                "Provider": " | ".join(providers),
                "Circle": " | ".join(circles),
                "Operator": " | ".join(operators),
                "Participant Type": " | ".join(types)
            }]

    def _get_single_participant_details(self, code):
        """Get details for a single participant code"""
        if not code or code == "N/A":
            return {
                "Provider": "Unknown",
                "Circle": "Unknown",
                "Operator": "Unknown",
                "Type": "Unknown"
            }

        # Check cache first
        if code in self._mobile_operator_cache:
            return self._mobile_operator_cache[code]

        result = {
            "Provider": "Unknown",
            "Circle": "Unknown",
            "Operator": "Unknown",
            "Type": "Unknown"
        }

        # Indian mobile check
        if len(code) == 4 and code.isdigit() and code[0] in "6789":
            try:
                code_int = int(code)
                mobile_operator = MobileOperator.objects(id=code_int).first()
                if mobile_operator:
                    result = {
                        "Provider": f"{mobile_operator.Operator}-{mobile_operator.Circle}",
                        "Circle": mobile_operator.Circle,
                        "Operator": mobile_operator.Operator,
                        "Type": "Mobile"
                    }
            except (ValueError, TypeError):
                pass

        # Indian landline check
        elif len(code) == 4 and code.isdigit() and code[0] in "2345":
            result = {
                "Provider": "Landline",
                "Circle": "India",
                "Operator": "BSNL/MTNL",
                "Type": "Landline"
            }

        # ISD check
        elif str(code) in COUNTRY_CODES:
            country_name = COUNTRY_CODES[str(code)]
            result = {
                "Provider": country_name,
                "Circle": country_name,
                "Operator": country_name,
                "Type": "ISD"
            }

        # Cache the result
        self._mobile_operator_cache[code] = result
        return result

    def _get_default_details(self):
        """Return default details when no participant info"""
        return [{
            "Provider": "Unknown",
            "Circle": "Unknown",
            "Operator": "Unknown",
            "Participant Type": "Unknown"
        }]


# ──────────────────────────────────────────────────────────────────────────────
#  WHATSAPP DATA PROCESSOR
# ──────────────────────────────────────────────────────────────────────────────

class WhatsAppDataProcessor:
    """Process WhatsApp data with consistent participant handling"""

    def __init__(self, ip_lookup_service):
        self.ip_lookup_service = ip_lookup_service
        self.participant_service = ParticipantDetailsService()

    def process_records(self, records, crime_name=None):
        """Process all WhatsApp records"""
        if not records:
            return {
                "message_summaries": [],
                "call_summaries": [],
                "errors": []
            }

        # Extract all IPs from records
        all_ips = self._extract_ips_from_records(records)

        # Perform IP lookup in single batch
        ip_details_map = self.ip_lookup_service.batch_lookup(all_ips)

        # Process records by type
        message_records = []
        call_records = []
        errors = []

        for record in records:
            try:
                call_type = record.get("Call_Type", "")
                if call_type in ["Msg Out", "Msg In"]:
                    message_records.append(record)
                elif call_type in ["Call In", "Call Out"]:
                    call_records.append(record)
            except Exception as e:
                errors.append({
                    "record_id": record.get("ID", "Unknown"),
                    "error": str(e)
                })

        # Process messages
        message_summaries = self._process_message_records(message_records, crime_name, ip_details_map)

        # Process and merge calls
        call_summaries = self._process_and_merge_calls(call_records, ip_details_map)

        return {
            "message_summaries": message_summaries,
            "call_summaries": call_summaries,
            "errors": errors
        }

    def _extract_ips_from_records(self, records):
        """Extract all unique IPs from records"""
        ips = set()
        for record in records:
            for ip_field in ["Target_IP", "Participant_IP"]:
                ip = record.get(ip_field)
                if ip and ip not in ["", "null", None, "None"]:
                    ips.add(str(ip).strip())
        return list(ips)

    def _process_message_records(self, records, crime_name, ip_details_map):
        """Process multiple message records"""
        summaries = []
        for record in records:
            try:
                summary = self._create_record_summary(record, crime_name, ip_details_map, is_message=True)
                if summary:
                    summaries.append(summary)
            except Exception as e:
                continue
        return summaries

    def _process_and_merge_calls(self, call_records, ip_details_map):
        """Process and merge call records with same ID"""
        # Group calls by ID
        call_groups = {}
        for record in call_records:
            call_id = record.get("ID", "N/A")
            if call_id not in call_groups:
                call_groups[call_id] = []
            call_groups[call_id].append(record)

        # Merge each group
        merged_calls = []
        for call_id, call_list in call_groups.items():
            try:
                merged_call = self._merge_call_group(call_list, ip_details_map)
                if merged_call:
                    merged_calls.append(merged_call)
            except Exception as e:
                continue

        return merged_calls

    def _create_record_summary(self, record, crime_name, ip_details_map, is_message=True):
        """Create summary for a single record (message or call)"""
        participant = record.get("Participant", "N/A")
        participant_code = record.get("Participant_Code", "N/A")

        # Get participant details using the service
        participant_details = self.participant_service.get_participant_details(participant, participant_code)
        p = participant_details[0] if participant_details else {}

        # Parse datetimes
        datetime_utc = record.get("DateTimeUTC")
        datetime_ist = record.get("DateTimeIST")
        parsed_utc = self._safe_parse_datetime(datetime_utc)
        parsed_ist = self._safe_parse_datetime(datetime_ist)

        if is_message:
            parsed_end_utc = parsed_utc
            parsed_end_ist = parsed_ist
            duration = 0
        else:
            parsed_end_utc = None
            parsed_end_ist = None
            duration = record.get("Size", 0)

        # IP analysis - flattened format
        target_ip = record.get("Target_IP")
        participant_ip = record.get("Participant_IP")

        IP_DETAIL_KEYS = [
            "ip",
            "isp_org",
            "domains",
            "usage",
            "country",
            "location",
            "latitude",
            "longitude",
            "vpn_proxy_tor",
        ]

        def populate_ip_details(ip_analysis, prefix, details=None):
            for key in IP_DETAIL_KEYS:
                prefixed_key = f"{prefix} {key}"
                if details and key in details and details[key] is not None:
                    value = details[key]
                    if isinstance(value, (int, float)):
                        ip_analysis[prefixed_key] = str(value)
                    else:
                        ip_analysis[prefixed_key] = str(value).strip()
                else:
                    ip_analysis[prefixed_key] = ""

        ip_analysis = {}

        # ---------- TARGET IP ----------
        if target_ip and target_ip not in ["", "null", None, "None"]:
            target_details = self.ip_lookup_service.get_ip_details(target_ip)
        else:
            target_details = None

        populate_ip_details(ip_analysis, "target", target_details)

        # ---------- PARTICIPANT IP ----------
        if participant_ip and participant_ip not in ["", "null", None, "None"]:
            participant_details_ip = self.ip_lookup_service.get_ip_details(participant_ip)
        else:
            participant_details_ip = None

        populate_ip_details(ip_analysis, "participant", participant_details_ip)

        summary = {
            "Target": record.get("Target", "N/A"),
            "Creator": record.get("Call_Creator"),
            "Participant": participant,
            "Participant Provider": p.get("Provider", "Unknown"),
            "Participant Circle": p.get("Circle", "Unknown"),
            "Participant Operator": p.get("Operator", "Unknown"),
            "Participant Type": p.get("Participant Type", "Unknown"),
            "Datetime Start UTC": parsed_utc,
            "Datetime End UTC": parsed_end_utc,
            "Datetime Start IST": parsed_ist,
            "Datetime End IST": parsed_end_ist,
            "Duration": duration,
            "Call Type": record.get("Call_Type", "N/A"),
            "Status": record.get("Status", "N/A"),
            "record_type": record.get("Type", "N/A"),
            "style": record.get("Style", "N/A"),
            "size_bytes": record.get("Size"),
            "ID": record.get("ID", "N/A"),
            "Group": crime_name,
            "Target Ip": target_ip,
            "Target Port": record.get("Target_Port"),
            "Target Device": record.get("Target_Device", "N/A"),
            "participant_ip": participant_ip,
            "participant_port": record.get("Participant_Port"),
            "participant_device": record.get("Participant_Device", "N/A"),
            "Group ID": record.get("Group_ID", "N/A"),
            **ip_analysis,
        }

        return summary

    def _merge_call_group(self, call_list, ip_details_map):
        """Merge a group of calls with the same ID"""
        if not call_list:
            return None

        # Get base record
        base_record = call_list[0]

        # Collect all unique participants from all records
        all_participants_info = self._collect_participants_from_call_group(call_list)

        # Build merged participant string
        participant_str, participant_code_str = self._build_merged_participant_string(all_participants_info)

        # Get participant details using the service
        participant_details = self.participant_service.get_participant_details(participant_str, participant_code_str)
        p = participant_details[0] if participant_details else {}

        # Merge other fields
        merged_data = self._merge_call_fields(call_list)

        # Parse datetimes
        parsed_start_utc = self._safe_parse_datetime(merged_data["datetime_start"])
        parsed_start_ist = self._safe_parse_datetime(merged_data["datetime_start_ist"])
        parsed_end_utc = self._safe_parse_datetime(merged_data["datetime_end"])
        parsed_end_ist = self._safe_parse_datetime(merged_data["datetime_end_ist"])

        # Calculate duration
        duration = 0
        if parsed_start_utc and parsed_end_utc:
            duration = int((parsed_end_utc - parsed_start_utc).total_seconds())

        # Get combined IP analysis for merged IPs (now returns flat dictionary)
        ip_analysis = self._get_combined_ip_analysis_for_merged_ips(
            merged_data["target_ips"], merged_data["participant_ips"]
        )
        filtered_statuses = [str(s) for s in merged_data["status"] if s and str(s).strip()]
        status_str = " | ".join(filtered_statuses) if filtered_statuses else "N/A"

        return {
            "Target": merged_data["target"],
            "Creator": merged_data["creator"],
            "Participant": participant_str,
            "Participant Provider": p.get("Provider", "Unknown"),
            "Participant Circle": p.get("Circle", "Unknown"),
            "Participant Operator": p.get("Operator", "Unknown"),
            "Participant Type": p.get("Participant Type", "Unknown"),
            "Datetime Start UTC": parsed_start_utc,
            "Datetime End UTC": parsed_end_utc,
            "Datetime Start IST": parsed_start_ist,
            "Datetime End IST": parsed_end_ist,
            "Duration": duration,
            "Call Type": merged_data["call_type"],
            "Status": status_str,
            "record_type": merged_data["record_type"],
            "style": merged_data["style"],
            "size_bytes": merged_data["size_bytes"],
            "ID": merged_data["id"],
            "Target Ip": " | ".join(sorted(merged_data["target_ips"])) if merged_data["target_ips"] else None,
            "Target Port": " | ".join(sorted(merged_data["target_ports"])) if merged_data["target_ports"] else None,
            "Target Device": merged_data["target_device"],
            "participant_ip": " | ".join(sorted(merged_data["participant_ips"])) if merged_data[
                "participant_ips"] else None,
            "Participant Port": " | ".join(sorted(merged_data["participant_ports"])) if merged_data[
                "participant_ports"] else None,
            "Participant Device": merged_data["participant_device"],
            **ip_analysis
        }

    def _collect_participants_from_call_group(self, call_list):
        """Collect all participants from a call group - FIXED VERSION"""
        participants_info = {}  # phone -> code

        target_phone = None
        for call in call_list:
            # Get target phone from first call
            if target_phone is None:
                target_phone = call.get("Target", "").strip()

            # First, check Participants field (for group calls)
            participants_field = call.get("Participants", "")
            if participants_field and participants_field not in ["", "null", "None", "[]"]:
                participants_data = self._parse_participants_field(participants_field)
                for phone in participants_data:
                    if phone and phone not in ["", "null", "None"] and phone != target_phone:
                        code = phone[:4] if len(phone) >= 4 else "N/A"
                        participants_info[phone] = code

            # Also check Participant field for individual participants
            participant = call.get("Participant", "").strip()
            participant_code = call.get("Participant_Code", "").strip()

            if participant and participant not in ["", "null", "None"] and participant != target_phone:
                if participant not in participants_info:
                    if participant_code and participant_code not in ["", "null", "None", "N/A"]:
                        participants_info[participant] = participant_code
                    else:
                        code = participant[:4] if len(participant) >= 4 else "N/A"
                        participants_info[participant] = code

        return participants_info

    def _parse_participants_field(self, participants_field):
        """Parse Participants field to extract phone numbers"""
        phones = []

        if not participants_field or participants_field in ["", "null", "None", "[]"]:
            return phones

        try:
            if isinstance(participants_field, str):
                try:
                    parsed = ast.literal_eval(participants_field)
                    if isinstance(parsed, list):
                        for item in parsed:
                            if isinstance(item, dict):
                                phone = item.get("Phone Number", "")
                                if phone:
                                    phones.append(str(phone).strip())
                            elif isinstance(item, str):
                                phones.append(str(item).strip())
                    elif isinstance(parsed, str) and parsed != "":
                        phones.append(str(parsed).strip())
                except:
                    if ',' in participants_field:
                        phones = [p.strip() for p in participants_field.split(',') if p.strip()]
                    else:
                        phones = [participants_field.strip()]
            elif isinstance(participants_field, list):
                for item in participants_field:
                    if isinstance(item, dict):
                        phone = item.get("Phone Number", "")
                        if phone:
                            phones.append(str(phone).strip())
                    elif isinstance(item, str):
                        phones.append(str(item).strip())
        except Exception as e:
            if isinstance(participants_field, str):
                phones = [participants_field.strip()]

        return phones

    def _build_merged_participant_string(self, participants_info):
        """Build merged participant string from participants info"""
        if not participants_info:
            return "N/A", "N/A"

        phones = sorted(participants_info.keys())
        codes = [participants_info[phone] if participants_info[phone] not in ["", "null", "None", "N/A"]
                 else (phone[:4] if len(phone) >= 4 else "N/A")
                 for phone in phones]

        if len(phones) == 1:
            return phones[0], codes[0]
        else:
            return " | ".join(phones), " | ".join(codes)

    def _merge_call_fields(self, call_list):
        """Merge common fields from call list"""
        base_record = call_list[0]

        merged = {
            "id": base_record.get("ID", "N/A"),
            "target": base_record.get("Target", "N/A"),
            "creator": base_record.get("Call_Creator"),
            "call_type": base_record.get("Call_Type", "N/A"),
            "record_type": base_record.get("Type", "N/A"),
            "style": base_record.get("Style", "N/A"),
            "target_device": base_record.get("Target_Device", "N/A"),
            "participant_device": base_record.get("Participant_Device", "N/A"),
            "datetime_start": base_record.get("DateTimeUTC"),
            "datetime_start_ist": base_record.get("DateTimeIST"),
            "datetime_end": None,
            "datetime_end_ist": None,
            "status": [],
            "target_ips": set(),
            "target_ports": set(),
            "participant_ips": set(),
            "participant_ports": set(),
            "size_bytes": 0
        }

        for call in call_list:
            status = call.get("Status")
            if status:
                merged["status"].append(status)

            target_ip = call.get("Target_IP")
            if target_ip and target_ip not in ["", "null", None, "None"]:
                merged["target_ips"].add(str(target_ip).strip())

            participant_ip = call.get("Participant_IP")
            if participant_ip and participant_ip not in ["", "null", None, "None"]:
                merged["participant_ips"].add(str(participant_ip).strip())

            target_port = call.get("Target_Port")
            if target_port:
                merged["target_ports"].add(str(target_port).strip())

            participant_port = call.get("Participant_Port")
            if participant_port:
                merged["participant_ports"].add(str(participant_port).strip())

            if status == "terminate":
                merged["datetime_end"] = call.get("DateTimeUTC")
                merged["datetime_end_ist"] = call.get("DateTimeIST")

            size = call.get("Size")
            if size and isinstance(size, (int, float)):
                merged["size_bytes"] += size

        return merged

    def _get_combined_ip_analysis_for_merged_ips(self, target_ips_set, participant_ips_set):
        """Get combined IP analysis for merged IP sets - flattened keys with prefixes"""
        ip_analysis = {}

        if target_ips_set:
            target_details_list = []
            for ip in sorted(target_ips_set):
                details = self.ip_lookup_service.get_ip_details(ip)
                target_details_list.append(details)

            if target_details_list:
                combined_target = self._combine_ip_details(target_details_list, "target ")
                ip_analysis.update(combined_target)

        if participant_ips_set:
            participant_details_list = []
            for ip in sorted(participant_ips_set):
                details = self.ip_lookup_service.get_ip_details(ip)
                participant_details_list.append(details)

            if participant_details_list:
                combined_participant = self._combine_ip_details(participant_details_list, "participant ")
                ip_analysis.update(combined_participant)

        return ip_analysis

    def _combine_ip_details(self, ip_details_list, prefix=""):
        """Combine multiple IP detail dictionaries into one with pipe-separated values and optional prefix"""
        if not ip_details_list:
            return {}

        if len(ip_details_list) == 1:
            single_details = ip_details_list[0]
            combined = {}
            for key, value in single_details.items():
                prefixed_key = f"{prefix}{key}" if prefix else key
                if value is None:
                    combined[prefixed_key] = "null"
                elif isinstance(value, (int, float)):
                    combined[prefixed_key] = str(value)
                else:
                    combined[prefixed_key] = str(value).strip()
            return combined
        else:
            combined = {}
            keys = ip_details_list[0].keys()

            for key in keys:
                values = []
                for detail in ip_details_list:
                    value = detail.get(key)
                    if value is None:
                        values.append("null")
                    elif isinstance(value, (int, float)):
                        values.append(str(value))
                    else:
                        values.append(str(value).strip())

                combined_value = " | ".join(values)
                prefixed_key = f"{prefix}{key}" if prefix else key
                combined[prefixed_key] = combined_value

            return combined

    def _safe_parse_datetime(self, datetime_value):
        """Safely parse datetime with optimized formatting"""
        if not datetime_value:
            return None

        if isinstance(datetime_value, datetime):
            return datetime_value

        if isinstance(datetime_value, str):
            clean_value = datetime_value.replace('+00:00', '').replace('Z', '').replace(' IST', '').strip()

            try:
                return datetime.fromisoformat(clean_value)
            except ValueError:
                pass

            for fmt in DATETIME_FORMATS:
                try:
                    return datetime.strptime(clean_value, fmt)
                except ValueError:
                    continue

        return None


# ──────────────────────────────────────────────────────────────────────────────
#  INFO FETCH HELPER
# ──────────────────────────────────────────────────────────────────────────────

def fetch_info_data(seq_id):
    """
    Fetch connections, groups, and contacts for a given seq_id.
    Returns a dict ready to merge into the main result.
    """
    # ── Connections ──────────────────────────────────────────────────────────
    connections_data = list(WhatsAppConnectionSerializer(
        WhatsAppConnection.objects(seq_id=seq_id), many=True
    ).data)

    target_codes = set()
    for item in connections_data:
        if item.get("TargetNo"):
            item["TargetNo"] = clean_number(item["TargetNo"])
            tar_code = item["TargetNo"][:4]
            if tar_code.isdigit():
                target_codes.add(int(tar_code))
            nexus_obj = WhatsAppInfoNexus.objects(Target_code=tar_code).first()
            mail = nexus_obj.Emails if nexus_obj and nexus_obj.Emails else []
            item["Emails"] = "  ".join(mail)

    target_operator_map = {}
    if target_codes:
        target_operator_map = {
            str(op.id): {"circle": op.Circle, "operator": op.Operator}
            for op in MobileOperator.objects(id__in=list(target_codes))
        }

    updated_connections = []
    for item in connections_data:
        new_item = {}
        for key, value in item.items():
            new_item[key] = value
            if key == "TargetNo" and value:
                tar_code = value[:4]
                info = target_operator_map.get(tar_code)
                new_item["circle_operator"] = (
                    f"{info['circle']} - {info['operator']}" if info else None
                )
        updated_connections.append(new_item)

    # ── Groups ───────────────────────────────────────────────────────────────
    groups_data = list(WhatsAppGroupsSerializer(
        WhatsAppGroups.objects(seq_id=seq_id), many=True
    ).data)
    for item in groups_data:
        if item.get("TargetNo"):
            item["TargetNo"] = clean_number(item["TargetNo"])

    # ── Contacts ─────────────────────────────────────────────────────────────
    symmetric = []
    symmetric_codes = []
    asymmetric = []
    asymmetric_codes = []

    contacts_obj = WhatsAppContacts.objects(seq_id=seq_id).first()
    if contacts_obj:
        contacts_data    = WhatsAppContactsSerializer(contacts_obj).data
        symmetric        = [clean_number(n) for n in contacts_data.get("symmetric_contacts", [])]
        symmetric_codes  = contacts_data.get("symmetric_contact_codes", [])
        asymmetric       = [clean_number(n) for n in contacts_data.get("asymmetric_contacts", [])]
        asymmetric_codes = contacts_data.get("asymmetric_contact_codes", [])

    # ── Operator lookup for contacts ──────────────────────────────────────────
    all_codes = {
        int(code) for code in symmetric_codes + asymmetric_codes
        if str(code).isdigit()
    }
    operator_map = {}
    if all_codes:
        operator_map = {
            str(op.id): {"circle": op.Circle, "operator": op.Operator}
            for op in MobileOperator.objects(id__in=list(all_codes))
        }

    def build_cir_op(codes):
        result = []
        for code in codes:
            info = operator_map.get(str(code))
            result.append(
                f"{info['circle']} - {info['operator']}" if info else None
            )
        return result

    sym_opr  = build_cir_op(symmetric_codes)
    asym_opr = build_cir_op(asymmetric_codes)
    # Side-by-side: zip symmetric and asymmetric into same rows
    max_len = max(len(symmetric), len(asymmetric))
    contacts_info = []
    for i in range(max_len):
        row = {}
        if i < len(symmetric):
            row["symmetric_contacts"] = symmetric[i]
            row["symmetric_contacts_cir_op"] = sym_opr[i] if i < len(sym_opr) else None
        else:
            row["symmetric_contacts"] = None
            row["symmetric_contacts_cir_op"] = None

        if i < len(asymmetric):
            row["asymmetric_contacts"] = asymmetric[i]
            row["asymmetric_contacts_cir_op"] = asym_opr[i] if i < len(asym_opr) else None
        else:
            row["asymmetric_contacts"] = None
            row["asymmetric_contacts_cir_op"] = None

        contacts_info.append(row)

    return {
        "target profile": updated_connections,
        "groups info":    groups_data,
        "contacts info": contacts_info,
    }


# ──────────────────────────────────────────────────────────────────────────────
#  NEXUS VIEW
# ──────────────────────────────────────────────────────────────────────────────

class WhatsAppNexusView(APIView):
    """
    GET API to fetch all WhatsApp Nexus records with filtering and pagination.
    """

    @swagger_auto_schema(
        operation_description="Fetch all WhatsApp Nexus records from MongoDB.",
        manual_parameters=[
            openapi.Parameter(
                'limit',
                openapi.IN_QUERY,
                description="Limit number of records (optional)",
                type=openapi.TYPE_INTEGER
            ),
            openapi.Parameter(
                'offset',
                openapi.IN_QUERY,
                description="Pagination offset (optional)",
                type=openapi.TYPE_INTEGER
            ),
            openapi.Parameter(
                'crime',
                openapi.IN_QUERY,
                description="Filter by CrimeName (optional)",
                type=openapi.TYPE_STRING
            ),
            openapi.Parameter(
                'year',
                openapi.IN_QUERY,
                description="Filter by Year (optional)",
                type=openapi.TYPE_INTEGER
            ),
            openapi.Parameter(
                'record_type',
                openapi.IN_QUERY,
                description="Filter by RecordType (optional)",
                type=openapi.TYPE_STRING
            ),
        ],
        responses={
            200: openapi.Response(
                description="List of WhatsApp Nexus records",
                schema=WhatsappNexusSerializer(many=True)
            ),
            500: "Internal server error"
        }
    )
    def get(self, request):
        try:
            crime = request.GET.get('crime')
            year = request.GET.get('year')
            record_type = request.GET.get('record_type')
            limit = request.GET.get('limit')
            offset = request.GET.get('offset')

            filters = Q()
            if crime:
                filters &= Q(CrimeName__icontains=crime)
            if year:
                try:
                    filters &= Q(Year=int(year))
                except ValueError:
                    pass
            if record_type:
                filters &= Q(RecordType=record_type)

            queryset = WhatsAppNexus.objects.filter(filters).order_by('-CreatedAt')
            total_count = queryset.count()

            if offset:
                queryset = queryset[int(offset):]
            if limit:
                queryset = queryset[:int(limit)]

            serializer = WhatsappNexusSerializer(queryset, many=True)
            nexus_data = serializer.data

            return Response(nexus_data)
        except ValueError as e:
            return Response(
                {"error": f"Invalid parameter value: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to fetch WhatsApp Nexus records: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# ──────────────────────────────────────────────────────────────────────────────
#  MAIN DATA VIEW
# ──────────────────────────────────────────────────────────────────────────────

class WhatsAppDataView(APIView):
    """
    POST API to fetch WhatsApp Nexus and WhatsApp Details by seq_id with advanced filtering.
    Response includes:
      - target profile   (connections)
      - groups info
      - contacts info
      - 1. Mapping       (all records)
      - 2. messages
      - 3. calls
      - 4. Group Messages
      - 5. Group Calls
      - 6. Summary
      - 7. IP Wise Summary
      - 8. ip port summary
      - 9. ip summary
    """

    def __init__(self):
        super().__init__()
        self.ip_lookup_service = IPLookupService()
        self.data_processor = WhatsAppDataProcessor(self.ip_lookup_service)

    @swagger_auto_schema(
        operation_description="Fetch WhatsApp Nexus and Details data by seq_id with filtering",
        request_body=WhatsAppFilterSerializer,
        responses={
            200: openapi.Response(
                description="Success response with WhatsApp data",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'target profile': openapi.Schema(type=openapi.TYPE_ARRAY,
                                                         items=openapi.Items(type=openapi.TYPE_OBJECT)),
                        'groups info': openapi.Schema(type=openapi.TYPE_ARRAY,
                                                      items=openapi.Items(type=openapi.TYPE_OBJECT)),
                        'contacts info': openapi.Schema(type=openapi.TYPE_OBJECT),
                        '1. Mapping': openapi.Schema(type=openapi.TYPE_ARRAY,
                                                     items=openapi.Items(type=openapi.TYPE_OBJECT)),
                        '2. messages': openapi.Schema(type=openapi.TYPE_ARRAY,
                                                      items=openapi.Items(type=openapi.TYPE_OBJECT)),
                        '3. calls': openapi.Schema(type=openapi.TYPE_ARRAY,
                                                   items=openapi.Items(type=openapi.TYPE_OBJECT)),
                        '4. Group Messages': openapi.Schema(type=openapi.TYPE_ARRAY,
                                                            items=openapi.Items(type=openapi.TYPE_OBJECT)),
                        '5. Group Calls': openapi.Schema(type=openapi.TYPE_ARRAY,
                                                         items=openapi.Items(type=openapi.TYPE_OBJECT)),
                        'errors': openapi.Schema(type=openapi.TYPE_ARRAY,
                                                 items=openapi.Items(type=openapi.TYPE_OBJECT))
                    }
                )
            ),
            400: "Missing or invalid seq_id",
            404: "Data not found",
            500: "Internal server error"
        }
    )
    def post(self, request):
        try:
            seq_id = request.data.get('seq_id')
            if not seq_id:
                return Response(
                    {"error": "seq_id is required."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            nexus_data = None
            crime_name = None

            try:
                nexus_obj = WhatsAppNexus.objects.get(_id=seq_id)
                nexus_serializer = WhatsappNexusSerializer(nexus_obj)
                nexus_data = nexus_serializer.data
                crime_name = nexus_data.get("CrimeName")
            except WhatsAppNexus.DoesNotExist:
                pass

            details_query = WhatsAppDetailsRecord.objects.filter(seq_id=seq_id)
            details_query = self._apply_details_filters(details_query, request.data)

            details_serializer = WhatsAppDetailsRecordSerializer(details_query, many=True)
            details_data = details_serializer.data

            if not details_data and not nexus_data:
                return Response(
                    {"message": f"No records found for seq_id: {seq_id}"},
                    status=status.HTTP_404_NOT_FOUND
                )

            # ── Process msg/call records ──────────────────────────────────────
            processed_data = self.data_processor.process_records(details_data, crime_name)

            message_summaries = processed_data["message_summaries"]
            call_summaries = processed_data["call_summaries"]
            errors = processed_data["errors"]

            # Separate group messages
            group_message_summaries = [msg for msg in message_summaries if msg.get("Group ID")]

            # Separate group calls
            group_call_summaries = []
            for call in call_summaries:
                status_str = call.get("Status", "")
                if status_str and "group_update" in str(status_str).lower():
                    group_call_summaries.append(call)

            all_summaries  = message_summaries + call_summaries
            summary_data   = build_summary_from_mapping(all_summaries)
            ip_wise_summary = generate_ip_wise_summary(all_summaries)
            ip_port_summary = generate_target_ip_port_summary(all_summaries)
            ip_summary      = generate_target_ip_summary(all_summaries)

            # ── Fetch info records (connections / groups / contacts) ───────────
            try:
                info_data = fetch_info_data(seq_id)
            except Exception as info_err:
                print(f"⚠️  Info data fetch error: {info_err}")
                info_data = {
                    "target profile": [],
                    "groups info":    [],
                    "contacts info":  [],
                }


            # ── Build final result ────────────────────────────────────────────
            result = {}

            # ── Add info section first ──
            if info_data.get("target profile") or info_data.get("groups info"):
                result.update({
                    "target profile": info_data["target profile"],
                    "groups info": info_data["groups info"],
                    "contacts info": info_data["contacts info"],
                })

            # ── Add remaining sections ──
            result.update({
                "1. Mapping": all_summaries,
                "2. messages": message_summaries,
                "3. calls": call_summaries,
                "4. Group Messages": group_message_summaries,
                "5. Group Calls": group_call_summaries,
                "6. Summary": summary_data,
                "7. IP Wise Summary": ip_wise_summary,
                "8. ip port summary": ip_port_summary,
                "9. ip summary": ip_summary,
            })

            return Response(result, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {"error": f"Failed to fetch WhatsApp data: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def _apply_details_filters(self, queryset, filters):
        """
        FIXED filter for WhatsAppDetailsRecord with proper date handling for MongoEngine DateTime fields
        """
        from datetime import datetime
        import pytz

        from_date = filters.get("from_date")
        to_date = filters.get("to_date")

        def parse_date_to_datetime(dt_str, is_end=False):
            """Parse date string and return timezone-aware datetime object"""
            if not dt_str:
                return None

            dt_str = (
                str(dt_str)
                .replace("Z", "")
                .replace("+00:00", "")
                .replace(" IST", "")
                .strip()
            )

            try:
                if "T" in dt_str:
                    if "." in dt_str:
                        dt_str = dt_str.split(".")[0]
                    dt_obj = datetime.fromisoformat(dt_str)
                elif len(dt_str) == 10:
                    dt_obj = datetime.strptime(dt_str, "%Y-%m-%d")
                    if is_end:
                        dt_obj = dt_obj.replace(hour=23, minute=59, second=59)
                elif " " in dt_str:
                    if "." in dt_str:
                        dt_str = dt_str.split(".")[0]
                    dt_obj = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                else:
                    return None

                if dt_obj.tzinfo is None:
                    dt_obj = pytz.UTC.localize(dt_obj)

                return dt_obj

            except Exception as e:
                print(f"Error parsing date {dt_str}: {e}")
                return None

        from_datetime = parse_date_to_datetime(from_date, is_end=False)
        to_datetime   = parse_date_to_datetime(to_date,   is_end=True)

        print(f"DEBUG: Filtering from {from_datetime} to {to_datetime}")

        if from_datetime and to_datetime:
            queryset = queryset.filter(
                __raw__={
                    "$or": [
                        {"DateTimeIST": {"$gte": from_datetime, "$lte": to_datetime}},
                        {"DateTimeUTC": {"$gte": from_datetime, "$lte": to_datetime}}
                    ]
                }
            )
            print(f"DEBUG: Records after date filter: {queryset.count()}")
        elif from_datetime:
            queryset = queryset.filter(
                __raw__={
                    "$or": [
                        {"DateTimeIST": {"$gte": from_datetime}},
                        {"DateTimeUTC": {"$gte": from_datetime}}
                    ]
                }
            )
        elif to_datetime:
            queryset = queryset.filter(
                __raw__={
                    "$or": [
                        {"DateTimeIST": {"$lte": to_datetime}},
                        {"DateTimeUTC": {"$lte": to_datetime}}
                    ]
                }
            )

        simple_filters = {
            'min_size':    ('Size__gte',              filters.get('min_size')),
            'max_size':    ('Size__lte',              filters.get('max_size')),
            'target':      ('Target',                 filters.get('target')),
            'participant': ('Participant__icontains', filters.get('participant')),
            'call_type':   ('Call_Type',              filters.get('call_type')),
            'status':      ('Status',                 filters.get('status')),
            'record_type': ('Type',                   filters.get('record_type')),
        }

        for _, (field, value) in simple_filters.items():
            if value not in [None, "", "null", "None"]:
                queryset = queryset.filter(**{field: value})

        return queryset