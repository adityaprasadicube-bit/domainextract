import ipaddress
from collections import defaultdict

from mongoengine import get_db
from rest_framework.views import APIView
from rest_framework.response import Response
from django.utils.dateparse import parse_datetime

from .ipdr_models.ip_model import IPDRNexus, PortInfo
from .ip_serializers import IPDRNexusSerializer, PortInfoSerializer
from ..models import ImeiDetails
from ..searchengine import search_ip
from ..serializers import DeviceInfoSerializer


class MobileNumber(APIView):

    def _parse_dt(self, val):
        try:
            return parse_datetime(val)
        except:
            return None

    def post(self, request):

        seq_id = request.data.get("seq_id")
        from_date = self._parse_dt(request.data.get("from_date"))
        to_date = self._parse_dt(request.data.get("to_date"))
        filter_type = request.data.get("filter_type", "imei")

        if not seq_id or not from_date or not to_date:
            return Response({"error": "seq_id, from_date, to_date are required"}, status=400)

        db = get_db("ipdr_db")
        collection = db["IPDetailRecords"]

        # Fetch IPDR Nexus
        nexus_collection = db["IPdrNexus"]
        nexus_data = list(nexus_collection.aggregate([
            {"$match": {"_id": seq_id}}
        ]))

        ipdr_val = None
        for row in nexus_data:
            ipdr_val = row.get("IPDR")

        # Main pipeline
        pipeline = [
            {"$match": {"seq_id": seq_id, "SDateTime": {"$gte": from_date, "$lte": to_date}}},
            {"$sort": {"SDateTime": 1}}
        ]

        data = list(collection.aggregate(pipeline))

        # Collect IMEI_TAC for device info lookup
        imei_tac_set = set()
        for record in data:
            tac = record.get("IMEI_TAC")
            if tac:
                try:
                    imei_tac_set.add(int(tac))
                except:
                    pass

        # Lookup IMEI TAC → manufacturer, type, etc.
        lookupimei = {}
        if imei_tac_set:
            try:
                imei_docs = ImeiDetails.objects.filter(id__in=list(imei_tac_set))
                lookupimei = {i.id: DeviceInfoSerializer(i).data for i in imei_docs}
            except Exception as e:
                print(f"Error fetching IMEI details: {e}")

        # GROUPING
        grouped_data = defaultdict(lambda: {"records": []})
        session_counts = {}

        for row in data:
            mobilenumber = row.get("MSISDN")
            imei = row.get("IMEI")
            imsi = row.get("IMSI")

            # ------------------------- FIXED GROUPING LOGIC ---------------------------- #
            if filter_type == "imei":
                # GROUP BY MOBILE NUMBER + IMEI + IMSI
                key = (mobilenumber, imei, imsi)

            elif filter_type == "imsi":
                key = imsi

            else:
                continue
            # ---------------------------------------------------------------------------- #

            if key:
                session_counts[key] = session_counts.get(key, 0) + 1
                grouped_data[key]["records"].append(row)

        # REPORT GENERATION
        report = []

        if filter_type == "imei":
            for key, value in grouped_data.items():

                mobilenumber, imei, imsi = key  # tuple unpack

                imei_tac = value["records"][0].get("IMEI_TAC")
                try:
                    imei_tac_int = int(imei_tac) if imei_tac else None
                except:
                    imei_tac_int = None

                imei_details = lookupimei.get(imei_tac_int, {})

                report.append({
                    "MobileNumber": mobilenumber,
                    "Registered User":None,
                    "Watchlist Name":None,
                    "TSP/ISP":None,
                    "IMEI": imei,
                    "IMSI": imsi,
                    "IMEIMANUFACTURER": imei_details.get("manufacturer"),
                    "DeviceType": imei_details.get("devicetype"),
                    "TotalSessions": session_counts.get(key, 0)
                })


        elif filter_type == "imsi":
            for key, value in grouped_data.items():
                imsi = key
                report.append({
                    "IPDR": ipdr_val,
                    "IMSI": imsi,
                    "TotalSessions": session_counts.get(key, 0)
                })

        # Sort by sessions
        sorted_data = sorted(report, key=lambda x: x['TotalSessions'], reverse=True)

        return Response({
            "Report": sorted_data,
            "TotalRecords": len(sorted_data),
            "FilterType": filter_type
        }, status=200)
