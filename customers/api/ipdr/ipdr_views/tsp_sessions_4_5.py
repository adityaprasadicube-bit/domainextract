from mongoengine import get_db
import ipaddress

db = get_db(alias='ipdr_db')
collection = db["IPDetailRecords"]

def bytes_to_ip(b):
    """Convert binary IP to string (IPv4/IPv6). Returns empty string if None."""
    if b is None:
        return ""
    try:
        return str(ipaddress.ip_address(b))
    except Exception:
        return ""

def get_tcp_sessions_4_5(seq_id, from_date, to_date):
    pipeline = [
        # Step 0: Convert binary Destination_ip to string for reliable lookup
        {
            "$addFields": {
                "Destination_ip_str": {
                    "$function": {
                        "body": """
                            function(b) {
                                if (!b) return '';
                                var arr = Array.from(b);
                                if (arr.length === 4) return arr.join('.');
                                if (arr.length === 16) return arr.map(x => x.toString(16).padStart(2,'0')).join(':');
                                return '';
                            }
                        """,
                        "args": ["$Destination_ip"],
                        "lang": "js"
                    }
                }
            }
        },

        # Step 1: Match seq_id, datetime, and 4-5 digit ports
        {
            "$match": {
                "seq_id": seq_id,
                "SDateTime": {"$gte": from_date, "$lte": to_date},
                "Destination_port": {"$gte": 1000, "$lte": 99999}
            }
        },

        # Step 4: Project fields
        {
            "$project": {
                "_id": 0,
                "Destination_ip": 1,
                "Port": "$Destination_port",
                "SDateTime": 1,
                "EDateTime": 1,
                "Minutes": {"$divide": ["$Duration", 60]},

            }
        },

        # Step 5: Sort
        {"$sort": {"SDateTime": 1}}
    ]

    results = list(collection.aggregate(pipeline))

    # Convert binary IPs to string for JSON-safe output
    for r in results:
        r["Destination_ip"] = bytes_to_ip(r.get("Destination_ip"))

    return results
