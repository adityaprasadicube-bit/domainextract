from datetime import datetime
from mongoengine import get_db

def get_towerdump_summary(seq_ids, from_date, to_date, filter_type):
    db = get_db(alias='tower_dump')
    collection = db['TowerDumpRecords']

    # Step 1: Match stage
    match_stage = {
        "$match": {
            "seq_id": {"$in": seq_ids},
            "SDateTime": {"$gte": from_date, "$lte": to_date}
        }
    }

    # Step 2: Decide group _id based on filter type
    if filter_type == "aparty":
        group_id = {"A_Party": "$A_Party"}
        unique_contacts_expr = {"$addToSet": "$B_Party"}   # opposite party
    elif filter_type == "bparty":

        unique_contacts_expr = {"$addToSet": "$A_Party"}
    elif filter_type == "both":
        group_id = {"A_Party": "$A_Party", "B_Party": "$B_Party"}
    elif filter_type=="bpartystate":
        group_id = {"B_Party": "$B_Party","A_Party": "$A_Party"}

    elif filter_type == "apartystate":
        group_id = {"A_Party": "$A_Party"}
        # collect both sides into one set
        unique_contacts_expr = {
            "$addToSet": {
                "$cond": [
                    {"$ne": ["$A_Party", None]},
                    "$A_Party",
                    "$B_Party"
                ]
            }
        }
    elif filter_type == "imei":
        group_id = {"IMEI": "$IMEI"}
        # collect both A & B parties
        unique_contacts_expr = {
            "$addToSet": {
                "$cond": [
                    {"$ne": ["$A_Party", None]},
                    "$A_Party",
                    "$B_Party"
                ]
            }
        }
    else:
        raise ValueError("Invalid filter_type")

    # Step 3: Group stage
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
            },
            # 👇 Always count unique B_Party
            "UniqueContacts": {"$addToSet": "$B_Party"}
        }
    }

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
            "No of Contact's": {"$size": "$UniqueContacts"},  # all B_Party numbers

            # Mobile numbers start with 6,7,8,9
            "Mobile No's": {
                "$size": {
                    "$filter": {
                        "input": "$UniqueContacts",
                        "as": "num",
                        "cond": {"$regexMatch": {"input": "$$num", "regex": "^[6789]"}}
                    }
                }
            },

            # Remaining numbers are SMS
            "CC No's": {
                "$size": {
                    "$filter": {
                        "input": "$UniqueContacts",
                        "as": "num",
                        "cond": {"$not": {"$regexMatch": {"input": "$$num", "regex": "^[6789]"}}}
                    }
                }
            },

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

    # Add A_Party / B_Party fields based on filter
    if filter_type == "aparty":
        project_stage["$project"]["A Party"] = "$_id.A_Party"
    elif filter_type == "bparty":
        project_stage["$project"]["B Party"] = "$_id.B_Party"
    elif filter_type == "both":
        project_stage["$project"]["A Party"] = "$_id.A_Party"
        project_stage["$project"]["B Party"] = "$_id.B_Party"
    elif filter_type == "imei":
        project_stage["$project"]["IMEI"] = "$_id.IMEI"
    elif filter_type=="bpartystate":
        project_stage["$project"]["A Party"] = "$_id.A_Party"
        project_stage["$project"]["B Party"] = "$_id.B_Party"
    elif filter_type=="apartystate":
        project_stage["$project"]["A Party"] = "$_id.A_Party"

    # Final pipeline
    pipeline = [match_stage, group_stage, project_stage]

    return list(collection.aggregate(pipeline))




def lrncode(b_party):
    # Connect to MongoDB (adjust URI and DB/Collection names)
    db = get_db(alias='tower_dump')
    collection = db['TowerDumpRecords']
    # Use regex to match any number ending with the given B_Party
    query = {"B_Party": {"$regex": f"{b_party}$"}}

    # Get distinct LRN numbers
    lrns = collection.distinct("LRN", query)

    return lrns