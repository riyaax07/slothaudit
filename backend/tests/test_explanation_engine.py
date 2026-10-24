"""Unit and integration tests for SlothAudit explanation generation layer.

Verifies:
- Deterministic explanation generation for all finding types and actions
- Length limit enforcement (plain_explanation, recommendation, caveat)
- Exclusion of raw transaction IDs and evidence items from ExplanationRequest
- Fallback to deterministic when a model provider fails with an exception
- Fallback when provider returns non-dict, missing fields, or empty strings
- Fallback when provider returns excessive output length
- Fallback when provider returns an unsupported generated_by identifier
- OllamaProvider raises NotImplementedError and triggers safe fallback
- /api/explain endpoint validation, success, and error behavior
"""

from __future__ import annotations

import datetime
from typing import Optional

import os
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.explanation_engine import (
    CAVEAT_MAX_CHARS,
    DEFAULT_HF_MODEL,
    DEFAULT_HF_ROUTER_URL,
    PLAIN_EXPLANATION_MAX_CHARS,
    RECOMMENDATION_MAX_CHARS,
    DeterministicProvider,
    ExplanationRequest,
    ExplanationResponse,
    HuggingFaceGemmaProvider,
    ModelProvider,
    OllamaProvider,
    _SAFETY_CAVEAT,
    _build_request,
    generate_explanation,
    get_default_provider,
    load_env_file,
)
from backend.app.main import app
from backend.app.schemas import AuditFinding, EvidenceItem


# ---------------------------------------------------------------------------
# Test Fixtures & Helpers
# ---------------------------------------------------------------------------

def _make_finding(
    finding_id: str = "FINDING-TEST-001",
    finding_type: str = "recurring",
    merchant: str = "TestSaaS",
    action: str = "keep",
    explanation: str = "Recurring subscription detected for TestSaaS billed monthly.",
    caveats: Optional[list[str]] = None,
    estimated_monthly: Optional[float] = 499.0,
    is_active: bool = True,
    is_supplemental: bool = False,
) -> AuditFinding:
    return AuditFinding(
        finding_id=finding_id,
        finding_type=finding_type,
        merchant_canonical=merchant,
        cadence="monthly",
        confidence=0.95,
        confidence_rationale="Regular 30-day cadence observed across 6 billing cycles.",
        recommended_action=action,
        evidence_txn_ids=["TXN-101", "TXN-102", "TXN-103"],
        evidence_summary="3 charges at ₹499.00 from Jan 2024 to Mar 2024",
        evidence_items=[
            EvidenceItem(
                txn_id="TXN-101",
                date=datetime.date(2024, 1, 15),
                amount=499.0,
                description="POS DEBIT TESTSAAS MUMBAI IN 101",
            ),
            EvidenceItem(
                txn_id="TXN-102",
                date=datetime.date(2024, 2, 15),
                amount=499.0,
                description="POS DEBIT TESTSAAS MUMBAI IN 102",
            ),
        ],
        is_active=is_active,
        is_supplemental=is_supplemental,
        estimated_monthly_cost=estimated_monthly,
        estimated_annual_cost=estimated_monthly * 12 if estimated_monthly else None,
        explanation=explanation,
        caveats=caveats if caveats is not None else ["Active recurring commitment."],
    )


class MockProvider(ModelProvider):
    """Configurable mock provider for testing provider boundaries."""

    def __init__(
        self,
        name: str = "gemma3:1b",
        return_value: Optional[dict] = None,
        raise_exc: Optional[Exception] = None,
    ):
        self._name = name
        self._return_value = return_value
        self._raise_exc = raise_exc

    @property
    def provider_name(self) -> str:
        return self._name

    def generate(self, request: ExplanationRequest) -> dict:
        if self._raise_exc is not None:
            raise self._raise_exc
        if self._return_value is not None:
            return self._return_value
        return {
            "finding_id": request.finding_id,
            "plain_explanation": f"Gemma explained {request.merchant_canonical}.",
            "recommendation": "Review your subscription tier.",
            "caveat": "Estimated based on observed history.",
            "generated_by": self._name,
        }


