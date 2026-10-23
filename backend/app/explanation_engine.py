"""Explanation generation layer for SlothAudit audit findings.

Architecture
------------
AuditFinding  ──▶  _build_request()  ──▶  ExplanationRequest
                                               │
                                    ┌──────────▼──────────┐
                                    │    ModelProvider     │ (pluggable)
                                    │  ┌────────────────┐  │
                                    │  │ Deterministic  │  │  always available
                                    │  │ OllamaProvider │  │  stub (future)
                                    │  └────────────────┘  │
                                    └──────────┬──────────┘
                                               │ raw dict
                                    _validate_provider_output()
                                               │ on failure → DeterministicProvider
                                               ▼
                                      ExplanationResponse

Design principles
-----------------
- ExplanationRequest contains only structured, aggregated data — no raw
  transaction narrations, no transaction IDs, no per-row personal data.
- The engine's pre-computed `explanation` and `caveats` fields are included in
  ExplanationRequest so both deterministic and future model providers can use
  them as a grounded baseline.
- generate_explanation() never raises: any provider or validation failure falls
  back to DeterministicProvider, which always succeeds.
- Audit calculations, confidence scores, and evidence IDs are never modified.
"""

from __future__ import annotations

import abc
import logging
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from backend.app.schemas import AuditFinding

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Output length limits
# ---------------------------------------------------------------------------

PLAIN_EXPLANATION_MAX_CHARS: int = 500
RECOMMENDATION_MAX_CHARS: int = 220
CAVEAT_MAX_CHARS: int = 280

_SAFETY_CAVEAT = (
    "These findings are analytical and observational only. "
    "Always verify with your bank or merchant before taking action."
)

_RECOMMENDATION_SENTENCES: dict[str, str] = {
    "keep": (
        "No immediate action needed; this appears to be an expected recurring charge."
    ),
    "investigate": (
        "Review your account records and bank statements to confirm this charge's "
        "current status and whether autopay has been fully terminated."
    ),
    "consider_cancelling": (
        "Consider whether this subscription is still needed, especially if it "
        "overlaps with another plan you already hold."
    ),
}


# ---------------------------------------------------------------------------
# Helper: soft truncation at sentence boundary
# ---------------------------------------------------------------------------

def _truncate(text: str, limit: int) -> str:
    """Trim text to `limit` chars, preferring a sentence boundary."""
    if len(text) <= limit:
        return text
    truncated = text[:limit]
    for sep in (".", "!", "?"):
        idx = truncated.rfind(sep)
        if idx > limit // 2:
            return truncated[: idx + 1]
    return truncated.rstrip() + "\u2026"  # ellipsis


# ---------------------------------------------------------------------------
# ExplanationRequest — minimal structured context, safe to pass to a model
# ---------------------------------------------------------------------------

class ExplanationRequest(BaseModel):
    """Minimal structured audit finding context forwarded to an explanation provider.

    Deliberately excludes:
    - ``evidence_txn_ids``: raw transaction IDs, not needed for narration.
    - ``evidence_items``: per-row records containing narration strings.
    - All free-form narration from bank statements.

    Includes the engine's pre-computed ``engine_explanation`` and
    ``engine_caveats`` as a structured grounding baseline that any provider
    (deterministic or model-based) can use or refine.
    """

    finding_id: str = Field(
        ...,
        description="Finding ID — for provenance tracking only; not sent in model prompt body.",
    )
    finding_type: Literal["recurring", "price_increase", "duplicate_candidate"]
    merchant_canonical: str = Field(..., description="Normalized merchant name")
    cadence: Literal["monthly", "annual", "variable_monthly", "irregular", "unknown"]
    is_active: bool
    is_supplemental: bool
    recommended_action: Literal["keep", "investigate", "consider_cancelling"]

    # Structural summary: counts, date ranges, amounts — no per-row narration
    evidence_summary: str = Field(
        ...,
        description=(
            "Pre-aggregated structural summary (e.g. '7 charges at ₹499/mo Jan–Jul 2024'). "
            "Does not contain raw transaction descriptions."
        ),
    )

    # Cost fields (structural, not personal)
    estimated_monthly_cost: Optional[float] = None
    estimated_annual_cost: Optional[float] = None
    historical_monthly_cost: Optional[float] = None
    historical_annual_cost: Optional[float] = None

    # Price-increase fields (structural)
    previous_amount: Optional[float] = None
    new_amount: Optional[float] = None
    percent_increase: Optional[float] = None

    # Pre-computed audit engine text used as grounding baseline
    engine_explanation: str = Field(
        ...,
        description="Existing engine-generated explanation string (used as deterministic baseline).",
    )
    engine_caveats: List[str] = Field(
        default_factory=list,
        description="Existing engine-generated caveats (used as deterministic baseline).",
    )


# ---------------------------------------------------------------------------
# ExplanationResponse — validated output
# ---------------------------------------------------------------------------

