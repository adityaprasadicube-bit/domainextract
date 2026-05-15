"""
ild_utils.py
────────────────────────────────────────────────────────────────────────────
Shared phone-number classification and cleaning utilities for the ILD pipeline.

Public API
──────────
  is_indian_number(number)          → bool
  extract_country_code(number)      → str | None
  extract_number(raw)               → {"country_code": str, "number": str} | None
  clean_number_string(raw)          → str
  trim_lrn(number)                  → str
  derive_country_codes(numbers)     → list[str]
  parse_party_column(...)           → (nums, mobile_codes, country_codes)
  debug_single_number(number)       → None  (manual testing helper)
"""

from __future__ import annotations

import re
import sys
import logging
from typing import Any

import polars as pl

logger = logging.getLogger(__name__)

# ── dedicated stdout debug logger (always visible in server logs) ─────────────
_dbg = logging.getLogger("ild.number.debug")
if not _dbg.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(message)s"))
    _h.setLevel(logging.DEBUG)
    _dbg.addHandler(_h)
    _dbg.setLevel(logging.DEBUG)
    _dbg.propagate = False


# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

_COUNTRY_CODES_RAW: list[str] = [
    "1", "7", "20", "27", "30", "31", "32", "33", "34", "36", "39", "40",
    "41", "43", "44", "45", "46", "47", "48", "49", "51", "52", "53", "54",
    "55", "56", "57", "58", "60", "61", "62", "63", "64", "65", "66",
    "81", "82", "84", "86", "90", "91", "92", "93", "94", "95", "98",
    "212", "213", "216", "218", "220", "221", "222", "223", "224", "225",
    "226", "227", "228", "229", "230", "231", "232", "233", "234", "235",
    "236", "237", "238", "239", "240", "241", "242", "243", "244", "245",
    "246", "247", "248", "249", "250", "251", "252", "253", "254", "255",
    "256", "257", "258", "260", "261", "262", "263", "264", "265", "266",
    "267", "268", "269", "284", "290", "291", "297", "298", "299", "345",
    "350", "351", "352", "353", "354", "355", "356", "357", "358", "359",
    "370", "371", "372", "373", "374", "375", "376", "377", "378", "380",
    "381", "385", "386", "387", "389", "420", "421", "441", "473", "500",
    "501", "502", "503", "504", "505", "506", "507", "508", "509", "590",
    "591", "592", "593", "594", "595", "596", "597", "598", "599", "649",
    "664", "670", "672", "673", "674", "675", "676", "677", "678", "679",
    "680", "681", "682", "683", "684", "685", "686", "687", "688", "689",
    "690", "691", "692", "758", "767", "787", "808", "809", "850", "852",
    "853", "855", "856", "868", "869", "876", "880", "886", "960", "961",
    "962", "963", "964", "965", "966", "967", "968", "971", "972", "973",
    "974", "975", "976", "977", "993", "994", "995", "996", "998",
    "5399",
]

# Longest-first so the most-specific prefix always wins.
SORTED_COUNTRY_CODES: list[str] = sorted(
    _COUNTRY_CODES_RAW, key=len, reverse=True
)

# ---------------------------------------------------------------------------
# Per-CC-length minimum subscriber digits.
#
# The threshold must vary by CC length to handle two conflicting requirements:
#
#   • 1-digit CCs ("1"=NANP, "7"=Russia) need a floor of 10 so that a plain
#     Indian 10-digit number starting with 7/8/9 (e.g. "7646271517") is NOT
#     mis-classified as Russian:  10 - 1 = 9  <  10  →  no match  ✓
#
#   • 3-digit CCs (e.g. "971"=UAE) only have 9 subscriber digits, so a flat
#     floor of 10 would reject them:  12 - 3 = 9  <  10  →  missed  ✗
#       With the per-length floor of 7:  9 >= 7  →  match  ✓
# ---------------------------------------------------------------------------
_MIN_SUB_BY_CC_LEN: dict[int, int] = {1: 10, 2: 8, 3: 7, 4: 6}
_MIN_SUB_DEFAULT: int = 7

