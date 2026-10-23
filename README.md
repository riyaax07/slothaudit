# SlothAudit 🦥

> **Local-first recurring-payment auditor**
> *Built for Hacktoberfest 2026 Week 3*

SlothAudit is a privacy-first, local-first web application that audits bank and credit card statement CSVs to detect recurring payments, stealth price increases, duplicate overlapping subscriptions, and inactive "zombie" accounts—entirely in memory on your machine.

---

## 🔒 Privacy & Architecture

Financial statements contain sensitive personal data. SlothAudit is engineered with strict local boundaries:
- **100% In-Memory Processing**: Uploaded statement files are parsed, normalized, audited, and discarded entirely in process memory. File contents are never written to disk, databases, or application logs.
- **Zero Cloud Exfiltration**: Core auditing, merchant normalization, cadence detection, and cost projections require zero external network calls or cloud APIs.
- **Local-Only Binding**: The backend is intended for local single-user execution (`127.0.0.1`). It has no authentication or access control and is **not designed for public network exposure**.
- **No Direct Bank Integration**: SlothAudit works solely on user-exported CSV files. It never requests bank logins, passwords, account tokens, or scraping credentials.
- **Non-Binding Analytical Estimates**: SlothAudit makes no accusations of fraud and cannot automatically modify, cancel, or dispute transactions on your behalf. All calculated savings are hypothetical.

---

## ✨ Implemented Features (Phase 1 MVP)

1. **In-Memory CSV Ingestion & Validation**:
   - Robust stream parser supporting common bank export aliases (`Date`, `Posted Date`, `Narration`, `Description`, `Amount`, `Debit`, `Credit`).
   - Handles multi-format dates, debit/credit polarity, parentheses negative numbers (`(100.00)`), and auto-detects CSV delimiters (`,` `;` `\t` `|`).
   - Enforces privacy: row-level error reports highlight line numbers and structural causes without echoing raw transaction descriptions.
   - Enforces safe limits: max 5 MB file size and max 50,000 transaction rows.

2. **Deterministic Merchant Normalization**:
   - Cleans payment gateway noise, POS codes, location suffixes, and transaction artifacts (e.g., `STREAMFLIX MUMBAI IN` → `StreamFlix`).
   - Regex-based canonical registry covering digital services, utilities, streaming platforms, and SaaS providers.

3. **Deterministic Audit Engine**:
   - **Monthly Subscriptions**: Identifies recurring charges with regular ~30-day cadence.
   - **Annual Renewals**: Detects yearly subscriptions spaced ~365 days apart.
   - **Variable Utility Bills**: Flags recurring utility/metered payments with fluctuating consumption amounts.
   - **Price Increase Alerts**: Identifies established monthly baselines that stepped up in price (e.g., ₹499 → ₹699, +40%), linking them supplementally to the primary subscription to prevent double-counting.
   - **Duplicate / Overlapping Plans**: Detects concurrent active subscriptions from the same merchant (e.g., Basic + Premium tiers billed in the same month).
   - **Inactive Subscriptions**: Identifies subscriptions with historical charges that have lapsed (>65 days since last charge), attributing ₹0.00 active cost and recommending status verification.

4. **FastAPI Backend Endpoints**:
   - `GET /health` — Simple health check and liveness probe.
   - `POST /api/audit` — Multipart CSV ingestion returning structured `AuditReport` with findings, active monthly/annual commitments, and parse diagnostics.
   - `POST /api/explain` — Isolated explanation endpoint for individual findings.

5. **Explanation Provider Boundary & Fallback**:
   - Dedicated provider abstraction (`ModelProvider`) decoupling explanation generation from the audit engine.
   - **Current Implementation**: Powered by `DeterministicProvider`, utilizing pre-computed engine summaries, action guidance, and safety caveats.
   - Provenance is explicitly tracked on each finding (`generated_by="deterministic"`).
   - *Status of AI Inference*: Local LLM inference (e.g. Gemma 3 1B via Ollama) and tabular ML (TabPFN) are **planned extensions**, currently represented by a tested stub (`OllamaProvider`) that safely falls back to deterministic text. No AI models are downloaded, and no external AI APIs are called.

6. **Interactive React + Vite Dashboard**:
   - Real-time drag-and-drop CSV upload with size and type validation.
   - Live summary metrics: Active Monthly Commitment, Active Annual Commitment, and Hypothetical Annual Savings.
   - Filter tabs: All Findings, Active Subscriptions, Price Hikes, Duplicates, and Inactive Plans.
   - Search filter by merchant name.
   - Collapsible transaction evidence tags (`txn_id`) and limitation caveats.
   - "Deep Dive Explanation" action per finding with visible provenance badge.
   - Instant state reset upon page refresh.

---

## 📁 Repository Structure

