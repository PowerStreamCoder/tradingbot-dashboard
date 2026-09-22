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

function renderProposalChanges(changes) {
    if (!changes || !changes.length) {
        return `<div class="empty-state" style="padding: 12px;">No pending parameter changes proposed.</div>`;
    }
    const rows = changes.map(ch => {
        const fieldName = ch.field ? `<code>${escapeHtml(ch.field)}</code>` : `<em>Priority Order Update</em>`;
        const valTransition = ch.field != null
            ? `<div class="val-transition"><span class="old-val">${escapeHtml(ch.current ?? '—')}</span><span class="val-arrow">→</span><span class="new-val">${escapeHtml(ch.proposed ?? '—')}</span></div>`
            : `Priority → <strong>${escapeHtml(ch.proposed_priority ?? '—')}</strong>`;

        const evidence = ch.evidence ? `<div class="evidence-text">${escapeHtml(ch.evidence)}</div>` : '';
        const risk = ch.risk ? `<div class="risk-warning">⚠ ${escapeHtml(ch.risk)}</div>` : '';

        return `
            <tr>
                <td>
                    ${chip(ch.kind || 'tune', ch.branch || 'general')}
                    <div style="font-size: 11px; color: var(--text-muted); margin-top: 2px;">${escapeHtml(ch.kind || '')}</div>
                </td>
                <td>${fieldName}</td>
                <td>${valTransition}</td>
                <td>
                    ${evidence}
                    ${risk}
                </td>
            </tr>`;
    }).join('');

    return `
        <table class="gov-changes-table">
            <thead>
                <tr>
                    <th style="width: 15%;">Branch / Kind</th>
                    <th style="width: 35%;">Parameter Field</th>
                    <th style="width: 20%;">Current → Proposed</th>
                    <th style="width: 30%;">Evidence & Risk Warning</th>
                </tr>
            </thead>
            <tbody>
                ${rows}
            </tbody>
        </table>`;
}

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
            const symbol = String(payload.symbol || entry.symbol || entry.doc_id || '—').toUpperCase();
            const intent = payload.intent || entry.intent || '—';
            const generatedRaw = entry.generated_at || payload.generated_at || '';
            const generatedFormatted = generatedRaw ? String(generatedRaw).slice(0, 19).replace('T', ' ') : '—';
            const isFresh = payload.config_current === true;
            const stalenessNote = payload.staleness_note || (isFresh ? 'Matches current live config' : 'Configuration drifted');
            const changes = payload.changes || [];
            const pendingCount = payload.pending_changes ?? changes.length;
            const fingerprintShort = payload.fingerprint ? String(payload.fingerprint).slice(0, 10) : '—';

            const freshBadge = isFresh
                ? chip('applied', 'FRESH (CONFIG MATCHES)')
                : chip('reverted', 'STALE / DRIFTED');

            const reviewBadge = payload.human_review_required
                ? chip('watch', 'HUMAN REVIEW REQUIRED')
                : '';

            const changesBadge = chip('fired', `${pendingCount} CHANGE(S)`);

            return `
                <div class="gov-card">
                    <div class="gov-header">
                        <div class="gov-header-left">
                            <div class="gov-symbol">🎯 ${escapeHtml(symbol)}</div>
                            <div class="gov-badges">
                                ${freshBadge}
                                ${reviewBadge}
                                ${changesBadge}
                            </div>
                        </div>
                        <div class="gov-intent">Intent: <strong>${escapeHtml(intent)}</strong></div>
                    </div>
                    <div class="gov-meta-info">
                        <div>📅 <strong>Generated:</strong> ${escapeHtml(generatedFormatted)}</div>
                        <div>🔑 <strong>Fingerprint:</strong> <code>${escapeHtml(fingerprintShort)}</code></div>
                        <div>ℹ️ <strong>Status:</strong> ${escapeHtml(stalenessNote)}</div>
                    </div>

                    ${renderProposalChanges(changes)}

                    <details>
                        <summary>View Technical Payload (JSON)</summary>
                        <pre>${escapeHtml(JSON.stringify(payload, null, 2))}</pre>
                    </details>
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

const ACTION_DESCRIPTIONS = {
    'peak_giveback_lock': { label: 'Peak Giveback Lock', desc: 'Protects unrealized peak profit against pullback' },
    'break_even_lock': { label: 'Break-Even Stop Lock', desc: 'Moves stop loss to entry price after initial gain' },
    'scale_out': { label: 'Partial Scale-Out', desc: 'Takes partial profit at target R-multiple' },
    'pyramid_add': { label: 'Pyramid Position Add', desc: 'Adds position size to winning trade milestone' },
    'time_decay_exit': { label: 'Time Decay Exit', desc: 'Exits or tightens stop at max holding duration' },
    'trailing_stop': { label: 'Trailing Stop Loss', desc: 'Trails stop loss as price advances' },
    'stop_loss': { label: 'Stop Loss Exit', desc: 'Standard protective stop loss' },
    'take_profit': { label: 'Take Profit Exit', desc: 'Target take profit exit' }
};

function formatActionType(actionType) {
    if (!actionType) return { label: '—', desc: '' };
    const key = String(actionType).toLowerCase().trim();
    if (ACTION_DESCRIPTIONS[key]) {
        return ACTION_DESCRIPTIONS[key];
    }
    // Dynamic fallback for any unmapped or future action types
    const cleanedLabel = key
        .replace(/_/g, ' ')
        .replace(/([a-z])([A-Z])/g, '$1 $2')
        .replace(/\b\w/g, l => l.toUpperCase());

    return { label: cleanedLabel, desc: '' };
}

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
            statsBox.innerHTML = stats.map(s => {
                const info = formatActionType(s.action_type);
                return `
                <div class="adaptive-stat">
                    <div class="a-type" title="${escapeHtml(s.action_type)}">${escapeHtml(info.label)}</div>
                    ${info.desc ? `<div style="font-size: 11px; color: var(--text-muted); margin-top: 2px; margin-bottom: 4px;">${escapeHtml(info.desc)}</div>` : ''}
                    <div class="a-meta">${s.fired} fired · ${s.resolved} resolved ·
                        win ${s.win_rate === null ? '—' : (s.win_rate * 100).toFixed(0) + '%'}</div>
                </div>`;
            }).join('');
        }

        if (!actions.length) {
            tbody.innerHTML = `<tr><td colspan="6" class="empty-state">No adaptive actions.</td></tr>`;
            return;
        }
        tbody.innerHTML = actions.map(a => {
            const outcome = a.outcome || {};
            const resolved = !!outcome.resolved;
            const info = formatActionType(a.action_type);
            return `
            <tr>
                <td>${escapeHtml(String(a.created_at || '').slice(0, 19).replace('T', ' '))}</td>
                <td>${escapeHtml(a.symbol || '—')}</td>
                <td>
                    <strong>${escapeHtml(info.label)}</strong>
                    ${info.desc ? `<div style="font-size: 11px; color: var(--text-muted);">${escapeHtml(info.desc)}</div>` : ''}
                    <div style="font-size: 10px; color: var(--text-muted); opacity: 0.7;"><code>${escapeHtml(a.action_type)}</code></div>
                </td>
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
    const container = document.getElementById('telemetry-container');
    try {
        const data = await fetchJSON(API_ENDPOINTS.GOVERNANCE.EXIT_TELEMETRY_SNAPSHOTS('', 50));
        const sessions = data.sessions || [];
        document.getElementById('stat-sessions').textContent = sessions.length;

        if (!sessions.length) {
            container.innerHTML = `<div class="empty-state">No archived exit-telemetry sessions yet.</div>`;
            return;
        }

        container.innerHTML = sessions.map(s => {
            const symbol = String(s.symbol || '—').toUpperCase();
            const archivedRaw = s.archived_at || '';
            const archivedFormatted = archivedRaw ? String(archivedRaw).slice(0, 19).replace('T', ' ') : '—';
            const branches = (s.inventory || {}).branches || {};
            const branchKeys = Object.keys(branches);

            const branchRows = branchKeys.map(branch => {
                const b = branches[branch] || {};
                const statusStr = String(b.status || 'shadowed').toLowerCase();
                const statusBadge = chip(statusStr, statusStr.toUpperCase());
                const proxNum = Number(b.proximity || 0);
                const proxPct = (proxNum * 100).toFixed(0);

                return `
                    <tr>
                        <td><code>${escapeHtml(branch)}</code></td>
                        <td>${statusBadge}</td>
                        <td>${b.evaluated ?? 0}</td>
                        <td>${b.fired ?? 0}</td>
                        <td>
                            <div class="val-transition">
                                <span class="${proxNum > 0 ? 'new-val' : ''}">${proxNum.toFixed(2)}</span>
                                <span style="font-size: 11px; color: var(--text-muted);">(${proxPct}%)</span>
                            </div>
                        </td>
                    </tr>`;
            }).join('');

            return `
                <div class="gov-card" style="margin-bottom: 16px;">
                    <div class="gov-header">
                        <div class="gov-header-left">
                            <div class="gov-symbol">📊 ${escapeHtml(symbol)}</div>
                            <div class="gov-badges">
                                ${chip('applied', `${branchKeys.length} BRANCHES`)}
                            </div>
                        </div>
                        <div class="gov-intent">Archived: <strong>${escapeHtml(archivedFormatted)}</strong></div>
                    </div>

                    <table class="gov-changes-table">
                        <thead>
                            <tr>
                                <th style="width: 25%;">Exit Branch</th>
                                <th style="width: 20%;">Qualification Status</th>
                                <th style="width: 15%;">Evaluated</th>
                                <th style="width: 15%;">Fired</th>
                                <th style="width: 25%;">Peak Proximity Saturation</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${branchRows || '<tr><td colspan="5" class="empty-state">No branch inventory.</td></tr>'}
                        </tbody>
                    </table>
                </div>`;
        }).join('');
    } catch (error) {
        container.innerHTML = `<div class="empty-state">Error loading sessions: ${escapeHtml(error.message)}</div>`;
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