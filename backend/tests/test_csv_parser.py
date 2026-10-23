"""Unit tests for SlothAudit CSV ingestion and parsing layer.

Verifies:
- Parsing of the synthetic demo dataset
- UTF-8 with BOM and Windows (CP1252) encodings
- Comma, semicolon, tab, and pipe delimiter auto-detection
- Header alias resolution (debit/credit, narration, transaction_date, etc.)
- Strict date and amount validation
- Rejection of empty files, missing required headers, and out-of-bound limits
- Negative amount conventions and refund detection
- Partial success behavior on dirty CSVs with row-level error reporting
- Privacy-preserving error messages (no leaking of row narrations/data)
"""

from pathlib import Path
import pytest

from backend.app.csv_parser import CSVParseError, parse_transactions


def test_parse_synthetic_demo_csv():
    """Verify parsing the real demo dataset located at data/demo_transactions.csv."""
    demo_path = Path("data/demo_transactions.csv")
    assert demo_path.exists(), "data/demo_transactions.csv must exist before running test"

    file_bytes = demo_path.read_bytes()
    transactions, report = parse_transactions(file_bytes, filename="demo_transactions.csv")

    assert len(transactions) == 83
    assert report.total_rows == 83
    assert report.valid_rows == 83
    assert report.invalid_rows == 0
    assert len(report.errors) == 0
    assert report.currencies_found == ["INR"]
    assert report.date_range_start is not None
    assert report.date_range_end is not None
    assert report.date_range_start <= report.date_range_end


def test_parse_utf8_bom_and_windows_encoding():
    """Verify UTF-8-BOM and Windows-1252 (CP1252) encoded byte streams parse cleanly."""
    csv_text = "date,description,amount\n2024-05-01,Café Sloth Espresso,120.00\n2024-05-02,BookStore,450.00\n"

    # 1. UTF-8 with BOM
    bom_bytes = csv_text.encode("utf-8-sig")
    txns_bom, report_bom = parse_transactions(bom_bytes, "bom.csv")
    assert len(txns_bom) == 2
    assert report_bom.valid_rows == 2

    # 2. Windows-1252 (CP1252)
    cp1252_bytes = csv_text.encode("cp1252")
    txns_cp, report_cp = parse_transactions(cp1252_bytes, "cp1252.csv")
    assert len(txns_cp) == 2
    assert report_cp.valid_rows == 2


def test_delimiters_semicolon_tab_pipe():
    """Verify delimiter auto-detection for semicolon, tab, and pipe delimiters."""
    # Semicolon
    semi_csv = b"date;description;amount\n2024-01-01;StreamFlix Sub;649.00\n2024-02-01;StreamFlix Sub;649.00\n"
    txns_semi, _ = parse_transactions(semi_csv, "semi.csv")
    assert len(txns_semi) == 2
    assert txns_semi[0].amount == 649.00

    # Tab
    tab_csv = b"date\tdescription\tamount\n2024-01-01\tMusicPulse Sub\t179.00\n"
    txns_tab, _ = parse_transactions(tab_csv, "tab.tsv")
    assert len(txns_tab) == 1
    assert txns_tab[0].amount == 179.00

    # Pipe
    pipe_csv = b"date|description|amount\n2024-01-01|GymFit Core|1500.00\n"
    txns_pipe, _ = parse_transactions(pipe_csv, "pipe.csv")
    assert len(txns_pipe) == 1
    assert txns_pipe[0].amount == 1500.00


def test_column_aliases():
    """Verify alias mapping for date, narration, amount, debit, and credit columns."""
    # Alias set 1: Transaction_Date, Narration, Net_Amount
    csv_1 = b"Transaction_Date,Narration,Net_Amount\n2024-03-15,Cloud Provider,499.00\n"
    txns_1, report_1 = parse_transactions(csv_1, "alias1.csv")
    assert len(txns_1) == 1
    assert txns_1[0].raw_description == "Cloud Provider"
    assert txns_1[0].amount == 499.00

    # Alias set 2: Posted Date, Particulars, Debit, Credit
    csv_2 = (
        b"Posted Date,Particulars,Debit,Credit\n"
        b"2024-04-01,Office Rent,12000.00,0.00\n"
        b"2024-04-05,Client Refund,0.00,1500.00\n"
    )
    txns_2, report_2 = parse_transactions(csv_2, "alias2.csv")
    assert len(txns_2) == 2
    assert txns_2[0].amount == 12000.00
    assert txns_2[0].is_refund is False
    assert txns_2[1].amount == -1500.00
    assert txns_2[1].is_refund is True


