# # from mongoengine import get_db
# # from rest_framework.views import APIView
# # from rest_framework.response import Response
# # from bson import ObjectId
# #
# #
# # def serialize_doc(doc):
# #     """Convert ObjectId to string recursively"""
# #     if isinstance(doc, list):
# #         return [serialize_doc(d) for d in doc]
# #     if isinstance(doc, dict):
# #         new_doc = {}
# #         for k, v in doc.items():
# #             if isinstance(v, ObjectId):
# #                 new_doc[k] = str(v)
# #             elif isinstance(v, list) or isinstance(v, dict):
# #                 new_doc[k] = serialize_doc(v)
# #             else:
# #                 new_doc[k] = v
# #         return new_doc
# #     return doc
# #
# #
# # def get_ipdr_by_crime_name(crime_name):
# #     ipdr_db = get_db(alias='ipdr_db')
# #     cdr_db = get_db(alias='cdr_db')
# #
# #     crime_collection = cdr_db['CrimeRegistry']
# #     nexus_collection = ipdr_db['IPdrNexus']
# #     ipdr_collection = ipdr_db['IPDetailRecords']
# #
# #     crime_doc = crime_collection.find_one(
# #         {"Crime": crime_name},
# #         {"_id": 1}
# #     )
# #
# #     if not crime_doc:
# #         return None, "Crime not found"
# #
# #     crime_id = crime_doc["_id"]
# #
# #     nexus_docs = list(nexus_collection.find({
# #         "CrimeID": crime_id
# #     }))
# #
# #     if not nexus_docs:
# #         return None, "No nexus found for this crime"
# #
# #     nexus_ids = [n["_id"] for n in nexus_docs]
# #
# #     ipdr_records = list(ipdr_collection.find({
# #         "seq_id": {"$in": nexus_ids}
# #     }))
# #
# #     # Serialize before returning
# #     ipdr_records = serialize_doc(ipdr_records)
# #
# #     return {
# #         "total_nexus": len(nexus_ids),
# #         "ipdr_records": ipdr_records
# #     }, None
# #
# #
# # class BpartyApi(APIView):
# #     def post(self, request):
# #         crimename = request.data.get("crimename")
# #
# #         result, error = get_ipdr_by_crime_name(crimename)
# #
# #         if error:
# #             return Response({"message": error}, status=404)
# #
# #         return Response({
# #             "total_nexus": result["total_nexus"],
# #             "data": result["ipdr_records"]
# #         })
# from mongoengine import get_db
# from rest_framework.views import APIView
# from rest_framework.response import Response
# from bson import ObjectId
#
#
# def serialize_doc(doc):
#     if isinstance(doc, list):
#         return [serialize_doc(d) for d in doc]
#     if isinstance(doc, dict):
#         new_doc = {}
#         for k, v in doc.items():
#             if isinstance(v, ObjectId):
#                 new_doc[k] = str(v)
#             elif isinstance(v, (list, dict)):
#                 new_doc[k] = serialize_doc(v)
#             else:
#                 new_doc[k] = v
#         return new_doc
#     return doc
#
#
# from mongoengine import get_db
# from rest_framework.views import APIView
# from rest_framework.response import Response
# from bson import ObjectId
#
#
# def serialize_doc(doc):
#     if isinstance(doc, list):
#         return [serialize_doc(d) for d in doc]
#     if isinstance(doc, dict):
#         return {k: serialize_doc(v) for k, v in doc.items()}
#     if isinstance(doc, ObjectId):
#         return str(doc)
#     return doc
#
#
# from datetime import timedelta
# from mongoengine import get_db
# from rest_framework.views import APIView
# from rest_framework.response import Response
# from bson import ObjectId
#
#
# def serialize_doc(doc):
#     if isinstance(doc, list):
#         return [serialize_doc(d) for d in doc]
#     if isinstance(doc, dict):
#         return {k: serialize_doc(v) for k, v in doc.items()}
#     if isinstance(doc, ObjectId):
#         return str(doc)
#     return doc
#
#
# class MobileWithBpartyApi(APIView):
#     def post(self, request):
#         crime_name = request.data.get("crimename")
#
#         ipdr_db = get_db(alias='ipdr_db')
#         cdr_db = get_db(alias='cdr_db')
#
#         # Step 1: Crime ID
#         crime = cdr_db["CrimeRegistry"].find_one({"Crime": crime_name}, {"_id": 1})
#         if not crime:
#             return Response({"message": "Crime not found"}, status=404)
#
#         crime_id = crime["_id"]
#
#         # Step 2: Nexus split
#         nexus_docs = list(ipdr_db["IPdrNexus"].find({"CrimeID": crime_id}))
#
#         mobile_seq = None
#         other_seqs = []
#
#         for n in nexus_docs:
#             if n.get("RecordType") == "Mobile":
#                 mobile_seq = n["_id"]
#             else:
#                 other_seqs.append(n["_id"])
#
#         if not mobile_seq:
#             return Response({"message": "No mobile file"}, status=400)
#
#         # Step 3: Load records
#         ipdr = ipdr_db["IPDetailRecords"]
#
#         mobile_records = list(ipdr.find({"seq_id": {"$in": [mobile_seq]}}))
#         other_records = list(ipdr.find({"seq_id": {"$in": other_seqs}}))
#
#         # Step 4: Build index by Translated_ip (NAT IP)
#         ip_index = {}
#         for r in other_records:
#             tip = r.get("Translated_ip")
#             if tip:
#                 ip_index.setdefault(tip, []).append(r)
#
#         # Step 5: Attach Bparty using NAT IP + time overlap
#         for m in mobile_records:
#             tip = m.get("Translated_ip")
#             m_start = m.get("SDateTime")
#             m_end = m.get("EDateTime")
#
#             bparties = set()
#
#             for r in ip_index.get(tip, []):
#                 r_start = r.get("SDateTime")
#                 r_end = r.get("EDateTime")
#
#                 if not (m_start and m_end and r_start and r_end):
#                     continue
#
#                 # ±5 min window overlap
#                 if not (r_end < m_start - timedelta(minutes=5) or
#                         r_start > m_end + timedelta(minutes=5)):
#
#                     b = r.get("MSISDN") or r.get("IMSI") or r.get("IMEI")
#                     if b and b != m.get("MSISDN"):
#                         bparties.add(b)
#
#             m["Bparty"] = list(bparties)
#
#         mobile_records = serialize_doc(mobile_records)
#
#         return Response({
#             "crime": crime_name,
#             "total_mobile_records": len(mobile_records),
#             "data": mobile_records
#         })
from datetime import datetime, timedelta
from mongoengine import get_db
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from bson import ObjectId
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)


