from mongoengine.connection import get_db
def FirstLastLocation(pk, b_party_list):
    pipeline = [
        {
            "$match": {
                "B_Party": {"$in": b_party_list},
                "seq_id": {"$in": pk}
            }
        },
        {
            "$group": {
                "_id": {
                    "B_Party": "$B_Party",
                    "seq_id": "$seq_id"
                },
                "first_datetime": {"$min": "$SDateTime"},
                "last_datetime": {"$max": "$SDateTime"}
            }
        },
        {
            "$project": {
                "_id": 0,
                "B_Party": "$_id.B_Party",
                "seq_id": "$_id.seq_id",
                "first_datetime": 1,
                "last_datetime": 1
            }
        }
    ]

    db = get_db(alias='cdr_db')
    return list(db.CallDetailRecords.aggregate(pipeline))  # Replace with your collection name

# results → List of dicts: [{'B_Party': '9876543210', 'first_datetime': ..., 'last_datetime': ...}, ...]
