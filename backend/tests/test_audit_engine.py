"""Unit tests for SlothAudit deterministic audit engine.

Verifies:
- Monthly and annual recurrence detection
- Non-recurring one-offs correctly ignored (no false alarms)
- Price increase detection with historical baseline verification
- Refund exclusion from recurring cost projections
- Variable consumption utility bill handling
- Inactive/churned subscription detection and zero-cost assignment
- Conservative duplicate candidate identification and hypothetical savings
- Deterministic repeated execution
- Every finding references valid underlying transaction IDs as evidence
- Handcrafted edge-case fixtures independent of the synthetic generator
"""

import datetime
from pathlib import Path
import pytest

from backend.app.audit_engine import audit_transactions
from backend.app.csv_parser import parse_transactions
from backend.app.schemas import Transaction


# ==============================================================================
# Helper to build handcrafted transaction fixtures
# ==============================================================================

def make_txn(
    txn_id: str,
    date_str: str,
    amount: float,
    merchant: str = "TestMerchant",
    desc: str = "Test Description",
    is_refund: bool = False,
    row: int = 2,
) -> Transaction:
    """Helper to construct strongly typed Transaction models for tests."""
    d = datetime.date.fromisoformat(date_str)
    return Transaction(
        txn_id=txn_id,
        date=d,
        amount=amount,
        currency="INR",
        raw_description=desc,
        merchant_raw=merchant,
        merchant_canonical=merchant,
        is_refund=is_refund,
        source_row=row,
    )


# ==============================================================================
# Tests on Handcrafted Fixtures (Independent of Generator)
# ==============================================================================

def test_monthly_subscription_handcrafted():
    """Verify monthly recurrence detection on an explicit 4-month sequence."""
    txns = [
        make_txn("T-1", "2024-01-05", 299.00, "SaaS App", "SaaS Sub 1"),
        make_txn("T-2", "2024-02-05", 299.00, "SaaS App", "SaaS Sub 2"),
        make_txn("T-3", "2024-03-05", 299.00, "SaaS App", "SaaS Sub 3"),
        make_txn("T-4", "2024-04-05", 299.00, "SaaS App", "SaaS Sub 4"),
    ]
    report = audit_transactions(txns)

    assert len(report.findings) == 1
    f = report.findings[0]
    assert f.finding_type == "recurring"
    assert f.merchant_canonical == "Saas App"
    assert f.cadence == "monthly"
    assert f.estimated_monthly_cost == 299.00
    assert f.estimated_annual_cost == 299.00 * 12
    assert f.confidence >= 0.85
    assert f.evidence_txn_ids == ["T-1", "T-2", "T-3", "T-4"]


def test_annual_subscription_handcrafted():
    """Verify annual cadence detection on 2 charges separated by 365 days."""
    txns = [
        make_txn("T-1", "2023-06-15", 4999.00, "Annual VPN", "VPN 1 Year"),
        make_txn("T-2", "2024-06-14", 4999.00, "Annual VPN", "VPN 1 Year Renewal"),
    ]
    report = audit_transactions(txns)

    assert len(report.findings) == 1
    f = report.findings[0]
    assert f.finding_type == "recurring"
    assert f.cadence == "annual"
    assert f.estimated_annual_cost == 4999.00
    assert f.estimated_monthly_cost == round(4999.00 / 12, 2)
    assert f.evidence_txn_ids == ["T-1", "T-2"]


def test_one_off_and_coincidental_twin_not_flagged():
    """Verify that isolated purchases and 2-occurrence coincidental meals are NOT flagged."""
    txns = [
        # Isolated one-off
        make_txn("T-1", "2024-01-10", 1200.00, "Shoe Store", "Running Shoes"),
        # Coincidental twins 30 days apart in early 2024, no subsequent activity in 9 months
        make_txn("T-2", "2024-02-10", 850.00, "Bistro Cafe", "Dinner with Friend"),
        make_txn("T-3", "2024-03-12", 850.00, "Bistro Cafe", "Lunch Meeting"),
        # Anchor dataset end date to October 2024
        make_txn("T-4", "2024-10-20", 50.00, "Stationery", "Notebook"),
    ]
    report = audit_transactions(txns)

    # Neither Shoe Store nor Bistro Cafe should be flagged as recurring
    recurring_merchants = {f.merchant_canonical for f in report.findings}
    assert "Shoe Store" not in recurring_merchants
    assert "Bistro Cafe" not in recurring_merchants


