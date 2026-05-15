from datetime import datetime
from collections import defaultdict
import math

# ---------------------------------------------------
# Geo Distance Logic (NEW - replaces CGI matching)
# ---------------------------------------------------

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371000  # meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def extract_lat_long(record):
    """
    Supports formats:
    '23.2345 77.2345 120'
    '23.2345,77.2345,120'
    """
    value = record.get("Lat-Long-Azimuth (First CellID)")
    if not value:
        return None

    try:
        import re
        parts = re.split(r'[,\s]+', str(value).strip())

        if len(parts) < 2:
            return None

        lat = float(parts[0])
        lon = float(parts[1])
        return lat, lon
    except:
        return None

def is_same_tower(r1, r2):
    coords1 = extract_lat_long(r1)
    coords2 = extract_lat_long(r2)

    if not coords1 or not coords2:
        return False

    dist = calculate_distance(
        coords1[0], coords1[1],
        coords2[0], coords2[1]
    )

    return dist <= 2000  # 2000 meter threshold

# ---------------------------------------------------

def is_valid_mobile(number):
    return number and str(number).isdigit() and len(str(number)) == 10 and str(number)[0] in '6789'

def is_call_record(record):
    call_type = str(record.get("Call Type", "")).upper()
    return call_type in {"CALL_IN", "CALL_OUT", "SMS_IN", "SMS_OUT"}

def parse_datetime(record):
    try:
        date_str = record.get("Date", "")
        time_str = record.get("Time", "")
        if date_str and time_str:
            return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
    except:
        pass
    return None


def get_under_tower_exact_calls(mapping_list, max_diff_seconds=5):


    """
    Detect UNDER TOWER CALLS and group by number pairs:
    - Same physical tower (Lat-Long distance <= 2000 meters)
    - Same Date
    - Call time within max_diff_seconds
    - Opposite A/B parties (A calls B, B calls A)

    All calls between the same pair of numbers get the same SNO
    """


    # Filter valid call records
    call_records = [
        r for r in mapping_list
        if is_call_record(r)
           and parse_datetime(r)
           and is_valid_mobile(r.get("A Party"))
           and is_valid_mobile(r.get("B Party"))
    ]

    # Group only by Date (tower grouping removed)
    buckets = defaultdict(list)

    for record in call_records:
        dt = parse_datetime(record)
        record['_parsed_dt'] = dt
        key = record.get("Date")
        buckets[key].append(record)

    matched_pairs = []
    used_records = set()

    for date, records in buckets.items():
        records.sort(key=lambda r: r['_parsed_dt'])

        for i in range(len(records)):
            r1 = records[i]
            record_id_1 = id(r1)

            if record_id_1 in used_records:
                continue

            t1 = r1['_parsed_dt']
            a1 = r1.get("A Party")
            b1 = r1.get("B Party")

            for j in range(i + 1, len(records)):
                r2 = records[j]
                record_id_2 = id(r2)

                if record_id_2 in used_records:
                    continue

                t2 = r2['_parsed_dt']
                time_diff = abs((t2 - t1).total_seconds())

                if time_diff > max_diff_seconds:
                    break

                a2 = r2.get("A Party")
                b2 = r2.get("B Party")

                # Opposite A/B check (UNCHANGED)
                if a1 == b2 and b1 == a2 and a1 != b1:

                    # 🔥 NEW: Same tower check using geo distance
                    if not is_same_tower(r1, r2):
                        continue

                    used_records.add(record_id_1)
                    used_records.add(record_id_2)

                    number_pair = tuple(sorted([a1, b1]))

                    matched_pairs.append({
                        'r1': r1,
                        'r2': r2,
                        'number_pair': number_pair,
                        'time': min(t1, t2)
                    })
                    break

    # Group by number pairs (UNCHANGED)
    number_pair_groups = defaultdict(list)
    for pair_data in matched_pairs:
        number_pair_groups[pair_data['number_pair']].append(pair_data)

    sorted_groups = sorted(
        number_pair_groups.items(),
        key=lambda x: min(p['time'] for p in x[1])
    )

    result = []

    for sno, (number_pair, pair_list) in enumerate(sorted_groups, 1):
        pair_list.sort(key=lambda p: p['time'])

        for pair_data in pair_list:
            r1 = pair_data['r1']
            r2 = pair_data['r2']

            records_to_add = sorted(
                [r1, r2],
                key=lambda r: r['_parsed_dt']
            )

            for record in records_to_add:
                result.append({
                    "Group_No": sno,
                    "TowerName": record.get("TowerName"),
                    "A Party": record.get("A Party"),
                    "B Party": record.get("B Party"),
                    "Date": record.get("Date"),
                    "Time": record.get("Time"),
                    "Duration": record.get("Duration"),
                    "Call Type": record.get("Call Type"),
                    "First Cell ID": record.get("First Cell ID"),
                    "First Cell ID Address": record.get("First Cell ID Address"),
                    "Last Cell ID": record.get("Last Cell ID"),
                    "Last Cell ID Address": record.get("Last Cell ID Address"),
                    "IMEI": record.get("IMEI"),
                    "IMSI": record.get("IMSI"),
                    "Con Type": record.get("Con Type"),
                    "Lat-Long-Azimuth (First CellID)": record.get("Lat-Long-Azimuth (First CellID)"),
                    "Crime": record.get("Crime"),
                    "A Party Provider": f"{record.get('A Party Operator')} - {record.get('A Party Circle')}",
                    "B Party Provider": f"{record.get('B Party Operator')} - {record.get('B Party Circle')}",
                    "Circle": record.get("Tower Circle"),
                    "Operator": record.get("Tower Operator"),
                    "LRN": record.get("LRN"),
                    "CallForward": record.get("CallForward"),
                })

    # Cleanup
    for record in call_records:
        record.pop('_parsed_dt', None)

    return result


