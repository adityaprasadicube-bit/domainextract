from mongoengine import get_db
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from datetime import datetime
from ...searchengine import search_ip


# ==================== MAIN VIEW ====================
class MaxOrgReportView(APIView):

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
            print(f"MAX ORG REPORT REQUEST:")
            print(f"{'=' * 60}")
            print(f"Request body: {request.data}")
            print(f"{'=' * 60}\n")

            # Accept multiple seq_ids
            seq_ids = request.data.get("seq_ids", [])
            fromdate = request.data.get("from_date")
            todate = request.data.get("to_date")
            filtertype = request.data.get("filter_type", "MAXorg")
            page = int(request.data.get("page", 1))
            page_size = int(request.data.get("page_size", 500))

            if not seq_ids or not fromdate or not todate:
                return Response(
                    {"error": "seq_ids, from_date, to_date are required"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if not isinstance(seq_ids, list):
                return Response(
                    {"error": "seq_ids must be a list"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Validate filtertype
            filtertype = filtertype.upper() if filtertype else "MAXORG"
            if filtertype not in ["MAXORG", "MAXCOUNTRY", "MAXUSAGE"]:
                return Response(
                    {"error": "filtertype must be 'MAXorg', 'MAXcountry', or 'MAXusage'"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            print(f"📋 Received seq_ids: {seq_ids}")
            print(f"📅 Date range: {fromdate} to {todate}")
            print(f"🔍 Filter type: {filtertype}")

            try:
                from_dt = datetime.fromisoformat(fromdate)
                to_dt = datetime.fromisoformat(todate)
            except (ValueError, TypeError) as e:
                return Response(
                    {"error": f"Invalid date format: {str(e)}"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Connect to MongoDB
            db = get_db("ipdr_db")

            # Fetch multiple Nexus records
            nexus_collection = db["IPdrNexus"]
            nexus_docs = list(nexus_collection.find({"_id": {"$in": seq_ids}}))

            if not nexus_docs:
                return Response(
                    {"error": "No matching seq_ids found in Nexus"},
                    status=404
                )

            print(f"✅ Found {len(nexus_docs)} nexus records")

            # Same behavior: take first IPDR value
            nexus_ipdr_value = nexus_docs[0].get("IPDR", "Unknown")
            print(f"📞 IPDR value: {nexus_ipdr_value}")

            # Fetch IPDR records
            collection = db["IPDetailRecords"]

            # Match multiple seq_ids
            pipeline = [
                {
                    "$match": {
                        "seq_id": {"$in": seq_ids},
                        "SDateTime": {"$gte": from_dt, "$lte": to_dt}
                    }
                },
                {"$sort": {"SDateTime": 1}}
            ]

            ipdr_records = list(collection.aggregate(pipeline))

            if not ipdr_records:
                return Response({
                    "message": "No matching records",
                    "TotalRecords": 0,
                    "Page": page,
                    "PageSize": page_size,
                    "TotalPages": 0,
                    "FilterType": filtertype,
                    "data": []
                }, status=200)

            print(f"📊 Found {len(ipdr_records)} IPDR records")

            # Collect all destination IPs
            dest_ips_list = []
            for doc in ipdr_records:
                decoded_ip = doc.get("Destination_ip")
                if decoded_ip:
                    dest_ips_list.append(decoded_ip)

            unique_ips = list(set(dest_ips_list))
            print(f"🌐 Looking up {len(unique_ips)} unique IPs")

            # Batch search using search_ip
            try:
                ip_info = search_ip(unique_ips)

                # Build IP lookup dictionary
                ip_lookup = {}
                for doc in ip_info.get("results", []):
                    if doc and 'ip' in doc:
                        ip_lookup[doc['ip']] = doc

                print(f"✅ Loaded {len(ip_lookup)} IP lookups")
            except Exception as e:
                print(f"⚠️ IP lookup error: {str(e)}")
                ip_lookup = {}

            lookupDestIP = {}

            print(f"🔄 Aggregating by {filtertype}...")

            # Aggregate based on filtertype
            if filtertype == "MAXORG":
                result = self._aggregate_by_org(
                    ipdr_records, ip_lookup, lookupDestIP, nexus_ipdr_value
                )
            elif filtertype == "MAXUSAGE":
                result = self._aggregate_by_usage(
                    ipdr_records, ip_lookup, lookupDestIP, nexus_ipdr_value
                )
            else:  # MAXCOUNTRY
                result = self._aggregate_by_country(
                    ipdr_records, ip_lookup, lookupDestIP, nexus_ipdr_value
                )

            print(f"📦 Aggregated into {len(result)} unique entries")

            # Pagination
            total_records = len(result)

            if total_records == 0:
                return Response({
                    "TotalRecords": 0,
                    "Page": page,
                    "PageSize": page_size,
                    "TotalPages": 0,
                    "FilterType": filtertype,
                    "data": [],
                    "message": "No records found matching criteria"
                }, status=200)

            start_index = (page - 1) * page_size
            end_index = start_index + page_size

            if start_index >= total_records:
                return Response({
                    "error": "Page number out of range"
                }, status=400)

            paginated_result = result[start_index:end_index]

            print(f"\n{'=' * 60}")
            print(f"RESULTS:")
            print(f"{'=' * 60}")
            print(f"Total records: {total_records}")
            print(f"Page: {page}/{(total_records + page_size - 1) // page_size}")
            print(f"Returning: {len(paginated_result)} records")
            print(f"Filter type: {filtertype}")
            print(f"{'=' * 60}\n")

            return Response({
                "TotalRecords": total_records,
                "Page": page,
                "PageSize": page_size,
                "TotalPages": (total_records + page_size - 1) // page_size,
                "FilterType": filtertype,
                "data": paginated_result
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

    # ==================== HELPERS ====================

    def _get_ip_details(self, decoded_ip, ip_lookup, lookupDestIP):
        """Extract IP details from lookup dictionary"""
        isp_org = country = usage = App = None
        VPN_Proxy_Tor = TSP_Broadband_Satellite = None

        if decoded_ip in ip_lookup:
            search_result = ip_lookup[decoded_ip]
            isp_org = search_result.get("Isp/Org")
            country = search_result.get("Country")
            usage = search_result.get("Usage")
            App = search_result.get("App/Hostname")
            VPN_Proxy_Tor = search_result.get("VPN/Proxy/Tor")
            TSP_Broadband_Satellite = search_result.get("TSP/Broadband/Satellite")

        return isp_org, country, usage, App, VPN_Proxy_Tor, TSP_Broadband_Satellite

    def _aggregate_by_org(self, ipdr_records, ip_lookup, lookupDestIP, nexus_ipdr_value):
        """Aggregate records by ISP/Organization"""
        org_report = {}

        for doc in ipdr_records:
            decoded_ip = doc.get("Destination_ip")
            if not decoded_ip:
                continue

            isp_org, _, _, _, _, _ = self._get_ip_details(decoded_ip, ip_lookup, lookupDestIP)
            isp_org = isp_org or "Unknown"

            if isp_org not in org_report:
                org_report[isp_org] = {
                    "IPDR": nexus_ipdr_value,
                    "Isp/Org": isp_org,
                    "Total Sessions": 0
                }

            org_report[isp_org]["Total Sessions"] += 1

        return sorted(org_report.values(), key=lambda x: x["Total Sessions"], reverse=True)

    def _aggregate_by_country(self, ipdr_records, ip_lookup, lookupDestIP, nexus_ipdr_value):
        """Aggregate records by Country"""
        country_report = {}

        for doc in ipdr_records:
            decoded_ip = doc.get("Destination_ip")
            if not decoded_ip:
                continue

            _, country, _, _, _, _ = self._get_ip_details(decoded_ip, ip_lookup, lookupDestIP)
            country = country or "Unknown"

            if country not in country_report:
                country_report[country] = {
                    "IPDR": nexus_ipdr_value,
                    "Country": country,
                    "Total Sessions": 0
                }

            country_report[country]["Total Sessions"] += 1

        return sorted(country_report.values(), key=lambda x: x["Total Sessions"], reverse=True)

    def _aggregate_by_usage(self, ipdr_records, ip_lookup, lookupDestIP, nexus_ipdr_value):
        """Aggregate records by Usage type"""
        usage_report = {}

        for doc in ipdr_records:
            decoded_ip = doc.get("Destination_ip")
            if not decoded_ip:
                continue

            _, _, usage, _, VPN_Proxy_Tor, TSP_Broadband_Satellite = self._get_ip_details(
                decoded_ip, ip_lookup, lookupDestIP
            )

            group_key = (
                usage or "Unknown",
                VPN_Proxy_Tor or "Unknown",
                TSP_Broadband_Satellite or "Unknown"
            )

            if group_key not in usage_report:
                usage_report[group_key] = {
                    "IPDR": nexus_ipdr_value,
                    "Usage": usage or "Unknown",
                    "VPN/Proxy/Tor": VPN_Proxy_Tor or "Unknown",
                    "TSP/Broadband/Satellite": TSP_Broadband_Satellite or "Unknown",
                    "Total Sessions": 0
                }

            usage_report[group_key]["Total Sessions"] += 1

        return sorted(usage_report.values(), key=lambda x: x["Total Sessions"], reverse=True)