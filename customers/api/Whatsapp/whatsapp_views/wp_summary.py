"""
wp_summary.py
─────────────────
Advanced activity summary for WhatsApp CDR data.

Sections
--------
  Activity Summary · Analysis Summary · Top Contacts
  IP Connections   · Device Usage     · Time Usage

Public API
----------
    generate_advanced_summary(all_summaries, contacts_info=None, top_n=10)

    contacts_info  – the "contacts info" list from fetch_info_data():
                     each row has keys:
                       symmetric_contacts   → saved by BOTH sides
                       asymmetric_contacts  → saved only by target

Group Detection Logic
---------------------
  A record is treated as a GROUP interaction when:
    • The "Participant" field contains "|" (multiple participants separated by pipe), OR
    • record_type / style contains "group", OR
    • Call Type contains "group" (case-insensitive)
"""

import re
from collections import Counter
from datetime import datetime


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _safe_str(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_dt(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "").strip())
        except ValueError:
            pass
    return None


def _clean_number(num: str) -> str:
    """Strip to digits only, then remove leading 91 for 12-digit Indian numbers."""
    digits = re.sub(r"\D", "", num)
    if digits.startswith("91") and len(digits) == 12:
        return digits[2:]
    if digits.startswith("0") and len(digits) == 11:
        return digits[1:]
    return digits


def _is_group(r: dict) -> bool:
    """
    Return True if this CDR record represents a group interaction.

    Detection rules (any one is sufficient):
      1. Participant field contains "|" separator (multiple participants)
      2. record_type or style contains the word "group"
      3. Call Type contains the word "group"
    """
    participant = _safe_str(r.get("Participant"))
    if "|" in participant:
        return True

    for field in ("record_type", "Type", "style", "Style", "Call Type", "Call_Type"):
        if "group" in _safe_str(r.get(field)).lower():
            return True

    return False