class ExplanationResponse(BaseModel):
    """Validated explanation for one audit finding.

    ``generated_by`` records which provider produced this output so the UI can
    indicate whether it is AI-generated or deterministic.
    """

    finding_id: str = Field(
        ..., description="Finding ID this explanation corresponds to."
    )
    plain_explanation: str = Field(
        ...,
        description=f"Plain-language explanation (max {PLAIN_EXPLANATION_MAX_CHARS} chars).",
    )
    recommendation: str = Field(
        ...,
        description=f"One actionable recommendation (max {RECOMMENDATION_MAX_CHARS} chars).",
    )
    caveat: str = Field(
        ...,
        description=f"One uncertainty or limitation statement (max {CAVEAT_MAX_CHARS} chars).",
    )
    generated_by: Literal["deterministic", "gemma3:1b", "gemma3:4b"] = Field(
        ...,
        description="Provenance: which provider generated this explanation.",
    )

    @field_validator("plain_explanation")
    @classmethod
    def check_plain_explanation(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("plain_explanation cannot be empty")
        if len(v) > PLAIN_EXPLANATION_MAX_CHARS:
            raise ValueError(
                f"plain_explanation exceeds {PLAIN_EXPLANATION_MAX_CHARS} chars (got {len(v)})"
            )
        return v

    @field_validator("recommendation")
    @classmethod
    def check_recommendation(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("recommendation cannot be empty")
        if len(v) > RECOMMENDATION_MAX_CHARS:
            raise ValueError(
                f"recommendation exceeds {RECOMMENDATION_MAX_CHARS} chars (got {len(v)})"
            )
        return v

    @field_validator("caveat")
    @classmethod
    def check_caveat(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("caveat cannot be empty")
        if len(v) > CAVEAT_MAX_CHARS:
            raise ValueError(
                f"caveat exceeds {CAVEAT_MAX_CHARS} chars (got {len(v)})"
            )
        return v


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------

class ModelProvider(abc.ABC):
    """Abstract base for explanation providers.

    Providers receive an ExplanationRequest and return a raw dict whose keys
    must match ExplanationResponse fields. Validation and fallback are handled
    by generate_explanation(); providers should raise on infrastructure failures
    (connection refused, timeout) rather than returning malformed output.

    Providers MUST NOT:
    - Re-derive totals, confidence scores, or evidence IDs.
    - Access the filesystem, network, or external APIs other than their own
      local inference runtime.
    - Raise on schema mismatches — that is the caller's responsibility.
    """

    @abc.abstractmethod
    def generate(self, request: ExplanationRequest) -> dict:
        """Return a raw dict conforming to ExplanationResponse fields.

        Raise any exception on infrastructure failure; generate_explanation()
        will catch it and invoke the deterministic fallback.
        """
        ...

    @property
    @abc.abstractmethod
    def provider_name(self) -> str:
        """Short provider identifier (must be a valid ExplanationResponse.generated_by value)."""
        ...


# ---------------------------------------------------------------------------
# DeterministicProvider — always-available fallback
# ---------------------------------------------------------------------------

class DeterministicProvider(ModelProvider):
    """Uses the audit engine's pre-computed explanation and caveats.

    This provider never fails and requires no external dependencies. It is the
    mandatory fallback when any model provider raises or returns invalid output.
    """

    @property
    def provider_name(self) -> str:
        return "deterministic"

    def generate(self, request: ExplanationRequest) -> dict:
        plain = _truncate(request.engine_explanation, PLAIN_EXPLANATION_MAX_CHARS)
        if not plain.strip():
            plain = f"A recurring payment pattern was detected for {request.merchant_canonical}."
        recommendation = _RECOMMENDATION_SENTENCES.get(
            request.recommended_action,
            _RECOMMENDATION_SENTENCES["investigate"],
        )
        if request.engine_caveats and request.engine_caveats[0].strip():
            caveat = _truncate(request.engine_caveats[0], CAVEAT_MAX_CHARS)
        else:
            caveat = _SAFETY_CAVEAT
        return {
            "finding_id": request.finding_id,
            "plain_explanation": plain,
            "recommendation": recommendation,
            "caveat": caveat,
            "generated_by": "deterministic",
        }


# ---------------------------------------------------------------------------
# OllamaProvider — integration boundary stub (not yet active)
# ---------------------------------------------------------------------------

class OllamaProvider(ModelProvider):
    """Future provider for gemma3:1b (or gemma3:4b) via the Ollama runtime.

    Currently raises ``NotImplementedError`` — the integration boundary is
    defined but no model is downloaded or invoked. To activate this provider:

    1. ``pip install ollama``  (add ``ollama>=0.5.0`` to requirements.txt)
    2. ``ollama pull gemma3:1b``  (~800 MB, one-time download)
    3. Implement ``generate()`` using the Ollama Python SDK.
    4. Wire in via ``generate_explanation(finding, provider=OllamaProvider())``.

    The provider must never raise on schema-mismatch output; raise only on
    connection errors or timeouts so the fallback chain activates correctly.
    """

    def __init__(self, model: str = "gemma3:1b", timeout: float = 15.0) -> None:
        self._model = model
        self._timeout = timeout

    @property
    def provider_name(self) -> str:
        # Must be a value accepted by ExplanationResponse.generated_by
        if "4b" in self._model:
            return "gemma3:4b"
        return "gemma3:1b"

    def generate(self, request: ExplanationRequest) -> dict:
        """Not yet implemented — raises NotImplementedError.

        Implementation guide (when ready):

            import ollama  # requires: pip install ollama
            system_prompt = _build_system_prompt()
            user_prompt = _build_user_prompt(request)
            response = ollama.chat(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                options={"num_ctx": 2048, "temperature": 0.3},
            )
            # Parse response["message"]["content"] as JSON and return dict.
            # Raise on connection error / timeout; let generate_explanation
            # handle validation failures via fallback.
        """
        raise NotImplementedError(
            f"OllamaProvider.generate() is not yet implemented. "
            f"Install the ollama package and pull {self._model!r} first."
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_DETERMINISTIC = DeterministicProvider()


def _build_request(finding: AuditFinding) -> ExplanationRequest:
    """Extract a minimal ExplanationRequest from a full AuditFinding.

    Deliberately excludes:
    - evidence_txn_ids
    - evidence_items (contains raw description strings)
    - confidence_rationale (engine-internal metric)
    - All per-row transaction narrations
    """
    return ExplanationRequest(
        finding_id=finding.finding_id,
        finding_type=finding.finding_type,
        merchant_canonical=finding.merchant_canonical,
        cadence=finding.cadence,
        is_active=finding.is_active,
        is_supplemental=finding.is_supplemental,
        recommended_action=finding.recommended_action,
        evidence_summary=finding.evidence_summary,
        estimated_monthly_cost=finding.estimated_monthly_cost,
        estimated_annual_cost=finding.estimated_annual_cost,
        historical_monthly_cost=finding.historical_monthly_cost,
        historical_annual_cost=finding.historical_annual_cost,
        previous_amount=finding.previous_amount,
        new_amount=finding.new_amount,
        percent_increase=finding.percent_increase,
        engine_explanation=finding.explanation,
        engine_caveats=finding.caveats,
    )


def _validate_provider_output(
    finding_id: str,
    raw: dict,
    default_generated_by: str = "deterministic",
) -> ExplanationResponse:
    """Parse and validate a raw provider dict into ExplanationResponse.

    Raises ``ValueError`` or ``pydantic.ValidationError`` on any schema problem.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"Provider output must be a dict, got {type(raw).__name__}")
    payload = dict(raw)
    if not payload.get("finding_id"):
        payload["finding_id"] = finding_id
    if not payload.get("generated_by"):
        payload["generated_by"] = default_generated_by
    return ExplanationResponse.model_validate(payload)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_explanation(
    finding: AuditFinding,
    provider: Optional[ModelProvider] = None,
) -> ExplanationResponse:
    """Generate a plain-language explanation for one audit finding.

    Args:
        finding: The full ``AuditFinding`` from ``audit_transactions()``.
        provider: Optional model provider. If ``None`` or if the provider raises
                  or returns invalid output, the ``DeterministicProvider``
                  is used as fallback. The fallback always succeeds.

    Returns:
        ``ExplanationResponse`` with ``generated_by`` indicating the provider used.

    Notes:
        - This function never raises. All exceptions are caught and logged.
        - Audit calculations, findings, and evidence IDs are never modified.
        - ``AuditFinding`` data that could be personal (evidence_items,
          evidence_txn_ids) is stripped before reaching any provider.
    """
    request = _build_request(finding)

    if provider is not None:
        try:
            raw = provider.generate(request)
            return _validate_provider_output(
                request.finding_id,
                raw,
                default_generated_by=getattr(provider, "provider_name", "deterministic"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Provider %r failed for finding %r (%s: %s); falling back to deterministic.",
                getattr(provider, "provider_name", "unknown"),
                finding.finding_id,
                type(exc).__name__,
                exc,
            )

    # Deterministic fallback — guaranteed to succeed
    try:
        raw = _DETERMINISTIC.generate(request)
        return _validate_provider_output(request.finding_id, raw, default_generated_by="deterministic")
    except Exception as exc:  # noqa: BLE001
        # Absolute last resort — should never happen
        logger.error(
            "DeterministicProvider failed for finding %r: %s. Using hardcoded response.",
            finding.finding_id,
            exc,
        )
        return ExplanationResponse(
            finding_id=finding.finding_id,
            plain_explanation="A recurring payment pattern was detected for this merchant.",
            recommendation=_RECOMMENDATION_SENTENCES["investigate"],
            caveat=_SAFETY_CAVEAT,
            generated_by="deterministic",
        )
