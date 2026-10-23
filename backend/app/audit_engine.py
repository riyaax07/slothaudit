"""Deterministic recurrence and anomaly detection engine for SlothAudit.

Analyzes normalized transaction streams to detect:
1. Recurring subscriptions (monthly, annual, variable utility)
2. Subscription price increases on established recurring baselines
3. Concurrent / duplicate subscription candidates

Design Principles:
- Deterministic: Identical input transactions always yield identical findings.
- Evidence-Backed: Every finding is tied directly to underlying transaction IDs.
- Conservative: Avoids false alarms on one-offs or coincidental pairs (e.g. 2 dining meals).
- Non-Accusatory: Never labels findings as fraud; potential savings are explicitly hypothetical.
- Independent: Pure Python and Pydantic; zero external AI/network dependencies.
"""

from collections import defaultdict
import datetime
from statistics import mean
from typing import Dict, List, Optional, Set, Tuple

from backend.app.merchant_normalizer import normalize_merchant
from backend.app.schemas import (
    AuditFinding,
    AuditReport,
    EvidenceItem,
    Transaction,
)

# ==============================================================================
# Centralized Audit Engine Threshold Constants
# ==============================================================================

# Minimum charges required to confirm a recurring monthly series
MIN_MONTHLY_OCCURRENCES: int = 3

# Minimum occurrences required to confirm an annual subscription
MIN_ANNUAL_OCCURRENCES: int = 2

# Cadence interval bounds (in calendar days)
MONTHLY_INTERVAL_MIN_DAYS: int = 25
MONTHLY_INTERVAL_MAX_DAYS: int = 36

ANNUAL_INTERVAL_MIN_DAYS: int = 345
ANNUAL_INTERVAL_MAX_DAYS: int = 385

# Inactivity threshold: if last charge is older than this relative to dataset end, marked inactive
INACTIVE_SUBSCRIPTION_DAYS: int = 65

# Maximum allowed price variance for a fixed-price subscription (5%)
FIXED_PRICE_VARIANCE_TOLERANCE: float = 0.05

# Minimum price step increase percentage to flag as price_increase (8.0%)
MIN_PRICE_INCREASE_PERCENT: float = 8.0

# Minimum baseline cycles at previous rate before a price hike is flagged
MIN_PRICE_INCREASE_BASELINE_COUNT: int = 2

# Minimum concurrent billing cycles to flag overlapping duplicate plans
MIN_CONCURRENT_CYCLES_FOR_DUPLICATE: int = 2


def _make_evidence_items(transactions: List[Transaction]) -> List[EvidenceItem]:
    """Convert transaction records into lightweight EvidenceItem schemas."""
    return [
        EvidenceItem(
            txn_id=t.txn_id,
            date=t.date,
            amount=t.amount,
            description=t.raw_description,
        )
        for t in transactions
    ]


def _check_annual_cadence(charges: List[Transaction]) -> bool:
    """Check if charges follow an approximate annual (365-day) interval pattern."""
    if len(charges) < MIN_ANNUAL_OCCURRENCES:
        return False
    for i in range(len(charges) - 1):
        diff = (charges[i + 1].date - charges[i].date).days
        if not (ANNUAL_INTERVAL_MIN_DAYS <= diff <= ANNUAL_INTERVAL_MAX_DAYS):
            return False
    return True


def _check_monthly_cadence(charges: List[Transaction]) -> bool:
    """Check if charges follow an approximate monthly (~30-day) interval pattern."""
    if len(charges) < MIN_MONTHLY_OCCURRENCES:
        return False

    intervals = [(charges[i + 1].date - charges[i].date).days for i in range(len(charges) - 1)]
    valid_intervals = [
        d for d in intervals if MONTHLY_INTERVAL_MIN_DAYS <= d <= MONTHLY_INTERVAL_MAX_DAYS
    ]
    # At least 75% of consecutive intervals must match monthly bounds
    return len(valid_intervals) / len(intervals) >= 0.75