def serialize_doc(doc):
    """Convert ObjectId to string recursively"""
    if isinstance(doc, list):
        return [serialize_doc(d) for d in doc]
    if isinstance(doc, dict):
        return {k: serialize_doc(v) for k, v in doc.items()}
    if isinstance(doc, ObjectId):
        return str(doc)
    return doc


def parse_date_string(date_str):
    """Parse date string into datetime object"""
    if not date_str:
        return None
    if isinstance(date_str, datetime):
        return date_str

    date_str = str(date_str).replace("Z", "+00:00")
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


def format_date_range(s, e):
    """Format date range for display"""
    s = parse_date_string(s) if isinstance(s, str) else s
    e = parse_date_string(e) if isinstance(e, str) else e
    if not s or not e:
        return "Unknown"
    fmt = '%d/%b/%Y %H:%M:%S' if s.date() == e.date() else '%d/%b/%Y %H:%M:%S'
    return f"{s.strftime(fmt)} - {e.strftime('%H:%M:%S' if s.date() == e.date() else fmt)}"


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
    return list(imeis)


def extract_imei_tacs(records):
    """Extract unique IMEI TAC numbers (first 8 digits) from records"""
    tac_numbers = set()
    for r in records:
        imei = r.get("IMEI")
        if imei and len(str(imei)) >= 8:
            tac = str(imei)[:8]
            if tac.isdigit():
                tac_numbers.add(tac)

        imei_tac = r.get("IMEI_TAC")
        if imei_tac:
            tac_numbers.add(str(imei_tac))

    return list(tac_numbers)


