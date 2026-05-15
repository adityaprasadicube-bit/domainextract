import json
from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.conf import settings

from ..ai_agent.cdranalyzer import analyze, report_top_contacts, report_tower_timeline, \
    report_imei_history, get_client

MONGO_URI = getattr(settings, "MONGO_URI", "mongodb://localhost:27017/")


@method_decorator(csrf_exempt, name="dispatch")
class CDRAnalyzeView(View):
    """
    POST /api/cdr/analyze/
    Body: { "query": "calls of target 9942131915 today", "limit": 1000, "raw_mode": false }
    """

    def post(self, request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"status": "error", "message": "Invalid JSON body."}, status=400)

        query = body.get("query", "").strip()
        if not query:
            return JsonResponse({"status": "error", "message": "'query' field is required."}, status=400)

        limit    = int(body.get("limit", 1000))
        raw_mode = bool(body.get("raw_mode", False))

        result = analyze(query, mongo_uri=MONGO_URI, raw_mode=raw_mode, limit=limit)

        status_code = 200 if result.get("status") in ("success", "empty") else 500
        return JsonResponse(result, status=status_code, safe=False)


@method_decorator(csrf_exempt, name="dispatch")
class CDRReportView(View):
    """
    POST /api/cdr/report/
    Body: { "type": "contacts"|"towers"|"imei", "target": "...", "days": 30, "date": "2024-03-01" }
    """

    REPORT_TYPES = ("contacts", "towers", "imei")

    def post(self, request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"status": "error", "message": "Invalid JSON body."}, status=400)

        report_type = body.get("type", "").strip()
        if report_type not in self.REPORT_TYPES:
            return JsonResponse(
                {"status": "error", "message": f"'type' must be one of: {self.REPORT_TYPES}"},
                status=400,
            )

        target = body.get("target", "").strip()
        if not target:
            return JsonResponse({"status": "error", "message": "'target' is required."}, status=400)

        try:
            if report_type == "contacts":
                days   = int(body.get("days", 30))
                result = report_top_contacts(target, days, MONGO_URI)

            elif report_type == "towers":
                date = body.get("date", "").strip()
                if not date:
                    return JsonResponse(
                        {"status": "error", "message": "'date' is required for tower report (YYYY-MM-DD)."},
                        status=400,
                    )
                result = report_tower_timeline(target, date, MONGO_URI)

            elif report_type == "imei":
                days   = int(body.get("days", 30))
                result = report_imei_history(target, days, MONGO_URI)

        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=500)

        return JsonResponse(result, safe=False)


class CDRHealthView(View):
    """
    GET /api/cdr/health/
    Ping MongoDB and return connection status.
    """

    def get(self, request):
        # from .cdr_analyzer import get_client
        try:
            client = get_client(MONGO_URI)
            client.admin.command("ping")
            col    = client["CDR"]["CallDetailRecords"]
            count  = col.estimated_document_count()
            return JsonResponse({
                "status":          "ok",
                "mongo":           "connected",
                "collection":      "CDR.CallDetailRecords",
                "estimated_docs":  count,
            })
        except Exception as e:
            return JsonResponse({"status": "error", "mongo": "unreachable", "message": str(e)}, status=503)