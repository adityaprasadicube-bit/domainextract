import ipaddress
from collections import defaultdict

from mongoengine import get_db
from psycopg2.errorcodes import lookup
from requests import session
from rest_framework.views import APIView
from rest_framework.response import Response
from django.utils.dateparse import parse_datetime
from ..ipdr_models.ip_model import IPDRNexus, PortInfo
from ..ip_serializers import IPDRNexusSerializer, PortInfoSerializer
from ...models import ImeiDetails
from ...searchengine import search_ip
from ...serializers import DeviceInfoSerializer


class ImeiImsiAPIView(APIView):
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
            print(f"IMEI/IMSI REQUEST:")
            print(f"{'=' * 60}")
            print(f"Request body: {request.data}")
            print(f"{'=' * 60}\n")

            # Support both single seq_id and multiple seq_ids
            seq_id = request.data.get("seq_id")
            seq_ids = request.data.get("seq_ids", [])

            # Convert single seq_id to list format for uniform processing
            if seq_id and not seq_ids:
                seq_ids = [seq_id]
            elif not seq_ids:
                return Response({
                    "error": "Either seq_id or seq_ids is required"
                }, status=400)

            from_date = self._parse_dt(request.data.get("from_date"))
            to_date = self._parse_dt(request.data.get("to_date"))
            filter_type = request.data.get("filter_type", "imei")

            page = int(request.data.get("page", 1))
            page_size = int(request.data.get("page_size", 500))

            if not from_date or not to_date:
                return Response({
                    "error": "from_date and to_date are required"
                }, status=400)

            if not isinstance(seq_ids, list):
                return Response({
                    "error": "seq_ids must be a list"
                }, status=400)

            if filter_type not in ["imei", "imsi"]:
                return Response({
                    "error": "filter_type must be 'imei' or 'imsi'"
                }, status=400)

            print(f"📋 Received seq_ids: {seq_ids}")
            print(f"📅 Date range: {from_date} to {to_date}")
            print(f"🔍 Filter type: {filter_type}")

            db = get_db("ipdr_db")
            collection = db["IPDetailRecords"]

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

            data = list(collection.aggregate(pipeline))

            if not data:
                return Response({
                    "error": "No IPDR records found for given criteria"
                }, status=404)

            print(f"📊 Found {len(data)} IPDR records")

            # Fetch nexus data
            nexus_collection = db["IPdrNexus"]
            nexus_pipeline = [
                {"$match": {"_id": {"$in": seq_ids}}}
            ]
            nexus_data = list(nexus_collection.aggregate(nexus_pipeline))

            if not nexus_data:
                return Response({
                    "error": "No nexus data found for given seq_ids"
                }, status=404)

            print(f"✅ Found {len(nexus_data)} nexus records")

            # Extract unique IMEI_TAC values from all records
            imei_tac_set = set()
            for record in data:
                tac = record.get("IMEI_TAC")
                if tac:
                    # Convert to int if needed, as the model uses IntField for id
                    try:
                        imei_tac_set.add(int(tac))
                    except (ValueError, TypeError):
                        pass

            print(f"📱 Found {len(imei_tac_set)} unique IMEI TACs")

            # Fetch IMEI details using id__in (since id is the primary key that represents TAC)
            lookupimei = {}
            if imei_tac_set:
                try:
                    imei_docs = ImeiDetails.objects.filter(id__in=list(imei_tac_set))
                    lookupimei = {i.id: DeviceInfoSerializer(i).data for i in imei_docs}
                    print(f"✅ Loaded {len(lookupimei)} IMEI lookups")
                except Exception as e:
                    print(f"⚠️ Error fetching IMEI details: {e}")

            session_counts = {}
            ipdr_val = None

            # Get IPDR value from first nexus record
            for row1 in nexus_data:
                ipdr_val = row1.get("IPDR")
                break

            print(f"📞 IPDR value: {ipdr_val}")

            grouped_data = defaultdict(
                lambda: {"records": [], "from_date": None, "to_date": None}
            )

            print(f"🔄 Grouping records by {filter_type}...")

            for row in data:
                imei = row.get("IMEI")
                imsi = row.get("IMSI")

                if filter_type == "imei":
                    key = imei
                elif filter_type == "imsi":
                    key = imsi
                else:
                    continue

                if key:
                    if key not in session_counts:
                        session_counts[key] = 0
                    session_counts[key] += 1
                    grouped_data[key]["records"].append(row)

            print(f"📦 Grouped into {len(grouped_data)} unique {filter_type}s")

            report = []
            for key, value in grouped_data.items():
                if filter_type == "imei":
                    imei = key
                    # Extract IMEI_TAC from one of the records for this IMEI
                    imei_tac = value["records"][0].get("IMEI_TAC") if value["records"] else None

                    # Convert to int for lookup
                    try:
                        imei_tac_int = int(imei_tac) if imei_tac else None
                    except (ValueError, TypeError):
                        imei_tac_int = None

                    imei_details = lookupimei.get(imei_tac_int, {})

                    report.append({
                        "IPDR": ipdr_val,
                        "IMEI": imei,
                        "IMEIMANUFACTURER": imei_details.get("manufacturer"),
                        "DeviceType": imei_details.get("devicetype"),
                        "TotalSessions": session_counts.get(key, 0)
                    })
                elif filter_type == "imsi":
                    imsi = key
                    report.append({
                        "IPDR": ipdr_val,
                        "IMSI": imsi,
                        "TotalSessions": session_counts.get(key, 0)
                    })

            # Sort by TotalSessions in descending order
            sorted_data = sorted(report, key=lambda x: x['TotalSessions'], reverse=True)

            # Apply pagination to sorted data
            total_records = len(sorted_data)

            if total_records == 0:
                return Response({
                    "Report": [],
                    "TotalRecords": 0,
                    "Page": page,
                    "PageSize": page_size,
                    "TotalPages": 0,
                    "FilterType": filter_type,
                    "message": "No records found matching criteria"
                }, status=200)

            start_index = (page - 1) * page_size
            end_index = start_index + page_size

            if start_index >= total_records:
                return Response({
                    "error": "Page number out of range"
                }, status=400)

            paginated_report = sorted_data[start_index:end_index]

            print(f"\n{'=' * 60}")
            print(f"RESULTS:")
            print(f"{'=' * 60}")
            print(f"Total records: {total_records}")
            print(f"Page: {page}/{(total_records + page_size - 1) // page_size}")
            print(f"Returning: {len(paginated_report)} records")
            print(f"Filter type: {filter_type}")
            print(f"{'=' * 60}\n")

            return Response({
                "TotalRecords": total_records,
                "Page": page,
                "PageSize": page_size,
                "TotalPages": (total_records + page_size - 1) // page_size,
                "FilterType": filter_type,
                "data": paginated_report,
            }, status=200)

        except ValueError as e:
            print(f"❌ ValueError: {str(e)}")
            return Response({
                "error": "Invalid value",
                "details": str(e)
            }, status=400)
        except Exception as e:
            import traceback
            print(f"\n{'!' * 60}")
            print(f"CRITICAL ERROR:")
            print(f"{'!' * 60}")
            print(traceback.format_exc())
            print(f"{'!' * 60}\n")
            return Response({
                "error": "Internal server error",
                "details": str(e)
            }, status=500)