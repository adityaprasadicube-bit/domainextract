
from mongoengine import get_db
from rest_framework.response import Response
from rest_framework.views import APIView


def generate_hash_id(group_name, sub_group_name):
    """
    ✅ Generate 16-character hash ID from GroupName and Sub_Group_Name
    (Same function as in import code)
    """
    combined = f"{group_name}_{sub_group_name}"
    hash_obj = hashlib.md5(combined.encode())
    return hash_obj.hexdigest()[:16]


def clean_record_data(data):
    """
    ✅ Remove empty/null fields from record data
    Only keeps fields with actual data

    Args:
        data: Dictionary of record data

    Returns:
        Dictionary with only non-empty fields
    """
    cleaned = {}

    for key, value in data.items():
        # Skip if value is None
        if value is None:
            continue

        # Skip if value is empty string
        if isinstance(value, str) and value.strip() == '':
            continue

        # Skip if value is empty list
        if isinstance(value, list) and len(value) == 0:
            continue

        # Skip if value is empty dict
        if isinstance(value, dict) and len(value) == 0:
            continue

        # Keep the field if it has data
        cleaned[key] = value

    return cleaned


# Get column Api
class WatchlistColumnApi(APIView):
    def get(self, request):
        try:
            db = get_db(alias='watchlist_db')
            col_names = db['Watchlist_cols']
            schema = col_names.find_one({})
            if schema:
                available_fields = [key for key in schema.keys() if key != '_id']
                return Response({
                    'status': 'success',
                    'fields': available_fields,
                    'count': len(available_fields)
                }, status=200)
            else:
                return Response({
                    'status': 'error',
                    'message': 'No field schema found in Watchlist_cols collection'
                }, status=404)
        except Exception as e:
            return Response({
                'status': 'error',
                'message': str(e)
            }, status=500)