def test_price_increase_detection_handcrafted():
    """Verify price increase detection on established recurring baseline."""
    txns = [
        # 3 baseline charges at 500
        make_txn("T-1", "2024-01-01", 500.00, "Cloud Host"),
        make_txn("T-2", "2024-02-01", 500.00, "Cloud Host"),
        make_txn("T-3", "2024-03-01", 500.00, "Cloud Host"),
        # 2 charges at 650 (30% increase)
        make_txn("T-4", "2024-04-01", 650.00, "Cloud Host"),
        make_txn("T-5", "2024-05-01", 650.00, "Cloud Host"),
    ]
    report = audit_transactions(txns)

    price_findings = [f for f in report.findings if f.finding_type == "price_increase"]
    assert len(price_findings) == 1
    pf = price_findings[0]
    assert pf.merchant_canonical == "Cloud Host"
    assert pf.is_supplemental is True
    assert pf.related_finding_id == "FINDING-CLOUD_HOST-MONTHLY"
    assert pf.previous_amount == 500.00
    assert pf.new_amount == 650.00
    assert pf.amount_difference == 150.00
    assert pf.percent_increase == 30.0
    assert pf.recommended_action == "investigate"
    assert pf.estimated_monthly_cost is None  # Prevents double-counting
    assert pf.evidence_txn_ids == ["T-1", "T-2", "T-3", "T-4", "T-5"]

    # Verify no double counting in report totals
    assert report.total_estimated_monthly_recurring == 650.00
    assert report.total_estimated_annual_recurring == 650.00 * 12


def test_refund_exclusion_from_recurring_cost():
    """Verify that refunds are excluded from recurring costs and do not confuse recurrence."""
    txns = [
        make_txn("T-1", "2024-01-15", 399.00, "Streaming Audio"),
        make_txn("T-2", "2024-02-15", 399.00, "Streaming Audio"),
        # Refund for accidental charge
        make_txn("T-REF", "2024-02-20", -399.00, "Streaming Audio", is_refund=True),
        make_txn("T-3", "2024-03-15", 399.00, "Streaming Audio"),
    ]
    report = audit_transactions(txns)

    assert len(report.findings) == 1
    f = report.findings[0]
    assert f.finding_type == "recurring"
    assert f.estimated_monthly_cost == 399.00
    # Refund is not part of the active subscription charge evidence
    assert "T-REF" not in f.evidence_txn_ids


def test_variable_utility_bill_handcrafted():
    """Verify fluctuating utility bills are categorized as variable_monthly with average costs."""
    txns = [
        make_txn("U-1", "2024-01-12", 1200.00, "Water Works"),
        make_txn("U-2", "2024-02-14", 1450.00, "Water Works"),
        make_txn("U-3", "2024-03-11", 1300.00, "Water Works"),
        make_txn("U-4", "2024-04-13", 1600.00, "Water Works"),
    ]
    report = audit_transactions(txns)

    assert len(report.findings) == 1
    f = report.findings[0]
    assert f.finding_type == "recurring"
    assert f.cadence == "variable_monthly"
    assert f.estimated_monthly_cost == round((1200 + 1450 + 1300 + 1600) / 4, 2)
    assert any("Variable utility bill" in c for c in f.caveats)


def test_inactive_churned_subscription_handcrafted():
    """Verify subscription that ceased months ago receives zero active cost and an inactivity caveat."""
    txns = [
        # Active in Q1 2024
        make_txn("O-1", "2024-01-01", 199.00, "Past Newspaper"),
        make_txn("O-2", "2024-02-01", 199.00, "Past Newspaper"),
        make_txn("O-3", "2024-03-01", 199.00, "Past Newspaper"),
        # Current active recurring service in late 2024 (anchor)
        make_txn("A-1", "2024-09-01", 50.00, "Active Service"),
        make_txn("A-2", "2024-10-01", 50.00, "Active Service"),
        make_txn("A-3", "2024-11-01", 50.00, "Active Service"),
    ]
    report = audit_transactions(txns)

    newspaper_findings = [f for f in report.findings if f.merchant_canonical == "Past Newspaper"]
    assert len(newspaper_findings) == 1
    nf = newspaper_findings[0]
    assert nf.is_active is False
    assert nf.recommended_action == "investigate"
    assert nf.estimated_monthly_cost == 0.0
    assert nf.estimated_annual_cost == 0.0
    assert nf.historical_monthly_cost == 199.00
    assert nf.historical_annual_cost == 199.00 * 12
    assert any("inactive or cancelled" in c.lower() for c in nf.caveats)

    # Active totals must only reflect Active Service (₹50.00), excluding Past Newspaper
    assert report.total_estimated_monthly_recurring == 50.00
    assert report.total_estimated_annual_recurring == 50.00 * 12


