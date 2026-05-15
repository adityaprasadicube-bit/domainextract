from ...ipdr.ipdr_models.ip_model import PortInfo
from collections import defaultdict
from datetime import datetime
from ...ipdr.ipdr_models.ip_model import PortInfo

def build_summary_from_mapping(mapping_records):
    """
    Build enhanced summary from Mapping report:
    - Counts Call In, Call Out, Msg In, Msg Out
    - Tracks total duration
    - Tracks start/end UTC and IST
    - Includes Participant Provider and Participant Type
    """



    summary_map = defaultdict(lambda: {
        "Call In": 0,
        "Call Out": 0,
        "Msg In": 0,
        "Msg Out": 0,
        "Total Duration Seconds": 0,
        "Start UTC": None,
        "End UTC": None,
        "Start IST": None,
        "End IST": None,
        "Participant Provider": None,
        "Participant Type": None,
    })

    for record in mapping_records:
        participant = record.get("Participant")
        target = record.get("Target")
        call_type = record.get("Call Type")

        if not participant or not target:
            continue

        start_utc = record.get("Datetime Start UTC")
        end_utc = record.get("Datetime End UTC")
        start_ist = record.get("Datetime Start IST")
        end_ist = record.get("Datetime End IST")
        duration = record.get("Duration", 0) or 0

        key = (participant, target)
        entry = summary_map[key]

        # Count types
        if call_type in entry:
            entry[call_type] += 1

        # Duration
        if isinstance(duration, (int, float)):
            entry["Total Duration Seconds"] += int(duration)

        # Start UTC
        if isinstance(start_utc, datetime):
            if not entry["Start UTC"] or start_utc < entry["Start UTC"]:
                entry["Start UTC"] = start_utc

        # End UTC
        if isinstance(end_utc, datetime):
            if not entry["End UTC"] or end_utc > entry["End UTC"]:
                entry["End UTC"] = end_utc

        # Start IST
        if isinstance(start_ist, datetime):
            if not entry["Start IST"] or start_ist < entry["Start IST"]:
                entry["Start IST"] = start_ist

        # End IST
        if isinstance(end_ist, datetime):
            if not entry["End IST"] or end_ist > entry["End IST"]:
                entry["End IST"] = end_ist

        # Capture provider and type (first non-null)
        if not entry["Participant Provider"]:
            entry["Participant Provider"] = record.get("Participant Provider", "Unknown")
        if not entry["Participant Type"]:
            entry["Participant Type"] = record.get("Participant Type", "Unknown")

    # Format final output
    summary = []
    for (participant, target), data in summary_map.items():
        start = data["Start UTC"]
        end = data["End UTC"]
        start_ist = data["Start IST"]
        end_ist = data["End IST"]

        total_days = ""
        if start and end:
            total_days = (end.date() - start.date()).days + 1

        total_calls = (
                data["Call In"]
                + data["Call Out"]
                + data["Msg In"]
                + data["Msg Out"]
        )

        summary.append({
            "Target": target,
            "Participant": participant,
            "Call In": data["Call In"],
            "Call Out": data["Call Out"],
            "Msg In": data["Msg In"],
            "Msg Out": data["Msg Out"],
            "Total Calls": total_calls,
            "Total Days": total_days,
            "Total Duration(s)": data["Total Duration Seconds"],
            "Start UTC": start.isoformat() if start else None,
            "End UTC": end.isoformat() if end else None,
            "Start IST": start_ist.isoformat() if start_ist else None,
            "End IST": end_ist.isoformat() if end_ist else None,
            "Par Provider": data["Participant Provider"],
            "Par Type": data["Participant Type"],
        })

    return summary

