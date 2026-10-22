# SlothAudit 🦥

> **Local-first AI recurring-payment auditor**  
> *Built for Hacktoberfest 2026 Week 3*

---

> [!WARNING]
> **Status: Work In Progress (WIP)**  
> SlothAudit is currently in initial setup and scaffolding. Core features and pipelines are being actively developed.

---

## 🔒 Privacy-First Goal

Financial transactions, bank statements, and subscription records contain sensitive personal data. **SlothAudit is designed local-first**:
- **Zero Cloud Exfiltration**: All parsing, cadence detection, and audit computations run entirely on your local machine.
- **Offline Capable**: The application does not require external third-party servers or SaaS APIs to analyze your financial statements.
- **Safe by Default**: Personal transaction exports are strictly kept local and excluded from version control.

---

## 🏗️ Planned Architecture

1. **Deterministic Audit Engine (Source of Truth)**:
   - High-precision algorithms for detecting billing cadences (weekly, monthly, quarterly, annual).
   - Rules-based detection for price hikes, duplicate/overlapping charges, creeping rate increases, and inactive "zombie" subscriptions.
2. **Backend**:
   - **Python 3.11** running **FastAPI**.
   - Structured endpoints for local file ingestion (CSV), deterministic audit execution, and report generation.
3. **Frontend**:
   - Modern, responsive SPA powered by **React** and **Vite**.
   - Interactive timeline, recurring payment breakdowns, and actionable cancellation checklists.
4. **Optional AI / Local Intelligence (Extensions)**:
   - *TabPFN*: Optional tabular ML for ambiguous pattern classification (without cloud APIs).
   - *Local LLM (e.g. Gemma)*: Optional local model runtime for generating subscription cancellation letters and friendly audit summaries.

---

## 📁 Repository Structure (Planned)

```text
slothaudit/
├── backend/
│   ├── app/          # FastAPI backend application modules
│   └── tests/        # Backend test suite
├── frontend/         # React + Vite user interface (scaffolded in subsequent task)
├── data/             # Local datasets and synthetic demo fixtures
├── docs/             # Architecture, design notes, and hackathon documentation
├── .gitignore        # Local-first privacy and build exclusions
└── README.md         # Project overview
```

---

## 📜 License

Open source under the MIT License.