# CRUD Operations(ADD,EDIT,Delete)
class WatchlistmodificationsApi(APIView):
    def post(self, request):
        """
        POST /api/watchlist/record
        Add a new record to watchlist (follows import logic)
        ✅ Only inserts fields with actual data (non-empty)
        ✅ Updates nexus counters (Inserted, Updated, Duplicate)

        Request Body:
        {
            "group": "group_name",
            "subgroup": "subgroup_name",
            "data": {
                "Number": "9876543210",
                "Name": "John Doe",
                "Address": "123 Main St",
                "Email": "",           // ✅ Will be skipped (empty)
                "Notes": null,         // ✅ Will be skipped (null)
                ...
            }
        }

        Response:
        {
            "status": "success",
            "message": "Record added successfully",
            "record_id": "9876543210",
            "seq_id": "abc123def4567890",
            "action": "inserted",
            "fields_inserted": ["Number", "Name", "Address"],
            "fields_skipped": ["Email", "Notes"],
            "nexus_counters": {
                "Inserted": 1,
                "Updated": 0,
                "Duplicate": 0
            }
        }
        """
        try:
            group = request.data.get("group")
            subgroup = request.data.get("subgroup")
            record_data = request.data.get("data")

            # Validation
            if not group or not subgroup or not record_data:
                return Response({
                    'status': 'error',
                    'message': 'group, subgroup, and data are required'
                }, status=400)

            # ✅ Validate Number field is present and not empty
            if "Number" not in record_data or not record_data["Number"]:
                return Response({
                    'status': 'error',
                    'message': 'Number field is required in data and cannot be empty'
                }, status=400)

            number = str(record_data["Number"]).strip()

            if not number:
                return Response({
                    'status': 'error',
                    'message': 'Number field cannot be empty'
                }, status=400)

            db = get_db(alias='watchlist_db')
            nexus_collection = db['Watchlist_nexus']
            data_collection = db['WatchList_data']

            # ✅ Generate seq_id using hash (same as import)
            seq_id = generate_hash_id(group, subgroup)

            # ✅ Find or create nexus record with hash-based _id
            nexus_record = nexus_collection.find_one({'_id': seq_id})

            if not nexus_record:
                # Create new nexus entry with hash-based _id
                new_nexus = {
                    '_id': seq_id,  # ✅ Use hash as _id
                    'Group_Name': group,
                    'Sub_Group_Name': subgroup,
                    'Description': '',
                    'Inserted': 0,
                    'Duplicate': 0,
                    'Updated': 0,
                    'InsertedAt': datetime.now(),
                    'Year': datetime.now().year
                }
                nexus_collection.insert_one(new_nexus)

            # ✅ Check if record already exists
            existing_record = data_collection.find_one({'_id': number})

            if existing_record:
                # Check if this seq_id is already in the record's seq_id array
                existing_seq_ids = existing_record.get('seq_id', [])

                # Handle case where seq_id might not be an array
                if not isinstance(existing_seq_ids, list):
                    existing_seq_ids = [existing_seq_ids]

                if seq_id in existing_seq_ids:
                    # ✅ DUPLICATE: Same group+subgroup already has this record
                    # ✅ Update Duplicate counter in nexus
                    nexus_update_result = nexus_collection.find_one_and_update(
                        {'_id': seq_id},
                        {'$inc': {'Duplicate': 1}},
                        return_document=True
                    )

                    return Response({
                        'status': 'error',
                        'message': f'Record with Number {number} already exists in group "{group}" / subgroup "{subgroup}"',
                        'record_id': number,
                        'seq_id': seq_id,
                        'action': 'duplicate',
                        'nexus_counters': {
                            'Inserted': nexus_update_result.get('Inserted', 0),
                            'Updated': nexus_update_result.get('Updated', 0),
                            'Duplicate': nexus_update_result.get('Duplicate', 0)
                        }
                    }, status=409)  # 409 Conflict
                else:
                    # ✅ UPDATE: Different group/subgroup - add new seq_id
                    data_collection.update_one(
                        {'_id': number},
                        {'$addToSet': {'seq_id': seq_id}}
                    )

                    # ✅ Update Updated counter in nexus
                    nexus_update_result = nexus_collection.find_one_and_update(
                        {'_id': seq_id},
                        {'$inc': {'Updated': 1}},
                        return_document=True
                    )

                    return Response({
                        'status': 'success',
                        'message': f'Record with Number {number} already exists but added to new group/subgroup',
                        'record_id': number,
                        'seq_id': seq_id,
                        'action': 'updated',
                        'note': 'seq_id added to existing record',
                        'nexus_counters': {
                            'Inserted': nexus_update_result.get('Inserted', 0),
                            'Updated': nexus_update_result.get('Updated', 0),
                            'Duplicate': nexus_update_result.get('Duplicate', 0)
                        }
                    }, status=200)

            # ✅ CLEAN DATA: Remove empty fields before insertion
            original_fields = list(record_data.keys())
            cleaned_data = clean_record_data(record_data)
            cleaned_fields = list(cleaned_data.keys())
            skipped_fields = [f for f in original_fields if f not in cleaned_fields]

            # Make sure Number is still present after cleaning
            if 'Number' not in cleaned_data:
                cleaned_data['Number'] = number

            # ✅ NEW RECORD: Insert with proper structure (only non-empty fields)
            # Set _id to Number (like import does)
            cleaned_data['_id'] = number

            # Set seq_id as array (like import does)
            cleaned_data['seq_id'] = [seq_id]

            # Insert the new record
            data_collection.insert_one(cleaned_data)

            # ✅ Update Inserted counter in nexus and get updated counters
            nexus_update_result = nexus_collection.find_one_and_update(
                {'_id': seq_id},
                {'$inc': {'Inserted': 1}},
                return_document=True  # Return the updated document
            )

            response_data = {
                'status': 'success',
                'message': 'Record added successfully',
                'record_id': number,
                'seq_id': seq_id,
                'action': 'inserted',
                'group': group,
                'subgroup': subgroup,
                'fields_inserted': cleaned_fields,
                'nexus_counters': {
                    'Inserted': nexus_update_result.get('Inserted', 0),
                    'Updated': nexus_update_result.get('Updated', 0),
                    'Duplicate': nexus_update_result.get('Duplicate', 0)
                }
            }

            # Add skipped fields info if any were skipped
            if skipped_fields:
                response_data['fields_skipped'] = skipped_fields
                response_data['note'] = f'{len(skipped_fields)} empty field(s) were not inserted'

            return Response(response_data, status=201)

        except Exception as e:
            return Response({
                'status': 'error',
                'message': str(e)
            }, status=500)

    def put(self, request):
        """
        PUT /api/watchlist/record
        Update an existing record (updates by Number)
        ✅ Only updates fields with actual data (non-empty)

        Request Body:
        {
            "Number": "9876543210",
            "Name": "Updated Name",
            "Address": "",          // ✅ Will be skipped (empty)
            "Email": "new@email.com",
            ...
        }

        Response:
        {
            "status": "success",
            "message": "Record updated successfully",
            "modified": true,
            "number": "9876543210",
            "fields_updated": ["Name", "Email"],
            "fields_skipped": ["Address"]
        }
        """
        try:
            number = request.data.get("Number")

            if not number:
                return Response({
                    'status': 'error',
                    'message': 'Number is required'
                }, status=400)

            # Convert to string to match _id
            number = str(number).strip()

            # Get all data except Number (which is used for matching)
            update_data = {k: v for k, v in request.data.items() if k != 'Number'}

            if not update_data:
                return Response({
                    'status': 'error',
                    'message': 'No data provided for update'
                }, status=400)

            # ✅ CLEAN DATA: Remove empty fields before update
            original_fields = list(update_data.keys())
            cleaned_update_data = clean_record_data(update_data)
            cleaned_fields = list(cleaned_update_data.keys())
            skipped_fields = [f for f in original_fields if f not in cleaned_fields]

            if not cleaned_update_data:
                return Response({
                    'status': 'error',
                    'message': 'No valid data to update (all fields are empty)',
                    'fields_skipped': skipped_fields
                }, status=400)

            db = get_db(alias='watchlist_db')
            data_collection = db['WatchList_data']

            # ✅ Update by _id (which is Number) - only non-empty fields
            result = data_collection.update_one(
                {'_id': number},  # ✅ Use _id instead of Number
                {'$set': cleaned_update_data}
            )

            if result.matched_count > 0:
                response_data = {
                    'status': 'success',
                    'message': 'Record updated successfully',
                    'modified': result.modified_count > 0,
                    'number': number,
                    'fields_updated': cleaned_fields
                }

                if skipped_fields:
                    response_data['fields_skipped'] = skipped_fields
                    response_data['note'] = f'{len(skipped_fields)} empty field(s) were not updated'

                return Response(response_data, status=200)
            else:
                return Response({
                    'status': 'error',
                    'message': f'Record with Number {number} not found'
                }, status=404)

        except Exception as e:
            return Response({
                'status': 'error',
                'message': str(e)
            }, status=500)

    def delete(self, request):
        """
        DELETE /api/watchlist/record
        Delete one or more watchlist records by Number
        ✅ Updates nexus counters (decrements Inserted count)

        Request Body:
        {
            "numbers": ["9116324059", "9928378457", "..."]
        }

        Response:
        {
            "status": "success",
            "message": "2 record(s) deleted successfully",
            "deleted_count": 2,
            "nexus_updates": [
                {
                    "seq_id": "abc123",
                    "group": "TestGroup",
                    "subgroup": "TestSub",
                    "inserted_count_updated": 5
                }
            ]
        }
        """
        try:
            numbers = request.data.get("numbers", [])

            if not numbers:
                return Response({
                    'status': 'error',
                    'message': 'numbers array is required'
                }, status=400)

            # Convert all numbers to strings and strip whitespace
            numbers = [str(num).strip() for num in numbers if str(num).strip()]

            if not numbers:
                return Response({
                    'status': 'error',
                    'message': 'No valid numbers provided for deletion'
                }, status=400)

            db = get_db(alias='watchlist_db')
            data_collection = db['WatchList_data']
            nexus_collection = db['Watchlist_nexus']

            # ✅ First, get the records to find their seq_ids
            records_to_delete = list(data_collection.find({'_id': {'$in': numbers}}))

            # Track seq_ids for counter updates
            seq_id_counts = {}
            for record in records_to_delete:
                seq_ids = record.get('seq_id', [])
                if not isinstance(seq_ids, list):
                    seq_ids = [seq_ids]

                for sid in seq_ids:
                    seq_id_counts[sid] = seq_id_counts.get(sid, 0) + 1

            # ✅ Delete by _id (which is Number)
            result = data_collection.delete_many(
                {'_id': {'$in': numbers}}
            )

            # ✅ Update nexus counters - decrement Inserted count
            nexus_updates = []
            for seq_id, count in seq_id_counts.items():
                updated_nexus = nexus_collection.find_one_and_update(
                    {'_id': seq_id},
                    {'$inc': {'Inserted': -count}},  # Decrement by count
                    return_document=True
                )

                if updated_nexus:
                    nexus_updates.append({
                        'seq_id': seq_id,
                        'group': updated_nexus.get('Group_Name'),
                        'subgroup': updated_nexus.get('Sub_Group_Name'),
                        'inserted_count': updated_nexus.get('Inserted', 0)
                    })

            return Response({
                'status': 'success',
                'message': f'{result.deleted_count} record(s) deleted successfully',
                'deleted_count': result.deleted_count,
                'requested_count': len(numbers),
                'nexus_updates': nexus_updates
            }, status=200)

        except Exception as e:
            return Response({
                'status': 'error',
                'message': str(e)
            }, status=500)

