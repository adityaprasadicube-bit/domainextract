def analyze_activity(data):

    alerts = []

    if data["total_records"] > 1000:
        alerts.append("High communication activity detected")

    if len(data["imei"]) > 1:
        alerts.append("Multiple devices (IMEI) used by this number")

    if data["unique_contacts"] > 100:
        alerts.append("Large number of unique contacts")

    return alerts

def detect_suspicious_activity(stats):

    alerts = []

    if stats["total_records"] > 1000:
        alerts.append("High communication volume detected")

    if len(stats["imei"]) > 1:
        alerts.append("Multiple devices used")

    if stats["unique_contacts"] > 100:
        alerts.append("Large contact network")

    return alerts