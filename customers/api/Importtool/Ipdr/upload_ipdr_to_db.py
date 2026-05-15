"""
upload_ipdr_to_db.py - MODIFIED FOR OPTION 1: Multi-valued seq_id
Aligned with CDR implementation for consistency
"""

import ipaddress
from datetime import datetime, date
import xxhash
from typing import List, Dict
import re

import polars as pl
from mongoengine import get_db
from pymongo.errors import BulkWriteError

from .Ipdr_validate import extract_ip
from ..models import IPdrNexus, CrimeRecord, UserRecord
from ..utils.db_connections import bulk_insert
from ..utils.user_data import UserDetails


# Pre-compile type checking (same as CDR)
SIMPLE_TYPES = frozenset([int, float, str, bool, type(None)])
IGNORE_VALUES = {None, "", "-", "0", 0, "null", "NULL", "None", "--", " ", "None None None"}

# Per-process client cache
_USER_ID_CACHE = None


def get_user_id():
    """Cache user ID globally"""
    global _USER_ID_CACHE
    if _USER_ID_CACHE is not None:
        return _USER_ID_CACHE
    user_inform = UserDetails()
    UserId = user_inform.user()
    _USER_ID_CACHE = UserId
    return UserId


def set_user_id(user_id):
    """Set user ID (callable from Django views)"""
    global _USER_ID_CACHE
    _USER_ID_CACHE = user_id


# ============================================================================
# DETECTION HELPER FUNCTIONS (UNCHANGED)
# ============================================================================

def _extract_ip_from_row(cells: list) -> str:
    """Extract valid IP address from row cells"""
    for cell in cells:
        if not cell:
            continue

        cell_str = str(cell).strip()

        # Try direct IP parsing
        try:
            ip_obj = ipaddress.ip_address(cell_str)
            return str(ip_obj)
        except ValueError:
            pass

        # Try IPv4 pattern: xxx.xxx.xxx.xxx
        ipv4_pattern = r'\b(?:\d{1,3}\.){3}\d{1,3}\b'
        ipv4_match = re.search(ipv4_pattern, cell_str)
        if ipv4_match:
            try:
                ip_obj = ipaddress.ip_address(ipv4_match.group())
                return str(ip_obj)
            except ValueError:
                pass

        # Split by spaces and try each word
        for word in cell_str.split():
            word = word.strip('"\',.:;()[]{}')
            try:
                ip_obj = ipaddress.ip_address(word)
                return str(ip_obj)
            except ValueError:
                pass

    return None


def _detect_from_top_rows(top_rows_dict: dict) -> tuple:
    """Detect RecordType and IPDR/CellID from top rows (header metadata)"""
    recordtype = "Unknown"
    detected_value = None

    if not top_rows_dict:
        return recordtype, detected_value

    try:
        clean_data = {col: s.drop_nulls().to_list() if hasattr(s, 'drop_nulls') else s
                      for col, s in top_rows_dict.items()}
    except:
        return recordtype, detected_value

    for key, values in clean_data.items():
        if not isinstance(values, (list, tuple)):
            values = [values]

        for value in values:
            if not value:
                continue

            cleaned_value = str(value).replace(" ", "").lower()

            if "cellid" in cleaned_value:
                recordtype = "Tower"
                match = re.search(r"CELL ID\s*:\s*([0-9\-]+)", str(value), re.IGNORECASE)
                if match:
                    detected_value = re.sub(r'\D', '', match.group(1))
                break

            elif "mobile" in cleaned_value:
                recordtype = "Mobile"
                match = re.search(r"(\+?\d{10,15})", str(value))
                if match:
                    detected_value = match.group(1)
                break

            elif "destinationip" in cleaned_value:
                recordtype = "Destination IP"
                match = re.search(r"\b(?:(?:\d{1,3}\.){3}\d{1,3}|[A-Fa-f0-9:]{2,})\b", str(value))
                if match:
                    detected_value = match.group(0)
                break

            elif "sourceip" in cleaned_value:
                recordtype = "Source IP"
                match = re.search(r"\b(?:(?:\d{1,3}\.){3}\d{1,3}|[A-Fa-f0-9:]{2,})\b", str(value))
                if match:
                    detected_value = match.group(0)
                break

            elif "publicip" in cleaned_value or "translatedip" in cleaned_value:
                recordtype = "Public IP"
                match = re.search(r"\b(?:(?:\d{1,3}\.){3}\d{1,3}|[A-Fa-f0-9:]{2,})\b", str(value))
                if match:
                    detected_value = match.group(0)
                break

            elif "privateip" in cleaned_value:
                recordtype = "Private IP"
                match = re.search(r"\b(?:(?:\d{1,3}\.){3}\d{1,3}|[A-Fa-f0-9:]{2,})\b", str(value))
                if match:
                    detected_value = match.group(0)
                break

        if recordtype != "Unknown":
            break

    return recordtype, detected_value


