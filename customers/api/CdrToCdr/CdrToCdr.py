from bson import ObjectId
from mongoengine import get_db


def get_cdr_to_cdr_counts(cdr_numbers, from_date, to_date):
    """
    Find all calls where BOTH A_Party and B_Party are within our CDR group.
    Groups by (A_Party, B_Party) and returns call count + date range.

    WHY A_Party instead of seq_id:
      - seq_id is stored as an Array field in MongoDB (not a scalar)
      - Some records have no seq_id at all
      - A_Party is always a flat string — safe and reliable to match on
    """
    db = get_db(alias='cdr_db')
    collection = db['CallDetailRecords']

    pipeline = [
        {
            "$match": {
                "A_Party": {"$in": cdr_numbers},
                "B_Party": {"$in": cdr_numbers},
                "SDateTime": {"$gte": from_date, "$lte": to_date}
            }
        },
        {
            "$group": {
                "_id": {
                    "A_Party": "$A_Party",
                    "B_Party": "$B_Party"
                },
                "count": {"$sum": 1},
                "first_call": {"$min": "$SDateTime"},
                "last_call": {"$max": "$SDateTime"}
            }
        },
        {
            "$project": {
                "_id": 0,
                "A_Party": "$_id.A_Party",
                "B_Party": "$_id.B_Party",
                "count": 1,
                "first_call": 1,
                "last_call": 1
            }
        }
    ]

    return list(collection.aggregate(pipeline))