def generate_ip_wise_summary(mapping_data):
    summary = defaultdict(lambda: {
        "Call In": 0,
        "Call Out": 0,
        "Msg In": 0,
        "Msg Out": 0,
        "Total Calls": 0,
        "Total Duration": 0,
        "Start UTC": None,
        "End UTC": None,
        "Start IST": None,
        "End IST": None,
        "Provider": "",
        "Participant Type": ""
    })

    # Cache for port info lookups
    port_info_cache = {}

    for row in mapping_data:
        target = row.get("Target")
        participant = row.get("Participant")

        target_ip = row.get("Target Ip")
        participant_ip = row.get("participant_ip")

        target_port = row.get("Target Port")
        participant_port = row.get("participant_port") or row.get("Participant Port")

        # ✅ NORMALIZE PORT - DON'T CONVERT IF IT CONTAINS PIPE
        # Keep as string if it contains multiple ports separated by |
        if participant_port not in (None, ""):
            participant_port_str = str(participant_port).strip()
            if "|" not in participant_port_str:
                try:
                    participant_port = int(participant_port_str)
                except (ValueError, TypeError):
                    participant_port = None
            else:
                # Keep as string for multiple ports
                participant_port = participant_port_str

        provider = row.get("Participant Provider")
        ptype = row.get("Participant Type")

        call_type = row.get("Call Type")
        duration = row.get("Duration", 0)

        start_utc = row.get("Datetime Start UTC")
        end_utc = row.get("Datetime End UTC")
        start_ist = row.get("Datetime Start IST")
        end_ist = row.get("Datetime End IST")

        # Skip records without any IP information
        if not target_ip and not participant_ip:
            continue

        # 🔑 UNIQUE COMPOSITE KEY
        key = (
            target,
            participant,
            target_ip,
            participant_ip,
            target_port,
            participant_port
        )

        s = summary[key]

        # Call / Message classification
        if call_type == "Call In":
            s["Call In"] += 1
        elif call_type == "Call Out":
            s["Call Out"] += 1
        elif call_type == "Msg In":
            s["Msg In"] += 1
        elif call_type == "Msg Out":
            s["Msg Out"] += 1

        s["Total Calls"] += 1
        s["Total Duration"] += duration

        # Time aggregation
        if start_utc:
            s["Start UTC"] = min(s["Start UTC"], start_utc) if s["Start UTC"] else start_utc
        if end_utc:
            s["End UTC"] = max(s["End UTC"], end_utc) if s["End UTC"] else end_utc

        if start_ist:
            s["Start IST"] = min(s["Start IST"], start_ist) if s["Start IST"] else start_ist
        if end_ist:
            s["End IST"] = max(s["End IST"], end_ist) if s["End IST"] else end_ist

        # Static fields
        s["Provider"] = provider
        s["Participant Type"] = ptype

    # Helper function to lookup port info (handles both single and multiple ports)
    def get_port_info(port_value):
        """Get port description, handles single port (int) or multiple ports (string with |)"""
        if port_value is None or port_value == "":
            return None

        # Handle multiple ports separated by |
        if isinstance(port_value, str) and "|" in port_value:
            port_list = [p.strip() for p in port_value.split("|")]
            descriptions = []
            for port_str in port_list:
                try:
                    port_int = int(port_str)
                    if port_int not in port_info_cache:
                        try:
                            port_obj = PortInfo.objects.get(id=port_int)
                            port_info_cache[port_int] = port_obj.Description
                        except PortInfo.DoesNotExist:
                            port_info_cache[port_int] = None

                    desc = port_info_cache[port_int]
                    descriptions.append(desc if desc else "Unknown")
                except (ValueError, TypeError):
                    descriptions.append("Unknown")

            return " | ".join(descriptions)

        # Handle single port
        else:
            try:
                port_int = int(port_value)
                if port_int not in port_info_cache:
                    try:
                        port_obj = PortInfo.objects.get(id=port_int)
                        port_info_cache[port_int] = port_obj.Description
                    except PortInfo.DoesNotExist:
                        port_info_cache[port_int] = None

                return port_info_cache[port_int]
            except (ValueError, TypeError):
                return None

    # 🔄 Final structured output
    output = []
    for (
            target,
            participant,
            target_ip,
            participant_ip,
            target_port,
            participant_port
    ), v in summary.items():
        # Lookup target port info
        target_port_description = get_port_info(target_port)

        # Lookup participant port info
        participant_port_description = get_port_info(participant_port)

        output.append({
            "Target": target,
            "Participant": participant,
            "Target IP": target_ip,
            "Target Port": target_port,
            "Target PortInfo": target_port_description,
            "Participant IP": participant_ip,
            "Participant Port": participant_port,
            "Participant PortInfo": participant_port_description,
            "Call In": v["Call In"],
            "Call Out": v["Call Out"],
            "Msg In": v["Msg In"],
            "Msg Out": v["Msg Out"],
            "Total Calls": v["Total Calls"],
            "Total Duration": v["Total Duration"],
            "Start UTC": v["Start UTC"],
            "End UTC": v["End UTC"],
            "Start IST": v["Start IST"],
            "End IST": v["End IST"],
            "Provider": v["Provider"],
            "Participant Type": v["Participant Type"],
        })
    return output

