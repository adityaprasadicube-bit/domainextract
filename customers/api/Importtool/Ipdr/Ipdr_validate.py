import ipaddress
import socket


def extract_all_info(s):
    """
    Enhanced extraction with better IP detection
    """
    data = {
        "IMEI": None,
        "Mobile": None,
        "TowerID": None,
        "IP": None
    }

    if not s or not isinstance(s, str):
        return data

    # Normalize string
    clean = s.replace('-', ' ').replace('_', ' ')
    parts = clean.split()

    # ---------- IP DETECTION (ENHANCED) ----------
    # Try regex patterns first for better accuracy
    import re

    # IPv4 pattern
    ipv4_pattern = r'\b(?:\d{1,3}\.){3}\d{1,3}\b'
    ipv4_matches = re.findall(ipv4_pattern, s)
    for match in ipv4_matches:
        try:
            ip_obj = ipaddress.ip_address(match)
            data["IP"] = str(ip_obj)
            return data
        except ValueError:
            continue

    # Original word-by-word detection
    for i in range(len(parts)):
        # Check single parts
        try:
            ip_obj = ipaddress.ip_address(parts[i])
            data["IP"] = str(ip_obj)
            break
        except ValueError:
            pass

        # Check 4-part segments for IPv4
        if i + 3 < len(parts):
            ipv4 = ".".join(parts[i:i + 4])
            try:
                ip_obj = ipaddress.ip_address(ipv4)
                data["IP"] = str(ip_obj)
                break
            except ValueError:
                pass

        # Check IPv6 (dot-separated converted to colon-separated)
        if parts[i].count('.') == 7 or parts[i].count(':') == 7:
            ipv6_candidate = parts[i].replace('.', ':')
            try:
                ip_obj = ipaddress.ip_address(ipv6_candidate)
                data["IP"] = str(ip_obj)
                break
            except ValueError:
                pass

    return {k: v for k, v in data.items() if v is not None}

import ipaddress
import re

def extract_ip(s: str):
    """
    Enhanced IP extraction:
    - Detects both IPv4 and IPv6 inside mixed strings
    - Cleans unnecessary symbols around the IP
    - Returns normalized string IP (not ipaddress object)
    """
    if not s or not isinstance(s, str):
        return s

    # Normalize separators
    clean = s.replace('-', ' ').replace('_', ' ').replace(',', ' ').strip()
    parts = clean.split()

    # ---------- Primary IPv4 & IPv6 regex ----------
    ipv4_pattern = r'\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b'
    ipv6_pattern = r'\b([A-Fa-f0-9:]{2,})\b'

    # Search IPv4 first (more common)
    ipv4_matches = re.findall(ipv4_pattern, clean)
    if ipv4_matches:
        for match in ipv4_matches:
            try:
                ip_obj = ipaddress.ip_address(match)
                return str(ip_obj)
            except ValueError:
                continue

    # Then search IPv6
    ipv6_matches = re.findall(ipv6_pattern, clean)
    for match in ipv6_matches:
        try:
            ip_obj = ipaddress.ip_address(match)
            return str(ip_obj)
        except ValueError:
            continue

    # ---------- Fallback detection ----------
    for i in range(len(parts)):
        word = parts[i].strip('"\',:;[](){}')
        try:
            ip_obj = ipaddress.ip_address(word)
            return str(ip_obj)
        except ValueError:
            pass

        # Check possible IPv4 sequence (e.g., "10 0 0 1")
        if i + 3 < len(parts):
            candidate = ".".join(parts[i:i+4])
            try:
                ip_obj = ipaddress.ip_address(candidate)
                return str(ip_obj)
            except ValueError:
                pass

    return s

import base64


# =========================
# 🔑 KEY MANAGEMENT
# =========================

# Convert your custom passphrase into a valid Fernet key


