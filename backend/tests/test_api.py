"""API-level tests for SlothAudit FastAPI endpoints.

Tests the /health and /api/audit endpoints using httpx's async test client
via FastAPI's TestClient wrapper (synchronous, backed by httpx).

Fixtures use the synthetic demo dataset and handcrafted CSV strings; no real
personal or financial data is included.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app

# ---------------------------------------------------------------------------
# Shared test client
# ---------------------------------------------------------------------------

client = TestClient(app)

DEMO_CSV_PATH = Path("data/demo_transactions.csv")


def _demo_csv_bytes() -> bytes:
    """Return raw bytes of the synthetic demo dataset."""
    return DEMO_CSV_PATH.read_bytes()


def _make_csv(rows: list[dict], *, extra_header: str = "") -> bytes:
    """Build a minimal CSV in-memory from a list of dicts."""
    if not rows:
        return b""
    buf = io.StringIO()
    fieldnames = list(rows[0].keys())
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    content = buf.getvalue()
    if extra_header:
        content = extra_header + "\n" + content
    return content.encode()


def _upload(file_bytes: bytes, filename: str = "test.csv", field: str = "file"):
    """POST to /api/audit as multipart form-data."""
    return client.post(
        "/api/audit",
        files={field: (filename, file_bytes, "text/csv")},
    )


# ---------------------------------------------------------------------------
# 1. Health endpoint
# ---------------------------------------------------------------------------


def test_health_returns_200():
    """GET /health must return HTTP 200 with status=healthy."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"


# ---------------------------------------------------------------------------
# 2. Valid demo CSV upload
# ---------------------------------------------------------------------------


def test_audit_demo_csv_success():
    """POST demo CSV returns 200 with expected structure and finding counts."""
    resp = _upload(_demo_csv_bytes(), "demo_transactions.csv")
    assert resp.status_code == 200, resp.text

    data = resp.json()

    # Top-level fields present
    assert "findings" in data
    assert "total_estimated_monthly_recurring" in data
    assert "total_estimated_annual_recurring" in data
    assert "potential_hypothetical_annual_savings" in data
    assert "summary_notes" in data
    assert "parse_diagnostics" in data

    # Expected aggregates from T1.4.1 verification
    assert data["total_transactions_analyzed"] == 83
    assert data["total_merchants_analyzed"] == 16
    assert len(data["findings"]) == 10
    assert data["total_estimated_monthly_recurring"] == 5015.92
    assert data["total_estimated_annual_recurring"] == 60191.00
    assert data["potential_hypothetical_annual_savings"] == 2388.00

    # Parse diagnostics
    pd = data["parse_diagnostics"]
    assert pd["total_rows"] == 83
    assert pd["valid_rows"] == 83
    assert pd["invalid_rows"] == 0


def test_audit_demo_csv_finding_structure():
    """Each finding in the audit response must have required fields."""
    resp = _upload(_demo_csv_bytes(), "demo.csv")
    assert resp.status_code == 200

    data = resp.json()
    for finding in data["findings"]:
        assert "finding_id" in finding
        assert "finding_type" in finding
        assert "merchant_canonical" in finding
        assert "evidence_txn_ids" in finding
        assert len(finding["evidence_txn_ids"]) > 0
        assert "confidence" in finding
        assert "recommended_action" in finding
        assert "explanation" in finding


def test_audit_demo_csv_evidence_ids_valid():
    """All evidence IDs in findings must reference real transaction IDs from the file."""
    from backend.app.csv_parser import parse_transactions

    raw = _demo_csv_bytes()
    txns, _ = parse_transactions(raw, "demo.csv")
    valid_ids = {t.txn_id for t in txns}

    resp = _upload(raw, "demo.csv")
    assert resp.status_code == 200

    data = resp.json()
    for finding in data["findings"]:
        for tid in finding["evidence_txn_ids"]:
            assert tid in valid_ids, (
                f"Finding {finding['finding_id']} references unknown txn_id {tid!r}"
            )


def test_audit_demo_csv_totals_and_summary_notes():
    """Summary notes must be non-empty and cost totals must be positive."""
    resp = _upload(_demo_csv_bytes(), "demo.csv")
    assert resp.status_code == 200
    data = resp.json()

    assert len(data["summary_notes"]) >= 1
    assert data["total_estimated_monthly_recurring"] > 0
    assert data["total_estimated_annual_recurring"] > 0


# ---------------------------------------------------------------------------
# 3. Empty upload
# ---------------------------------------------------------------------------


def test_audit_empty_upload_returns_400():
    """An empty file body must return HTTP 400."""
    resp = _upload(b"", "empty.csv")
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 4. Unsupported file extension
# ---------------------------------------------------------------------------


def test_audit_unsupported_extension_returns_415():
    """Non-CSV file extension must return HTTP 415."""
    resp = _upload(b"some data", "transactions.xlsx")
    assert resp.status_code == 415


def test_audit_txt_extension_returns_415():
    """Plain-text .txt extension must also be rejected."""
    resp = _upload(b"date,description,amount\n2024-01-01,Test,100.00", "data.txt")
    assert resp.status_code == 415


