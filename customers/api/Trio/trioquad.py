from rest_framework.views import APIView
from rest_framework.response import Response
from mongoengine import get_db
from datetime import datetime

from ..Whatsapp.whatsapp_views.whatsapp_views import WhatsAppNexusView
from ..models import CrimeInformation
from ..serializers import CrimeInformationSerializer


class TrioNexuaApi(APIView):
    from functools import lru_cache

    @lru_cache(maxsize=512)
    def get_crime_name(self,crime_id):
        if not crime_id:
            return None
        try:
            crime = CrimeInformation.objects.only("Crime").get(id=crime_id)
            return crime.Crime
        except CrimeInformation.DoesNotExist:
            return None

    def get(self, request):

        ipdr_db = get_db(alias='ipdr_db')
        cdr_db = get_db(alias='cdr_db')
        whatsapp_db = get_db(alias='whatsapp_db')
        source_db = get_db(alias='source_db')
        # crime_info = CrimeInformation.objects.get(id=crime_id)
        # crime_serializer = CrimeInformationSerializer(crime_info, many=False).data
        trioquad_collection = source_db['TrioQuadNexus']

        temp = {}

        # ---------------- CDR ----------------
        for r in cdr_db['DataNexus'].find(
            {"RecordType": "CDR"},
            {
                "CDRNo_Or_ImeiNo": 1,
                "FromDate": 1,
                "ToDate": 1,
                "Inserted": 1,
                "CrimeID": 1,
                "_id": 0
            }
        ):
            target = r.get("CDRNo_Or_ImeiNo")
            if not target:
                continue

            temp.setdefault(target, {})["CDR"] = {
                "DateRange": f"{r['FromDate']} to {r['ToDate']}",
                "Records": r.get("Inserted", 0),
                "CrimeID": r.get("CrimeID")
            }

        # ---------------- IPDR ----------------
        for r in ipdr_db['IPdrNexus'].find(
            {"RecordType": "Mobile"},
            {
                "IPDR": 1,
                "FromDate": 1,
                "ToDate": 1,
                "Inserted": 1,
                "CrimeID": 1,
                "_id": 0
            }
        ):
            target = r.get("IPDR")
            if not target:
                continue

            temp.setdefault(target, {})["IPDR"] = {
                "DateRange": f"{r['FromDate']} to {r['ToDate']}",
                "Records": r.get("Inserted", 0),
                "CrimeID": r.get("CrimeID")
            }

        # ---------------- WhatsApp ----------------
        for r in whatsapp_db['WhatsAppNexus'].find(
            {"RecordType": "WhatsApp"},
            {
                "TargetNo": 1,
                "FromDate": 1,
                "ToDate": 1,
                "Inserted": 1,
                "CrimeID": 1,
                "_id": 0
            }
        ):
            target = r.get("TargetNo")
            if not target:
                continue

            temp.setdefault(target, {})["WhatsApp"] = {
                "DateRange": f"{r['FromDate']} to {r['ToDate']}",
                "Records": r.get("Inserted", 0),
                "CrimeID": r.get("CrimeID")
            }

        # ----------- ONLY COMMON + FLATTEN + INSERT -----------
        final_data = []

        for mobile, modules in temp.items():
            if len(modules) >= 2:   # ✅ only common
                crime_names = [
                    self.get_crime_name(modules.get("CDR", {}).get("CrimeID")),
                    self.get_crime_name(modules.get("IPDR", {}).get("CrimeID")),
                    self.get_crime_name(modules.get("WhatsApp", {}).get("CrimeID")),
                ]

                row = {
                    "MobileNo": mobile,

                    "CDRDateRange": modules.get("CDR", {}).get("DateRange"),
                    "CDRRecords": modules.get("CDR", {}).get("Records"),
                    "CDRCrimeID": modules.get("CDR", {}).get("CrimeID"),


                    "Crime": " , ".join([name for name in crime_names if name]),

                    "IPDRDateRange": modules.get("IPDR", {}).get("DateRange"),
                    "IPDRRecords": modules.get("IPDR", {}).get("Records"),
                    # "IPDRCrimeID": modules.get("IPDR", {}).get("CrimeID"),

                    "WhatsAppDateRange": modules.get("WhatsApp", {}).get("DateRange"),
                    "WhatsAppRecords": modules.get("WhatsApp", {}).get("Records"),
                    "WhatsAppCrimeID": modules.get("WhatsApp", {}).get("CrimeID"),


                    "UpdatedAt": datetime.utcnow()
                }

                # 🔥 UPSERT into TrioQuadNexus
                trioquad_collection.update_one(
                    {"MobileNo": mobile},
                    {"$set": row, "$setOnInsert": {"CreatedAt": datetime.utcnow()}},
                    upsert=True
                )

                final_data.append(row)

        return Response({
            "total": len(final_data),
            "data": final_data
        })
