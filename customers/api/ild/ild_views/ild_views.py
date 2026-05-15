import ast
import json
from datetime import datetime, time
from django.utils.dateparse import parse_datetime
from functools import lru_cache
from django.db.models import Q
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from ...models import MobileOperator, CrimeInformation,CellTower
from ..ild_models.ild_model import ildNexus,ildRecord
from ..ild_models.ild_serializers import ildNexusSerializer,ildRecordSerializer
from ...serializers import CrimeInformationSerializer

import os
import json
from django.conf import settings

file_path = os.path.join(settings.BASE_DIR, "api", "data", "country_codes.json")

with open(file_path, "r", encoding="utf-8") as f:
    COUNTRY_CODES = json.load(f)

def get_country_name(code):
    if not code:
        return None
    code = str(code)

    if code in COUNTRY_CODES and len(COUNTRY_CODES[code]) > 0:
        return COUNTRY_CODES[code][0]  # first country name

    return None
class ILDNexusView(APIView):

    @swagger_auto_schema(
        operation_description="Retrieve all ILD Nexus records",
        responses={200: ildNexusSerializer(many=True)}
    )
    def get(self, request):
        try:
            nexus = ildNexus.objects.all()
        except ildNexus.DoesNotExist:
            return Response(
                {"error": "ILD Nexus records not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = ildNexusSerializer(nexus, many=True)
        nexus_data = serializer.data

        return Response(nexus_data, status=status.HTTP_200_OK)


class ILDRecordView(APIView):

    def post(self, request):
        try:
            pk = request.data.get("seq_id")
            filtervalue = request.data.get("filter")

            if not pk:
                return Response({"error": "seq_id is required"}, status=status.HTTP_400_BAD_REQUEST)

            nexus_data = ildNexus.objects.get(_id=pk)
            nexus_serializer = ildNexusSerializer(nexus_data, many=False)
            crime_id = nexus_serializer.data.get("CrimeID")

            crime_info = CrimeInformation.objects.get(id=crime_id)

            # Build queryset (still lazy)
            records = ildRecord.objects.filter(seq_id=pk)

            if filtervalue:
                from_date = parse_datetime(request.data.get("from_date"))
                to_date = parse_datetime(request.data.get("to_date"))
                min_duration = request.data.get("min_duration")
                max_duration = request.data.get("max_duration")

                if from_date and to_date:
                    records = records.filter(SDateTime__gte=from_date, SDateTime__lte=to_date)
                if min_duration is not None and max_duration is not None:
                    records = records.filter(
                        Duration__gte=int(min_duration),
                        Duration__lte=int(max_duration)
                    )

            # FIX 1: Check existence cheaply, don't evaluate the full queryset
            if not records:
                return Response({"error": "Record not found"}, status=status.HTTP_404_NOT_FOUND)

            # FIX 2: Pagination — don't return 94k rows in one shot
            page = int(request.data.get("page", 1))
            page_size = int(request.data.get("page_size", 500))
            offset = (page - 1) * page_size
            paged_records = records[offset: offset + page_size]

            record_serializer = ildRecordSerializer(paged_records, many=True)
            data = record_serializer.data  # list of OrderedDicts

            # FIX 3: Collect all lookup keys first, then batch-fetch from MongoDB
            b_codes = {
                r.get("b_mobile_code")
                for r in data
                if r.get("b_mobile_code") and str(r.get("b_mobile_code")).isdigit()
            }
            cell_ids = {r.get("First_CGI") for r in data if r.get("First_CGI")}

            # Batch lookup — one query each instead of N queries
            operators = {
                op.id: op
                for op in MobileOperator.objects(id__in=[int(c) for c in b_codes])
            }
            towers = {
                t.id: t
                for t in CellTower.objects(id__in=list(cell_ids))
            }

            remove_fields = ["seq_id", "_id", "CARRIER","SDateTime","SDateTime"]

            # FIX 4: Enrich `data` (the list), not record_serializer.data
            enriched = []
            for record in data:
                record = dict(record)  # make it mutable

                for field in remove_fields:
                    record.pop(field, None)

                b_code = record.get("b_mobile_code")
                if b_code and str(b_code).isdigit():
                    op = operators.get(int(b_code))
                    if op:
                        record["b_party_provider"] = f"{op.Operator} - {op.Circle}"

                cellid = record.get("First_CGI")
                if cellid:
                    tower = towers.get(cellid)
                    if tower:
                        record["First Cell Id Address"] = tower.ADDRESS
                        record["Main City (First Cellid)"] = tower.MAIN_CITY
                        record["Sub City (First Cellid)"] = tower.SUB_CITY
                        record["Lat-Long-Azimuth (First CellID)"] = (
                            f"{tower.LATITUDE} {tower.LONGITUDE} {tower.AZIMUTH}"
                        )
                cellid1 = record.get("Last_CGI")
                if cellid1:
                    tower = towers.get(cellid1)
                    if tower:
                        record["Last Cell Id Address"] = tower.ADDRESS
                        record["Main City (Last Cellid)"] = tower.MAIN_CITY
                        record["Sub City (Last Cellid)"] = tower.SUB_CITY
                        record["Lat-Long-Azimuth (Last CellID)"] = (
                            f"{tower.LATITUDE} {tower.LONGITUDE} {tower.AZIMUTH}"
                        )

                a_country = get_country_name(record.get("a_country_code"))
                b_country = get_country_name(record.get("b_country_code"))

                if a_country:
                    record["Country"] = a_country
                if b_country:
                    record["B Party Provider"] = b_country

                enriched.append(record)

            return Response({
                "page": page,
                "page_size": page_size,
                "total": records.count(),
                "records": enriched,
            }, status=status.HTTP_200_OK)

        except ildNexus.DoesNotExist:
            return Response({"error": "Nexus record not found"}, status=status.HTTP_404_NOT_FOUND)
        except CrimeInformation.DoesNotExist:
            return Response({"error": "Crime information not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