def _detect_price_increase(
    charges: List[Transaction],
    merchant: str,
) -> Optional[AuditFinding]:
    """Detect if an established monthly series experienced a step-up price increase."""
    if len(charges) < 4:
        return None

    # Check for a single clear step transition in amounts
    amounts = [t.amount for t in charges]
    first_amount = amounts[0]

    # Find first index where amount changes significantly
    split_idx = None
    for idx, amt in enumerate(amounts):
        if abs(amt - first_amount) / first_amount > FIXED_PRICE_VARIANCE_TOLERANCE:
            split_idx = idx
            break

    if split_idx is None or split_idx < MIN_PRICE_INCREASE_BASELINE_COUNT:
        return None

    baseline_amounts = amounts[:split_idx]
    new_amounts = amounts[split_idx:]

    if len(new_amounts) < 2:
        return None

    # Both segments must be internally stable
    if max(baseline_amounts) - min(baseline_amounts) > first_amount * FIXED_PRICE_VARIANCE_TOLERANCE:
        return None

    second_amount = new_amounts[0]
    if max(new_amounts) - min(new_amounts) > second_amount * FIXED_PRICE_VARIANCE_TOLERANCE:
        return None

    # Must be a price increase (not decrease) exceeding threshold
    if second_amount <= first_amount:
        return None

    pct_increase = ((second_amount - first_amount) / first_amount) * 100.0
    if pct_increase < MIN_PRICE_INCREASE_PERCENT:
        return None

    hike_date = charges[split_idx].date
    amount_diff = second_amount - first_amount

    return AuditFinding(
        finding_id=f"FINDING-{merchant.upper().replace(' ', '_')}-PRICE-INCREASE",
        finding_type="price_increase",
        merchant_canonical=merchant,
        cadence="monthly",
        is_active=True,
        is_supplemental=True,
        related_finding_id=f"FINDING-{merchant.upper().replace(' ', '_')}-MONTHLY",
        last_charged_date=charges[-1].date,
        confidence=0.95,
        confidence_rationale=(
            f"Established {len(baseline_amounts)}-month baseline at ₹{first_amount:.2f} "
            f"followed by {len(new_amounts)} charges at revised rate of ₹{second_amount:.2f}. "
            f"Linked to primary series FINDING-{merchant.upper().replace(' ', '_')}-MONTHLY."
        ),
        recommended_action="investigate",
        evidence_txn_ids=[t.txn_id for t in charges],
        evidence_summary=(
            f"{len(charges)} charges: ₹{first_amount:.2f} (x{len(baseline_amounts)}) -> "
            f"₹{second_amount:.2f} (x{len(new_amounts)}) starting {hike_date}"
        ),
        evidence_items=_make_evidence_items(charges),
        estimated_monthly_cost=None,
        estimated_annual_cost=None,
        previous_amount=first_amount,
        new_amount=second_amount,
        amount_difference=round(amount_diff, 2),
        percent_increase=round(pct_increase, 1),
        explanation=(
            f"Supplemental price increase insight for active recurring subscription {merchant} "
            f"(FINDING-{merchant.upper().replace(' ', '_')}-MONTHLY): Billed at ₹{first_amount:.2f} "
            f"for {len(baseline_amounts)} cycles, then stepped up to ₹{second_amount:.2f} "
            f"(+{pct_increase:.1f}%, +₹{amount_diff:.2f}/mo) on {hike_date}. "
            f"Note: This is an analytical alert for the existing series and does not represent an additional subscription charge."
        ),
        caveats=[
            f"Supplemental insight: active recurring costs are accounted for under the primary finding (FINDING-{merchant.upper().replace(' ', '_')}-MONTHLY).",
            "Verify with your merchant statements whether this rate hike was authorized, scheduled, or promotional.",
        ],
    )


