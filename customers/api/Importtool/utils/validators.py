# cdr/utils/validators.py
import json
import re
from django.conf import settings


def normalize(name: str) -> str:
    """Normalize column name for matching"""
    return re.sub(r'[^A-Z0-9]', '', str(name).strip().upper())


def validate_mandatory_fields(matched_columns_dict, mandatory_list):
    """Validate if all mandatory fields are present"""
    if not matched_columns_dict or not mandatory_list:
        return False

    all_matched_columns = set()
    for standard_names in matched_columns_dict.values():
        all_matched_columns.update(standard_names)

    matched_lower = {col.lower() for col in all_matched_columns}
    mandatory_lower = {col.lower() for col in mandatory_list}

    return mandatory_lower.issubset(matched_lower)


def column_check(clmn_list, headers_json_path, mandatory_header_json_path):
    """Check if columns match expected headers"""
    if not clmn_list:
        return {
            'status': 'not matched',
            'row_index': None,
            'message': 'empty input list',
            'columns': {}
        }

    # Load JSON files
    with open(headers_json_path, 'r', encoding='utf-8') as f:
        column_mapping = json.load(f)

    with open(mandatory_header_json_path, 'r', encoding='utf-8') as f:
        mandatory_data = json.load(f)

    # Normalize mandatory data
    if isinstance(mandatory_data, dict):
        mandatory_list = mandatory_data.get("mandatory", list(mandatory_data.values()))
    else:
        mandatory_list = mandatory_data if isinstance(mandatory_data, list) else []

    # Build reverse lookup
    reverse_lookup = {}
    for key, synonyms in column_mapping.items():
        for syn in synonyms:
            reverse_lookup[normalize(syn)] = key

    # Iterate through rows to find header
    for idx, record in enumerate(clmn_list, 1):
        if not record:
            continue

        result = {}
        for value in record:
            if value is None:
                continue
            str_val = str(value).strip()
            if not str_val or str_val.lower() in ['nan', 'none', '']:
                continue

            str_val_upper = str_val.upper()
            matches = []

            for std_name, variations in column_mapping.items():
                for variation in variations:
                    if str_val_upper == variation.upper():
                        matches.append(std_name)
                        break

            if matches:
                result[str_val] = matches

        # Check if this row is the header
        if len(result) >= 3:
            if validate_mandatory_fields(result, mandatory_list):
                all_matched = set()
                for m in result.values():
                    all_matched.update(m)

                return {
                    'status': 'matched',
                    'row_index': idx,
                    'message': 'all mandatory fields present',
                    'columns': result
                }

    return {
        'status': 'not matched',
        'row_index': None,
        'message': 'no valid header row found',
        'columns': {}
    }
