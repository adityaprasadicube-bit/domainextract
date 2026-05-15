from datetime import datetime

from dbf import delete
from mongoengine import get_db
from rest_framework.response import Response
from rest_framework import status
from rest_framework.views import APIView


from datetime import datetime
from bson import ObjectId

from mongoengine import get_db
from rest_framework.response import Response
from rest_framework import status
from rest_framework.views import APIView


class Delete(APIView):
    def post(self, request):

        seq_id = request.data.get('seq_id')
        filter_value = request.data.get('filervalue')   # keep your key name

        if not seq_id:
            return Response(
                {"error": "seq_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not filter_value:
            return Response(
                {"error": "filervalue is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # ✅ Map filter → db + collection
        MODULE_MAP = {
            "CDR": ("cdr_db", "DataNexus"),
            "TowerDump": ("tower_dump", "TowerDumpNexus"),
            "WhatsApp": ("whatsapp_db", "WhatsAppNexus"),
            "IPDR": ("ipdr_db", "IPdrNexus"),
        }

        if filter_value not in MODULE_MAP:
            return Response(
                {"error": "Invalid filervalue"},
                status=status.HTTP_400_BAD_REQUEST
            )

        db_original, collection_original = MODULE_MAP[filter_value]

        # ✅ Get source collection
        source_db = get_db(alias=db_original)
        source_collection = source_db[collection_original]

        # ✅ Get DeletedNexus (single archive DB)
        archive_db = get_db(alias="source_db")
        deleted_collection = archive_db["DeletedNexus"]

        # ✅ Convert string id to ObjectId
        try:
            object_id = seq_id
        except Exception:
            return Response(
                {"error": "Invalid seq_id format"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # ✅ Find records
        records = list(source_collection.find({"_id": object_id}))

        if not records:
            return Response(
                {"message": "No records found for given seq_id"},
                status=status.HTTP_404_NOT_FOUND
            )

        # ✅ Add audit info
        for rec in records:
            rec["deleted_at"] = datetime.utcnow()
            rec["deleted_by"] = (
                request.user.username if request.user.is_authenticated else "system"
            )
            rec["Collection"] = collection_original
            rec["Source_db"] = db_original

        # ✅ Insert into DeletedNexus
        deleted_collection.insert_many(records)

        # ✅ Delete from source
        delete_result = source_collection.delete_many({"_id": object_id})

        return Response(
            {
                "message": "Records deleted and archived successfully",
                "deleted_count": delete_result.deleted_count
            },
            status=status.HTTP_200_OK
        )


from mongoengine import get_db
from rest_framework.response import Response
from rest_framework import status
from rest_framework.views import APIView


class RestoreAPI(APIView):
    def post(self, request):

        crime_db = get_db(alias="cdr_db")
        crime_collection = crime_db['CrimeRegistry']

        source_db = get_db(alias="source_db")

        source_collection = source_db['DeletedNexus']

        crime_name = request.data.get('crimename')

        if not crime_name:
            return Response(
                {"error": "crimename is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # ✅ Find crime records
        crime_records = list(crime_collection.find({"Crime": crime_name}))

        if not crime_records:
            return Response(
                {"message": "Crime not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        # ✅ Extract seq_ids from crime records
        seq_ids = [

                rec.get("seq_id") for rec in crime_records if rec.get("seq_id")
        ]
        print('seqids',seq_ids)
        if not seq_ids:
            return Response(
                {"message": "No seq_id found for this crime"},
                status=status.HTTP_404_NOT_FOUND
            )

        # ✅ Find deleted nexus records using seq_id
        deleted_records = list(
            source_collection.find({"_id": {"$in": seq_ids}})
        )

        if not deleted_records:
            return Response(
                {"message": "No deleted records found to restore"},
                status=status.HTTP_404_NOT_FOUND
            )

        # ✅ Remove delete audit fields before restore (optional)
        for rec in deleted_records:
            rec.pop("deleted_at", None)
            rec.pop("deleted_by", None)
            rec.pop("original_collection", None)

        # ✅ Insert back into TowerDumpNexus
        for  rec in deleted_records:
            adding_db = get_db(alias=rec.get("Source_db"))
            adding_db[rec.get("Collection")].insert_one(rec)

        # ✅ Remove from DeletedNexus after restore
        source_collection.delete_many({"_id": {"$in": seq_ids}})

        return Response(
            {
                "message": "Records restored successfully",
                "restored_count": len(deleted_records)
            },
            status=status.HTTP_200_OK
        )




class NameAddition(APIView):
     def post(self,request):
         sourcedb = request.data.get('sourcedb')
         seq_id = request.data.get('seq_id')
         name = request.data.get('name')
         db = None
         collection = None
         if sourcedb == 'cdr':
             db = get_db("cdr_db")
             collection = db['DataNexus']
         if sourcedb == 'ipdr':
            db = get_db('ipdr_db')
            collection = db['IPdrNexus']
         if sourcedb == 'towerdump':
             db = get_db('towerdump_db')
             collection = db["TowerDumpNexus"]
         if sourcedb == 'wp':
             db=get_db("whatsapp_db")
             collection = db['WhatsAppNexus']

         try:
             collection.update_one(
                 {"_id": seq_id},
                 {"$set": {"Name": name}}
             )

             return Response(
                 {"message": "Name added successfully"},
                 status=status.HTTP_200_OK
             )

         except Exception as e:
             return Response(
                 {"error": str(e)},
                 status=status.HTTP_500_INTERNAL_SERVER_ERROR
             )