def get_imei_details(tac_numbers):
    """Bulk lookup IMEI details from ImeiDetails database"""
    if not tac_numbers:
        return {}

    try:
        from ...models import ImeiDetails
        from ...serializers import DeviceInfoSerializer

        tac_codes = ImeiDetails.objects.filter(id__in=tac_numbers)
        tac_codesdata = DeviceInfoSerializer(tac_codes, many=True).data
        return {item["id"]: item for item in tac_codesdata}
    except Exception as e:
        logger.warning(f"Error fetching IMEI details: {str(e)}")
        return {}


def get_tower_addresses(tower_ids):
    """Bulk lookup tower addresses from CellTower database"""
    if not tower_ids:
        return {}

    try:
        from ...models import CellTower
        from ...serializers import CellTowerSerializer

        towers = CellTower.objects.filter(id__in=tower_ids)
        towersdata = CellTowerSerializer(towers, many=True).data
        lookupTower = {}

        for item in towersdata:
            tower_id = item["id"]
            tower_address = item.get("ADDRESS") or "Unknown"
            lookupTower[tower_id] = tower_address

        # Handle towers not in database
        for tower_id in tower_ids:
            if tower_id not in lookupTower:
                lookupTower[tower_id] = "Latest Tower Id. Not Exists in Our Database"

        return lookupTower
    except Exception as e:
        logger.warning(f"Error fetching tower addresses: {str(e)}")
        return {}


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


def aggregate_time_ranges(records):
    """Aggregate time ranges from multiple records"""
    start_times = []
    end_times = []

    for rec in records:
        s = parse_date_string(rec.get("SDateTime"))
        e = parse_date_string(rec.get("EDateTime"))

        if s:
            start_times.append(s)
        if e:
            end_times.append(e)

    min_start = min(start_times) if start_times else None
    max_end = max(end_times) if end_times else None

    return min_start, max_end