# Kept for backward-compatibility with any external code that imports it.
# Internal logic now calls _min_sub_digits() instead.
MIN_SUBSCRIBER_DIGITS: int = 10

# Number of leading digits used as a provisional Indian operator prefix when
# parse_number_series returns no code (matches TRAI VNL prefix length).
_INDIAN_PREFIX_LEN: int = 4

_RE_INDIAN_91 = re.compile(r"^91[6-9]\d{9}$")   # 12-digit: 91 + mobile
_RE_INDIAN_10 = re.compile(r"^[6-9]\d{9}$")      # plain 10-digit mobile


# ══════════════════════════════════════════════════════════════════════════════
# CORE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _min_sub_digits(code: str) -> int:
    """Return the minimum subscriber-digit count for a given CC string."""
    return _MIN_SUB_BY_CC_LEN.get(len(code), _MIN_SUB_DEFAULT)


def is_indian_number(number: str) -> bool:
    """
    Return True for valid Indian mobile numbers:
      •  91XXXXXXXXXX  (12-digit with country-code prefix, digit 3 in [6-9])
      •    XXXXXXXXXX  (plain 10-digit, first digit in [6-9])
      • +91XXXXXXXXXX  (leading '+' stripped before matching)
    """
    n = number.lstrip("+")
    return bool(_RE_INDIAN_91.match(n) or _RE_INDIAN_10.match(n))


def normalize_indian_cc(number: str) -> str:
    """
    Strip a '91' dial-out prefix from a 12-digit Indian mobile number.

    "917311411309" → "7311411309"

    Has no effect on numbers that do not match _RE_INDIAN_91, so it is safe
    to call unconditionally.
    """
    return number[2:] if _RE_INDIAN_91.match(number) else number


# ══════════════════════════════════════════════════════════════════════════════
# COUNTRY-CODE EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def extract_country_code(number: str) -> str | None:
    """
    Return the longest matching country-code prefix, or None.

    Rules
    ─────
    • Indian numbers always return None (handled by separate mobile-code path).
    • After stripping the CC prefix, the remaining subscriber digits must meet
      the per-CC-length minimum (see _MIN_SUB_BY_CC_LEN).
    • Longest prefix wins (SORTED_COUNTRY_CODES is ordered longest-first).
    """
    if not number or is_indian_number(number):
        return None
    for code in SORTED_COUNTRY_CODES:
        if number.startswith(code) and len(number) - len(code) >= _min_sub_digits(code):
            return code
    return None


# ══════════════════════════════════════════════════════════════════════════════
# LRN TRIMMING
# ══════════════════════════════════════════════════════════════════════════════

def trim_lrn(number: str) -> str:
    """
    Strip any LRN prefix prepended by a carrier switch to an international
    number, e.g. "21099659881474" → "99659881474"  (LRN=210, CC=996).

    Walks every start-offset in the digit string and, at each offset, tries
    every CC longest-first.  The **first** offset that yields a valid
    (CC + enough subscriber digits) match is returned — this minimises the
    amount stripped and correctly handles LRN prefixes of any length.

    Falls back to the original string when no match is found.
    """
    if not number or is_indian_number(number):
        return number
    for i in range(len(number)):
        remaining = number[i:]
        for code in SORTED_COUNTRY_CODES:
            if remaining.startswith(code):
                subscriber = remaining[len(code):]
                if len(subscriber) >= _min_sub_digits(code):
                    # NANP guard: area code must not start with 0 or 1
                    if code == "1" and (not subscriber or subscriber[0] in ("0", "1")):
                        continue
                    return remaining
    return number


# ══════════════════════════════════════════════════════════════════════════════
# INTERNATIONAL NUMBER EXTRACTION  (LRN stripping + CC detection)
# ══════════════════════════════════════════════════════════════════════════════

