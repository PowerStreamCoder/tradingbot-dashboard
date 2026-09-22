/**
 * Governance Review Dashboard JavaScript
 * Loads exit-governance mirror, applied-changes ledger, adaptive engine
 * actions, and the per-session exit-telemetry archive. Polls on an interval.
 */

let refreshTimer = null;

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[ch]));
}

function showToast(message, type = 'success') {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = `toast ${type} show`;
    setTimeout(() => toast.classList.remove('show'), 3000);
}

function chip(status, label) {
    const cls = String(status || '').replace(/[^a-z0-9_]/gi, '');
    return `<span class="chip chip-${cls}">${label}</span>`;
}

function fmtVal(v) {
    if (v === null || v === undefined || v === '') return '—';
    if (typeof v === 'object') return `<pre>${JSON.stringify(v).slice(0, 120)}</pre>`;
    return String(v).length > 90 ? String(v).slice(0, 90) + '…' : String(v);
}

async function fetchJSON(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}

/* ------------------------------- governance ------------------------------- */

async function loadGovernance() {
    const container = document.getElementById('governance-container');
    try {
        const data = await fetchJSON(API_ENDPOINTS.GOVERNANCE.EXIT);
        const entries = data.governance || [];
        document.getElementById('stat-governance').textContent = entries.length;

        if (!entries.length) {
            container.innerHTML = `<div class="empty-state">No exit-governance proposals mirrored yet.</div>`;
            return;
        }
        container.innerHTML = entries.map(entry => {
            const payload = entry.payload || entry.data || entry;
            const intent = payload.intent || entry.intent || '—';
            const generated = entry.generated_at || payload.generated_at || '';
            return `
                <div class="gov-card">
                    <div class="gov-symbol">${escapeHtml(entry.symbol || entry.doc_id || '—')}</div>
                    <div class="gov-intent">Intent: ${escapeHtml(intent)} · ${escapeHtml(generated)}</div>
                    <pre>${escapeHtml(JSON.stringify(payload, null, 2).slice(0, 1400))}</pre>
                </div>`;
        }).join('');
    } catch (error) {
        container.innerHTML = `<div class="empty-state">Error loading governance: ${escapeHtml(error.message)}</div>`;
    }
}

/* ------------------------------ ledger ------------------------------ */

async function loadLedger() {
    const tbody = document.getElementById('ledger-body');
    try {
        const data = await fetchJSON(API_ENDPOINTS.GOVERNANCE.APPLIED_CHANGES('', 100));
        const changes = data.changes || [];
        document.getElementById('stat-ledger').textContent = changes.length;

        if (!changes.length) {
            tbody.innerHTML = `<tr><td colspan="8" class="empty-state">No applied-changes ledger records.</td></tr>`;
            return;
        }
        tbody.innerHTML = changes.map(c => `
            <tr>
                <td>${escapeHtml(String(c.applied_at || '').slice(0, 19).replace('T', ' '))}</td>
                <td>${escapeHtml(c.symbol || '—')}</td>
                <td>${escapeHtml(c.engine || '—')}</td>
                <td>${escapeHtml(c.field || '—')}</td>
                <td>${fmtVal(c.old_value)}</td>
                <td>${fmtVal(c.new_value)}</td>
                <td>${chip(c.decision, c.decision || '—')}</td>
                <td>${chip(c.status, c.status || '—')}</td>
            </tr>`).join('');
    } catch (error) {
        tbody.innerHTML = `<tr><td colspan="8" class="empty-state">Error loading ledger: ${escapeHtml(error.message)}</td></tr>`;
    }
}

/* ---------------------------- adaptive actions ---------------------------- */

