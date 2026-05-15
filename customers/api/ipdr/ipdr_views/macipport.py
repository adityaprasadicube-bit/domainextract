import ipaddress
from mongoengine import get_db
from rest_framework.views import APIView
from rest_framework.response import Response
from django.utils.dateparse import parse_datetime
from ..ipdr_models.ip_model import IPDRNexus, PortInfo
from ..ip_serializers import IPDRNexusSerializer, PortInfoSerializer
from ...searchengine import search_ip


class MaxipportAPIView(APIView):

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
            print(f"MAX IP PORT REQUEST:")
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
            filter_type = request.data.get("filter_type", "maxipport")

            # Pagination parameters
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

            if filter_type not in [
                "maxip", "maxport", "maxipport",
                "maxipportsessions", "maxipsessions"
            ]:
                return Response({
                    "error": "filter_type must be one of "
                             "'maxip', 'maxport', 'maxipport', "
                             "'maxipportsessions', 'maxipsessions'"
                }, status=400)

            print(f"📋 Received seq_ids: {seq_ids}")
            print(f"📅 Date range: {from_date} to {to_date}")
            print(f"🔍 Filter type: {filter_type}")

            # Fetch Nexus records
            try:
                nexus_qs = IPDRNexus.objects.filter(id__in=seq_ids)

                if not nexus_qs:
                    return Response({
                        "error": "No Nexus records found for given seq_ids"
                    }, status=404)

                # Use first nexus record for IPDR value
                nexus_data = IPDRNexusSerializer(nexus_qs[0]).data
                print(f"✅ Found {len(nexus_qs)} nexus records")

            except Exception as e:
                print(f"❌ Error fetching Nexus records: {str(e)}")
                return Response({
                    "error": f"Error fetching Nexus records: {str(e)}"
                }, status=404)

            # MongoDB Aggregation
            db = get_db("ipdr_db")
            collection = db["IPDetailRecords"]

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
                    "error": "No IPDR records found for given date range"
                }, status=404)

            print(f"📊 Found {len(data)} IPDR records")

            # Update Destination IP if RecordType is "Destination IP"
            if nexus_data.get("RecordType") == "Destination IP":
                nexus_ip = nexus_data.get("IPDR")
                print(f"🔄 Setting Destination_ip to: {nexus_ip}")
                for d in data:
                    d["Destination_ip"] = nexus_ip

            # Collect IP + Port for lookup
            ips = set(d.get("Destination_ip") for d in data if d.get("Destination_ip"))
            ports = set(d.get("Destination_port") for d in data if d.get("Destination_port"))

            if not ips:
                return Response({
                    "error": "No Destination IP data available"
                }, status=404)

            print(f"🌐 Looking up {len(ips)} unique IPs")
            print(f"🔌 Looking up {len(ports)} unique ports")

            # Bulk IP lookup
            try:
                ip_info = search_ip(list(ips))
                lookupIP = {rec["ip"]: rec for rec in ip_info.get("results", [])}
                print(f"✅ Loaded {len(lookupIP)} IP lookups")
            except Exception as e:
                print(f"⚠️ IP lookup error: {str(e)}")
                return Response({
                    "error": "Failed to fetch IP intelligence data",
                    "details": str(e)
                }, status=500)

            # Bulk Port lookup
            try:
                port_docs = PortInfo.objects.filter(Port__in=ports)
                lookupPort = {p.Port: PortInfoSerializer(p).data for p in port_docs}
                print(f"✅ Loaded {len(lookupPort)} port lookups")
            except Exception as e:
                print(f"⚠️ Port lookup error: {str(e)}")
                lookupPort = {}

            # Build session counts based on filter_type
            print(f"🔄 Counting sessions by {filter_type}...")
            session_counts = {}

            for row in data:
                ip = row.get("Destination_ip")
                prt = row.get("Destination_port", "")
                msisdn = row.get("MSISDN", "")

                if filter_type == "maxip":
                    key = ip
                elif filter_type == "maxport":
                    key = prt
                elif filter_type == "maxipportsessions":
                    key = (msisdn,ip, prt)
                elif filter_type == "maxipsessions":
                    key = (msisdn,ip)
                else:  # maxipport
                    key = (ip, prt)

                session_counts[key] = session_counts.get(key, 0) + 1

            print(f"📦 Counted {len(session_counts)} unique session groups")

            # Build Final Report
            processed_keys = set()
            report = []

            for row in data:
                ip = row.get("Destination_ip")
                prt = row.get("Destination_port", "")
                msisdn = row.get("MSISDN", "")

                if filter_type == "maxip":
                    key = ip
                elif filter_type == "maxport":
                    key = prt
                elif filter_type == "maxipportsessions":
                    key = (msisdn, prt,ip)
                elif filter_type == "maxipsessions":
                    key = (msisdn,ip)
                else:  # maxipport
                    key = (ip, prt)

                if key in processed_keys:
                    continue
                processed_keys.add(key)

                ip_detail = lookupIP.get(ip, {})
                port_detail = lookupPort.get(prt, {})

                isp_org = ip_detail.get("Isp/Org", "")
                country = ip_detail.get("Country", "")
                location = ip_detail.get("Location", "")
                usage = ip_detail.get("Usage", "")
                domains = ip_detail.get("Domains", "")
                VPN_Proxy_Tor = ip_detail.get("VPN/Proxy/Tor")
                TSP_Broadband_Satellite = ip_detail.get("TSP/Broadband/Satellite")
                ip_lat = ip_detail.get("IPLat")
                ip_long = ip_detail.get("IPLong")

                hostname = domains.split(',')[0].strip() if domains else ""

                port_type = port_detail.get("Type", "")
                port_desc = port_detail.get("Description", "")
                port_category = port_detail.get("Category", "")
                port_info_combined = f"{port_desc} {port_type}".strip() if port_desc else port_type

                # Calculate duration
                duration_minutes = 0
                if row.get("EDateTime") and row.get("SDateTime"):
                    try:
                        duration_minutes = round(
                            (row["EDateTime"] - row["SDateTime"]).total_seconds() / 60
                        )
                    except:
                        pass

                # Build response based on filter_type
                if filter_type == "maxip":
                    report.append({
                        "IPDR": nexus_data.get("IPDR", ""),
                        "Destination_IP": ip,
                        "Total_Session": session_counts.get(key, 0),
                        "Isp/Org": isp_org,
                        "App/Hostname": hostname,
                        "Usage": usage,
                        "Domains": domains,
                        "Country": country,
                        "Location": location,
                        "IP_Type": self._ip_type(ip),
                        "Start_DateTime": row.get("SDateTime"),
                        "End_DateTime": row.get("EDateTime"),
                        "Duration_Minutes": duration_minutes
                    })
                elif filter_type == "maxport":
                    report.append({
                        "IPDR": nexus_data.get("IPDR", ""),
                        "Total_Session": session_counts.get(key, 0),
                        "Destination_Port_Info": port_info_combined,
                        "Port_Category": port_category,
                        "Port": prt,
                        "Start_DateTime": row.get("SDateTime"),
                        "End_DateTime": row.get("EDateTime"),
                        "Duration_Minutes": duration_minutes
                    })
                elif filter_type == "maxipport":
                    report.append({
                        "IPDR": nexus_data.get("IPDR", ""),
                        "Destination_IP": ip,
                        "Total_Session": session_counts.get(key, 0),
                        "Isp/Org": isp_org,
                        "App/Hostname": hostname,
                        "Destination_Port_Info": port_info_combined,
                        "Port_Category": port_category,
                        "Port": prt,
                        "Usage": usage,
                        "Domains": domains,
                        "Country": country,
                        "Location": location,
                        "IP_Type": self._ip_type(ip),
                        "Start_DateTime": row.get("SDateTime"),
                        "End_DateTime": row.get("EDateTime"),
                        "Duration_Minutes": duration_minutes
                    })
                elif filter_type == "maxipportsessions":
                    report.append({
                        "IPDR": nexus_data.get("IPDR", ""),
                        "MSISDN": msisdn,
                        "Total_Session": session_counts.get(key, 0),
                        "Destination_IP": ip,
                        "Isp/Org": isp_org,
                        "Domains": domains,
                        "Usage": usage,
                        "VPN/Proxy/Tor": VPN_Proxy_Tor,
                        "TSP/Broadband/Satellite": TSP_Broadband_Satellite,
                        "App/Hostname": hostname,
                        "DestinationPort": prt,
                        "Destination_Port_Info": port_info_combined,
                        "Port_Category": port_category,
                        "Port Type": port_type,
                        "Country": country,
                        "Location": location,
                        "IPLat": ip_lat,
                        "IPLong": ip_long,
                        "IP_Type": self._ip_type(ip),
                        "Start_DateTime": row.get("SDateTime"),
                        "End_DateTime": row.get("EDateTime"),
                        "Duration_Minutes": duration_minutes
                    })
                elif filter_type == "maxipsessions":
                    report.append({
                        "IPDR": nexus_data.get("IPDR", ""),
                        "MSISDN": msisdn,
                        "Total_Session": session_counts.get(key, 0),
                        "Destination_IP": ip,
                        "Isp/Org": isp_org,
                        "Domains": domains,
                        "Usage": usage,
                        "VPN/Proxy/Tor": VPN_Proxy_Tor,
                        "TSP/Broadband/Satellite": TSP_Broadband_Satellite,
                        "App/Hostname": hostname,
                        "Country": country,
                        "Location": location,
                        "IPLat": ip_lat,
                        "IPLong": ip_long,
                        "IP_Type": self._ip_type(ip),
                        "Start_DateTime": row.get("SDateTime"),
                        "End_DateTime": row.get("EDateTime"),
                        "Duration_Minutes": duration_minutes
                    })

            if not report:
                return Response({
                    "TotalRecords": 0,
                    "Page": page,
                    "PageSize": page_size,
                    "TotalPages": 0,
                    "FilterType": filter_type,
                    "data": [],
                    "message": "No data available after applying filters"
                }, status=200)

            # Sort by Total_Session descending
            sorted_report = sorted(report, key=lambda x: x["Total_Session"], reverse=True)

            # Apply pagination
            total_records = len(sorted_report)
            start_index = (page - 1) * page_size

            if start_index >= total_records:
                return Response({
                    "error": "Page number out of range"
                }, status=400)

            paginated_report = sorted_report[start_index:start_index + page_size]

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