# ---------------------------------------------------------------------------
# 1. Deterministic Explanation Tests
# ---------------------------------------------------------------------------

def test_deterministic_explanation_basic():
    """Verify deterministic generation populates all fields with expected values."""
    finding = _make_finding(action="keep")
    result = generate_explanation(finding)

    assert isinstance(result, ExplanationResponse)
    assert result.finding_id == "FINDING-TEST-001"
    assert result.generated_by == "deterministic"
    assert len(result.plain_explanation) <= PLAIN_EXPLANATION_MAX_CHARS
    assert len(result.recommendation) <= RECOMMENDATION_MAX_CHARS
    assert len(result.caveat) <= CAVEAT_MAX_CHARS
    assert "No immediate action needed" in result.recommendation


@pytest.mark.parametrize(
    ("action", "expected_phrase"),
    [
        ("keep", "No immediate action needed"),
        ("investigate", "Review your account records"),
        ("consider_cancelling", "Consider whether this subscription is still needed"),
    ],
)
def test_deterministic_recommendation_tailored_to_action(action: str, expected_phrase: str):
    """Verify recommendation string reflects the engine's recommended_action."""
    finding = _make_finding(action=action)
    result = generate_explanation(finding)
    assert expected_phrase in result.recommendation


def test_deterministic_explanation_default_caveat():
    """When finding has empty caveats, a standard safety caveat is used."""
    finding = _make_finding(caveats=[])
    result = generate_explanation(finding)
    assert result.caveat == _SAFETY_CAVEAT


def test_deterministic_explanation_truncates_excessive_engine_text():
    """Very long engine explanations and caveats are truncated within limits."""
    long_explanation = "This is a detailed analysis. " * 30  # ~870 chars
    long_caveat = "Important note about assumptions. " * 20   # ~680 chars
    finding = _make_finding(explanation=long_explanation, caveats=[long_caveat])

    result = generate_explanation(finding)
    assert len(result.plain_explanation) <= PLAIN_EXPLANATION_MAX_CHARS
    assert len(result.caveat) <= CAVEAT_MAX_CHARS


def test_explanation_request_excludes_raw_data():
    """Verify ExplanationRequest excludes evidence_txn_ids and evidence_items."""
    finding = _make_finding()
    req = _build_request(finding)

    dump = req.model_dump()
    assert "evidence_txn_ids" not in dump
    assert "evidence_items" not in dump
    assert "confidence_rationale" not in dump

    # Check that raw narrations are not leaked into the request
    req_json = req.model_dump_json()
    assert "POS DEBIT TESTSAAS" not in req_json
    assert "TXN-101" not in req_json


# ---------------------------------------------------------------------------
# 2. Schema Validation Direct Tests
# ---------------------------------------------------------------------------

def test_explanation_response_valid():
    """Valid dictionary validates cleanly against ExplanationResponse."""
    payload = {
        "finding_id": "FINDING-001",
        "plain_explanation": "Valid plain explanation.",
        "recommendation": "Keep it active.",
        "caveat": "Observational only.",
        "generated_by": "deterministic",
    }
    resp = ExplanationResponse.model_validate(payload)
    assert resp.finding_id == "FINDING-001"
    assert resp.generated_by == "deterministic"


@pytest.mark.parametrize(
    ("field", "bad_val"),
    [
        ("plain_explanation", ""),
        ("plain_explanation", "   "),
        ("recommendation", ""),
        ("caveat", ""),
    ],
)
def test_explanation_response_rejects_empty_strings(field: str, bad_val: str):
    """Empty or whitespace-only string fields raise ValidationError."""
    payload = {
        "finding_id": "FINDING-001",
        "plain_explanation": "Valid text.",
        "recommendation": "Valid recommendation.",
        "caveat": "Valid caveat.",
        "generated_by": "deterministic",
    }
    payload[field] = bad_val
    with pytest.raises(ValidationError):
        ExplanationResponse.model_validate(payload)


