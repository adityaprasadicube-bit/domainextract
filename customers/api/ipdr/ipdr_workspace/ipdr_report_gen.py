from datetime import datetime
import time


# ─────────────────────────────────────────────────────────────────────────────
# Timing utilities — imported by ipdr_mapping.py and ip_views.py
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> str:
    """HH:MM:SS.mmm timestamp for log lines."""
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _elapsed(t0: float) -> str:
    """Human-readable elapsed time since time.monotonic() snapshot t0."""
    ms = (time.monotonic() - t0) * 1000
    if ms < 1000:
        return f"{ms:.1f}ms"
    return f"{ms / 1000:.2f}s"


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _format_timespan(start_str, end_str):
    try:
        start = datetime.fromisoformat(start_str.rstrip("Z"))
        end   = datetime.fromisoformat(end_str.rstrip("Z"))
        diff  = end - start
        secs  = diff.seconds
        return (
            f"{diff.days} dys, {secs // 3600} hrs, "
            f"{(secs % 3600) // 60} mns, {secs % 60} secs"
        )
    except Exception:
        return None


def _split_dt(dt_str):
    if not dt_str:
        return None, None
    try:
        dt = datetime.fromisoformat(dt_str.rstrip('Z'))
        return dt.date().isoformat(), dt.time().isoformat(timespec="seconds")
    except Exception:
        return None, None


def _fmt_lat_long_az(tower):
    if not isinstance(tower, dict):
        return None
    lat = tower.get("Lat")
    lon = tower.get("Long")
    az  = tower.get("Azimuth")
    if lat is None and lon is None and az is None:
        return None
    return f"{lat or ''} {lon or ''} {az or ''}".strip()


# ─────────────────────────────────────────────────────────────────────────────
# FAST PATH — zero external lookups
# ─────────────────────────────────────────────────────────────────────────────

