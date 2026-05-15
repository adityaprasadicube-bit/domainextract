import json
import re
import xxhash
from datetime import date

from bson import ObjectId
from django.http import HttpResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from .watchlist_models import WatchlistEntity, WatchlistEntry


# ─────────────────────────────────────────────────────────────────────
# Custom JSON encoder
# ─────────────────────────────────────────────────────────────────────

class _Encoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, ObjectId):
            return str(o)

        if isinstance(o, date):
            return o.isoformat()

        return super().default(o)


def _json_response(data: dict, status: int = 200) -> HttpResponse:
    return HttpResponse(
        content=json.dumps(data, cls=_Encoder),
        content_type='application/json',
        status=status,
    )


# ─────────────────────────────────────────────────────────────────────
# Number normalizer
# Handles: +919988776659 / 919988776659 / 9988776659.0 / 9988776659
# All produce: "9988776659"  (10-digit Indian mobile)
# ─────────────────────────────────────────────────────────────────────

def _normalize_number(raw: str) -> str:
    """
    Clean and normalize an Indian mobile number to 10 digits.
    Accepts:
        +919988776659   → 9988776659
        919988776659    → 9988776659
        9988776659.0    → 9988776659
        9988776659      → 9988776659
    Returns the cleaned 10-digit string, or raises ValueError if invalid.
    """
    raw = str(raw).strip()

    # Handle float-like strings  e.g. "9988776659.0"
    if '.' in raw:
        raw = raw.split('.')[0]

    # Remove all non-digit characters (+, spaces, dashes)
    digits = re.sub(r'\D', '', raw)

    # Strip Indian country code 91 if number is 12 digits
    if len(digits) == 12 and digits.startswith('91'):
        digits = digits[2:]

    # Strip leading 0 for rare "09988776659" format
    if len(digits) == 11 and digits.startswith('0'):
        digits = digits[1:]

    # if len(digits) != 10:
    #     raise ValueError(
    #         f"Invalid Indian mobile number '{raw}' — expected 10 digits, got {len(digits)}"
    #     )

    return digits


# ─────────────────────────────────────────────────────────────────────
# ID generators
# ─────────────────────────────────────────────────────────────────────

def _make_seq_id(group: str) -> str:
    """Stable entity ID: same group → same seq_id always."""
    return xxhash.xxh64(group.lower().strip()).hexdigest()


def _make_entry_id(seq_id: str, number: str) -> str:
    """
    Deterministic entry ID: seq_id + normalized number.
    Same group + same number → same ID → upsert instead of duplicate.
    """
    return xxhash.xxh64(seq_id + number).hexdigest()


# ─────────────────────────────────────────────────────────────────────
# Entity helpers
# ─────────────────────────────────────────────────────────────────────

def _get_or_create_entity(group: str, subgroup: str = '', description: str = '') -> WatchlistEntity:
    seq_id = _make_seq_id(group)
    entity = WatchlistEntity.objects(id=seq_id).first()
    if not entity:
        entity = WatchlistEntity(
            id          = seq_id,
            group       = group,
            subgroup    = subgroup,
            description = description,
        ).save()
    return entity


def _entry_to_dict(entry: WatchlistEntry, entity: WatchlistEntity = None) -> dict:
    return {
        "id":       str(entry.id)     or '',
        "seq_id":   str(entry.seq_id) or '',
        "group":    entity.group      if entity else '',
        "subgroup": entity.subgroup   if entity else '',
        "number":   entry.number      or '',
        "name":     entry.name        or '',
        "imei":     entry.imei        or '',
        "cell_id":  entry.cell_id     or '',
        "ip":       entry.ip          or '',
    }


def _enrich(entries) -> list:
    """Fetch all needed entities in one query — no N+1."""
    seq_ids = list({str(e.seq_id) for e in entries if e.seq_id})
    entity_map = {
        str(en.id): en
        for en in WatchlistEntity.objects(id__in=seq_ids)
    }
    return [_entry_to_dict(e, entity_map.get(str(e.seq_id))) for e in entries]


# ─────────────────────────────────────────────────────────────────────
# Core upsert logic
# Same (seq_id + number) exists → UPDATE
# Not found → CREATE
# ─────────────────────────────────────────────────────────────────────

def _upsert_entry(entity: WatchlistEntity, number: str, data: dict) -> tuple:
    """
    Returns (entry, created: bool)
      created=True  → new record inserted
      created=False → existing record updated
    """
    entry_id = _make_entry_id(str(entity.id), number)
    existing = WatchlistEntry.objects(id=entry_id).first()

    if existing:
        # UPDATE — overwrite all mutable fields
        existing.name    = data.get("name",   "").strip()
        existing.imei    = data.get("IMEI",   "").strip()
        existing.cell_id = data.get("Cellid", "").strip()
        existing.ip      = data.get("IP",     "").strip()
        existing.seq_id  = str(entity.id)
        existing.save()
        return existing, False
    else:
        # CREATE — new entry
        entry = WatchlistEntry(
            id      = entry_id,
            number  = number,
            name    = data.get("name",   "").strip(),
            imei    = data.get("IMEI",   "").strip(),
            cell_id = data.get("Cellid", "").strip(),
            ip      = data.get("IP",     "").strip(),
            seq_id  = str(entity.id),
        ).save()
        return entry, True


