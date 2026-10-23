"""Pydantic schemas for normalized financial transactions and parse reports.

SlothAudit is a local-first recurring payment auditor. These schemas define
the normalized transaction model used across the ingestion, audit engine,
and reporting layers.
"""

import datetime
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, field_validator, model_validator


class Transaction(BaseModel):
    """Normalized financial transaction record.

    Sign Convention:
        - Debits / Expenses / Charges: POSITIVE numbers (e.g., +649.00).
          Represents money leaving the user's account for goods, services, or subscriptions.
        - Credits / Refunds / Chargebacks: NEGATIVE numbers (e.g., -649.00).
          Represents money credited back to the user's account.
        - When `is_refund` is True, `amount` is negative by convention.
    """

    txn_id: str = Field(
        ...,
        description="Stable unique transaction identifier (e.g. 'TXN-001' or hash)",
    )
    date: datetime.date = Field(
        ...,
        description="Transaction posting or settlement date (YYYY-MM-DD)",
    )
    amount: float = Field(
        ...,
        description=(
            "Transaction amount in major currency units. "
            "Positive = debit/expense/charge, Negative = credit/refund."
        ),
    )
    currency: str = Field(
        default="INR",
        description="ISO 4217 3-letter currency code (default: 'INR')",
    )
    raw_description: str = Field(
        ...,
        description="Original transaction narration string from the source statement/CSV",
    )
    merchant_raw: str = Field(
        ...,
        description="Extracted merchant name prior to canonical normalization",
    )
    merchant_canonical: str = Field(
        ...,
        description="Normalized, clean merchant identifier (e.g. 'StreamFlix' for 'STREAMFLIX MUMBAI')",
    )
    is_refund: bool = Field(
        default=False,
        description="True if this transaction represents a refund, reversal, or credit",
    )
    source_row: int = Field(
        ...,
        ge=1,
        description="1-based row index in the original source statement",
    )

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        code = v.strip().upper()
        if len(code) != 3 or not code.isalpha():
            raise ValueError(f"Currency must be a 3-letter alphabetic code, got {v!r}")
        return code

    @field_validator("txn_id", "raw_description", "merchant_raw", "merchant_canonical")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Field cannot be empty or whitespace only")
        return cleaned

    @model_validator(mode="after")
    def sync_refund_and_amount(self) -> "Transaction":
        """Keep amount sign and is_refund flag consistent."""
        if self.amount < 0 and not self.is_refund:
            self.is_refund = True
        elif self.is_refund and self.amount > 0:
            # If flagged as refund but positive, negate amount to enforce convention
            self.amount = -abs(self.amount)
        return self


class ParseReport(BaseModel):
    """Summary of transaction file ingestion and schema validation results."""

    total_rows: int = Field(..., ge=0, description="Total raw rows processed from source file")
    valid_rows: int = Field(..., ge=0, description="Number of rows successfully validated")
    invalid_rows: int = Field(default=0, ge=0, description="Count of rows failing validation")
    errors: List[str] = Field(
        default_factory=list,
        description="Validation error messages encountered during parsing",
    )
    date_range_start: Optional[datetime.date] = Field(
        default=None,
        description="Earliest valid transaction date in batch",
    )
    date_range_end: Optional[datetime.date] = Field(
        default=None,
        description="Latest valid transaction date in batch",
    )
    currencies_found: List[str] = Field(
        default_factory=list,
        description="List of unique currency codes present in the batch",
    )


class EvidenceItem(BaseModel):
    """Structured evidence record referencing a specific source transaction."""

    txn_id: str = Field(..., description="Unique transaction ID")
    date: datetime.date = Field(..., description="Transaction date")
    amount: float = Field(..., description="Transaction amount")
    description: str = Field(..., description="Transaction raw description")


