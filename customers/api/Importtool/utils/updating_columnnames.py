import json
import re
import polars as pl

from ..models import CallRecord
from .parsers import string_extract

import os
import json
from django.conf import settings

# -------- FIXED PATH ---------
json_path = os.path.join(
    settings.BASE_DIR,
    "api", "data", "cdr", "CdrHeaders.json"
)

# ------------------------------

with open(json_path, 'r') as f:
    HEADER_MAPPING = json.load(f)

# List of standardized columns you want to keep
DESIRED_COLUMNS = list(HEADER_MAPPING.keys())


# Function to normalize strings for matching
def normalize(name: str) -> str:
    return re.sub(r'[^A-Z0-9]', '', str(name).strip().upper())


# Build reverse lookup: normalized synonym -> standardized key
reverse_lookup = {}
for key, synonyms in HEADER_MAPPING.items():
    for syn in synonyms:
        reverse_lookup[normalize(syn)] = key


def rename_and_filter_columns(df: pl.DataFrame) -> pl.DataFrame:
    """
    Rename columns using HEADER_MAPPING and keep only desired columns.
    ✅ FIXED: Handles duplicate columns that may arise during renaming
    ✅ PRIORITY: When both FirstBTS and FirstCGI exist, FirstCGI takes precedence
    """
    # Step 1: Map old columns to new names and track original column info
    column_mapping = []  # List of (original_col, mapped_name, original_index)

    for idx, col in enumerate(df.columns):
        col_norm = normalize(col)
        if col_norm in reverse_lookup:
            mapped_name = reverse_lookup[col_norm]
            column_mapping.append((col, mapped_name, idx))
        else:
            column_mapping.append((col, col, idx))

    # Step 2: Apply priority rules for specific columns
    # Group columns by their mapped name
    mapped_groups = {}
    for orig_col, mapped_name, idx in column_mapping:
        if mapped_name not in mapped_groups:
            mapped_groups[mapped_name] = []
        mapped_groups[mapped_name].append((orig_col, idx))

    # Step 3: Handle First_CGI priority (FirstCGI > FirstBTS)
    priority_rules = {
        'First_CGI': ['FIRSTCGI', 'FIRST_CGI', 'CGI'],# Higher priority variants
        'Last_CGI' : ['LASTCGI','LAST_CGI']
    }

    columns_to_use = {}  # mapped_name -> (original_col, index)

    for mapped_name, occurrences in mapped_groups.items():
        if len(occurrences) == 1:
            # Only one column maps to this name, use it
            columns_to_use[mapped_name] = occurrences[0]
        else:
            # Multiple columns map to same name - apply priority
            if mapped_name in priority_rules:
                # Apply priority rule
                priority_list = priority_rules[mapped_name]
                selected = None

                # First, try to find high-priority columns
                for orig_col, idx in occurrences:
                    orig_norm = normalize(orig_col)
                    for priority_pattern in priority_list:
                        if priority_pattern in orig_norm and 'BTS' not in orig_norm.upper():
                            selected = (orig_col, idx)
                            break
                    if selected:
                        break

                # If no high-priority match, exclude FirstBTS if both FirstBTS and FirstCGI exist
                if not selected:
                    non_bts_cols = [(orig, idx) for orig, idx in occurrences
                                    if 'BTS' not in normalize(orig)]
                    if non_bts_cols:
                        # Use first non-BTS column
                        selected = non_bts_cols[0]
                    else:
                        # All are BTS, use first one
                        selected = occurrences[0]

                columns_to_use[mapped_name] = selected
            else:
                # No priority rule - use first occurrence
                columns_to_use[mapped_name] = occurrences[0]

    # Step 4: Build the rename dictionary and select columns
    columns_to_select = []
    rename_dict = {}

    for mapped_name, (orig_col, idx) in sorted(columns_to_use.items(), key=lambda x: x[1][1]):
        if mapped_name in DESIRED_COLUMNS:
            columns_to_select.append(orig_col)
            if orig_col != mapped_name:
                rename_dict[orig_col] = mapped_name

    # Step 5: Select and rename
    if columns_to_select:
        df_selected = df.select(columns_to_select)
        if rename_dict:
            df_result = df_selected.rename(rename_dict)
        else:
            df_result = df_selected
    else:
        # No columns match - return empty DataFrame with desired schema
        df_result = pl.DataFrame()

    return df_result


import math
from datetime import timedelta

import math

import polars as pl
import math
from datetime import timedelta

import math
from datetime import timedelta

"""
updating_columnnames.py - FIXED VERSION
Maps DataFrame column data to sequence data with correct field names for IPdrNexus
"""

from datetime import datetime

from datetime import datetime