def _detect_from_filename(seq_data: dict, filename: str, top_rows_dict: dict):
    """Detect record type from filename based on unique single-value sets in seq_data."""
    checks = {
        'IMEI': seq_data.get('imei', set()),
        'Tower': seq_data.get('tower', set()),
        'Public IP': seq_data.get('translatedip', set()),
        'Destination IP': seq_data.get('dip', set()),
        'Source IP': seq_data.get('sip', set()),
        'Mobile': seq_data.get('number', set()),
        'Translated Port': seq_data.get('pport', set()),
        'Destination Port': seq_data.get('dport', set()),
        'Source Port': seq_data.get('sport', set())
    }

    record_type = 'Unknown'
    detected_value = None

    if not filename:
        return record_type, detected_value

    filename_lower = str(filename).lower().replace(" ", "")

    try:
        for type_name, data_set in checks.items():
            if not isinstance(data_set, (set, list)) or len(data_set) != 1:
                continue

            original_value = next(iter(data_set))
            unique_value = str(original_value).strip().lower().replace(" ", "")

            if unique_value and unique_value in filename_lower:
                record_type = type_name
                detected_value = str(original_value).strip()
                break

    except Exception as e:
        print(f"[FILENAME-DETECT] Error detecting record type: {e}")

    return record_type, detected_value


def _detect_record_type_seqdata(seq_data: dict):
    """Detect record type from seq_data"""
    recordtype = "Unknown"
    ipdr_value = None

    if len(seq_data.get("number", set())) == 1:
        return "Mobile", next(iter(seq_data["number"]))

    elif len(seq_data.get("imei", set())) == 1:
        return "IMEI", next(iter(seq_data["imei"]))

    elif len(seq_data.get("imsi", set())) == 1:
        return "IMSI", next(iter(seq_data["imsi"]))

    elif len(seq_data.get("tower", set())) == 1:
        return "Tower", next(iter(seq_data["tower"]))

    elif len(seq_data.get("sip", set())) == 1:
        return "Source IP", next(iter(seq_data["sip"]))

    elif len(seq_data.get("dip", set())) == 1:
        return "Destination IP", next(iter(seq_data["dip"]))

    elif len(seq_data.get("translatedip", set())) == 1:
        return "Public IP", next(iter(seq_data["translatedip"]))

    return recordtype, ipdr_value


def _detect_record_type(seq_data: dict, filename: str, top_rows: dict = None) -> dict:
    """MASTER DETECTION FUNCTION"""
    print(f"\n{'=' * 70}")
    print(f"[DETECTION] 🎯 Starting RecordType Detection for: {filename}")
    print(f"{'=' * 70}")

    record_type = "Unknown"
    ipdr_value = None

    # PRIORITY 1: Top rows
    if top_rows:
        record_type, ipdr_value = _detect_from_top_rows(top_rows)
        if record_type != "Unknown":
            print(f"[DETECTION] ✅ Detected from top rows: {record_type}")

    # PRIORITY 2: Filename
    if record_type == "Unknown":
        record_type, ipdr_value = _detect_from_filename(seq_data, filename, top_rows)
        if record_type != "Unknown":
            print(f"[DETECTION] ✅ Detected from filename: {record_type}")

    # PRIORITY 3: Seq data
    if record_type == "Unknown":
        record_type, ipdr_value = _detect_record_type_seqdata(seq_data)
        if record_type != "Unknown":
            print(f"[DETECTION] ✅ Detected from seq_data: {record_type}")

    # Store only filename (not full path)
    if filename:
        seq_data['FileName'] = filename

    # Update seq_data with detected values
    if record_type != "Unknown":
        seq_data['RecordType'] = record_type

        if ipdr_value:
            if 'IP' in record_type:
                seq_data['IPDR'] = extract_ip(str(ipdr_value))
            else:
                seq_data['IPDR'] = str(ipdr_value)
        else:
            seq_data['IPDR'] = 'Unknown'

        print(f"[DETECTION] ✅ FINAL: {seq_data['RecordType']} | IPDR: {seq_data['IPDR']}")
    else:
        seq_data['RecordType'] = 'Unknown'
        seq_data['IPDR'] = 'Unknown'
        print(f"[DETECTION] ⚠️ FINAL (default): Unknown | Unknown")

    return seq_data


