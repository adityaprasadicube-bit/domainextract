from datetime import datetime
from mongoengine import get_db
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView


def get_towerdump_summary(seq_ids, from_date, to_date, filter_type):
    db = get_db(alias='cdr_db')
    collection = db['CallDetailRecords']

    # Step 1: Match stage
    match_stage = {
        "$match": {
            "seq_id": {"$in": seq_ids},
            "SDateTime": {"$gte": from_date, "$lte": to_date}
        }
    }

    # Step 2: Lookup A_Party from DataNexus when missing
    lookup_stage = {
        "$lookup": {
            "from": "DataNexus",
            "let": {"record_seq_id": "$_id"},
            "pipeline": [
                {
                    "$match": {
                        "$expr": {
                            "$and": [
                                {"$eq": ["$seq_id", "$record_seq_id"]},
                                {"$eq": ["$RecordType", "CDR"]}
                            ]
                        }
                    }
                },
                {
                    "$project": {
                        "CDRNo_Or_ImeiNo": 1,
                        "_id": 0
                    }
                },
                {"$limit": 1}
            ],
            "as": "nexus_data"
        }
    }

    # Step 3: Add field to use looked-up A_Party if original is missing
    add_fields_stage = {
        "$addFields": {
            "A_Party": {
                "$cond": {
                    "if": {
                        "$or": [
                            {"$eq": ["$A_Party", None]},
                            {"$eq": ["$A_Party", ""]},
                            {"$not": ["$A_Party"]}
                        ]
                    },
                    "then": {
                        "$arrayElemAt": ["$nexus_data.CDRNo_Or_ImeiNo", 0]
                    },
                    "else": "$A_Party"
                }
            }
        }
    }

    # Step 4: Decide group _id and unique contacts based on filter type
    if filter_type == "aparty":
        group_id = {"A_Party": "$A_Party", "First_CGI": "$First_CGI"}
        unique_contacts_field = "$B_Party"  # Track who A_Party contacted
    elif filter_type == "bparty":
        group_id = {"B_Party": "$B_Party", "First_CGI": "$First_CGI"}
        unique_contacts_field = "$A_Party"  # Track who contacted B_Party
    elif filter_type == "both":
        group_id = {"B_Party": "$B_Party", "A_Party": "$A_Party", "First_CGI": "$First_CGI"}
        unique_contacts_field = None  # No unique contact tracking for both
    else:
        raise ValueError(f"Invalid filter_type: {filter_type}")

    # Step 5: Group stage
    group_stage = {
        "$group": {
            "_id": group_id,
            "Total Calls": {"$sum": 1},
            "Out Calls": {
                "$sum": {"$cond": [{"$eq": ["$Call_Type", "CALL_OUT"]}, 1, 0]}
            },
            "In Calls": {
                "$sum": {"$cond": [{"$eq": ["$Call_Type", "CALL_IN"]}, 1, 0]}
            },
            "Out Sms": {
                "$sum": {"$cond": [{"$eq": ["$Call_Type", "SMS_OUT"]}, 1, 0]}
            },
            "In Sms": {
                "$sum": {"$cond": [{"$eq": ["$Call_Type", "SMS_IN"]}, 1, 0]}
            },
            "Other Calls": {
                "$sum": {
                    "$cond": [
                        {"$not": [{"$in": ["$Call_Type", ["CALL_OUT", "CALL_IN", "SMS_OUT", "SMS_IN"]]}]},
                        1,
                        0
                    ]
                }
            },
            "First_Call": {"$min": "$SDateTime"},
            "Last_Call": {"$max": "$SDateTime"},
            "CellIDs": {"$addToSet": "$First_CGI"},
            "Total_Days": {
                "$addToSet": {
                    "$dateToString": {"format": "%Y-%m-%d", "date": "$SDateTime"}
                }
            }
        }
    }

    # Add unique contacts only for aparty and bparty filters
    if unique_contacts_field:
        group_stage["$group"]["UniqueContacts"] = {"$addToSet": unique_contacts_field}

    # Step 6: Project stage
    project_stage = {
        "$project": {
            "_id": 0,
            "Total Calls": 1,
            "Out Calls": 1,
            "In Calls": 1,
            "Out Sms": 1,
            "In Sms": 1,
            "Other Calls": 1,
            "Cell IDs": {"$size": "$CellIDs"},
            "Total Days": {"$size": "$Total_Days"},
            "First Call Date": {
                "$dateToString": {"format": "%d-%m-%Y", "date": "$First_Call"}
            },
            "First Call Time": {
                "$dateToString": {"format": "%H:%M:%S", "date": "$First_Call"}
            },
            "Last Call Date": {
                "$dateToString": {"format": "%d-%m-%Y", "date": "$Last_Call"}
            },
            "Last Call Time": {
                "$dateToString": {"format": "%H:%M:%S", "date": "$Last_Call"}
            }
        }
    }

    # Add unique contact metrics only when applicable
    if unique_contacts_field:
        project_stage["$project"]["No of Contact's"] = {"$size": "$UniqueContacts"}

        # Mobile numbers start with 6,7,8,9 (Indian numbers)
        project_stage["$project"]["Mobile No's"] = {
            "$size": {
                "$filter": {
                    "input": "$UniqueContacts",
                    "as": "num",
                    "cond": {"$regexMatch": {"input": "$$num", "regex": "^[6789]"}}
                }
            }
        }

        # CC (Control Channel/Short Code) numbers
        project_stage["$project"]["CC No's"] = {
            "$size": {
                "$filter": {
                    "input": "$UniqueContacts",
                    "as": "num",
                    "cond": {"$not": {"$regexMatch": {"input": "$$num", "regex": "^[6789]"}}}
                }
            }
        }

    # Add party fields based on filter type
    if filter_type == "aparty":
        project_stage["$project"]["A Party"] = "$_id.A_Party"
        project_stage["$project"]["First_CGI"] = "$_id.First_CGI"
    elif filter_type == "bparty":
        project_stage["$project"]["B Party"] = "$_id.B_Party"
        project_stage["$project"]["First_CGI"] = "$_id.First_CGI"
    elif filter_type == "both":
        project_stage["$project"]["B Party"] = "$_id.B_Party"
        project_stage["$project"]["A Party"] = "$_id.A_Party"
        project_stage["$project"]["First_CGI"] = "$_id.First_CGI"

    # Final pipeline
    pipeline = [match_stage, lookup_stage, add_fields_stage, group_stage, project_stage]

    # Debug: Print pipeline
    print(f"Executing pipeline with filter_type: {filter_type}")

    results = list(collection.aggregate(pipeline))
    print(f"Pipeline returned {len(results)} results")

    return results


