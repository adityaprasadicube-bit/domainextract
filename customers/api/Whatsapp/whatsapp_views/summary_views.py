"""
wp_adv_summary_view.py
──────────────────────
Standalone API endpoint for WhatsApp Advanced Activity Summary.

Endpoint : POST /api/whatsapp/advanced-summary/
Request  : { "seq_id": "...", "from_date": "...", "to_date": "..." }
Response : {
    "seq_id"           : "...",
    "crime_name"       : "...",
    "total_records"    : 123,
    "Activity Summary" : { ... },
    "Analysis Summary" : { ... },
    "Top Contacts"     : { ... },
    "IP Connections"   : { ... },
    "Device Usage"     : { ... },
    "Time Usage"       : { ... },
}
"""

import pytz
from datetime import datetime


from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from ..whatsapp_models.serializers import (
    WhatsAppDetailsRecordSerializer,
    WhatsappNexusSerializer,
)
from ..whatsapp_models.whatsapp_models import (
    WhatsAppDetailsRecord,
    WhatsAppNexus,
)

# Re-use processor + IP service from the main views file


# fetch_info_data brings in contacts (symmetric / asymmetric)
from .whatsapp_views import fetch_info_data, IPLookupService, WhatsAppDataProcessor

# Advanced summary generator
from .wp_summary import generate_advanced_summary


# ─────────────────────────────────────────────────────────────────────────────
#  SWAGGER SCHEMAS
# ─────────────────────────────────────────────────────────────────────────────

_REQUEST_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    required=["seq_id"],
    properties={
        "seq_id":      openapi.Schema(type=openapi.TYPE_STRING,  description="Nexus / sequence ID"),
        "from_date":   openapi.Schema(type=openapi.TYPE_STRING,  description="Filter start (ISO 8601)", example="2024-01-01"),
        "to_date":     openapi.Schema(type=openapi.TYPE_STRING,  description="Filter end   (ISO 8601)", example="2024-12-31"),
        "call_type":   openapi.Schema(type=openapi.TYPE_STRING,  description="Filter by Call_Type"),
        "target":      openapi.Schema(type=openapi.TYPE_STRING,  description="Filter by target number"),
        "participant": openapi.Schema(type=openapi.TYPE_STRING,  description="Partial match on participant"),
        "record_type": openapi.Schema(type=openapi.TYPE_STRING,  description="Filter by record type"),
        "status":      openapi.Schema(type=openapi.TYPE_STRING,  description="Filter by status"),
        "top_n":       openapi.Schema(type=openapi.TYPE_INTEGER, description="Ranked-list size (default 10)", example=10),
    },
)


# ─────────────────────────────────────────────────────────────────────────────
#  VIEW
# ─────────────────────────────────────────────────────────────────────────────

class WhatsAppAdvancedSummaryView(APIView):
    """
    POST /api/whatsapp/advanced-summary/

    Returns an advanced activity summary including contact analysis
    (saved / active / unknown / inactive) for a given seq_id.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.ip_lookup_service = IPLookupService()
        self.data_processor    = WhatsAppDataProcessor(self.ip_lookup_service)

    @swagger_auto_schema(
        operation_summary="WhatsApp Advanced Activity Summary",
        operation_description=(
            "Returns a structured advanced summary including Activity Summary, "
            "Analysis Summary (saved/active/unknown/inactive contacts), Top Contacts, "
            "IP Connections, Device Usage, and Time Usage."
        ),
        request_body=_REQUEST_SCHEMA,
        responses={
            200: openapi.Response(description="Advanced summary data"),
            400: openapi.Response(description="Missing or invalid seq_id"),
            404: openapi.Response(description="No records found"),
            500: openapi.Response(description="Internal server error"),
        },
        tags=["WhatsApp"],
    )
    def post(self, request):
        try:
            # ── 1. Validate ───────────────────────────────────────────────
            seq_id = request.data.get("seq_id")
            if not seq_id:
                return Response(
                    {"error": "seq_id is required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            top_n = int(request.data.get("top_n", 10))

            # ── 2. Nexus (crime name) ─────────────────────────────────────
            crime_name = None
            target = None
            formatted_date = None

            try:
                nexus_obj = WhatsAppNexus.objects.get(_id=seq_id)
                data = WhatsappNexusSerializer(nexus_obj).data

                crime_name = data.get("CrimeName")
                target = data.get("Target")

                raw_date = data.get("ToDateIST")  # FIXED (removed space)

                if raw_date:
                    # Parse ISO format date
                    dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                    formatted_date = dt.strftime("%d-%m-%Y")
            except WhatsAppNexus.DoesNotExist:
                pass

            # ── 3. CDR records ────────────────────────────────────────────
            queryset     = WhatsAppDetailsRecord.objects.filter(seq_id=seq_id)
            queryset     = self._apply_filters(queryset, request.data)
            details_data = WhatsAppDetailsRecordSerializer(queryset, many=True).data

            if not details_data:
                return Response(
                    {"message": f"No records found for seq_id: {seq_id}"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # ── 4. Process records (IP lookup + participant enrichment) ───
            processed     = self.data_processor.process_records(details_data, crime_name)
            all_summaries = processed["message_summaries"] + processed["call_summaries"]

            # ── 5. Contacts info (symmetric / asymmetric) ─────────────────
            contacts_info = []
            try:
                info_data     = fetch_info_data(seq_id)
                contacts_info = info_data.get("contacts info", [])
            except Exception as e:
                print(f"⚠️  contacts_info fetch error: {e}")

            # ── 6. Generate summary ───────────────────────────────────────
            adv_summary = generate_advanced_summary(
                all_summaries,
                contacts_info=contacts_info,
                top_n=top_n,
            )

            # ── 7. Response ───────────────────────────────────────────────
            return Response(
                {
                    "seq_id"       : seq_id,
                    "crime_name"   : crime_name,
                    "target" : target,
                    "date" : formatted_date,
                    "total_records": len(all_summaries),
                    "data" : adv_summary,
                    #**adv_summary,
                },
                status=status.HTTP_200_OK,
            )

        except ValueError as exc:
            return Response(
                {"error": f"Invalid parameter: {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            return Response(
                {"error": f"Failed to generate advanced summary: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ── Filter helper ─────────────────────────────────────────────────────────
    def _apply_filters(self, queryset, filters: dict):
        def parse_dt(dt_str, is_end=False):
            if not dt_str:
                return None
            dt_str = (
                str(dt_str)
                .replace("Z", "").replace("+00:00", "").replace(" IST", "").strip()
            )
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