def test_duplicate_candidate_handcrafted():
    """Verify concurrent recurring plans under the same merchant trigger a duplicate finding."""
    txns = [
        # Plan A (₹600 on the 5th)
        make_txn("P1", "2024-01-05", 600.00, "Video Hub"),
        make_txn("P2", "2024-02-05", 600.00, "Video Hub"),
        make_txn("P3", "2024-03-05", 600.00, "Video Hub"),
        # Plan B (₹200 on the 20th in same months)
        make_txn("S1", "2024-01-20", 200.00, "Video Hub"),
        make_txn("S2", "2024-02-20", 200.00, "Video Hub"),
        make_txn("S3", "2024-03-20", 200.00, "Video Hub"),
    ]
    report = audit_transactions(txns)

    dup_findings = [f for f in report.findings if f.finding_type == "duplicate_candidate"]
    assert len(dup_findings) == 1
    df = dup_findings[0]
    assert df.merchant_canonical == "Video Hub"
    assert df.estimated_monthly_cost == 200.00
    assert df.potential_hypothetical_monthly_savings == 200.00
    assert df.recommended_action == "consider_cancelling"
    assert report.potential_hypothetical_annual_savings == 200.00 * 12


def test_deterministic_repeated_runs_handcrafted():
    """Verify that multiple audit runs on identical inputs produce identical reports."""
    txns = [
        make_txn("T-1", "2024-01-01", 100.00, "Alpha"),
        make_txn("T-2", "2024-02-01", 100.00, "Alpha"),
        make_txn("T-3", "2024-03-01", 100.00, "Alpha"),
        make_txn("T-4", "2024-04-01", 100.00, "Alpha"),
    ]
    rep1 = audit_transactions(txns)
    rep2 = audit_transactions(txns)

    assert rep1.model_dump() == rep2.model_dump()


# ==============================================================================
# Full Integration Tests on Synthetic Demo Dataset
# ==============================================================================