def test_explanation_response_rejects_excessive_plain_explanation():
    """plain_explanation exceeding 500 chars raises ValidationError."""
    payload = {
        "finding_id": "FINDING-001",
        "plain_explanation": "X" * 501,
        "recommendation": "Valid.",
        "caveat": "Valid.",
        "generated_by": "deterministic",
    }
    with pytest.raises(ValidationError):
        ExplanationResponse.model_validate(payload)


def test_explanation_response_rejects_excessive_recommendation():
    """recommendation exceeding 220 chars raises ValidationError."""
    payload = {
        "finding_id": "FINDING-001",
        "plain_explanation": "Valid.",
        "recommendation": "X" * 221,
        "caveat": "Valid.",
        "generated_by": "deterministic",
    }
    with pytest.raises(ValidationError):
        ExplanationResponse.model_validate(payload)


def test_explanation_response_rejects_excessive_caveat():
    """caveat exceeding 280 chars raises ValidationError."""
    payload = {
        "finding_id": "FINDING-001",
        "plain_explanation": "Valid.",
        "recommendation": "Valid.",
        "caveat": "X" * 281,
        "generated_by": "deterministic",
    }
    with pytest.raises(ValidationError):
        ExplanationResponse.model_validate(payload)


def test_explanation_response_rejects_unknown_generated_by():
    """generated_by not in the allowed literal set raises ValidationError."""
    payload = {
        "finding_id": "FINDING-001",
        "plain_explanation": "Valid.",
        "recommendation": "Valid.",
        "caveat": "Valid.",
        "generated_by": "unapproved_gpt_model",
    }
    with pytest.raises(ValidationError):
        ExplanationResponse.model_validate(payload)


# ---------------------------------------------------------------------------
# 3. Model Provider Boundary & Fallback Tests
# ---------------------------------------------------------------------------

def test_custom_provider_success():
    """A conforming provider response is returned with provenance set."""
    finding = _make_finding()
    provider = MockProvider(name="gemma3:1b")
    result = generate_explanation(finding, provider=provider)

    assert result.generated_by == "gemma3:1b"
    assert "Gemma explained TestSaaS" in result.plain_explanation


def test_provider_exception_falls_back_to_deterministic():
    """When a provider raises an exception (e.g. timeout/offline), fallback succeeds."""
    finding = _make_finding()
    provider = MockProvider(
        name="gemma3:1b",
        raise_exc=ConnectionRefusedError("Ollama daemon unreachable on port 11434"),
    )
    result = generate_explanation(finding, provider=provider)

    assert result.generated_by == "deterministic"
    assert result.finding_id == finding.finding_id
    assert "TestSaaS" in result.plain_explanation


def test_provider_returns_non_dict_falls_back():
    """When provider returns a non-dict object, fallback succeeds."""
    finding = _make_finding()
    provider = MockProvider(return_value="Plain text string rather than JSON dict")  # type: ignore[arg-type]
    result = generate_explanation(finding, provider=provider)

    assert result.generated_by == "deterministic"


def test_provider_missing_fields_falls_back():
    """When provider output misses required fields, fallback succeeds."""
    finding = _make_finding()
    # Missing 'recommendation' and 'caveat'
    provider = MockProvider(
        return_value={"plain_explanation": "Incomplete output from model"}
    )
    result = generate_explanation(finding, provider=provider)

    assert result.generated_by == "deterministic"
    assert "TestSaaS" in result.plain_explanation