def build_raw_mapping(ipdr: dict, crime_info: dict, ipdr_value: str, ipdr_type: str) -> dict:
    """
    Map a raw DB record to output shape using only in-memory data.
    No DB queries, no network calls.
    Internal '_' fields carry enrichment keys and are stripped before client response.
    """
    sdate, stime = _split_dt(ipdr.get("SDateTime"))
    edate, etime = _split_dt(ipdr.get("EDateTime"))

    session_timespan = None
    if ipdr.get("SDateTime") or ipdr.get("EDateTime"):
        session_timespan = _format_timespan(ipdr.get("SDateTime"), ipdr.get("EDateTime"))

    msisdn = ipdr_value if ipdr_type == 'Mobile' else ipdr.get('MSISDN')

    return {
        "IPDR":                        ipdr_value,
        "MSISDN":                      msisdn,
        "FullName":                    None,
        "FatherName":                  None,
        "LocalAddress":                None,
        "Destination IP":              ipdr.get("Destination_ip"),
        "Isp/Org":                     None,
        "Domains":                     None,
        "Usage":                       None,
        "VPN/Proxy/Tor":               None,
        "TSP/Broadband/Satellite":     None,
        "App/Hostname":                None,
        "Location":                    None,
        "Country":                     None,
        "IPLat":                       None,
        "IPLong":                      None,
        "Destination Port":            ipdr.get("Destination_port"),
        "Port Info":                   None,
        "Port Category":               None,
        "Port Type":                   None,
        "Session Start Date":          sdate,
        "Session Start Time":          stime,
        "Session End Date":            edate,
        "Session End Time":            etime,
        "Duration":                    ipdr.get("Duration"),
        "Session Timespan":            session_timespan,
        "TowerID":                     ipdr.get("TowerID"),
        "TowerID Address":             None,
        "Main City(TowerID)":          None,
        "Sub City(TowerID)":           None,
        "Lat-Long-Azimuth(TowerID)":   None,
        "IMEI":                        ipdr.get("IMEI"),
        "IMEI Manufacturer":           None,
        "Device Type":                 None,
        "IMSI":                        ipdr.get("IMSI"),
        "IMSI MccMnc":                 ipdr.get("IMSI_CODE"),
        "IMSI Circle":                 None,
        "IMSI Operator":               None,
        "Upload Data":                 ipdr.get("DataUpload"),
        "Download Data":               ipdr.get("DataDownload"),
        "Source IP":                   ipdr.get("Source_ip"),
        "Source Port":                 ipdr.get("Source_port"),
        "Translated IP":               ipdr.get("Translated_ip"),
        "Translated Port":             ipdr.get("Translated_port"),
        "Crime":                       crime_info.get("Crime"),
        "AreaLocation":                crime_info.get("AreaLocation"),
        "Roaming":                     None,
        "Circle":                      None,
        "Operator":                    None,
        "Contact No":                  ipdr.get("Contact No"),
        "Name of Person/Organization": ipdr.get("Name of Person/Organization"),
        "enriched":                    False,
        # Internal tracking fields — stripped before sending to client
        "_seq_id":        ipdr.get("seq_id"),
        "_msisdn_raw":    msisdn,
        "_dest_ip_raw":   ipdr.get("Destination_ip"),
        "_dest_port_raw": ipdr.get("Destination_port"),
        "_tower_id_raw":  ipdr.get("TowerID"),
        "_imei_tac_raw":  ipdr.get("IMEI_TAC"),
        "_imsi_code_raw": ipdr.get("IMSI_CODE"),
        "_roam_code_raw": ipdr.get("RoamCode"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# BACKGROUND PATH — apply pre-built lookup tables (pure in-memory)
# ─────────────────────────────────────────────────────────────────────────────

def apply_enrichment_to_record(record: dict, lookups: dict) -> dict:
    """Merge enrichment lookup dicts into a raw-mapped record. Zero DB/network calls."""

    msisdn_val  = record.get("_msisdn_raw")
    msisdn_info = lookups.get("msisdn", {}).get(str(msisdn_val)) if msisdn_val else None
    if msisdn_info:
        record["FullName"]     = msisdn_info.get("FullName")
        record["FatherName"]   = msisdn_info.get("FatherName")
        record["LocalAddress"] = msisdn_info.get("LocalAddress")

    ip_info = lookups.get("dest_ip", {}).get(record.get("_dest_ip_raw")) if record.get("_dest_ip_raw") else None
    if ip_info:
        record["Isp/Org"]                 = ip_info.get("Isp/Org")
        record["Domains"]                 = ip_info.get("Domains")
        record["Usage"]                   = ip_info.get("Usage")
        record["VPN/Proxy/Tor"]           = ip_info.get("VPN/Proxy/Tor")
        record["TSP/Broadband/Satellite"] = ip_info.get("TSP/Broadband/Satellite")
        record["App/Hostname"]            = ip_info.get("App/Hostname")
        record["Location"]                = ip_info.get("Location")
        record["Country"]                 = ip_info.get("Country")
        record["IPLat"]                   = ip_info.get("IPLat")
        record["IPLong"]                  = ip_info.get("IPLong")

    port_info = lookups.get("dest_port", {}).get(record.get("_dest_port_raw")) if record.get("_dest_port_raw") else None
    if port_info:
        record["Port Info"]     = port_info.get("Description")
        record["Port Category"] = port_info.get("Category")
        record["Port Type"]     = port_info.get("Type")

    tower_key  = str(record.get("_tower_id_raw")).upper() if record.get("_tower_id_raw") else None
    tower_info = lookups.get("tower", {}).get(tower_key) if tower_key else None
    if tower_info:
        record["TowerID Address"]           = tower_info.get("ADDRESS")
        record["Main City(TowerID)"]        = tower_info.get("MAIN_CITY")
        record["Sub City(TowerID)"]         = tower_info.get("SUB_CITY")
        record["Lat-Long-Azimuth(TowerID)"] = _fmt_lat_long_az(tower_info)

    imei_info = lookups.get("imei", {}).get(record.get("_imei_tac_raw")) if record.get("_imei_tac_raw") else None
    if imei_info:
        record["IMEI Manufacturer"] = imei_info.get("manufacturer")
        record["Device Type"]       = imei_info.get("devicetype")

    imsi_info = lookups.get("imsi", {}).get(record.get("_imsi_code_raw")) if record.get("_imsi_code_raw") else None
    if imsi_info:
        record["IMSI Circle"]   = imsi_info.get("circle")
        record["IMSI Operator"] = imsi_info.get("operator")
        record["Circle"]        = imsi_info.get("circle")
        record["Operator"]      = imsi_info.get("operator")

    roam_info = lookups.get("roam", {}).get(record.get("_roam_code_raw")) if record.get("_roam_code_raw") else None
    if roam_info:
        record["Roaming"] = roam_info.get("circle")

    record["enriched"] = True
    return record


def strip_internal_fields(record: dict) -> dict:
    """Remove _ prefixed internal tracking fields before sending to client."""
    return {k: v for k, v in record.items() if not k.startswith("_")}