def get_under_tower_associate_calls(mapping_list, max_diff_seconds=5):
    """
    Detect UNDER TOWER CALLS and their ASSOCIATED CALLS including contacts:
    Uses SAME geo-distance logic as get_under_tower_exact_calls

    1. Find under-tower call pairs (A calls B, B calls A from same tower - using GEO DISTANCE)
    2. Find ALL other calls made by these numbers from the same tower (using GEO DISTANCE)
    3. Find ALL calls made by their associated numbers (contacts) from the same tower
    4. Each record appears in ONLY ONE group
    5. MAIN_PAIR records take priority - if a record is MAIN_PAIR in any group, it cannot appear elsewhere
    6. Only ONE MAIN_PAIR per group (the earliest one)

    Returns:
    - Main under-tower calls with their associated calls grouped by SNO
    """

    # Filter only call records with valid data
    call_records = [
        r for r in mapping_list
        if is_call_record(r)
           and parse_datetime(r)
           and is_valid_mobile(r.get("A Party"))
    ]

    # Add parsed datetime to all records
    for record in call_records:
        record['_parsed_dt'] = parse_datetime(record)

    # Group by DATE only (same as exact function)
    buckets = defaultdict(list)
    for record in call_records:
        key = record.get("Date")
        buckets[key].append(record)

    # STEP 1: Find all under tower call pairs (USING GEO DISTANCE)
    matched_pairs = []
    under_tower_record_ids = set()

    for date, records in buckets.items():
        records.sort(key=lambda r: r['_parsed_dt'])

        for i in range(len(records)):
            r1 = records[i]
            record_id_1 = id(r1)

            if record_id_1 in under_tower_record_ids:
                continue

            # Only check CALL_IN and CALL_OUT for main pairs
            if r1.get("Call Type", "").upper() not in {"CALL_IN", "CALL_OUT"}:
                continue

            t1 = r1['_parsed_dt']
            a1 = r1.get("A Party")
            b1 = r1.get("B Party")

            if not is_valid_mobile(b1):
                continue

            for j in range(i + 1, len(records)):
                r2 = records[j]
                record_id_2 = id(r2)

                if record_id_2 in under_tower_record_ids:
                    continue

                if r2.get("Call Type", "").upper() not in {"CALL_IN", "CALL_OUT"}:
                    continue

                t2 = r2['_parsed_dt']
                time_diff = abs((t2 - t1).total_seconds())

                if time_diff > max_diff_seconds:
                    break

                a2 = r2.get("A Party")
                b2 = r2.get("B Party")

                if not is_valid_mobile(b2):
                    continue

                # Check if opposite parties (A1->B1 matches B2->A2)
                if a1 == b2 and b1 == a2 and a1 != b1:

                    # 🔥 CRITICAL: Same tower check using geo distance (SAME AS EXACT FUNCTION)
                    if not is_same_tower(r1, r2):
                        continue

                    under_tower_record_ids.add(record_id_1)
                    under_tower_record_ids.add(record_id_2)

                    # Create a canonical number pair
                    number_pair = tuple(sorted([a1, b1]))

                    # Store lat-long for this pair to use for associate matching
                    coords1 = extract_lat_long(r1)

                    matched_pairs.append({
                        'r1': r1,
                        'r2': r2,
                        'number_pair': number_pair,
                        'time': min(t1, t2),
                        'date': date,
                        'reference_coords': coords1  # Store coords for later matching
                    })
                    break

    # Group by number pairs
    number_pair_groups = defaultdict(list)
    for pair_data in matched_pairs:
        number_pair_groups[pair_data['number_pair']].append(pair_data)

    # Sort groups by earliest call time
    sorted_groups = sorted(number_pair_groups.items(),
                           key=lambda x: min(p['time'] for p in x[1]))

    # STEP 2: For each number pair, find their associated numbers (contacts)
    def get_associated_numbers(main_numbers, all_records):
        """Find all numbers that the main numbers have communicated with"""
        associated = set()
        for record in all_records:
            a_party = record.get("A Party")
            b_party = record.get("B Party")

            # If A Party is one of main numbers, add B Party to associates
            if a_party in main_numbers and is_valid_mobile(b_party):
                associated.add(b_party)
            # If B Party is one of main numbers, add A Party to associates
            elif is_valid_mobile(b_party) and b_party in main_numbers and is_valid_mobile(a_party):
                associated.add(a_party)

        # Remove the main numbers themselves
        associated -= set(main_numbers)
        return associated

    # STEP 3: For each number pair, find ALL associated calls from same tower (USING GEO DISTANCE)
    result = []
    globally_used_records = set()
    all_main_pair_records = set()

    # First pass: collect all MAIN_PAIR record IDs
    for number_pair, pair_list in number_pair_groups.items():
        for pair_data in pair_list:
            all_main_pair_records.add(id(pair_data['r1']))
            all_main_pair_records.add(id(pair_data['r2']))

    # Second pass: build groups
    for sno, (number_pair, pair_list) in enumerate(sorted_groups, 1):
        num1, num2 = number_pair

        # Collect ALL under-tower matched record IDs for this number pair
        main_pair_record_ids = set()
        reference_coords_list = []  # Collect all reference coords for this group

        for pair_data in pair_list:
            main_pair_record_ids.add(id(pair_data['r1']))
            main_pair_record_ids.add(id(pair_data['r2']))
            if pair_data.get('reference_coords'):
                reference_coords_list.append(pair_data['reference_coords'])

        # 🔥 CHANGED: Get ALL dates (not just dates with under-tower calls)
        # We'll search ALL dates and filter by tower proximity instead
        dates = set(buckets.keys())  # All dates in the dataset

        # Find associated numbers for this pair
        associated_numbers = get_associated_numbers([num1, num2], call_records)

        # Find only BIDIRECTIONAL associates
        bidirectional_associates = set()

        for date in dates:
            if date in buckets:
                main_to_assoc = {}
                assoc_to_main = {}

                for record in buckets[date]:
                    # Check if this record is near any of the reference towers (using geo distance)
                    is_near_tower = False
                    for ref_coords in reference_coords_list:
                        rec_coords = extract_lat_long(record)
                        if rec_coords and ref_coords:
                            dist = calculate_distance(
                                ref_coords[0], ref_coords[1],
                                rec_coords[0], rec_coords[1]
                            )
                            if dist <= 2000:  # Same 2000m threshold
                                is_near_tower = True
                                break

                    if not is_near_tower:
                        continue

                    a_party = record.get("A Party")
                    b_party = record.get("B Party")

                    # Main number calling associate
                    if a_party in number_pair and is_valid_mobile(b_party) and b_party in associated_numbers:
                        if b_party not in main_to_assoc:
                            main_to_assoc[b_party] = set()
                        main_to_assoc[b_party].add(id(record))

                    # Associate calling main number
                    if a_party in associated_numbers and is_valid_mobile(b_party) and b_party in number_pair:
                        if a_party not in assoc_to_main:
                            assoc_to_main[a_party] = set()
                        assoc_to_main[a_party].add(id(record))

                # Only include associates who have calls in BOTH directions
                for assoc_num in associated_numbers:
                    if assoc_num in main_to_assoc and assoc_num in assoc_to_main:
                        bidirectional_associates.add(assoc_num)

        # Find all records involving these numbers from the same towers (USING GEO DISTANCE)
        group_records = []

        for date in dates:
            if date in buckets:
                for record in buckets[date]:
                    rec_id = id(record)

                    # Skip if this record is a MAIN_PAIR in ANY OTHER group
                    if rec_id in all_main_pair_records and rec_id not in main_pair_record_ids:
                        continue

                    # Skip if already used in ANY group
                    if rec_id in globally_used_records:
                        continue

                    # Check if this record is near any of the reference towers (using geo distance)
                    is_near_tower = False
                    for ref_coords in reference_coords_list:
                        rec_coords = extract_lat_long(record)
                        if rec_coords and ref_coords:
                            dist = calculate_distance(
                                ref_coords[0], ref_coords[1],
                                rec_coords[0], rec_coords[1]
                            )
                            if dist <= 2000:  # Same 2000m threshold
                                is_near_tower = True
                                break

                    if not is_near_tower:
                        continue

                    a_party = record.get("A Party")
                    b_party = record.get("B Party")

                    # Include if A Party is in the main number pair
                    if a_party in number_pair:
                        group_records.append(record)
                        globally_used_records.add(rec_id)

        # Sort by time
        group_records.sort(key=lambda r: r['_parsed_dt'])

        # Remove duplicate associate calls
        seen_calls = {}
        filtered_records = []

        for record in group_records:
            rec_id = id(record)
            a_party = record.get("A Party")
            b_party = record.get("B Party")
            duration = record.get("Duration")
            time_str = record.get("Time")

            key1 = (a_party, b_party, duration, time_str)
            key2 = (b_party, a_party, duration, time_str)

            if key2 in seen_calls:
                if rec_id not in main_pair_record_ids and id(seen_calls[key2]) not in main_pair_record_ids:
                    continue

            seen_calls[key1] = record
            filtered_records.append(record)

        # Add all filtered records to result
        group_size = len(filtered_records)

        for record in filtered_records:
            call_type = record.get("Call Type", "")
            rec_id = id(record)
            a_party = record.get("A Party")
            b_party = record.get("B Party")

            is_under_tower = rec_id in main_pair_record_ids

            # Determine relationship type
            relationship = ""
            if is_under_tower:
                relationship = "MAIN_PAIR"
            elif a_party in number_pair:
                if is_valid_mobile(b_party) and b_party in bidirectional_associates:
                    relationship = "MAIN_TO_ASSOCIATE"
                else:
                    relationship = "MAIN_CALL"

            result.append({
                "Group_No": sno,
                "Total_Group_Records": group_size,
                "Duration": record.get("Duration"),
                "Number_Pair": f"{num1} - {num2}",
                "Is_Under_Tower_Match": "YES" if is_under_tower else "NO",
                # "Relationship": relationship,
                "TowerName": record.get("TowerName"),
                "A Party": record.get("A Party"),
                "B Party": record.get("B Party"),
                "Date": record.get("Date"),
                "Time": record.get("Time"),
                #"Duration": record.get("Duration"),
                "Call Type": call_type,
                "First Cell ID": record.get("First Cell ID"),
                "First Cell ID Address": record.get("First Cell ID Address"),
                "Last Cell ID": record.get("Last Cell ID"),
                "Last Cell ID Address": record.get("Last Cell ID Address"),
                "IMEI": record.get("IMEI"),
                "IMSI": record.get("IMSI"),
                "Con Type": record.get("Con Type"),
                "Lat-Long-Azimuth (First CellID)": record.get("Lat-Long-Azimuth (First CellID)"),
                "Crime": record.get("Crime"),
                "A Party Provider": f"{record.get('A Party Operator')} - {record.get('A Party Circle')}",
                "B Party Provider": f"{record.get('B Party Operator')} - {record.get('B Party Circle')}",
                "Circle": record.get("Tower Circle"),
                "Operator": record.get("Tower Operator"),
                "LRN": record.get("LRN"),
                "CallForward": record.get("CallForward"),
            })

    # Clean up temporary field
    for record in call_records:
        record.pop('_parsed_dt', None)

    return result