def _partition_concurrent_streams(charges: List[Transaction]) -> List[List[Transaction]]:
    """Partition charges into distinct concurrent recurring streams (e.g. basic + premium)."""
    # Group by calendar month: (year, month) -> list of charges
    month_map: Dict[Tuple[int, int], List[Transaction]] = defaultdict(list)
    for c in charges:
        month_map[(c.date.year, c.date.month)].append(c)

    # Check how many months contain multiple charges
    multi_charge_months = sum(1 for txns in month_map.values() if len(txns) >= 2)

    if multi_charge_months < MIN_CONCURRENT_CYCLES_FOR_DUPLICATE:
        return [charges]

    # Cluster by amount (e.g. 649 vs 199)
    amount_clusters: Dict[float, List[Transaction]] = defaultdict(list)
    for c in charges:
        # Find existing cluster within tolerance
        matched_cluster = None
        for key in amount_clusters:
            if abs(c.amount - key) / max(key, 1.0) <= FIXED_PRICE_VARIANCE_TOLERANCE:
                matched_cluster = key
                break
        if matched_cluster is not None:
            amount_clusters[matched_cluster].append(c)
        else:
            amount_clusters[c.amount].append(c)

    # Only return clusters with at least MIN_MONTHLY_OCCURRENCES
    valid_clusters = [cluster for cluster in amount_clusters.values() if len(cluster) >= 2]
    return valid_clusters if len(valid_clusters) >= 2 else [charges]


