"""In-memory CSV ingestion and parsing layer for SlothAudit.

Extracts, normalizes, and validates financial statement CSV records into
strongly-typed Transaction models and produces a ParseReport summary.

Design Principles:
- Local-first & In-memory: Never writes uploaded files or rows to disk.
- Privacy-Preserving Errors: Row-level error messages report line numbers and
  structural reasons without exposing transaction narration or personal data.
- Conservative Parsing: Strict date and amount parsing without guessing.
- Strict Constraints: 5 MB file size limit and 50,000 row dataset limit.
"""

import csv
import datetime
import io
import re
from typing import Dict, List, Optional, Set, Tuple

from backend.app.schemas import ParseReport, Transaction

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_DATA_ROWS = 50_000

# Canonical Column Alias Registry
COLUMN_ALIASES: Dict[str, Set[str]] = {
    "date": {
        "date",
        "txn_date",
        "transaction_date",
        "posted_date",
        "posting_date",
        "booking_date",
        "value_date",
        "trans_date",
    },
    "description": {
        "description",
        "raw_description",
        "narration",
        "details",
        "particulars",
        "remarks",
        "transaction_details",
        "payee",
        "merchant",
        "merchant_name",
    },
    "amount": {
        "amount",
        "txn_amount",
        "transaction_amount",
        "net_amount",
        "sum",
    },
    "debit": {
        "debit",
        "withdrawal",
        "dr",
        "debit_amount",
        "dr_amount",
        "spent",
    },
    "credit": {
        "credit",
        "deposit",
        "cr",
        "credit_amount",
        "cr_amount",
        "received",
    },
    "currency": {
        "currency",
        "curr",
        "ccy",
    },
    "merchant_raw": {
        "merchant_raw",
        "merchant",
    },
    "merchant_canonical": {
        "merchant_canonical",
        "canonical_merchant",
    },
    "is_refund": {
        "is_refund",
        "refund",
    },
    "txn_id": {
        "txn_id",
        "transaction_id",
        "id",
        "ref_no",
        "reference_number",
    },
}

SUPPORTED_DATE_FORMATS = [
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d-%b-%Y",
    "%d %b %Y",
    "%b %d, %Y",
    "%Y%m%d",
]

CLEAN_AMOUNT_REGEX = re.compile(r"[^\d.\-+()]")


class CSVParseError(ValueError):
    """Raised when a CSV upload fails top-level file constraints or required headers."""
    pass


def _decode_file_bytes(file_bytes: bytes) -> str:
    """Decode raw bytes using utf-8-sig (with BOM support), cp1252, or latin-1."""
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            decoded = file_bytes.decode(encoding)
            # Ensure no excessive null bytes indicating binary content
            if decoded.count("\x00") > 0:
                raise CSVParseError("Unsupported file content: binary file detected")
            return decoded
        except UnicodeDecodeError:
            continue
    raise CSVParseError("Unable to decode CSV file with supported encodings (UTF-8, CP1252, Latin-1)")


def _detect_delimiter(sample_text: str) -> str:
    """Detect delimiter among comma, semicolon, tab, or pipe."""
    lines = [line.strip() for line in sample_text.splitlines() if line.strip()]
    if not lines:
        return ","

    first_line = lines[0]
    sample = "\n".join(lines[:10])

    try:
        sniffer = csv.Sniffer()
        dialect = sniffer.sniff(sample, delimiters=[",", ";", "\t", "|"])
        return dialect.delimiter
    except Exception:
        # Heuristic count on header line
        candidates = [",", ";", "\t", "|"]
        counts = {d: first_line.count(d) for d in candidates}
        best = max(counts, key=counts.get)
        return best if counts[best] > 0 else ","


def _normalize_header(header_name: str) -> str:
    """Normalize column header to lowercase alphanumeric with underscores."""
    cleaned = re.sub(r"[^\w\s]", "", header_name).strip().lower()
    return re.sub(r"[\s\-]+", "_", cleaned)