# ---------------------------------------------------------------------------
# 5. Oversized upload
# ---------------------------------------------------------------------------


def test_audit_oversized_file_returns_413():
    """A file exceeding 5 MB must return HTTP 413."""
    big = b"x" * (5 * 1024 * 1024 + 1)  # 5 MB + 1 byte
    resp = _upload(big, "huge.csv")
    assert resp.status_code == 413
    assert "5 mb" in resp.json()["detail"].lower() or "5,242,880" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 6. Malformed CSV / no usable transactions
# ---------------------------------------------------------------------------


def test_audit_missing_required_columns_returns_422():
    """CSV without required date/description/amount columns must return HTTP 422."""
    bad_csv = b"foo,bar,baz\n1,2,3\n"
    resp = _upload(bad_csv, "malformed.csv")
    assert resp.status_code == 422


def test_audit_no_usable_rows_returns_400():
    """CSV with correct headers but all invalid rows must return HTTP 400."""
    bad_rows = _make_csv([
        {"date": "NOT-A-DATE", "description": "Junk", "amount": "NOTANUMBER"},
        {"date": "ALSO-BAD", "description": "More junk", "amount": "???"},
    ])
    resp = _upload(bad_rows, "all_bad.csv")
    # Parser may raise CSVParseError (422) or succeed with 0 valid rows (400)
    assert resp.status_code in (400, 422)


def test_audit_completely_empty_csv_body():
    """Uploading a zero-byte body must return HTTP 400."""
    resp = _upload(b"", "empty.csv")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 7. Partial success — some rows valid, some invalid
# ---------------------------------------------------------------------------


def test_audit_partial_success_returns_200_with_diagnostics():
    """When some rows are invalid but valid rows exist, return 200 with diagnostics."""
    good_rows = [
        {"date": "2024-01-01", "description": "GoodService Monthly", "amount": "299.00"},
        {"date": "2024-02-01", "description": "GoodService Monthly", "amount": "299.00"},
        {"date": "2024-03-01", "description": "GoodService Monthly", "amount": "299.00"},
        {"date": "2024-04-01", "description": "GoodService Monthly", "amount": "299.00"},
    ]
    # Construct CSV with one corrupt row injected after the header
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["date", "description", "amount"])
    writer.writeheader()
    buf.write("GARBAGE_ROW,,,extra_field\n")  # invalid row
    writer.writerows(good_rows)
    partial_csv = buf.getvalue().encode()

    resp = _upload(partial_csv, "partial.csv")
    assert resp.status_code == 200, resp.text

    data = resp.json()
    pd = data["parse_diagnostics"]
    assert pd["valid_rows"] >= 1
    # At least one finding should exist (4 good rows for GoodService)
    assert len(data["findings"]) >= 1


# ---------------------------------------------------------------------------
# 8. Response serialization completeness
# ---------------------------------------------------------------------------


def test_audit_response_contains_all_report_fields():
    """Verify full AuditReport schema fields appear in serialized JSON response."""
    resp = _upload(_demo_csv_bytes(), "demo.csv")
    assert resp.status_code == 200
    data = resp.json()

    expected_keys = {
        "total_transactions_analyzed",
        "total_merchants_analyzed",
        "findings",
        "total_estimated_monthly_recurring",
        "total_estimated_annual_recurring",
        "potential_hypothetical_annual_savings",
        "currency",
        "summary_notes",
        "parse_diagnostics",
    }
    assert expected_keys.issubset(data.keys()), (
        f"Missing keys: {expected_keys - data.keys()}"
    )


def test_audit_inactive_subscription_in_response():
    """OldNews must appear as inactive with zero active cost in API response."""
    resp = _upload(_demo_csv_bytes(), "demo.csv")
    assert resp.status_code == 200

    findings = resp.json()["findings"]
    oldnews = next(
        (f for f in findings if f["merchant_canonical"] == "OldNews"), None
    )
    assert oldnews is not None, "OldNews finding missing from API response"
    assert oldnews["is_active"] is False
    assert oldnews["recommended_action"] == "investigate"
    assert oldnews["estimated_monthly_cost"] == 0.0
    assert oldnews["estimated_annual_cost"] == 0.0
    assert oldnews["historical_monthly_cost"] == 299.00


def test_audit_price_increase_supplemental_in_response():
    """DevToolbox price-increase finding must be supplemental with None costs."""
    resp = _upload(_demo_csv_bytes(), "demo.csv")
    assert resp.status_code == 200

    findings = resp.json()["findings"]
    pi = next(
        (f for f in findings
         if f["merchant_canonical"] == "DevToolbox" and f["finding_type"] == "price_increase"),
        None,
    )
    assert pi is not None, "DevToolbox price_increase finding missing from API response"
    assert pi["is_supplemental"] is True
    assert pi["related_finding_id"] == "FINDING-DEVTOOLBOX-MONTHLY"
    assert pi["estimated_monthly_cost"] is None
    assert pi["estimated_annual_cost"] is None