# ─────────────────────────────────────────────────────────────────────
# POST /watchlist/
# Accepts: { ... }  or  [ { ... }, { ... } ]
# ─────────────────────────────────────────────────────────────────────

@method_decorator(csrf_exempt, name='dispatch')
class AddToWatchlistView(View):

    def _parse_one(self, data: dict) -> tuple:
        if not isinstance(data, dict):
            raise ValueError("Each item must be a JSON object")

        # Validate + normalize number
        raw_number = data.get("Number", "")
        number = _normalize_number(raw_number)      # raises ValueError if invalid

        # Validate group
        group = data.get("group", "").strip()
        if not group:
            raise ValueError("'group' is required")

        # Resolve or create entity
        entity = _get_or_create_entity(
            group       = group,
            subgroup    = data.get("subgroup",    "").strip(),
            description = data.get("description", "").strip(),
        )

        # Upsert entry
        entry, created = _upsert_entry(entity, number, data)
        return entry, entity, created

    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return _json_response({"error": "Invalid JSON body"}, status=400)

        # Normalize to list
        if isinstance(data, dict):
            data = [data]
        elif isinstance(data, list):
            if not data:
                return _json_response({"error": "Empty list received"}, status=400)
        else:
            return _json_response({"error": "Expected a JSON object or array"}, status=400)

        inserted = []
        updated  = []
        errors   = []

        for i, item in enumerate(data):
            try:
                entry, entity, created = self._parse_one(item)
                record = _entry_to_dict(entry, entity)
                if created:
                    inserted.append(record)
                else:
                    updated.append(record)
            except ValueError as e:
                errors.append({"index": i, "error": str(e)})
            except Exception as e:
                errors.append({"index": i, "error": f"Unexpected error: {e}"})

        if not inserted and not updated and errors:
            return _json_response({"errors": errors}, status=400)

        response = {
            "message":  f"{len(inserted)} inserted, {len(updated)} updated",
            "inserted": inserted,
            "updated":  updated,
        }
        if errors:
            response["errors"] = errors

        return _json_response(response, status=201)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return _json_response({"error": "Only POST allowed"}, status=405)


# ─────────────────────────────────────────────────────────────────────
# GET /watchlist/list/
# GET /watchlist/list/?group=X  &subgroup=Y  &number=Z
# ─────────────────────────────────────────────────────────────────────

@method_decorator(csrf_exempt, name='dispatch')
class WatchlistListView(View):

    def get(self, request, *args, **kwargs):
        group_name    = request.GET.get('group',    '').strip()
        subgroup_name = request.GET.get('subgroup', '').strip()
        number        = request.GET.get('number',   '').strip()

        # Normalize number filter if provided
        if number:
            try:
                number = _normalize_number(number)
            except ValueError:
                pass    # use raw if normalization fails

        seq_ids = None
        if group_name or subgroup_name:
            eq = WatchlistEntity.objects()
            if group_name:
                eq = eq.filter(group__iexact=group_name)
            if subgroup_name:
                eq = eq.filter(subgroup__iexact=subgroup_name)
            seq_ids = [str(e.id) for e in eq]
            if not seq_ids:
                return _json_response({"count": 0, "entries": []})

        qs = WatchlistEntry.objects().order_by('-id')
        if seq_ids is not None:
            qs = qs.filter(seq_id__in=seq_ids)
        if number:
            qs = qs.filter(number=number)

        entries = list(qs)
        return _json_response({"count": len(entries), "entries": _enrich(entries)})


# ─────────────────────────────────────────────────────────────────────
# GET    /watchlist/<id>/
# DELETE /watchlist/<id>/
# ─────────────────────────────────────────────────────────────────────

@method_decorator(csrf_exempt, name='dispatch')
class WatchlistDetailView(View):

    def _get_entry(self, pk):
        try:
            return WatchlistEntry.objects.get(id=pk)
        except Exception:
            return None

    def get(self, request, pk, *args, **kwargs):
        entry = self._get_entry(pk)
        if not entry:
            return _json_response({"error": "Entry not found"}, status=404)
        entity = WatchlistEntity.objects(id=str(entry.seq_id)).first()
        return _json_response(_entry_to_dict(entry, entity))

    def delete(self, request, pk, *args, **kwargs):
        entry = self._get_entry(pk)
        if not entry:
            return _json_response({"error": "Entry not found"}, status=404)
        entry.delete()
        return _json_response({"message": f"Entry {pk} deleted"})


# ─────────────────────────────────────────────────────────────────────
# GET /watchlist/groups/
# ─────────────────────────────────────────────────────────────────────

@method_decorator(csrf_exempt, name='dispatch')
class WatchlistGroupListView(View):

    def get(self, request, *args, **kwargs):
        entities = WatchlistEntity.objects().order_by('group', 'subgroup')
        data = [
            {
                "seq_id":      str(e.id),
                "group":       e.group       or '',
                "subgroup":    e.subgroup    or '',
                "description": e.description or '',
                "created_at":  e.created_at.isoformat() if e.created_at else '',
            }
            for e in entities
        ]
        return _json_response({"count": len(data), "entities": data})