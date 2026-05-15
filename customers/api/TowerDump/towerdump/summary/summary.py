from mongoengine import get_db

db = get_db(alias='towerdump_db')
collection = db['TowerDumpRecords']

def get_aparty_summary(seq_ids, from_date=None, to_date=None):

    # Match stage
    match_conditions = {"seq_id": {"$in": seq_ids}}
    if from_date:
        match_conditions["SDateTime"] = {"$gte": from_date}
    if to_date:
        if "SDateTime" in match_conditions:
            match_conditions["SDateTime"]["$lte"] = to_date
        else:
            match_conditions["SDateTime"] = {"$lte": to_date}

    match_stage = {"$match": match_conditions}

    # Group by A_Party
    group_stage = {
        "$group": {
            "_id": "$A_Party",
            "Total_Calls": {"$sum": 1},
            "Out_Calls": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "CALL_OUT"]}, 1, 0]}},
            "In_Calls": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "CALL_IN"]}, 1, 0]}},
            "Out_SMS": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "SMS_OUT"]}, 1, 0]}},
            "In_SMS": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "SMS_IN"]}, 1, 0]}},
            "Other_Calls": {"$sum": {"$cond": [{"$not": [{"$in": ["$Call_Type", ["CALL_OUT", "CALL_IN", "SMS_OUT", "SMS_IN"]]}]}, 1, 0]}},
            "CellIDs": {"$addToSet": "$First_CGI"},
            "Total_Days": {"$addToSet": {"$dateToString": {"format": "%Y-%m-%d", "date": "$SDateTime"}}},
            "First_Call": {"$min": "$SDateTime"},
            "Last_Call": {"$max": "$SDateTime"}
        }
    }

    # Project stage
    # Project stage with correct sequence
    project_stage = {
        "$project": {
            "_id": 0,
            "A_Party": "$_id",  # unwrap _id
            "Total_Calls": 1,
            "Out_Calls": 1,
            "In_Calls": 1,
            "Out_SMS": 1,
            "In_SMS": 1,
            "Other_Calls": 1,
            "Cell_IDs": {"$size": "$CellIDs"},
            "Total_Days": {"$size": "$Total_Days"},
            "First_Call_Date": {"$dateToString": {"format": "%d-%m-%Y", "date": "$First_Call"}},
            "First_Call_Time": {"$dateToString": {"format": "%H:%M:%S", "date": "$First_Call"}},
            "Last_Call_Date": {"$dateToString": {"format": "%d-%m-%Y", "date": "$Last_Call"}},
            "Last_Call_Time": {"$dateToString": {"format": "%H:%M:%S", "date": "$Last_Call"}}
        }
    }

    pipeline = [match_stage, group_stage, project_stage]

    return list(collection.aggregate(pipeline))


def get_bparty_summary(seq_ids, from_date=None, to_date=None):

    # Match stage
    match_conditions = {"seq_id": {"$in": seq_ids}}
    if from_date:
        match_conditions["SDateTime"] = {"$gte": from_date}
    if to_date:
        if "SDateTime" in match_conditions:
            match_conditions["SDateTime"]["$lte"] = to_date
        else:
            match_conditions["SDateTime"] = {"$lte": to_date}
    match_stage = {"$match": match_conditions}

    # Group by B_Party
    group_stage = {
        "$group": {
            "_id": "$B_Party",
            "Total_Calls": {"$sum": 1},
            "Out_Calls": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "CALL_OUT"]}, 1, 0]}},
            "In_Calls": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "CALL_IN"]}, 1, 0]}},
            "Out_SMS": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "SMS_OUT"]}, 1, 0]}},
            "In_SMS": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "SMS_IN"]}, 1, 0]}},
            "Other_Calls": {
                "$sum": {
                    "$cond": [
                        {"$not": [{"$in": ["$Call_Type", ["CALL_OUT", "CALL_IN", "SMS_OUT", "SMS_IN"]]}]},
                        1,
                        0,
                    ]
                }
            },
            "CellIDs": {"$addToSet": "$First_CGI"},
            "Total_Days": {"$addToSet": {"$dateToString": {"format": "%Y-%m-%d", "date": "$SDateTime"}}},
            "First_Call": {"$min": "$SDateTime"},
            "Last_Call": {"$max": "$SDateTime"},
        }
    }

    # Project stage with B_Party first
    project_stage = {
        "$project": {
            "_id": 0,
            "B_Party": "$_id",
            "Total_Calls": 1,
            "Out_Calls": 1,
            "In_Calls": 1,
            "Out_SMS": 1,
            "In_SMS": 1,
            "Other_Calls": 1,
            "Cell_IDs": {"$size": "$CellIDs"},
            "Total_Days": {"$size": "$Total_Days"},
            "First_Call_Date": {"$dateToString": {"format": "%d-%m-%Y", "date": "$First_Call"}},
            "First_Call_Time": {"$dateToString": {"format": "%H:%M:%S", "date": "$First_Call"}},
            "Last_Call_Date": {"$dateToString": {"format": "%d-%m-%Y", "date": "$Last_Call"}},
            "Last_Call_Time": {"$dateToString": {"format": "%H:%M:%S", "date": "$Last_Call"}},
        }
    }

    pipeline = [match_stage, group_stage, project_stage]
    return list(collection.aggregate(pipeline))



