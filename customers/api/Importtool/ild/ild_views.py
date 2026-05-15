"""
ild_views.py
────────────────────────────────────────────────────────────────────────────
Django REST API views for ILD (International Long Distance) file uploads.

Supported formats : .xlsx  |  .csv
Mandatory columns : A_Party (calling)  |  B_Party (called)  |  Call_Type

Endpoints
──────────────────────────────────────────────────────────────────────────
  POST /api/ild/upload/           upload_ild_file
  POST /api/ild/preview/          preview_ild_columns
  POST /api/ild/indexes/?action=  manage_ild_indexes_view   (admin)

FIXES
─────
  [FIX-1] _apply_party_swap: has_target_header check was broken — the nested
          loop compared target_headers items against file_columns items with
          wrong normalization, causing "A_Party" → "aparty" to accidentally
          match "aparty" in target_headers, skipping ALL swaps.
          Fix: normalize file_columns, then do a simple set-intersection check.

  [FIX-2] _apply_ild_field_parsing / Call_Type: when ct_col is None and the
          file already has a column named Call_Type, the elif branch only
          uppercased it but the reverse-lookup block below was guarded by
          the same ct_reverse dict — if CallTypes.json failed to load the dict
          was empty and no normalization happened. Added explicit debug
          logging and a hard fallback warning when ct_reverse is empty.

  [FIX-3] _apply_party_swap call-site: swap was called at step 8b, AFTER
          parse_party_column had already processed A_Party/B_Party. By that
          point the country-code prefix information needed to distinguish
          Indian from international numbers was already stripped/lost.
          Fix: move swap to step 6b, BEFORE parse_party_column (steps 7/8).

  [FIX-4] _normalise_number_for_swap: the '00' IDD-prefix branch
          unconditionally reassigned n = candidate before the 91-strip check,
          corrupting numbers like 0091XXXXXXXXXX. Fixed with a length guard.

  [FIX-5] _is_indian_normalised: fragile multi-branch prefix matching
          replaced with single unambiguous two-step strip (0091 → 91) with
          explicit length guards. Safe to call on both raw and pre-stripped
          input.

  [FIX-6] _is_international_normalised: was calling _is_indian_normalised(n)
          on the raw input while testing _RE_E164 against digits-only — a
          double-normalisation mismatch. Fixed to use the same digits value
          for both checks.

  [FIX-7] _apply_party_swap: Polars with_columns aliasing race condition.
          When A_Party and B_Party are both updated in a single with_columns
          call, the expression for B_Party reads the already-updated A_Party
          alias instead of the original value, causing both columns to end up
          with the same value after the swap.
          Fix: snapshot originals into temporary columns (_orig_a, _orig_b)
          before the swap expressions run, then drop them afterward. The same
          race affects a_mobile_code/b_mobile_code and a_country_code/
          b_country_code — fixed with the same pattern.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import os
import re
from collections import Counter
from datetime import datetime
from io import BytesIO
from typing import Any, Optional

import chardet
import openpyxl
import polars as pl
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from ..ild.ild_utils import (
    is_indian_number,
    extract_country_code,
    clean_number_string,
    parse_party_column,
    SORTED_COUNTRY_CODES,
)
from ..ild.ild_to_db import (
    ILD_COLUMN_MAP,
    MANDATORY_FIELDS,
    build_reverse_lookup,
    insert_ild_file,
    manage_ild_indexes,
    rename_columns,
    validate_mandatory,
)
from ..utils.parsers import (
    parse_call_type_series,
    parse_cgi_series,
    parse_datetime_adaptive,
    parse_imei_series,
    parse_imsi_series,
    parse_number_series,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# JSON CONFIG — PATHS & CACHES
# ══════════════════════════════════════════════════════════════════════════════

_ILD_HEADERS_PATH = os.path.join(
    settings.BASE_DIR, "api", "data", "ild", "ILDHeaders.json"
)
_CALL_TYPES_PATH = os.path.join(
    settings.BASE_DIR, "data", "cdr", "CallTypes.json"
)

_ILD_HEADERS_CACHE: dict | None = None
_CALL_TYPES_CACHE: dict | None = None
_CALLTYPE_REVERSE_CACHE: dict[str, str] | None = None


def _load_ild_headers() -> dict:
    global _ILD_HEADERS_CACHE
    if _ILD_HEADERS_CACHE is None:
        try:
            with open(_ILD_HEADERS_PATH, "r", encoding="utf-8") as fh:
                _ILD_HEADERS_CACHE = json.load(fh)
            logger.info("ILDHeaders loaded: %d keys", len(_ILD_HEADERS_CACHE))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            logger.error("ILDHeaders.json error: %s", exc)
            _ILD_HEADERS_CACHE = {}
    return _ILD_HEADERS_CACHE


def _load_call_types() -> dict:
    """Load CallTypes.json  →  { canonical_key: [raw_variant, ...] }."""
    global _CALL_TYPES_CACHE
    if _CALL_TYPES_CACHE is None:
        try:
            with open(_CALL_TYPES_PATH, "r", encoding="utf-8") as fh:
                _CALL_TYPES_CACHE = json.load(fh)
            logger.info("CallTypes loaded: %d keys", len(_CALL_TYPES_CACHE))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            logger.error("CallTypes.json error: %s", exc)
            _CALL_TYPES_CACHE = {}
    return _CALL_TYPES_CACHE


def _calltype_reverse_lookup() -> dict[str, str]:
    """
    Build and cache  raw_variant (upper) → canonical_key.

    CallTypes.json:  { "CALL_IN": ["IC", "IN", "INCOMING", ...], ... }
    Result:          { "IC": "CALL_IN", "IN": "CALL_IN", ... }
    """
    global _CALLTYPE_REVERSE_CACHE
    if _CALLTYPE_REVERSE_CACHE is None:
        reverse: dict[str, str] = {}
        call_types = _load_call_types()
        if not call_types:
            logger.error(
                "[ILD-CALLTYPE] CallTypes.json is empty or failed to load from '%s'. "
                "Call_Type values will NOT be normalised to canonical keys.",
                _CALL_TYPES_PATH,
            )
        for canonical, variants in call_types.items():
            reverse[canonical.strip().upper()] = canonical
            for raw in variants:
                reverse[raw.strip().upper()] = canonical
        _CALLTYPE_REVERSE_CACHE = reverse
        logger.info(
            "[ILD-CALLTYPE] Reverse lookup built: %d entries", len(reverse)
        )
    return _CALLTYPE_REVERSE_CACHE


# ══════════════════════════════════════════════════════════════════════════════
# NUMBER NORMALISATION  [FIX-4, FIX-5, FIX-6]
# ══════════════════════════════════════════════════════════════════════════════

_RE_INDIAN_10 = re.compile(r"^[6-9]\d{9}$")   # valid 10-digit Indian mobile
_RE_E164      = re.compile(r"^\d{7,15}$")      # E.164-like international


def _strip_to_digits(n: str) -> str:
    return re.sub(r"\D", "", n)


def _normalise_number_for_swap(raw: str) -> str:
    """
    Return the minimal digits-only form of a phone number suitable for
    Indian / international classification.

    Pipeline:
      1. Cast to str, strip whitespace.
      2. Remove leading '+'.
      3. Strip all non-digit characters.
      4. Remove leading '00' IDD prefix when remainder is ≥ 7 digits.  [FIX-4]
      5. Strip Indian '91' country-code prefix only when the result is exactly
         10 digits and matches [6-9]\\d{9}.
    """
    if not raw:
        return ""

    n = str(raw).strip().lstrip("+")
    n = _strip_to_digits(n)
    if not n:
        return ""

    # Step 4 — IDD prefix  [FIX-4: length guard, not unconditional reassign]
    if n.startswith("00") and len(n) >= 9:
        candidate = n[2:]
        if len(candidate) >= 7:
            n = candidate

    # Step 5 — Indian CC strip (exactly 12 digits: 91 + 10-digit mobile)
    if n.startswith("91") and len(n) == 12:
        candidate = n[2:]
        if _RE_INDIAN_10.match(candidate):
            return candidate   # "919509592051" → "9509592051"

    return n


def _is_indian_normalised(n: str) -> bool:
    """
    Return True if n (raw or already-stripped) is a valid Indian 10-digit
    mobile.  [FIX-5] — single unambiguous two-step strip with length guards.

    Handles: +919509592051 | 919509592051 | 00919509592051 | 9509592051
    """
    if not n:
        return False
    n = _strip_to_digits(str(n).strip())
    if not n:
        return False
    if n.startswith("0091") and len(n) == 14:
        n = n[4:]
    elif n.startswith("91") and len(n) == 12:
        n = n[2:]
    return len(n) == 10 and bool(_RE_INDIAN_10.match(n))


def _is_international_normalised(n: str) -> bool:
    """
    Return True if n is a plausible international (non-Indian) E.164-like
    number.  [FIX-6] — uses the same digits value for both checks.
    """
    if not n:
        return False
    digits = _strip_to_digits(str(n).strip())
    if not digits:
        return False
    return bool(_RE_E164.match(digits)) and not _is_indian_normalised(digits)


# ══════════════════════════════════════════════════════════════════════════════
# DURATION / DATE / TIME ALIASES
# ══════════════════════════════════════════════════════════════════════════════

_DURATION_ALIASES: tuple[str, ...] = (
    "Duration", "Dur_s", "DUR", "DUR(S)", "DUR(SEC)", "DUR_IN_SECS",
    "DURATION", "DURATION (SEC)", "DURATION (SECONDS)", "DURATION IN SECONDS",
    "DURATION IN SECS", "BILLING DURATION", "BILL DURATION", "BILLING_DURATION",
    "CALL DURATION", "CALL DURATION (IN SECOND)", "CALL DURATION (IN SECONDS)",
    "CALL DURATION (SECONDS)", "CALL DURATION (SECOND)", "CALL_DURATION",
    "CALL_DURATION_SEC", "CALLDUR", "CALLDURATION", "CALLDURATION(SECONDS)",
    "CHARGEDURN", "ACT_DURATION", "ACTDURATION", "PRIMARY_UNITS",
    "SESSION DURATION", "PDP DURATION SECONDS", "PDP DURATION",
    "DURATION IN SEC", "DURATION(SEC)", "DURATION ( IN SECOND)",
    "CALL_DURATION(SEC)", "CALL DURATION(SEC)", "ACTUAL_DURATION",
    " ACTUAL_DURATION", "DURATION OF CALL",
)

_DATE_ALIASES: tuple[str, ...] = (
    "Date", "Call Date", "Call_Date", "DATE", "CALL_DATE", "CDR_DATE",
    "START_DATE", "RECORD_DATE", "EVENT_DATE", "CALL_DATE (DD-MM-YYYY)",
    "CALL_START_DATE", "CALL_START_DATE_TRF", "START DATE", "START_DT",
    "Start Date", "StartDate", "Start Dt", "Datetime", "DATE_TIME",
    "DATE (MM/DD/YYYY)", "DAYOFTHECALL", "DTSTRTCHRG", "00-00-00_Date",
    "Call date",
)

_TIME_ALIASES: tuple[str, ...] = (
    "Time", "Call Time", "Call_Time", "TIME", "CALL_TIME", "CDR_TIME",
    "START_TIME", "RECORD_TIME", "EVENT_TIME",
    "CALL_INITIATION_TIME(HH:MM:SS)", "Call_Initiation_Time(CIT)",
    "CALL_INITIATION_TIME(CIT)", "Call Initiation Time",
    "Call Initiation Time (CIT)", "CALL_START_TIME", "Call Termination Time",
    "START_TIM", "Start Time", "Start Time Sec", "Start Time Hour",
    "Start Time Min", "TIMEOFTHECALL", "CALL TIME", "Seconds", "TIME (HR:MM:SS)",
)


# ══════════════════════════════════════════════════════════════════════════════
# COLUMN-NAME HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _party_column_variants(ild_headers: dict) -> set[str]:
    """All raw column-name variants from ILDHeaders.json that map to A/B_Party."""
    target_keys = {k for k, v in ILD_COLUMN_MAP.items() if v in ("A_Party", "B_Party")}
    target_keys.update({"A_Party", "B_Party"})
    variants: set[str] = {"A_Party", "B_Party"}
    for json_key, raw_list in ild_headers.items():
        if json_key in target_keys:
            for v in raw_list:
                variants.add(v.strip())
    return variants


def _detect_header_row(raw_rows: list[list[Any]], ild_headers: dict) -> int:
    """Return the 0-based index of the row with the most header-name matches."""
    all_variants = {
        v.strip().lower()
        for variants in ild_headers.values()
        for v in variants
    }
    best_row, best_score = 0, -1
    for i, row in enumerate(raw_rows[:20]):
        score = sum(
            1 for cell in row
            if cell and str(cell).strip().lower() in all_variants
        )
        if score > best_score:
            best_score, best_row = score, i
    return best_row


# ══════════════════════════════════════════════════════════════════════════════
# FILE PARSERS
# ══════════════════════════════════════════════════════════════════════════════

def _parse_xlsx(file_bytes: bytes, ild_headers: dict) -> tuple[pl.DataFrame, int]:
    wb = openpyxl.load_workbook(BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active
    all_rows = [[cell.value for cell in row] for row in ws.iter_rows()]
    wb.close()

    if not all_rows:
        return pl.DataFrame(), 0

    header_idx  = _detect_header_row(all_rows, ild_headers)
    header_row  = [str(c).strip() if c is not None else "" for c in all_rows[header_idx]]
    data_rows   = [r for r in all_rows[header_idx + 1:] if any(c is not None for c in r)]
    n           = len(header_row)
    padded      = [r[:n] + [None] * max(0, n - len(r)) for r in data_rows]

    def _norm(v: Any) -> Any:
        if isinstance(v, float) and v == v:
            try:
                iv = int(v)
                if float(iv) == v:
                    return iv
            except (OverflowError, ValueError):
                pass
        return v

    padded = [[_norm(c) for c in r] for r in padded]

    party_variants = _party_column_variants(ild_headers)
    party_idx      = {i for i, col in enumerate(header_row) if col in party_variants}

    if party_idx:
        def _to_str_cell(v: Any) -> Any:
            if v is None:
                return None
            s = str(v).strip()
            if "." in s:
                ip, _, dp = s.partition(".")
                if ip.lstrip("-").isdigit() and (not dp or set(dp) <= {"0"}):
                    return ip
            return s

        padded = [
            [_to_str_cell(cell) if ci in party_idx else cell for ci, cell in enumerate(row)]
            for row in padded
        ]

    try:
        df = pl.DataFrame(
            {col: [row[i] for row in padded] for i, col in enumerate(header_row)},
            infer_schema_length=500,
        )
    except TypeError:
        df = pl.DataFrame(
            {col: [str(row[i]) if row[i] is not None else None for row in padded]
             for i, col in enumerate(header_row)}
        )

    return df, header_idx


def _detect_delimiter(text: str) -> str:
    sample     = text[:4096]
    candidates = ["\t", "|", ";", ","]
    try:
        import csv as _csv
        dialect = _csv.Sniffer().sniff(sample, delimiters="".join(candidates))
        return dialect.delimiter
    except Exception:
        pass
    lines    = [ln for ln in sample.splitlines() if ln.strip()][:5]
    best, best_avg = ",", 0
    for delim in candidates:
        avg = sum(len(l.split(delim)) for l in lines) / len(lines) if lines else 0
        if avg > best_avg:
            best_avg, best = avg, delim
    return best


def _parse_csv(file_bytes: bytes, ild_headers: dict) -> tuple[pl.DataFrame, int]:
    encoding = chardet.detect(file_bytes[:4096]).get("encoding") or "utf-8"
    try:
        text = file_bytes.decode(encoding, errors="replace")
    except Exception:
        text = file_bytes.decode("utf-8", errors="replace")

    delimiter      = _detect_delimiter(text)
    raw_rows       = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    header_idx     = _detect_header_row(raw_rows, ild_headers)
    actual_headers = (
        [c.strip() for c in raw_rows[header_idx]]
        if header_idx < len(raw_rows) else []
    )

    party_variants      = _party_column_variants(ild_headers)
    party_cols_in_file  = [col for col in actual_headers if col in party_variants]

    try:
        df = pl.read_csv(
            BytesIO(file_bytes),
            separator=delimiter,
            has_header=False,
            new_columns=actual_headers or None,
            skip_rows=header_idx + 1,
            infer_schema_length=500,
            encoding=encoding,
            ignore_errors=True,
            truncate_ragged_lines=True,
        )
    except Exception as exc:
        logger.warning("[ILD-CSV] has_header=False failed (%s); fallback", exc)
        schema_overrides = {col: pl.Utf8 for col in actual_headers if col in party_variants}
        df = pl.read_csv(
            BytesIO(file_bytes),
            separator=delimiter,
            skip_rows=header_idx,
            infer_schema_length=500,
            schema_overrides=schema_overrides or None,
            encoding=encoding,
            ignore_errors=True,
            truncate_ragged_lines=True,
        )
        return df, header_idx

    for col in party_cols_in_file:
        if col in df.columns and df[col].dtype != pl.Utf8:
            df = df.with_columns(pl.col(col).cast(pl.Utf8).alias(col))

    return df, header_idx


def _read_file_to_dataframe(
    file_bytes: bytes,
    filename: str,
    ild_headers: dict,
) -> tuple[pl.DataFrame | None, int, str | None]:
    ext = os.path.splitext(filename)[-1].lower()
    try:
        if ext in (".xlsx", ".xls"):
            df, skipped = _parse_xlsx(file_bytes, ild_headers)
        elif ext == ".csv":
            df, skipped = _parse_csv(file_bytes, ild_headers)
        else:
            return None, 0, f"Unsupported file type '{ext}'. Only .xlsx and .csv accepted."
    except Exception as exc:
        logger.exception("Failed to parse %s: %s", filename, exc)
        return None, 0, f"Parse error for '{filename}': {exc}"

    if df is None or df.is_empty():
        return None, 0, f"'{filename}' produced an empty DataFrame."

    # Strip stray whitespace from column names
    cleaned = [c.strip() for c in df.columns]
    if cleaned != df.columns:
        df = df.rename({old: new for old, new in zip(df.columns, cleaned) if old != new})

    return df, skipped, None


# ══════════════════════════════════════════════════════════════════════════════
# PRE-PROCESSING HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _force_party_columns_to_str(
    df: pl.DataFrame, filename: str, ild_headers: dict
) -> pl.DataFrame:
    """Cast A_Party / B_Party (and all raw variants) to clean Utf8."""
    targets = {"A_Party", "B_Party"} | _party_column_variants(ild_headers)
    for col in df.columns:
        if col not in targets:
            continue
        try:
            if df[col].dtype in (pl.Float32, pl.Float64):
                df = df.with_columns(
                    pl.col(col).cast(pl.Int64, strict=False)
                      .cast(pl.Utf8).fill_null("").alias(col)
                )
            else:
                df = df.with_columns(
                    pl.col(col).cast(pl.Utf8).str.strip_chars()
                      .str.replace_all(r"\.0+$", "").alias(col)
                )
        except Exception as exc:
            logger.warning("[ILD-CAST] %s: '%s' cast failed — %s", filename, col, exc)
    return df


def _resolve_duration_column(df: pl.DataFrame, filename: str) -> pl.DataFrame:
    """Rename the first matching duration alias to 'Duration'."""
    if "Duration" in df.columns:
        return df
    col_lower = {c.lower(): c for c in df.columns}
    for alias in _DURATION_ALIASES:
        if alias in df.columns:
            return df.rename({alias: "Duration"})
        actual = col_lower.get(alias.lower())
        if actual:
            return df.rename({actual: "Duration"})
    return df


# ══════════════════════════════════════════════════════════════════════════════
# A/B PARTY SWAP  [FIX-1, FIX-3, FIX-7]
# ══════════════════════════════════════════════════════════════════════════════

# Normalised (lowercase, no spaces/underscores/slashes) column-name tokens
# that signal the file already has a dedicated target-number column.
# "aparty" is intentionally absent — A_Party is a standard CDR column, not
# a signal that the file is already correctly oriented.  [FIX-1]
_TARGET_HEADER_TOKENS: frozenset[str] = frozenset({
    "target",
    "targetno",
    "targetnumber",
    "targetapartynumber",
    "targetaparty",
    "mobileno",
})

_CALLTYPE_FLIP: dict[str, str] = {
    "CALL_OUT": "CALL_IN",
    "CALL_IN":  "CALL_OUT",
    "SMS_OUT":  "SMS_IN",
    "SMS_IN":   "SMS_OUT",
    "MO":  "MT",
    "MT":  "MO",
    "MOC": "MTC",
    "MTC": "MOC",
}


def _normalise_col_token(col: str) -> str:
    """Lowercase + strip spaces, underscores and slashes for header matching."""
    return re.sub(r"[\s_/]", "", col).lower()


def _flip_call_type_value(ct: str) -> str:
    """Flip CALL_OUT↔CALL_IN, SMS_OUT↔SMS_IN, MO↔MT, MOC↔MTC."""
    return _CALLTYPE_FLIP.get((ct or "").strip().upper(), ct)


def _swap_column_pair(
    df: pl.DataFrame,
    col_a: str,
    col_b: str,
    swap_flag_col: str = "_swap_flag",
) -> pl.DataFrame:
    """
    Swap col_a ↔ col_b for every row where swap_flag_col is True.

    Uses temporary snapshot columns to avoid the Polars aliasing race
    condition where a within-the-same-with_columns expression reads the
    already-updated alias instead of the original value.  [FIX-7]
    """
    tmp_a = f"_orig_{col_a}"
    tmp_b = f"_orig_{col_b}"

    df = df.with_columns([
        pl.col(col_a).alias(tmp_a),
        pl.col(col_b).alias(tmp_b),
    ])
    df = df.with_columns([
        pl.when(pl.col(swap_flag_col))
          .then(pl.col(tmp_b)).otherwise(pl.col(tmp_a)).alias(col_a),
        pl.when(pl.col(swap_flag_col))
          .then(pl.col(tmp_a)).otherwise(pl.col(tmp_b)).alias(col_b),
    ])
    return df.drop([tmp_a, tmp_b])


def _apply_party_swap(
    df: pl.DataFrame,
    filename: str,
    file_columns: Optional[list[str]] = None,
) -> pl.DataFrame:
    """
    Ensure the ILD target (international) number always lands in A_Party.

    ╔══════════════════════════════════════════════════════════════════════╗
    ║  MUST be called BEFORE parse_party_column (steps 7/8).  [FIX-3]    ║
    ║  The function needs raw number strings that still carry their        ║
    ║  country-code prefix (e.g. "919509592051", "971554596491").         ║
    ║  After parse_party_column runs, the prefix information is lost.     ║
    ╚══════════════════════════════════════════════════════════════════════╝

    SWAP RULES (evaluated per-row)
    ──────────────────────────────
    RULE 1 — Explicit type mismatch (primary)
        A_Party Indian   AND B_Party international  →  SWAP
        A_Party international AND B_Party Indian    →  no swap (correct)

    RULE 2 — Ambiguous rows (both same type, or unclassifiable)
        Consistency-scoring fallback: the side with one dominant number
        (high frequency, low unique count) is the target. If target is
        on B → swap those rows. Scores too close → no swap.

    RULE 3 — Target-header guard  [FIX-1]
        If the file already has a dedicated target column (e.g. "Target",
        "TargetNo", "MobileNo") → skip ALL swap logic.

    Call_Type is flipped for every swapped row:
        CALL_OUT → CALL_IN  |  CALL_IN → CALL_OUT
        SMS_OUT  → SMS_IN   |  SMS_IN  → SMS_OUT
        MO → MT             |  MT → MO  |  MOC → MTC  |  MTC → MOC

    NOTE: column pairs are swapped via _swap_column_pair() which snapshots
    originals first to avoid the Polars aliasing race condition.  [FIX-7]
    """
    if "A_Party" not in df.columns or "B_Party" not in df.columns:
        logger.debug("[ILD-SWAP] %s: A_Party or B_Party missing — skipping", filename)
        return df

    # ── RULE 3: target-header guard  [FIX-1] ─────────────────────────────────
    cols_to_check        = file_columns if file_columns is not None else list(df.columns)
    normalised_file_cols = {_normalise_col_token(c) for c in cols_to_check}
    matched_tokens       = normalised_file_cols & _TARGET_HEADER_TOKENS
    if matched_tokens:
        logger.info(
            "[ILD-SWAP] %s: target-indicator column(s) %s detected — skipping swap",
            filename, matched_tokens,
        )
        return df

    _MIN_DOMINANCE = 0.30
    _TIE_MARGIN    = 0.10

    try:
        total_rows = df.height
        if total_rows == 0:
            return df

        a_raw = df["A_Party"].to_list()
        b_raw = df["B_Party"].to_list()

        norm_a = [_normalise_number_for_swap(str(n)) if n is not None else "" for n in a_raw]
        norm_b = [_normalise_number_for_swap(str(n)) if n is not None else "" for n in b_raw]

        is_indian_a = [_is_indian_normalised(n)        for n in norm_a]
        is_indian_b = [_is_indian_normalised(n)        for n in norm_b]
        is_intl_a   = [_is_international_normalised(n) for n in norm_a]
        is_intl_b   = [_is_international_normalised(n) for n in norm_b]

        # ── RULE 1 ────────────────────────────────────────────────────────────
        swap_needed: list[bool] = []
        rule1_swap    = 0
        rule1_correct = 0
        ambiguous_idx: list[int] = []

        for i, (ind_a, intl_a, ind_b, intl_b) in enumerate(
            zip(is_indian_a, is_intl_a, is_indian_b, is_intl_b)
        ):
            if ind_a and intl_b:
                swap_needed.append(True)
                rule1_swap += 1
            elif intl_a and ind_b:
                swap_needed.append(False)
                rule1_correct += 1
            else:
                swap_needed.append(False)   # placeholder; resolved by RULE 2
                ambiguous_idx.append(i)

        logger.info(
            "[ILD-SWAP] %s: RULE-1 — swap=%d correct=%d ambiguous=%d",
            filename, rule1_swap, rule1_correct, len(ambiguous_idx),
        )

        # ── RULE 2 ────────────────────────────────────────────────────────────
        if ambiguous_idx:
            amb_a     = [norm_a[i] for i in ambiguous_idx]
            amb_b     = [norm_b[i] for i in ambiguous_idx]
            amb_total = len(ambiguous_idx)

            freq_a = Counter(n for n in amb_a if n)
            freq_b = Counter(n for n in amb_b if n)

            top_a, cnt_a = freq_a.most_common(1)[0] if freq_a else ("", 0)
            top_b, cnt_b = freq_b.most_common(1)[0] if freq_b else ("", 0)

            ratio_a  = cnt_a / amb_total
            ratio_b  = cnt_b / amb_total
            unique_a = len(freq_a)
            unique_b = len(freq_b)

            score_a = ratio_a / math.log2(unique_a + 2)
            score_b = ratio_b / math.log2(unique_b + 2)

            logger.info(
                "[ILD-SWAP] %s: RULE-2 (%d rows) — "
                "A top=%r ratio=%.1f%% unique=%d score=%.4f | "
                "B top=%r ratio=%.1f%% unique=%d score=%.4f",
                filename, amb_total,
                top_a, ratio_a * 100, unique_a, score_a,
                top_b, ratio_b * 100, unique_b, score_b,
            )

            rule2_target: Optional[str] = None

            if ratio_a < _MIN_DOMINANCE and ratio_b < _MIN_DOMINANCE:
                logger.warning(
                    "[ILD-SWAP] %s: RULE-2 — neither side dominant "
                    "(A=%.1f%% B=%.1f%%) — ambiguous rows left as-is",
                    filename, ratio_a * 100, ratio_b * 100,
                )
            elif abs(score_a - score_b) <= _TIE_MARGIN * max(score_a, score_b, 1e-9):
                logger.info(
                    "[ILD-SWAP] %s: RULE-2 — scores too close "
                    "(A=%.4f B=%.4f) — ambiguous rows left as-is",
                    filename, score_a, score_b,
                )
            elif score_b > score_a:
                rule2_target = top_b
                logger.info(
                    "[ILD-SWAP] %s: RULE-2 — target %r on B-side → will swap",
                    filename, top_b,
                )
            else:
                logger.info(
                    "[ILD-SWAP] %s: RULE-2 — target %r already on A-side → no swap",
                    filename, top_a,
                )

            for idx, i in enumerate(ambiguous_idx):
                if rule2_target:
                    swap_needed[i] = (
                        amb_b[idx] == rule2_target and amb_a[idx] != rule2_target
                    )

        # ── Apply ─────────────────────────────────────────────────────────────
        total_swaps = sum(swap_needed)
        logger.info(
            "[ILD-SWAP] %s: rows to swap = %d / %d",
            filename, total_swaps, total_rows,
        )

        if not total_swaps:
            logger.info("[ILD-SWAP] %s: no swaps needed", filename)
            return df

        df = df.with_columns(pl.Series("_swap_flag", swap_needed))

        # A_Party ↔ B_Party  [FIX-7: snapshot to avoid aliasing race]
        df = _swap_column_pair(df, "A_Party", "B_Party")

        # Mobile/country code pairs  [FIX-7: same race applies]
        for col_a, col_b in [
            ("a_mobile_code",  "b_mobile_code"),
            ("a_country_code", "b_country_code"),
        ]:
            if col_a in df.columns and col_b in df.columns:
                df = _swap_column_pair(df, col_a, col_b)

        # Flip Call_Type for swapped rows
        if "Call_Type" in df.columns:
            raw_ct  = df["Call_Type"].cast(pl.Utf8).fill_null("").to_list()
            flipped = [
                _flip_call_type_value(ct) if swap_needed[i] else ct
                for i, ct in enumerate(raw_ct)
            ]
            df = df.with_columns(pl.Series("Call_Type", flipped, dtype=pl.Utf8))
            logger.info(
                "[ILD-SWAP] %s: Call_Type flipped for %d rows", filename, total_swaps
            )

        df = df.drop("_swap_flag")
        logger.info(
            "[ILD-SWAP] %s: ✓ complete — %d / %d rows corrected",
            filename, total_swaps, total_rows,
        )

    except Exception as exc:
        logger.error(
            "[ILD-SWAP] %s: unexpected error — %s", filename, exc, exc_info=True
        )
        if "_swap_flag" in df.columns:
            df = df.drop("_swap_flag")

    return df


# ══════════════════════════════════════════════════════════════════════════════
# MAIN FIELD-PARSING PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

def _apply_ild_field_parsing(
    df: pl.DataFrame,
    filename: str,
    ild_headers: dict | None = None,
) -> pl.DataFrame | None:
    """
    Full CDR-style field-parsing pipeline for ILD data.

    Steps
    ─────
      0   Global quote-strip
      1   Datetime column detection & parsing
      2   Validate SDateTime (drop null rows)
      3   IMEI
      4   IMSI
      5   CGI
      6   Lat/Long split
      6b  A/B Party swap  ← BEFORE parse_party_column  [FIX-3]
      7   A_Party  (parse_party_column)
      8   B_Party  (parse_party_column)
      9   Call_Type normalisation
      10  Duration
      11  EDateTime derivation
    """

    # ── 0. Global quote-strip ─────────────────────────────────────────────────
    str_cols = [c for c, dt in zip(df.columns, df.dtypes) if dt == pl.Utf8]
    if str_cols:
        df = df.with_columns([
            pl.col(c).str.strip_chars("'\" ").alias(c) for c in str_cols
        ])

    # ── 1. Datetime ───────────────────────────────────────────────────────────
    try:
        if ild_headers:
            sdate_variants = {v.strip() for v in ild_headers.get("SDate", [])}
            stime_variants = {v.strip() for v in ild_headers.get("STime", [])}
            for col in list(df.columns):
                cs = col.strip()
                if cs in sdate_variants and "SDate" not in df.columns:
                    df = df.rename({col: "SDate"})
                elif cs in stime_variants and "STime" not in df.columns:
                    df = df.rename({col: "STime"})

        has_sd = "SDate" in df.columns
        has_st = "STime" in df.columns

        if not has_sd:
            for alias in _DATE_ALIASES:
                if alias in df.columns:
                    df = df.rename({alias: "SDate"})
                    has_sd = True
                    break

        if not has_st:
            for alias in _TIME_ALIASES:
                if alias in df.columns:
                    df = df.rename({alias: "STime"})
                    has_st = True
                    break

        # Strip sub-second precision from STime strings
        if has_st and "STime" in df.columns and df["STime"].dtype == pl.Utf8:
            df = df.with_columns(
                pl.col("STime").str.replace(r"\.\d+$", "").alias("STime")
            )

        if has_sd and has_st:
            pdts, pds = parse_datetime_adaptive(df["SDate"], df["STime"])
        elif has_sd:
            pdts, pds = parse_datetime_adaptive(df["SDate"])
        else:
            col_lower = {c.lower(): c for c in df.columns}
            fd = next((col_lower[lc] for lc in col_lower if "date" in lc and "time" not in lc), None)
            ft = next((col_lower[lc] for lc in col_lower if "time" in lc and "date" not in lc), None)
            if fd and ft:
                if df[ft].dtype == pl.Utf8:
                    df = df.with_columns(
                        pl.col(ft).str.replace(r"\.\d+$", "").alias(ft)
                    )
                df = df.rename({fd: "SDate", ft: "STime"})
                pdts, pds = parse_datetime_adaptive(df["SDate"], df["STime"])
            else:
                dt_cols = [c for c in df.columns if "date" in c.lower() or "datetime" in c.lower()]
                if dt_cols:
                    pdts, pds = parse_datetime_adaptive(df[dt_cols[0]])
                else:
                    logger.error("[ILD-PARSE] %s: no date/time column found", filename)
                    return None

        df = df.with_columns([
            pl.Series("SDateTime", pdts, dtype=pl.Datetime),
            pl.Series("SDate",     pds,  dtype=pl.Date),
        ])

    except Exception as exc:
        logger.error(
            "[ILD-PARSE] %s: datetime parsing failed — %s", filename, exc, exc_info=True
        )
        return None

    # ── 2. Validate SDateTime ─────────────────────────────────────────────────
    if "SDateTime" not in df.columns:
        logger.error("[ILD-PARSE] %s: SDateTime missing after step 1", filename)
        return None

    before = df.height
    df     = df.filter(pl.col("SDateTime").is_not_null())
    logger.info("[ILD-PARSE] %s: valid rows %d / %d", filename, df.height, before)
    if df.is_empty():
        logger.error("[ILD-PARSE] %s: no rows with valid SDateTime", filename)
        return None

    # ── 3. IMEI ───────────────────────────────────────────────────────────────
    if "IMEI" in df.columns:
        try:
            tac, imei = parse_imei_series(df["IMEI"])
            df = df.with_columns([
                pl.Series("IMEI_TAC", tac,  dtype=pl.Utf8),
                pl.Series("IMEI",     imei, dtype=pl.Utf8),
            ])
        except Exception as exc:
            logger.warning("[ILD-PARSE] %s: IMEI error — %s", filename, exc)

    # ── 4. IMSI ───────────────────────────────────────────────────────────────
    if "IMSI" in df.columns:
        try:
            imsi, code = parse_imsi_series(df["IMSI"])
            df = df.with_columns([
                pl.Series("IMSI",      imsi, dtype=pl.Utf8),
                pl.Series("IMSI_CODE", code, dtype=pl.Utf8),
            ])
        except Exception as exc:
            logger.warning("[ILD-PARSE] %s: IMSI error — %s", filename, exc)

    # ── 5. CGI ────────────────────────────────────────────────────────────────
    _INVALID_CGI = frozenset({
        "---", "N/A", "N/a", "n/a", "Unknown", "unknown", "null", "None", "", "-"
    })
    for canonical, raw_name in [("First_CGI", "First CGI"), ("Last_CGI", "Last CGI")]:
        actual = next((c for c in (canonical, raw_name) if c in df.columns), None)
        if not actual:
            continue
        try:
            df = df.with_columns(
                pl.when(
                    pl.col(actual).cast(pl.Utf8).str.strip_chars().is_in(_INVALID_CGI)
                )
                .then(pl.lit(""))
                .otherwise(pl.col(actual).cast(pl.Utf8).str.strip_chars())
                .alias(actual)
            )
            _, _, _, _, cgi_clean = parse_cgi_series(df[actual])
            df = df.with_columns(pl.Series(actual, cgi_clean, dtype=pl.Utf8))
            if actual != canonical:
                df = df.rename({actual: canonical})
        except Exception as exc:
            logger.warning(
                "[ILD-PARSE] %s: CGI '%s' error — %s", filename, actual, exc
            )

    # ── 6. Lat/Long ───────────────────────────────────────────────────────────
    for src, lat_col, long_col in [
        ("First CGI Lat/Long", "First_Lat", "First_Long"),
        ("Last CGI Lat/Long",  "Last_Lat",  "Last_Long"),
        ("FirstLatLong",       "First_Lat", "First_Long"),
        ("LastLatLong",        "Last_Lat",  "Last_Long"),
    ]:
        if src not in df.columns:
            continue
        if lat_col in df.columns:
            df = df.drop(src)
            continue
        try:
            lats, lngs = [], []
            for val in df[src].cast(pl.Utf8).fill_null("").to_list():
                if val and "/" in val:
                    parts = val.split("/", 1)
                    lats.append(parts[0].strip())
                    lngs.append(parts[1].strip() if len(parts) > 1 else "")
                else:
                    lats.append("")
                    lngs.append("")
            df = df.with_columns([
                pl.Series(lat_col,  lats, dtype=pl.Utf8),
                pl.Series(long_col, lngs, dtype=pl.Utf8),
            ])
        except Exception as exc:
            logger.warning(
                "[ILD-PARSE] %s: LatLong '%s' error — %s", filename, src, exc
            )
        finally:
            if src in df.columns:
                df = df.drop(src)

    # ── 6b. A/B Party swap — BEFORE parse_party_column  [FIX-3] ─────────────
    # Must run while A_Party/B_Party still contain full country-code-prefixed
    # strings (e.g. "919509592051", "971554596491"). After parse_party_column
    # (steps 7/8) those prefixes are stripped and the Indian vs. international
    # distinction becomes unreliable.
    df = _apply_party_swap(df, filename, file_columns=list(df.columns))

    # ── 7. A_Party ────────────────────────────────────────────────────────────
    if "A_Party" in df.columns:
        try:
            a_nums, a_mc, a_cc = parse_party_column(
                df["A_Party"].to_list(), "A_Party", filename,
                strip_leading_zeros=False,
                parse_number_series_fn=parse_number_series,
            )
            df = df.with_columns([
                pl.Series("A_Party",        a_nums, dtype=pl.Utf8),
                pl.Series("a_mobile_code",  a_mc,   dtype=pl.Utf8),
                pl.Series("a_country_code", a_cc,   dtype=pl.Utf8),
            ])
        except Exception as exc:
            logger.warning("[ILD-PARSE] %s: A_Party error — %s", filename, exc)

    # ── 8. B_Party ────────────────────────────────────────────────────────────
    if "B_Party" in df.columns:
        try:
            b_nums, b_mc, b_cc = parse_party_column(
                df["B_Party"].to_list(), "B_Party", filename,
                strip_leading_zeros=True,
                parse_number_series_fn=parse_number_series,
            )
            df = df.with_columns([
                pl.Series("B_Party",        b_nums, dtype=pl.Utf8),
                pl.Series("b_mobile_code",  b_mc,   dtype=pl.Utf8),
                pl.Series("b_country_code", b_cc,   dtype=pl.Utf8),
            ])
        except Exception as exc:
            logger.warning("[ILD-PARSE] %s: B_Party error — %s", filename, exc)

    # ── 9. Call_Type ──────────────────────────────────────────────────────────
    try:
        filecalltype_variants:    set[str] = set()
        fileservicetype_variants: set[str] = set()

        if ild_headers:
            filecalltype_variants    = {v.strip() for v in ild_headers.get("FileCallType", [])}
            fileservicetype_variants = {v.strip() for v in ild_headers.get("FileServiceType", [])}

        _FILECALLTYPE_HARDCODED: frozenset[str] = frozenset({
            "CALL_DIRECTION", "CALL_DIR", "DIRECTION", "FileCallType",
            "CALL_TYPE", "Call Type", "Call Type (IN/OUT/SMS IN/SMS OUT)",
            "Call Type ( IN/ Out)", "REC_TYPE", "RECORD_TYPE", "RECORDTYPE",
            "L9_RECORD_TYPE", "Type", "Calltype",
            "CALL_DIR (Voice- OUT/INC SMS- MOC/MTC)",
        })

        _CT_PRIORITY: list[str] = [
            "CALL_DIRECTION", "CALL_DIR",
            "CALL_DIR (Voice- OUT/INC SMS- MOC/MTC)",
            "DIRECTION", "FileCallType",
            "CALL_TYPE", "Call Type", "Call Type (IN/OUT/SMS IN/SMS OUT)",
            "Call Type ( IN/ Out)", "REC_TYPE", "RECORD_TYPE", "RECORDTYPE",
            "L9_RECORD_TYPE", "Type", "Calltype",
        ]

        filecalltype_cols_found: list[str] = [
            col for col in df.columns
            if col in filecalltype_variants
               or col in _FILECALLTYPE_HARDCODED
               or col == "FileCallType"
        ]
        fileservicetype_cols_found: list[str] = [
            col for col in df.columns
            if col in fileservicetype_variants or col == "FileServiceType"
        ]

        logger.debug(
            "[ILD-PARSE] %s: FileCallType cols=%s  FileServiceType cols=%s  bare Call_Type=%s",
            filename, filecalltype_cols_found, fileservicetype_cols_found,
            "Call_Type" in df.columns,
        )

        ct_col: str | None = None
        if filecalltype_cols_found:
            ct_col = next(
                (p for p in _CT_PRIORITY if p in filecalltype_cols_found),
                filecalltype_cols_found[0],
            )
            duplicates = [c for c in filecalltype_cols_found if c != ct_col]
            if duplicates:
                logger.warning(
                    "[ILD-PARSE] %s: multiple FileCallType columns %s; "
                    "selected %r; duplicate(s) %s ignored.",
                    filename, filecalltype_cols_found, ct_col, duplicates,
                )

        svc_col: str | None = (
            fileservicetype_cols_found[0] if fileservicetype_cols_found else None
        )

        # PATH A — FileCallType variant present
        if ct_col:
            logger.debug(
                "[ILD-PARSE] %s: PATH A — col=%r  svc_col=%r", filename, ct_col, svc_col
            )
            parsed_ct: list[str] = parse_call_type_series(
                df[ct_col],
                df[svc_col] if svc_col else None,
            )
            empty_count = sum(1 for v in parsed_ct if not v)
            if empty_count:
                raw_sample = list({str(v) for v in df[ct_col].to_list() if v})[:10]
                logger.warning(
                    "[ILD-PARSE] %s: parse_call_type_series empty for %d / %d rows. "
                    "Sample from %r: %s",
                    filename, empty_count, df.height, ct_col, raw_sample,
                )
            df = df.with_columns(pl.Series("Call_Type", parsed_ct, dtype=pl.Utf8))

        # PATH B — bare Call_Type column only  [FIX-2]
        elif "Call_Type" in df.columns:
            logger.debug(
                "[ILD-PARSE] %s: PATH B — bare Call_Type; applying reverse-lookup",
                filename,
            )
            df = df.with_columns(
                pl.col("Call_Type").cast(pl.Utf8).str.strip_chars()
                  .str.to_uppercase().alias("Call_Type")
            )
            ct_reverse = _calltype_reverse_lookup()
            if not ct_reverse:
                logger.error(
                    "[ILD-PARSE] %s: PATH B — ct_reverse EMPTY ('%s' failed to load). "
                    "Call_Type values will remain as-is.",
                    filename, _CALL_TYPES_PATH,
                )
            else:
                raw_ct    = df["Call_Type"].cast(pl.Utf8).fill_null("").to_list()
                unique_raw = set(raw_ct) - {""}
                normalised = [
                    ct_reverse.get(v.strip().upper(), v.strip().upper()) for v in raw_ct
                ]
                unmapped = {v for v in unique_raw if v.strip().upper() not in ct_reverse}
                if unmapped:
                    logger.warning(
                        "[ILD-PARSE] %s: PATH B — unmapped Call_Type values: %s",
                        filename, sorted(unmapped),
                    )
                df = df.with_columns(pl.Series("Call_Type", normalised, dtype=pl.Utf8))

        else:
            logger.warning(
                "[ILD-PARSE] %s: no Call_Type source found. Columns: %s",
                filename, list(df.columns),
            )

    except Exception as exc:
        logger.warning(
            "[ILD-PARSE] %s: Call_Type step failed — %s", filename, exc, exc_info=True
        )

    # ── 10. Duration ──────────────────────────────────────────────────────────
    if "Duration" in df.columns:
        try:
            df = df.with_columns(
                pl.col("Duration")
                  .cast(pl.Utf8).str.strip_chars()
                  .str.replace_all(r"[^\d.]", "").fill_null("0")
                  .str.replace_all(r"^\.*$", "0")
                  .cast(pl.Float64, strict=False).fill_null(0.0)
                  .round(0).cast(pl.Int64)
                  .alias("Duration")
            )
        except Exception as exc:
            logger.warning("[ILD-PARSE] %s: Duration error — %s", filename, exc)
    else:
        df = df.with_columns(pl.lit(0).cast(pl.Int64).alias("Duration"))

    # ── 11. EDateTime ─────────────────────────────────────────────────────────
    try:
        df = df.with_columns(
            pl.when(pl.col("SDateTime").is_not_null())
              .then(pl.col("SDateTime") + pl.duration(seconds=pl.col("Duration")))
              .otherwise(pl.lit(None).cast(pl.Datetime))
              .alias("EDateTime")
        )
    except Exception as exc:
        logger.warning("[ILD-PARSE] %s: EDateTime error — %s", filename, exc)

    logger.info(
        "[ILD-PARSE] %s: complete — %d rows, cols: %s",
        filename, df.height, df.columns,
    )
    return df


# ══════════════════════════════════════════════════════════════════════════════
# VIEWS
# ══════════════════════════════════════════════════════════════════════════════

@csrf_exempt
@api_view(["POST"])
@parser_classes([MultiPartParser, FormParser])
def upload_ild_file(request):
    """
    Upload one or more ILD files (.xlsx / .csv).

    Required form fields
    ────────────────────
      crimename      str   investigation / case name
      arealocation   str   geographic context
      file           file  one or more ILD files (repeated field)
    """
    start_time = datetime.now()

    crime_name     = (request.POST.get("crimename")    or "").strip()
    area_location  = (request.POST.get("arealocation") or "").strip()
    uploaded_files = request.FILES.getlist("file")

    if not crime_name or not area_location:
        return Response(
            {"status": "error", "message": "Both 'crimename' and 'arealocation' are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not uploaded_files:
        return Response(
            {"status": "error", "message": "At least one file must be uploaded."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user_id     = str(
        getattr(request.user, "id", None) or getattr(request.user, "pk", "anonymous")
    )
    ild_headers = _load_ild_headers()
    if not ild_headers:
        return Response(
            {"status": "error", "message": "ILDHeaders.json could not be loaded."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    results: dict[str, Any] = {}
    any_success = False

    for uploaded_file in uploaded_files:
        filename   = uploaded_file.name
        buf        = BytesIO()
        for chunk in uploaded_file.chunks():
            buf.write(chunk)
        file_bytes = buf.getvalue()

        # 1. Parse raw file → DataFrame
        df, rows_skipped, err = _read_file_to_dataframe(file_bytes, filename, ild_headers)
        if err:
            results[filename] = {"status": "failed", "error": err}
            continue

        # 2. Pre-process
        df = _force_party_columns_to_str(df, filename, ild_headers)
        df = _resolve_duration_column(df, filename)

        # 3. Full field-parsing pipeline (includes swap at step 6b)
        df = _apply_ild_field_parsing(df, filename, ild_headers=ild_headers)

        if df is None or df.is_empty():
            results[filename] = {
                "status": "failed",
                "error":  "No valid records after parsing.",
            }
            continue

        # 4. Persist
        try:
            ins = insert_ild_file(
                df=df,
                crime_name=crime_name,
                area_location=area_location,
                filename=filename,
                user_id=user_id,
                ild_headers=ild_headers,
                top_rows_skipped=rows_skipped,
            )
            if ins.get("errors"):
                results[filename] = {"status": "failed", "errors": ins["errors"]}
            else:
                results[filename] = {
                    "status":       "success",
                    "inserted":     ins["inserted"],
                    "duplicates":   ins["duplicates"],
                    "updated":      ins["updated"],
                    "rows_skipped": rows_skipped,
                    "nexus_id":     ins["nexus"].get("_id", ""),
                }
                any_success = True
        except Exception as exc:
            logger.exception("[ILD-VIEW] Unhandled error for %s: %s", filename, exc)
            results[filename] = {"status": "failed", "error": str(exc)}

    if not any_success:
        return Response(
            {
                "status":  "error",
                "message": "All files failed to process.",
                "results": results,
            },
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    elapsed = (datetime.now() - start_time).total_seconds()
    return Response(
        {
            "status":          "success",
            "message":         f"Processed {len(uploaded_files)} ILD file(s) in {elapsed:.2f}s",
            "files_processed": len(uploaded_files),
            "crime_name":      crime_name,
            "area_location":   area_location,
            "results":         results,
        },
        status=status.HTTP_200_OK,
    )


@csrf_exempt
@api_view(["POST"])
@parser_classes([MultiPartParser, FormParser])
def preview_ild_columns(request):
    """
    Preview column mapping for a single ILD file without inserting any data.

    Returns matched/unmatched columns, mandatory-field status, and a 3-row
    parsed sample so the caller can validate the mapping before committing.
    """
    uploaded_files = request.FILES.getlist("file")
    if not uploaded_files:
        return Response(
            {"status": "error", "message": "No file provided."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    uploaded_file = uploaded_files[0]
    filename      = uploaded_file.name
    buf           = BytesIO()
    for chunk in uploaded_file.chunks():
        buf.write(chunk)
    file_bytes = buf.getvalue()

    ild_headers = _load_ild_headers()
    df, rows_skipped, err = _read_file_to_dataframe(file_bytes, filename, ild_headers)
    if err:
        return Response({"status": "error", "message": err}, status=status.HTTP_400_BAD_REQUEST)

    df = _force_party_columns_to_str(df, filename, ild_headers)
    df = _resolve_duration_column(df, filename)

    lookup  = build_reverse_lookup(ild_headers)
    mapping = {
        col: lookup[col]
        for col in df.columns
        if col in lookup or col.lower() in lookup
    }
    unmatched = [col for col in df.columns if col not in mapping]

    _, matched = rename_columns(df, ild_headers)
    missing    = validate_mandatory(matched)

    parsed_df = _apply_ild_field_parsing(df.clone(), filename, ild_headers=ild_headers)
    sample    = (
        parsed_df.head(3).to_dicts()
        if parsed_df is not None
        else df.head(3).to_dicts()
    )

    return Response(
        {
            "status":            "ok",
            "filename":          filename,
            "rows_skipped":      rows_skipped,
            "total_raw_columns": len(df.columns),
            "matched_columns":   mapping,
            "unmatched_columns": unmatched,
            "mandatory_present": sorted(MANDATORY_FIELDS - set(missing)),
            "mandatory_missing": missing,
            "sample_rows":       sample,
        },
        status=status.HTTP_200_OK,
    )


@csrf_exempt
@api_view(["POST"])
def manage_ild_indexes_view(request):
    """
    Manage MongoDB indexes for the ILD collection.

    Query params
    ────────────
      ?action=drop      drop all ILD indexes
      ?action=rebuild   (default) drop then rebuild
    """
    action = request.query_params.get("action", "rebuild")
    if action not in ("drop", "rebuild"):
        return Response(
            {"status": "error", "message": "action must be 'drop' or 'rebuild'"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        manage_ild_indexes(action=action)
        return Response(
            {"status": "success", "message": f"ILD indexes {action} completed."}
        )
    except Exception as exc:
        logger.exception("Index management failed: %s", exc)
        return Response(
            {"status": "error", "message": str(exc)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )