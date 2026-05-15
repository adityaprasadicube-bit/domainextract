from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from collections import defaultdict
from django.utils.dateparse import parse_datetime

from ..TowerDump.towerdump.towerdump_models.towerdump_model import TowerDumpDetailRecord
from ..ipdr.ipdr_models.ip_model import IPDRRecord

from ..models import CallDetailRecord, Nexus
from ..Whatsapp.whatsapp_models.whatsapp_models import WhatsAppNexus, WhatsAppDetailsRecord

import re


def normalize_mobile(value):
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    # Extract digits only
    digits = re.sub(r"\D", "", value)
    if not digits:
        return None
    if len(digits) >= 10:
        last10 = digits[-10:]
        if last10[0] in "6789":
            return last10
    if len(digits) > 10:
        return digits


def normalize_imei(value):
    """Normalize IMEI - extract digits only"""
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    # Extract digits only
    digits = re.sub(r"\D", "", value)
    if digits and len(digits) >= 14:  # Valid IMEI should be 14-15 digits
        return digits
    return None


def normalize_cellid(value):
    """Normalize Cell ID - return as-is after stripping"""
    if value is None:
        return None
    value = str(value).strip()
    return value if value else None


class AutoCDRAnalysisView(APIView):

    @swagger_auto_schema(
        operation_description="Fetch CDR, TowerDump, IPDR & WhatsApp and correlate records using various identifiers",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'cdr_seqid': openapi.Schema(type=openapi.TYPE_STRING, description="CDR seq_id (optional)"),
                'towerdump_seqid': openapi.Schema(type=openapi.TYPE_STRING, description="TowerDump seq_id (optional)"),
                'ipdr_seqid': openapi.Schema(type=openapi.TYPE_STRING, description="IPDR seq_id (optional)"),
                'whatsapp_seqid': openapi.Schema(type=openapi.TYPE_STRING, description="WhatsApp seq_id (optional)"),
                'filter_type': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="Filter type: 'mobile' (default), 'imei', or 'cellid'",
                    enum=['mobile', 'imei', 'cellid']
                ),
                'cdr_type': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="CDR party type: 'a' (A-Party), 'b' (B-Party, default), or 'both' (only for mobile)",
                    enum=['a', 'b', 'both']
                ),
                'tower_type': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="TowerDump party type: 'a' (A-Party), 'b' (B-Party, default), or 'both' (only for mobile)",
                    enum=['a', 'b', 'both']
                ),
                'wap_type': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description="WhatsApp party type: 'a' (TargetNo from Nexus), 'b' (Participant from Details, default), or 'both' (both TargetNo and Participant)",
                    enum=['a', 'b', 'both']
                ),
                'cdr_fromdate': openapi.Schema(type=openapi.TYPE_STRING, format='date-time',
                                               description='CDR from_date filter'),
                'cdr_todate': openapi.Schema(type=openapi.TYPE_STRING, format='date-time',
                                             description='CDR to_date filter'),
                'tower_fromdate': openapi.Schema(type=openapi.TYPE_STRING, format='date-time',
                                                 description='TowerDump from_date filter'),
                'tower_todate': openapi.Schema(type=openapi.TYPE_STRING, format='date-time',
                                               description='TowerDump to_date filter'),
                'ipdr_fromdate': openapi.Schema(type=openapi.TYPE_STRING, format='date-time',
                                                description='IPDR from_date filter'),
                'ipdr_todate': openapi.Schema(type=openapi.TYPE_STRING, format='date-time',
                                              description='IPDR to_date filter'),
                'wap_fromdate': openapi.Schema(type=openapi.TYPE_STRING, format='date-time',
                                               description='WhatsApp from_date filter'),
                'wap_todate': openapi.Schema(type=openapi.TYPE_STRING, format='date-time',
                                             description='WhatsApp to_date filter'),
            }
        ),
    )
    def post(self, request):
        try:
            # ---------------- REQUIRED INPUT ----------------
            cdr_seqid = request.data.get("cdr_seqid")  # CDR
            towerdump_seqid = request.data.get("towerdump_seqid")  # TowerDump
            ipdr_seqid = request.data.get("ipdr_seqid")  # IPDR
            whatsapp_seqid = request.data.get("whatsapp_seqid")  # WhatsApp

            # At least 2 seq_ids are required
            provided_sources = sum([bool(cdr_seqid), bool(towerdump_seqid), bool(ipdr_seqid), bool(whatsapp_seqid)])
            if provided_sources < 2:
                return Response(
                    {
                        "error": "At least 2 seq_ids are required (cdr_seqid=CDR, towerdump_seqid=TowerDump, ipdr_seqid=IPDR, whatsapp_seqid=WhatsApp)"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # ---------------- FILTER CONFIGURATION ----------------
            filter_type = request.data.get("filter_type", "mobile").lower()
            cdr_type = request.data.get("cdr_type", "b").lower()
            tower_type = request.data.get("tower_type", "b").lower()
            wap_type = request.data.get("wap_type", "b").lower()

            # Validate filter_type
            if filter_type not in ["mobile", "imei", "cellid"]:
                return Response(
                    {"error": "filter_type must be 'mobile', 'imei', or 'cellid'"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Validate party types (only applicable for mobile)
            if filter_type == "mobile":
                if cdr_type not in ["a", "b", "both"] or tower_type not in ["a", "b", "both"]:
                    return Response(
                        {"error": "cdr_type and tower_type must be 'a', 'b', or 'both' for mobile"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                if wap_type not in ["a", "b", "both"]:
                    return Response(
                        {"error": "wap_type must be 'a', 'b', or 'both' for mobile"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            elif filter_type in ["imei", "cellid"]:
                # For IMEI and CellID, ignore party type parameters
                cdr_type = None
                tower_type = None
                wap_type = None

            # ---------------- MODULE-SPECIFIC DATE PARSING ----------------
            cdr_fromdate = parse_datetime(request.data.get("cdr_fromdate")) if request.data.get(
                "cdr_fromdate") else None
            cdr_todate = parse_datetime(request.data.get("cdr_todate")) if request.data.get("cdr_todate") else None

            tower_fromdate = parse_datetime(request.data.get("tower_fromdate")) if request.data.get(
                "tower_fromdate") else None
            tower_todate = parse_datetime(request.data.get("tower_todate")) if request.data.get(
                "tower_todate") else None

            ipdr_fromdate = parse_datetime(request.data.get("ipdr_fromdate")) if request.data.get(
                "ipdr_fromdate") else None
            ipdr_todate = parse_datetime(request.data.get("ipdr_todate")) if request.data.get("ipdr_todate") else None

            wap_fromdate = parse_datetime(request.data.get("wap_fromdate")) if request.data.get(
                "wap_fromdate") else None
            wap_todate = parse_datetime(request.data.get("wap_todate")) if request.data.get("wap_todate") else None

            # ---------------- FETCH DATA ----------------
            cdr_data = []
            tower_data = []
            ipdr_data = []
            whatsapp_data = []

            # Fetch CDR
            if cdr_seqid:
                cdr_qs = CallDetailRecord.objects(seq_id=cdr_seqid)
                if cdr_fromdate:
                    cdr_qs = cdr_qs.filter(SDateTime__gte=cdr_fromdate)
                if cdr_todate:
                    cdr_qs = cdr_qs.filter(SDateTime__lte=cdr_todate)
                cdr_data = list(cdr_qs.as_pymongo())

            # Fetch Tower Dump
            if towerdump_seqid:
                tower_qs = TowerDumpDetailRecord.objects(seq_id=towerdump_seqid)
                if tower_fromdate:
                    tower_qs = tower_qs.filter(SDateTime__gte=tower_fromdate)
                if tower_todate:
                    tower_qs = tower_qs.filter(SDateTime__lte=tower_todate)
                tower_data = list(tower_qs.as_pymongo())

            # Fetch IPDR
            if ipdr_seqid:
                ipdr_qs = IPDRRecord.objects(seq_id=ipdr_seqid)
                if ipdr_fromdate:
                    ipdr_qs = ipdr_qs.filter(SDateTime__gte=ipdr_fromdate)
                if ipdr_todate:
                    ipdr_qs = ipdr_qs.filter(SDateTime__lte=ipdr_todate)
                ipdr_data = list(ipdr_qs.as_pymongo())

            # Fetch WhatsApp
            if whatsapp_seqid:
                if filter_type == "mobile":
                    if wap_type in ["a", "both"]:
                        # Fetch from WhatsAppNexus using TargetNo
                        wap_nexus_qs = WhatsAppNexus.objects(_id=whatsapp_seqid)
                        if wap_fromdate:
                            wap_nexus_qs = wap_nexus_qs.filter(FromDate__lte=wap_todate if wap_todate else wap_fromdate)
                        if wap_todate:
                            wap_nexus_qs = wap_nexus_qs.filter(ToDate__gte=wap_fromdate if wap_fromdate else wap_todate)
                        whatsapp_data.extend(list(wap_nexus_qs.as_pymongo()))

                    if wap_type in ["b", "both"]:
                        # Fetch from WhatsAppDetailsRecord using Participant
                        wap_details_qs = WhatsAppDetailsRecord.objects(seq_id=whatsapp_seqid)
                        if wap_fromdate:
                            wap_details_qs = wap_details_qs.filter(DateTimeIST__gte=wap_fromdate)
                        if wap_todate:
                            wap_details_qs = wap_details_qs.filter(DateTimeIST__lte=wap_todate)
                        whatsapp_data.extend(list(wap_details_qs.as_pymongo()))

            # ---------------- BUILD NEXUS LOOKUP (FOR A_PARTY FALLBACK) ----------------
            nexus_aparty_values = set()
            if cdr_seqid and filter_type == "mobile":
                # Check if we need A_Party (cdr_type is 'a' or 'both')
                if cdr_type in ["a", "both"]:
                    # Fetch all Nexus records for this seq_id within date range
                    nexus_qs = Nexus.objects(id=cdr_seqid)

                    # Apply date filters if provided
                    if cdr_fromdate:
                        nexus_qs = nexus_qs.filter(FromDate__lte=cdr_todate if cdr_todate else cdr_fromdate)
                    if cdr_todate:
                        nexus_qs = nexus_qs.filter(ToDate__gte=cdr_fromdate if cdr_fromdate else cdr_todate)

                    nexus_records = nexus_qs.only('CDRNo_Or_ImeiNo')

                    # Collect all CDRNo_Or_ImeiNo values as potential A_Party values
                    for nexus in nexus_records:
                        if nexus.CDRNo_Or_ImeiNo:
                            nexus_aparty_values.add(nexus.CDRNo_Or_ImeiNo)

            # ---------------- BUILD INDEXES BASED ON FILTER TYPE ----------------
            cdr_index = defaultdict(list)
            tower_index = defaultdict(list)
            ipdr_index = defaultdict(list)
            whatsapp_index = defaultdict(list)

            if filter_type == "imei":
                # IMEI mode: Check IMEI field only (no party distinction)
                normalize_func = normalize_imei
                cdr_fields = [("IMEI", "imei")]
                tower_fields = [("IMEI", "imei")]
                ipdr_fields = [("IMEI", "imei")]
                whatsapp_fields = []  # WhatsApp doesn't have IMEI
                field_label = "IMEI Correlation"

            elif filter_type == "cellid":
                # CellID mode: Check First_CGI for CDR/Tower, TowerID for IPDR
                normalize_func = normalize_cellid
                cdr_fields = [("First_CGI", "first_cgi")]
                tower_fields = [("First_CGI", "first_cgi")]
                ipdr_fields = [("TowerID", "towerid", "tower_id")]  # IPDR uses TowerID
                whatsapp_fields = []  # WhatsApp doesn't have CellID
                field_label = "Cell ID Correlation"

            elif filter_type == "mobile":
                # Mobile mode: Use party-based logic for CDR/Tower, MSISDN for IPDR
                normalize_func = normalize_mobile

                # CDR fields
                if cdr_type == "both":
                    cdr_fields = [("A_Party", "a_party"), ("B_Party", "b_party")]
                else:
                    party = cdr_type.upper()
                    cdr_fields = [(f"{party}_Party", f"{cdr_type}_party")]

                # Tower fields
                if tower_type == "both":
                    tower_fields = [("A_Party", "a_party"), ("B_Party", "b_party")]
                else:
                    party = tower_type.upper()
                    tower_fields = [(f"{party}_Party", f"{tower_type}_party")]

                # IPDR always uses MSISDN (A_Party equivalent)
                ipdr_fields = [("MSISDN", "msisdn")]

                # WhatsApp fields based on wap_type
                if wap_type == "both":
                    whatsapp_fields = [("TargetNo", "targetno"), ("Participant", "participant")]
                elif wap_type == "a":
                    whatsapp_fields = [("TargetNo", "targetno")]
                else:
                    whatsapp_fields = [("Participant", "participant")]

                # Generate field label
                if wap_type == "both":
                    wap_label = "Both TargetNo & Participant"
                elif wap_type == "a":
                    wap_label = "TargetNo"
                else:
                    wap_label = "Participant"

                if cdr_type == "both" and tower_type == "both":
                    field_label = f"Mobile (Both A & B-Party CDR, Both A & B-Party Tower, MSISDN IPDR, {wap_label} WhatsApp)"
                elif cdr_type == "both":
                    field_label = f"Mobile (Both A & B-Party CDR, {tower_type.upper()}-Party Tower, MSISDN IPDR, {wap_label} WhatsApp)"
                elif tower_type == "both":
                    field_label = f"Mobile ({cdr_type.upper()}-Party CDR, Both A & B-Party Tower, MSISDN IPDR, {wap_label} WhatsApp)"
                else:
                    field_label = f"Mobile ({cdr_type.upper()}-Party CDR, {tower_type.upper()}-Party Tower, MSISDN IPDR, {wap_label} WhatsApp)"

            # Build CDR index
            if cdr_seqid:
                for r in cdr_data:
                    for field_tuple in cdr_fields:
                        raw = None
                        for field_name in field_tuple:
                            raw = r.get(field_name)
                            if raw:
                                break

                        # **NEW LOGIC**: If A_Party is empty and we have Nexus values, use them
                        if not raw and filter_type == "mobile" and "A_Party" in field_tuple:
                            # Use all Nexus CDRNo_Or_ImeiNo values as potential A_Party values
                            for nexus_value in nexus_aparty_values:
                                normalized = normalize_func(nexus_value)
                                if normalized:
                                    cdr_index[normalized].append(r)
                            continue  # Skip normal normalization since we already processed

                        normalized = normalize_func(raw)
                        if normalized:
                            cdr_index[normalized].append(r)

            # Build Tower index
            if towerdump_seqid:
                for r in tower_data:
                    for field_tuple in tower_fields:
                        raw = None
                        for field_name in field_tuple:
                            raw = r.get(field_name)
                            if raw:
                                break
                        normalized = normalize_func(raw)
                        if normalized:
                            tower_index[normalized].append(r)

            # Build IPDR index
            if ipdr_seqid:
                for r in ipdr_data:
                    for field_tuple in ipdr_fields:
                        raw = None
                        for field_name in field_tuple:
                            raw = r.get(field_name)
                            if raw:
                                break
                        normalized = normalize_func(raw)
                        if normalized:
                            ipdr_index[normalized].append(r)

            # Build WhatsApp index
            if whatsapp_seqid and whatsapp_fields:
                for r in whatsapp_data:
                    for field_tuple in whatsapp_fields:
                        raw = None
                        for field_name in field_tuple:
                            raw = r.get(field_name)
                            if raw:
                                break
                        normalized = normalize_func(raw)
                        if normalized:
                            whatsapp_index[normalized].append(r)

            # ---------------- FIND COMMON VALUES (AT LEAST 2 SOURCES MATCH) ----------------
            all_values = set()
            if cdr_index:
                all_values.update(cdr_index.keys())
            if tower_index:
                all_values.update(tower_index.keys())
            if ipdr_index:
                all_values.update(ipdr_index.keys())
            if whatsapp_index:
                all_values.update(whatsapp_index.keys())

            # Find values that appear in at least 2 sources
            matched_values = {}
            for value in all_values:
                sources = []
                if value in cdr_index:
                    sources.append("CDR")
                if value in tower_index:
                    sources.append("TowerDump")
                if value in ipdr_index:
                    sources.append("IPDR")
                if value in whatsapp_index:
                    sources.append("WhatsApp")

                # At least 2 sources must have this value
                if len(sources) >= 2:
                    matched_values[value] = sources

            if not matched_values:
                return Response(
                    {
                        "matched": False,
                        "message": f"No common {filter_type} values found in at least 2 sources using {field_label}",
                        "filter_config": {
                            "filter_type": filter_type,
                            "cdr_type": cdr_type,
                            "tower_type": tower_type,
                            "wap_type": wap_type,
                            "comparison": field_label,
                            "sources_provided": {
                                "CDR": bool(cdr_seqid),
                                "TowerDump": bool(towerdump_seqid),
                                "IPDR": bool(ipdr_seqid),
                                "WhatsApp": bool(whatsapp_seqid)
                            }
                        },
                        "unique_counts": {
                            "cdr": len(cdr_index) if cdr_seqid else 0,
                            "tower": len(tower_index) if towerdump_seqid else 0,
                            "ipdr": len(ipdr_index) if ipdr_seqid else 0,
                            "whatsapp": len(whatsapp_index) if whatsapp_seqid else 0
                        }
                    },
                    status=status.HTTP_200_OK
                )

            # ---------------- BUILD RESPONSE ----------------
            results = []

            for value, sources in matched_values.items():
                # Get time ranges from each source
                cdr_times = []
                tower_times = []
                ipdr_times = []
                whatsapp_times = []

                if "CDR" in sources:
                    cdr_times = sorted(
                        r["SDateTime"] for r in cdr_index[value] if r.get("SDateTime")
                    )

                if "TowerDump" in sources:
                    tower_times = sorted(
                        r["SDateTime"] for r in tower_index[value] if r.get("SDateTime")
                    )

                if "IPDR" in sources:
                    ipdr_times = sorted(
                        r["SDateTime"] for r in ipdr_index[value] if r.get("SDateTime")
                    )

                if "WhatsApp" in sources:
                    # For WhatsApp, use appropriate date field based on wap_type
                    whatsapp_times = []
                    for r in whatsapp_index[value]:
                        if wap_type in ["a", "both"]:
                            # Check for WhatsAppNexus records (FromDate/ToDate)
                            if r.get("FromDate"):
                                whatsapp_times.append(r["FromDate"])
                            if r.get("ToDate"):
                                whatsapp_times.append(r["ToDate"])

                        if wap_type in ["b", "both"]:
                            # Check for WhatsAppDetailsRecord records (DateTimeIST)
                            if r.get("DateTimeIST"):
                                whatsapp_times.append(r["DateTimeIST"])

                    whatsapp_times = sorted(whatsapp_times) if whatsapp_times else []

                # Display last 10 digits for mobile, full value for others
                display_value = value[-10:] if filter_type == "mobile" and len(value) >= 10 else value

                result_item = {
                    filter_type: display_value,
                    "matched_sources": ", ".join(sources),
                    "match_count": len(sources)
                }

                if "CDR" in sources:
                    # result_item["cdr_count"] = len(cdr_index[value])
                    result_item["cdr_time_range"] = f"{cdr_times[0]} - {cdr_times[-1]}" if cdr_times else None

                if "TowerDump" in sources:
                    # result_item["towerdump_count"] = len(tower_index[value])
                    result_item[
                        "towerdump_time_range"] = f"{tower_times[0]} - {tower_times[-1]}" if tower_times else None

                if "IPDR" in sources:
                    # result_item["ipdr_count"] = len(ipdr_index[value])
                    result_item["ipdr_time_range"] = f"{ipdr_times[0]} - {ipdr_times[-1]}" if ipdr_times else None

                if "WhatsApp" in sources:
                    # result_item["whatsapp_count"] = len(whatsapp_index[value])
                    result_item[
                        "whatsapp_time_range"] = f"{whatsapp_times[0]} - {whatsapp_times[-1]}" if whatsapp_times else None

                results.append(result_item)
                print(results)

            # Sort results by match_count (descending), then by value
            results.sort(key=lambda x: (-x["match_count"], x[filter_type]))

            # Calculate summary statistics
            summary = {
                "two_source_matches": sum(1 for r in results if r["match_count"] == 2)
            }

            # Only add three_source_matches if at least 3 sources were provided
            if provided_sources >= 3:
                summary["three_source_matches"] = sum(1 for r in results if r["match_count"] == 3)

            # Only add four_source_matches if all 4 sources were provided
            if provided_sources >= 4:
                summary["four_source_matches"] = sum(1 for r in results if r["match_count"] == 4)

            return Response(
                {
                    "matched_count": len(results),
                    # "filter_config": {
                    #     "filter_type": filter_type,
                    #     "cdr_type": cdr_type if filter_type == "mobile" else "N/A",
                    #     "tower_type": tower_type if filter_type == "mobile" else "N/A",
                    #     "wap_type": wap_type if (filter_type == "mobile" and whatsapp_seqid) else "N/A",
                    #     "comparison": field_label,
                    #     "sources_provided": {
                    #         "CDR": bool(cdr_seqid),
                    #         "TowerDump": bool(towerdump_seqid),
                    #         "IPDR": bool(ipdr_seqid),
                    #         "WhatsApp": bool(whatsapp_seqid)
                    #     }
                    # },
                    # "summary": summary,
                    "data": results
                },
                status=status.HTTP_200_OK
            )

        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )