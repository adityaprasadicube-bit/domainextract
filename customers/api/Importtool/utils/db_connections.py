import configparser

from mongoengine import get_db
from pymongo import MongoClient, UpdateOne
from pymongo.errors import BulkWriteError
import os
import sys
from typing import List, Dict

# ---------- Resource Path Helper ----------
def resource_path(relative_path):
    """ Get absolute path to resource (works for dev and PyInstaller) """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)
import os
import json

base_dir = os.path.dirname(os.path.abspath(__file__))
json_path = os.path.join(
    base_dir,       # customers/api/Importtool/utils
    "..",           # customers/api/Importtool
    "..",           # customers/api
    "data",
    "config.ini"
)





config_path = resource_path(json_path)

# ---------- Load Config ----------
config = configparser.ConfigParser()


if not os.path.exists(config_path):
    raise FileNotFoundError(f"Config file not found at: {config_path}")

config.read(config_path)

if "database" not in config:
    raise KeyError("The 'database' section is missing from config.ini")



# ---------- Insert / Update ----------
# Enhanced Connection.py bulk_insert function with detailed logging

def bulk_insert(database_name: str, collection_name: str, records: List[Dict], upload_type: str):
    """
    ENHANCED: Added comprehensive logging to debug insert issues
    """


    try:
        db = get_db(database_name)
        #print(f"[BULK_INSERT] ✅ Database '{database_name}' accessed")

        collection = db[collection_name]
        #print(f"[BULK_INSERT] ✅ Collection '{collection_name}' accessed")

    except Exception as e:
        print(f"[BULK_INSERT] ❌ Failed to access database/collection: {e}")
        return {"inserted": 0, "duplicates": 0, "error": str(e)}

    if upload_type == "insert":
        #print(f"[BULK_INSERT] Performing INSERT operation...")
        try:
            # Check if records have _id field

            result = collection.insert_many(records, ordered=False)

            inserted_count = len(result.inserted_ids)
            #print(f"[BULK_INSERT] ✅ Insert successful!")
            #print(f"[BULK_INSERT] Inserted IDs count: {inserted_count}")

            return {"inserted": inserted_count, "duplicates": 0}

        except BulkWriteError as e:
            #print(f"[BULK_INSERT] ⚠️ BulkWriteError occurred (this is normal for duplicates)")

            # Extract details
            write_errors = e.details.get("writeErrors", [])
            n_inserted = e.details.get("nInserted", 0)

            #print(f"[BULK_INSERT] Successfully inserted: {n_inserted}")
            #print(f"[BULK_INSERT] Write errors: {len(write_errors)}")

            # Count duplicates (error code 11000)
            duplicate_count = sum(
                1 for err in write_errors if err.get("code") == 11000
            )

            #print(f"[BULK_INSERT] Duplicate key errors: {duplicate_count}")

            # Check for other errors
            other_errors = [err for err in write_errors if err.get("code") != 11000]

            return {"inserted": n_inserted, "duplicates": duplicate_count}

        except Exception as e:
            print(f"[BULK_INSERT] ❌ Unexpected error during insert: {e}")
            print(f"[BULK_INSERT] Error type: {type(e).__name__}")
            import traceback
            traceback.print_exc()
            return {"inserted": 0, "duplicates": 0, "error": str(e)}

    elif upload_type == "update":
        #print(f"[BULK_INSERT] Performing UPDATE operation...")
        try:
            bulk_operations = [
                UpdateOne({"_id": record["_id"]}, {"$set": record}, upsert=True)
                for record in records
            ]

            if not bulk_operations:
                #print(f"[BULK_INSERT] ⚠️ No bulk operations to perform")
                return {"matched": 0, "modified": 0, "upserted": 0}

            #print(f"[BULK_INSERT] Prepared {len(bulk_operations)} update operations")
            #print(f"[BULK_INSERT] Executing bulk_write...")

            result = collection.bulk_write(bulk_operations)

            #print(f"[BULK_INSERT] ✅ Update successful!")
            #print(f"[BULK_INSERT] Matched: {result.matched_count}")
            #print(f"[BULK_INSERT] Modified: {result.modified_count}")
            #print(f"[BULK_INSERT] Upserted: {len(result.upserted_ids)}")

            return {
                "matched": result.matched_count,
                "modified": result.modified_count,
                "upserted": len(result.upserted_ids),
            }

        except Exception as e:
            #print(f"[BULK_INSERT] ❌ Error during update: {e}")
            import traceback
            traceback.print_exc()
            return {"matched": 0, "modified": 0, "upserted": 0, "error": str(e)}

    else:
        #print(f"[BULK_INSERT] ❌ Invalid upload_type: {upload_type}")
        return {"error": f"Invalid upload_type: {upload_type}"}

    #print(f"[BULK_INSERT] Complete\n")
    return {}
# ---------- Fetch Single Record ----------
def fetch_record(database_name: str, collection_name: str, query: str) -> Dict:
    db = get_db(database_name)
    collection = db[collection_name]
    record = collection.find_one({"mobile": query})
    return record or {}