def test_negative_amounts_and_refund_syntax():
    """Verify amount syntax variations: parentheses, negative sign, and CR/DR flags."""
    csv_data = (
        b"date,description,amount\n"
        b"2024-01-01,Standard Debit,500.00\n"
        b"2024-01-02,Parentheses Credit,(250.00)\n"
        b"2024-01-03,Negative Sign Credit,-125.00\n"
        b"2024-01-04,Trailing CR Credit,300.00 CR\n"
        b"2024-01-05,Trailing DR Debit,400.00 DR\n"
    )
    txns, report = parse_transactions(csv_data, "amounts.csv")
    assert len(txns) == 5

    assert txns[0].amount == 500.00
    assert txns[0].is_refund is False

    assert txns[1].amount == -250.00
    assert txns[1].is_refund is True

    assert txns[2].amount == -125.00
    assert txns[2].is_refund is True

    assert txns[3].amount == -300.00
    assert txns[3].is_refund is True

    assert txns[4].amount == 400.00
    assert txns[4].is_refund is False


def test_missing_required_columns_and_empty_files():
    """Verify rejection of empty files and files missing required column headers."""
    # Completely empty bytes
    with pytest.raises(CSVParseError, match="empty"):
        parse_transactions(b"", "empty.csv")

    # Whitespace only
    with pytest.raises(CSVParseError, match="empty"):
        parse_transactions(b"   \n   \t  \n", "whitespace.csv")

    # Missing date column
    with pytest.raises(CSVParseError, match="Missing required CSV column"):
        parse_transactions(b"description,amount\nGrocery,100.00\n", "no_date.csv")

    # Missing description column
    with pytest.raises(CSVParseError, match="Missing required CSV column"):
        parse_transactions(b"date,amount\n2024-01-01,100.00\n", "no_desc.csv")

    # Missing amount column
    with pytest.raises(CSVParseError, match="Missing required CSV column"):
        parse_transactions(b"date,description\n2024-01-01,Grocery\n", "no_amount.csv")


def test_malformed_rows_and_partial_success():
    """Verify partial success: valid rows are returned while malformed rows are counted in errors."""
    csv_data = (
        b"date,description,amount\n"
        b"2024-01-01,Valid Row 1,100.00\n"
        b"invalid-date,Bad Date Row,200.00\n"
        b"2024-01-03,,300.00\n"                     # Missing description
        b"2024-01-04,Bad Amount Row,not-a-number\n" # Invalid amount
        b"2024-01-05,Zero Amount Row,0.00\n"        # Zero amount
        b"2024-01-06,Valid Row 2,400.00\n"
    )
    txns, report = parse_transactions(csv_data, "mixed.csv")

    assert len(txns) == 2
    assert report.total_rows == 6
    assert report.valid_rows == 2
    assert report.invalid_rows == 4
    assert len(report.errors) == 4

    # Verify line numbers are referenced in error strings
    error_text = " ".join(report.errors)
    assert "Row 3" in error_text  # Bad date
    assert "Row 4" in error_text  # Missing desc
    assert "Row 5" in error_text  # Non-numeric amount
    assert "Row 6" in error_text  # Zero amount


def test_privacy_preserving_error_messages():
    """Verify that error messages do NOT leak sensitive narration or user data."""
    secret_narration = "CONFIDENTIAL PSYCHIATRIST CONSULTATION VIP999"
    csv_data = f"date,description,amount\n99-99-9999,{secret_narration},5000.00\n".encode("utf-8")

    txns, report = parse_transactions(csv_data, "sensitive.csv")
    assert len(txns) == 0
    assert report.invalid_rows == 1

    error_msg = report.errors[0]
    assert "CONFIDENTIAL" not in error_msg
    assert "PSYCHIATRIST" not in error_msg
    assert "VIP999" not in error_msg
    assert "5000.00" not in error_msg
    assert "Row 2: Invalid or unrecognized date format" in error_msg


def test_file_size_and_row_count_limits():
    """Verify rejection of files exceeding size (5 MB) or row count (50,000) limits."""
    # 1. File size > 5 MB
    large_bytes = b"date,description,amount\n" + (b"2024-01-01,Test,10.00\n" * 250_000)
    assert len(large_bytes) > 5 * 1024 * 1024
    with pytest.raises(CSVParseError, match="5 MB limit"):
        parse_transactions(large_bytes, "oversized.csv")

    # 2. Row count > 50,000 within a small byte footprint
    # (50,001 short rows under 5 MB)
    header = "date,description,amount\n"
    row = "2024-01-01,A,1\n"
    many_rows_bytes = (header + (row * 50_001)).encode("ascii")
    assert len(many_rows_bytes) < 5 * 1024 * 1024
    with pytest.raises(CSVParseError, match="50,000"):
        parse_transactions(many_rows_bytes, "too_many_rows.csv")