def data_seq(dict_data):
    """
    Extract sequence summary info from processed IPDR records.
    Compatible with dataframe columns:
    ['username', 'user_address', 'user_contact', 'user_alternate_contact',
     'user_mail_address', 'msisdn', 'source_ip', 'source_port', 'translated_ip',
     'translated_port', 'destination_ip', 'destination_port', 'imei', 'imsi',
     'first_cgi', 'last_cgi', 'duration', 'data_uploaded', 'data_downloaded',
     'sdatetime', 'edatetime', 'msisdn_code', 'imei_tac', 'imsi_code']
    """

    if not dict_data:
        return {}

    seq_data = {
        'number': set(),
        'imei': set(),
        'imsi': set(),
        'first_cgi': set(),
        'last_cgi': set(),
        'sip': set(),
        'dip': set(),
        'translatedip': set(),
        'sport': set(),
        'dport': set(),
        'pport': set(),
    }

    min_duration = float('inf')
    max_duration = 0
    min_date = None
    max_date = None

    for record in dict_data:
        if record.get('msisdn'):
            seq_data['number'].add(str(record['msisdn']))

        if record.get('imei'):
            seq_data['imei'].add(str(record['imei']))

        if record.get('imsi'):
            seq_data['imsi'].add(str(record['imsi']))

        if record.get('first_cgi'):
            seq_data['first_cgi'].add(str(record['first_cgi']))
        if record.get('last_cgi'):
            seq_data['last_cgi'].add(str(record['last_cgi']))

        if record.get('source_ip'):
            seq_data['sip'].add(str(record['source_ip']))
        if record.get('destination_ip'):
            seq_data['dip'].add(str(record['destination_ip']))
        if record.get('translated_ip'):
            seq_data['translatedip'].add(str(record['translated_ip']))

        if record.get('source_port'):
            seq_data['sport'].add(str(record['source_port']))
        if record.get('destination_port'):
            seq_data['dport'].add(str(record['destination_port']))
        if record.get('translated_port'):
            seq_data['pport'].add(str(record['translated_port']))

        dur = record.get('duration')
        if dur:
            try:
                dur_val = int(dur)
                if dur_val > 0:
                    min_duration = min(min_duration, dur_val)
                    max_duration = max(max_duration, dur_val)
            except (ValueError, TypeError):
                pass

        start = record.get('sdatetime')
        end = record.get('edatetime')

        for dt in [start, end]:
            if dt:
                try:
                    if isinstance(dt, str):
                        dt = datetime.fromisoformat(dt)
                    if not min_date or dt < min_date:
                        min_date = dt
                    if not max_date or dt > max_date:
                        max_date = dt
                except Exception:
                    pass

    result = {k: v for k, v in seq_data.items() if v}

    if min_duration != float('inf'):
        result['MinDur'] = min_duration
    if max_duration > 0:
        result['MaxDur'] = max_duration
    if min_date:
        result['FromDate'] = min_date
    if max_date:
        result['ToDate'] = max_date

    return result


def cdr_seq_data(records: list) -> dict:
    number_set = set()
    imei_set = set()
    firstcgi_set = set()

    seq_data_dict = {
        'number': number_set,
        'first_cgi': firstcgi_set,
        'imei': imei_set,
        'is_number_cdr': True,
        'is_tower_cdr': True,
        'min_dur': math.inf,
        'max_dur': 0,
        'FromDate': None,
        'ToDate': None,
        'ImsiCode': ''
    }

    if not records:
        return seq_data_dict

    imsi_code_set = set()

    for rec in records:
        if isinstance(rec, CallRecord):
            rec_dict = rec.to_dict()
        else:
            rec_dict = rec

        if rec_dict.get('A_Party'):
            num = string_extract(rec_dict['A_Party'])
            number_set.add(num)

        if rec_dict.get('IMEI'):
            imei = string_extract(rec_dict['IMEI'])
            imei_set.add(imei)

        duration = rec_dict.get('Duration')
        if duration is not None:
            seq_data_dict['min_dur'] = min(seq_data_dict['min_dur'], duration)
            seq_data_dict['max_dur'] = max(seq_data_dict['max_dur'], duration)

        dt = rec_dict.get('SDateTime')
        if dt:
            if not seq_data_dict['FromDate'] or dt < seq_data_dict['FromDate']:
                seq_data_dict['FromDate'] = dt
            if not seq_data_dict['ToDate'] or dt > seq_data_dict['ToDate']:
                seq_data_dict['ToDate'] = dt + timedelta(seconds=seq_data_dict['max_dur'])

        imsi = rec_dict.get('IMSI_CODE')
        if imsi:
            imsi_code_set.add(imsi)

        first_cgi = rec_dict.get('First_CGI')
        if first_cgi and seq_data_dict['is_tower_cdr']:
            firstcgi_set.add(first_cgi)

    if imsi_code_set:
        seq_data_dict['ImsiCode'] = next(iter(imsi_code_set))

    if seq_data_dict['min_dur'] == math.inf:
        seq_data_dict['min_dur'] = 0

    return seq_data_dict