# IP PORT summary


def generate_target_ip_port_summary(mapping_data):
    from collections import defaultdict
    from datetime import datetime

    summary = defaultdict(lambda: {
        "Call In": 0,
        "Call Out": 0,
        "Msg In": 0,
        "Msg Out": 0,
        "Total Duration": 0,
        "Start UTC": None,
        "End UTC": None,
        "Start IST": None,
        "End IST": None,
        "Target ISP": None,
    })

    # Cache for port info lookups
    port_info_cache = {}

    for row in mapping_data:
        target = row.get("Target")
        target_ip = row.get("Target Ip") or row.get("target_ip")
        target_port = row.get("Target Port")

        if not target:
            continue

        # Normalize port - DON'T CONVERT IF IT CONTAINS PIPE
        if target_port not in (None, ""):
            target_port_str = str(target_port).strip()
            if "|" not in target_port_str:
                try:
                    target_port = int(target_port_str)
                except (ValueError, TypeError):
                    target_port = None
            else:
                # Keep as string for multiple ports
                target_port = target_port_str

        call_type = row.get("Call Type")
        duration = row.get("Duration", 0) or 0

        start_utc = row.get("Datetime Start UTC")
        end_utc = row.get("Datetime End UTC")
        start_ist = row.get("Datetime Start IST")
        end_ist = row.get("Datetime End IST")

        target_isp = row.get("target isp_org") or row.get("Target ISP") or row.get("Target Org")

        # 🔑 KEY: Group records without Target IP separately
        # If no target_ip, use empty string and None for port
        if not target_ip or str(target_ip).strip() == "":
            key = (target, "", None)
        else:
            key = (target, target_ip, target_port)

        s = summary[key]

        # Count call/message types
        if call_type == "Call In":
            s["Call In"] += 1
        elif call_type == "Call Out":
            s["Call Out"] += 1
        elif call_type == "Msg In":
            s["Msg In"] += 1
        elif call_type == "Msg Out":
            s["Msg Out"] += 1

        s["Total Duration"] += int(duration)

        # Time aggregation
        if isinstance(start_utc, datetime):
            s["Start UTC"] = min(s["Start UTC"], start_utc) if s["Start UTC"] else start_utc
        if isinstance(end_utc, datetime):
            s["End UTC"] = max(s["End UTC"], end_utc) if s["End UTC"] else end_utc

        if isinstance(start_ist, datetime):
            s["Start IST"] = min(s["Start IST"], start_ist) if s["Start IST"] else start_ist
        if isinstance(end_ist, datetime):
            s["End IST"] = max(s["End IST"], end_ist) if s["End IST"] else end_ist

        # Static
        if not s["Target ISP"]:
            s["Target ISP"] = target_isp

    # Helper function to lookup port info (handles both single and multiple ports)
    def get_port_info(port_value):
        """Get port description, handles single port (int) or multiple ports (string with |)"""
        if port_value is None or port_value == "":
            return None

        # Handle multiple ports separated by |
        if isinstance(port_value, str) and "|" in port_value:
            port_list = [p.strip() for p in port_value.split("|")]
            descriptions = []
            for port_str in port_list:
                try:
                    port_int = int(port_str)
                    if port_int not in port_info_cache:
                        try:
                            port_obj = PortInfo.objects.get(id=port_int)
                            port_info_cache[port_int] = port_obj.Description
                        except PortInfo.DoesNotExist:
                            port_info_cache[port_int] = None

                    desc = port_info_cache[port_int]
                    descriptions.append(desc if desc else "")
                except (ValueError, TypeError):
                    descriptions.append("")

            return " | ".join(descriptions) if len(descriptions) > 2 else ""

        # Handle single port
        else:
            try:
                port_int = int(port_value)
                if port_int not in port_info_cache:
                    try:
                        port_obj = PortInfo.objects.get(id=port_int)
                        port_info_cache[port_int] = port_obj.Description
                    except PortInfo.DoesNotExist:
                        port_info_cache[port_int] = None

                return port_info_cache[port_int]
            except (ValueError, TypeError):
                return None

    # 🔄 Final Output
    output = []
    for (target, target_ip, target_port), v in summary.items():
        total_calls = (
                v["Call In"]
                + v["Call Out"]
                + v["Msg In"]
                + v["Msg Out"]
        )

        total_days = ""
        if v["Start IST"] and v["End IST"]:
            total_days = (v["End IST"].date() - v["Start IST"].date()).days + 1

        # Lookup port info using helper function
        port_description = get_port_info(target_port)

        output.append({
            "Target": target,
            "Target IP": target_ip if target_ip else "",
            "Target ISP / Org": v["Target ISP"],
            "Target Port": target_port,
            "Target PortInfo": port_description,
            "Total Calls": total_calls,
            "Call In": v["Call In"],
            "Call Out": v["Call Out"],
            "Msg In": v["Msg In"],
            "Msg Out": v["Msg Out"],
            "Total Duration": v["Total Duration"],
            "Total Days": total_days,
            "Start IST": v["Start IST"],
            "End IST": v["End IST"],
            "Start UTC": v["Start UTC"],
            "End UTC": v["End UTC"],
        })

    return output


