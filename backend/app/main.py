"""FastAPI application for SlothAudit.

Exposes three endpoints:
- GET  /health       — liveness check.
- POST /api/audit    — accept a multipart CSV upload, parse and audit it,
                       return a JSON-serialized AuditReport.
- POST /api/explain  — generate a plain-language explanation for one audit
                       finding; uses the deterministic provider by default.

Design constraints:
- Entirely in-memory: uploaded file bytes are never saved to disk, databases,
  logs, or any persistent store. No PII leaves this process.
- Delegates all parsing to csv_parser.parse_transactions() and all detection
  to audit_engine.audit_transactions(); no audit logic lives here.
- Returns meaningful HTTP error codes without exposing raw transaction data.
- /api/explain is isolated: an unavailable model provider cannot affect
  /api/audit or any other endpoint.
"""

from __future__ import annotations

import json
import os

from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app.audit_engine import audit_transactions
from backend.app.csv_parser import MAX_FILE_SIZE_BYTES, CSVParseError, parse_transactions
from backend.app.explanation_engine import (
    ExplanationResponse,
    generate_explanation,
    get_default_provider,
)
from backend.app.schemas import AuditFinding

# ---------------------------------------------------------------------------
# CORS Configuration
# ---------------------------------------------------------------------------

DEFAULT_ALLOWED_ORIGINS: list[str] = [
    "https://slothaudit-frontend.onrender.com",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
]


def get_allowed_origins() -> list[str]:
    """Return the list of allowed CORS origins.

    Reads ALLOWED_ORIGINS from the environment (comma-separated or JSON list).
    Merges any custom origins with default production and local origins,
    ensuring trailing slashes are trimmed.
    """
    raw = os.environ.get("ALLOWED_ORIGINS", "").strip()
    if not raw:
        return list(DEFAULT_ALLOWED_ORIGINS)

    parsed: list[str] = []
    if raw.startswith("[") and raw.endswith("]"):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                parsed = [str(o).strip().rstrip("/") for o in data if str(o).strip()]
        except Exception:
            pass

    if not parsed:
        parsed = [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]

    origins = list(parsed)
    for dev_origin in DEFAULT_ALLOWED_ORIGINS:
        if dev_origin not in origins:
            origins.append(dev_origin)
    return origins


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="SlothAudit API",
    description=(
        "Local-first recurring payment auditor. "
        "All processing is in-memory; no transaction data is persisted."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Allowed upload extensions
# ---------------------------------------------------------------------------

_ALLOWED_EXTENSIONS = {".csv"}


def _check_extension(filename: str) -> None:
    """Raise 415 if the uploaded filename does not end with an allowed extension."""
    lower = (filename or "").lower()
    if not any(lower.endswith(ext) for ext in _ALLOWED_EXTENSIONS):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Unsupported file type. "
                f"Only CSV uploads are accepted (got {filename!r})."
            ),
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", summary="Health check")
async def health() -> JSONResponse:
    """Return a simple liveness response.

    Always returns HTTP 200 with ``{"status": "healthy"}`` while the server
    is running. Suitable for load-balancer or uptime probes.
    """
    return JSONResponse(content={"status": "healthy"})


@app.post(
    "/api/audit",
    summary="Audit a CSV transaction file",
    response_description="JSON-serialized AuditReport with findings, cost estimates, and summary notes.",
)
async def audit_csv(
    file: UploadFile = File(..., description="CSV transaction export (max 5 MB, max 50 000 rows)."),
) -> JSONResponse:
    """Parse an uploaded CSV file and return a deterministic AuditReport.

    ### Request
    - `Content-Type: multipart/form-data`
    - Form field name: `file`
    - File must be a UTF-8 (or CP-1252 / Latin-1) encoded CSV.

    ### Response
    Returns the full ``AuditReport`` schema as JSON, including:
    - `findings` — list of detected recurring / price-increase / duplicate findings.
    - `total_estimated_monthly_recurring` / `total_estimated_annual_recurring`.
    - `potential_hypothetical_annual_savings`.
    - `summary_notes` — plain-language audit observations.
    - `parse_diagnostics` — row counts and any row-level validation errors
      (structural information only; no personal data is included).

    ### Error codes
    | Code | Reason |
    |------|--------|
    | 400  | Empty upload / no file content |
    | 400  | No usable transactions after parsing |
    | 413  | File exceeds 5 MB limit |
    | 415  | File extension is not `.csv` |
    | 422  | CSV is structurally invalid (missing required columns, encoding failure, etc.) |
    """
    # 1. Validate filename / extension
    filename = file.filename or ""
    _check_extension(filename)

    # 2. Read bytes (impose size guard before parser to give a clean 413)
    raw: bytes = await file.read()

    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    if len(raw) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"File size {len(raw):,} bytes exceeds the maximum allowed "
                f"size of {MAX_FILE_SIZE_BYTES:,} bytes (5 MB)."
            ),
        )

    # 3. Parse CSV — CSVParseError covers file-level failures (bad encoding,
    #    missing required columns, row-limit exceeded, etc.)
    try:
        transactions, parse_report = parse_transactions(raw, filename)
    except CSVParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    # 4. Guard: no usable transactions
    if not transactions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"No valid transactions found in the uploaded file. "
                f"Processed {parse_report.total_rows} row(s); "
                f"{parse_report.invalid_rows} row(s) failed validation."
            ),
        )

    # 5. Run deterministic audit (pure in-memory, no I/O)
    report = audit_transactions(transactions)

    # 6. Serialize to dict; attach parse diagnostics as a top-level sibling
    #    (not embedded inside AuditReport schema to keep the core schema clean)
    response_payload = report.model_dump(mode="json")
    response_payload["parse_diagnostics"] = {
        "total_rows": parse_report.total_rows,
        "valid_rows": parse_report.valid_rows,
        "invalid_rows": parse_report.invalid_rows,
        # Row-level errors contain only structural info (row number + reason),
        # never raw transaction narration or personal data.
        "errors": parse_report.errors,
        "date_range_start": (
            parse_report.date_range_start.isoformat()
            if parse_report.date_range_start
            else None
        ),
        "date_range_end": (
            parse_report.date_range_end.isoformat()
            if parse_report.date_range_end
            else None
        ),
        "currencies_found": parse_report.currencies_found,
    }

    return JSONResponse(content=response_payload)


@app.post(
    "/api/explain",
    summary="Generate plain-language explanation for an audit finding",
    response_description="JSON-serialized ExplanationResponse.",
    response_model=ExplanationResponse,
)
async def explain_finding(
    finding: AuditFinding,
) -> JSONResponse:
    """Generate a plain-language explanation for one audit finding.

    Accepts an existing AuditFinding JSON payload, strips internal and personal data,
    and returns a structured ExplanationResponse with plain explanation,
    recommendation, and caveats.

    Uses hosted Gemma via Hugging Face Inference Providers if HF_TOKEN is configured,
    with automatic fallback to the deterministic provider if unavailable.
    This endpoint is fully isolated from /api/audit.
    """
    provider = get_default_provider()
    explanation = generate_explanation(finding, provider=provider)
    return JSONResponse(content=explanation.model_dump(mode="json"))
