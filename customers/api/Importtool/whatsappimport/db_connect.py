import os
import configparser
from pymongo import MongoClient, UpdateOne
from pymongo.errors import BulkWriteError
import sys
from typing import List, Dict


# ---------- Resource Path Helper ----------
def resource_path(relative_path):
    """ Get absolute path to resource (works for dev and PyInstaller) """
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


# ---------- MongoDB Connection ----------
MONGO_HOST = os.environ.get('MONGO_HOST')
MONGO_PORT = os.environ.get('MONGO_PORT', 27017)

if MONGO_HOST:
    mongo_uri = f"mongodb://{MONGO_HOST}:{MONGO_PORT}/"
    print(f"[DB_CONNECT] Using MongoDB from env: {MONGO_HOST}:{MONGO_PORT}")
else:
    import os

    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(
        base_dir,
        "..",
        "..",
        "data",
        "config.ini"
    )

    config_path = resource_path(config_path)

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found at: {config_path}")

    config = configparser.ConfigParser()
    config.read(config_path)

    if "database" not in config:
        raise KeyError("The 'database' section is missing from config.ini")

    db_host = config["database"].get("host", "localhost")
    db_port = config["database"].getint("port", 27017)

    mongo_uri = f"mongodb://{db_host}:{db_port}/"
    print(f"[DB_CONNECT] Using MongoDB from config: {db_host}:{db_port}")


# ---------- Create MongoDB Client ----------
try:
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    client.server_info()
    print(f"[DB_CONNECT] ✅ Connected to MongoDB at {mongo_uri}")
except Exception as e:
    print(f"[DB_CONNECT] ❌ Failed to connect to MongoDB: {e}")
    print(f"[DB_CONNECT] URI: {mongo_uri}")
    client = None


# ---------- Get Collection Helper ----------
def get_collection(database_name: str, collection_name: str):
    """
    Returns a raw PyMongo collection object for direct operations.
    Used when fine-grained control is needed (e.g., seq_id array updates on duplicates).
    """
    if client is None:
        raise ConnectionError("[GET_COLLECTION] ❌ MongoDB client not initialized")
    return client[database_name][collection_name]


# ---------- seq_id normaliser ----------
def _normalise_seq_id(seq_id_value) -> list:
    """
    Always returns a plain list of non-empty strings.

    Handles every storage shape that may exist in incoming records:
      - None / missing          → []
      - ""  (empty string)      → []
      - "abc"  (plain string)   → ["abc"]
      - ["abc", "def"]  (list)  → ["abc", "def"]
      - mixed list with None    → filtered to non-empty strings only
    """
    if not seq_id_value:
        return []
    if isinstance(seq_id_value, str):
        return [seq_id_value] if seq_id_value.strip() else []
    if isinstance(seq_id_value, (list, tuple)):
        return [str(s) for s in seq_id_value if s and str(s).strip()]
    return [str(seq_id_value)]


