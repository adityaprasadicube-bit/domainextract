from datetime import datetime
from mongoengine import get_db
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from collections import defaultdict
from functools import lru_cache
from itertools import combinations
import logging
import re

from ...models import CellTower, MccMnc, ImeiDetails
from ...serializers import CellTowerSerializer, MccMncSerializer, DeviceInfoSerializer

logger = logging.getLogger(__name__)


# Utilities

@lru_cache(maxsize=1000)
def parse_date_string(date_str):
    """Parse date string into datetime object"""
    if not date_str:
        return None
    date_str = date_str.replace("Z", "+00:00")
    formats = [
        "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except:
            pass
    try:
        return datetime.fromisoformat(date_str)
    except:
        return None


def normalize_ips(ip):
    """Normalize IP addresses to list format"""
    if not ip:
        return []
    return [str(i).strip() for i in ip if i] if isinstance(ip, list) else [str(ip).strip()]


def normalize_seq_ids(seq_id):
    """Normalize sequence IDs to list format"""
    return seq_id if isinstance(seq_id, list) else [seq_id] if seq_id else []


def format_date_range(s, e):
    """Format date range for display"""
    s = parse_date_string(s) if isinstance(s, str) else s
    e = parse_date_string(e) if isinstance(e, str) else e
    if not s or not e:
        return "Unknown"
    fmt = '%d/%b/%Y %H:%M:%S' if s.date() == e.date() else '%d/%b/%Y %H:%M:%S'
    return f"{s.strftime(fmt)} - {e.strftime('%H:%M:%S' if s.date() == e.date() else fmt)}"


def get_seq_id_metadata(seq_ids):
    """Fetch metadata for sequence IDs from IPdrNexus"""
    nexus = get_db(alias="ipdr_db")["IPdrNexus"]
    metadata = {}
    for sid in seq_ids:
        rec = nexus.find_one({"_id": sid}) or {}
        metadata[sid] = {
            "RecordType": rec.get("RecordType", "Unknown"),
            "IPDR": rec.get("IPDR", "Unknown"),
        }
    return metadata


def categorize_seq_ids(seq_ids, metadata):
    """Categorize sequence IDs by record type"""
    mobile_ids, ip_ids, tower_ids, other_ids = [], [], [], []

    for sid in seq_ids:
        rt = metadata[sid].get("RecordType", "Unknown")
        ipdr = metadata[sid].get("IPDR", "Unknown")

        if rt == "Mobile":
            mobile_ids.append(sid)
        elif rt in ["IP", "Public IP", "Destination IP"]:
            ip_ids.append(sid)
        elif rt == "Tower" or (isinstance(ipdr, str) and ipdr[:3] in ["404", "405"]):
            tower_ids.append(sid)
        else:
            other_ids.append(sid)

    logger.info(
        f"Categorized: Mobile={len(mobile_ids)}, IP={len(ip_ids)}, Tower={len(tower_ids)}, Other={len(other_ids)}")
    return mobile_ids, ip_ids, tower_ids, other_ids


def generate_unique_pairs(list_a, list_b, filter_type):
    """Generate unique pairs from two lists"""
    pairs, seen = [], set()
    for a in list_a:
        for b in list_b:
            if a != b and (a, b) not in seen and (b, a) not in seen:
                pairs.append((a, b))
                seen.add((a, b))
    logger.debug(f"Generated {len(pairs)} unique pairs for {filter_type}")
    return pairs


def extract_tower_ids(records):
    """Extract unique Tower IDs from records"""
    tower_ids = set()
    for r in records:
        tower_id = r.get("TowerID") or r.get("Tower_ID") or r.get("tower_id")
        if tower_id:
            tower_ids.add(str(tower_id))
    return list(tower_ids)


def extract_imeis(records):
    """Extract unique IMEIs from records"""
    imeis = set()
    for r in records:
        imei = r.get("IMEI")
        if imei:
            imeis.add(str(imei))
    return list(imeis) if imeis else []


def extract_imei_tacs(records):
    """Extract unique IMEI TAC numbers (first 8 digits) from records"""
    tac_numbers = set()
    for r in records:
        # Extract TAC from IMEI field
        imei = r.get("IMEI")
        if imei and len(str(imei)) >= 8:
            tac = str(imei)[:8]
            if tac.isdigit():
                tac_numbers.add(tac)

        # Also check IMEI_TAC field if exists
        imei_tac = r.get("IMEI_TAC")
        if imei_tac:
            tac_numbers.add(str(imei_tac))

    return list(tac_numbers)


def get_imei_details(tac_numbers):
    """Bulk lookup IMEI details from ImeiDetails database"""
    if not tac_numbers:
        return {}

    try:
        tac_codes = ImeiDetails.objects.filter(id__in=tac_numbers)
        tac_codesdata = DeviceInfoSerializer(tac_codes, many=True).data
        return {item["id"]: item for item in tac_codesdata}
    except Exception as e:
        logger.warning(f"Error fetching IMEI details: {str(e)}")
        return {}


def _extract_mccmnc_from_cgi(cgi):
    """Extract MCC-MNC from CGI/TowerID"""
    if not cgi:
        return None

    cgi_str = str(cgi)
    match = re.match(r'^(\d{5,6})', cgi_str)
    if match:
        return match.group(1)
    return None


def get_tower_addresses(tower_ids):
    """Bulk lookup tower addresses from CellTower database"""
    if not tower_ids:
        return {}

    lookupTower = {}
    roam_code_numbers = set()
    tower_roam_map = {}

    # Extract roaming codes from tower IDs
    for tower_id in tower_ids:
        mccmnc = _extract_mccmnc_from_cgi(tower_id)
        if mccmnc:
            roam_code_numbers.add(mccmnc)
            tower_roam_map[tower_id] = mccmnc

    # Bulk Tower lookup
    towers = CellTower.objects.filter(id__in=tower_ids)
    towersdata = CellTowerSerializer(towers, many=True).data
    lookupTowerDB = {item["id"]: item for item in towersdata}

    # Bulk Roaming code lookup
    lookupRoam = {}
    if roam_code_numbers:
        roam_codes = MccMnc.objects.filter(mccmnc_temp__in=roam_code_numbers)
        roam_codesdata = MccMncSerializer(roam_codes, many=True).data
        lookupRoam = {item["mccmnc_temp"]: item for item in roam_codesdata}

    # Build final lookup with addresses
    for tower_id in tower_ids:
        if tower_id in lookupTowerDB:
            tower_detail = lookupTowerDB[tower_id]
            tower_address = tower_detail.get("ADDRESS") or ""
            lookupTower[tower_id] = tower_address
        else:
            lookupTower[tower_id] = "Latest Tower Id. Not Exists in Our Database"

    return lookupTower


# Core Matching Engine

def get_translated_ip_for_record(record, metadata):
    """Get Translated_ip for a record based on RecordType"""
    record_type = metadata.get("RecordType", "Unknown")

    if record_type == "Public IP":
        translated_ip = metadata.get("IPDR", "Unknown")
        return normalize_ips(translated_ip) if translated_ip != "Unknown" else []
    else:
        return normalize_ips(record.get("Translated_ip"))


def build_index(records, field, sameport, metadata=None):
    """Build IP/Port index from records"""
    index = defaultdict(list)
    for r in records:
        if field == "Translated_ip" and metadata:
            ips = get_translated_ip_for_record(r, metadata)
        else:
            ips = normalize_ips(r.get(field))

        port = r.get(f"{field.split('_')[0]}_port")
        for ip in ips:
            key = (ip, port) if sameport else ip
            index[key].append(r)
    return index


def get_record_identifier(record, metadata_id=None):
    """Get identifier (MSISDN/IMSI/IMEI or metadata ID)"""
    return record.get("MSISDN") or record.get("IMSI") or record.get("IMEI") or metadata_id or "Unknown"


def aggregate_time_ranges(records):
    """Aggregate time ranges from multiple records"""
    start_times = []
    end_times = []

    for rec in records:
        s_val = rec.get("SDateTime")
        e_val = rec.get("EDateTime")

        if isinstance(s_val, str):
            s = parse_date_string(s_val)
        elif isinstance(s_val, datetime):
            s = s_val
        else:
            s = None

        if isinstance(e_val, str):
            e = parse_date_string(e_val)
        elif isinstance(e_val, datetime):
            e = e_val
        else:
            e = None

        if s:
            start_times.append(s)
        if e:
            end_times.append(e)

    min_start = min(start_times) if start_times else None
    max_end = max(end_times) if end_times else None

    return min_start, max_end


def format_imei_details(imei_list, tac_lookup):
    """Format IMEI details for display"""
    if not imei_list or not tac_lookup:
        return "Unknown"

    details_list = []
    for imei in imei_list:
        if len(imei) >= 8:
            tac = imei[:8]
            if tac in tac_lookup:
                detail = tac_lookup[tac]
                manufacturer = detail.get("manufacturer", "Unknown")
                devicetype = detail.get("devicetype", "Unknown")
                details_list.append(f"{manufacturer} - {devicetype}")
            else:
                details_list.append("IMEI Not Found in Database")
        else:
            details_list.append("Invalid IMEI")

    return ", ".join(details_list) if details_list else "Unknown"


def match_generic(a_records, b_records, a_meta, b_meta, match_type, sameport, require_msisdn, is_ip2m=False,
                  is_ip2ip=False, is_m2m=False, is_t2t=False, is_m2t=False, is_t2m=False, is_ip2t=False, is_t2ip=False):
    """Generic matching engine for all filter types"""
    # Store matches grouped by (A Party, B Party, IP display)
    grouped_matches = defaultdict(lambda: {"a_records": [], "b_records": []})

    # Initialize variables based on filter type
    if is_ip2m:
        ip_translated_ip = a_meta.get("IPDR", "Unknown")
        b_party_meta = b_meta.get("IPDR", "Unknown")
    elif is_ip2ip:
        a_translated_ip = a_meta.get("IPDR", "Unknown")
        b_translated_ip = b_meta.get("IPDR", "Unknown")
    elif is_m2m:
        a_party_meta = a_meta.get("IPDR", "Unknown")
        b_party_meta = b_meta.get("IPDR", "Unknown")
    elif is_t2t:
        a_party_meta = a_meta.get("IPDR", "Unknown")
        b_party_meta = b_meta.get("IPDR", "Unknown")
    elif is_m2t:
        a_party_meta = a_meta.get("IPDR", "Unknown")
        b_party_meta = b_meta.get("IPDR", "Unknown")
    elif is_t2m:
        a_party_meta = a_meta.get("IPDR", "Unknown")
        b_party_meta = b_meta.get("IPDR", "Unknown")
    elif is_ip2t:
        ip_translated_ip = a_meta.get("IPDR", "Unknown")
        b_party_meta = b_meta.get("IPDR", "Unknown")
    elif is_t2ip:
        a_party_meta = a_meta.get("IPDR", "Unknown")
        b_translated_ip = b_meta.get("IPDR", "Unknown")
    else:
        a_party_label = a_meta.get("IPDR", "Unknown")
        b_party_label = b_meta.get("IPDR", "Unknown")


    # DTTD MATCH TYPE
    if match_type == "DTTD":
        if is_ip2m:
            ip_trans_ip = a_meta.get("IPDR", "Unknown")
            b_dest_index = build_index(b_records, "Destination_ip", sameport)

            for a in a_records:
                a_party = get_record_identifier(a)
                if a_party == "Unknown":
                    continue

                a_trans_port = a.get("Translated_port")
                a_dest_ips = normalize_ips(a.get("Destination_ip"))
                a_dest_port = a.get("Destination_port")

                key = (ip_trans_ip, a_trans_port) if sameport else ip_trans_ip
                for b in b_dest_index.get(key, []):
                    b_trans_ips = get_translated_ip_for_record(b, b_meta)
                    b_trans_port = b.get("Translated_port")

                    if any((sameport and a_dest_ip in b_trans_ips and a_dest_port == b_trans_port) or
                           (not sameport and a_dest_ip in b_trans_ips) for a_dest_ip in a_dest_ips):
                        a_dest_ip = a_dest_ips[0] if a_dest_ips else "Unknown"
                        ip_display = f"{a_dest_ip}({a_dest_port}/{ip_trans_ip}({a_trans_port}))" if sameport else f"{ip_trans_ip}/{a_dest_ip}"
                        group_key = (a_party, b_party_meta, ip_display)
                        grouped_matches[group_key]["a_records"].append(a)
                        grouped_matches[group_key]["b_records"].append(b)

        elif is_ip2ip:
            b_dest_index = build_index(b_records, "Destination_ip", sameport)

            for a in a_records:
                a_party = get_record_identifier(a)
                if a_party == "Unknown":
                    continue

                a_trans_port = a.get("Translated_port")
                a_dest_ips = normalize_ips(a.get("Destination_ip"))
                a_dest_port = a.get("Destination_port")

                key = (a_translated_ip, a_trans_port) if sameport else a_translated_ip
                for b in b_dest_index.get(key, []):
                    b_party = get_record_identifier(b)
                    if b_party == "Unknown":
                        continue

                    b_trans_port = b.get("Translated_port")

                    if any((sameport and a_dest_ip == b_translated_ip and a_dest_port == b_trans_port) or
                           (not sameport and a_dest_ip == b_translated_ip) for a_dest_ip in a_dest_ips):
                        a_dest_ip = a_dest_ips[0] if a_dest_ips else "Unknown"
                        ip_display = f"{a_dest_ip}({a_dest_port})/{a_translated_ip}({a_trans_port})" if sameport else f"{a_dest_ip}/{a_translated_ip}"
                        group_key = (a_party, b_party, ip_display)
                        grouped_matches[group_key]["a_records"].append(a)
                        grouped_matches[group_key]["b_records"].append(b)

        elif is_m2m:
            a_trans_index = build_index(a_records, "Translated_ip", sameport)

            for b in b_records:
                b_dest_ips = normalize_ips(b.get("Destination_ip"))
                b_dest_port = b.get("Destination_port")
                b_trans_port = b.get("Translated_port")

                for b_dest_ip in b_dest_ips:
                    key = (b_dest_ip, b_dest_port) if sameport else b_dest_ip
                    for a in a_trans_index.get(key, []):
                        a_dest_ips = normalize_ips(a.get("Destination_ip"))
                        a_dest_port = a.get("Destination_port")
                        a_trans_ips = normalize_ips(a.get("Translated_ip"))
                        a_trans_port = a.get("Translated_port")

                        b_trans_ips = normalize_ips(b.get("Translated_ip"))

                        if any((sameport and a_dest_ip in b_trans_ips and a_dest_port == b_trans_port) or
                               (not sameport and a_dest_ip in b_trans_ips) for a_dest_ip in a_dest_ips):
                            a_trans_ip = a_trans_ips[0] if a_trans_ips else "Unknown"
                            a_dest_ip = a_dest_ips[0] if a_dest_ips else "Unknown"

                            ip_display = f"{a_dest_ip}({a_dest_port})/{a_trans_ip}({a_trans_port})" if sameport else f"{a_dest_ip}/{a_trans_ip}"
                            group_key = (a_party_meta, b_party_meta, ip_display)
                            grouped_matches[group_key]["a_records"].append(a)
                            grouped_matches[group_key]["b_records"].append(b)

        elif is_t2t:
            a_trans_index = build_index(a_records, "Translated_ip", sameport)

            for b in b_records:
                b_dest_ips = normalize_ips(b.get("Destination_ip"))
                b_dest_port = b.get("Destination_port")
                b_trans_port = b.get("Translated_port")

                for b_dest_ip in b_dest_ips:
                    key = (b_dest_ip, b_dest_port) if sameport else b_dest_ip
                    for a in a_trans_index.get(key, []):
                        a_dest_ips = normalize_ips(a.get("Destination_ip"))
                        a_dest_port = a.get("Destination_port")
                        a_trans_ips = normalize_ips(a.get("Translated_ip"))
                        a_trans_port = a.get("Translated_port")

                        b_trans_ips = normalize_ips(b.get("Translated_ip"))

                        if any((sameport and a_dest_ip in b_trans_ips and a_dest_port == b_trans_port) or
                               (not sameport and a_dest_ip in b_trans_ips) for a_dest_ip in a_dest_ips):
                            a_trans_ip = a_trans_ips[0] if a_trans_ips else "Unknown"
                            a_dest_ip = a_dest_ips[0] if a_dest_ips else "Unknown"

                            ip_display = f"{a_dest_ip}({a_dest_port})/{a_trans_ip}({a_trans_port})" if sameport else f"{a_dest_ip}/{a_trans_ip}"
                            group_key = (a_party_meta, b_party_meta, ip_display)
                            grouped_matches[group_key]["a_records"].append(a)
                            grouped_matches[group_key]["b_records"].append(b)

        elif is_m2t or is_t2m or is_ip2t or is_t2ip:
            # For M2T, T2M, IP2T, T2IP - similar logic to M2M/T2T
            a_trans_index = build_index(a_records, "Translated_ip", sameport, a_meta if is_ip2t or is_t2ip else None)

            for b in b_records:
                b_dest_ips = normalize_ips(b.get("Destination_ip"))
                b_dest_port = b.get("Destination_port")
                b_trans_port = b.get("Translated_port")

                for b_dest_ip in b_dest_ips:
                    key = (b_dest_ip, b_dest_port) if sameport else b_dest_ip
                    for a in a_trans_index.get(key, []):
                        a_dest_ips = normalize_ips(a.get("Destination_ip"))
                        a_dest_port = a.get("Destination_port")
                        a_trans_ips = get_translated_ip_for_record(a, a_meta) if is_ip2t or is_t2ip else normalize_ips(a.get("Translated_ip"))
                        a_trans_port = a.get("Translated_port")

                        b_trans_ips = normalize_ips(b.get("Translated_ip"))

                        if any((sameport and a_dest_ip in b_trans_ips and a_dest_port == b_trans_port) or
                               (not sameport and a_dest_ip in b_trans_ips) for a_dest_ip in a_dest_ips):
                            a_trans_ip = a_trans_ips[0] if a_trans_ips else "Unknown"
                            a_dest_ip = a_dest_ips[0] if a_dest_ips else "Unknown"

                            ip_display = f"{a_dest_ip}({a_dest_port})/{a_trans_ip}({a_trans_port})" if sameport else f"{a_dest_ip}/{a_trans_ip}"
                            group_key = (a_party_meta, b_party_meta, ip_display)
                            grouped_matches[group_key]["a_records"].append(a)
                            grouped_matches[group_key]["b_records"].append(b)

        else:
            a_trans_index = build_index(a_records, "Translated_ip", sameport, a_meta)
            b_translated_ip = b_meta.get("IPDR")
            if not b_translated_ip:
                return []

            for b in b_records:
                b_party = get_record_identifier(b, b_party_label)
                if require_msisdn and not (b.get("MSISDN") or b.get("IMSI") or b.get("IMEI")):
                    continue

                b_dest_ips = normalize_ips(b.get("Destination_ip"))
                b_dest_port = b.get("Destination_port")
                b_trans_port = b.get("Translated_port")

                for b_dest_ip in b_dest_ips:
                    key = (b_dest_ip, b_dest_port) if sameport else b_dest_ip
                    for a in a_trans_index.get(key, []):
                        a_dest_ips = normalize_ips(a.get("Destination_ip"))
                        a_dest_port = a.get("Destination_port")

                        if any((sameport and a_dest_ip == b_translated_ip and a_dest_port == b_trans_port) or
                               (not sameport and a_dest_ip == b_translated_ip) for a_dest_ip in a_dest_ips):
                            a_trans_ips = get_translated_ip_for_record(a, a_meta)
                            a_trans_port = a.get("Translated_port")
                            a_trans_ip = a_trans_ips[0] if a_trans_ips else "Unknown"
                            a_dest_ip = a_dest_ips[0] if a_dest_ips else "Unknown"

                            ip_display = f"{a_dest_ip}({a_dest_port})/{a_trans_ip}({a_trans_port})" if sameport else f"{a_dest_ip}/{a_trans_ip}"
                            group_key = (a_party_label, b_party, ip_display)
                            grouped_matches[group_key]["a_records"].append(a)
                            grouped_matches[group_key]["b_records"].append(b)

    # T2D MATCH TYPE
    elif match_type == "T2D":
        if is_ip2m:
            b_dest_index = build_index(b_records, "Destination_ip", sameport)
            for a in a_records:
                a_party = get_record_identifier(a)
                if a_party == "Unknown":
                    continue

                a_trans_port = a.get("Translated_port")
                key = (ip_translated_ip, a_trans_port) if sameport else ip_translated_ip

                for b in b_dest_index.get(key, []):
                    ip_display = f"{ip_translated_ip}({a_trans_port})" if sameport else ip_translated_ip
                    group_key = (a_party, b_party_meta, ip_display)
                    grouped_matches[group_key]["a_records"].append(a)
                    grouped_matches[group_key]["b_records"].append(b)

        elif is_ip2ip:
            b_dest_index = build_index(b_records, "Destination_ip", sameport)

            for a in a_records:
                a_party = get_record_identifier(a)
                if a_party == "Unknown":
                    continue

                a_trans_port = a.get("Translated_port")
                key = (a_translated_ip, a_trans_port) if sameport else a_translated_ip

                for b in b_dest_index.get(key, []):
                    b_party = get_record_identifier(b)
                    if b_party == "Unknown":
                        continue

                    ip_display = f"{a_translated_ip}({a_trans_port})" if sameport else a_translated_ip
                    group_key = (a_party, b_party, ip_display)
                    grouped_matches[group_key]["a_records"].append(a)
                    grouped_matches[group_key]["b_records"].append(b)

        elif is_m2m:
            a_trans_index = build_index(a_records, "Translated_ip", sameport)

            for b in b_records:
                b_dest_ips = normalize_ips(b.get("Destination_ip"))
                b_dest_port = b.get("Destination_port")

                for b_dest_ip in b_dest_ips:
                    key = (b_dest_ip, b_dest_port) if sameport else b_dest_ip
                    for a in a_trans_index.get(key, []):
                        ip_display = f"{b_dest_ip}({b_dest_port})" if sameport else b_dest_ip
                        group_key = (a_party_meta, b_party_meta, ip_display)
                        grouped_matches[group_key]["a_records"].append(a)
                        grouped_matches[group_key]["b_records"].append(b)

        elif is_t2t:
            a_trans_index = build_index(a_records, "Translated_ip", sameport)

            for b in b_records:
                b_dest_ips = normalize_ips(b.get("Destination_ip"))
                b_dest_port = b.get("Destination_port")

                for b_dest_ip in b_dest_ips:
                    key = (b_dest_ip, b_dest_port) if sameport else b_dest_ip
                    for a in a_trans_index.get(key, []):
                        ip_display = f"{b_dest_ip}({b_dest_port})" if sameport else b_dest_ip
                        group_key = (a_party_meta, b_party_meta, ip_display)
                        grouped_matches[group_key]["a_records"].append(a)
                        grouped_matches[group_key]["b_records"].append(b)

        elif is_m2t or is_t2m or is_ip2t or is_t2ip:
            # For M2T, T2M, IP2T, T2IP - similar logic to M2M/T2T
            a_trans_index = build_index(a_records, "Translated_ip", sameport, a_meta if is_ip2t or is_t2ip else None)

            for b in b_records:
                b_dest_ips = normalize_ips(b.get("Destination_ip"))
                b_dest_port = b.get("Destination_port")

                for b_dest_ip in b_dest_ips:
                    key = (b_dest_ip, b_dest_port) if sameport else b_dest_ip
                    for a in a_trans_index.get(key, []):
                        ip_display = f"{b_dest_ip}({b_dest_port})" if sameport else b_dest_ip
                        group_key = (a_party_meta, b_party_meta, ip_display)
                        grouped_matches[group_key]["a_records"].append(a)
                        grouped_matches[group_key]["b_records"].append(b)

        elif is_m2t or is_t2m or is_ip2t or is_t2ip:
            # For M2T, T2M, IP2T, T2IP - similar logic to M2M/T2T
            a_dest_index = build_index(a_records, "Destination_ip", sameport)

            for b in b_records:
                b_trans_ips = normalize_ips(b.get("Translated_ip"))
                b_trans_port = b.get("Translated_port")

                for b_trans_ip in b_trans_ips:
                    key = (b_trans_ip, b_trans_port) if sameport else b_trans_ip
                    for a in a_dest_index.get(key, []):
                        ip_display = f"{b_trans_ip}({b_trans_port})" if sameport else b_trans_ip
                        group_key = (a_party_meta, b_party_meta, ip_display)
                        grouped_matches[group_key]["a_records"].append(a)
                        grouped_matches[group_key]["b_records"].append(b)

        else:
            a_trans_index = build_index(a_records, "Translated_ip", sameport, a_meta)
            for b in b_records:
                b_party = get_record_identifier(b, b_party_label)
                if require_msisdn and not (b.get("MSISDN") or b.get("IMSI") or b.get("IMEI")):
                    continue

                b_dest_ips = normalize_ips(b.get("Destination_ip"))
                b_dest_port = b.get("Destination_port")

                for b_dest_ip in b_dest_ips:
                    key = (b_dest_ip, b_dest_port) if sameport else b_dest_ip
                    for a in a_trans_index.get(key, []):
                        ip_display = f"{b_dest_ip}({b_dest_port})" if sameport else b_dest_ip
                        group_key = (a_party_label, b_party, ip_display)
                        grouped_matches[group_key]["a_records"].append(a)
                        grouped_matches[group_key]["b_records"].append(b)

    # D2T MATCH TYPE
    elif match_type == "D2T":
        if is_ip2m:
            a_dest_index = build_index(a_records, "Destination_ip", sameport)
            for b in b_records:
                b_trans_ips = get_translated_ip_for_record(b, b_meta)
                b_trans_port = b.get("Translated_port")

                for b_trans_ip in b_trans_ips:
                    key = (b_trans_ip, b_trans_port) if sameport else b_trans_ip
                    for a in a_dest_index.get(key, []):
                        a_party = get_record_identifier(a)
                        if a_party == "Unknown":
                            continue

                        ip_display = f"{b_trans_ip}({b_trans_port})" if sameport else b_trans_ip
                        group_key = (a_party, b_party_meta, ip_display)
                        grouped_matches[group_key]["a_records"].append(a)
                        grouped_matches[group_key]["b_records"].append(b)

        elif is_ip2ip:
            a_dest_index = build_index(a_records, "Destination_ip", sameport)

            for b in b_records:
                b_party = get_record_identifier(b)
                if b_party == "Unknown":
                    continue

                b_trans_port = b.get("Translated_port")
                key = (b_translated_ip, b_trans_port) if sameport else b_translated_ip

                for a in a_dest_index.get(key, []):
                    a_party = get_record_identifier(a)
                    if a_party == "Unknown":
                        continue

                    ip_display = f"{b_translated_ip}({b_trans_port})" if sameport else b_translated_ip
                    group_key = (a_party, b_party, ip_display)
                    grouped_matches[group_key]["a_records"].append(a)
                    grouped_matches[group_key]["b_records"].append(b)

        elif is_m2m:
            a_dest_index = build_index(a_records, "Destination_ip", sameport)

            for b in b_records:
                b_trans_ips = normalize_ips(b.get("Translated_ip"))
                b_trans_port = b.get("Translated_port")

                for b_trans_ip in b_trans_ips:
                    key = (b_trans_ip, b_trans_port) if sameport else b_trans_ip
                    for a in a_dest_index.get(key, []):
                        ip_display = f"{b_trans_ip}({b_trans_port})" if sameport else b_trans_ip
                        group_key = (a_party_meta, b_party_meta, ip_display)
                        grouped_matches[group_key]["a_records"].append(a)
                        grouped_matches[group_key]["b_records"].append(b)

        elif is_t2t:
            a_dest_index = build_index(a_records, "Destination_ip", sameport)

            for b in b_records:
                b_trans_ips = normalize_ips(b.get("Translated_ip"))
                b_trans_port = b.get("Translated_port")

                for b_trans_ip in b_trans_ips:
                    key = (b_trans_ip, b_trans_port) if sameport else b_trans_ip
                    for a in a_dest_index.get(key, []):
                        ip_display = f"{b_trans_ip}({b_trans_port})" if sameport else b_trans_ip
                        group_key = (a_party_meta, b_party_meta, ip_display)
                        grouped_matches[group_key]["a_records"].append(a)
                        grouped_matches[group_key]["b_records"].append(b)

        else:
            b_translated_ip = b_meta.get("IPDR")
            if not b_translated_ip:
                return []

            a_dest_index = build_index(a_records, "Destination_ip", sameport)
            for b in b_records:
                b_party = get_record_identifier(b, b_party_label)
                if require_msisdn and not (b.get("MSISDN") or b.get("IMSI") or b.get("IMEI")):
                    continue

                b_trans_port = b.get("Translated_port")
                key = (b_translated_ip, b_trans_port) if sameport else b_translated_ip

                for a in a_dest_index.get(key, []):
                    ip_display = f"{b_translated_ip}({b_trans_port})" if sameport else b_translated_ip
                    group_key = (a_party_label, b_party, ip_display)
                    grouped_matches[group_key]["a_records"].append(a)
                    grouped_matches[group_key]["b_records"].append(b)

    # Create aggregated results from grouped matches
    results = []

    for (a_party, b_party, ip_display), match_data in grouped_matches.items():
        # Remove duplicate records
        a_unique = list({r["_id"]: r for r in match_data["a_records"]}.values())
        b_unique = list({r["_id"]: r for r in match_data["b_records"]}.values())

        # Aggregate time ranges
        a_min_start, a_max_end = aggregate_time_ranges(a_unique)
        b_min_start, b_max_end = aggregate_time_ranges(b_unique)

        # Extract Tower IDs and IMEIs
        a_tower_ids_list = extract_tower_ids(a_unique)
        b_tower_ids_list = extract_tower_ids(b_unique)
        a_imeis_list = extract_imeis(a_unique)
        b_imeis_list = extract_imeis(b_unique)

        # Extract IMEI TAC numbers for lookup
        a_tac_numbers = extract_imei_tacs(a_unique)
        b_tac_numbers = extract_imei_tacs(b_unique)

        # Bulk lookups
        all_tac_numbers = list(set(a_tac_numbers + b_tac_numbers))
        imei_details_lookup = get_imei_details(all_tac_numbers)

        all_tower_ids = a_tower_ids_list + b_tower_ids_list
        tower_address_lookup = get_tower_addresses(all_tower_ids)

        # Format tower IDs and addresses
        a_tower_ids = ", ".join(sorted(a_tower_ids_list)) if a_tower_ids_list else "Unknown"
        b_tower_ids = ", ".join(sorted(b_tower_ids_list)) if b_tower_ids_list else "Unknown"

        a_tower_addresses = [tower_address_lookup.get(tid, "Unknown") for tid in a_tower_ids_list]
        a_tower_address = ", ".join(a_tower_addresses) if a_tower_addresses else "Unknown"

        b_tower_addresses = [tower_address_lookup.get(tid, "Unknown") for tid in b_tower_ids_list]
        b_tower_address = ", ".join(b_tower_addresses) if b_tower_addresses else "Unknown"

        # Format IMEI details
        a_imei_details = format_imei_details(a_imeis_list, imei_details_lookup)
        b_imei_details = format_imei_details(b_imeis_list, imei_details_lookup)

        # Format simple IMEI list
        a_imei_simple = ", ".join(sorted(a_imeis_list)) if a_imeis_list else "Unknown"
        b_imei_simple = ", ".join(sorted(b_imeis_list)) if b_imeis_list else "Unknown"

        results.append({
            "A Party": a_party,
            "B Party": b_party,
            # "Match Type": match_type,
            # "Port Match": "Yes" if sameport else "No",
            "Application Name":"",
            "Public/Destination IP": ip_display,
            "DateRange(A Party)": format_date_range(b_min_start, b_max_end),
            "DateRange(B Party)": format_date_range(a_min_start, a_max_end),
            "A Party Tower ID": a_tower_ids,
            "B Party Tower ID": b_tower_ids,
            "A Party IMEI": a_imei_simple,
            "B Party IMEI": b_imei_simple,
            "A Party IMEI Details": a_imei_details,
            "B Party IMEI Details": b_imei_details,
            "A Party Tower Address": a_tower_address,
            "B Party Tower Address": b_tower_address,
        })

    return results


def match_records(a_seq_id, b_seq_id, match_type, sameport, require_msisdn, is_ip2m=False, is_ip2ip=False,
                  is_m2m=False, is_t2t=False, is_m2t=False, is_t2m=False, is_ip2t=False, is_t2ip=False):
    """Main matching function"""
    db = get_db(alias="ipdr_db")
    ipdr, nexus = db["IPDetailRecords"], db["IPdrNexus"]

    a_meta = nexus.find_one({"_id": a_seq_id}) or {}
    b_meta = nexus.find_one({"_id": b_seq_id}) or {}

    projection = {
        "_id": 1, "seq_id": 1, "Destination_ip": 1, "Destination_port": 1,
        "Translated_ip": 1, "Translated_port": 1, "Source_ip": 1,
        "MSISDN": 1, "IMSI": 1, "IMEI": 1, "SDateTime": 1, "EDateTime": 1,
        "TowerID": 1, "Tower_ID": 1, "tower_id": 1, "IMEI_TAC": 1
    }

    a_records = list(ipdr.find({"seq_id": a_seq_id}, projection))
    b_records = list(ipdr.find({"seq_id": b_seq_id}, projection))

    if not a_records or not b_records:
        logger.warning(f"No records for A={a_seq_id} or B={b_seq_id}")
        return []

    return match_generic(a_records, b_records, a_meta, b_meta, match_type, sameport, require_msisdn, is_ip2m, is_ip2ip,
                         is_m2m, is_t2t, is_m2t, is_t2m, is_ip2t, is_t2ip)


# Filter Implementations

def execute_filter(seq_ids, filter_type, match_type, sameport, require_msisdn):
    """Execute specific filter logic"""
    metadata = get_seq_id_metadata(seq_ids)
    mobile_ids, ip_ids, tower_ids, other_ids = categorize_seq_ids(seq_ids, metadata)

    # Determine pairs based on filter type
    if filter_type == "M2IP":
        if not mobile_ids or not ip_ids:
            print(f"❌ Error: M2IP needs both Mobile and IP seq_ids")
            return []
        pairs = generate_unique_pairs(mobile_ids, ip_ids, "M2IP")
        is_ip2m = False
        is_ip2ip = False
        is_m2m = False
        is_t2t = False
        is_m2t = False
        is_t2m = False
        is_ip2t = False
        is_t2ip = False

    elif filter_type == "M2M":
        if len(mobile_ids) < 2:
            print(f"❌ Error: M2M needs at least 2 Mobile seq_ids")
            return []
        pairs = list(combinations(mobile_ids, 2))
        require_msisdn = False
        is_ip2m = False
        is_ip2ip = False
        is_m2m = True
        is_t2t = False
        is_m2t = False
        is_t2m = False
        is_ip2t = False
        is_t2ip = False

    elif filter_type == "IP2IP":
        if len(ip_ids) < 2:
            print(f"❌ Error: IP2IP needs at least 2 IP seq_ids")
            return []
        pairs = list(combinations(ip_ids, 2))
        is_ip2m = False
        is_ip2ip = True
        is_m2m = False
        is_t2t = False
        is_m2t = False
        is_t2m = False
        is_ip2t = False
        is_t2ip = False

    elif filter_type == "IP2M":
        if not ip_ids or not mobile_ids:
            print(f"❌ Error: IP2M needs both IP and Mobile seq_ids")
            return []
        pairs = generate_unique_pairs(ip_ids, mobile_ids, "IP2M")
        is_ip2m = True
        is_ip2ip = False
        is_m2m = False
        is_t2t = False
        is_m2t = False
        is_t2m = False
        is_ip2t = False
        is_t2ip = False

    elif filter_type == "T2T":
        if len(tower_ids) < 2:
            print(f"❌ Error: T2T needs at least 2 Tower seq_ids")
            return []
        pairs = list(combinations(tower_ids, 2))
        require_msisdn = False
        is_ip2m = False
        is_ip2ip = False
        is_m2m = False
        is_t2t = True
        is_m2t = False
        is_t2m = False
        is_ip2t = False
        is_t2ip = False

    elif filter_type == "M2T":
        if not mobile_ids or not tower_ids:
            print(f"❌ Error: M2T needs both Mobile and Tower seq_ids")
            return []
        pairs = generate_unique_pairs(mobile_ids, tower_ids, "M2T")
        require_msisdn = False
        is_ip2m = False
        is_ip2ip = False
        is_m2m = False
        is_t2t = False
        is_m2t = True
        is_t2m = False
        is_ip2t = False
        is_t2ip = False

    elif filter_type == "T2M":
        if not tower_ids or not mobile_ids:
            print(f"❌ Error: T2M needs both Tower and Mobile seq_ids")
            return []
        pairs = generate_unique_pairs(tower_ids, mobile_ids, "T2M")
        require_msisdn = False
        is_ip2m = False
        is_ip2ip = False
        is_m2m = False
        is_t2t = False
        is_m2t = False
        is_t2m = True
        is_ip2t = False
        is_t2ip = False

    elif filter_type == "IP2T":
        if not ip_ids or not tower_ids:
            print(f"❌ Error: IP2T needs both IP and Tower seq_ids")
            return []
        pairs = generate_unique_pairs(ip_ids, tower_ids, "IP2T")
        is_ip2m = False
        is_ip2ip = False
        is_m2m = False
        is_t2t = False
        is_m2t = False
        is_t2m = False
        is_ip2t = True
        is_t2ip = False

    elif filter_type == "T2IP":
        if not tower_ids or not ip_ids:
            print(f"❌ Error: T2IP needs both Tower and IP seq_ids")
            return []
        pairs = generate_unique_pairs(tower_ids, ip_ids, "T2IP")
        is_ip2m = False
        is_ip2ip = False
        is_m2m = False
        is_t2t = False
        is_m2t = False
        is_t2m = False
        is_ip2t = False
        is_t2ip = True

    else:
        return []

    # Log filter info
    print(f"\n{'=' * 60}")
    print(f"{filter_type} FILTER - {match_type} Match")
    print(f"Mobile IDs: {mobile_ids}, IP IDs: {ip_ids}")
    print(f"Tower IDs: {tower_ids}, Other IDs: {other_ids}")
    print(f"Pairs: {len(pairs)}, Port Match: {'Yes' if sameport else 'No'}")
    print(f"{'=' * 60}")

    # Execute matching for all pairs
    all_results = []
    for a_seq, b_seq in pairs:
        results = match_records(a_seq, b_seq, match_type, sameport, require_msisdn, is_ip2m, is_ip2ip, is_m2m, is_t2t, is_m2t, is_t2m, is_ip2t, is_t2ip)
        all_results.extend(results)
        print(f"  → Pair {a_seq} ↔ {b_seq}: {len(results)} connections")

    print(f"\n{'=' * 60}")
    print(f"{filter_type} RESULTS: {len(all_results)} total connections")
    print(f"{'=' * 60}\n")

    logger.info(f"{filter_type} filter completed: {len(all_results)} connections")
    return all_results


# API Controller

def identify_bparty_with_filter(seq_ids, filter_type="M2IP", match_type="T2D", sameport=True, require_msisdn=True):
    """Main controller"""
    if not seq_ids or (isinstance(seq_ids, list) and len(seq_ids) < 2):
        logger.error("Insufficient seq_ids")
        return []

    if not isinstance(seq_ids, list):
        seq_ids = [seq_ids]

    filter_type, match_type = filter_type.upper(), match_type.upper()

    if filter_type not in ["M2IP", "M2M", "IP2IP", "IP2M", "T2T", "M2T", "T2M", "IP2T", "T2IP"]:
        print(f"❌ Invalid filter_type: {filter_type}")
        return []

    if match_type not in ["T2D", "D2T", "DTTD"]:
        print(f"❌ Invalid match_type: {match_type}")
        return []

    try:
        return execute_filter(seq_ids, filter_type, match_type, sameport, require_msisdn)
    except Exception as e:
        logger.error(f"Error in {filter_type}: {str(e)}", exc_info=True)
        print(f"❌ Error: {str(e)}")
        return []


# API View

class IdentifyBPartyView(APIView):
    def post(self, request):
        try:
            seq_ids = request.data.get("seq_ids", [])
            if not seq_ids or (isinstance(seq_ids, list) and len(seq_ids) < 2):
                return Response(
                    {"success": False, "error": "At least 2 seq_ids required"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if not isinstance(seq_ids, list):
                seq_ids = [seq_ids]

            filter_type = request.data.get("filter_type", "M2IP").upper()
            match_type = request.data.get("match_type", "T2D").upper()
            sameport = request.data.get("sameport", True)
            require_msisdn = request.data.get("require_msisdn", True)
            page = int(request.data.get("page", 1))
            page_size = int(request.data.get("page_size", 500))

            valid_filters = ["M2IP", "M2M", "IP2IP", "IP2M", "T2T", "M2T", "T2M", "IP2T", "T2IP"]
            valid_matches = ["T2D", "D2T", "DTTD"]

            if filter_type not in valid_filters:
                return Response({
                    "success": False,
                    "error": f"Invalid filter_type: {filter_type}",
                    "valid_options": valid_filters
                }, status=status.HTTP_400_BAD_REQUEST)

            if match_type not in valid_matches:
                return Response({
                    "success": False,
                    "error": f"Invalid match_type: {match_type}",
                    "valid_options": valid_matches
                }, status=status.HTTP_400_BAD_REQUEST)

            logger.info(f"Executing {filter_type} filter with {len(seq_ids)} seq_ids")
            results = identify_bparty_with_filter(seq_ids, filter_type, match_type, sameport, require_msisdn)

            # Remove exact duplicate records
            unique_results = []
            seen_records = set()
            for record in results:
                record_tuple = tuple(sorted(record.items()))
                if record_tuple not in seen_records:
                    seen_records.add(record_tuple)
                    unique_results.append(record)

            logger.info(f"Removed {len(results) - len(unique_results)} duplicate records")

            start_idx = (page - 1) * page_size
            paginated_results = unique_results[start_idx:start_idx + page_size]

            return Response({
                "success": True,
                "filter_type": filter_type,
                "match_type": match_type,
                "sameport": sameport,
                "port_matching": "Enabled (IP + Port)" if sameport else "Disabled (IP only)",
                "total_connections": len(unique_results),
                "data": paginated_results,
                "page": page,
                "page_size": page_size,
                "total_pages": (len(unique_results) + page_size - 1) // page_size if unique_results else 0,

            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"API Error: {str(e)}", exc_info=True)
            return Response({
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)