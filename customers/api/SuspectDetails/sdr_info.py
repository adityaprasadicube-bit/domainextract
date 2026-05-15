import json
import os
from django.conf import settings
from mongoengine import get_db


class SuspectDetails:
    def __init__(self, numbers: list):
        # map(str) ensures all numbers from your tower dump match the DB string type
        self.numbers = list(set(map(str, numbers)))
        self.master_data = {}
        # Add this mapping at the top of the class or as a constant
        self.WATCHLIST_FIELD_ALIASES = {
            "User_Name": "FullName",
            "Address":"LocalAddress",

            # add more mappings here as needed
        }

        self.config_path = os.path.join(settings.BASE_DIR, "api", "data", "column_config.json")
        self.column_config = self._load_config()

    def _load_config(self):
        try:
            with open(self.config_path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def fetch_all_mapped_details(self):
        # Step 1: Query Watchlist using "Number" field
        found_in_watch = self._fetch_from_collection(
            alias="watchlist_db",
            collection_name="WatchList_data",
            key_field="Number",
            source_tag="watchlist"
        )

        # Step 2: Only query SDR for numbers NOT found in Watchlist
        remaining = [n for n in self.numbers if n not in found_in_watch]
        if remaining:
            self._fetch_from_sdr(targets=remaining)

        return self.master_data

    def _fetch_from_sdr(self, targets):
        db = get_db(alias="sdr_db")
        col = db["Subscriber_Master"]

        str_list = [str(n) for n in targets]
        int_list = []
        for n in targets:
            try:
                int_list.append(int(n))
            except (ValueError, TypeError):
                pass

        projection = {"_id": 0, "PhoneNumber": 1}
        for field in self.column_config.get("SDR", []):
            projection[field] = 1

        cursor = col.find({"PhoneNumber": {"$in": str_list + int_list}}, projection)

        for doc in cursor:
            num = str(doc.pop("PhoneNumber", ""))
            if num:
                doc['record_source'] = 'sdr'
                self.master_data[num] = doc



    def _fetch_from_collection(self, alias, collection_name, key_field, source_tag, targets=None):
        db = get_db(alias=alias)
        col = db[collection_name]

        search_list = targets if targets is not None else self.numbers

        str_list = [str(n) for n in search_list]
        int_list = []
        for n in search_list:
            try:
                int_list.append(int(n))
            except (ValueError, TypeError):
                pass

        cursor = col.find({key_field: {"$in": str_list + int_list}})

        found_keys = set()
        for doc in cursor:
            raw_key = doc.get(key_field)
            num = str(raw_key) if raw_key is not None else ""
            if num:
                doc.pop("_id", None)

                # Apply field aliases for watchlist
                if source_tag == "watchlist":
                    aliased_doc = {}
                    for k, v in doc.items():
                        mapped_key = self.WATCHLIST_FIELD_ALIASES.get(k, k)  # rename or keep original
                        aliased_doc[mapped_key] = v
                    doc = aliased_doc

                doc['record_source'] = source_tag
                self.master_data[num] = doc
                found_keys.add(num)
        return found_keys