class MobileWithBpartyApi(APIView):
    def post(self, request):
        try:
            crime_name = request.data.get("crimename")
            time_window = int(request.data.get("time_window", 5))
            page = int(request.data.get("page", 1))
            page_size = int(request.data.get("page_size", 500))
            sameport = request.data.get("sameport", False)
            debug = request.data.get("debug", False)

            if not crime_name:
                return Response({
                    "success": False,
                    "error": "crimename is required"
                }, status=status.HTTP_400_BAD_REQUEST)

            ipdr_db = get_db(alias='ipdr_db')
            cdr_db = get_db(alias='cdr_db')

            # Step 1: Get Crime ID
            crime = cdr_db["CrimeRegistry"].find_one({"Crime": crime_name}, {"_id": 1})
            if not crime:
                return Response({
                    "success": False,
                    "error": "Crime not found"
                }, status=status.HTTP_404_NOT_FOUND)

            crime_id = crime["_id"]
            logger.info(f"Processing crime: {crime_name}, ID: {crime_id}")

            # Step 2: Get all Nexus records and categorize
            nexus_docs = list(ipdr_db["IPdrNexus"].find({"CrimeID": crime_id}))

            if not nexus_docs:
                return Response({
                    "success": False,
                    "error": "No nexus records found for this crime"
                }, status=status.HTTP_404_NOT_FOUND)

            mobile_seqs = []
            other_seqs = []
            nexus_metadata = {}

            for n in nexus_docs:
                seq_id = n["_id"]
                nexus_metadata[seq_id] = {
                    "RecordType": n.get("RecordType", "Unknown"),
                    "IPDR": n.get("IPDR", "Unknown")
                }

                if n.get("RecordType") == "Mobile":
                    mobile_seqs.append(seq_id)
                else:
                    other_seqs.append(seq_id)

            if not mobile_seqs:
                return Response({
                    "success": False,
                    "error": "No mobile records found for this crime"
                }, status=status.HTTP_404_NOT_FOUND)

            logger.info(f"Found {len(mobile_seqs)} mobile seq_ids and {len(other_seqs)} other seq_ids")

            # Step 3: Load records with projection
            ipdr = ipdr_db["IPDetailRecords"]

            projection = {
                "_id": 1, "seq_id": 1, "Destination_ip": 1, "Destination_port": 1,
                "Translated_ip": 1, "Translated_port": 1, "Source_ip": 1, "Source_port": 1,
                "MSISDN": 1, "IMSI": 1, "IMEI": 1, "SDateTime": 1, "EDateTime": 1,
                "TowerID": 1, "Tower_ID": 1, "tower_id": 1, "IMEI_TAC": 1
            }

            mobile_records = list(ipdr.find({"seq_id": {"$in": mobile_seqs}}, projection))
            other_records = list(ipdr.find({"seq_id": {"$in": other_seqs}}, projection))

            logger.info(f"Loaded {len(mobile_records)} mobile records and {len(other_records)} other records")

            debug_info = {
                "mobile_sample": [],
                "other_sample": [],
                "index_keys": [],
                "match_attempts": 0,
                "time_mismatches": 0,
                "ip_mismatches": 0,
                "successful_matches": 0
            }

            # Helper function to get seq_id (handle both list and single value)
            def get_seq_id(record):
                seq = record.get("seq_id")
                if isinstance(seq, list):
                    return seq[0] if seq else None
                return seq

            # Debug: Sample records
            if debug and mobile_records:
                m = mobile_records[0]
                debug_info["mobile_sample"].append({
                    "seq_id": str(get_seq_id(m)),
                    "MSISDN": m.get("MSISDN"),
                    "Translated_ip": m.get("Translated_ip"),
                    "Translated_port": m.get("Translated_port"),
                    "Destination_ip": m.get("Destination_ip"),
                    "Destination_port": m.get("Destination_port"),
                    "SDateTime": str(m.get("SDateTime")),
                    "EDateTime": str(m.get("EDateTime"))
                })

            if debug and other_records:
                for i in range(min(3, len(other_records))):
                    r = other_records[i]
                    r_seq_id = get_seq_id(r)
                    debug_info["other_sample"].append({
                        "seq_id": str(r_seq_id),
                        "RecordType": nexus_metadata.get(r_seq_id, {}).get("RecordType"),
                        "MSISDN": r.get("MSISDN"),
                        "IMSI": r.get("IMSI"),
                        "IMEI": r.get("IMEI"),
                        "Translated_ip": r.get("Translated_ip"),
                        "Translated_port": r.get("Translated_port"),
                        "Destination_ip": r.get("Destination_ip"),
                        "Destination_port": r.get("Destination_port"),
                        "SDateTime": str(r.get("SDateTime")),
                        "EDateTime": str(r.get("EDateTime"))
                    })

            # Step 4: Build multiple indexes for flexible matching
            translated_ip_index = defaultdict(list)
            destination_ip_index = defaultdict(list)

            for r in other_records:
                r_seq_id = get_seq_id(r)
                if not r_seq_id:
                    continue

                r_metadata = nexus_metadata.get(r_seq_id, {})

                # For Public IP records, use IPDR field as Translated_ip
                if r_metadata.get("RecordType") == "Public IP":
                    ipdr_value = r_metadata.get("IPDR", "Unknown")
                    if ipdr_value != "Unknown":
                        tips = normalize_ips(ipdr_value)
                    else:
                        tips = normalize_ips(r.get("Translated_ip"))
                else:
                    tips = normalize_ips(r.get("Translated_ip"))

                t_port = r.get("Translated_port")

                for tip in tips:
                    if tip:
                        if sameport and t_port:
                            key = (tip, t_port)
                        else:
                            key = tip
                        translated_ip_index[key].append(r)

                # Also index by Destination_ip
                dips = normalize_ips(r.get("Destination_ip"))
                d_port = r.get("Destination_port")

                for dip in dips:
                    if dip:
                        if sameport and d_port:
                            key = (dip, d_port)
                        else:
                            key = dip
                        destination_ip_index[key].append(r)

            if debug:
                debug_info["index_keys"] = {
                    "translated_ip_index_sample": [str(k) for k in list(translated_ip_index.keys())[:10]],
                    "destination_ip_index_sample": [str(k) for k in list(destination_ip_index.keys())[:10]],
                    "translated_ip_index_size": len(translated_ip_index),
                    "destination_ip_index_size": len(destination_ip_index)
                }

            # Step 5: Match mobile records with other records
            grouped_matches = defaultdict(lambda: {
                "mobile_record": None,
                "bparty_records": [],
                "bparties": set()
            })

            for m in mobile_records:
                m_seq_id = get_seq_id(m)
                m_tips = normalize_ips(m.get("Translated_ip"))
                m_port = m.get("Translated_port")
                m_dips = normalize_ips(m.get("Destination_ip"))
                m_dport = m.get("Destination_port")
                m_start = parse_date_string(m.get("SDateTime"))
                m_end = parse_date_string(m.get("EDateTime"))
                m_party = m.get("MSISDN") or m.get("IMSI") or m.get("IMEI") or "Unknown"

                if not m_start or not m_end:
                    continue

                # Strategy 1: Mobile Translated_ip matches Other Destination_ip
                for m_tip in m_tips:
                    if not m_tip:
                        continue

                    if sameport and m_port:
                        key = (m_tip, m_port)
                    else:
                        key = m_tip

                    for r in destination_ip_index.get(key, []):
                        debug_info["match_attempts"] += 1

                        r_start = parse_date_string(r.get("SDateTime"))
                        r_end = parse_date_string(r.get("EDateTime"))

                        if not r_start or not r_end:
                            continue

                        # Check time overlap with tolerance
                        if r_end < m_start - timedelta(minutes=time_window) or r_start > m_end + timedelta(
                                minutes=time_window):
                            debug_info["time_mismatches"] += 1
                            continue

                        b_party = r.get("MSISDN") or r.get("IMSI") or r.get("IMEI")
                        if b_party and b_party != m_party:
                            debug_info["successful_matches"] += 1
                            ip_display = f"{m_tip}({m_port})" if sameport and m_port else m_tip
                            group_key = (m_party, ip_display)

                            if not grouped_matches[group_key]["mobile_record"]:
                                grouped_matches[group_key]["mobile_record"] = m

                            grouped_matches[group_key]["bparties"].add(b_party)
                            grouped_matches[group_key]["bparty_records"].append(r)

                # Strategy 2: Mobile Destination_ip matches Other Translated_ip
                for m_dip in m_dips:
                    if not m_dip:
                        continue

                    if sameport and m_dport:
                        key = (m_dip, m_dport)
                    else:
                        key = m_dip

                    for r in translated_ip_index.get(key, []):
                        debug_info["match_attempts"] += 1

                        r_start = parse_date_string(r.get("SDateTime"))
                        r_end = parse_date_string(r.get("EDateTime"))

                        if not r_start or not r_end:
                            continue

                        # Check time overlap with tolerance
                        if r_end < m_start - timedelta(minutes=time_window) or r_start > m_end + timedelta(
                                minutes=time_window):
                            debug_info["time_mismatches"] += 1
                            continue

                        b_party = r.get("MSISDN") or r.get("IMSI") or r.get("IMEI")
                        if b_party and b_party != m_party:
                            debug_info["successful_matches"] += 1
                            ip_display = f"{m_dip}({m_dport})" if sameport and m_dport else m_dip
                            group_key = (m_party, ip_display)

                            if not grouped_matches[group_key]["mobile_record"]:
                                grouped_matches[group_key]["mobile_record"] = m

                            grouped_matches[group_key]["bparties"].add(b_party)
                            grouped_matches[group_key]["bparty_records"].append(r)

            # Step 6: Build final results with aggregated data
            results = []

            for (m_party, ip_display), match_data in grouped_matches.items():
                mobile_rec = match_data["mobile_record"]
                bparty_recs = match_data["bparty_records"]
                bparties = list(match_data["bparties"])

                # Remove duplicate B-party records
                unique_bparty_recs = list({r["_id"]: r for r in bparty_recs}.values())

                # Aggregate time ranges
                m_start, m_end = aggregate_time_ranges([mobile_rec])
                b_start, b_end = aggregate_time_ranges(unique_bparty_recs)

                # Extract Tower IDs and IMEIs
                m_tower_ids = extract_tower_ids([mobile_rec])
                b_tower_ids = extract_tower_ids(unique_bparty_recs)
                m_imeis = extract_imeis([mobile_rec])
                b_imeis = extract_imeis(unique_bparty_recs)

                # Get TAC numbers
                m_tacs = extract_imei_tacs([mobile_rec])
                b_tacs = extract_imei_tacs(unique_bparty_recs)

                # Bulk lookups
                all_tacs = list(set(m_tacs + b_tacs))
                imei_lookup = get_imei_details(all_tacs)

                all_tower_ids = m_tower_ids + b_tower_ids
                tower_lookup = get_tower_addresses(all_tower_ids)

                # Format data
                m_tower_id_str = ", ".join(sorted(m_tower_ids)) if m_tower_ids else "Unknown"
                b_tower_id_str = ", ".join(sorted(b_tower_ids)) if b_tower_ids else "Unknown"

                m_tower_addr = ", ".join(
                    [tower_lookup.get(tid, "Unknown") for tid in m_tower_ids]) if m_tower_ids else "Unknown"
                b_tower_addr = ", ".join(
                    [tower_lookup.get(tid, "Unknown") for tid in b_tower_ids]) if b_tower_ids else "Unknown"

                m_imei_str = ", ".join(sorted(m_imeis)) if m_imeis else "Unknown"
                b_imei_str = ", ".join(sorted(b_imeis)) if b_imeis else "Unknown"

                m_imei_details = format_imei_details(m_imeis, imei_lookup)
                b_imei_details = format_imei_details(b_imeis, imei_lookup)

                results.append({
                    "Mobile Party": m_party,
                    "B Parties": ", ".join(sorted(bparties)),
                    "B Party Count": len(bparties),
                    "Matched IP": ip_display,
                    "Mobile DateRange": format_date_range(m_start, m_end),
                    "BParty DateRange": format_date_range(b_start, b_end),
                    "Mobile Tower ID": m_tower_id_str,
                    "BParty Tower ID": b_tower_id_str,
                    "Mobile IMEI": m_imei_str,
                    "BParty IMEI": b_imei_str,
                    "Mobile IMEI Details": m_imei_details,
                    "BParty IMEI Details": b_imei_details,
                    "Mobile Tower Address": m_tower_addr,
                    "BParty Tower Address": b_tower_addr,
                    "Connection Count": len(unique_bparty_recs)
                })

            # Remove duplicates
            unique_results = []
            seen_records = set()
            for record in results:
                record_tuple = tuple(sorted(record.items()))
                if record_tuple not in seen_records:
                    seen_records.add(record_tuple)
                    unique_results.append(record)

            logger.info(f"Generated {len(unique_results)} unique connections")

            # Pagination
            start_idx = (page - 1) * page_size
            paginated_results = unique_results[start_idx:start_idx + page_size]

            response_data = {
                "success": True,
                "crime": crime_name,
                "time_window_minutes": time_window,
                "port_matching": "Enabled (IP + Port)" if sameport else "Disabled (IP only)",
                "total_mobile_seqs": len(mobile_seqs),
                "total_other_seqs": len(other_seqs),
                "total_mobile_records": len(mobile_records),
                "total_other_records": len(other_records),
                "total_connections": len(unique_results),
                "data": paginated_results,
                "page": page,
                "page_size": page_size,
                "total_pages": (len(unique_results) + page_size - 1) // page_size if unique_results else 0
            }

            if debug:
                response_data["debug"] = debug_info

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"API Error: {str(e)}", exc_info=True)
            return Response({
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)