# ---------- Insert / Update ----------
def bulk_insert(database_name: str, collection_name: str, records: List[Dict], upload_type: str):
    """
    Bulk insert or update records into MongoDB.

    INSERT behaviour
    ────────────────
    • New records  → inserted as-is; their seq_id field is normalised to a list
                     before insertion so every document always stores seq_id as
                     an array (makes future $addToSet operations safe).
    • Duplicate _id (code 11000)
        → The existing document's seq_id array is updated with every new seq_id
          value from the incoming record using $addToSet + $each, so no value
          is ever lost and there are no duplicates in the array.
        → This happens even when the incoming seq_id is a plain string, an
          empty value, or already an array.

    UPDATE behaviour
    ────────────────
    • Standard upsert via bulk_write (unchanged).
    """
    if client is None:
        print(f"[BULK_INSERT] ❌ MongoDB client not initialized")
        return {"inserted": 0, "duplicates": 0, "error": "MongoDB not connected"}

    try:
        db         = client[database_name]
        collection = db[collection_name]

        # ── INSERT ──────────────────────────────────────────────────────
        if upload_type == "insert":

            # Nexus collections are summary/index records that use _id as the
            # nexus_id itself – they must never accumulate a seq_id array.
            NEXUS_COLLECTIONS = {"WhatsAppNexus", "WhatsAppInfoNexus"}
            is_nexus = collection_name in NEXUS_COLLECTIONS

            # Normalise seq_id to list on every non-nexus record before
            # inserting, so the field is always stored as an array in MongoDB.
            prepared = []
            for rec in records:
                r = dict(rec)  # shallow copy – don't mutate caller's data
                if not is_nexus:
                    r["seq_id"] = _normalise_seq_id(r.get("seq_id"))
                prepared.append(r)

            # Build a lookup so we can quickly find seq_ids for duplicate ids.
            # For nexus collections this will be empty (no seq_id updates needed).
            records_by_id: Dict[str, list] = {
                r["_id"]: r.get("seq_id", [])
                for r in prepared
                if "_id" in r and not is_nexus
            }

            try:
                result = collection.insert_many(prepared, ordered=False)
                inserted_count = len(result.inserted_ids)
                print(f"[BULK_INSERT] ✅ {database_name}.{collection_name}: "
                      f"inserted={inserted_count}, duplicates=0")
                return {"inserted": inserted_count, "duplicates": 0}

            except BulkWriteError as bwe:
                write_errors   = bwe.details.get("writeErrors", [])
                n_inserted     = bwe.details.get("nInserted", 0)
                duplicate_count = 0

                for err in write_errors:
                    # Only handle duplicate-key errors (code 11000)
                    if err.get("code") != 11000:
                        print(f"[BULK_INSERT] ⚠️  Non-duplicate write error: {err}")
                        continue

                    duplicate_count += 1

                    # Resolve the _id of the duplicate document
                    dup_id = (
                        err.get("keyValue", {}).get("_id")
                        or err.get("op", {}).get("_id")
                    )
                    if dup_id is None:
                        continue

                    # Nexus collections: no seq_id to maintain – just count the dupe.
                    if is_nexus:
                        try:
                            incoming_record = next(
                                (r for r in prepared if r.get("_id") == dup_id),
                                None
                            )

                            if not incoming_record:
                                continue

                            existing_doc = collection.find_one({"_id": dup_id})
                            if not existing_doc:
                                continue

                            old_from = existing_doc.get("FromDate")
                            old_to = existing_doc.get("ToDate")

                            new_from = incoming_record.get("FromDate")
                            new_to = incoming_record.get("ToDate")

                            updated_from = (
                                min(old_from, new_from)
                                if old_from and new_from
                                else new_from or old_from
                            )

                            updated_to = (
                                max(old_to, new_to)
                                if old_to and new_to
                                else new_to or old_to
                            )

                            collection.update_one(
                                {"_id": dup_id},
                                {
                                    "$set": {
                                        "FromDate": updated_from,
                                        "ToDate": updated_to
                                    },
                                    "$unset": {
                                        "fromdate": "",
                                        "todate": ""
                                    }
                                }
                            )

                        except Exception as nexus_err:
                            print(f"[BULK_INSERT] ⚠️ Nexus date merge failed: {nexus_err}")

                        continue

                    # Gather the new seq_id values from the incoming record
                    new_seq_ids = records_by_id.get(dup_id, [])
                    if not new_seq_ids:
                        # Nothing to append – but still convert the existing
                        # field to an array if it is still stored as a string
                        _ensure_seq_id_is_array(collection, dup_id)
                        continue

                    # First make sure the existing doc has seq_id as an array,
                    # then atomically add the new values (no duplicates).
                    _ensure_seq_id_is_array(collection, dup_id)
                    try:
                        collection.update_one(
                            {"_id": dup_id},
                            {"$addToSet": {"seq_id": {"$each": new_seq_ids}}}
                        )
                        # print(f"[BULK_INSERT] 🔄 Duplicate _id={dup_id}: "
                        #       f"appended seq_ids {new_seq_ids}")
                    except Exception as upd_err:
                        print(f"[BULK_INSERT] ⚠️  seq_id update failed for "
                              f"_id={dup_id}: {upd_err}")

                print(f"[BULK_INSERT] ✅ {database_name}.{collection_name}: "
                      f"inserted={n_inserted}, duplicates={duplicate_count}")
                return {"inserted": n_inserted, "duplicates": duplicate_count}

        # ── UPDATE ──────────────────────────────────────────────────────
        elif upload_type == "update":
            try:
                bulk_operations = [
                    UpdateOne({"_id": record["_id"]}, {"$set": record}, upsert=True)
                    for record in records
                ]

                if not bulk_operations:
                    return {"matched": 0, "modified": 0, "upserted": 0}

                result = collection.bulk_write(bulk_operations)
                return {
                    "matched":  result.matched_count,
                    "modified": result.modified_count,
                    "upserted": len(result.upserted_ids),
                }

            except Exception as e:
                print(f"[BULK_INSERT] ❌ Update error: {e}")
                return {"matched": 0, "modified": 0, "upserted": 0, "error": str(e)}

        else:
            return {"error": f"Invalid upload_type: {upload_type}"}

    except Exception as e:
        print(f"[BULK_INSERT] ❌ Unexpected error: {e}")
        return {"inserted": 0, "duplicates": 0, "error": str(e)}


# ---------- Internal helper ----------
def _ensure_seq_id_is_array(collection, doc_id):
    """
    If the document's seq_id field is stored as a plain string (legacy data),
    convert it to a single-element array in-place so that $addToSet works
    correctly on subsequent calls.

    If seq_id is already an array, or missing, this is a no-op.
    """
    try:
        doc = collection.find_one({"_id": doc_id}, {"seq_id": 1})
        if doc and isinstance(doc.get("seq_id"), str):
            existing = doc["seq_id"]
            new_val  = [existing] if existing.strip() else []
            collection.update_one(
                {"_id": doc_id},
                {"$set": {"seq_id": new_val}}
            )
            #print(f"[BULK_INSERT] 🔧 Migrated seq_id string→array for _id={doc_id}")
    except Exception as e:
        print(f"[BULK_INSERT] ⚠️  _ensure_seq_id_is_array failed for _id={doc_id}: {e}")


def fetch_record(database_name: str, collection_name: str, query: str) -> Dict:
    if client is None:
        return {}
    db = client[database_name]
    collection = db[collection_name]
    record = collection.find_one({"mobile": query})
    return record or {}