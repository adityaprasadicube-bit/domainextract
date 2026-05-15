"""
new_summary.py
──────────────
POST /whatsapp-summary/

Returns a structured JSON summary report (mirrors GRS report format)
using WhatsApp-specific data. No Excel file is generated.

Request body:
    {
        "seq_id":     "...",
        "from_date":  "2024-01-01",   # optional
        "to_date":    "2024-12-31",   # optional
        "top_n":      10              # optional, default 10
    }

Response (200):
    {
        "1. WhatsApp Number Info":         [...],
        "2. Communication Summary":        [...],
        "3. Other Party Frequency Wise":   [...],
        "4. Other Party Duration Wise":    [...],
        "5. Top Message Types":            [...],
        "6. Top Call Types":               [...],
        "7. Group Activity Summary":       {...},
        "8. Top 5 Groups Frequency Wise":  [...],
        "9. IP Connections Country Wise":  [...],
        "10. Device Usage":                        {...},
        "11. Time Usage":                          {...},
        "12. Alerts & Flags":                      [...],
        "13. Final Observation":                   "...",
        "14. App Version":                         [...],
        "15. Device OS Build Number":              [...],
        "16. Top 5 Other Party - Individual Type": [...],
        "17. Top 5 Other Party - Group Type":      [...],
        "18. Top 5 Group - Member Wise":           {"All Groups":[...], "Owned Groups":[...], "Participated Groups":[...]},
        "19. Top 5 Latest Creation Group Name":    [...]
    }
"""

import pytz
from collections import defaultdict
from datetime import datetime

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from ..whatsapp_models.serializers import (
    WhatsAppDetailsRecordSerializer,
    WhatsappNexusSerializer,
)

from .whatsapp_views import (
    IPLookupService,
    WhatsAppDataProcessor,
    fetch_info_data,
)
from .wp_reports import (
    build_summary_from_mapping,
    generate_ip_wise_summary,
    generate_target_ip_port_summary,
    generate_target_ip_summary,
)
from .wp_summary import generate_advanced_summary
from ..whatsapp_models.whatsapp_models import WhatsAppNexus, WhatsAppDetailsRecord
from ...models import MobileOperator


def _fmt_dt(v):
    if isinstance(v, datetime):
        return v.strftime("%d-%m-%Y %H:%M:%S")
    return v or ""


