"""
wp_utils.py  –  Shared WhatsApp parsing utilities
===================================================
Single source-of-truth for functions that were previously copy-pasted
into every processor class:

    • _process_single_number
    • extract_phone_number
    • extract_mobile_code
    • is_metadata_line

Import in other modules with:
    from .wp_utils import extract_phone_number, extract_mobile_code, is_metadata_line

All logic is byte-for-byte identical to the original implementations.
"""

import re

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Known country codes (longest-match first order used in extract_mobile_code)
COUNTRY_CODES = [
    '1', '44', '91', '92', '93', '86', '81',
    '49', '33', '7', '39', '34', '55', '52', '234',
]

# Metadata patterns shared by all parsers
_METADATA_PATTERNS = [
    r'WhatsApp Business Record Page \d+',
    r'^Page \d+$',
    r'^Record Page$',
    r'^Business Record$',
]
_METADATA_RE = [re.compile(p, re.IGNORECASE) for p in _METADATA_PATTERNS]


# ---------------------------------------------------------------------------
# Phone-number helpers
# ---------------------------------------------------------------------------

def _process_single_number(text: str) -> str:
    """
    Process a single phone number, handling .0 suffix and various formats.

    Cases handled
    ─────────────
    1. Exactly 10 digits starting with 6-9  →  Indian mobile, returned as-is
    2. 12 digits starting with 91 + 6-9     →  strip '91' prefix
    3. 11 digits starting with 91           →  strip leading '9' if remainder is valid
    4. 11 digits starting with 0            →  strip leading '0' if remainder is valid
    5. Everything else                      →  return digits (if ≥7) or original text
    """
    if not text:
        return ""

    # Convert to string and handle .0 suffix (Excel/CSV artifact)
    text = str(text)
    if text.endswith('.0'):
        text = text[:-2]

    digits_only = re.sub(r'\D', '', text)

    # Handle 13-digit numbers (from .0 suffix on 12-digit numbers)
    if len(digits_only) == 13 and digits_only.startswith('91'):
        digits_only = digits_only[:-1]

    # Case 1 – plain 10-digit Indian mobile
    if len(digits_only) == 10 and digits_only[0] in '6789':
        return digits_only

    # Case 2 – 12-digit with '91' prefix
    elif len(digits_only) == 12 and digits_only.startswith('91') and digits_only[2] in '6789':
        return digits_only[2:]

    # Case 3 – 11-digit starting with '91'
    elif len(digits_only) == 11 and digits_only.startswith('91'):
        potential = digits_only[1:]
        if len(potential) == 10 and potential[0] in '6789':
            return potential
        return text

    # Case 4 – 11-digit starting with '0'
    elif len(digits_only) == 11 and digits_only.startswith('0'):
        potential = digits_only[1:]
        if len(potential) == 10 and potential[0] in '6789':
            return potential
        return text

    # Case 5 – fallback
    else:
        return digits_only if len(digits_only) >= 7 else text


def extract_phone_number(text: str) -> str:
    """
    Extract and format phone number(s) from text.
    Handles both single numbers and comma-separated lists.
    """
    if not text:
        return ""

    if ',' in text:
        processed = [
            _process_single_number(n.strip())
            for n in text.split(',')
        ]
        cleaned = [n for n in processed if n]
        return ", ".join(cleaned) if cleaned else text

    return _process_single_number(text)


def extract_mobile_code(phone_number: str) -> str:
    """
    Extract mobile/country code from a phone number (or comma-separated list).

    Rules
    ─────
    • 10-digit Indian mobile starting with 6/7/8/9  →  first 4 digits
    • Otherwise                                      →  matched country code
                                                        (or first 1-3 digits as fallback)
    """
    if not phone_number:
        return ""

    phone_str = str(phone_number).strip()
    if not phone_str:
        return ""

    numbers = [n.strip() for n in phone_str.split(',')]
    results = []

    for number in numbers:
        if not number:
            continue

        clean = re.sub(r"\D", "", number)

        if len(clean) == 10 and clean[0] in "6789":
            results.append(clean[:4])
            continue

        found_code = ""
        for size in [3, 2, 1]:
            if len(clean) >= size and clean[:size] in COUNTRY_CODES:
                found_code = clean[:size]
                break

        if not found_code:
            found_code = clean[:3] if len(clean) >= 3 else clean

        results.append(found_code)

    return ", ".join(results) if results else ""


# ---------------------------------------------------------------------------
# Metadata line helper
# ---------------------------------------------------------------------------

def is_metadata_line(line: str) -> bool:
    """
    Return True if *line* is a WhatsApp Business pagination/header artefact
    rather than real data content.
    """
    return any(pattern.search(line) for pattern in _METADATA_RE)