def extract_number(raw: Any) -> dict[str, str] | None:
    """
    Parse a raw value that may carry an LRN/prefix before the international
    dialled number, e.g.:

        "21099659881474"  →  LRN=210 | CC=996 | subscriber=59881474

    Returns a dict with keys ``"country_code"`` and ``"number"`` (the full
    E.164-style string starting with the CC), or ``None`` if no valid
    international number can be extracted.

    Strategy
    ────────
    Walk every start-offset *i* in the digit string.  At each offset try
    every CC (longest-first).  The first (shortest offset, longest CC) match
    whose subscriber portion satisfies the per-CC-length minimum is returned.
    This means a leading LRN of any length is silently discarded.
    """
    raw_digits = re.sub(r"\D", "", str(raw))
    if not raw_digits:
        return None

    for i in range(len(raw_digits)):
        remaining = raw_digits[i:]
        for cc in SORTED_COUNTRY_CODES:          # longest CC first
            if remaining.startswith(cc):
                subscriber = remaining[len(cc):]
                sub_len = len(subscriber)
                if sub_len < _min_sub_digits(cc):
                    continue
                # NANP (CC "1"): area code must not start with 0 or 1
                if cc == "1" and (not subscriber or subscriber[0] in ("0", "1")):
                    continue
                return {"country_code": cc, "number": remaining}

    return None


# ══════════════════════════════════════════════════════════════════════════════
# RAW VALUE CLEANING
# ══════════════════════════════════════════════════════════════════════════════

def clean_number_string(raw: Any, strip_leading_zeros: bool = False) -> str:
    """Convert a raw cell value to a clean phone-number string."""
    if raw is None:
        return ""
    s = str(raw).strip().strip("'\" ")
    if not s:
        return ""

    # Scientific notation  e.g. "1.848e10"
    if "e" in s.lower():
        try:
            fv = float(s)
            iv = int(fv)
            if float(iv) == fv:
                s = str(iv)
        except (ValueError, OverflowError):
            pass

    # Trailing .0 / .00 artefact  e.g. "7646271517.0"
    if "." in s:
        int_part, _, dec_part = s.partition(".")
        if int_part.lstrip("-").isdigit() and (not dec_part or set(dec_part) <= {"0"}):
            s = int_part

    if strip_leading_zeros:
        s = s.lstrip("0")

    # LRN prefix trim for suspiciously long digit strings
    if len(s) > 13 and s.isdigit():
        s = trim_lrn(s)

    return s


# ══════════════════════════════════════════════════════════════════════════════
# BATCH COUNTRY-CODE DERIVATION
# ══════════════════════════════════════════════════════════════════════════════

def derive_country_codes(numbers: list[str]) -> list[str]:
    """Return a parallel list of country-code strings ('' for Indian/unknown)."""
    result: list[str] = []
    for n in numbers:
        cleaned = str(n).strip().lstrip("0") if n else ""
        cc = extract_country_code(cleaned)
        result.append(cc if cc else "")
    return result


# ══════════════════════════════════════════════════════════════════════════════
# PARTY COLUMN PARSER  (with full step-by-step debug output)
# ══════════════════════════════════════════════════════════════════════════════