# class WatchlistApi(APIView):
#     def post(self,request):
#         group = request.data.get("group")
#         subgroup = request.data.get("subgroup")
#         filtertype = request.data.get("filtertype")
#         search_type = request.data.get("search_type")
#         modifyopt = request.data.get("modifyopt")
#
#         db = get_db(alias='watchlist_db')
#         nexus_collection = db['Watchlist_nexus']
#         data_collection = db['WatchList_data']
#         col_names = db['Watchlist_cols']
#         nexus_data = list(nexus_collection.find({'Group_Name':group,'Sub_Group_Name':subgroup}))
#
#         # whole_data = []
#
#         whole_data=list(data_collection.find({'seq_id':record.get('_id') for record in nexus_data}))
#         return Response(
#             whole_data
#         )


# ============================================================================
# STEP 3: SEARCH AND DISPLAY DATA
# ============================================================================

class WatchlistSearchApi(APIView):
    """
    Search and display watchlist data with pagination
    """

    def post(self, request):
        """
        POST /api/watchlist/search
        Search/Display watchlist data based on filters with pagination

        Request Body for Display All:
        {
            "group": "saigroup",
            "subgroup": "saigroup1",
            "page": 1,           // optional, default: 1
            "page_size": 10      // optional, default: 10
        }

        Request Body for Search:
        {
            "group": "saigroup",
            "subgroup": "saigroup1",
            "search_type": "Number",
            "search_values": "9876543210, 9988776655",
            "page": 1,           // optional, default: 1
            "page_size": 10      // optional, default: 10
        }

        Response:
        {
            "status": "success",
            "data": [...],
            "pagination": {
                "current_page": 1,
                "page_size": 10,
                "total_records": 100,
                "total_pages": 10,
                "has_next": true,
                "has_previous": false
            },
            "search_applied": false
        }
        """
        try:
            group = request.data.get("group")
            subgroup = request.data.get("subgroup")
            search_type = request.data.get("search_type")
            search_values = request.data.get("search_values")

            # Pagination parameters
            page = request.data.get("page", 1)
            page_size = request.data.get("page_size", 10)

            # Validate pagination parameters
            try:
                page = int(page)
                page_size = int(page_size)

                if page < 1:
                    page = 1
                if page_size < 1:
                    page_size = 10
                if page_size > 100:  # Maximum limit to prevent overload
                    page_size = 100

            except (ValueError, TypeError):
                return Response({
                    'status': 'error',
                    'message': 'page and page_size must be valid integers'
                }, status=400)

            # Validate required fields
            if not group or not subgroup:
                return Response({
                    'status': 'error',
                    'message': 'group and subgroup are required'
                }, status=400)

            db = get_db(alias='watchlist_db')
            nexus_collection = db['Watchlist_nexus']
            data_collection = db['WatchList_data']

            # Step 1: Find the nexus record for this group/subgroup
            nexus_query = {
                'Group_Name': group,
                'Sub_Group_Name': subgroup
            }

            nexus_data = list(nexus_collection.find(nexus_query))

            if not nexus_data:
                return Response({
                    'status': 'success',
                    'data': [],
                    'pagination': {
                        'current_page': page,
                        'page_size': page_size,
                        'total_records': 0,
                        'total_pages': 0,
                        'has_next': False,
                        'has_previous': False
                    },
                    'message': f'No records found for group: {group}, subgroup: {subgroup}'
                }, status=200)

            # Step 2: Extract seq_ids from nexus records
            seq_ids = [record.get('_id') for record in nexus_data]

            # Step 3: Build the data query
            data_query = {'seq_id': {'$in': seq_ids}}

            # Step 4: Add search filter if provided
            if search_type and search_values:
                if isinstance(search_values, str):
                    values_list = [v.strip() for v in search_values.split(',') if v.strip()]
                else:
                    values_list = search_values if isinstance(search_values, list) else [search_values]

                data_query[search_type] = {'$in': values_list}

            # Step 5: Get total count for pagination
            total_records = data_collection.count_documents(data_query)

            # Calculate pagination metadata
            total_pages = (total_records + page_size - 1) // page_size  # Ceiling division
            has_next = page < total_pages
            has_previous = page > 1

            # Adjust page if it exceeds total pages
            if page > total_pages and total_pages > 0:
                page = total_pages

            # Step 6: Fetch paginated data
            skip = (page - 1) * page_size
            whole_data = list(
                data_collection.find(data_query)
                .skip(skip)
                .limit(page_size)
            )

            # Step 7: Convert ObjectId to string for JSON serialization
            for record in whole_data:
                if '_id' in record:
                    record['_id'] = str(record['_id'])
                if 'seq_id' in record:
                    record['seq_id'] = str(record['seq_id'])

            # Step 8: Return the results with pagination info
            return Response({
                'status': 'success',
                'data': whole_data,
                'pagination': {
                    'current_page': page,
                    'page_size': page_size,
                    'total_records': total_records,
                    'total_pages': total_pages,
                    'has_next': has_next,
                    'has_previous': has_previous
                },
                'search_applied': bool(search_type and search_values)
            }, status=200)

        except Exception as e:
            return Response({
                'status': 'error',
                'message': str(e)
            }, status=500)
