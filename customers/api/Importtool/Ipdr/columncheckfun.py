# cdr_handle/methods_for_column_check.py
from ..utils.json_handler import CDRConfigLoader


def validate_mandatory_fields(matched_columns_dict, mandatory_list):
    """
    Validate if all mandatory fields are present in the matched columns.
    Returns True if all are present, else False.
    """
    if not matched_columns_dict or not mandatory_list:
        return False

    # Extract all standard column names that were matched
    all_matched_columns = set()
    for standard_names in matched_columns_dict.values():
        all_matched_columns.update(standard_names)

    # Convert to lowercase for comparison
    matched_lower = {col.lower() for col in all_matched_columns}
    mandatory_lower = {col.lower() for col in mandatory_list}

    return mandatory_lower.issubset(matched_lower)


def column_check(clmn_list, headers_json, mandatory_header_json):
    """
    Check if the given column list matches with headers_json mapping
    and contains all mandatory headers.
    This function detects the header row from raw CSV data.
    """


    if not clmn_list:
        #print("ERROR: clmn_list is empty")
        return {
            'status': 'not matched',
            'row_index': None,
            'message': 'Invalid column list',
            'columns': {}
        }

    loader = CDRConfigLoader()

    try:
        column_mapping = loader.load_cdr_headers(headers_json)
        mandatory_data = loader.load_mandatory_headers(mandatory_header_json)
    except Exception as e:
        #print(f"ERROR loading JSON files: {e}")
        return {
            'status': 'not matched',
            'row_index': None,
            'message': f'json load error: {e}',
            'columns': {}
        }

    # Normalize mandatory_data into a list
    if isinstance(mandatory_data, dict):
        if "mandatory" in mandatory_data:
            mandatory_list = mandatory_data["mandatory"]
        else:
            mandatory_list = list(mandatory_data.values())
    elif isinstance(mandatory_data, list):
        mandatory_list = mandatory_data
    else:
        mandatory_list = []

    #print(f"\n{'=' * 70}")
    #print(f"COLUMN CHECK - DETECTING HEADER ROW")
    #print(f"{'=' * 70}")
    #print(f"Mandatory fields required: {mandatory_list}")
    #print(f"Checking {len(clmn_list)} rows for header...\n")

    # Iterate through each row to find the header
    for idx, record in enumerate(clmn_list, 1):
        if not record:
            continue

        result = {}
        non_null_values = []

        # Process each value in the row
        for value in record:
            if value is None:
                continue
            str_val = str(value).strip()
            if not str_val or str_val.lower() in ['nan', 'none', '']:
                continue

            non_null_values.append(str_val)
            str_val_upper = str_val.upper()
            matches = []

            # Check against column mapping
            for std_name, variations in column_mapping.items():
                for variation in variations:
                    if str_val_upper.replace(" ", "") == variation.upper().replace(" ", ""):
                        matches.append(std_name)
                        break

            if matches:
                result[str_val] = matches

        # Debug first 5 rows
        # if idx <= 5:
        #     #print(f"Row {idx}: {len(result)} column matches found")
            # if result:
            #     #print(f"  Matched columns: {list(result.keys())[:5]}")
            # else:
            #     #print(f"  Sample values: {non_null_values[:5]}")
            #print()

        # Check if this row is the header (at least 3 columns matched)
        if len(result) >= 3:
            if validate_mandatory_fields(result, mandatory_list):
                all_matched = set()
                for m in result.values():
                    all_matched.update(m)

                #print(f"✅ HEADER ROW FOUND at row {idx}")
                #print(f"Matched columns: {sorted(all_matched)}")
                #print(f"{'=' * 70}\n")

                return {
                    'status': 'matched',
                    'row_index': idx,
                    'message': 'all mandatory fields present',
                    'columns': result
                }
            else:
                # Found some matches but missing mandatory fields
                all_matched = set()
                for m in result.values():
                    all_matched.update(m)
                matched_lower = {c.lower() for c in all_matched}
                mandatory_lower = {c.lower() for c in mandatory_list}
                missing = mandatory_lower - matched_lower

                #print(f"Row {idx}: {len(result)} matches but MISSING mandatory fields: {missing}")
                #print(f"  Matched: {sorted(all_matched)}\n")

                # Continue searching - this might not be the real header
                continue

    #print(f"❌ NO VALID HEADER ROW FOUND")
    #print(f"{'=' * 70}\n")
    return {
        'status': 'not matched',
        'row_index': None,
        'message': 'Invalid Header Rows',
        'columns': {}
    }


def headers_of_cdr(original_keys, mapped_keys):
    """
    Map original headers to standard headers with their index positions.
    Returns a tuple of (standard headers, index positions).
    """
    original_index_map = {col: idx for idx, col in enumerate(original_keys)}

    updated_json_file = {
        mapped_keys[key][0]: original_index_map[key]
        for key in original_keys
        if key in mapped_keys
    }

    return [tuple(updated_json_file.keys()), tuple(updated_json_file.values())]


def get_csv_delimiter(lines):
    """
    Detect the most likely delimiter from a list of CSV lines.
    Defaults to comma if none found.
    """
    if not lines:
        return ","

    delimiters = {",": 0, "\t": 0, "~": 0, ";": 0, "|": 0}

    for line in lines:
        if line.strip():
            for delim in delimiters:
                delimiters[delim] += line.count(delim)

    best_delim, best_count = max(delimiters.items(), key=lambda item: item[1])
    return best_delim if best_count > 0 else ","