def parse_party_column(
    raw_list: list[Any],
    col_label: str,
    filename: str,
    strip_leading_zeros: bool = False,
    parse_number_series_fn=None,
) -> tuple[list[str], list[str], list[str]]:
    """
    Parse raw phone-number values → (cleaned_numbers, mobile_codes, country_codes).

    Steps
    ─────
    1   clean_number_string (float artefacts, sci-notation, LRN trim)
    1.5 normalize 91-prefixed Indian numbers  ("917311411309" → "7311411309")
    2   classify each number as international or Indian/local
    3   allocate output arrays
    4   fill international rows  (country_code set, mobile_code blank)
    5   call parse_number_series for Indian rows; fallback prefix if no code

    parse_number_series_fn must be injected by the caller (avoids circular import).
    """
    if parse_number_series_fn is None:
        raise ValueError("parse_number_series_fn must be provided")

    SEP = "=" * 60
    _dbg.debug("\n%s", SEP)
    _dbg.debug("[ILD-NUM] parse_party_column START")
    _dbg.debug("  file=%s  col=%s  strip_zeros=%s  rows=%d",
               filename, col_label, strip_leading_zeros, len(raw_list))
    _dbg.debug("  first 5 raw: %s", raw_list[:5])

    # ── Step 1: clean ─────────────────────────────────────────────────────────
    cleaned: list[str] = [
        clean_number_string(v, strip_leading_zeros=strip_leading_zeros)
        for v in raw_list
    ]

    _dbg.debug("\n[STEP-1] clean_number_string output (first 5):")
    for i in range(min(5, len(raw_list))):
        _dbg.debug("  [%d] %r  →  %r", i, raw_list[i], cleaned[i])

    # ── Step 1.5: normalize 91-prefixed Indian numbers ────────────────────────
    # Strips the "91" dial-out country-code prefix so parse_number_series always
    # receives a clean 10-digit subscriber number.  Without this step, the first
    # 4–5 characters of the output (e.g. "9173") would be extracted as the
    # mobile/operator code instead of the correct subscriber prefix.
    _dbg.debug("\n[STEP-1.5] 91-prefix normalisation:")
    normalized: list[str] = []
    for num in cleaned:
        if _RE_INDIAN_91.match(num):
            stripped = num[2:]
            _dbg.debug("  [NORM-91] %r → %r", num, stripped)
            normalized.append(stripped)
        else:
            normalized.append(num)
    cleaned = normalized

    # ── Step 2: classify ──────────────────────────────────────────────────────
    intl_idx:   list[int] = []
    indian_idx: list[int] = []

    _dbg.debug("\n[STEP-2] Classification (first 10):")
    for i, num in enumerate(cleaned):
        if not num:
            indian_idx.append(i)
            if i < 10:
                _dbg.debug("  [%02d] %r  → EMPTY → indian bucket", i, num)
            continue

        indian = is_indian_number(num)
        cc     = extract_country_code(num)

        if i < 10:
            _dbg.debug(
                "  [%02d] %r  len=%d  is_indian=%s  cc=%r  → %s",
                i, num, len(num), indian, cc,
                "indian" if (indian or not cc) else "INTL",
            )

        if indian or not cc:
            indian_idx.append(i)
        else:
            intl_idx.append(i)

    _dbg.debug("  TOTAL intl=%d  indian/local=%d", len(intl_idx), len(indian_idx))

    # ── Step 3: output arrays ─────────────────────────────────────────────────
    out_nums:   list[str] = [""] * len(cleaned)
    out_mcodes: list[str] = [""] * len(cleaned)
    out_ccodes: list[str] = [""] * len(cleaned)

    # ── Step 4: international rows ────────────────────────────────────────────
    for i in intl_idx:
        num = cleaned[i]
        cc  = extract_country_code(num) or ""
        out_nums[i]   = num
        out_mcodes[i] = ""        # international numbers carry country_code, not mobile_code
        out_ccodes[i] = cc

    # ── Step 5: Indian / unknown rows via parse_number_series ─────────────────
    if indian_idx:
        indian_raw    = [cleaned[i] for i in indian_idx]
        indian_series = pl.Series(indian_raw)

        _dbg.debug("\n[STEP-5] Calling parse_number_series on %d values:", len(indian_raw))
        _dbg.debug("  input (first 10): %s", indian_raw[:10])

        p_nums_raw, p_codes_raw = parse_number_series_fn(indian_series)
        p_nums  = [str(x) if x is not None else "" for x in p_nums_raw]
        p_codes = [str(x) if x is not None else "" for x in p_codes_raw]

        _dbg.debug("[STEP-5] parse_number_series returned (first 10):")
        for j in range(min(10, len(p_nums))):
            _dbg.debug("  [%02d] input=%r  num=%r  code=%r",
                       j, indian_raw[j], p_nums[j], p_codes[j])

        _dbg.debug("\n[STEP-5] Post-process (first 10):")
        for j, i in enumerate(indian_idx):
            num   = p_nums[j]
            mcode = p_codes[j]
            ccode = ""

            if num:
                if is_indian_number(num):
                    # Confirmed Indian mobile.  If parse_number_series returned
                    # no operator code, derive a provisional one from the first
                    # _INDIAN_PREFIX_LEN digits (TRAI VNL prefix length = 4).
                    if not mcode and len(num) >= _INDIAN_PREFIX_LEN:
                        mcode = num[:_INDIAN_PREFIX_LEN]
                        _dbg.debug(
                            "  [%02d] fallback mcode %r from %r", j, mcode, num
                        )
                    ccode = ""
                else:
                    # parse_number_series resolved it to an international number.
                    cc_recheck = extract_country_code(num)
                    if cc_recheck:
                        mcode = ""
                        ccode = cc_recheck

            out_nums[i]   = num
            out_mcodes[i] = mcode
            out_ccodes[i] = ccode

            if j < 10:
                _dbg.debug(
                    "  [%02d] parsed_num=%r  is_indian=%s  "
                    "final_mcode=%r  final_ccode=%r",
                    j, num, is_indian_number(num) if num else "N/A",
                    mcode, ccode,
                )

    # ── Final summary ─────────────────────────────────────────────────────────
    non_empty_mc = sum(1 for m in out_mcodes if m)
    non_empty_cc = sum(1 for c in out_ccodes if c)
    _dbg.debug(
        "\n[FINAL] %s %s — mobile_codes filled: %d/%d  country_codes filled: %d/%d",
        col_label, filename, non_empty_mc, len(out_mcodes), non_empty_cc, len(out_ccodes),
    )
    _dbg.debug("[FINAL] First 10 output rows:")
    for i in range(min(10, len(cleaned))):
        _dbg.debug("  [%02d] raw=%r  num=%r  mcode=%r  ccode=%r",
                   i, raw_list[i], out_nums[i], out_mcodes[i], out_ccodes[i])
    _dbg.debug("%s\n", SEP)

    return out_nums, out_mcodes, out_ccodes