def _resolve_column_mapping(headers: List[str]) -> Dict[str, int]:
    """Map canonical field names to column indexes based on supported aliases."""
    mapping: Dict[str, int] = {}
    normalized_headers = [_normalize_header(h) for h in headers]

    for col_idx, norm_header in enumerate(normalized_headers):
        for canonical_name, aliases in COLUMN_ALIASES.items():
            if norm_header in aliases and canonical_name not in mapping:
                mapping[canonical_name] = col_idx
                break

    # Validate essential requirements:
    # 1. Date is required
    # 2. Description is required
    # 3. EITHER amount OR at least one of (debit, credit) is required
    has_date = "date" in mapping
    has_desc = "description" in mapping
    has_amount = "amount" in mapping or "debit" in mapping or "credit" in mapping

    if not (has_date and has_desc and has_amount):
        missing = []
        if not has_date:
            missing.append("date")
        if not has_desc:
            missing.append("description")
        if not has_amount:
            missing.append("amount (or debit/credit)")
        raise CSVParseError(
            f"Missing required CSV column(s): {', '.join(missing)}. "
            f"Detected headers: {headers}"
        )

    return mapping


def _parse_date(date_str: str) -> Optional[datetime.date]:
    """Conservatively parse date using strictly defined formats."""
    cleaned = date_str.strip()
    if not cleaned:
        return None

    # Handle ISO datetime timestamps like '2024-05-10T00:00:00'
    if "T" in cleaned:
        cleaned = cleaned.split("T")[0]

    for fmt in SUPPORTED_DATE_FORMATS:
        try:
            return datetime.datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue

    return None


def _parse_amount(
    amount_str: str,
    debit_str: Optional[str] = None,
    credit_str: Optional[str] = None,
) -> Tuple[Optional[float], bool]:
    """Parse numeric amount following SlothAudit sign convention.

    Convention:
      - Debits / charges / expenses: POSITIVE (+amount).
      - Credits / refunds / chargebacks: NEGATIVE (-amount).
    Returns (amount, is_refund).
    """
    # 1. If explicit debit / credit columns are present
    if debit_str or credit_str:
        d_val = _clean_numeric(debit_str) if debit_str else None
        c_val = _clean_numeric(credit_str) if credit_str else None

        if d_val is not None and d_val > 0:
            return d_val, False
        if c_val is not None and c_val > 0:
            return -c_val, True

    # 2. Parse unified amount column
    if not amount_str or not amount_str.strip():
        return None, False

    raw = amount_str.strip()
    is_refund = False

    # Check parentheses negative convention: (100.00) -> -100.00
    if raw.startswith("(") and raw.endswith(")"):
        raw = "-" + raw[1:-1]
        is_refund = True

    # Check CR / DR indicators
    upper = raw.upper()
    if upper.endswith("CR"):
        raw = "-" + upper.rstrip("CR").strip()
        is_refund = True
    elif upper.endswith("DR"):
        raw = upper.rstrip("DR").strip()

    cleaned = CLEAN_AMOUNT_REGEX.sub("", raw)
    try:
        val = float(cleaned)
        if val < 0:
            is_refund = True
        return val, is_refund
    except (ValueError, TypeError):
        return None, False