def lrncode(b_party):
    """
    Get distinct LRN (Location Routing Numbers) for a given B_Party suffix
    """
    db = get_db(alias='tower_dump')
    collection = db['TowerDumpRecords']

    # Use regex to match any number ending with the given B_Party
    query = {"B_Party": {"$regex": f"{b_party}$"}}

    # Get distinct LRN numbers
    lrns = collection.distinct("LRN", query)

    return lrns


def debug_data_structure(seq_ids):
    """
    Debug helper to check data structure and field names
    """
    db = get_db(alias='tower_dump')
    collection = db['TowerDumpRecords']

    # Get a sample record
    sample = collection.find_one({"seq_id": {"$in": seq_ids}})

    if sample:
        print("Sample record structure:")
        print(f"Fields: {list(sample.keys())}")
        print(f"seq_id: {sample.get('seq_id')}")
        print(f"SDateTime type: {type(sample.get('SDateTime'))}")
        print(f"SDateTime value: {sample.get('SDateTime')}")
        print(f"A_Party: {sample.get('A_Party')}")
        print(f"B_Party: {sample.get('B_Party')}")
        print(f"First_CGI: {sample.get('First_CGI')}")
        print(f"Call_Type: {sample.get('Call_Type')}")
        return sample
    else:
        print("No records found with given seq_ids")
        return None


class CDRcommonView(APIView):
    """
    API to summarize TowerDump CDR records with filtering by:
    - aparty: Group by caller (A_Party)
    - bparty: Group by receiver (B_Party)
    - both: Group by both parties
    """

    def post(self, request):
        try:
            seq_ids = request.data.get("seq_ids")
            from_date = request.data.get("from_date")
            to_date = request.data.get("to_date")
            filter_type = request.data.get("filter")

            print(f"Request params - seq_ids: {seq_ids}, from: {from_date}, to: {to_date}, filter: {filter_type}")

            # Validate required parameters
            if not seq_ids or not from_date or not to_date:
                return Response(
                    {"error": "Missing required parameters (seq_ids, from_date, to_date)"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if not filter_type:
                return Response(
                    {"error": "Missing required parameter: filter"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Debug: Check data structure
            print("=" * 50)
            print("DEBUGGING DATA STRUCTURE:")
            debug_data_structure(seq_ids)
            print("=" * 50)

            # Parse dates
            from_dt = datetime.fromisoformat(from_date)
            to_dt = datetime.fromisoformat(to_date)

            print(f"Parsed dates - from: {from_dt}, to: {to_dt}")

            # Execute aggregation
            results = get_towerdump_summary(seq_ids, from_dt, to_dt, filter_type)

            # Sort by total calls descending
            sorted_data = sorted(results, key=lambda x: x.get('Total Calls', 0), reverse=True)

            return Response(sorted_data, status=status.HTTP_200_OK)

        except ValueError as e:
            return Response(
                {"error": f"Invalid parameter value: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            print(f"Error in CDRcommonView: {str(e)}")
            import traceback
            traceback.print_exc()
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )