/**
 * SlothAudit API Client
 *
 * Configurable via VITE_API_BASE_URL (defaults to empty string for Vite proxy).
 * Never persists or transmits financial data beyond the configured backend endpoint.
 */

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '');

/**
 * Upload a CSV file to /api/audit
 * @param {File} file
 * @returns {Promise<import('./types').AuditReport>}
 */
export async function uploadAndAuditCsv(file) {
  if (!file) {
    throw new Error('Please select a CSV file to audit.');
  }

  const formData = new FormData();
  formData.append('file', file);

  let response;
  try {
    response = await fetch(`${API_BASE}/api/audit`, {
      method: 'POST',
      body: formData,
    });
  } catch (err) {
    throw new Error(
      `Unable to reach the SlothAudit API server (${API_BASE || 'local'}). ${err.message || ''} Please ensure the backend is running on http://127.0.0.1:8000.`
    );
  }

  const contentType = response.headers.get('content-type') || '';
  let payload;
  if (contentType.includes('application/json')) {
    payload = await response.json();
  } else {
    const text = await response.text();
    payload = { detail: text || `HTTP ${response.status} ${response.statusText}` };
  }

  if (!response.ok) {
    const errorMsg =
      payload.detail ||
      payload.message ||
      `Audit request failed with status code ${response.status}.`;
    throw new Error(errorMsg);
  }

  return payload;
}

/**
 * Request an evidence-grounded explanation for an individual finding via /api/explain
 * @param {object} finding - AuditFinding
 * @returns {Promise<object>} - ExplanationResponse
 */
export async function fetchFindingExplanation(finding) {
  if (!finding) {
    throw new Error('Invalid finding data.');
  }

  let response;
  try {
    response = await fetch(`${API_BASE}/api/explain`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(finding),
    });
  } catch (err) {
    throw new Error(
      `Failed to contact explanation service (${err.message || 'network error'}).`
    );
  }

  const contentType = response.headers.get('content-type') || '';
  let payload;
  if (contentType.includes('application/json')) {
    payload = await response.json();
  } else {
    payload = { detail: await response.text() };
  }

  if (!response.ok) {
    throw new Error(payload.detail || `Explanation request failed (HTTP ${response.status}).`);
  }

  return payload;
}

/**
 * Check backend liveness via /health
 */
export async function checkBackendHealth() {
  try {
    const res = await fetch(`${API_BASE}/health`, { method: 'GET' });
    if (!res.ok) return false;
    const data = await res.json();
    return data.status === 'healthy';
  } catch {
    return false;
  }
}