def _clean_numeric(val_str: str) -> Optional[float]:
    """Clean and parse a float from a string."""
    cleaned = CLEAN_AMOUNT_REGEX.sub("", val_str.strip())
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_transactions(
    file_bytes: bytes,
    filename: str = "statement.csv",
) -> Tuple[List[Transaction], ParseReport]:
    """Parse an uploaded CSV file into a list of validated Transactions and a ParseReport.

    Args:
        file_bytes: In-memory raw bytes of the CSV file.
        filename: Original filename (used only for context/logging).

    Returns:
        tuple[list[Transaction], ParseReport]: Validated transactions and summary report.

    Raises:
        CSVParseError: If file is empty, exceeds 5 MB, has invalid binary content,
                       exceeds 50,000 rows, or lacks required column headers.
    """
    # 1. Enforce file size limit
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise CSVParseError(
            f"File size exceeds 5 MB limit ({len(file_bytes)} bytes > {MAX_FILE_SIZE_BYTES} bytes)"
        )

    # 2. Enforce non-empty bytes
    if not file_bytes or not file_bytes.strip():
        raise CSVParseError("File is empty or contains only whitespace")

    # 3. Decode bytes in memory
    text = _decode_file_bytes(file_bytes)
    if not text.strip():
        raise CSVParseError("File is empty or contains only whitespace")

    # 4. Detect delimiter
    delimiter = _detect_delimiter(text)

    # 5. Read CSV rows in memory
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header_row = next(reader)
    except StopIteration:
        raise CSVParseError("File contains no CSV header row")

    if not any(h.strip() for h in header_row):
        raise CSVParseError("CSV header row is empty")

    # 6. Map headers to canonical schema fields
    col_map = _resolve_column_mapping(header_row)

    # 7. Process rows with row-level error collection
    transactions: List[Transaction] = []
    errors: List[str] = []
    invalid_rows = 0
    total_data_rows = 0

    currencies_seen: Set[str] = set()

    for row_idx, row in enumerate(reader, start=2):  # 1-based index (header is row 1)
        # Skip completely blank lines
        if not row or not any(cell.strip() for cell in row):
            continue

        total_data_rows += 1
        if total_data_rows > MAX_DATA_ROWS:
            raise CSVParseError(
                f"Dataset exceeds maximum limit of {MAX_DATA_ROWS:,} data rows"
            )

        # Helper to safely retrieve cell
        def get_val(col_name: str) -> str:
            idx = col_map.get(col_name)
            if idx is not None and idx < len(row):
                return row[idx].strip()
            return ""

        # Validate date
        raw_date = get_val("date")
        parsed_date = _parse_date(raw_date)
        if not parsed_date:
            invalid_rows += 1
            errors.append(f"Row {row_idx}: Invalid or unrecognized date format")
            continue

        # Validate description
        raw_desc = get_val("description")
        if not raw_desc:
            invalid_rows += 1
            errors.append(f"Row {row_idx}: Missing transaction description")
            continue

        # Validate amount
        raw_amount = get_val("amount")
        raw_debit = get_val("debit")
        raw_credit = get_val("credit")
        parsed_amount, is_ref = _parse_amount(raw_amount, raw_debit, raw_credit)

        if parsed_amount is None or parsed_amount == 0:
            invalid_rows += 1
            errors.append(f"Row {row_idx}: Invalid or zero transaction amount")
            continue

        # Currency
        currency_val = get_val("currency") or "INR"
        currency_val = currency_val.strip().upper()
        if len(currency_val) != 3 or not currency_val.isalpha():
            currency_val = "INR"
        currencies_seen.add(currency_val)

        # Optional metadata overrides (if present in CSV like demo data)
        raw_is_ref = get_val("is_refund").lower()
        if raw_is_ref in ("true", "1", "yes"):
            is_ref = True

        merch_raw = get_val("merchant_raw") or raw_desc
        merch_canon = get_val("merchant_canonical") or merch_raw
        txn_id = get_val("txn_id") or f"TXN-{row_idx:05d}"

        try:
            t = Transaction(
                txn_id=txn_id,
                date=parsed_date,
                amount=parsed_amount,
                currency=currency_val,
                raw_description=raw_desc,
                merchant_raw=merch_raw,
                merchant_canonical=merch_canon,
                is_refund=is_ref,
                source_row=row_idx,
            )
            transactions.append(t)
        except Exception:
            invalid_rows += 1
            errors.append(f"Row {row_idx}: Schema validation failed")

    # 8. Assemble ParseReport
    date_start = min((t.date for t in transactions), default=None)
    date_end = max((t.date for t in transactions), default=None)

    report = ParseReport(
        total_rows=total_data_rows,
        valid_rows=len(transactions),
        invalid_rows=invalid_rows,
        errors=errors,
        date_range_start=date_start,
        date_range_end=date_end,
        currencies_found=sorted(list(currencies_seen)),
    )

    return transactions, report
