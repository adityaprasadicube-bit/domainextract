from mongoengine import get_db


def get_CDR_summary(seq_ids, from_dt=None, to_dt=None, ignore_dates=False, filter_type="normal"):
    """
    filter_type options:
        normal, daily, hourly, daily_hourly, weekly, weekday, monthly,
        imeisummary, transactional_summary, Apartyimeisummary, Apartyimsisummary, first_last_avg
    """
    db = get_db(alias='cdr_db')
    cdr_collection = db['CallDetailRecords']
    seq_collection_name = 'DataNexus'

    match_stage = {"seq_id": {"$in": seq_ids}}

    if not ignore_dates and from_dt and to_dt:
        match_stage["SDateTime"] = {"$gte": from_dt, "$lte": to_dt}

    # Transactional/Promotional Alerts: B_Party NOT starting with 6,7,8,9
    if filter_type == "transactional_summary":
        match_stage["B_Party"] = {"$not": {"$regex": "^[6-9]"}}

    pipeline = [{"$match": match_stage}]

    # Lookup A_Party info
    pipeline.append({
        "$lookup": {
            "from": seq_collection_name,
            "localField": "seq_id",
            "foreignField": "_id",
            "as": "seq_info"
        }
    })
    pipeline.append({"$unwind": "$seq_info"})

    # Daily / Hourly fields
    if filter_type in ["daily", "daily_hourly", "hourly"]:
        pipeline.append({"$addFields": {"Day": {"$dateToString": {"format": "%d/%m/%Y", "date": "$SDateTime"}}}})
    if filter_type in ["hourly", "daily_hourly"]:
        pipeline.append({"$addFields": {"Hour": {"$hour": "$SDateTime"}}})

    # Weekly fields - calculate week number based on Sunday-Saturday weeks containing the dates
    if filter_type == "weekly":
        pipeline.append({
            "$addFields": {
                "FromDate": "$seq_info.FromDate",
                "DayOfWeek": {"$dayOfWeek": "$SDateTime"}  # 1=Sunday, 7=Saturday
            }
        })

        # Calculate the Sunday of the week containing SDateTime
        pipeline.append({
            "$addFields": {
                "WeekStartDate": {
                    "$dateSubtract": {
                        "startDate": "$SDateTime",
                        "unit": "day",
                        "amount": {"$subtract": ["$DayOfWeek", 1]}  # Days since Sunday
                    }
                }
            }
        })

        # Calculate the Sunday of the week containing FromDate
        pipeline.append({
            "$addFields": {
                "FromDateDayOfWeek": {"$dayOfWeek": "$FromDate"},
                "FromDateWeekStart": {
                    "$dateSubtract": {
                        "startDate": "$FromDate",
                        "unit": "day",
                        "amount": {"$subtract": [{"$dayOfWeek": "$FromDate"}, 1]}
                    }
                }
            }
        })

        # Calculate week number as difference in weeks from FromDate's week
        pipeline.append({
            "$addFields": {
                "DaysDiff": {
                    "$dateDiff": {
                        "startDate": "$FromDateWeekStart",
                        "endDate": "$WeekStartDate",
                        "unit": "day"
                    }
                }
            }
        })

        pipeline.append({
            "$addFields": {
                "WeekNum": {
                    "$divide": ["$DaysDiff", 7]
                }
            }
        })

    # Weekday fields
    if filter_type == "weekday":
        pipeline.append({
            "$addFields": {
                "DayOfWeek": {"$dayOfWeek": "$SDateTime"},
                "sai": {
                    "$switch": {
                        "branches": [
                            {"case": {"$eq": [{"$dayOfWeek": "$SDateTime"}, 1]}, "then": "Sun"},
                            {"case": {"$eq": [{"$dayOfWeek": "$SDateTime"}, 2]}, "then": "Mon"},
                            {"case": {"$eq": [{"$dayOfWeek": "$SDateTime"}, 3]}, "then": "Tue"},
                            {"case": {"$eq": [{"$dayOfWeek": "$SDateTime"}, 4]}, "then": "Wed"},
                            {"case": {"$eq": [{"$dayOfWeek": "$SDateTime"}, 5]}, "then": "Thu"},
                            {"case": {"$eq": [{"$dayOfWeek": "$SDateTime"}, 6]}, "then": "Fri"},
                            {"case": {"$eq": [{"$dayOfWeek": "$SDateTime"}, 7]}, "then": "Sat"},
                        ],
                        "default": "Unknown"
                    }
                }
            }
        })

    # Monthly fields
    if filter_type == "monthly":
        pipeline.append({
            "$addFields": {
                "MonthYear": {"$year": "$SDateTime"},
                "MonthNumber": {"$month": "$SDateTime"},
                "MonthLabel": {
                    "$concat": [
                        {"$toString": {"$year": "$SDateTime"}}, ",",
                        {"$toString": {"$month": "$SDateTime"}}, "(",
                        {"$dateToString": {"format": "%b", "date": "$SDateTime"}}, ")"
                    ]
                }
            }
        })

    # Group ID based on filter_type
    if filter_type == "hourly":
        group_id = {"Day": "$Day", "Hour": "$Hour", "A_Party": "$seq_info.CDRNo_Or_ImeiNo"}
    elif filter_type == "daily":
        group_id = {"Day": "$Day", "A_Party": "$seq_info.CDRNo_Or_ImeiNo"}
    elif filter_type == "daily_hourly":
        group_id = {"Day": "$Day", "Hour": "$Hour"}
    elif filter_type == "weekly":
        group_id = {"A_Party": "$seq_info.CDRNo_Or_ImeiNo", "WeekNum": "$WeekNum"}
    elif filter_type == "weekday":
        group_id = {"A_Party": "$seq_info.CDRNo_Or_ImeiNo", "sai": "$sai", "DayOfWeek": "$DayOfWeek"}
    elif filter_type == "monthly":
        group_id = {"A_Party": "$seq_info.CDRNo_Or_ImeiNo", "MonthYear": "$MonthYear", "MonthNumber": "$MonthNumber"}
    elif filter_type == "imeisummary":
        group_id = {"IMEI": "$IMEI", "B_Party": "$B_Party"}
    elif filter_type == "transactional_summary":
        group_id = {"A_Party": "$seq_info.CDRNo_Or_ImeiNo", "B_Party": "$B_Party", "Operator": "$Operator"}
    elif filter_type == "Apartyimeisummary":
        group_id = {"A_Party": "$seq_info.CDRNo_Or_ImeiNo", "IMEI": "$IMEI"}
    elif filter_type == "Apartyimsisummary":
        group_id = {"A_Party": "$seq_info.CDRNo_Or_ImeiNo", "IMSI": "$IMEI"}
    elif filter_type == "first_last_avg":
        group_id = {"A_Party": "$seq_info.CDRNo_Or_ImeiNo", "Hour": {"$hour": "$SDateTime"}}
    else:
        group_id = {"A_Party": "$seq_info.CDRNo_Or_ImeiNo", "B_Party": "$B_Party"}

    # Group Stage
    if filter_type != "first_last_avg":
        group_stage = {
            "_id": group_id,
            "TotalCalls": {"$sum": 1},
            "OutCalls": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "CALL_OUT"]}, 1, 0]}},
            "InCalls": {"$sum": {"$cond": [{"$eq": ["$Call_Type", "CALL_IN"]}, 1, 0]}},
            "OutSms": {"$sum": {"$cond": [
                {"$or": [
                    {"$eq": ["$Call_Type", "SMS_OUT"]},
                    {"$eq": ["$FileCallType", "SMS_OUT"]},
                    {"$eq": ["$FileCallType", "OUT_SMS"]}
                ]}, 1, 0
            ]}},
            "InSms": {"$sum": {"$cond": [
                {"$or": [
                    {"$eq": ["$Call_Type", "SMS_IN"]},
                    {"$eq": ["$FileCallType", "SMS_IN"]},
                    {"$eq": ["$FileCallType", "IN_SMS"]}
                ]}, 1, 0
            ]}},
            "OtherCalls": {"$sum": {"$cond": [
                {"$and": [
                    {"$ne": ["$Call_Type", "CALL_OUT"]},
                    {"$ne": ["$Call_Type", "CALL_IN"]},
                    {"$ne": ["$Call_Type", "SMS_OUT"]},
                    {"$ne": ["$Call_Type", "SMS_IN"]}
                ]}, 1, 0
            ]}},
            "WifiCalls": {"$sum": {"$cond": [
                {"$or": [
                    {"$eq": ["$Call_Type", "WIFI_CALL"]},
                    {"$regexMatch": {"input": {"$toString": "$Call_Type"}, "regex": "wifi", "options": "i"}}
                ]}, 1, 0
            ]}},
            "TotalDuration": {"$sum": {"$ifNull": ["$Duration", 0]}},
            "RoamCalls": {"$sum": {"$cond": [
                {"$and": [
                    # Voice calls only
                    {"$or": [
                        {"$eq": ["$Call_Type", "CALL_OUT"]},
                        {"$eq": ["$Call_Type", "CALL_IN"]}
                    ]},
                    # Both First_CGI and IMSI_CODE must exist and not be empty
                    {"$ne": [{"$ifNull": ["$First_CGI", ""]}, ""]},
                    {"$ne": [{"$ifNull": ["$IMSI_CODE", ""]}, ""]},
                    # Check if IMSI_CODE (first 6 chars) is NOT in First_CGI (first 6 chars)
                    # This indicates roaming - user's home network != current network
                    {"$ne": [
                        {"$substr": ["$IMSI_CODE", 0, 6]},
                        {"$substr": ["$First_CGI", 0, 6]}
                    ]}
                ]}, 1, 0
            ]}},
            "RoamSms": {"$sum": {"$cond": [
                {"$and": [
                    # SMS only
                    {"$or": [
                        {"$eq": ["$Call_Type", "SMS_OUT"]},
                        {"$eq": ["$Call_Type", "SMS_IN"]},
                        {"$eq": ["$FileCallType", "SMS_OUT"]},
                        {"$eq": ["$FileCallType", "IN_SMS"]},
                        {"$eq": ["$FileCallType", "OUT_SMS"]}
                    ]},
                    # Both First_CGI and IMSI_CODE must exist and not be empty
                    {"$ne": [{"$ifNull": ["$First_CGI", ""]}, ""]},
                    {"$ne": [{"$ifNull": ["$IMSI_CODE", ""]}, ""]},
                    # Check if IMSI_CODE (first 6 chars) is NOT in First_CGI (first 6 chars)
                    {"$ne": [
                        {"$substr": ["$IMSI_CODE", 0, 6]},
                        {"$substr": ["$First_CGI", 0, 6]}
                    ]}
                ]}, 1, 0
            ]}},
            "FirstCallDate": {"$min": "$SDateTime"},
            "LastCallDate": {"$max": "$SDateTime"},
        }

        # Add fields based on filter type
        if filter_type in ["daily", "weekly", "hourly", "daily_hourly", "weekday"]:
            # For daily/weekly/hourly reports, use $addToSet to collect unique values
            group_stage.update({
                "AllCellIDs": {"$addToSet": "$First_CGI"},
                "AllIMEIs": {"$addToSet": "$IMEI"},
                "AllIMSIs": {"$addToSet": "$IMSI"},
            })
        else:
            # For other reports, collect both unique counts and individual fields
            group_stage.update({
                "TotalCellIDs": {"$addToSet": "$First_CGI"},
                "TotalIMEIs": {"$addToSet": "$IMEI"},
                "TotalIMSIs": {"$addToSet": "$IMSI"},
                "CellID": {"$first": "$First_CGI"},
                "IMEI": {"$first": "$IMEI"},
                "IMSI": {"$first": "$IMSI"},
                "LRN": {"$first": {"$ifNull": ["$LRN", "$b_mobile_code"]}},
                "IMEI_TAC": {"$first": "$IMEI_TAC"},
                "B_Party_Code": {"$first": "$b_mobile_code"},
            })

            # For transactional_summary, we also need unique dates per B_Party
            if filter_type == "transactional_summary":
                group_stage["AllDates"] = {"$addToSet": {"$dateToString": {"format": "%Y-%m-%d", "date": "$SDateTime"}}}

        if filter_type == "monthly":
            group_stage["MonthLabel"] = {"$first": "$MonthLabel"}

        pipeline.append({"$group": group_stage})

    # First/Last Average Timings
    if filter_type == "first_last_avg":
        pipeline.append({"$sort": {"SDateTime": 1}})
        pipeline.append({
            "$group": {
                "_id": {"A_Party": "$seq_info.CDRNo_Or_ImeiNo", "Hour": {"$hour": "$SDateTime"}},
                "FirstCall": {"$first": "$SDateTime"},
                "LastCall": {"$last": "$SDateTime"},
                "TotalCalls": {"$sum": 1}
            }
        })
        pipeline.append({
            "$project": {
                "_id": 0,
                "A_Party": "$_id.A_Party",
                "Hours": {"$concat": [{"$toString": "$_id.Hour"}, " - ", {"$toString": {"$add": ["$_id.Hour", 1]}}]},
                "TotalCalls": 1,
                "CallType": {
                    "$cond": [
                        {"$eq": ["$FirstCall", "$FirstCall"]}, "FirstCall",
                        "LastCall"
                    ]
                }
            }
        })

    # Project Stage for other filters
    if filter_type != "first_last_avg":
        # For daily, weekly, hourly, weekday - include unique counts with proper null filtering
        if filter_type in ["daily", "weekly", "hourly", "daily_hourly", "weekday"]:
            # Filter out null/empty values and get unique counts
            pipeline.append({
                "$addFields": {
                    "FilteredCellIDs": {
                        "$filter": {
                            "input": "$AllCellIDs",
                            "as": "cell",
                            "cond": {
                                "$and": [
                                    {"$ne": ["$$cell", None]},
                                    {"$ne": ["$$cell", ""]}
                                ]
                            }
                        }
                    },
                    "FilteredIMEIs": {
                        "$filter": {
                            "input": "$AllIMEIs",
                            "as": "imei",
                            "cond": {
                                "$and": [
                                    {"$ne": ["$$imei", None]},
                                    {"$ne": ["$$imei", ""]}
                                ]
                            }
                        }
                    },
                    "FilteredIMSIs": {
                        "$filter": {
                            "input": "$AllIMSIs",
                            "as": "imsi",
                            "cond": {
                                "$and": [
                                    {"$ne": ["$$imsi", None]},
                                    {"$ne": ["$$imsi", ""]}
                                ]
                            }
                        }
                    }
                }
            })

            pipeline.append({
                "$addFields": {
                    "TotalCellIDsCount": {"$size": "$FilteredCellIDs"},
                    "TotalIMEIsCount": {"$size": "$FilteredIMEIs"},
                    "TotalIMSIsCount": {"$size": "$FilteredIMSIs"}
                }
            })

            project_stage = {
                "_id": 0,
                "TotalCalls": 1,
                "OutCalls": 1,
                "InCalls": 1,
                "OutSms": 1,
                "InSms": 1,
                "OtherCalls": 1,
                "WifiCalls": 1,
                "RoamCalls": 1,
                "RoamSms": 1,
                "TotalDuration": 1,
                "TotalCellIDs": "$TotalCellIDsCount",
                "TotalIMEIs": "$TotalIMEIsCount",
                "TotalIMSIs": "$TotalIMSIsCount",
                "TotalDays": {
                    "$add": [{
                        "$dateDiff": {
                            "startDate": "$FirstCallDate",
                            "endDate": "$LastCallDate",
                            "unit": "day"
                        }
                    }, 1]
                },
                "FirstCallDate": {"$dateToString": {"format": "%d-%m-%Y", "date": "$FirstCallDate"}},
                "FirstCallTime": {"$dateToString": {"format": "%H:%M:%S", "date": "$FirstCallDate"}},
                "LastCallDate": {"$dateToString": {"format": "%d-%m-%Y", "date": "$LastCallDate"}},
                "LastCallTime": {"$dateToString": {"format": "%H:%M:%S", "date": "$LastCallDate"}},
            }

            if filter_type == "daily":
                project_stage.update({
                    "CdrNo": "$_id.A_Party",
                    "Date": "$_id.Day"
                })

            elif filter_type == "weekly":
                project_stage.update({
                    "CdrNo": "$_id.A_Party",
                    "WeekNum": "$_id.WeekNum"
                })
            elif filter_type == "weekday":
                project_stage.update({
                    "CdrNo": "$_id.A_Party",
                    "Day": "$_id.sai",
                    "DayOfWeek": "$_id.DayOfWeek"
                })
            elif filter_type == "hourly":
                project_stage.update({
                    "CdrNo": "$_id.A_Party",
                    "Date": "$_id.Day",
                    "Hour": "$_id.Hour"
                })
            elif filter_type == "daily_hourly":
                project_stage.update({
                    "Day": "$_id.Day",
                    "Hour": "$_id.Hour"
                })

        else:
            # For other filter types, include unique counts
            pipeline.append({
                "$addFields": {
                    "TotalCellIDsCount": {"$size": "$TotalCellIDs"},
                    "TotalIMEIsCount": {"$size": "$TotalIMEIs"},
                    "TotalIMSIsCount": {"$size": "$TotalIMSIs"}
                }
            })

            # For transactional_summary, calculate TotalDays as count of unique dates
            if filter_type == "transactional_summary":
                pipeline.append({
                    "$addFields": {
                        "TotalDaysCount": {"$size": {"$ifNull": ["$AllDates", []]}}
                    }
                })

            project_stage = {
                "_id": 0,
                "TotalCalls": 1,
                "OutCalls": 1,
                "InCalls": 1,
                "OutSms": 1,
                "InSms": 1,
                "RoamCalls": 1,
                "RoamSms": 1,
                "WifiCalls": 1,
                "OtherCalls": 1,
                "TotalDuration": 1,
                "TotalCellIDs": "$TotalCellIDsCount",
                "TotalIMEIs": "$TotalIMEIsCount",
                "TotalIMSIs": "$TotalIMSIsCount",
                "CellID": 1,
                "IMEI": 1,
                "IMSI": 1,
                "LRN": 1,
                "IMEI_TAC": 1,
                "B_Party_Code": 1,
                "FirstCallDate": {"$dateToString": {"format": "%d-%m-%Y", "date": "$FirstCallDate"}},
                "LastCallDate": {"$dateToString": {"format": "%d-%m-%Y", "date": "$LastCallDate"}},
                "FirstCallTime": {"$dateToString": {"format": "%H:%M:%S", "date": "$FirstCallDate"}},
                "LastCallTime": {"$dateToString": {"format": "%H:%M:%S", "date": "$LastCallDate"}},
            }

            # Calculate TotalDays differently for transactional_summary
            if filter_type == "transactional_summary":
                project_stage["TotalDays"] = "$TotalDaysCount"
            else:
                project_stage["TotalDays"] = {
                    "$add": [{
                        "$dateDiff": {
                            "startDate": "$FirstCallDate",
                            "endDate": "$LastCallDate",
                            "unit": "day"
                        }
                    }, 1]
                }

            if filter_type == "monthly":
                project_stage.update({
                    "A_Party": "$_id.A_Party",
                    "MonthYear": "$_id.MonthYear",
                    "MonthNumber": "$_id.MonthNumber",
                    "MonthLabel": 1
                })
            elif filter_type == "imeisummary":
                project_stage.update({"IMEI": "$_id.IMEI", "B_Party": "$_id.B_Party"})
            elif filter_type == "transactional_summary":
                project_stage.update(
                    {"A_Party": "$_id.A_Party", "B_Party": "$_id.B_Party", "Operator": "$_id.Operator"})
            elif filter_type == "normal":
                project_stage.update({"A_Party": "$_id.A_Party", "B_Party": "$_id.B_Party"})
            elif filter_type == "Apartyimeisummary":
                project_stage.update({"A_Party": "$_id.A_Party", "IMEI": "$_id.IMEI"})
            elif filter_type == "Apartyimsisummary":
                project_stage.update({"A_Party": "$_id.A_Party", "IMSI": "$_id.IMSI"})

        pipeline.append({"$project": project_stage})

    return list(cdr_collection.aggregate(pipeline))