# ══════════════════════════════════════════════════════════════════════════════
# STANDALONE DEBUG HELPER
# ══════════════════════════════════════════════════════════════════════════════

def debug_single_number(number: str) -> None:
    """
    Print a full classification trace for one number.  Use from Django shell:

        from api.ild.ild_utils import debug_single_number
        debug_single_number("7646271517")
        debug_single_number("917885916244")
        debug_single_number("17405714205")
        debug_single_number("971554596491")
    """
    print(f"\n{'─' * 60}")
    print(f"debug_single_number({number!r})")
    print(f"  len                  : {len(number)}")
    n = number.lstrip("+")
    print(f"  _RE_INDIAN_91 match  : {bool(_RE_INDIAN_91.match(n))}")
    print(f"  _RE_INDIAN_10 match  : {bool(_RE_INDIAN_10.match(n))}")
    print(f"  is_indian_number()   : {is_indian_number(number)}")
    print(f"  normalize_indian_cc  : {normalize_indian_cc(number)!r}")
    print(f"  extract_country_code : {extract_country_code(number)}")

    cleaned   = clean_number_string(number, strip_leading_zeros=False)
    cleaned_s = clean_number_string(number, strip_leading_zeros=True)
    print(f"  clean (no strip)     : {cleaned!r}")
    print(f"  clean (strip zeros)  : {cleaned_s!r}")

    # Show every CC prefix the number starts with and whether it passes the
    # per-CC-length threshold (not the legacy flat MIN_SUBSCRIBER_DIGITS).
    matches = [c for c in SORTED_COUNTRY_CODES if number.startswith(c)]
    print(f"  CC prefixes present  : {matches[:10]}")
    for code in matches[:5]:
        remainder = len(number) - len(code)
        threshold = _min_sub_digits(code)
        ok        = remainder >= threshold
        print(
            f"    code={code!r}  remainder={remainder}"
            f"  >= {threshold} (CC-len={len(code)})? {ok}"
            f"  → {'MATCH' if ok else 'SKIP (too short)'}"
        )
    print(f"{'─' * 60}\n")