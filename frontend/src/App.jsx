import { useState, useId } from 'react';
import { uploadAndAuditCsv, fetchFindingExplanation } from './api';
import './App.css';

export default function App() {
  const [file, setFile] = useState(null);
  const [isDragging, setIsDragging] = useState(false);
  const [status, setStatus] = useState('idle'); // 'idle' | 'loading' | 'success' | 'error'
  const [errorMessage, setErrorMessage] = useState('');
  const [auditReport, setAuditReport] = useState(null);
  const [activeFilter, setActiveFilter] = useState('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [expandedEvidence, setExpandedEvidence] = useState({});
  const [explanations, setExplanations] = useState({}); // finding_id -> { loading, data, error }

  const fileInputId = useId();

  // ---------------------------------------------------------------------------
  // File Selection & Drag-and-Drop
  // ---------------------------------------------------------------------------

  const handleFileChange = (e) => {
    const selected = e.target.files?.[0];
    if (selected) {
      validateAndSetFile(selected);
    }
  };

  const validateAndSetFile = (f) => {
    if (!f.name.toLowerCase().endsWith('.csv')) {
      setErrorMessage('Only CSV statement files are accepted (.csv).');
      return;
    }
    if (f.size > 5 * 1024 * 1024) {
      setErrorMessage(`File exceeds the 5 MB limit (${(f.size / (1024 * 1024)).toFixed(2)} MB).`);
      return;
    }
    setErrorMessage('');
    setFile(f);
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragging(false);
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) {
      validateAndSetFile(dropped);
    }
  };

  // ---------------------------------------------------------------------------
  // Audit Submission
  // ---------------------------------------------------------------------------

  const handleRunAudit = async () => {
    if (!file) {
      setErrorMessage('Please select a CSV file first.');
      return;
    }

    setStatus('loading');
    setErrorMessage('');
    setExplanations({});
    setExpandedEvidence({});

    try {
      const report = await uploadAndAuditCsv(file);
      setAuditReport(report);
      setStatus('success');
    } catch (err) {
      setStatus('error');
      setErrorMessage(err.message || 'An error occurred during audit.');
    }
  };

  const handleReset = () => {
    setFile(null);
    setStatus('idle');
    setAuditReport(null);
    setErrorMessage('');
    setExplanations({});
    setExpandedEvidence({});
    setSearchQuery('');
    setActiveFilter('all');
  };

  // ---------------------------------------------------------------------------
  // Separate Explanation Action (/api/explain)
  // ---------------------------------------------------------------------------

  const handleExplainFinding = async (finding) => {
    const id = finding.finding_id;
    setExplanations((prev) => ({
      ...prev,
      [id]: { loading: true, data: null, error: null },
    }));

    try {
      const result = await fetchFindingExplanation(finding);
      setExplanations((prev) => ({
        ...prev,
        [id]: { loading: false, data: result, error: null },
      }));
    } catch (err) {
      // Fallback: use engine's pre-computed text
      setExplanations((prev) => ({
        ...prev,
        [id]: {
          loading: false,
          data: {
            finding_id: id,
            plain_explanation: finding.explanation,
            recommendation:
              finding.recommended_action === 'keep'
                ? 'No immediate action needed; subscription appears active and expected.'
                : finding.recommended_action === 'consider_cancelling'
                ? 'Consider whether this subscription is still needed or overlaps with another plan.'
                : 'Review your bank statements to confirm whether this recurring charge is still authorized.',
            caveat:
              finding.caveats?.[0] ||
              'Findings are analytical estimates. Verify with your merchant or bank before acting.',
            generated_by: 'deterministic',
          },
          error: `AI explanation unreachable (${err.message}); showing verified deterministic fallback.`,
        },
      }));
    }
  };

  const toggleEvidence = (id) => {
    setExpandedEvidence((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  // ---------------------------------------------------------------------------
  // Filter & Search Logic
  // ---------------------------------------------------------------------------

  const findings = auditReport?.findings || [];

  const filteredFindings = findings.filter((f) => {
    // Filter tab
    if (activeFilter === 'recurring' && (!f.is_active || f.finding_type !== 'recurring')) {
      return false;
    }
    if (activeFilter === 'inactive' && f.is_active) {
      return false;
    }
    if (activeFilter === 'price_increase' && f.finding_type !== 'price_increase') {
      return false;
    }
    if (activeFilter === 'duplicate_candidate' && f.finding_type !== 'duplicate_candidate') {
      return false;
    }

    // Search query
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      const matchMerchant = f.merchant_canonical.toLowerCase().includes(q);
      const matchExplanation = f.explanation.toLowerCase().includes(q);
      const matchAction = f.recommended_action.toLowerCase().includes(q);
      return matchMerchant || matchExplanation || matchAction;
    }
    return true;
  });

  const countActive = findings.filter((f) => f.is_active && f.finding_type === 'recurring').length;
  const countInactive = findings.filter((f) => !f.is_active).length;
  const countPriceHike = findings.filter((f) => f.finding_type === 'price_increase').length;
  const countDuplicates = findings.filter((f) => f.finding_type === 'duplicate_candidate').length;

  // ---------------------------------------------------------------------------
  // Formatters
  // ---------------------------------------------------------------------------

  const formatCurrency = (amt, curr = 'INR') => {
    if (amt === null || amt === undefined) return '—';
    const symbol = curr === 'INR' ? '₹' : curr + ' ';
    return `${symbol}${amt.toLocaleString('en-IN', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  };

  return (
    <div className="app-container">
      {/* -------------------------------------------------------------------- */}
      {/* Product Header */}
      {/* -------------------------------------------------------------------- */}
      <header className="header">
        <div className="header-brand">
          <div className="brand-icon" aria-hidden="true">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
            </svg>
          </div>
          <div>
            <div className="brand-title-row">
              <h1 className="brand-title">SlothAudit</h1>
              <span className="badge badge-local">Local-First</span>
            </div>
            <p className="brand-subtitle">Private Recurring Payment & Subscription Auditor</p>
          </div>
        </div>

        <div className="header-privacy-badge">
          <span className="privacy-dot" aria-hidden="true"></span>
          <span>100% In-Memory · Zero Cloud Telemetry · Non-Binding Estimates</span>
        </div>
      </header>

      {/* -------------------------------------------------------------------- */}
      {/* Main Content Area */}
      {/* -------------------------------------------------------------------- */}
      <main className="main-content">
        {/* Step 1: Upload Card (when idle, error, or file selected) */}
        {status !== 'success' && (
          <section className="card upload-card" aria-labelledby="upload-heading">
            <div className="upload-header">
              <h2 id="upload-heading" className="card-title">Audit Bank / Card Statement</h2>
              <p className="card-description">
                Upload a CSV export from your bank or card account. SlothAudit detects recurring
                subscriptions, hidden price increases, and duplicate plans entirely in memory on your machine.
              </p>
            </div>

            <div
              className={`dropzone ${isDragging ? 'dropzone-active' : ''}`}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
            >
              <input
                id={fileInputId}
                type="file"
                accept=".csv"
                className="file-input-hidden"
                onChange={handleFileChange}
              />
              <label htmlFor={fileInputId} className="dropzone-label">
                <svg className="dropzone-icon" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>
                </svg>
                {file ? (
                  <div className="dropzone-file-info">
                    <span className="file-name">{file.name}</span>
                    <span className="file-meta">
                      {(file.size / 1024).toFixed(1)} KB · Ready to audit
                    </span>

                </div>
                ) : (
                  <div className="dropzone-prompt">
                    <span className="dropzone-text-primary">
                      Click to choose statement CSV or drag & drop
                    </span>
                    <span className="dropzone-text-secondary">
                      Accepts .csv up to 5 MB (max 50,000 transactions)
                    </span>

                </div>
                )}
              </label>
            </div>

            {/* Error Message Alert */}
            {errorMessage && (
              <div className="alert alert-error" role="alert">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
                </svg>
                <span>{errorMessage}</span>
              </div>
            )}

            {/* Action Bar & Demo Link */}
            <div className="upload-actions">
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleRunAudit}
                disabled={!file || status === 'loading'}
              >
                {status === 'loading' ? (
                  <>
                    <span className="spinner" aria-hidden="true"></span>
                    Analyzing Statement In-Memory...
                  </>
                ) : (
                  'Run Audit'
                )}
              </button>

              {file && status !== 'loading' && (
                <button type="button" className="btn btn-secondary" onClick={handleReset}>
                  Clear File
                </button>
              )}
            </div>

            {/* Demo Helper Callout */}
            <div className="demo-callout">
              <span className="demo-callout-icon" aria-hidden="true">💡</span>
              <p>
                <strong>Testing SlothAudit?</strong> Use the included 83-row synthetic dataset at{' '}
                <code>data/demo_transactions.csv</code>. See{' '}
                <code>docs/demo_dataset.md</code> for scenario details. No real accounts or credentials needed.
              </p>
            </div>
          </section>
        )}

        {/* Step 2: Audit Results (when status === 'success') */}
        {status === 'success' && auditReport && (
          <div className="dashboard-results">
            {/* Action bar on top of results */}
            <div className="results-toolbar">
              <div className="results-meta">
                <span className="badge badge-success">Audit Complete</span>
                <span className="meta-text">
                  Analyzed <strong>{auditReport.total_transactions_analyzed}</strong> transactions across{' '}
                  <strong>{auditReport.total_merchants_analyzed}</strong> merchants
                </span>
              </div>
              {/* Download JSON button */}
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={() => {
                  try {
                    const exportReport = JSON.parse(JSON.stringify(auditReport));
                    exportReport.findings = exportReport.findings.map((f) => {
                      if (Array.isArray(f.evidence_items)) {
                        f.evidence_items = f.evidence_items.map((item) => {
                          const { description: _description, ...rest } = item;
                          return rest;
                        });
                      }
                      return f;
                    });
                    const payload = {
                      schema_version: "1.0",
                      audit_report: exportReport,
                      ...(exportReport.parse_diagnostics ? { parse_diagnostics: exportReport.parse_diagnostics } : {}),
                    };
                    const jsonStr = JSON.stringify(payload, null, 2);
                    const blob = new Blob([jsonStr], { type: "application/json;charset=utf-8" });
                    const url = URL.createObjectURL(blob);
                    const link = document.createElement('a');
                    const date = new Date().toLocaleDateString('en-CA');
                    link.href = url;
                    link.download = `slothaudit-audit-${date}.json`;
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                    URL.revokeObjectURL(url);
                  } catch (e) {
                    console.error('Download failed', e);
                    alert('Failed to generate download. See console for details.');
                  }
                }}
              >
                Download JSON
              </button>
              <button type="button" className="btn btn-secondary btn-sm" onClick={handleReset}>
                ← Audit Another Statement
              </button>
            </div>

            {/* Parse Diagnostics Notice (if any) */}
            {auditReport.parse_diagnostics && auditReport.parse_diagnostics.invalid_rows > 0 && (
              <div className="alert alert-warning" role="status">
                <span>
                  <strong>Note:</strong> {auditReport.parse_diagnostics.valid_rows} of{' '}
                  {auditReport.parse_diagnostics.total_rows} rows were successfully validated. (
                  {auditReport.parse_diagnostics.invalid_rows} row(s) had non-standard formatting and were safely skipped).
                </span>
              </div>
            )}

            {/* Summary Metrics Cards */}
            <section className="metrics-grid" aria-label="Audit Cost Summary">
              <div className="metric-card">
                <div className="metric-header">
                  <span className="metric-label">Active Monthly Commitment</span>
                  <span className="metric-tag tag-neutral">Current</span>

                </div>
                <div className="metric-value">
                  {formatCurrency(auditReport.total_estimated_monthly_recurring, auditReport.currency)}
                  <span className="metric-unit">/mo</span>

                </div>
                <p className="metric-hint">Sum of detected active recurring subscriptions</p>
              </div>

              <div className="metric-card">
                <div className="metric-header">
                  <span className="metric-label">Active Annual Commitment</span>
                  <span className="metric-tag tag-neutral">Annualized</span>

                </div>
                <div className="metric-value">
                  {formatCurrency(auditReport.total_estimated_annual_recurring, auditReport.currency)}
                  <span className="metric-unit">/yr</span>

                </div>
                <p className="metric-hint">Monthly charges × 12 + annual renewals</p>
              </div>

              <div className="metric-card metric-card-highlight">
                <div className="metric-header">
                  <span className="metric-label">Hypothetical Annual Savings</span>
                  <span className="metric-tag tag-highlight">Potential</span>

                </div>
                <div className="metric-value text-highlight">
                  {formatCurrency(auditReport.potential_hypothetical_annual_savings, auditReport.currency)}
                  <span className="metric-unit">/yr</span>

                </div>
                <p className="metric-hint">
                  If duplicate or overlapping plans are consolidated. Non-binding estimate.
                </p>
              </div>
            </section>

            {/* Filter Tabs & Search Bar */}
            <div className="filter-bar">
              <div className="filter-tabs" role="tablist">
                <button
                  type="button"
                  role="tab"
                  aria-selected={activeFilter === 'all'}
                  className={`tab-btn ${activeFilter === 'all' ? 'tab-btn-active' : ''}`}
                  onClick={() => setActiveFilter('all')}
                >
                  All Findings <span className="tab-count">{findings.length}</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={activeFilter === 'recurring'}
                  className={`tab-btn ${activeFilter === 'recurring' ? 'tab-btn-active' : ''}`}
                  onClick={() => setActiveFilter('recurring')}
                >
                  Active <span className="tab-count">{countActive}</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={activeFilter === 'price_increase'}
                  className={`tab-btn ${activeFilter === 'price_increase' ? 'tab-btn-active' : ''}`}
                  onClick={() => setActiveFilter('price_increase')}
                >
                  Price Hikes <span className="tab-count">{countPriceHike}</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={activeFilter === 'duplicate_candidate'}
                  className={`tab-btn ${activeFilter === 'duplicate_candidate' ? 'tab-btn-active' : ''}`}
                  onClick={() => setActiveFilter('duplicate_candidate')}
                >
                  Duplicates <span className="tab-count">{countDuplicates}</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={activeFilter === 'inactive'}
                  className={`tab-btn ${activeFilter === 'inactive' ? 'tab-btn-active' : ''}`}
                  onClick={() => setActiveFilter('inactive')}
                >
                  Inactive <span className="tab-count">{countInactive}</span>
                </button>
              </div>

              <div className="search-box">
                <input
                  type="search"
                  placeholder="Filter by merchant..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="search-input"
                />
              </div>
            </div>

            {/* Findings List */}
            {filteredFindings.length === 0 ? (
              <div className="empty-state card">
                <p>No audit findings matched your current filter.</p>
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => { setActiveFilter('all'); setSearchQuery(''); }}>
                  Reset Filters
                </button>
              </div>
            ) : (
              <div className="findings-list">
                {filteredFindings.map((finding) => {
                  const id = finding.finding_id;
                  const explanationState = explanations[id];
                  const hasExplanation = Boolean(explanationState?.data);
                  const isExpanded = Boolean(expandedEvidence[id]);

                  return (
                    <article key={id} className="finding-card card">
                      {/* Top Header of Finding Card */}
                      <div className="finding-header">
                        <div>
                          <div className="merchant-row">
                            <h3 className="merchant-title">{finding.merchant_canonical}</h3>
                            {/* Type badge */}
                            <span className={`badge ${
                              !finding.is_active
                                ? 'badge-inactive'
                                : finding.finding_type === 'price_increase'
                                ? 'badge-hike'
                                : finding.finding_type === 'duplicate_candidate'
                                ? 'badge-duplicate'
                                : 'badge-active'
                            }`}>
                              {!finding.is_active
                                ? 'Inactive / Churned'
                                : finding.finding_type === 'price_increase'
                                ? 'Price Hike Alert'
                                : finding.finding_type === 'duplicate_candidate'
                                ? 'Overlapping Plan'
                                : 'Active Subscription'}
                            </span>

                            {/* Cadence */}
                            <span className="badge badge-outline">
                              {finding.cadence === 'variable_monthly'
                                ? 'Variable Utility'
                                : finding.cadence === 'annual'
                                ? 'Annual'
                                : 'Monthly'}
                            </span>
        
                </div>

                          <p className="evidence-summary-text">{finding.evidence_summary}</p>
      
                </div>

                        {/* Cost Block */}
                        <div className="finding-cost-block">
                          {finding.is_supplemental ? (
                            <div className="cost-supplemental">
                              <span className="cost-label">Price Adjustment</span>
                              <span className="cost-val text-hike">
                                +{finding.percent_increase}%
                              </span>
                              <span className="cost-sub">
                                +{formatCurrency(finding.amount_difference, auditReport.currency)}/mo
                              </span>
          
                </div>
                          ) : !finding.is_active ? (
                            <div className="cost-inactive">
                              <span className="cost-label">Active Commitment</span>
                              <span className="cost-val">₹0.00</span>
                              <span className="cost-sub">
                                Previously {formatCurrency(finding.historical_monthly_cost, auditReport.currency)}/mo
                              </span>
          
                </div>
                          ) : (
                            <div className="cost-active">
                              <span className="cost-label">Estimated Cost</span>
                              <span className="cost-val">
                                {formatCurrency(finding.estimated_monthly_cost, auditReport.currency)}
                                <span className="cost-unit">/mo</span>
                              </span>
                              <span className="cost-sub">
                                {formatCurrency(finding.estimated_annual_cost, auditReport.currency)}/yr
                              </span>
          
                </div>
                          )}
      
                </div>
    
                </div>

                      {/* Engine Finding Body */}
                      <div className="finding-body">
                        <p className="finding-explanation">{finding.explanation}</p>

                        {/* Price Increase Details Pill if applicable */}
                        {finding.finding_type === 'price_increase' && (
                          <div className="hike-detail-box">
                            <span className="hike-detail-text">
                              Rate Transition: <strong>₹{finding.previous_amount?.toFixed(2)}</strong> →{' '}
                              <strong>₹{finding.new_amount?.toFixed(2)}</strong> (
                              +{finding.percent_increase}% increase). Linked to primary finding{' '}
                              <code>{finding.related_finding_id}</code> (zero double-counting in totals).
                            </span>
        
                </div>
                        )}

                        {/* Duplicate candidate potential savings pill if applicable */}
                        {finding.finding_type === 'duplicate_candidate' && (
                          <div className="duplicate-detail-box">
                            <span>
                              Concurrent with primary subscription tier. Consolidating or cancelling this secondary
                              plan could yield up to{' '}
                              <strong>
                                {formatCurrency(finding.potential_hypothetical_monthly_savings, auditReport.currency)}/mo
                              </strong>{' '}
                              in hypothetical savings.
                            </span>
        
                </div>
                        )}

                        {/* Recommended Action + Confidence */}
                        <div className="finding-meta-row">
                          <div className="action-pill-wrapper">
                            <span className="meta-field-label">Action:</span>
                            <span
                              className={`action-pill ${
                                finding.recommended_action === 'keep'
                                  ? 'action-keep'
                                  : finding.recommended_action === 'consider_cancelling'
                                  ? 'action-cancel'
                                  : 'action-investigate'
                              }`}
                            >
                              {finding.recommended_action === 'keep'
                                ? '✓ Keep (Expected)'
                                : finding.recommended_action === 'consider_cancelling'
                                ? '⚠ Consider Cancelling'
                                : '🔍 Investigate Status'}
                            </span>
        
                </div>

                          <div className="confidence-wrapper">
                            <span className="meta-field-label">Certainty:</span>
                            <span className="confidence-pill">
                              {Math.round(finding.confidence * 100)}% Confidence
                            </span>
        
                </div>
      
                </div>

                        {/* Caveats list */}
                        {finding.caveats && finding.caveats.length > 0 && (
                          <div className="caveats-box">
                            <span className="caveats-title">Considerations:</span>
                            <ul className="caveats-list">
                              {finding.caveats.map((c, idx) => (
                                <li key={idx}>{c}</li>
                              ))}
                            </ul>
        
                </div>
                        )}
    
                </div>

                      {/* Evidence Txn IDs Collapsible */}
                      <div className="evidence-section">
                        <button
                          type="button"
                          className="evidence-toggle-btn"
                          onClick={() => toggleEvidence(id)}
                        >
                          <svg
                            className={`toggle-caret ${isExpanded ? 'toggle-caret-expanded' : ''}`}
                            width="14"
                            height="14"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                          >
                            <polyline points="9 18 15 12 9 6"></polyline>
                          </svg>
                          <span>Evidence: {finding.evidence_txn_ids.length} Transaction IDs</span>
                        </button>

                        {isExpanded && (
                          <div className="evidence-ids-list">
                            {finding.evidence_txn_ids.map((txnId) => (
                              <code key={txnId} className="evidence-id-tag">
                                {txnId}
                              </code>
                            ))}
        
                </div>
                        )}
    
                </div>

                      {/* Deep-Dive Explanation Panel (/api/explain) */}
                      <div className="explanation-section">
                        {!hasExplanation && !explanationState?.loading && (
                          <button
                            type="button"
                            className="btn btn-outline btn-sm"
                            onClick={() => handleExplainFinding(finding)}
                          >
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                              <circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/>
                            </svg>
                            Explain Finding (Deep Dive)
                          </button>
                        )}

                        {explanationState?.loading && (
                          <div className="explanation-loading">
                            <span className="spinner spinner-sm" aria-hidden="true"></span>
                            <span>Generating evidence-grounded explanation...</span>
        
                </div>
                        )}

                        {hasExplanation && (
                          <div className="explanation-box">
                            <div className="explanation-box-header">
                              <span className="explanation-box-title">Structured Audit Insight</span>
                              <span className={`badge ${
                                explanationState.data.generated_by === 'deterministic'
                                  ? 'badge-prov-deterministic'
                                  : 'badge-prov-ai'
                              }`}>
                                {explanationState.data.generated_by === 'deterministic'
                                  ? 'Verified Deterministic Engine'
                                  : `Model: ${explanationState.data.generated_by}`}
                              </span>
          
                </div>

                            <p className="explanation-plain">{explanationState.data.plain_explanation}</p>

                            <div className="explanation-fields">
                              <div className="explanation-field">
                                <span className="field-label">Action Guidance:</span>
                                <span className="field-val">{explanationState.data.recommendation}</span>
            
                </div>
                              <div className="explanation-field">
                                <span className="field-label">Uncertainty Note:</span>
                                <span className="field-val text-muted">{explanationState.data.caveat}</span>
            
                </div>
          
                </div>

                            {explanationState.error && (
                              <div className="explanation-notice">
                                <small>{explanationState.error}</small>
            
                </div>
                            )}
        
                </div>
                        )}
    
                </div>
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </main>

      {/* -------------------------------------------------------------------- */}
      {/* Privacy & Trust Footer */}
      {/* -------------------------------------------------------------------- */}
      <footer className="footer">
        <div className="footer-content">
          <div className="footer-col">
            <h4 className="footer-heading">SlothAudit Privacy Commitment</h4>
            <p className="footer-text">
              SlothAudit is engineered as a local-first application. Transactions are parsed and audited
              entirely in memory on this device. No statement bytes or customer financial data are stored,
              persisted to disk, or sent to external AI APIs.
            </p>
          </div>
          <div className="footer-col">
            <h4 className="footer-heading">Safety & Non-Binding Notice</h4>
            <p className="footer-text">
              Findings and hypothetical savings are analytical estimates based strictly on observed billing
              cadences. SlothAudit makes no assertions of fraud and will never automatically modify, cancel, or
              charge any subscription on your behalf.
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
}