def undertowernumberswithoutexternalcalls(mapping_list, max_diff_seconds=5):
    """
    Returns only those under-tower groups where both numbers communicate
    EXCLUSIVELY with each other (no external contacts in the entire dataset).

    Args:
        mapping_list: List of call records
        max_diff_seconds: Maximum time difference for under-tower detection

    Returns:
        List of under-tower call records where number pairs are isolated
    """

    # Step 1: Get all under-tower grouped results
    undertower_results = get_under_tower_exact_calls(
        mapping_list,
        max_diff_seconds=max_diff_seconds
    )

    if not undertower_results:
        return []

    # Step 2: Build complete communication map for all numbers
    contact_map = defaultdict(set)

    for record in mapping_list:
        if not is_call_record(record):
            continue

        a_party = record.get("A Party")
        b_party = record.get("B Party")

        # Add bidirectional contacts
        if is_valid_mobile(a_party) and is_valid_mobile(b_party) and a_party != b_party:
            contact_map[a_party].add(b_party)
            contact_map[b_party].add(a_party)

    # Step 3: Group under-tower results by Group_No
    group_map = defaultdict(list)
    for row in undertower_results:
        group_map[row["Group_No"]].append(row)

    # Step 4: Filter for isolated pairs (no external communication)
    final_result = []

    for group_no, records in group_map.items():
        # Extract unique numbers in this group
        numbers = set()
        for record in records:
            numbers.add(record["A Party"])
            numbers.add(record["B Party"])

        # Valid group must have exactly 2 numbers
        if len(numbers) != 2:
            continue

        num1, num2 = list(numbers)

        # Check if both numbers ONLY communicate with each other
        if contact_map[num1] == {num2} and contact_map[num2] == {num1}:
            final_result.extend(records)

    return final_result