```text
slothaudit/
├── backend/
│   ├── app/
│   │   ├── audit_engine.py          # Deterministic detection algorithms & thresholds
│   │   ├── csv_parser.py            # In-memory CSV ingestion, alias resolution & guards
│   │   ├── explanation_engine.py    # Provider boundary & deterministic explanation fallback
│   │   ├── main.py                  # FastAPI application (/health, /api/audit, /api/explain)
│   │   ├── merchant_normalizer.py   # Canonical merchant cleaning & rules
│   │   ├── schemas.py               # Pydantic models for transactions, findings & reports
│   │   └── synthetic_data.py        # Seeded synthetic generator for reproducible testing
│   ├── tests/
│   │   ├── test_api.py              # Endpoint tests (8 test suites, 16 tests)
│   │   ├── test_audit_engine.py     # Recurrence, price-hike & duplicate detection tests
│   │   ├── test_csv_parser.py       # Ingestion edge cases & privacy validation tests
│   │   ├── test_explanation_engine.py # Explanation schema, boundary & fallback tests
│   │   ├── test_merchant_normalizer.py # Merchant canonical mapping tests
│   │   └── test_schemas_and_data.py # Schema & synthetic generator validation tests
│   └── requirements.txt             # Pinned backend dependencies
├── frontend/
│   ├── src/
│   │   ├── api.js                   # API client for backend communication
│   │   ├── App.jsx                  # Main dashboard component
│   │   ├── App.css                  # Dashboard layout, cards & responsive styles
│   │   ├── index.css                # Design tokens & color system
│   │   └── main.jsx                 # React DOM root entry
│   ├── public/                      # Static assets & SVG icons
│   ├── package.json                 # Node scripts & dependencies
│   └── vite.config.js               # Vite configuration & dev proxy
├── data/
│   └── demo_transactions.csv        # 83-row synthetic demo statement dataset (Seed 42)
├── docs/
│   └── demo_dataset.md              # Scenario documentation for synthetic demo data
├── .gitignore                       # Strict privacy, cache, and build exclusion rules
├── LICENSE                          # MIT License
└── README.md                        # Project documentation
```

---

## 🚀 Quickstart Guide (Windows PowerShell)

### Prerequisites
- **Python 3.11** (available via `py -3.11` or standard python)
- **Node.js 20+** (verified on Node.js 22.12.0 with npm 11.11.0)
- **Git**

---

### Step 1: Set Up Backend Environment

Open a PowerShell terminal in the project root:

```powershell
cd C:\Users\Dell\Downloads\slothaudit

# Create Python virtual environment if not already present
py -3.11 -m venv backend\.venv

# Activate virtual environment
.\backend\.venv\Scripts\Activate.ps1

# Install pinned dependencies
pip install -r backend\requirements.txt
```

---

### Step 2: Set Up Frontend Environment

In the same or a new terminal:

```powershell
cd C:\Users\Dell\Downloads\slothaudit\frontend

# Install frontend dependencies
npm install
```

---

### Step 3: Run the Application

#### Terminal 1 — Start the Backend (Local Only):
```powershell
cd C:\Users\Dell\Downloads\slothaudit
.\backend\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```
*Backend will be live at `http://127.0.0.1:8000` (Health: `http://127.0.0.1:8000/health`).*

#### Terminal 2 — Start the Frontend:
```powershell
cd C:\Users\Dell\Downloads\slothaudit\frontend
npm run dev -- --host 127.0.0.1
```
*Frontend dev server will be live at `http://127.0.0.1:5173`.*

Open your browser to `http://127.0.0.1:5173`.

---

## 🧪 Testing & Verification

### Run Backend Test Suite (85 Passing Tests)
```powershell
cd C:\Users\Dell\Downloads\slothaudit
.\backend\.venv\Scripts\python.exe -m pytest backend\tests -q
```

### Run Frontend Linter
```powershell
cd C:\Users\Dell\Downloads\slothaudit\frontend
npm run lint
```

### Run Frontend Production Build
```powershell
cd C:\Users\Dell\Downloads\slothaudit\frontend
npm run build
```

---

## 💡 Trying the Synthetic Demo Dataset

To immediately audit transactions without real bank files:
1. Open the dashboard at `http://127.0.0.1:5173`.
2. Upload the included synthetic file: `data/demo_transactions.csv`.
3. Click **Run Audit**.
4. You will see:
   - **83** transactions parsed across **16** merchants.
   - **10** total audit findings (7 active subscriptions, 1 price hike alert, 1 duplicate candidate, 1 inactive subscription).
   - **₹5,015.92/mo** active monthly commitment (₹60,191.00/yr).
   - **₹2,388.00/yr** potential hypothetical savings (from StreamFlix Basic plan consolidation).
   - Click **Explain Finding (Deep Dive)** on any card to view the deterministic explanation and provenance.

Read [`docs/demo_dataset.md`](docs/demo_dataset.md) for full scenario descriptions.

---

## 📜 License

Open source under the [MIT License](LICENSE). Copyright (c) 2026 Riya Maurya.