def group_activity(records: list):
    voice_calls     = 0
    video_calls     = 0
    msg_sent        = 0
    msg_recv        = 0
    images_shared   = 0
    contacts_shared = 0
    group_calls = 0  # group voice + group video calls combined
    group_msgs = 0  # group messages sent + received combined

    for r in records:
        call_type = _safe_str(r.get("Call Type") or r.get("Call_Type"))
        record_type = _safe_str(r.get("record_type") or r.get("Type")).lower()
        is_grp = _is_group(r)

        if call_type in ("Call In", "Call Out"):
            if "video" in record_type:
                video_calls += 1
            else:
                voice_calls += 1
            if is_grp:
                group_calls += 1

        elif call_type in ("Msg Out", "Msg In"):
            if call_type == "Msg Out":
                msg_sent += 1
            else:
                msg_recv += 1
            if is_grp:
                group_msgs += 1

        if "image" in record_type or "photo" in record_type:
            images_shared += 1
        if "contact" in record_type or "vcard" in record_type:
            contacts_shared += 1
    return {
        # ── group additions ───────────────────────────────────────────────
        "Group Calls"      : group_calls,
        "Group Messages"   : group_msgs,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  COUNTRY CODES  (for international normalisation in Top Contacts)
# ─────────────────────────────────────────────────────────────────────────────

COUNTRY_CODES = {
    "1","7","20","27","30","31","32","33","34","36","39","40","41","43","44",
    "45","46","47","48","49","51","52","53","54","55","56","57","58","60","61",
    "62","63","64","65","66","81","82","84","86","90","91","92","93","94","95",
    "98","212","213","216","218","220","221","222","223","224","225","226","227",
    "228","229","230","231","232","233","234","235","236","237","238","239","240",
    "241","242","243","244","245","246","247","248","249","250","251","252","253",
    "254","255","256","257","258","260","261","262","263","264","265","266","267",
    "268","269","290","291","297","298","299","345","350","351","352","353","354",
    "355","356","357","358","359","370","371","372","373","374","375","376","377",
    "378","380","381","385","386","387","389","420","421","441","473","500","501",
    "502","503","504","505","506","507","508","509","590","591","592","593","594",
    "595","596","597","598","599","649","664","670","672","673","674","675","676",
    "677","678","679","680","681","682","683","684","685","686","687","688","689",
    "690","691","692","758","767","787","808","809","850","852","853","855","856",
    "868","869","876","880","886","960","961","962","963","964","965","966","967",
    "968","971","972","973","974","975","976","977","993","994","995","996","998",
}


def _normalize_number(num: str) -> str:
    """Normalize Indian and international numbers to display format."""
    digits = re.sub(r"\D", "", num)

    if digits.startswith("91") and len(digits) == 12:
        return f"+91 {digits[2:]}"
    if digits.startswith("0") and len(digits) == 11:
        return f"+91 {digits[1:]}"
    if len(digits) == 10 and digits[0] in "6789":
        return f"+91 {digits}"

    for i in range(1, 5):
        code = digits[:i]
        if code in COUNTRY_CODES:
            return f"+{code} {digits[i:]}"

    return num


# ─────────────────────────────────────────────────────────────────────────────
#  SECTION 1 – ACTIVITY SUMMARY
# ─────────────────────────────────────────────────────────────────────────────

def _build_activity_summary(records: list) -> dict:
    voice_calls     = 0
    video_calls     = 0
    msg_sent        = 0
    msg_recv        = 0
    images_shared   = 0
    contacts_shared = 0

    # ── NEW: group counters ────────────────────────────────────────────────
    group_calls     = 0   # group voice + group video calls combined
    group_msgs      = 0   # group messages sent + received combined

    for r in records:
        call_type   = _safe_str(r.get("Call Type") or r.get("Call_Type"))
        record_type = _safe_str(r.get("record_type") or r.get("Type")).lower()
        is_grp      = _is_group(r)

        if call_type in ("Call In", "Call Out"):
            if "video" in record_type:
                video_calls += 1
            else:
                voice_calls += 1
            if is_grp:
                group_calls += 1

        elif call_type in ("Msg Out", "Msg In"):
            if call_type == "Msg Out":
                msg_sent += 1
            else:
                msg_recv += 1
            if is_grp:
                group_msgs += 1

        if "image" in record_type or "photo" in record_type:
            images_shared += 1
        if "contact" in record_type or "vcard" in record_type:
            contacts_shared += 1

    return {
        "Voice Calls"      : voice_calls,
        "Video Calls"      : video_calls,
        "Messages Sent"    : msg_sent,
        "Messages Received": msg_recv,
        "Images Shared"    : images_shared,
        "Contacts Shared"  : contacts_shared,

    }


# ─────────────────────────────────────────────────────────────────────────────
#  SECTION 2 – ANALYSIS SUMMARY
# ─────────────────────────────────────────────────────────────────────────────

def _extract_all_participants(records: list) -> Counter:
    counter: Counter = Counter()
    for r in records:
        participant = _safe_str(r.get("Participant"))
        if not participant or participant in ("N/A", "null", "None"):
            continue
        for num in participant.split("|"):
            num = num.strip()
            if num:
                counter[_clean_number(num)] += 1
    return counter


def _build_analysis_summary(records: list, contacts_info: list, top_n: int = 10) -> dict:
    symmetric_set  = set()
    asymmetric_set = set()

    for row in (contacts_info or []):
        sym  = _safe_str(row.get("symmetric_contacts"))
        asym = _safe_str(row.get("asymmetric_contacts"))
        if sym  and sym  not in ("None", "null", ""):
            symmetric_set.add(_clean_number(sym))
        if asym and asym not in ("None", "null", ""):
            asymmetric_set.add(_clean_number(asym))

    known_set    = symmetric_set | asymmetric_set
    cdr_counter  = _extract_all_participants(records)
    cdr_set      = set(cdr_counter.keys())

    active_set   = cdr_set & known_set
    unknown_set  = cdr_set - known_set
    inactive_set = known_set - cdr_set

    unknown_ranked = sorted(
        [(num, cdr_counter[num]) for num in unknown_set],
        key=lambda x: x[1], reverse=True
    )[:top_n]

    return {
        "Saved Contacts"   : len(known_set),
        "Active Contacts"  : len(active_set),
        "Unknown Numbers"  : len(unknown_set),
        "Inactive Contacts": len(inactive_set),
        "Unknown Numbers Detail": {
            _normalize_number(num): count
            for num, count in unknown_ranked
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
#  SECTION 3 – TOP CONTACTS
# ─────────────────────────────────────────────────────────────────────────────

def _build_top_contacts(records: list, top_n: int = 10) -> dict:
    counter: Counter = Counter()

    for r in records:
        participant = _safe_str(r.get("Participant"))
        if not participant or participant in ("N/A", "null", "None"):
            continue
        for number in participant.split("|"):
            number = number.strip()
            if number:
                counter[_normalize_number(number)] += 1

    return {num: cnt for num, cnt in counter.most_common(top_n)}


# ─────────────────────────────────────────────────────────────────────────────
#  SECTION 4 – IP CONNECTIONS
# ─────────────────────────────────────────────────────────────────────────────

def _build_ip_connections(records: list, top_n: int = 10) -> dict:
    country_counter: Counter = Counter()

    for r in records:
        for prefix in ("target", "participant"):
            country = _safe_str(r.get(f"{prefix} country"))
            if country and country.lower() not in ("unknown", "null", ""):
                for c in country.split("|"):
                    c = c.strip()
                    if c:
                        country_counter[c] += 1

    return {country: count for country, count in country_counter.most_common(top_n)}


# ─────────────────────────────────────────────────────────────────────────────
#  SECTION 4b – TOP IP CONNECTIONS
# ─────────────────────────────────────────────────────────────────────────────

def _build_top_ips(records: list, top_n: int = 5) -> list:
    ip_counter : Counter       = Counter()
    ip_country : dict          = {}

    for r in records:
        for ip_field, country_field in (
            ("participant_ip",  "participant country"),
            ("participant ip",  "participant country"),
        ):
            ip      = _safe_str(r.get(ip_field))
            country = _safe_str(r.get(country_field))

            if not ip or ip.lower() in ("none", "null", "n/a", ""):
                continue

            for single_ip in ip.split("|"):
                single_ip = single_ip.strip()
                if not single_ip:
                    continue
                ip_counter[single_ip] += 1
                if single_ip not in ip_country and country:
                    for c in country.split("|"):
                        c = c.strip()
                        if c and c.lower() not in ("none", "null", "unknown", ""):
                            ip_country[single_ip] = c
                            break

    return [
        {"ip": ip, "count": count, "country": ip_country.get(ip, "Unknown")}
        for ip, count in ip_counter.most_common(top_n)
    ]


# ─────────────────────────────────────────────────────────────────────────────
#  SECTION 5 – DEVICE USAGE
# ─────────────────────────────────────────────────────────────────────────────

_DEVICE_KEYWORDS = {
    "Android": ["android"],
    "iPhone" : ["iphone", "ios", "apple"],
    "iPad"   : ["ipad"],
    "Web"    : ["web", "windows", "mac", "linux", "chrome", "firefox"],
}

def _classify_device(device_str: str) -> str:
    d = device_str.lower()
    for label, keywords in _DEVICE_KEYWORDS.items():
        if any(kw in d for kw in keywords):
            return label
    return "Unknown"


def _build_device_usage(records: list) -> dict:
    counter: Counter = Counter()
    total = 0

    for r in records:
        for field in ("Target Device", "participant_device", "Participant Device"):
            device = _safe_str(r.get(field))
            if device and device.lower() not in ("n/a", "null", "none", "unknown", ""):
                counter[_classify_device(device)] += 1
                total += 1

    return {
        label: f"{round((counter[label] / total) * 100, 1)}%"
        for label in ("Android", "iPhone", "iPad", "Web", "Unknown")
        if counter.get(label, 0) > 0
    }


# ─────────────────────────────────────────────────────────────────────────────
#  SECTION 6 – TIME USAGE
# ─────────────────────────────────────────────────────────────────────────────

def _build_time_usage(records: list) -> dict:
    slot_counter: Counter = Counter()
    total = 0

    for r in records:
        dt = _parse_dt(r.get("Datetime Start IST") or r.get("Datetime Start UTC"))
        if not dt:
            continue

        hour = dt.hour
        if   6  <= hour < 12:  slot = "Morning"
        elif 12 <= hour < 17:  slot = "Afternoon"
        elif 17 <= hour < 24:  slot = "Night"
        else:                  slot = "Late Night"

        slot_counter[slot] += 1
        total += 1

    return {
        slot: f"{round((slot_counter[slot] / total) * 100, 1)}%"
        for slot in ("Morning", "Afternoon", "Night", "Late Night")
        if slot_counter.get(slot, 0) > 0
    }


# ─────────────────────────────────────────────────────────────────────────────
#  SECTION 7 – ALERTS & FLAGS
# ─────────────────────────────────────────────────────────────────────────────

_ALERT_THRESHOLDS = {
    "multi_device_min_types"       : 2,
    "international_min_countries"  : 1,
    "concentrated_contacts_ratio"  : 0.70,
    "unsaved_interaction_ratio"    : 0.30,
    "tower_movement_min_towers"    : 3,
    # ── group thresholds ──────────────────────────────────────────────────
    "group_call_min"               : 1,    # any group call triggers alert
    "group_msg_min"                : 1,    # any group message triggers alert
}

_DOMESTIC_COUNTRIES = {"India", "IN", "india"}


def _build_alerts(
    records       : list,
    activity      : dict,
    analysis      : dict,
    top_contacts  : dict,
    ip_connections: dict,
    device_usage  : dict,
) -> list:
    alerts: list = []

    if len(device_usage) >= _ALERT_THRESHOLDS["multi_device_min_types"]:
        alerts.append("Multiple device usage detected")

    intl_countries = [c for c in ip_connections if c not in _DOMESTIC_COUNTRIES]
    if len(intl_countries) >= _ALERT_THRESHOLDS["international_min_countries"]:
        alerts.append("International connections observed")

    total_interactions = sum(top_contacts.values()) if top_contacts else 0
    top5_interactions  = sum(list(top_contacts.values())[:5])
    if total_interactions > 0:
        if (top5_interactions / total_interactions) >= _ALERT_THRESHOLDS["concentrated_contacts_ratio"]:
            alerts.append("High interaction with limited contacts")

    unknown_count  = analysis.get("Unknown Numbers", 0)
    active_count   = analysis.get("Active Contacts", 0)
    total_contacts = unknown_count + active_count
    if total_contacts > 0:
        if (unknown_count / total_contacts) >= _ALERT_THRESHOLDS["unsaved_interaction_ratio"]:
            alerts.append("Unsaved numbers frequently contacted")

    # ── NEW: group activity alerts ─────────────────────────────────────────
    if activity.get("Group Calls", 0) >= _ALERT_THRESHOLDS["group_call_min"]:
        alerts.append(
            f"Group call activity detected ({activity['Group Calls']} group call(s))"
        )

    if activity.get("Group Messages", 0) >= _ALERT_THRESHOLDS["group_msg_min"]:
        alerts.append(
            f"Group messaging activity detected ({activity['Group Messages']} group message(s))"
        )

    return alerts


def _extract_unique_towers(records: list) -> set:
    towers: set = set()
    for r in records:
        for field in ("Tower", "tower_id", "Cell_ID", "cell_id", "Location"):
            val = _safe_str(r.get(field))
            if val and val.lower() not in ("null", "none", "n/a", ""):
                towers.add(val)
    return towers


# ─────────────────────────────────────────────────────────────────────────────
#  SECTION 8 – FINAL OBSERVATION
# ─────────────────────────────────────────────────────────────────────────────

def _build_final_observation(
    activity      : dict,
    analysis      : dict,
    top_contacts  : dict,
    ip_connections: dict,
    device_usage  : dict,
    time_usage    : dict,
    alerts        : list,
) -> str:
    parts: list = []

    total_msgs  = activity.get("Messages Sent", 0) + activity.get("Messages Received", 0)
    total_calls = activity.get("Voice Calls", 0)   + activity.get("Video Calls", 0)
    if total_msgs or total_calls:
        parts.append(
            f"Active communication pattern observed with "
            f"{total_msgs:,} message(s) and {total_calls:,} call(s) recorded."
        )

    # ── NEW: group activity summary ────────────────────────────────────────
    gp_calls = activity.get("Group Calls", 0)
    gp_msgs  = activity.get("Group Messages", 0)
    if gp_calls or gp_msgs:
        gp_parts = []
        if gp_calls: gp_parts.append(f"{gp_calls:,} group call(s)")
        if gp_msgs:  gp_parts.append(f"{gp_msgs:,} group message(s)")
        parts.append(
            f"Group activity includes {' and '.join(gp_parts)}, "
            f"indicating coordinated or multi-party communication."
        )

    n_top = len(top_contacts)
    if n_top:
        parts.append(f"Interactions are concentrated among {n_top} frequent contact(s).")

    if len(device_usage) >= 2:
        parts.append(
            f"Multi-device usage detected across {', '.join(device_usage.keys())} platforms."
        )
    elif device_usage:
        parts.append(f"Single device type in use: {next(iter(device_usage))}.")

    intl = [c for c in ip_connections if c not in _DOMESTIC_COUNTRIES]
    if intl:
        parts.append(
            f"International IP connections observed from {len(intl)} country/region(s)."
        )

    unknown = analysis.get("Unknown Numbers", 0)
    if unknown:
        parts.append(f"{unknown} unsaved / unknown number(s) present in CDR activity.")

    if time_usage:
        peak_slot = max(time_usage, key=lambda s: float(time_usage[s].rstrip("%")))
        parts.append(f"Peak activity observed during {peak_slot} hours.")

    if alerts:
        parts.append(f"{len(alerts)} flag(s) require immediate attention.")

    return " ".join(parts) if parts else (
        "No significant activity patterns detected in the available records."
    )


# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def generate_advanced_summary(
    all_summaries : list,
    contacts_info : list = None,
    top_n         : int  = 10,
) -> dict:
    if not all_summaries:
        return {
            "Activity Summary"      : {},
            "CONTACT BOOK ANALYSIS" : {},
            "Top Contacts"          : {},
            "IP Connections"        : {},
            "Device Usage"          : {},
            "Time Usage"            : {},
            "Alerts & Flags"        : [],
            "Final Observation"     : "",
            "Top IP Connections"    : [],
        }

    activity       = _build_activity_summary(all_summaries)
    groupactivity =group_activity(all_summaries)
    top_contacts   = _build_top_contacts(all_summaries, 5)
    ip_connections = _build_ip_connections(all_summaries, top_n)
    device_usage   = _build_device_usage(all_summaries)
    time_usage     = _build_time_usage(all_summaries)
    analysis       = _build_analysis_summary(all_summaries, contacts_info or [], top_n)

    alerts = _build_alerts(
        all_summaries, activity, analysis,
        top_contacts, ip_connections, device_usage,
    )

    observation = _build_final_observation(
        activity, analysis, top_contacts,
        ip_connections, device_usage, time_usage, alerts,
    )

    return {
        "Activity Summary"      : activity,
        "Group Activity"        :groupactivity,
        "Top Contacts"          : top_contacts,
        "IP Connections"        : ip_connections,
        "Device Usage"          : device_usage,
        "Time Usage"            : time_usage,
        # "CONTACT BOOK ANALYSIS" : analysis,
        "Top IP Connections": _build_top_ips(all_summaries, top_n=5),
        "Alerts & Flags"        : alerts,
        "Final Observation"     : observation,

    }