def generate_target_ip_summary(mapping_data):


    summary = defaultdict(lambda: {
        "Call In": 0,
        "Call Out": 0,
        "Msg In": 0,
        "Msg Out": 0,
        "Total Duration": 0,
        "Start UTC": None,
        "End UTC": None,
        "Start IST": None,
        "End IST": None,
        "Target ISP": None,
    })

    for row in mapping_data:
        target = row.get("Target")
        target_ip = row.get("Target Ip") or row.get("target_ip")

        if not target:
            continue

        call_type = row.get("Call Type")
        duration = row.get("Duration", 0) or 0

        start_utc = row.get("Datetime Start UTC")
        end_utc = row.get("Datetime End UTC")
        start_ist = row.get("Datetime Start IST")
        end_ist = row.get("Datetime End IST")

        target_isp = row.get("target isp_org") or row.get("Target ISP") or row.get("Target Org")

        # 🔑 KEY: Group records without Target IP separately
        # If no target_ip, use empty string as key component
        if not target_ip or str(target_ip).strip() == "":
            key = (target, "")
        else:
            key = (target, target_ip)

        s = summary[key]

        # Count types
        if call_type == "Call In":
            s["Call In"] += 1
        elif call_type == "Call Out":
            s["Call Out"] += 1
        elif call_type == "Msg In":
            s["Msg In"] += 1
        elif call_type == "Msg Out":
            s["Msg Out"] += 1

        s["Total Duration"] += int(duration)

        # Time aggregation
        if isinstance(start_utc, datetime):
            s["Start UTC"] = min(s["Start UTC"], start_utc) if s["Start UTC"] else start_utc
        if isinstance(end_utc, datetime):
            s["End UTC"] = max(s["End UTC"], end_utc) if s["End UTC"] else end_utc

        if isinstance(start_ist, datetime):
            s["Start IST"] = min(s["Start IST"], start_ist) if s["Start IST"] else start_ist
        if isinstance(end_ist, datetime):
            s["End IST"] = max(s["End IST"], end_ist) if s["End IST"] else end_ist

        if not s["Target ISP"]:
            s["Target ISP"] = target_isp

    # 🔄 Final Output
    output = []
    for (target, target_ip), v in summary.items():
        total_calls = (
                v["Call In"]
                + v["Call Out"]
                + v["Msg In"]
                + v["Msg Out"]
        )

        total_days = ""
        if v["Start IST"] and v["End IST"]:
            total_days = (v["End IST"].date() - v["Start IST"].date()).days + 1

        output.append({
            "Target": target,
            "Target IP": target_ip if target_ip else "",
            "Target ISP / Org": v["Target ISP"],
            "Total Calls": total_calls,
            "Call In": v["Call In"],
            "Call Out": v["Call Out"],
            "Msg In": v["Msg In"],
            "Msg Out": v["Msg Out"],
            "Total Duration": v["Total Duration"],
            "Total Days": total_days,
            "Start IST": v["Start IST"],
            "End IST": v["End IST"],
            "Start UTC": v["Start UTC"],
            "End UTC": v["End UTC"],
        })

    return output