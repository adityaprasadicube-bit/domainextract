import ipaddress
from collections import defaultdict

from mongoengine import get_db
from rest_framework.views import APIView
from rest_framework.response import Response
from django.utils.dateparse import parse_datetime
from ..ipdr_models.ip_model import IPDRNexus, PortInfo
from ..ip_serializers import IPDRNexusSerializer, PortInfoSerializer
from ...searchengine import search_ip


class CountrywiseAPIView(APIView):
    def _parse_dt(self, val):
        try:
            return parse_datetime(val)
        except:
            return None

    def _ip_type(self, ip):
        try:
            return "IPv4" if ipaddress.ip_address(ip).version == 4 else "IPv6"
        except:
            return ""

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
            print(f"COUNTRYWISE REQUEST:")
            print(f"{'=' * 60}")
            print(f"Request body: {request.data}")
            print(f"{'=' * 60}\n")

            seq_ids = request.data.get("seq_ids", [])
            from_date = self._parse_dt(request.data.get("from_date"))
            to_date = self._parse_dt(request.data.get("to_date"))
            filter_type = request.data.get("filter_type", "number/ip")

            # Pagination parameters
            page = int(request.data.get("page", 1))
            page_size = int(request.data.get("page_size", 500))

            if not seq_ids or not from_date or not to_date:
                return Response({
                    "error": "seq_ids, from_date, to_date are required"
                }, status=400)

            if not isinstance(seq_ids, list):
                return Response({
                    "error": "seq_ids must be a list"
                }, status=400)

            if filter_type not in ["number/ip", "number", "ip"]:
                return Response({
                    "error": "filter_type must be 'number/ip', 'number', or 'ip'"
                }, status=400)

            print(f"📋 Received seq_ids: {seq_ids}")
            print(f"📅 Date range: {from_date} to {to_date}")
            print(f"🔍 Filter type: {filter_type}")

            db = get_db("ipdr_db")
            collection = db["IPDetailRecords"]

            # MongoDB aggregation pipeline
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
            nexus_data = list(nexus_collection.find({"_id": {"$in": seq_ids}}))

            if not nexus_data:
                return Response({
                    "error": "No nexus data found for given seq_ids"
                }, status=404)

            print(f"✅ Found {len(nexus_data)} nexus records")

            # If RecordType is "Destination IP", update all records
            if nexus_data[0].get("RecordType") == "Destination IP":
                nexus_ip = nexus_data[0].get("IPDR")
                print(f"🔄 Setting Destination_ip to: {nexus_ip}")
                for d in data:
                    d["Destination_ip"] = nexus_ip

            # Get all unique IPs and fetch country info
            ips = set(d.get("Destination_ip") for d in data if d.get("Destination_ip"))

            if not ips:
                return Response({
                    "error": "No destination IPs found in records"
                }, status=404)

            print(f"🌐 Looking up {len(ips)} unique IPs")

            try:
                ip_info = search_ip(list(ips))
                lookupIP = {rec["ip"]: rec for rec in ip_info.get("results", [])}
                print(f"✅ Loaded {len(lookupIP)} IP lookups")
            except Exception as e:
                print(f"⚠️ IP lookup error: {str(e)}")
                lookupIP = {}

            # Group data based on filter_type
            grouped_data = defaultdict(
                lambda: {"records": [], "from_date": None, "to_date": None}
            )

            for row in data:
                ip = row.get("Destination_ip")
                msisdn = row.get("MSISDN")
                s_datetime = row.get("SDateTime")
                e_datetime = row.get("EDateTime")

                # Get country for this IP
                ip_details = lookupIP.get(ip, {})
                country = ip_details.get("Country", "")

                # Create grouping key based on filter_type
                # IMPORTANT: Always include country in the grouping key
                if filter_type == "number/ip":
                    key = (msisdn, ip, country)
                elif filter_type == "number":
                    key = (msisdn, country)
                elif filter_type == "ip":
                    key = (ip, country)

                # Store the record
                grouped_data[key]["records"].append(row)

                # Update from_date (first SDateTime)
                if grouped_data[key]["from_date"] is None or s_datetime < grouped_data[key]["from_date"]:
                    grouped_data[key]["from_date"] = s_datetime

                # Update to_date (last EDateTime)
                if grouped_data[key]["to_date"] is None or e_datetime > grouped_data[key]["to_date"]:
                    grouped_data[key]["to_date"] = e_datetime

            print(f"📦 Grouped into {len(grouped_data)} unique combinations")

            # Build the report
            report = []
            for key, value in grouped_data.items():
                if filter_type == "number/ip":
                    msisdn, dest_ip, country = key
                    report.append({
                        "MSISDN": msisdn,
                        "Destination_IP": dest_ip,
                        "From Date": value["from_date"],
                        "To Date": value["to_date"],
                        "Country": country,
                        "Record Count": len(value["records"])
                    })
                elif filter_type == "number":
                    msisdn, country = key
                    report.append({
                        "MSISDN": msisdn,
                        "From Date": value["from_date"],
                        "To Date": value["to_date"],
                        "Country": country,
                        "Record Count": len(value["records"])
                    })
                elif filter_type == "ip":
                    dest_ip, country = key
                    report.append({
                        "Destination_IP": dest_ip,
                        "From Date": value["from_date"],
                        "To Date": value["to_date"],
                        "Country": country,
                        "Record Count": len(value["records"])
                    })

            # Apply pagination
            total_records = len(report)

            if total_records == 0:
                return Response({
                    "TotalRecords": 0,
                    "Page": page,
                    "PageSize": page_size,
                    "TotalPages": 0,
                    "FilterType": filter_type,
                    "Report": [],
                    "message": "No records found matching criteria"
                }, status=200)

            start_index = (page - 1) * page_size
            end_index = start_index + page_size

            if start_index >= total_records:
                return Response({
                    "error": "Page number out of range"
                }, status=400)

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
                "FilterType": filter_type,
                "data": paginated_report
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