def get_both_summary(seq_ids, from_date=None, to_date=None):


    # Match stage
    match_conditions = {"seq_id": {"$in": seq_ids}}
    if from_date:
        match_conditions["SDateTime"] = {"$gte": from_date}
    if to_date:
        if "SDateTime" in match_conditions:
            match_conditions["SDateTime"]["$lte"] = to_date
        else:
            match_conditions["SDateTime"] = {"$lte": to_date}
    match_stage = {"$match": match_conditions}

    # Group by A_Party + B_Party
    group_stage = {
        "$group": {
            "_id": {"A_Party": "$A_Party", "B_Party": "$B_Party"},
            "Total_Calls": {"$sum": 1},
            "Out_Calls": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "CALL_OUT"]}, 1, 0]}},
            "In_Calls": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "CALL_IN"]}, 1, 0]}},
            "Out_SMS": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "SMS_OUT"]}, 1, 0]}},
            "In_SMS": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "SMS_IN"]}, 1, 0]}},
            "Other_Calls": {
                "$sum": {
                    "$cond": [
                        {"$not": [{"$in": ["$Call_Type", ["CALL_OUT", "CALL_IN", "SMS_OUT", "SMS_IN"]]}]},
                        1,
                        0,
                    ]
                }
            },
            "CellIDs": {"$addToSet": "$First_CGI"},
            "Total_Days": {"$addToSet": {"$dateToString": {"format": "%Y-%m-%d", "date": "$SDateTime"}}},
            "First_Call": {"$min": "$SDateTime"},
            "Last_Call": {"$max": "$SDateTime"},
        }
    }

    # Project stage with both A_Party + B_Party
    project_stage = {
        "$project": {
            "_id": 0,
            "A_Party": "$_id.A_Party",
            "B_Party": "$_id.B_Party",
            "Total_Calls": 1,
            "Out_Calls": 1,
            "In_Calls": 1,
            "Out_SMS": 1,
            "In_SMS": 1,
            "Other_Calls": 1,
            "Cell_IDs": {"$size": "$CellIDs"},
            "Total_Days": {"$size": "$Total_Days"},
            "First_Call_Date": {"$dateToString": {"format": "%d-%m-%Y", "date": "$First_Call"}},
            "First_Call_Time": {"$dateToString": {"format": "%H:%M:%S", "date": "$First_Call"}},
            "Last_Call_Date": {"$dateToString": {"format": "%d-%m-%Y", "date": "$Last_Call"}},
            "Last_Call_Time": {"$dateToString": {"format": "%H:%M:%S", "date": "$Last_Call"}},
        }
    }

    pipeline = [match_stage, group_stage, project_stage]
    return list(collection.aggregate(pipeline))



def get_imei_summary(seq_ids, from_date=None, to_date=None):
    # Match stage
    match_conditions = {"seq_id": {"$in": seq_ids}}
    if from_date:
        match_conditions["SDateTime"] = {"$gte": from_date}
    if to_date:
        if "SDateTime" in match_conditions:
            match_conditions["SDateTime"]["$lte"] = to_date
        else:
            match_conditions["SDateTime"] = {"$lte": to_date}

    match_stage = {"$match": match_conditions}

    # Group by IMEI
    group_stage = {
        "$group": {
            "_id": "$IMEI",
            "Brand_Model": {"$first": "$Device_Info"},
            "Device_Type": {"$first": "$Device_Type"},
            "Total_Calls": {"$sum": 1},
            "Out_Calls": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "CALL_OUT"]}, 1, 0]}},
            "In_Calls": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "CALL_IN"]}, 1, 0]}},
            "Out_SMS": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "SMS_OUT"]}, 1, 0]}},
            "In_SMS": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "SMS_IN"]}, 1, 0]}},
            "Other_Calls": {"$sum": {"$cond": [{"$not": [{"$in": ["$Call_Type", ["CALL_OUT", "CALL_IN", "SMS_OUT", "SMS_IN"]]}]}, 1, 0]}},
            "Cell_IDs": {"$addToSet": "$First_CGI"},
            "Total_Days": {"$addToSet": {"$dateToString": {"format": "%Y-%m-%d", "date": "$SDateTime"}}},
            "First_Call": {"$min": "$SDateTime"},
            "Last_Call": {"$max": "$SDateTime"}
        }
    }

    # Project stage
    project_stage = {
        "$project": {
            "_id": 0,
            "IMEI": "$_id",
            "Brand_Model": 1,
            "Device_Type": 1,
            "Total_Calls": 1,
            "Out_Calls": 1,
            "In_Calls": 1,
            "Out_SMS": 1,
            "In_SMS": 1,
            "Other_Calls": 1,
            "Cell_IDs": {"$size": "$Cell_IDs"},
            "Total_Days": {"$size": "$Total_Days"},
            "First_Call_Date": {"$dateToString": {"format": "%d-%m-%Y", "date": "$First_Call"}},
            "First_Call_Time": {"$dateToString": {"format": "%H:%M:%S", "date": "$First_Call"}},
            "Last_Call_Date": {"$dateToString": {"format": "%d-%m-%Y", "date": "$Last_Call"}},
            "Last_Call_Time": {"$dateToString": {"format": "%H:%M:%S", "date": "$Last_Call"}}
        }
    }

    pipeline = [match_stage, group_stage, project_stage]
    return  list(collection.aggregate(pipeline))





