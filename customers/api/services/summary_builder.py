def build_summary(data, alerts):

    summary = f"""
Number {data['number']} shows telecom activity with {data['total_records']} total records.

Calls:
Incoming: {data['incoming_calls']}
Outgoing: {data['outgoing_calls']}

SMS:
Incoming: {data['incoming_sms']}
Outgoing: {data['outgoing_sms']}

Unique Contacts: {data['unique_contacts']}
Frequent Contact: {data['frequent_contact']}

Primary Location: {data['location']}
"""

    if alerts:
        summary += "\nAlerts:\n"
        for alert in alerts:
            summary += f"- {alert}\n"

    return summary.strip()