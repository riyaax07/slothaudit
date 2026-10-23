"""Tests for SlothAudit transaction schemas and synthetic data generator.

Verifies:
- Transaction and ParseReport schema validation and sign conventions
- Deterministic reproducibility of the synthetic dataset
- Scenario coverage (monthly, annual, price increase, duplicates, one-offs, refunds, variations, ambiguous cases)
- Uniqueness and integrity of transaction IDs and records
"""

import datetime
from pathlib import Path
import pytest
from pydantic import ValidationError

from backend.app.schemas import Transaction, ParseReport
from backend.app.synthetic_data import generate_demo_transactions, write_demo_csv


# ==============================================================================
# 1. Schema & Validation Tests
# ==============================================================================

def test_transaction_valid_construction():
    """Verify standard valid transaction creation and default values."""
    txn = Transaction(
        txn_id="TXN-0001",
        date=datetime.date(2024, 5, 10),
        amount=649.00,
        raw_description="STREAMFLIX DIGITAL SUBS",
        merchant_raw="StreamFlix Digital",
        merchant_canonical="StreamFlix",
        source_row=2,
    )
    assert txn.txn_id == "TXN-0001"
    assert txn.currency == "INR"
    assert txn.amount == 649.00
    assert txn.is_refund is False
    assert txn.source_row == 2


def test_transaction_sign_convention_and_refund_alignment():
    """Verify sign convention: positive for charges, negative for refunds/credits."""
    # Negative amount automatically sets is_refund to True
    refund_txn = Transaction(
        txn_id="TXN-REFUND-1",
        date=datetime.date(2024, 4, 12),
        amount=-899.00,
        raw_description="BOOKHAVEN RETURN REFUND",
        merchant_raw="BookHaven Online",
        merchant_canonical="BookHaven",
        source_row=15,
    )
    assert refund_txn.is_refund is True
    assert refund_txn.amount == -899.00

    # If is_refund is True with positive amount, it is synced to negative
    refund_txn_auto_negated = Transaction(
        txn_id="TXN-REFUND-2",
        date=datetime.date(2024, 4, 12),
        amount=899.00,
        is_refund=True,
        raw_description="BOOKHAVEN RETURN REFUND",
        merchant_raw="BookHaven Online",
        merchant_canonical="BookHaven",
        source_row=16,
    )
    assert refund_txn_auto_negated.is_refund is True
    assert refund_txn_auto_negated.amount == -899.00


def test_transaction_validation_failures():
    """Verify validation errors on empty fields, invalid currencies, and invalid rows."""
    base_data = {
        "txn_id": "TXN-001",
        "date": datetime.date(2024, 1, 1),
        "amount": 100.0,
        "currency": "INR",
        "raw_description": "Valid desc",
        "merchant_raw": "Valid merchant",
        "merchant_canonical": "Valid canonical",
        "source_row": 1,
    }

    # Empty txn_id
    with pytest.raises(ValidationError):
        Transaction(**{**base_data, "txn_id": "   "})

    # Empty merchant
    with pytest.raises(ValidationError):
        Transaction(**{**base_data, "merchant_canonical": ""})

    # Invalid currency format
    with pytest.raises(ValidationError):
        Transaction(**{**base_data, "currency": "IN"})
    with pytest.raises(ValidationError):
        Transaction(**{**base_data, "currency": "123"})

    # Invalid source_row (< 1)
    with pytest.raises(ValidationError):
        Transaction(**{**base_data, "source_row": 0})


def test_parse_report_schema():
    """Verify ParseReport structure and defaults."""
    report = ParseReport(
        total_rows=10,
        valid_rows=9,
        invalid_rows=1,
        errors=["Row 3: Invalid currency 'XX'"],
        date_range_start=datetime.date(2024, 1, 1),
        date_range_end=datetime.date(2024, 10, 1),
        currencies_found=["INR"],
    )
    assert report.total_rows == 10
    assert report.valid_rows == 9
    assert report.invalid_rows == 1
    assert len(report.errors) == 1
    assert report.currencies_found == ["INR"]


# ==============================================================================
# 2. Synthetic Generator Reproducibility & Integrity
# ==============================================================================

def test_synthetic_generator_deterministic_reproducibility():
    """Verify identical data is produced when using the same seed."""
    txns_a = generate_demo_transactions(seed=42)
    txns_b = generate_demo_transactions(seed=42)

    assert len(txns_a) == len(txns_b)
    for a, b in zip(txns_a, txns_b):
        assert a.model_dump() == b.model_dump()


def test_synthetic_csv_write_reproducibility(tmp_path: Path):
    """Verify writing the demo CSV repeatedly generates byte-for-byte identical output."""
    file1 = tmp_path / "run1.csv"
    file2 = tmp_path / "run2.csv"

    write_demo_csv(file1, seed=42)
    write_demo_csv(file2, seed=42)

    assert file1.read_text(encoding="utf-8") == file2.read_text(encoding="utf-8")


def test_synthetic_dataset_uniqueness_and_sequence():
    """Verify all transaction IDs are unique, valid, and rows are numbered sequentially."""
    txns = generate_demo_transactions(seed=42)
    assert len(txns) >= 50

    txn_ids = [t.txn_id for t in txns]
    assert len(txn_ids) == len(set(txn_ids)), "Duplicate txn_id detected!"

    # Source rows must be strictly increasing starting at 2 (line 1 is header)
    for idx, t in enumerate(txns, start=2):
        assert t.source_row == idx

    # Dates must be sorted chronologically
    dates = [t.date for t in txns]
    assert dates == sorted(dates), "Transactions are not in chronological order!"


