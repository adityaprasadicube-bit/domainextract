import ipaddress
from collections import defaultdict

from mongoengine import get_db
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.utils.dateparse import parse_datetime
from ..ipdr_models.ip_model import IPDRNexus, PortInfo
from ..ip_serializers import IPDRNexusSerializer, PortInfoSerializer
from ...models import MccMnc
from ...searchengine import search_ip
from ...serializers import MccMncSerializer


class CircleWiseApiView(APIView):
    def _parse_dt(self, val):
        try:
            return parse_datetime(val)
        except:
            return None

    def _normalize_seq_id(self, raw_seq_id):
        """
        Convert seq_id to string, handling cases where it might be:
        - A string: "322f0618ccc94731"
        - A list: ["322f0618ccc94731"]
        - None
        """
        if raw_seq_id is None:
            return None

        # If it's a list, get the first element
        if isinstance(raw_seq_id, list):
            if len(raw_seq_id) > 0:
                return str(raw_seq_id[0])
            return None

        # Otherwise convert to string
        return str(raw_seq_id)

    def post(self, request):
        try:
            print(f"\n{'=' * 60}")
            print(f"CIRCLE WISE REQUEST:")
            print(f"{'=' * 60}")
            print(f"Request body: {request.data}")
            print(f"{'=' * 60}\n")

            seq_ids = request.data.get("seq_ids", [])
            from_date = self._parse_dt(request.data.get("from_date"))
            to_date = self._parse_dt(request.data.get("to_date"))

            # Pagination parameters
            page = int(request.data.get("page", 1))
            page_size = int(request.data.get("page_size", 500))

            if not seq_ids or not from_date or not to_date:
                return Response(
                    {"error": "seq_ids, from_date, to_date are required"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if not isinstance(seq_ids, list):
                return Response(
                    {"error": "seq_ids must be a list"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            print(f"📋 Received seq_ids: {seq_ids}")
            print(f"📅 Date range: {from_date} to {to_date}")

            db = get_db("ipdr_db")
            collections = db["IPDetailRecords"]

            # MongoDB aggregation pipeline for IPDR records
            pipeline = [
                {
                    "$match": {
                        "seq_id": {"$in": seq_ids},
                        "SDateTime": {"$gte": from_date, "$lte": to_date}
                    }
                },
                {"$sort": {"SDateTime": 1}}
            ]

            # Fetch nexus data
            collections_nex = db["IPdrNexus"]
            pipeline_nex = [
                {"$match": {"_id": {"$in": seq_ids}}}
            ]

            nexusdata = list(collections_nex.aggregate(pipeline_nex))

            if not nexusdata:
                return Response(
                    {"error": "No Nexus data found for given seq_ids"},
                    status=404
                )

            print(f"✅ Found {len(nexusdata)} nexus records")

            # Fetch IPDR records
            data = list(collections.aggregate(pipeline))

            if not data:
                return Response(
                    {"error": "No IPDR records found for given date range"},
                    status=404
                )

            print(f"📊 Found {len(data)} IPDR records")

            # If RecordType is Mobile, add MSISDN from nexus
            if nexusdata[0].get("RecordType") == "Mobile":
                for record in data:
                    record["MSISDN"] = nexusdata[0].get("IPDR")

            # Extract unique IMSI MCC MNC codes
            imsi_mcc = set()
            for record in data:
                mcc = record.get("IMSI_CODE")
                if mcc:
                    imsi_mcc.add(str(mcc))

            print(f"🔍 Found {len(imsi_mcc)} unique IMSI MCC codes")

            # Lookup IMSI details
            lookupimsi = {}
            if imsi_mcc:
                try:
                    imsi_mcc_docs = MccMnc.objects.filter(
                        mccmnc_temp__in=list(imsi_mcc)
                    )
                    lookupimsi = {
                        i.mccmnc_temp: MccMncSerializer(i).data
                        for i in imsi_mcc_docs
                    }
                    print(f"✅ Loaded {len(lookupimsi)} IMSI lookups")
                except Exception as e:
                    print(f"⚠️ Failed to fetch IMSI details: {str(e)}")
                    return Response(
                        {"error": "Failed to fetch IMSI details", "details": str(e)},
                        status=500
                    )

            # Group data by IMSI
            grouped_data = defaultdict(
                lambda: {"records": [], "from_date": None, "to_date": None}
            )

            for row in data:
                imsi = row.get("IMSI")
                if imsi:
                    grouped_data[imsi]["records"].append(row)

            if not grouped_data:
                return Response(
                    {"error": "No valid IMSI data available"},
                    status=404
                )

            print(f"📦 Grouped into {len(grouped_data)} unique IMSIs")

            # Build report
            report = []
            for key, value in grouped_data.items():
                imsi = key
                imsi_mccmnc = (
                    value["records"][0].get("IMSI_CODE")
                    if value["records"]
                    else None
                )

                imsi_mcc_str = str(imsi_mccmnc) if imsi_mccmnc else None
                imsi_details = lookupimsi.get(imsi_mcc_str, {})

                first_record = (
                    value["records"][0] if value["records"] else {}
                )

                report.append({
                    "MSISDN": first_record.get("MSISDN"),
                    "IMSI": imsi,
                    "IMSI MCC MNC": imsi_mccmnc,
                    "IMSI Circle": imsi_details.get("circle"),
                    "IMSI Operator": imsi_details.get("operator")
                })

            # Apply pagination
            total_records = len(report)
            start_index = (page - 1) * page_size
            end_index = start_index + page_size

            if start_index >= total_records:
                return Response(
                    {"error": "Page number out of range"},
                    status=400
                )

            paginated_report = report[start_index:end_index]

            print(f"\n{'=' * 60}")
            print(f"RESULTS:")
            print(f"{'=' * 60}")
            print(f"Total records: {total_records}")
            print(f"Page: {page}/{(total_records + page_size - 1) // page_size}")
            print(f"Returning: {len(paginated_report)} records")
            print(f"{'=' * 60}\n")

            return Response({
                "TotalRecords": total_records,
                "Page": page,
                "PageSize": page_size,
                "TotalPages": (total_records + page_size - 1) // page_size,
                "data": paginated_report
            }, status=200)

        except ValueError as e:
            print(f"❌ ValueError: {str(e)}")
            return Response(
                {
                    "error": "Invalid value",
                    "details": str(e)
                },
                status=400
            )
        except Exception as e:
            import traceback
            print(f"\n{'!' * 60}")
            print(f"CRITICAL ERROR:")
            print(f"{'!' * 60}")
            print(traceback.format_exc())
            print(f"{'!' * 60}\n")
            return Response(
                {
                    "error": "Internal server error",
                    "details": str(e)
                },
                status=500
            )