# ============================================================================
# MAIN INSERT FUNCTION - MODIFIED FOR OPTION 1 (ALIGNED WITH CDR)
# ============================================================================

def insert_ipdr_file(
    df: pl.DataFrame,
    crime_name: str,
    area_location: str,
    filename: str,
    top_rows: dict,
    seq_data: dict
):
    """
    OPTION 1: Multi-valued seq_id implementation for IPDR
    Aligned with CDR implementation

    Changes:
    1. Generate _id WITHOUT nexus_hash (content-based only)
    2. Store seq_id as array: [nexus_hash]
    3. On duplicates, UPDATE existing records with $addToSet
    """

    if top_rows is None:
        top_rows = {}

    UserId = get_user_id()

    print(f"\n[OPTION 1 IPDR] Processing file: {filename}")
    print(f"[OPTION 1 IPDR] Crime: {crime_name}, Area: {area_location}")

    # Rename columns to match model field names
    rename_map = {
        "msisdn": "MSISDN",
        "msisdn_code": "MSISDN_code",
        "first_cgi": "TowerID",
        "imei": "IMEI",
        "imei_tac": "IMEI_TAC",
        "imsi": "IMSI",
        "imsi_code": "IMSI_CODE",
        "source_ip": "Source_ip",
        "translated_ip": "Translated_ip",
        "destination_ip": "Destination_ip",
        "sdatetime": "SDateTime",
        "edatetime": "EDateTime",
        "duration": "Duration",
        "data_uploaded": "DataUpload",
        "data_downloaded": "DataDownload",
        "source_port": "Source_port",
        "translated_port": "Translated_port",
        "destination_port": "Destination_port",
        "username": "NameOfPersonOrOrganization",
        "user_address": "AddressOfPersonOrOrganization",
        "user_contact": "ContactNo",
        "roaming": "Roaming",
        "sdate": "SDate",
        "edate": "EDate"
    }

    # Drop existing columns that conflict
    for key, value in rename_map.items():
        if value in df.columns:
            df = df.drop(value)

    # Rename columns
    safe_rename_map = {k: v for k, v in rename_map.items() if k in df.columns}
    df = df.rename(safe_rename_map)

    # Convert DataFrame to list of dictionaries
    raw_dicts = df.to_dicts()

    if not raw_dicts:
        return {"inserted": 0, "duplicates": 0, "updated": 0, "seqdata": {}}

    # 🎯 DETECT RECORD TYPE
    seq_data = _detect_record_type(seq_data, filename, top_rows)

    # Normalize IP addresses
    if "ip" in str(seq_data.get("RecordType", "")).lower():
        if isinstance(seq_data.get("IPDR"), (ipaddress.IPv4Address, ipaddress.IPv6Address)):
            seq_data["IPDR"] = str(seq_data["IPDR"])
        elif seq_data["IPDR"] in ['Unknown', '', None]:
            seq_data["IPDR"] = 'Unknown'

    # ============================================================
    # Generate nexus_hash (SAME AS CDR LOGIC)
    # ============================================================
    ipdr_or_identifier = seq_data.get('IPDR', 'UNKNOWN')

    ipdr_nexus_id = f"{crime_name}{area_location}{UserId}{ipdr_or_identifier}"
    nexus_hash = xxhash.xxh64(ipdr_nexus_id.lower()).hexdigest()

    crime_hash = xxhash.xxh64(f"{crime_name}{area_location}".lower()).hexdigest()
    user_hash = xxhash.xxh64(str(UserId).lower()).hexdigest()

    print(f"[OPTION 1 IPDR] Generated nexus_hash: {nexus_hash[:16]}...")
    print(f"[OPTION 1 IPDR] Record Type: {seq_data.get('RecordType', 'Unknown')}")
    print(f"[OPTION 1 IPDR] IPDR Identifier: {ipdr_or_identifier}")

    # ============================================================
    # CRITICAL CHANGE: Generate _id WITHOUT nexus_hash
    # This makes _id content-based only (same as CDR)
    # ============================================================
    # id_parts_cols = ['SDateTime', 'Duration', 'MSISDN', 'Source_ip', 'Destination_ip',
    #                  'Translated_ip', 'IMEI', 'IMSI', 'TowerID']

    id_parts_cols = rename_map.values()

    # Build ID parts list
    id_parts = []
    for col_name in id_parts_cols:
        if col_name in df.columns:
            id_parts.append(pl.col(col_name).cast(pl.Utf8).fill_null("").str.to_lowercase())
        else:
            id_parts.append(pl.lit(""))

    # NOTE: We DON'T add nexus_hash here (different from original)
    # This makes _id content-based only

    df = df.with_columns([
        pl.concat_str(id_parts, separator="").alias("id_string")
    ])

    id_strings = df.select("id_string").to_series().to_list()
    hashed_ids = [xxhash.xxh64(s.encode()).hexdigest() for s in id_strings]

    df = df.with_columns([
        pl.Series("_id", hashed_ids)
    ]).drop("id_string")

    print(f"[OPTION 1 IPDR] Generated {len(hashed_ids)} content-based record IDs")

    # ============================================================
    # CONVERT TO DICTS & CLEAN (SAME AS CDR)
    # ============================================================
    raw_dicts = df.to_dicts()
    dict_data_new = []

    for record in raw_dicts:
        cleaned_record = {}

        for k, v in record.items():
            # Convert date to datetime
            if isinstance(v, date) and not isinstance(v, datetime):
                cleaned_record[k] = datetime.combine(v, datetime.min.time())
            elif isinstance(v, datetime):
                cleaned_record[k] = v
            elif type(v) in SIMPLE_TYPES:
                cleaned_record[k] = v
            elif hasattr(v, 'isoformat'):
                cleaned_record[k] = v.isoformat()
            else:
                cleaned_record[k] = v

            # Remove ignore values
            if v in IGNORE_VALUES:
                cleaned_record.pop(k, None)

        # Convert port numbers to integers
        for port_field in ["Destination_port", "Translated_port", "Source_port"]:
            if port_field in cleaned_record:
                val = cleaned_record[port_field]
                if isinstance(val, str) and val.isnumeric():
                    cleaned_record[port_field] = int(val)

        # Normalize MSISDN
        if "MSISDN" in cleaned_record:
            msisdn = str(cleaned_record["MSISDN"]).strip()
            if msisdn.isdigit():
                if msisdn.startswith("91") and len(msisdn) > 10:
                    msisdn = msisdn[2:]
                elif msisdn.startswith("0") and len(msisdn) == 11:
                    msisdn = msisdn[1:]
            cleaned_record["MSISDN"] = msisdn

        # Remove redundant fields based on record type
        cleaned_record = _remove_redundant_fields(cleaned_record, seq_data.get('RecordType', ''))

        # ============================================================
        # CRITICAL CHANGE: Add seq_id as ARRAY with single element
        # ============================================================
        cleaned_record['seq_id'] = [nexus_hash]  # ← ARRAY, not string!

        dict_data_new.append(cleaned_record)

    print(f"[OPTION 1 IPDR] Prepared {len(dict_data_new)} records with seq_id arrays")

    # ============================================================
    # TRY BULK INSERT
    # ============================================================
    db = get_db('ipdr_db')
    collection = db['IPDetailRecords']

    inserted_count = 0
    duplicate_ids = []

    try:
        print(f"[OPTION 1 IPDR] Attempting bulk insert of {len(dict_data_new)} records...")
        result = collection.insert_many(dict_data_new, ordered=False)
        inserted_count = len(result.inserted_ids)
        print(f"[OPTION 1 IPDR] ✅ Successfully inserted {inserted_count} NEW records")

    except BulkWriteError as e:
        details = e.details
        inserted_count = details.get('nInserted', 0)
        write_errors = details.get('writeErrors', [])

        print(f"[OPTION 1 IPDR] ⚠️  Bulk insert completed with errors:")
        print(f"[OPTION 1 IPDR]     - Inserted: {inserted_count}")
        print(f"[OPTION 1 IPDR]     - Errors: {len(write_errors)}")

        # Extract duplicate record IDs
        for error in write_errors:
            if error.get('code') == 11000:  # Duplicate key error
                duplicate_id = error['op']['_id']
                duplicate_ids.append(duplicate_id)

        print(f"[OPTION 1 IPDR] 🔍 Found {len(duplicate_ids)} DUPLICATE records")

    except Exception as e:
        print(f"[OPTION 1 IPDR] ❌ Unexpected error during insert: {e}")
        import traceback
        traceback.print_exc()

    # ============================================================
    # UPDATE DUPLICATE RECORDS - Add seq_id to Array
    # ============================================================
    updated_count = 0

    if duplicate_ids:
        print(f"\n[OPTION 1 IPDR] 🔄 Updating {len(duplicate_ids)} duplicate records...")
        print(f"[OPTION 1 IPDR]    Adding seq_id '{nexus_hash[:16]}...' to existing arrays")

        try:
            # $addToSet: Add to array only if not already present
            update_result = collection.update_many(
                {"_id": {"$in": duplicate_ids}},
                {"$addToSet": {"seq_id": nexus_hash}}
            )

            updated_count = update_result.modified_count

            print(f"[OPTION 1 IPDR] ✅ Updated {updated_count} records")
            print(f"[OPTION 1 IPDR]    These records now belong to multiple cases")

            # Show example
            if duplicate_ids:
                example = collection.find_one({"_id": duplicate_ids[0]})
                if example:
                    print(f"\n[OPTION 1 IPDR] 📋 Example updated record:")
                    print(f"[OPTION 1 IPDR]    _id: {example['_id'][:16]}...")
                    print(f"[OPTION 1 IPDR]    seq_id: {example['seq_id']}")
                    print(f"[OPTION 1 IPDR]    → Record belongs to {len(example['seq_id'])} case(s)")

        except Exception as e:
            print(f"[OPTION 1 IPDR] ❌ Error updating duplicates: {e}")
            import traceback
            traceback.print_exc()

    # ============================================================
    # CREATE/UPDATE NEXUS METADATA
    # ============================================================
    default_date = datetime(1970, 1, 1)
    now = datetime.now()



    # Build nexus model
    nexus_model = {
        '_id': nexus_hash,
        'RecordType': seq_data.get('RecordType', 'Unknown'),
        'IPDR': seq_data.get('IPDR', 'Unknown'),
        'FromDate': seq_data.get('FromDate', default_date),
        'ToDate': seq_data.get('ToDate', default_date),
        'MinDur': seq_data.get('MinDur', 0),
        'MaxDur': seq_data.get('MaxDur', 0),
        'FileName': filename,
        'Inserted': inserted_count,
        'Duplicate': len(duplicate_ids),
        'Updated': updated_count,
        'Skipped': len(top_rows) if top_rows else 0,
        'InsertedAt': now,
        'Year': now.year,
        'Month': now.month,
        'Day': now.day,
        'CrimeID': crime_hash,
        'UserAccessID': user_hash
    }

    # Check for existing nexus record and merge
    nexus_collection = db['IPdrNexus']
    existing_nexus = nexus_collection.find_one({"_id": nexus_hash})

    if existing_nexus:
        # Merge date ranges
        if existing_nexus.get('FromDate') and existing_nexus['FromDate'] < nexus_model['FromDate']:
            nexus_model['FromDate'] = existing_nexus['FromDate']
        if existing_nexus.get('ToDate') and existing_nexus['ToDate'] > nexus_model['ToDate']:
            nexus_model['ToDate'] = existing_nexus['ToDate']

        # Merge duration ranges
        if existing_nexus.get('MinDur') is not None and existing_nexus['MinDur'] < nexus_model['MinDur']:
            nexus_model['MinDur'] = existing_nexus['MinDur']
        if existing_nexus.get('MaxDur') is not None and existing_nexus['MaxDur'] > nexus_model['MaxDur']:
            nexus_model['MaxDur'] = existing_nexus['MaxDur']

        # Accumulate counts
        nexus_model['Inserted'] = existing_nexus.get('Inserted', 0) + nexus_model['Inserted']
        nexus_model['Skipped'] = existing_nexus.get('Skipped', 0) + nexus_model['Skipped']
        nexus_model['Duplicate'] = existing_nexus.get('Duplicate', 0) + nexus_model['Duplicate']
        nexus_model['Updated'] = existing_nexus.get('Updated', 0) + nexus_model['Updated']

    # Create final nexus record
    try:
        nexus_record = IPdrNexus(**nexus_model)
        final_nexus = nexus_record.to_dict()
    except:
        final_nexus = nexus_model

    # Create crime and user records
    crime_registry = {
        '_id': crime_hash,
        'Crime': crime_name,
        'AreaLocation': area_location,
        'seq_id': nexus_hash
    }

    user_map = {
        'UserID': UserId,
        'seq_id': nexus_hash,
        '_id': user_hash
    }

    try:
        crime_model_data = {field: crime_registry.get(field) for field in CrimeRecord.__dataclass_fields__}
        crime_record = CrimeRecord(**crime_model_data)
        final_crime_record = crime_record.to_dict()
    except:
        final_crime_record = crime_registry

    try:
        user_map_model = {field: user_map.get(field) for field in UserRecord.__dataclass_fields__}
        user_map_record = UserRecord(**user_map_model)
        final_user_map_record = user_map_record.to_dict()
    except:
        final_user_map_record = user_map

    # Insert/Update nexus and related records
    nexus_collection.update_one(
        {'_id': nexus_hash},
        {'$set': final_nexus},
        upsert=True
    )

    cdr_db = get_db('cdr_db')

    user_coll = cdr_db['UserAccessMapping']
    if not user_coll.find_one({'_id': user_hash}):
        user_coll.insert_one(final_user_map_record)

    crime_coll = cdr_db['CrimeRegistry']
    if not crime_coll.find_one({'_id': crime_hash}):
        crime_coll.insert_one(final_crime_record)

    print(f"\n[OPTION 1 IPDR] ✅ Summary:")
    print(f"[OPTION 1 IPDR]    New records:       {inserted_count}")
    print(f"[OPTION 1 IPDR]    Duplicates found:  {len(duplicate_ids)}")
    print(f"[OPTION 1 IPDR]    Records updated:   {updated_count}")
    print(f"[OPTION 1 IPDR]    Nexus ID:          {nexus_hash[:16]}...")

    return {
        'inserted': inserted_count,
        'duplicates': len(duplicate_ids),
        'updated': updated_count,
        'skipped': nexus_model['Skipped'],
        'seqdata': final_nexus
    }


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _remove_redundant_fields(record_dict: Dict, record_type: str) -> Dict:
    """Remove redundant fields based on record type"""
    match record_type:
        case "Mobile":
            for key in ["MSISDN", "MSISDN_code"]:
                record_dict.pop(key, None)
        case "Public IP":
            record_dict.pop('Translated_ip', None)
        case "Tower":
            record_dict.pop('TowerID', None)
        case "Destination IP":
            record_dict.pop('Destination_ip', None)
        case "Source IP":
            record_dict.pop('Source_ip', None)
        case "IMEI":
            for key in ["IMEI", "IMEI_TAC"]:
                record_dict.pop(key, None)
        case "Public Port" | "Translated Port":
            record_dict.pop('Translated_port', None)
        case "Destination Port":
            record_dict.pop('Destination_port', None)
        case "Source Port":
            record_dict.pop('Source_port', None)

    return record_dict