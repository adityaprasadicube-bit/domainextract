from datetime import datetime
from mongoengine import get_db

def get_Providerandotherstateinformation(seq_ids, from_date, to_date):
    db = get_db(alias='tower_dump')
    collection = db['CallDetailRecords']  # must match the meta collection name

    pipeline = [
        {
            "$match": {
                "seq_id": {"$in": seq_ids},
                "SDateTime": {"$gte": from_date, "$lte": to_date}
            }
        },
        {
            "$group": {
                "_id": {
                    "A_Party": "$A_Party",
                    "B_Party": "$B_Party"
                },
                "total_calls": {"$sum": 1},
                "total_duration": {"$sum": "$Duration"},
                "first_date": {"$min": "$SDateTime"},
                "last_date": {"$max": "$SDateTime"},
                "first_cgi_list": {"$addToSet": "$First_CGI"},
                "last_cgi_list": {"$addToSet": "$Last_CGI"},
                "imei_list": {"$addToSet": "$IMEI"},
                "imsi_list": {"$addToSet": "$IMSI"},
                "connection_types": {"$addToSet": "$Con_Type"}
            }
        },
        {
            "$project": {
                "_id": 0,
                "A_Party": "$_id.A_Party",
                "B_Party": "$_id.B_Party",
                "total_calls": 1,
                "total_duration": 1,
                "first_date": 1,
                "last_date": 1,
                "first_cgi_list": 1,
                "last_cgi_list": 1,
                "imei_list": 1,
                "imsi_list": 1,
                "connection_types": 1
            }
        }
    ]

    result = list(collection.aggregate(pipeline))
    return result
