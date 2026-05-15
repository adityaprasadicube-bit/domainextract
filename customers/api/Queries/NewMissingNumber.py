from mongoengine.connection import get_db

def NewMissingNumber(seq_ids, filtervalue, from_date, to_date):
    isNew = filtervalue == "New"  # New = only in date range, Missing = only outside date range

    pipeline = [
        {
            "$match": {
                "seq_id": {"$in": seq_ids},
                "B_Party": {"$ne": None}
            }
        },
        {
            "$group": {
                "_id": {
                    "B_Party": "$B_Party",
                    "seq_id": "$seq_id"
                },
                "all_dates": {"$push": "$SDateTime"},
                "first_datetime": {"$min": "$SDateTime"},
                "last_datetime": {"$max": "$SDateTime"}
            }
        },
        {
            "$project": {
                "_id": 1,
                "first_datetime": 1,
                "last_datetime": 1,
                "in_range_flags": {
                    "$map": {
                        "input": "$all_dates",
                        "as": "dt",
                        "in": {
                            "$and": [
                                {"$gte": ["$$dt", from_date]},
                                {"$lte": ["$$dt", to_date]}
                            ]
                        }
                    }
                }
            }
        },
        {
            "$project": {
                "_id": 1,
                "first_datetime": 1,
                "last_datetime": 1,
                "all_in_range": {"$not": [{"$in": [False, "$in_range_flags"]}]},
                "any_in_range": {"$in": [True, "$in_range_flags"]}
            }
        },
        {
            "$group": {
                "_id": "$_id.B_Party",
                "seq_id": {"$addToSet": "$_id.seq_id"},
                "first_datetime": {"$min": "$first_datetime"},
                "last_datetime": {"$max": "$last_datetime"},
                "all_in_range_flags": {"$addToSet": "$all_in_range"},
                "any_in_range_flags": {"$addToSet": "$any_in_range"}
            }
        },
        {
            "$match": {
                "$expr": {
                    "$cond": {
                        "if": isNew,
                        "then": {
                            # Every seq_id must be fully in range and never out
                            "$and": [
                                {"$setEquals": ["$all_in_range_flags", [True]]},
                                {"$setEquals": ["$any_in_range_flags", [True]]}
                            ]
                        },
                        "else": {
                            # Every seq_id must be fully out of range (i.e. not even once in range)
                            "$setEquals": ["$any_in_range_flags", [False]]
                        }
                    }
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "B_Party": "$_id",
                "seq_id": 1,
                "first_datetime": 1,
                "last_datetime": 1
            }
        }
    ]

    db = get_db(alias='cdr_db')

    def is_valid_mobile(number):
        return (
                number
                and number.isdigit()
                and len(number) >= 10
                and not (number.startswith('140') or number.startswith('1800'))
        )

    results = list(db.CallDetailRecords.aggregate(pipeline))

    final_result = [
        doc for doc in results if is_valid_mobile(doc["B_Party"])
    ]

    return final_result