# ==============================================================================
# 3. Scenario Coverage Tests
# ==============================================================================

def test_scenario_monthly_subscriptions():
    """Verify presence of multiple regular monthly subscriptions."""
    txns = generate_demo_transactions(seed=42)

    streamflix = [t for t in txns if t.merchant_canonical == "StreamFlix" and t.amount == 649.00]
    musicpulse = [t for t in txns if t.merchant_canonical == "MusicPulse" and t.amount == 179.00]
    cloudvault = [t for t in txns if t.merchant_canonical == "CloudVault" and t.amount == 130.00]
    gymfit = [t for t in txns if t.merchant_canonical == "GymFit" and t.amount == 1500.00]

    # Each should span 10 consecutive months (Jan - Oct 2024)
    assert len(streamflix) == 10
    assert len(musicpulse) == 10
    assert len(cloudvault) == 10
    assert len(gymfit) == 10


def test_scenario_annual_subscription():
    """Verify presence of an annual subscription with ~365 days cadence."""
    txns = generate_demo_transactions(seed=42)
    codehost = [t for t in txns if t.merchant_canonical == "CodeHost"]

    assert len(codehost) == 2
    assert codehost[0].date == datetime.date(2023, 10, 15)
    assert codehost[1].date == datetime.date(2024, 10, 15)
    assert (codehost[1].date - codehost[0].date).days in (365, 366)
    assert codehost[0].amount == 3599.00
    assert codehost[1].amount == 3599.00


def test_scenario_price_increase():
    """Verify presence of a subscription with a price hike."""
    txns = generate_demo_transactions(seed=42)
    devtoolbox = [t for t in txns if t.merchant_canonical == "DevToolbox"]

    assert len(devtoolbox) == 10
    early_charges = [t for t in devtoolbox if t.amount == 499.00]
    late_charges = [t for t in devtoolbox if t.amount == 699.00]

    assert len(early_charges) == 5
    assert len(late_charges) == 5
    # Price increased starting in month 6 (June 2024)
    assert max(t.date for t in early_charges) < min(t.date for t in late_charges)


def test_scenario_duplicate_subscriptions():
    """Verify concurrent/overlapping subscriptions from the same provider."""
    txns = generate_demo_transactions(seed=42)

    # In March 2024, both StreamFlix Premium (649) and StreamFlix Basic (199) occur
    march_streamflix = [
        t for t in txns
        if t.merchant_canonical == "StreamFlix" and t.date.year == 2024 and t.date.month == 3
    ]
    amounts = {t.amount for t in march_streamflix}
    assert 649.00 in amounts
    assert 199.00 in amounts


def test_scenario_one_off_purchases():
    """Verify presence of ordinary non-recurring purchases."""
    txns = generate_demo_transactions(seed=42)

    kirana = [t for t in txns if t.merchant_canonical == "Kirana Fresh"]
    sloth_cafe = [t for t in txns if t.merchant_canonical == "Sloth Cafe"]
    quickcab = [t for t in txns if t.merchant_canonical == "QuickCab"]

    assert len(kirana) == 1
    assert len(sloth_cafe) == 1
    assert len(quickcab) == 1


def test_scenario_refund_presence():
    """Verify presence of at least one negative amount refund."""
    txns = generate_demo_transactions(seed=42)
    refunds = [t for t in txns if t.is_refund]

    assert len(refunds) >= 1
    assert any(t.amount < 0 for t in refunds)
    # Check specific BookHaven refund
    book_refund = next(t for t in refunds if t.merchant_canonical == "BookHaven")
    assert book_refund.amount == -899.00


def test_scenario_merchant_description_variations():
    """Verify that multiple raw descriptions map to the same canonical merchant."""
    txns = generate_demo_transactions(seed=42)

    streamflix_descriptions = {
        t.raw_description for t in txns if t.merchant_canonical == "StreamFlix" and t.amount == 649.00
    }
    assert len(streamflix_descriptions) >= 3

    musicpulse_descriptions = {
        t.raw_description for t in txns if t.merchant_canonical == "MusicPulse"
    }
    assert len(musicpulse_descriptions) >= 3


def test_scenario_ambiguous_cases():
    """Verify ambiguous edge cases designed to challenge naive heuristics."""
    txns = generate_demo_transactions(seed=42)

    # Case A: Variable utility bill
    utility = [t for t in txns if t.merchant_canonical == "CityPower"]
    assert len(utility) == 10
    # Amounts vary across months
    utility_amounts = {t.amount for t in utility}
    assert len(utility_amounts) >= 5

    # Case B: False recurrence / coincidental twins
    dinefine = [t for t in txns if t.merchant_canonical == "DineFine"]
    assert len(dinefine) == 2
    assert (dinefine[1].date - dinefine[0].date).days == 31
    assert dinefine[0].amount == dinefine[1].amount == 750.00

    # Case C: Cancelled / Zombie subscription
    oldnews = [t for t in txns if t.merchant_canonical == "OldNews"]
    assert len(oldnews) == 3
    # Months are Jan, Feb, Mar 2024 only
    assert {t.date.month for t in oldnews} == {1, 2, 3}