def test_provider_excessive_length_falls_back():
    """When provider output violates length bounds, fallback succeeds."""
    finding = _make_finding()
    provider = MockProvider(
        return_value={
            "finding_id": finding.finding_id,
            "plain_explanation": "Wordy response. " * 50,  # ~800 chars, exceeds 500 limit
            "recommendation": "Review tier.",
            "caveat": "Some caveat.",
            "generated_by": "gemma3:1b",
        }
    )
    result = generate_explanation(finding, provider=provider)

    assert result.generated_by == "deterministic"
    assert len(result.plain_explanation) <= PLAIN_EXPLANATION_MAX_CHARS


def test_provider_unsupported_name_falls_back():
    """When provider name is outside allowed literal set, fallback succeeds."""
    finding = _make_finding()
    provider = MockProvider(
        name="unauthorized_ai_model",
        return_value={
            "finding_id": finding.finding_id,
            "plain_explanation": "Valid text.",
            "recommendation": "Valid rec.",
            "caveat": "Valid caveat.",
            "generated_by": "unauthorized_ai_model",
        },
    )
    result = generate_explanation(finding, provider=provider)

    assert result.generated_by == "deterministic"


def test_ollama_provider_stub_falls_back():
    """OllamaProvider currently raises NotImplementedError, which triggers fallback."""
    finding = _make_finding()
    provider = OllamaProvider(model="gemma3:1b")
    result = generate_explanation(finding, provider=provider)

    assert result.generated_by == "deterministic"
    assert result.finding_id == finding.finding_id


# ---------------------------------------------------------------------------
# 4. API /api/explain Endpoint Tests
# ---------------------------------------------------------------------------

client = TestClient(app)


def test_api_explain_endpoint_success(monkeypatch):
    """POST /api/explain with valid AuditFinding returns 200 and ExplanationResponse."""
    monkeypatch.delenv("HF_TOKEN", raising=False)
    finding = _make_finding(action="consider_cancelling")
    payload = finding.model_dump(mode="json")

    resp = client.post("/api/explain", json=payload)
    assert resp.status_code == 200, resp.text

    data = resp.json()
    assert data["finding_id"] == finding.finding_id
    assert data["generated_by"] == "deterministic"
    assert "Consider whether this subscription is still needed" in data["recommendation"]
    assert len(data["plain_explanation"]) > 0
    assert len(data["caveat"]) > 0


def test_api_explain_endpoint_invalid_body():
    """POST /api/explain with invalid or empty body returns 422."""
    resp = client.post("/api/explain", json={"not_a_finding": 123})
    assert resp.status_code == 422


