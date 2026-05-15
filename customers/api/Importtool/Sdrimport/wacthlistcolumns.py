import logging
import os
from threading import Lock


from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response
from rest_framework import status

from pymongo import MongoClient, errors, UpdateOne

logger = logging.getLogger(__name__)

# ================== CONFIG ==================
MONGO_HOST = os.environ.get("MONGO_HOST", "localhost")
MONGO_PORT  = int(os.environ.get("MONGO_PORT", 27017))
SUBSCRIBER_DB      = os.getenv("Watchlist",      "Watchlist")
MAPPING_COLLECTION = os.getenv("MAPPING_COLLECTION", "Watchlist_cols")
DATA_COLLECTION    = os.getenv("DATA_COLLECTION",    "WatchList_data")


MONGO_URI = (
    f"mongodb://{MONGO_HOST}:{MONGO_PORT}/{SUBSCRIBER_DB}"
    "?directConnection=true"
)
POLARS_CHUNK_ROWS  = 500_000   # rows per polars batch  (was 100k — Rust handles 500k fine)
PANDAS_CHUNK_ROWS  = 100_000   # rows per pandas chunk  (was 50k)
MONGO_BATCH_SIZE   =  25_000   # documents per bulk_write (was 10k)
MAX_WORKERS        =     32    # parallel MongoDB writer threads (was 16)
PREVIEW_ROWS       =     10

# Module-level sh

_MONGO_CLIENT_POOL: MongoClient | None = None
_MONGO_POOL_LOCK = Lock()
def get_mongo_client() -> MongoClient:
    """Return a shared MongoClient with a large connection pool."""
    global _MONGO_CLIENT_POOL
    with _MONGO_POOL_LOCK:
        if _MONGO_CLIENT_POOL is None:
            _MONGO_CLIENT_POOL = MongoClient(
                MONGO_URI,
                maxPoolSize=MAX_WORKERS * 2,
                minPoolSize=8,
                connectTimeoutMS=5000,
                serverSelectionTimeoutMS=5000,
                socketTimeoutMS=30_000,
            )
        return _MONGO_CLIENT_POOL


def get_mongodb_mapping():
    client = get_mongo_client()
    doc = client[SUBSCRIBER_DB][MAPPING_COLLECTION].find_one() or {}
    doc.pop("_id", None)
    return doc

class AddWatchlistMappingKeyView(APIView):
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        key_name = (request.POST.get("key_name") or request.data.get("key_name", "")).strip()
        if not key_name:
            return Response({"error": "key_name is required"}, status=400)

        try:
            client = get_mongo_client()
            col = client[SUBSCRIBER_DB][MAPPING_COLLECTION]
            doc = col.find_one() or {}

            if key_name in doc and key_name != "_id":
                return Response({"error": f"Key '{key_name}' already exists"}, status=400)

            col.update_one({}, {"$set": {key_name: []}}, upsert=True)

            return Response({
                "status": "success",
                "message": f"Key '{key_name}' added successfully",
                "key_name": key_name,
            })

        except Exception as e:
            logger.error(f"Error adding key: {e}", exc_info=True)
            return Response({"error": "Failed to add key", "detail": str(e)}, status=500)