def test_audit_demo_transactions_dataset():
    """Verify audit results on the full data/demo_transactions.csv file."""
    demo_path = Path("data/demo_transactions.csv")
    assert demo_path.exists()

    txns, _ = parse_transactions(demo_path.read_bytes(), "demo.csv")
    assert len(txns) == 83

    report = audit_transactions(txns)
    assert report.total_transactions_analyzed == 83
    assert report.total_merchants_analyzed == 16

    # 1. Total findings count
    assert len(report.findings) == 10

    # 2. Finding types distribution
    types = [f.finding_type for f in report.findings]
    assert types.count("recurring") == 8
    assert types.count("price_increase") == 1
    assert types.count("duplicate_candidate") == 1

    # 3. Recurring subscriptions verified
    recurring_merchants = {f.merchant_canonical for f in report.findings if f.finding_type == "recurring"}
    expected_recurring = {
        "StreamFlix",
        "MusicPulse",
        "CloudVault",
        "GymFit",
        "CodeHost",
        "DevToolbox",
        "CityPower",
        "OldNews",
    }
    assert recurring_merchants == expected_recurring

    # 4. Price hike verified on DevToolbox
    price_hike = next(f for f in report.findings if f.finding_type == "price_increase")
    assert price_hike.merchant_canonical == "DevToolbox"
    assert price_hike.previous_amount == 499.00
    assert price_hike.new_amount == 699.00
    assert price_hike.amount_difference == 200.00

    # 5. Duplicate candidate verified on StreamFlix Basic
    duplicate = next(f for f in report.findings if f.finding_type == "duplicate_candidate")
    assert duplicate.merchant_canonical == "StreamFlix"
    assert duplicate.estimated_monthly_cost == 199.00
    assert duplicate.potential_hypothetical_monthly_savings == 199.00

    # 6. One-offs NOT in findings
    one_off_merchants = {
        "Kirana Fresh",
        "Sloth Cafe",
        "QuickCab",
        "BookHaven",
        "Electronics Hub",
        "Metro Transit",
        "PharmHealth",
        "DineFine",
    }
    flagged_merchants = {f.merchant_canonical for f in report.findings}
    for m in one_off_merchants:
        assert m not in flagged_merchants, f"False positive detected: {m} should not be flagged"

    # 7. Inactive subscription (OldNews) explicitly verified
    oldnews = next(f for f in report.findings if f.merchant_canonical == "OldNews")
    assert oldnews.is_active is False
    assert oldnews.recommended_action == "investigate"
    assert oldnews.estimated_monthly_cost == 0.0
    assert oldnews.estimated_annual_cost == 0.0
    assert oldnews.historical_monthly_cost == 299.00
    assert oldnews.historical_annual_cost == 299.00 * 12
    assert oldnews.last_charged_date == datetime.date(2024, 3, 25)

    # 8. Supplemental price increase linkage and zero double counting
    assert price_hike.is_supplemental is True
    assert price_hike.related_finding_id == "FINDING-DEVTOOLBOX-MONTHLY"
    assert price_hike.estimated_monthly_cost is None
    assert price_hike.estimated_annual_cost is None

    # 9. Verify exact cost aggregates without double counting
    active_monthly_sum = sum(
        f.estimated_monthly_cost for f in report.findings if f.is_active and not f.is_supplemental
    )
    assert round(active_monthly_sum, 2) == report.total_estimated_monthly_recurring
    assert report.total_estimated_monthly_recurring == 5015.92
    assert report.total_estimated_annual_recurring == 60191.00
    assert report.potential_hypothetical_annual_savings == 2388.00

    # 10. Evidence IDs all link to real transactions
    valid_ids = {t.txn_id for t in txns}
    for f in report.findings:
        assert len(f.evidence_txn_ids) > 0
        for tid in f.evidence_txn_ids:
            assert tid in valid_ids, f"Invalid evidence txn_id {tid} in finding {f.finding_id}"


def test_inactive_subscription_and_no_double_counting_comprehensive():
    """Verify that multiple insights on one series share evidence without duplicating recurring cost."""
    txns = [
        # Series 1: Active monthly subscription with price hike (SaasPro: 300 x3, then 450 x3)
        make_txn("SP-1", "2024-05-01", 300.00, "SaasPro"),
        make_txn("SP-2", "2024-06-01", 300.00, "SaasPro"),
        make_txn("SP-3", "2024-07-01", 300.00, "SaasPro"),
        make_txn("SP-4", "2024-08-01", 450.00, "SaasPro"),
        make_txn("SP-5", "2024-09-01", 450.00, "SaasPro"),
        make_txn("SP-6", "2024-10-01", 450.00, "SaasPro"),
        # Series 2: Inactive churned subscription (ceased in Feb 2024)
        make_txn("CH-1", "2023-12-15", 150.00, "OldClub"),
        make_txn("CH-2", "2024-01-15", 150.00, "OldClub"),
        make_txn("CH-3", "2024-02-15", 150.00, "OldClub"),
    ]
    report = audit_transactions(txns)

    # SaasPro should have 2 findings: 1 recurring, 1 supplemental price increase
    sp_findings = [f for f in report.findings if f.merchant_canonical == "Saaspro"]
    assert len(sp_findings) == 2

    recurring_sp = next(f for f in sp_findings if f.finding_type == "recurring")
    hike_sp = next(f for f in sp_findings if f.finding_type == "price_increase")

    # Linked correctly
    assert hike_sp.is_supplemental is True
    assert hike_sp.related_finding_id == recurring_sp.finding_id
    assert recurring_sp.is_supplemental is False

    # Evidence IDs are shared between the primary series and supplemental alert
    assert set(hike_sp.evidence_txn_ids) == set(recurring_sp.evidence_txn_ids)

    # Inactive subscription OldClub
    old_club = next(f for f in report.findings if f.merchant_canonical == "Oldclub")
    assert old_club.is_active is False
    assert old_club.estimated_monthly_cost == 0.0
    assert old_club.historical_monthly_cost == 150.00

    # Total active recurring cost must be EXACTLY 450.00/mo (SaasPro current rate only)
    assert report.total_estimated_monthly_recurring == 450.00
    assert report.total_estimated_annual_recurring == 450.00 * 12