def audit_transactions(transactions: List[Transaction]) -> AuditReport:
    """Analyze a collection of normalized transactions and generate an AuditReport.

    Args:
        transactions: List of normalized Transaction objects.

    Returns:
        AuditReport: Complete deterministic audit findings and cost estimates.
    """
    if not transactions:
        return AuditReport(
            total_transactions_analyzed=0,
            total_merchants_analyzed=0,
            findings=[],
            total_estimated_monthly_recurring=0.0,
            total_estimated_annual_recurring=0.0,
            potential_hypothetical_annual_savings=0.0,
            summary_notes=["No transactions supplied for audit."],
        )

    # Determine latest transaction date in entire dataset
    dataset_end_date = max(t.date for t in transactions)

    # Group transactions by canonical merchant
    merchant_groups: Dict[str, List[Transaction]] = defaultdict(list)
    for t in transactions:
        canon = normalize_merchant(t.merchant_canonical or t.merchant_raw or t.raw_description)
        merchant_groups[canon].append(t)

    findings: List[AuditFinding] = []
    total_monthly_recurring = 0.0
    total_annual_recurring = 0.0
    total_hypothetical_savings = 0.0

    # Evaluate each merchant group deterministically
    for merchant in sorted(merchant_groups.keys()):
        txns = merchant_groups[merchant]

        # Filter charges and refunds
        charges = [t for t in txns if not t.is_refund and t.amount > 0]
        charges.sort(key=lambda t: (t.date, t.txn_id))

        if not charges:
            continue

        # Check for multiple concurrent streams (duplicate/overlapping subscriptions)
        streams = _partition_concurrent_streams(charges)

        if len(streams) >= 2:
            # Multi-stream candidate (e.g. StreamFlix Basic + Premium)
            # Sort streams by average amount descending: primary plan is highest cost
            streams.sort(key=lambda s: mean(t.amount for t in s), reverse=True)
            primary_stream = streams[0]
            secondary_streams = streams[1:]

            # Audit primary stream
            primary_amt = round(mean(t.amount for t in primary_stream), 2)
            findings.append(
                AuditFinding(
                    finding_id=f"FINDING-{merchant.upper().replace(' ', '_')}-PRIMARY-RECURRING",
                    finding_type="recurring",
                    merchant_canonical=merchant,
                    cadence="monthly",
                    is_active=True,
                    is_supplemental=False,
                    last_charged_date=primary_stream[-1].date,
                    confidence=0.95,
                    confidence_rationale=(
                        f"Detected primary recurring subscription with {len(primary_stream)} "
                        f"monthly charges at ₹{primary_amt:.2f}."
                    ),
                    recommended_action="keep",
                    evidence_txn_ids=[t.txn_id for t in primary_stream],
                    evidence_summary=(
                        f"{len(primary_stream)} charges averaging ₹{primary_amt:.2f} "
                        f"from {primary_stream[0].date} to {primary_stream[-1].date}"
                    ),
                    evidence_items=_make_evidence_items(primary_stream),
                    estimated_monthly_cost=primary_amt,
                    estimated_annual_cost=round(primary_amt * 12, 2),
                    explanation=f"Active primary recurring subscription for {merchant}.",
                    caveats=["Core subscription plan."],
                )
            )
            total_monthly_recurring += primary_amt
            total_annual_recurring += round(primary_amt * 12, 2)

            # Audit secondary streams as duplicate candidates
            for sec_idx, sec_stream in enumerate(secondary_streams, start=2):
                sec_amt = round(mean(t.amount for t in sec_stream), 2)
                sec_annual = round(sec_amt * 12, 2)

                findings.append(
                    AuditFinding(
                        finding_id=f"FINDING-{merchant.upper().replace(' ', '_')}-DUPLICATE-{sec_idx}",
                        finding_type="duplicate_candidate",
                        merchant_canonical=merchant,
                        cadence="monthly",
                        is_active=True,
                        is_supplemental=False,
                        last_charged_date=sec_stream[-1].date,
                        confidence=0.90,
                        confidence_rationale=(
                            f"Detected overlapping recurring plan (₹{sec_amt:.2f}/mo) charged "
                            f"concurrently in the same billing cycles as the primary plan."
                        ),
                        recommended_action="consider_cancelling",
                        evidence_txn_ids=[t.txn_id for t in sec_stream],
                        evidence_summary=(
                            f"{len(sec_stream)} overlapping charges averaging ₹{sec_amt:.2f}/mo "
                            f"running concurrently across {len(sec_stream)} billing cycles"
                        ),
                        evidence_items=_make_evidence_items(sec_stream),
                        estimated_monthly_cost=sec_amt,
                        estimated_annual_cost=sec_annual,
                        overlapping_plan_name=f"{merchant} Secondary Stream (₹{sec_amt:.2f}/mo)",
                        potential_hypothetical_monthly_savings=sec_amt,
                        explanation=(
                            f"Possible duplicate or overlapping subscription detected for {merchant}. "
                            f"Charges at ₹{sec_amt:.2f} occur simultaneously with primary charges of ₹{primary_amt:.2f}."
                        ),
                        caveats=[
                            "Hypothetical savings assume user intends to consolidate to a single account/plan.",
                            "Family or secondary household memberships cannot be distinguished without user confirmation.",
                        ],
                    )
                )
                total_monthly_recurring += sec_amt
                total_annual_recurring += sec_annual
                total_hypothetical_savings += sec_annual

            continue

        # Single-stream analysis
        # 1. Check Annual Subscription
        if _check_annual_cadence(charges):
            latest_annual_charge = charges[-1].amount
            findings.append(
                AuditFinding(
                    finding_id=f"FINDING-{merchant.upper().replace(' ', '_')}-ANNUAL",
                    finding_type="recurring",
                    merchant_canonical=merchant,
                    cadence="annual",
                    is_active=True,
                    is_supplemental=False,
                    last_charged_date=charges[-1].date,
                    confidence=0.90 if len(charges) == 2 else 0.95,
                    confidence_rationale=(
                        f"Detected {len(charges)} annual renewal charges spaced approximately "
                        f"365 days apart at consistent amount (₹{latest_annual_charge:.2f})."
                    ),
                    recommended_action="keep",
                    evidence_txn_ids=[t.txn_id for t in charges],
                    evidence_summary=(
                        f"{len(charges)} annual charges: " +
                        ", ".join(f"₹{t.amount:.2f} ({t.date})" for t in charges)
                    ),
                    evidence_items=_make_evidence_items(charges),
                    estimated_monthly_cost=round(latest_annual_charge / 12, 2),
                    estimated_annual_cost=round(latest_annual_charge, 2),
                    explanation=f"Annual recurring subscription for {merchant}.",
                    caveats=[
                        "Annualized cost reflects the single yearly renewal fee.",
                        "Monthly cost is an accounting spread (annual / 12).",
                    ],
                )
            )
            total_monthly_recurring += round(latest_annual_charge / 12, 2)
            total_annual_recurring += round(latest_annual_charge, 2)
            continue

        # 2. Check Monthly Subscription
        if _check_monthly_cadence(charges):
            # Check for Price Increase within series first
            price_hike_finding = _detect_price_increase(charges, merchant)
            if price_hike_finding:
                findings.append(price_hike_finding)

            # Check if active or churned/inactive
            days_since_last = (dataset_end_date - charges[-1].date).days
            is_inactive = days_since_last > INACTIVE_SUBSCRIPTION_DAYS

            amounts = [t.amount for t in charges]
            price_variance = (max(amounts) - min(amounts)) / max(min(amounts), 1.0)
            is_variable = price_variance > FIXED_PRICE_VARIANCE_TOLERANCE and not price_hike_finding

            if is_variable:
                cadence = "variable_monthly"
                avg_cost = round(mean(amounts), 2)
                findings.append(
                    AuditFinding(
                        finding_id=f"FINDING-{merchant.upper().replace(' ', '_')}-VARIABLE-UTILITY",
                        finding_type="recurring",
                        merchant_canonical=merchant,
                        cadence=cadence,
                        is_active=True,
                        is_supplemental=False,
                        last_charged_date=charges[-1].date,
                        confidence=0.85,
                        confidence_rationale=(
                            f"Detected {len(charges)} monthly charges with regular date intervals "
                            f"but fluctuating consumption amounts (₹{min(amounts):.2f} - ₹{max(amounts):.2f})."
                        ),
                        recommended_action="keep",
                        evidence_txn_ids=[t.txn_id for t in charges],
                        evidence_summary=(
                            f"{len(charges)} charges averaging ₹{avg_cost:.2f}/mo "
                            f"(range ₹{min(amounts):.2f} to ₹{max(amounts):.2f})"
                        ),
                        evidence_items=_make_evidence_items(charges),
                        estimated_monthly_cost=avg_cost,
                        estimated_annual_cost=round(avg_cost * 12, 2),
                        explanation=(
                            f"Variable utility or usage-based monthly bill detected for {merchant}."
                        ),
                        caveats=[
                            "Variable utility bill detected. Recurring costs fluctuate by usage.",
                            "Annual estimate is a projection based on historical average.",
                        ],
                    )
                )
                total_monthly_recurring += avg_cost
                total_annual_recurring += round(avg_cost * 12, 2)
            elif is_inactive:
                last_amt = charges[-1].amount
                historical_monthly = round(last_amt, 2)
                historical_annual = round(last_amt * 12, 2)
                findings.append(
                    AuditFinding(
                        finding_id=f"FINDING-{merchant.upper().replace(' ', '_')}-INACTIVE-MONTHLY",
                        finding_type="recurring",
                        merchant_canonical=merchant,
                        cadence="monthly",
                        is_active=False,
                        is_supplemental=False,
                        last_charged_date=charges[-1].date,
                        confidence=0.85,
                        confidence_rationale=(
                            f"Detected {len(charges)} historical monthly charges ending on {charges[-1].date}. "
                            f"No charges detected in the subsequent {days_since_last} days; subscription is confirmed inactive/churned."
                        ),
                        recommended_action="investigate",
                        evidence_txn_ids=[t.txn_id for t in charges],
                        evidence_summary=(
                            f"{len(charges)} historical charges at ₹{last_amt:.2f} "
                            f"(last billed on {charges[-1].date}, {days_since_last} days ago)"
                        ),
                        evidence_items=_make_evidence_items(charges),
                        estimated_monthly_cost=0.0,
                        estimated_annual_cost=0.0,
                        historical_monthly_cost=historical_monthly,
                        historical_annual_cost=historical_annual,
                        explanation=(
                            f"Inactive or cancelled monthly subscription for {merchant}. "
                            f"Last billed on {charges[-1].date} ({days_since_last} days ago). "
                            f"Previously cost ₹{historical_monthly:.2f}/mo (₹{historical_annual:.2f}/yr). "
                            f"Not counted toward current active monthly/annual commitments."
                        ),
                        caveats=[
                            "Inactive or cancelled subscription: zero active cost is counted toward your current commitments.",
                            "Action 'investigate': verify with your bank or account provider that recurring autopay authorization has been fully terminated.",
                        ],
                    )
                )
            else:
                # Active fixed-price monthly subscription
                current_monthly_rate = charges[-1].amount
                annual_cost = round(current_monthly_rate * 12, 2)
                findings.append(
                    AuditFinding(
                        finding_id=f"FINDING-{merchant.upper().replace(' ', '_')}-MONTHLY",
                        finding_type="recurring",
                        merchant_canonical=merchant,
                        cadence="monthly",
                        is_active=True,
                        is_supplemental=False,
                        last_charged_date=charges[-1].date,
                        confidence=min(0.98, round(0.75 + 0.025 * len(charges), 2)),
                        confidence_rationale=(
                            f"Detected {len(charges)} monthly charges with regular ~30-day interval "
                            f"and consistent billing rate (₹{current_monthly_rate:.2f})."
                        ),
                        recommended_action="keep",
                        evidence_txn_ids=[t.txn_id for t in charges],
                        evidence_summary=(
                            f"{len(charges)} charges at ₹{current_monthly_rate:.2f}/mo "
                            f"from {charges[0].date} to {charges[-1].date}"
                        ),
                        evidence_items=_make_evidence_items(charges),
                        estimated_monthly_cost=current_monthly_rate,
                        estimated_annual_cost=annual_cost,
                        explanation=f"Active recurring monthly subscription for {merchant}.",
                        caveats=["Cost projection assumes current monthly tier remains active."],
                    )
                )
                total_monthly_recurring += current_monthly_rate
                total_annual_recurring += annual_cost

    # Summary notes
    active_count = sum(1 for f in findings if f.is_active and not f.is_supplemental)
    inactive_count = sum(1 for f in findings if not f.is_active)
    supplemental_count = sum(1 for f in findings if f.is_supplemental)
    duplicate_count = sum(1 for f in findings if f.finding_type == "duplicate_candidate")

    notes = [
        f"Analyzed {len(transactions)} transactions across {len(merchant_groups)} canonical merchants.",
        f"Identified {len(findings)} total audit findings: {active_count} active subscriptions, "
        f"{inactive_count} inactive/churned subscriptions, {supplemental_count} supplemental rate adjustments, "
        f"and {duplicate_count} duplicate candidate alerts.",
        f"Total active recurring commitment: ₹{total_monthly_recurring:.2f}/mo (₹{total_annual_recurring:.2f}/yr).",
    ]
    if inactive_count > 0:
        notes.append(
            f"{inactive_count} inactive subscription(s) recorded with ₹0.00 active cost commitment."
        )
    if supplemental_count > 0:
        notes.append(
            f"{supplemental_count} supplemental price increase alert(s) linked to existing recurring series (zero double-counting)."
        )
    if total_hypothetical_savings > 0:
        notes.append(
            f"Potential hypothetical annual savings: ₹{total_hypothetical_savings:.2f} "
            f"if identified duplicate candidates are cancelled."
        )

    return AuditReport(
        total_transactions_analyzed=len(transactions),
        total_merchants_analyzed=len(merchant_groups),
        findings=findings,
        total_estimated_monthly_recurring=round(total_monthly_recurring, 2),
        total_estimated_annual_recurring=round(total_annual_recurring, 2),
        potential_hypothetical_annual_savings=round(total_hypothetical_savings, 2),
        currency="INR",
        summary_notes=notes,
    )
