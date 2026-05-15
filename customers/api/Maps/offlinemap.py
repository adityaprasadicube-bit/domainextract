import logging
import re

from bson import Binary
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound
from mongoengine import get_db
from pymongo.errors import OperationFailure, ConnectionFailure
from django.core.cache import cache

# ─────────────────────────────────────────────
#  Logging
# ─────────────────────────────────────────────
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  Config — values come from settings.py
# ─────────────────────────────────────────────
MAX_ZOOM  = getattr(settings, 'MAX_ZOOM',        14)
CACHE_TTL = getattr(settings, 'TILE_CACHE_TTL',  60 * 60 * 24 * 30)

# ─────────────────────────────────────────────
#  Layer name validation
# ─────────────────────────────────────────────
LAYER_NAME_PATTERN = re.compile(r'^[\w-]{1,32}$')

# ─────────────────────────────────────────────
#  Transparent 1×1 PNG fallback
# ─────────────────────────────────────────────
TRANSPARENT_PIXEL = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
    b'\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
    b'\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01'
    b'\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)

# ─────────────────────────────────────────────
#  NOTE: No MongoClient / _db here.
#  We use MongoEngine's 'Maps' alias registered
#  in settings.py — same pattern as all other
#  views in this project.
# ─────────────────────────────────────────────


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────
def _make_cache_key(layer: str, z: int, x: int, y: int) -> str:
    return f"maptile:{layer}:{z}:{x}:{y}"


def _fetch_from_mongo(layer: str, z: int, x: int, y: int) -> bytes:
    """
    Fetch a single tile from MongoDB.

    Uses the MongoEngine 'Maps' alias connected in settings.py.
    This works on localhost AND inside Docker because MONGO_HOST
    is read from the environment variable in settings.py.

    Returns raw PNG bytes, or TRANSPARENT_PIXEL if the tile
    does not exist in the database.
    """
    db         = get_db(alias='Maps')          # ← uses settings.py connection
    collection = db[f"tiles_{layer}"]
    tile = collection.find_one(
        {"z": z, "x": x, "y": y},
        {"image_data": 1, "_id": 0},
    )
    if tile and "image_data" in tile:
        raw = tile["image_data"]
        return raw.data if isinstance(raw, Binary) else bytes(raw)
    return TRANSPARENT_PIXEL


# ─────────────────────────────────────────────
#  Main view
# ─────────────────────────────────────────────
def get_map_tile(request, layer: str, z, x, y):
    """
    Serve offline map tiles stored in MongoDB.
    URL pattern:  tiles/<str:layer>/<int:z>/<int:x>/<int:y>.png
    """

    # 1. Validate layer name — prevent injection / path traversal
    if not LAYER_NAME_PATTERN.match(layer):
        logger.warning("Invalid layer name rejected: %s", layer)
        return HttpResponseNotFound("Invalid layer name")

    # 2. Cast coordinates to int
    try:
        z, x, y = int(z), int(x), int(y)
    except (ValueError, TypeError):
        return HttpResponseBadRequest("Tile coordinates must be integers")

    # 3. Validate zoom level
    if not (0 <= z <= MAX_ZOOM):
        return HttpResponseNotFound(f"Zoom {z} out of range (0–{MAX_ZOOM})")

    # 4. Try the file cache first — zero DB hit on repeat requests
    cache_key   = _make_cache_key(layer, z, x, y)
    image_bytes = cache.get(cache_key)

    if image_bytes is None:
        # 5. Cache miss → go to MongoDB
        try:
            image_bytes = _fetch_from_mongo(layer, z, x, y)
        except ConnectionFailure as exc:
            # MongoDB is unreachable — surface a 503 so the client can retry
            logger.critical("MongoDB connection failed: %s", exc)
            return HttpResponse("Map database unreachable", status=503)
        except OperationFailure as exc:
            logger.error("MongoDB operation failed: %s", exc)
            return HttpResponse("Map database error", status=500)
        except Exception as exc:
            logger.exception("Unexpected error fetching tile (%s %s/%s/%s): %s",
                             layer, z, x, y, exc)
            return HttpResponse("Internal server error", status=500)

        # 6. Only cache real tiles — NOT the transparent fallback.
        #    Caching the fallback would permanently hide tiles that
        #    weren't in MongoDB at the time of the first request.
        if image_bytes != TRANSPARENT_PIXEL:
            cache.set(cache_key, image_bytes, CACHE_TTL)
            logger.debug("Cache MISS → stored  %s", cache_key)
        else:
            logger.debug("Tile missing in DB, not caching: %s", cache_key)
    else:
        logger.debug("Cache HIT  %s", cache_key)

    # 7. Return the tile with appropriate cache headers
    response = HttpResponse(image_bytes, content_type="image/png")
    response["Cache-Control"]               = f"public, max-age={CACHE_TTL}, immutable"
    response["Access-Control-Allow-Origin"] = "*"
    return response