class AuditFinding(BaseModel):
    """Individual audit finding (recurring payment, price increase, or duplicate candidate).

    Distinguishes clearly between detected historical evidence and estimated costs.
    Findings are purely observational and analytical; they are not accusations of fraud.
    """

    finding_id: str = Field(..., description="Stable unique identifier for this audit finding")
    finding_type: Literal["recurring", "price_increase", "duplicate_candidate"] = Field(
        ...,
        description="Category of the detected finding",
    )
    merchant_canonical: str = Field(..., description="Normalized merchant name")
    cadence: Literal["monthly", "annual", "variable_monthly", "irregular", "unknown"] = Field(
        default="monthly",
        description="Observed billing interval cadence",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Detection confidence score from 0.0 (uncertain) to 1.0 (certain)",
    )
    confidence_rationale: str = Field(
        ...,
        description="Plain-language explanation of why this confidence level was assigned",
    )
    recommended_action: Literal["keep", "investigate", "consider_cancelling"] = Field(
        default="keep",
        description="Actionable suggestion for the user",
    )

    # Historical Evidence
    evidence_txn_ids: List[str] = Field(
        ...,
        description="Transaction IDs that serve as historical evidence for this finding",
    )
    evidence_summary: str = Field(
        ...,
        description="Summary of historical evidence counts, dates, and amounts",
    )
    evidence_items: List[EvidenceItem] = Field(
        default_factory=list,
        description="Structured list of underlying transaction evidence",
    )

    # Status & Insight Structure
    is_active: bool = Field(
        default=True,
        description="True if the subscription series is currently active; False if churned/inactive.",
    )
    is_supplemental: bool = Field(
        default=False,
        description="True if this finding is a supplemental insight (e.g. price increase) on an existing series rather than a separate subscription.",
    )
    related_finding_id: Optional[str] = Field(
        default=None,
        description="Finding ID of the primary recurring finding this insight supplements.",
    )
    last_charged_date: Optional[datetime.date] = Field(
        default=None,
        description="Date of the most recent charge observed in this series.",
    )

    # Estimated Active Costs (Explicitly projected, separate from historical totals)
    estimated_monthly_cost: Optional[float] = Field(
        default=None,
        description="Estimated monthly recurring cost for active subscriptions (None or 0 for inactive/supplemental)",
    )
    estimated_annual_cost: Optional[float] = Field(
        default=None,
        description="Estimated annualized cost for active subscriptions (None or 0 for inactive/supplemental)",
    )

    # Historical Inactive Costs (for churned/inactive subscriptions)
    historical_monthly_cost: Optional[float] = Field(
        default=None,
        description="Historical monthly cost for inactive/churned subscriptions prior to cancellation",
    )
    historical_annual_cost: Optional[float] = Field(
        default=None,
        description="Historical annualized cost for inactive/churned subscriptions prior to cancellation",
    )

    # Price Increase Details (if applicable)
    previous_amount: Optional[float] = Field(
        default=None,
        description="Historical recurring price prior to step increase",
    )
    new_amount: Optional[float] = Field(
        default=None,
        description="Current recurring price after step increase",
    )
    amount_difference: Optional[float] = Field(
        default=None,
        description="Absolute price hike amount (new_amount - previous_amount)",
    )
    percent_increase: Optional[float] = Field(
        default=None,
        description="Percentage price hike relative to previous amount",
    )

    # Duplicate Candidate Details (if applicable)
    overlapping_plan_name: Optional[str] = Field(
        default=None,
        description="Name of the secondary or overlapping subscription plan",
    )
    potential_hypothetical_monthly_savings: Optional[float] = Field(
        default=None,
        description="Hypothetical monthly savings if user decides to cancel overlapping subscription",
    )

    explanation: str = Field(
        ...,
        description="Detailed plain-language audit explanation",
    )
    caveats: List[str] = Field(
        default_factory=list,
        description="Known limitations, assumptions, and missing-history caveats",
    )


class AuditReport(BaseModel):
    """Aggregated recurring payment audit report.

    Contains all detected findings, estimated recurring expenses, and hypothetical
    savings calculations without asserting fraud or guaranteed savings.
    """

    total_transactions_analyzed: int = Field(..., ge=0, description="Total transactions parsed and reviewed")
    total_merchants_analyzed: int = Field(..., ge=0, description="Unique canonical merchants evaluated")
    findings: List[AuditFinding] = Field(default_factory=list, description="All audit findings generated")
    total_estimated_monthly_recurring: float = Field(
        default=0.0,
        ge=0.0,
        description="Sum of active monthly recurring charges",
    )
    total_estimated_annual_recurring: float = Field(
        default=0.0,
        ge=0.0,
        description="Sum of all annualized recurring expenses (monthly*12 + annual)",
    )
    potential_hypothetical_annual_savings: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Hypothetical annual savings if duplicate or unwanted subscriptions are cancelled. "
            "Non-binding estimate; actual savings depend on user action."
        ),
    )
    currency: str = Field(default="INR", description="Primary currency of the audit")
    summary_notes: List[str] = Field(default_factory=list, description="General audit observations")