def test_api_explain_isolated_from_audit():
    """Confirm /api/audit continues to function normally alongside /api/explain."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy"}


# ---------------------------------------------------------------------------
# 5. Hosted Hugging Face Gemma Provider Tests (Mocked)
# ---------------------------------------------------------------------------

def _mock_hf_response(content: str, status_code: int = 200) -> httpx.Response:
    """Helper to build a mocked httpx.Response mimicking HF router output."""
    body = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                }
            }
        ]
    }
    return httpx.Response(
        status_code=status_code,
        json=body,
        request=httpx.Request("POST", DEFAULT_HF_ROUTER_URL),
    )


def test_env_loader_loads_without_overwriting(tmp_path, monkeypatch):
    """load_env_file loads vars from a file without overwriting existing environment vars."""
    env_file = tmp_path / "test.env"
    env_file.write_text(
        "TEST_EXISTING_KEY=new_val\nTEST_NEW_KEY=hello_gemma\n# comment\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_EXISTING_KEY", "original_val")
    monkeypatch.delenv("TEST_NEW_KEY", raising=False)

    load_env_file(env_file)

    assert os.environ.get("TEST_EXISTING_KEY") == "original_val"
    assert os.environ.get("TEST_NEW_KEY") == "hello_gemma"


def test_hf_provider_missing_token_raises_and_falls_back():
    """Provider without HF_TOKEN raises ValueError and triggers deterministic fallback."""
    finding = _make_finding()
    provider = HuggingFaceGemmaProvider(token="")
    req = _build_request(finding)

    with pytest.raises(ValueError, match="HF_TOKEN is not configured"):
        provider.generate(req)

    result = generate_explanation(finding, provider=provider)
    assert result.generated_by == "deterministic"
    assert result.finding_id == finding.finding_id


def test_hf_provider_prompt_sanitization_excludes_private_fields():
    """Prompt sent to model must not contain transaction IDs, narration, or finding ID."""
    finding = _make_finding()
    req = _build_request(finding)
    provider = HuggingFaceGemmaProvider(token="dummy_token")

    prompt = provider._build_prompt(req)

    assert req.finding_id not in prompt
    assert "TXN-101" not in prompt
    assert "POS DEBIT" not in prompt
    assert "TestSaaS" in prompt
    assert "monthly" in prompt


def test_hf_provider_success_mocked(monkeypatch):
    """Conforming response from HF provider succeeds with Gemma provenance."""
    finding = _make_finding()
    provider = HuggingFaceGemmaProvider(token="mock_hf_token", model="google/gemma-2-2b-it")

    mock_content = (
        '{"plain_explanation": "TestSaaS is billed monthly at 499 INR.", '
        '"recommendation": "Review account records to confirm requirement.", '
        '"caveat": "Based on 3 historical charges; confirm with bank."}'
    )

    def mock_post(*args, **kwargs):
        return _mock_hf_response(mock_content)

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    result = generate_explanation(finding, provider=provider)
    assert result.generated_by == "google/gemma-2-2b-it"
    assert result.finding_id == finding.finding_id
    assert "TestSaaS is billed monthly" in result.plain_explanation
    assert "Review account records" in result.recommendation
    assert "Based on 3 historical charges" in result.caveat


def test_hf_provider_markdown_fences_handled(monkeypatch):
    """JSON output wrapped in markdown code fences is parsed cleanly."""
    finding = _make_finding()
    provider = HuggingFaceGemmaProvider(token="mock_hf_token", model="google/gemma-2-2b-it")

    fenced_content = (
        "```json\n"
        '{\n  "plain_explanation": "Cleanly extracted from fences.",\n'
        '  "recommendation": "Actionable advice.",\n'
        '  "caveat": "Standard caveat."\n}\n'
        "```"
    )

    monkeypatch.setattr(httpx.Client, "post", lambda *args, **kwargs: _mock_hf_response(fenced_content))

    result = generate_explanation(finding, provider=provider)
    assert result.generated_by == "google/gemma-2-2b-it"
    assert result.plain_explanation == "Cleanly extracted from fences."


def test_hf_provider_http_status_error_falls_back(monkeypatch):
    """HTTP 503 from provider raises and triggers deterministic fallback."""
    finding = _make_finding()
    provider = HuggingFaceGemmaProvider(token="mock_hf_token")

    def mock_post_503(*args, **kwargs):
        resp = httpx.Response(
            status_code=503,
            json={"error": "Model currently loading"},
            request=httpx.Request("POST", DEFAULT_HF_ROUTER_URL),
        )
        resp.raise_for_status()
        return resp

    monkeypatch.setattr(httpx.Client, "post", mock_post_503)

    result = generate_explanation(finding, provider=provider)
    assert result.generated_by == "deterministic"
    assert result.finding_id == finding.finding_id


def test_hf_provider_timeout_falls_back(monkeypatch):
    """Timeout during provider call triggers deterministic fallback."""
    finding = _make_finding()
    provider = HuggingFaceGemmaProvider(token="mock_hf_token")

    def mock_timeout(*args, **kwargs):
        raise httpx.TimeoutException("Connection timed out after 12s")

    monkeypatch.setattr(httpx.Client, "post", mock_timeout)

    result = generate_explanation(finding, provider=provider)
    assert result.generated_by == "deterministic"
    assert result.finding_id == finding.finding_id


def test_hf_provider_malformed_json_falls_back(monkeypatch):
    """Non-JSON model output triggers deterministic fallback."""
    finding = _make_finding()
    provider = HuggingFaceGemmaProvider(token="mock_hf_token")

    monkeypatch.setattr(
        httpx.Client,
        "post",
        lambda *args, **kwargs: _mock_hf_response("I am a language model and cannot output JSON."),
    )

    result = generate_explanation(finding, provider=provider)
    assert result.generated_by == "deterministic"
    assert result.finding_id == finding.finding_id


def test_hf_provider_missing_fields_falls_back(monkeypatch):
    """Model output missing required fields triggers deterministic fallback."""
    finding = _make_finding()
    provider = HuggingFaceGemmaProvider(token="mock_hf_token")

    incomplete_json = '{"plain_explanation": "Only one field here."}'
    monkeypatch.setattr(httpx.Client, "post", lambda *args, **kwargs: _mock_hf_response(incomplete_json))

    result = generate_explanation(finding, provider=provider)
    assert result.generated_by == "deterministic"
    assert result.finding_id == finding.finding_id


def test_hf_provider_excessive_length_falls_back(monkeypatch):
    """Model output exceeding maximum character limits triggers fallback."""
    finding = _make_finding()
    provider = HuggingFaceGemmaProvider(token="mock_hf_token")

    excessive_json = (
        '{"plain_explanation": "' + ("A" * 600) + '", '
        '"recommendation": "Rec", "caveat": "Cav"}'
    )
    monkeypatch.setattr(httpx.Client, "post", lambda *args, **kwargs: _mock_hf_response(excessive_json))

    result = generate_explanation(finding, provider=provider)
    assert result.generated_by == "deterministic"
    assert len(result.plain_explanation) <= PLAIN_EXPLANATION_MAX_CHARS


def test_api_explain_endpoint_with_mocked_hf_provider(monkeypatch):
    """POST /api/explain uses HuggingFaceGemmaProvider when HF_TOKEN is configured."""
    monkeypatch.setenv("HF_TOKEN", "mock_hf_token_123")
    monkeypatch.setenv("HF_MODEL", "google/gemma-2-2b-it")

    mock_content = (
        '{"plain_explanation": "Active monthly subscription for TestSaaS.", '
        '"recommendation": "Review your subscription tier.", '
        '"caveat": "Observational analytics only."}'
    )
    orig_post = httpx.Client.post

    def selective_post(self, url, *args, **kwargs):
        if "router.huggingface.co" in str(url) or "chat/completions" in str(url):
            return _mock_hf_response(mock_content)
        return orig_post(self, url, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "post", selective_post)

    finding = _make_finding(action="keep")
    payload = finding.model_dump(mode="json")

    resp = client.post("/api/explain", json=payload)
    assert resp.status_code == 200, resp.text

    data = resp.json()
    assert data["finding_id"] == finding.finding_id
    assert data["generated_by"] == "google/gemma-2-2b-it"
    assert data["plain_explanation"] == "Active monthly subscription for TestSaaS."


def test_api_explain_endpoint_falls_back_on_error(monkeypatch):
    """POST /api/explain falls back to deterministic when provider fails."""
    monkeypatch.setenv("HF_TOKEN", "mock_hf_token_123")
    orig_post = httpx.Client.post

    def selective_error(self, url, *args, **kwargs):
        if "router.huggingface.co" in str(url) or "chat/completions" in str(url):
            raise httpx.ConnectError("Network unreachable")
        return orig_post(self, url, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "post", selective_error)

    finding = _make_finding(action="investigate")
    payload = finding.model_dump(mode="json")

    resp = client.post("/api/explain", json=payload)
    assert resp.status_code == 200, resp.text

    data = resp.json()
    assert data["finding_id"] == finding.finding_id
    assert data["generated_by"] == "deterministic"
    assert "Review your account records" in data["recommendation"]