# def get_under_tower_group_calls(mapping_list):
#
#     # -----------------------------
#     # 1️⃣ Filter Valid Call Records
#     # -----------------------------
#     call_records = []
#     directed_calls = defaultdict(int)
#     adjacency = defaultdict(set)
#
#     for r in mapping_list:
#         call_type = str(r.get("Call Type", "")).upper()
#         if call_type not in {"CALL_IN", "CALL_OUT"}:
#             continue
#
#         a = r.get("A Party")
#         b = r.get("B Party")
#
#         if not is_valid_mobile(a) or not is_valid_mobile(b):
#             continue
#
#         call_records.append(r)
#         directed_calls[(a, b)] += 1
#         adjacency[a].add(b)
#         adjacency[b].add(a)
#
#     if not call_records:
#         return []
#
#     # ----------------------------------
#     # 2️⃣ Build Mutual Core Graph
#     # ----------------------------------
#     mutual_graph = defaultdict(set)
#
#     for (a, b) in directed_calls:
#         if (b, a) in directed_calls:
#             mutual_graph[a].add(b)
#             mutual_graph[b].add(a)
#
#     # ----------------------------------
#     # 3️⃣ Find Mutual Core Components
#     # ----------------------------------
#     visited = set()
#     groups = []
#     group_no = 1
#
#     for number in mutual_graph:
#
#         if number in visited:
#             continue
#
#         # BFS to extract one mutual core cluster
#         queue = deque([number])
#         core_numbers = set()
#
#         while queue:
#             current = queue.popleft()
#
#             if current in visited:
#                 continue
#
#             visited.add(current)
#             core_numbers.add(current)
#
#             for neighbor in mutual_graph[current]:
#                 if neighbor not in visited:
#                     queue.append(neighbor)
#
#         # ----------------------------------
#         # 4️⃣ Controlled One-Level Expansion
#         # ----------------------------------
#         expanded_numbers = set(core_numbers)
#
#         for core in core_numbers:
#             expanded_numbers.update(adjacency[core])
#
#         # ----------------------------------
#         # 5️⃣ Collect Records Inside Cluster
#         # ----------------------------------
#         component_records = [
#             r for r in call_records
#             if r.get("A Party") in expanded_numbers
#             and r.get("B Party") in expanded_numbers
#         ]
#
#         if component_records:
#             groups.append({
#                 "group_no": group_no,
#                 "group_strength": len(expanded_numbers),
#                 "total_calls": len(component_records),
#                 "unique_numbers": sorted(expanded_numbers),
#                 "records": sorted(
#                     component_records,
#                     key=lambda x: (x.get("Date"), x.get("Time"))
#                 )
#             })
#
#             group_no += 1
#
#     return groups