async function loadAdaptive() {
    const statsBox = document.getElementById('adaptive-stats');
    const tbody = document.getElementById('adaptive-body');
    try {
        const data = await fetchJSON(API_ENDPOINTS.GOVERNANCE.ADAPTIVE_ACTIONS(100));
        const actions = data.actions || [];
        const stats = data.stats || [];
        document.getElementById('stat-adaptive').textContent = actions.length;

        if (!stats.length) {
            statsBox.innerHTML = `<div class="empty-state">No adaptive engine actions recorded.</div>`;
        } else {
            statsBox.innerHTML = stats.map(s => `
                <div class="adaptive-stat">
                    <div class="a-type">${escapeHtml(s.action_type)}</div>
                    <div class="a-meta">${s.fired} fired · ${s.resolved} resolved ·
                        win ${s.win_rate === null ? '—' : (s.win_rate * 100).toFixed(0) + '%'}</div>
                </div>`).join('');
        }

        if (!actions.length) {
            tbody.innerHTML = `<tr><td colspan="6" class="empty-state">No adaptive actions.</td></tr>`;
            return;
        }
        tbody.innerHTML = actions.map(a => {
            const outcome = a.outcome || {};
            const resolved = !!outcome.resolved;
            return `
            <tr>
                <td>${escapeHtml(String(a.created_at || '').slice(0, 19).replace('T', ' '))}</td>
                <td>${escapeHtml(a.symbol || '—')}</td>
                <td>${escapeHtml(a.action_type || '—')}</td>
                <td>${fmtVal(a.threshold_used)}</td>
                <td>${chip(resolved ? 'applied' : 'pending', resolved ? 'resolved' : 'pending')}</td>
                <td>${outcome.final_pnl === undefined || outcome.final_pnl === null ? '—' : '$' + Number(outcome.final_pnl).toFixed(2)}</td>
            </tr>`;
        }).join('');
    } catch (error) {
        statsBox.innerHTML = `<div class="empty-state">Error loading adaptive actions: ${escapeHtml(error.message)}</div>`;
        tbody.innerHTML = '';
    }
}

/* --------------------------- telemetry archive --------------------------- */

async function loadTelemetry() {
    const tbody = document.getElementById('telemetry-body');
    try {
        const data = await fetchJSON(API_ENDPOINTS.GOVERNANCE.EXIT_TELEMETRY_SNAPSHOTS('', 50));
        const sessions = data.sessions || [];
        document.getElementById('stat-sessions').textContent = sessions.length;

        if (!sessions.length) {
            tbody.innerHTML = `<tr><td colspan="7" class="empty-state">No archived exit-telemetry sessions yet.</td></tr>`;
            return;
        }
        const rows = [];
        for (const s of sessions) {
            const branches = (s.inventory || {}).branches || {};
            for (const [branch, b] of Object.entries(branches)) {
                rows.push(`
                    <tr>
                        <td>${escapeHtml(String(s.archived_at || '').slice(0, 19).replace('T', ' '))}</td>
                        <td>${escapeHtml(s.symbol || '—')}</td>
                        <td>${escapeHtml(branch)}</td>
                        <td>${chip(b.status, b.status || '—')}</td>
                        <td>${b.evaluated}</td>
                        <td>${b.fired}</td>
                        <td>${Number(b.proximity || 0).toFixed(2)}</td>
                    </tr>`);
            }
        }
        tbody.innerHTML = rows.length ? rows.join('') : `<tr><td colspan="7" class="empty-state">No branch inventory.</td></tr>`;
    } catch (error) {
        tbody.innerHTML = `<tr><td colspan="7" class="empty-state">Error loading sessions: ${escapeHtml(error.message)}</td></tr>`;
    }
}

/* ---------------------------------- main ---------------------------------- */

async function loadAll() {
    await Promise.allSettled([loadGovernance(), loadLedger(), loadAdaptive(), loadTelemetry()]);
}

function startPolling() {
    const sel = document.getElementById('refresh-interval');
    const schedule = () => {
        if (refreshTimer) clearInterval(refreshTimer);
        refreshTimer = setInterval(loadAll, Number(sel.value));
    };
    sel.addEventListener('change', schedule);
    schedule();
}

document.addEventListener('DOMContentLoaded', () => {
    loadAll();
    startPolling();
});