class WhatsAppSummaryReportView(APIView):
    """
    POST /whatsapp-summary/

    Returns a structured JSON WhatsApp summary report with 19 numbered
    sections matching the GRS report layout (13 original + 6 new sections:
    App Version, Device OS Build Number, Top 5 Other Party Individual/Group
    Type, Top 5 Group Member Wise, Top 5 Latest Creation Group Name).
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.ip_lookup_service = IPLookupService()
        self.data_processor    = WhatsAppDataProcessor(self.ip_lookup_service)

    @swagger_auto_schema(
        operation_summary="WhatsApp Summary Report (JSON)",
        operation_description=(
            "Returns a structured JSON summary report with 19 numbered sections: "
            "WhatsApp Number Info, Communication Summary, Other Party Frequency/Duration, "
            "Message Types, Call Types, Group Activity, IP Connections, Device Usage, "
            "Time Usage, Alerts & Flags, Final Observation, App Version, "
            "Device OS Build Number, Top 5 Other Party (Individual/Group Type), "
            "Top 5 Group Member Wise, and Top 5 Latest Creation Group Name."
        ),
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["seq_id"],
            properties={
                "seq_id":    openapi.Schema(type=openapi.TYPE_STRING),
                "from_date": openapi.Schema(type=openapi.TYPE_STRING, example="2024-01-01"),
                "to_date":   openapi.Schema(type=openapi.TYPE_STRING, example="2024-12-31"),
                "top_n":     openapi.Schema(type=openapi.TYPE_INTEGER, default=10),
            },
        ),
        responses={
            200: openapi.Response(description="JSON summary report"),
            400: openapi.Response(description="Missing seq_id"),
            404: openapi.Response(description="No records found"),
            500: openapi.Response(description="Internal error"),
        },
        tags=["WhatsApp"],
    )
    def post(self, request):
        try:
            seq_id = request.data.get("seq_id")
            if not seq_id:
                return Response({"error": "seq_id is required."},
                                status=status.HTTP_400_BAD_REQUEST)

            top_n = int(request.data.get("top_n", 10))

            # ── 1. Nexus metadata ─────────────────────────────────────────────
            crime_name     = None
            target         = None
            formatted_date = None

            try:
                nexus_obj  = WhatsAppNexus.objects.get(_id=seq_id)
                nexus_data = WhatsappNexusSerializer(nexus_obj).data
                crime_name = nexus_data.get("CrimeName")
                target     = nexus_data.get("Target")
                raw_date   = nexus_data.get("ToDateIST")
                if raw_date:
                    dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                    formatted_date = dt.strftime("%d-%m-%Y")
            except WhatsAppNexus.DoesNotExist:
                pass

            # ── 1b. Mobile operator lookup (State / Region & Service Provider) ─
            circle   = ""
            operator = ""

            try:
                # Strip whitespace and leading '+'
                raw_number = str(target or "").strip().lstrip("+")
                # Remove leading country code 91 for Indian numbers if present
                if raw_number.startswith("91") and len(raw_number) >= 12:
                    raw_number = raw_number[2:]
                prefix_4 = int(raw_number[:4]) if len(raw_number) >= 4 and raw_number[:4].isdigit() else None

                if prefix_4:
                    mob = MobileOperator.objects.filter(id=prefix_4).first()
                    if mob:
                        circle   = mob.Circle   or ""
                        operator = mob.Operator or ""
            except Exception as e:
                print(f"[wa_summary] MobileOperator lookup error: {e}")

            # ── 2. CDR records ────────────────────────────────────────────────
            queryset     = WhatsAppDetailsRecord.objects.filter(seq_id=seq_id)
            queryset     = self._apply_filters(queryset, request.data)
            details_data = WhatsAppDetailsRecordSerializer(queryset, many=True).data

            if not details_data:
                return Response({"message": f"No records found for seq_id: {seq_id}"},
                                status=status.HTTP_404_NOT_FOUND)

            # ── 3. Process records ────────────────────────────────────────────
            processed         = self.data_processor.process_records(details_data, crime_name)
            message_summaries = processed["message_summaries"]
            call_summaries    = processed["call_summaries"]
            all_summaries     = message_summaries + call_summaries

            # ── 4. Info data ──────────────────────────────────────────────────
            try:
                info_data = fetch_info_data(seq_id)
            except Exception as e:
                print(f"[wa_summary] info_data error: {e}")
                info_data = {"target profile": [], "groups info": [], "contacts info": []}

            contacts_info = info_data.get("contacts info", [])
            groups_info   = info_data.get("groups info",   [])
            target_profile= info_data.get("target profile", [{}])
            tp            = target_profile[0] if target_profile else {}

            # ── 5. Advanced summary ───────────────────────────────────────────
            adv_summary = generate_advanced_summary(
                all_summaries,
                contacts_info=contacts_info,
                top_n=top_n,
            )

            # ── Build report dict (GRS-style numbered sections) ───────────────
            report = {}

            # ── SECTION 1: WhatsApp Number Info ───────────────────────────────
            sym_count  = sum(1 for c in contacts_info if c.get("symmetric_contacts"))
            asym_count = sum(1 for c in contacts_info if c.get("asymmetric_contacts"))
            owned_groups = sum(
                1 for g in groups_info
                if str(g.get("IsAdmin", "")).lower() in ("true", "1", "yes", "admin")
            )
            part_groups  = len(groups_info) - owned_groups
            device_usage = adv_summary.get("Device Usage", {})

            report["1. WhatsApp Number Info"] = [
                {"Information": "Case Name",                   "Value": crime_name or ""},
                {"Information": "State / Region",              "Value": circle},
                {"Information": "Service Provider",            "Value": operator},
                {"Information": "Date & Time Of Import",       "Value": ""},
                {"Information": "Start Period",                "Value": formatted_date or ""},
                {"Information": "End Period",                  "Value": ""},
                {"Information": "Target Number(s)",            "Value": target or ""},
                {"Information": "Name",                        "Value": tp.get("Name", "")},
                {"Information": "Address",                     "Value": tp.get("Address", "")},
                {"Information": "DOA",                         "Value": tp.get("DOA", "")},
                {"Information": "Total Symmetric Contacts",   "Value": sym_count},
                {"Information": "Total Asymmetric Contacts",  "Value": asym_count},
                {"Information": "Owned Groups",               "Value": owned_groups},
                {"Information": "Participated Groups",        "Value": part_groups},
                {"Information": "Device Type",
                 "Value": ", ".join(list(device_usage.keys())[:3])},
            ]

            # ── SECTION 2: Communication Summary ─────────────────────────────
            cdr_sum = {}
            for rec in all_summaries:
                ct = rec.get("Call Type", "")
                cdr_sum[ct] = cdr_sum.get(ct, 0) + 1

            comm_summary = []
            for ctype in ("Call In", "Call Out", "Msg In", "Msg Out"):
                dur = sum(
                    int(rec.get("Duration", 0) or 0)
                    for rec in all_summaries
                    if rec.get("Call Type") == ctype
                )
                comm_summary.append({
                    "Call / Message Type":    ctype,
                    "Number of Records":      cdr_sum.get(ctype, 0),
                    "Total Duration (s)":     dur,
                    "Remark":                 "",
                })
            report["2. Communication Summary"] = comm_summary

            # ── SECTION 3: Other Party Frequency Wise ─────────────────────────
            part_freq = defaultdict(lambda: {"count": 0, "starts": [], "ends": []})
            for rec in all_summaries:
                p = str(rec.get("Participant", "") or "").strip()
                if p and p not in ("N/A", ""):
                    part_freq[p]["count"] += 1
                    if rec.get("Datetime Start IST"):
                        part_freq[p]["starts"].append(_fmt_dt(rec["Datetime Start IST"]))
                    if rec.get("Datetime End IST"):
                        part_freq[p]["ends"].append(_fmt_dt(rec["Datetime End IST"]))

            report["3. Other Party Frequency Wise"] = [
                {
                    "Other Party": num,
                    "Name":        "",
                    "Frequency":   data["count"],
                    "From Date":   min(data["starts"]) if data["starts"] else "",
                    "To Date":     max(data["ends"])   if data["ends"]   else "",
                    "Remark":      "",
                }
                for num, data in sorted(
                    part_freq.items(), key=lambda x: x[1]["count"], reverse=True
                )[:top_n]
            ]

            # ── SECTION 4: Other Party Duration Wise (Calls) ──────────────────
            dur_map = defaultdict(lambda: {"count": 0, "dur": 0, "starts": [], "ends": []})
            for rec in all_summaries:
                if "Call" not in str(rec.get("Call Type", "") or ""):
                    continue
                p = str(rec.get("Participant", "") or "").strip()
                if p and p not in ("N/A", ""):
                    dur_map[p]["count"] += 1
                    dur_map[p]["dur"]   += int(rec.get("Duration", 0) or 0)
                    if rec.get("Datetime Start IST"):
                        dur_map[p]["starts"].append(_fmt_dt(rec["Datetime Start IST"]))
                    if rec.get("Datetime End IST"):
                        dur_map[p]["ends"].append(_fmt_dt(rec["Datetime End IST"]))

            report["4. Other Party Duration Wise"] = [
                {
                    "Other Party":       num,
                    "Name":              "",
                    "Frequency":         data["count"],
                    "Total Duration (s)":data["dur"],
                    "From Date":         min(data["starts"]) if data["starts"] else "",
                    "To Date":           max(data["ends"])   if data["ends"]   else "",
                    "Remark":            "",
                }
                for num, data in sorted(
                    dur_map.items(), key=lambda x: x[1]["dur"], reverse=True
                )[:top_n]
            ]

            # ── SECTION 5: Top Message Types ──────────────────────────────────
            msg_type_freq = defaultdict(int)
            for rec in all_summaries:
                rtype = str(rec.get("record_type", "") or "").lower()
                for label in ("text", "image", "video", "document", "url"):
                    if label in rtype:
                        msg_type_freq[label.capitalize()] += 1

            report["5. Top Message Types"] = [
                {"Type": label, "Frequency": msg_type_freq.get(label, 0), "Remark": ""}
                for label in ["Text", "Image", "Video", "Document", "Url"]
            ]

            # ── SECTION 6: Top Call Types ─────────────────────────────────────
            call_type_freq = defaultdict(int)
            for rec in all_summaries:
                ct    = str(rec.get("Call Type", "") or "")
                rtype = str(rec.get("record_type", "") or "").lower()
                if "Call" in ct:
                    if "video" in rtype and "|" in str(rec.get("Participant", "")):
                        call_type_freq["Group Video"] += 1
                    elif "video" in rtype:
                        call_type_freq["Video"] += 1
                    elif "|" in str(rec.get("Participant", "")):
                        call_type_freq["Group Audio"] += 1
                    else:
                        call_type_freq["Audio"] += 1

            report["6. Top Call Types"] = [
                {"Type": label, "Frequency": call_type_freq.get(label, 0), "Remark": ""}
                for label in ["Audio", "Video", "Group Audio", "Group Video"]
            ]

            # ── SECTION 7: Group Activity Summary ────────────────────────────
            grp_act = adv_summary.get("Group Activity", {})
            report["7. Group Activity Summary"] = {
                "Total Groups":        len(groups_info),
                "Owned Groups":        owned_groups,
                "Participated Groups": part_groups,
                **grp_act,
            }

            # ── SECTION 8: Top 5 Groups Frequency Wise ───────────────────────
            group_freq = defaultdict(lambda: {"count": 0, "starts": [], "ends": []})
            for rec in all_summaries:
                gid = rec.get("Group ID", "")
                if gid and gid not in ("N/A", ""):
                    group_freq[gid]["count"] += 1
                    if rec.get("Datetime Start IST"):
                        group_freq[gid]["starts"].append(_fmt_dt(rec["Datetime Start IST"]))
                    if rec.get("Datetime End IST"):
                        group_freq[gid]["ends"].append(_fmt_dt(rec["Datetime End IST"]))

            gi_map      = {g.get("GroupID", ""): g for g in groups_info}
            top_groups  = sorted(
                group_freq.items(), key=lambda x: x[1]["count"], reverse=True
            )[:5]

            report["8. Top 5 Groups Frequency Wise"] = [
                {
                    "Group ID":      gid,
                    "Group Name":    gi_map.get(gid, {}).get("GroupName", ""),
                    "Creation Date": _fmt_dt(gi_map.get(gid, {}).get("CreationDate", "")),
                    "Total Members": gi_map.get(gid, {}).get("MemberCount", ""),
                    "Frequency":     data["count"],
                    "From Date":     min(data["starts"]) if data["starts"] else "",
                    "To Date":       max(data["ends"])   if data["ends"]   else "",
                    "Remark":        "",
                }
                for gid, data in top_groups
            ]

            # ── SECTION 9: IP Connections Country Wise ────────────────────────
            report["9. IP Connections Country Wise"] = [
                {"Country": country, "Count": cnt, "Remark": ""}
                for country, cnt in adv_summary.get("IP Connections", {}).items()
            ]

            # ── SECTION 10: Device Usage ──────────────────────────────────────
            report["10. Device Usage"] = adv_summary.get("Device Usage", {})

            # ── SECTION 11: Time Usage ────────────────────────────────────────
            report["11. Time Usage"] = adv_summary.get("Time Usage", {})

            # ── SECTION 12: Alerts & Flags ────────────────────────────────────
            report["12. Alerts & Flags"] = adv_summary.get("Alerts & Flags", [])

            # ── SECTION 13: Final Observation ─────────────────────────────────
            report["13. Final Observation"] = adv_summary.get("Final Observation", "")

            # ── SECTION 14: App Version (per device) ──────────────────────────
            # Each device key in device_usage may carry an app-version string.
            # We also fall back to target-profile fields when present.
            # app_version_rows = []
            # for device_key, device_val in device_usage.items():
            #     # device_val might be a dict (with "AppVersion") or a plain string
            #     if isinstance(device_val, dict):
            #         app_ver = device_val.get("AppVersion") or device_val.get("app_version", "")
            #     else:
            #         app_ver = str(device_val or "")
            #     app_version_rows.append({
            #         "Device":      device_key,
            #         "App Version": app_ver,
            #     })
            # # Fallback: single row from target profile if no device_usage entries
            # if not app_version_rows:
            #     app_version_rows.append({
            #         "Device":      tp.get("DeviceModel", ""),
            #         "App Version": tp.get("AppVersion", ""),
            #     })
            # report["14. App Version"] = app_version_rows

            # ── SECTION 15: Device OS Build Number (per device) ───────────────
            # os_build_rows = []
            # for device_key, device_val in device_usage.items():
            #     if isinstance(device_val, dict):
            #         os_ver   = device_val.get("OSVersion") or device_val.get("os_version", "")
            #         os_build = device_val.get("OSBuild")   or device_val.get("os_build",   "")
            #         model    = device_val.get("Model")     or device_val.get("model",       "")
            #     else:
            #         os_ver   = ""
            #         os_build = ""
            #         model    = str(device_val or "")
            #     os_build_rows.append({
            #         "Device":           device_key,
            #         "OS Version":       os_ver,
            #         "OS Build Number":  os_build,
            #         "Model":            model,
            #     })
            # if not os_build_rows:
            #     os_build_rows.append({
            #         "Device":           tp.get("DeviceModel", ""),
            #         "OS Version":       tp.get("OSVersion",   ""),
            #         "OS Build Number":  tp.get("OSBuild",     ""),
            #         "Model":            tp.get("DeviceModel", ""),
            #     })
            # report["15. Device OS Build Number"] = os_build_rows

            # ── SECTION 16: Top 5 Other Party — Individual Type ───────────────
            # Individual = participant does NOT contain "|" and record has no Group ID
            indiv_freq = defaultdict(lambda: {"count": 0, "starts": [], "ends": []})
            for rec in all_summaries:
                p   = str(rec.get("Participant", "") or "").strip()
                gid = str(rec.get("Group ID",    "") or "").strip()
                if p and p not in ("N/A", "") and "|" not in p and not gid:
                    indiv_freq[p]["count"] += 1
                    if rec.get("Datetime Start IST"):
                        indiv_freq[p]["starts"].append(_fmt_dt(rec["Datetime Start IST"]))
                    if rec.get("Datetime End IST"):
                        indiv_freq[p]["ends"].append(_fmt_dt(rec["Datetime End IST"]))

            report["16. Top 5 Other Party - Individual Type"] = [
                {
                    "Other Party": num,
                    "Name":        "",
                    "Frequency":   data["count"],
                    "From Date":   min(data["starts"]) if data["starts"] else "",
                    "To Date":     max(data["ends"])   if data["ends"]   else "",
                    "Remark":      "",
                }
                for num, data in sorted(
                    indiv_freq.items(), key=lambda x: x[1]["count"], reverse=True
                )[:5]
            ]

            # ── SECTION 17: Top 5 Other Party — Group Type ────────────────────
            # Group = record has a non-empty Group ID
            grp_party_freq = defaultdict(lambda: {"count": 0, "starts": [], "ends": [], "gid": ""})
            for rec in all_summaries:
                p   = str(rec.get("Participant", "") or "").strip()
                gid = str(rec.get("Group ID",    "") or "").strip()
                if p and p not in ("N/A", "") and gid and gid not in ("N/A", ""):
                    grp_party_freq[p]["count"] += 1
                    grp_party_freq[p]["gid"]    = gid
                    if rec.get("Datetime Start IST"):
                        grp_party_freq[p]["starts"].append(_fmt_dt(rec["Datetime Start IST"]))
                    if rec.get("Datetime End IST"):
                        grp_party_freq[p]["ends"].append(_fmt_dt(rec["Datetime End IST"]))

            report["17. Top 5 Other Party - Group Type"] = [
                {
                    "Other Party":   num,
                    "Name":          "",
                    "Group ID":      data["gid"],
                    "Group Name":    gi_map.get(data["gid"], {}).get("GroupName",   ""),
                    "Creation Date": _fmt_dt(gi_map.get(data["gid"], {}).get("CreationDate", "")),
                    "Total Members": gi_map.get(data["gid"], {}).get("MemberCount", ""),
                    "Frequency":     data["count"],
                    "From Date":     min(data["starts"]) if data["starts"] else "",
                    "To Date":       max(data["ends"])   if data["ends"]   else "",
                    "Remark":        "",
                }
                for num, data in sorted(
                    grp_party_freq.items(), key=lambda x: x[1]["count"], reverse=True
                )[:5]
            ]

            # ── SECTION 18: Top 5 Group — Member Wise ────────────────────────
            # Helper to build sorted group rows
            def _group_rows(group_list):
                def _safe_int(v):
                    try:
                        return int(v or 0)
                    except (TypeError, ValueError):
                        return 0
                return [
                    {
                        "Group ID":      g.get("GroupID",      ""),
                        "Group Name":    g.get("GroupName",    ""),
                        "Creation Date": _fmt_dt(g.get("CreationDate", "")),
                        "Total Members": g.get("MemberCount",  ""),
                        "Is Admin":      g.get("IsAdmin",      ""),
                        "Remark":        "",
                    }
                    for g in sorted(
                        group_list,
                        key=lambda x: _safe_int(x.get("MemberCount")),
                        reverse=True,
                    )[:5]
                ]

            owned_group_list = [
                g for g in groups_info
                if str(g.get("IsAdmin", "")).lower() in ("true", "1", "yes", "admin")
            ]
            participated_group_list = [
                g for g in groups_info
                if str(g.get("IsAdmin", "")).lower() not in ("true", "1", "yes", "admin")
            ]

            report["18. Top 5 Group - Member Wise"] = {
                "All Groups":          _group_rows(groups_info),
                "Owned Groups":        _group_rows(owned_group_list),
                "Participated Groups": _group_rows(participated_group_list),
            }

            # ── SECTION 19: Top 5 Latest Creation Group Name ──────────────────
            def _parse_creation_date(g):
                raw = g.get("CreationDate")
                if isinstance(raw, datetime):
                    return raw
                if raw:
                    try:
                        s = str(raw).replace("Z", "").replace("+00:00", "").strip()
                        return datetime.fromisoformat(s)
                    except Exception:
                        pass
                return datetime.min

            report["19. Top 5 Latest Creation Group Name"] = [
                {
                    "Group Name":    g.get("GroupName",   ""),
                    "Group ID":      g.get("GroupID",     ""),
                    "Creation Date": _fmt_dt(g.get("CreationDate", "")),
                    "Total Members": g.get("MemberCount", ""),
                    "Is Admin":      g.get("IsAdmin",     ""),
                    "Remark":        "",
                }
                for g in sorted(groups_info, key=_parse_creation_date, reverse=True)[:5]
            ]

            return Response(report, status=status.HTTP_200_OK)

        except ValueError as exc:
            return Response({"error": f"Invalid parameter: {exc}"},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            import traceback
            print(traceback.format_exc())
            return Response({"error": f"Failed to generate report: {exc}"},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # ── Date / field filter ───────────────────────────────────────────────────
    def _apply_filters(self, queryset, filters: dict):
        def parse_dt(dt_str, is_end=False):
            if not dt_str:
                return None
            dt_str = (str(dt_str)
                      .replace("Z", "").replace("+00:00", "").replace(" IST", "").strip())
            try:
                if "T" in dt_str:
                    dt_obj = datetime.fromisoformat(dt_str.split(".")[0])
                elif len(dt_str) == 10:
                    dt_obj = datetime.strptime(dt_str, "%Y-%m-%d")
                    if is_end:
                        dt_obj = dt_obj.replace(hour=23, minute=59, second=59)
                elif " " in dt_str:
                    dt_obj = datetime.strptime(dt_str.split(".")[0], "%Y-%m-%d %H:%M:%S")
                else:
                    return None
                if dt_obj.tzinfo is None:
                    dt_obj = pytz.UTC.localize(dt_obj)
                return dt_obj
            except Exception:
                return None

        from_dt = parse_dt(filters.get("from_date"), is_end=False)
        to_dt   = parse_dt(filters.get("to_date"),   is_end=True)

        if from_dt and to_dt:
            queryset = queryset.filter(__raw__={"$or": [
                {"DateTimeIST": {"$gte": from_dt, "$lte": to_dt}},
                {"DateTimeUTC": {"$gte": from_dt, "$lte": to_dt}},
            ]})
        elif from_dt:
            queryset = queryset.filter(__raw__={"$or": [
                {"DateTimeIST": {"$gte": from_dt}},
                {"DateTimeUTC": {"$gte": from_dt}},
            ]})
        elif to_dt:
            queryset = queryset.filter(__raw__={"$or": [
                {"DateTimeIST": {"$lte": to_dt}},
                {"DateTimeUTC": {"$lte": to_dt}},
            ]})

        field_map = {
            "target"     : "Target",
            "participant": "Participant__icontains",
            "call_type"  : "Call_Type",
            "status"     : "Status",
            "record_type": "Type",
            "min_size"   : "Size__gte",
            "max_size"   : "Size__lte",
        }
        for param, db_field in field_map.items():
            value = filters.get(param)
            if value not in (None, "", "null", "None"):
                queryset = queryset.filter(**{db_field: value})

        return queryset