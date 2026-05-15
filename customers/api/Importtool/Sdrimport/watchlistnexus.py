from mongoengine import get_db
from rest_framework.views import APIView


from rest_framework.views import APIView
from rest_framework.response import Response
from bson import ObjectId

class WatchlistNexusApi(APIView):
    def get(self, request):
        db = get_db(alias="watchlist_db")
        collection = db["Watchlist_nexus"]

        data = list(collection.find({}))
        # Convert ObjectId to string
        for item in data:
            item["_id"] = str(item["_id"])

        return Response(data)