"""
Group Management API
Handles Create, Update, Delete operations for Groups and Subgroups
Automatically updates seq_id in all related records when groups change
"""

import hashlib
from datetime import datetime


def generate_hash_id(group_name, sub_group_name):
    """
    ✅ Generate 16-character hash ID from GroupName and Sub_Group_Name
    (Same function as in import code)
    """
    combined = f"{group_name}_{sub_group_name}"
    hash_obj = hashlib.md5(combined.encode())
    return hash_obj.hexdigest()[:16]


class WatchlistGroupManagementApi(APIView):
    """
    Complete Group/Subgroup Management API
    - POST: Create new group/subgroup
    - PUT: Edit/Rename group/subgroup (auto-updates all record seq_ids)
    - DELETE: Delete group/subgroup (with option to delete or reassign records)
    """

    def post(self, request):
        """
        POST /api/watchlist/group/manage
        Create a new group/subgroup combination

        Request Body:
        {
            "group": "new_group_name",
            "subgroup": "new_subgroup_name",
            "description": "Optional description"
        }

        Response:
        {
            "status": "success",
            "message": "Group/Subgroup created successfully",
            "group": "new_group_name",
            "subgroup": "new_subgroup_name",
            "seq_id": "abc123def4567890"
        }
        """
        try:
            group = request.data.get("group")
            subgroup = request.data.get("subgroup")
            description = request.data.get("description", "")

            # Validation
            if not group or not subgroup:
                return Response({
                    'status': 'error',
                    'message': 'Both group and subgroup are required'
                }, status=400)

            db = get_db(alias='watchlist_db')
            nexus_collection = db['Watchlist_nexus']

            # ✅ Generate seq_id using same logic as import
            seq_id = generate_hash_id(group, subgroup)

            # Check if this group/subgroup combination already exists
            existing = nexus_collection.find_one({
                '_id': seq_id  # ✅ Check by seq_id (which is _id)
            })

            if existing:
                return Response({
                    'status': 'error',
                    'message': f'Group "{group}" with subgroup "{subgroup}" already exists',
                    'existing_seq_id': seq_id
                }, status=400)

            # ✅ Create new group/subgroup entry with hash-based _id
            new_entry = {
                '_id': seq_id,  # ✅ Use hash as _id
                'Group_Name': group,
                'Sub_Group_Name': subgroup,
                'Description': description,
                'Inserted': 0,  # ✅ Initialize counters like import
                'Duplicate': 0,
                'Updated': 0,
                'InsertedAt': datetime.now(),
                'Year': datetime.now().year
            }

            nexus_collection.insert_one(new_entry)

            return Response({
                'status': 'success',
                'message': 'Group/Subgroup created successfully',
                'group': group,
                'subgroup': subgroup,
                'seq_id': seq_id  # ✅ Return the hash-based seq_id
            }, status=201)

        except Exception as e:
            return Response({
                'status': 'error',
                'message': str(e)
            }, status=500)

    def put(self, request):
        """
        PUT /api/watchlist/group/manage
        Edit/Rename a group or subgroup
        AUTOMATICALLY updates seq_id in all related records

        Request Body:
        {
            "old_group": "current_group_name",
            "old_subgroup": "current_subgroup_name",
            "new_group": "new_group_name",        // optional, if renaming group
            "new_subgroup": "new_subgroup_name",  // optional, if renaming subgroup
            "description": "Updated description"   // optional
        }

        Response:
        {
            "status": "success",
            "message": "Group updated successfully",
            "old_seq_id": "abc123def4567890",
            "new_seq_id": "xyz789ghi0123456",
            "records_updated": 25
        }
        """
        try:
            old_group = request.data.get("old_group")
            old_subgroup = request.data.get("old_subgroup")
            new_group = request.data.get("new_group")
            new_subgroup = request.data.get("new_subgroup")
            description = request.data.get("description")

            # Validation
            if not old_group or not old_subgroup:
                return Response({
                    'status': 'error',
                    'message': 'old_group and old_subgroup are required'
                }, status=400)

            # At least one change must be specified
            if not new_group and not new_subgroup and description is None:
                return Response({
                    'status': 'error',
                    'message': 'At least one of new_group, new_subgroup, or description must be provided'
                }, status=400)

            db = get_db(alias='watchlist_db')
            nexus_collection = db['Watchlist_nexus']
            data_collection = db['WatchList_data']

            # ✅ Generate old seq_id to find the record
            old_seq_id = generate_hash_id(old_group, old_subgroup)

            # Find the old nexus record
            old_nexus = nexus_collection.find_one({'_id': old_seq_id})

            if not old_nexus:
                return Response({
                    'status': 'error',
                    'message': f'Group "{old_group}" with subgroup "{old_subgroup}" not found',
                    'expected_seq_id': old_seq_id
                }, status=404)

            # Determine final group and subgroup names
            final_group = new_group if new_group else old_group
            final_subgroup = new_subgroup if new_subgroup else old_subgroup

            # ✅ If only description is changing, just update in place
            if final_group == old_group and final_subgroup == old_subgroup:
                # No need to change seq_id, just update description
                nexus_collection.update_one(
                    {'_id': old_seq_id},
                    {'$set': {'Description': description}}
                )

                return Response({
                    'status': 'success',
                    'message': 'Description updated successfully',
                    'seq_id': old_seq_id,
                    'group': old_group,
                    'subgroup': old_subgroup
                }, status=200)

            # ✅ Generate new seq_id for new group/subgroup combination
            new_seq_id = generate_hash_id(final_group, final_subgroup)

            # Check if new combination already exists
            existing_new = nexus_collection.find_one({'_id': new_seq_id})

            if existing_new:
                return Response({
                    'status': 'error',
                    'message': f'Target group "{final_group}" with subgroup "{final_subgroup}" already exists',
                    'existing_seq_id': new_seq_id
                }, status=400)

            # ✅ CRITICAL FIX: Update records' seq_id array, not replace
            # Records have seq_id as an array: seq_id: [seq_id1, seq_id2, ...]

            # Step 1: Find all records with old_seq_id in their seq_id array
            records_with_old_seq = data_collection.find({'seq_id': old_seq_id})
            records_count = data_collection.count_documents({'seq_id': old_seq_id})

            if records_count > 0:
                # Step 2: Remove old_seq_id and add new_seq_id to all records
                update_result = data_collection.update_many(
                    {'seq_id': old_seq_id},
                    {
                        '$pull': {'seq_id': old_seq_id},  # Remove old seq_id
                        '$addToSet': {'seq_id': new_seq_id}  # Add new seq_id
                    }
                )
                records_updated = update_result.modified_count
            else:
                records_updated = 0

            # Step 3: Create new nexus entry
            new_nexus_entry = {
                '_id': new_seq_id,  # ✅ Use hash as _id
                'Group_Name': final_group,
                'Sub_Group_Name': final_subgroup,
                'Description': description if description is not None else old_nexus.get('Description', ''),
                'Inserted': old_nexus.get('Inserted', 0),  # ✅ Preserve counts
                'Duplicate': old_nexus.get('Duplicate', 0),
                'Updated': old_nexus.get('Updated', 0),
                'InsertedAt': old_nexus.get('InsertedAt', datetime.now()),
                'Year': datetime.now().year
            }

            nexus_collection.insert_one(new_nexus_entry)

            # Step 4: Delete old nexus entry
            nexus_collection.delete_one({'_id': old_seq_id})

            return Response({
                'status': 'success',
                'message': f'Group/Subgroup updated successfully. {records_updated} records updated.',
                'old_seq_id': old_seq_id,
                'new_seq_id': new_seq_id,
                'records_updated': records_updated,
                'old_group': old_group,
                'old_subgroup': old_subgroup,
                'new_group': final_group,
                'new_subgroup': final_subgroup
            }, status=200)

        except Exception as e:
            return Response({
                'status': 'error',
                'message': str(e)
            }, status=500)

    def delete(self, request):
        """
        DELETE /api/watchlist/group/manage
        Delete a group/subgroup
        Options: delete all records OR move records to another group

        Request Body:
        {
            "group": "group_to_delete",
            "subgroup": "subgroup_to_delete",
            "delete_records": true,  // true = delete all records, false = move to target
            "target_group": "new_group",      // required if delete_records = false
            "target_subgroup": "new_subgroup"  // required if delete_records = false
        }

        Response:
        {
            "status": "success",
            "message": "Group deleted and 25 records moved to new group",
            "records_deleted": 25,  // or records_moved
            "nexus_deleted": true
        }
        """
        try:
            group = request.data.get("group")
            subgroup = request.data.get("subgroup")
            delete_records = request.data.get("delete_records", True)
            target_group = request.data.get("target_group")
            target_subgroup = request.data.get("target_subgroup")

            # Validation
            if not group or not subgroup:
                return Response({
                    'status': 'error',
                    'message': 'group and subgroup are required'
                }, status=400)

            # If not deleting records, target group/subgroup required
            if not delete_records and (not target_group or not target_subgroup):
                return Response({
                    'status': 'error',
                    'message': 'target_group and target_subgroup are required when delete_records is false'
                }, status=400)

            db = get_db(alias='watchlist_db')
            nexus_collection = db['Watchlist_nexus']
            data_collection = db['WatchList_data']

            # ✅ Generate seq_id to find the record
            seq_id_to_delete = generate_hash_id(group, subgroup)

            # Find the nexus record to delete
            nexus_to_delete = nexus_collection.find_one({'_id': seq_id_to_delete})

            if not nexus_to_delete:
                return Response({
                    'status': 'error',
                    'message': f'Group "{group}" with subgroup "{subgroup}" not found',
                    'expected_seq_id': seq_id_to_delete
                }, status=404)

            if delete_records:
                # Option 1: Delete all records that have this seq_id in their array
                delete_result = data_collection.delete_many({'seq_id': seq_id_to_delete})
                records_affected = delete_result.deleted_count

                # Delete the nexus entry
                nexus_collection.delete_one({'_id': seq_id_to_delete})

                return Response({
                    'status': 'success',
                    'message': f'Group/Subgroup deleted successfully. {records_affected} records deleted.',
                    'records_deleted': records_affected,
                    'nexus_deleted': True,
                    'deleted_seq_id': seq_id_to_delete
                }, status=200)

            else:
                # Option 2: Move all records to target group/subgroup
                # ✅ Generate target seq_id
                target_seq_id = generate_hash_id(target_group, target_subgroup)

                # Find or create target nexus
                target_nexus = nexus_collection.find_one({'_id': target_seq_id})

                if not target_nexus:
                    # Create target nexus if it doesn't exist
                    nexus_collection.insert_one({
                        '_id': target_seq_id,  # ✅ Use hash as _id
                        'Group_Name': target_group,
                        'Sub_Group_Name': target_subgroup,
                        'Description': f'Created during group migration from {group}/{subgroup}',
                        'Inserted': 0,
                        'Duplicate': 0,
                        'Updated': 0,
                        'InsertedAt': datetime.now(),
                        'Year': datetime.now().year
                    })

                # ✅ Update records: remove old seq_id, add new seq_id
                update_result = data_collection.update_many(
                    {'seq_id': seq_id_to_delete},
                    {
                        '$pull': {'seq_id': seq_id_to_delete},  # Remove old
                        '$addToSet': {'seq_id': target_seq_id}  # Add new
                    }
                )

                records_affected = update_result.modified_count

                # Delete the old nexus entry
                nexus_collection.delete_one({'_id': seq_id_to_delete})

                return Response({
                    'status': 'success',
                    'message': f'Group/Subgroup deleted successfully. {records_affected} records moved to {target_group}/{target_subgroup}.',
                    'records_moved': records_affected,
                    'nexus_deleted': True,
                    'deleted_seq_id': seq_id_to_delete,
                    'target_group': target_group,
                    'target_subgroup': target_subgroup,
                    'target_seq_id': target_seq_id
                }, status=200)

        except Exception as e:
            return Response({
                'status': 'error',
                'message': str(e)
            }, status=500)