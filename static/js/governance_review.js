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
    const cls = String(status || '').replace(/[^a-z0-9_-]/gi, '');
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

function formatBranchKindCell(branch, kind) {
    const bKey = String(branch || '').toLowerCase().trim();
    const branchMeta = (typeof EXIT_BRANCH_METADATA !== 'undefined' && EXIT_BRANCH_METADATA[bKey]) ? EXIT_BRANCH_METADATA[bKey] : {
        name: bKey ? bKey.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase()) : 'General Rule',
        icon: '🌿',
        category: 'Exit Rule'
    };

    const kKey = String(kind || '').toLowerCase().trim();
    let kindBadge = '';
    if (kKey === 'enable') {
        kindBadge = `<span class="kind-badge kind-enable">⚡ Enable Rule</span>`;
    } else if (kKey === 'disable') {
        kindBadge = `<span class="kind-badge kind-disable">⛔ Disable Rule</span>`;
    } else if (kKey === 'tune') {
        kindBadge = `<span class="kind-badge kind-tune">🔧 Tune Threshold</span>`;
    } else if (kKey === 'reorder') {
        kindBadge = `<span class="kind-badge kind-reorder">↕️ Reorder Priority</span>`;
    } else if (kKey) {
        kindBadge = `<span class="kind-badge kind-general">${escapeHtml(kKey)}</span>`;
    }

    return `
        <div class="branch-kind-cell">
            <div class="branch-title-row">
                <span class="branch-icon">${branchMeta.icon}</span>
                <strong class="branch-name">${escapeHtml(branchMeta.name)}</strong>
            </div>
            <code class="branch-raw">${escapeHtml(branch || 'general')}</code>
            <div class="branch-kind-row">${kindBadge}</div>
        </div>
    `;
}

function renderProposalChanges(changes) {
    if (!changes || !changes.length) {
        return `<div class="empty-state" style="padding: 24px;">No pending parameter changes proposed.</div>`;
    }
    const rows = changes.map(ch => {
        const branchCell = formatBranchKindCell(ch.branch, ch.kind);
        const fieldCell = ch.field ? formatFieldCell(ch.field) : `
            <div class="field-cell">
                <div class="field-title-row">
                    <span class="field-title">Cascade Priority Ordering</span>
                    <span class="field-cat cat-priority">Evaluation Priority</span>
                </div>
                <code class="field-code">cascade_priority</code>
                <div class="field-desc">Order in which exit conditions are evaluated by the cascade</div>
            </div>`;

        let currentFormatted = '';
        let proposedFormatted = '';

        if (ch.field != null) {
            const isEnableKind = String(ch.kind || '').toLowerCase() === 'enable';
            let currVal = ch.current;
            if ((currVal === null || currVal === undefined || currVal === '') && isEnableKind) {
                currVal = false;
            }
            let propVal = ch.proposed;
            if ((propVal === null || propVal === undefined || propVal === '') && isEnableKind) {
                propVal = true;
            }
            currentFormatted = formatValDetailed(currVal, ch.field);
            proposedFormatted = formatValDetailed(propVal, ch.field);
        } else {
            currentFormatted = `<span class="val-pill val-metric">P${escapeHtml(ch.current_priority ?? '—')}</span>`;
            proposedFormatted = `<span class="val-pill val-on">P${escapeHtml(ch.proposed_priority ?? '—')}</span>`;
        }

        const valTransition = `
            <div class="val-transition-card">
                <div class="val-state-box val-current">
                    <span class="val-state-label">CURRENT</span>
                    <div class="val-content">${currentFormatted}</div>
                </div>
                <div class="val-arrow-box">➔</div>
                <div class="val-state-box val-proposed">
                    <span class="val-state-label">PROPOSED</span>
                    <div class="val-content">${proposedFormatted}</div>
                </div>
            </div>
        `;

        const evidence = ch.evidence ? `
            <div class="proposal-evidence">
                <div class="evidence-header">
                    <span class="evidence-icon">📊</span>
                    <strong>Observed Evidence</strong>
                </div>
                <div class="evidence-body">${escapeHtml(ch.evidence)}</div>
            </div>` : '';

        const cleanRisk = ch.risk ? String(ch.risk).replace(/^⚠️?\s*/, '') : '';
        const risk = cleanRisk ? `
            <div class="proposal-risk">
                <div class="risk-header">
                    <span class="risk-icon">⚠️</span>
                    <strong>Governance &amp; Safety Impact</strong>
                </div>
                <div class="risk-body">${escapeHtml(cleanRisk)}</div>
            </div>` : '';

        return `
            <tr>
                <td class="col-branch">${branchCell}</td>
                <td class="col-field">${fieldCell}</td>
                <td class="col-transition">${valTransition}</td>
                <td class="col-evidence">
                    <div class="proposal-evidence-stack">
                        ${evidence}
                        ${risk}
                    </div>
                </td>
            </tr>`;
    }).join('');

    return `
        <div class="gov-table-wrapper">
            <table class="gov-changes-table">
                <thead>
                    <tr>
                        <th style="width: 20%;">Branch / Kind</th>
                        <th style="width: 30%;">Parameter Field</th>
                        <th style="width: 22%;">Current → Proposed</th>
                        <th style="width: 28%;">Evidence &amp; Risk Warning</th>
                    </tr>
                </thead>
                <tbody>
                    ${rows}
                </tbody>
            </table>
        </div>`;
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

            const autoApplyOn = payload.auto_apply_governance !== undefined
                ? Boolean(payload.auto_apply_governance)
                : (entry.auto_apply_governance !== undefined ? Boolean(entry.auto_apply_governance) : true);

            const autoApplyBadge = autoApplyOn
                ? chip('auto-apply-on', '⚡ AUTO-APPLY: ON')
                : chip('auto-apply-off', '⚪ AUTO-APPLY: OFF');

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
                                ${autoApplyBadge}
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
                        <div>⚙️ <strong>Auto-Apply:</strong> ${autoApplyOn ? '<span style="color: #10b981; font-weight: 600;">ON</span> (Autonomous L1 calibration)' : '<span style="color: #94a3b8; font-weight: 600;">OFF</span> (Manual operator review only)'}</div>
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

let rawLedgerData = [];
const expandedLedgerRows = new Set();

const FIELD_METADATA = {
    'threshold_coordination.max_hold_hours': {
        name: 'Max Holding Duration',
        category: 'Time Exit',
        desc: 'Forced liquidation ceiling to free up capital if trade stagnates',
        unit: 'hours',
        formatVal: (v) => {
            const n = Number(v);
            if (isNaN(n) || n === 0) return '<span class="val-pill val-off">0 hrs (No Limit)</span>';
            if (n === 24) return '<span class="val-pill val-on">24 hrs (1 Day)</span>';
            if (n % 24 === 0) return `<span class="val-pill val-on">${n} hrs (${n / 24} Days)</span>`;
            return `<span class="val-pill val-on">${n} hrs</span>`;
        }
    },
    'use_atr_targets': {
        name: 'ATR Dynamic Targets',
        category: 'Profit Targets',
        desc: 'Derives profit and trailing targets from dynamic ATR multiples instead of fixed percentages',
        isBool: true
    },
    'use_sma_slope_exit': {
        name: 'SMA Slope Trend Exit',
        category: 'Trend Exit',
        desc: 'Triggers early exit when the 200 SMA slope reverses against position direction',
        isBool: true
    },
    'threshold_coordination.take_profit_without_call_atr': {
        name: 'Take Profit (Naked Long)',
        category: 'Profit Targets',
        desc: 'ATR-multiple profit target for naked long positions',
        unit: '× ATR',
        formatVal: (v) => `<span class="val-pill val-metric">${escapeHtml(v)}× ATR</span>`
    },
    'threshold_coordination.take_profit_with_call_atr': {
        name: 'Take Profit (Covered Call)',
        category: 'Profit Targets',
        desc: 'ATR-multiple profit target when covered call is active',
        unit: '× ATR',
        formatVal: (v) => `<span class="val-pill val-metric">${escapeHtml(v)}× ATR</span>`
    },
    'threshold_coordination.trailing_stop_activation_atr': {
        name: 'Trailing Stop Activation',
        category: 'Trailing Stop',
        desc: 'ATR profit threshold required before trailing stop arms',
        unit: '× ATR',
        formatVal: (v) => `<span class="val-pill val-metric">${escapeHtml(v)}× ATR</span>`
    },
    'threshold_coordination.trailing_stop_distance_atr': {
        name: 'Trailing Stop Distance',
        category: 'Trailing Stop',
        desc: 'Distance behind peak price to trail the protective stop',
        unit: '× ATR',
        formatVal: (v) => `<span class="val-pill val-metric">${escapeHtml(v)}× ATR</span>`
    },
    'capital_multiplier': {
        name: 'Capital Sizing Multiplier',
        category: 'Position Sizing',
        desc: 'Multiplies default base capital allocation for position sizing',
        unit: '×',
        formatVal: (v) => `<span class="val-pill val-metric">${escapeHtml(v)}×</span>`
    },
    'stop_loss_threshold': {
        name: 'Protective Stop Loss',
        category: 'Risk & Stops',
        desc: 'Maximum tolerated loss threshold before hard exit',
        unit: '%',
        formatVal: (v) => `<span class="val-pill val-metric">${(Number(v) * 100).toFixed(2)}%</span>`
    },
    'profit_target_net': {
        name: 'Net Profit Target',
        category: 'Profit Targets',
        desc: 'Base net percentage profit target for position exit',
        unit: '%',
        formatVal: (v) => `<span class="val-pill val-metric">${(Number(v) * 100).toFixed(2)}%</span>`
    },
    'pyramiding_enabled': {
        name: 'Pyramiding Scale-In',
        category: 'Position Sizing',
        desc: 'Allows adding position size to winning trades on milestone breakouts',
        isBool: true
    },
    'adaptive_pnl.time_decay_enabled': {
        name: 'Time Decay Exit',
        category: 'Time Exit',
        desc: 'Progressively lowers profit target as position duration extends',
        isBool: true
    },
    'adaptive_pnl.peak_giveback_lock': {
        name: 'Peak Giveback Lock',
        category: 'Profit Lock',
        desc: 'Protects unrealized peak profit against sharp drawdowns',
        isBool: true
    },
    'adaptive_pnl.break_even_lock': {
        name: 'Break-Even Stop Lock',
        category: 'Risk & Stops',
        desc: 'Moves stop loss to entry price after initial favorable movement',
        isBool: true
    },
    'scale_out_target_r': {
        name: 'Partial Scale-Out Target',
        category: 'Profit Targets',
        desc: 'R-multiple target to take partial position profits',
        unit: 'R',
        formatVal: (v) => `<span class="val-pill val-metric">${escapeHtml(v)}R</span>`
    },
    'regime_filter_enabled': {
        name: 'Macro Regime Filter',
        category: 'Trend Filter',
        desc: 'Gates entries and adjusts exits based on macro regime volatility',
        isBool: true
    }
};

const ENGINE_METADATA = {
    'decoder': {
        name: 'Exit Decoder',
        tier: 'L1 Auto-Apply',
        icon: '🛡️',
        className: 'engine-decoder',
        tooltip: 'Exit Governance L1: Autonomous rule execution based on exit branch telemetry'
    },
    'adaptive': {
        name: 'Adaptive Engine',
        tier: 'L0 Autonomous',
        icon: '⚡',
        className: 'engine-adaptive',
        tooltip: 'Adaptive Feedback Engine: Real-time autonomous threshold nudges'
    },
    'learning': {
        name: 'Learning Engine',
        tier: 'L2 Strategic',
        icon: '🧠',
        className: 'engine-learning',
        tooltip: 'Nightly Strategic Learning Pipeline: 30-day pattern analysis'
    },
    'operator': {
        name: 'Operator',
        tier: 'Manual',
        icon: '👤',
        className: 'engine-operator',
        tooltip: 'Manual human operator override'
    }
};

function formatFieldInfo(fieldName) {
    if (!fieldName) {
        return { name: '—', category: 'General', desc: 'Configuration parameter', code: '' };
    }
    const raw = String(fieldName).trim();
    if (FIELD_METADATA[raw]) {
        return {
            ...FIELD_METADATA[raw],
            code: raw
        };
    }
    // Dynamic fallback for unmapped parameter fields
    let category = 'Configuration';
    let clean = raw;
    if (clean.startsWith('threshold_coordination.')) {
        category = 'Thresholds';
        clean = clean.replace('threshold_coordination.', '');
    } else if (clean.startsWith('adaptive_pnl.')) {
        category = 'Adaptive PnL';
        clean = clean.replace('adaptive_pnl.', '');
    }
    const name = clean
        .replace(/_/g, ' ')
        .replace(/([a-z])([A-Z])/g, '$1 $2')
        .replace(/\b\w/g, l => l.toUpperCase());

    return {
        name,
        category,
        desc: `Configuration parameter (${category})`,
        code: raw
    };
}

function formatFieldCell(fieldName) {
    const meta = formatFieldInfo(fieldName);
    const catClass = 'cat-' + meta.category.toLowerCase().replace(/[^a-z0-9]/g, '');
    return `
        <div class="field-cell" title="${escapeHtml(meta.desc)}">
            <div class="field-title-row">
                <span class="field-title">${escapeHtml(meta.name)}</span>
                <span class="field-cat ${catClass}">${escapeHtml(meta.category)}</span>
            </div>
            <code class="field-code">${escapeHtml(meta.code)}</code>
            <div class="field-desc">${escapeHtml(meta.desc)}</div>
        </div>
    `;
}

function formatValDetailed(val, fieldName) {
    if (val === null || val === undefined || val === '') {
        return '<span class="val-empty">—</span>';
    }

    const meta = FIELD_METADATA[fieldName];
    if (meta && typeof meta.formatVal === 'function') {
        return meta.formatVal(val);
    }

    // Boolean check
    if (val === true || val === 'true' || (meta && meta.isBool && (val === 1 || val === '1'))) {
        return '<span class="val-pill val-on"><span class="pill-dot">●</span> True (Enabled)</span>';
    }
    if (val === false || val === 'false' || (meta && meta.isBool && (val === 0 || val === '0'))) {
        return '<span class="val-pill val-off"><span class="pill-dot">○</span> False (Disabled)</span>';
    }

    // Duration fallback
    if (String(fieldName).includes('max_hold_hours')) {
        const num = Number(val);
        if (!isNaN(num)) {
            if (num === 0) return '<span class="val-pill val-off">0 hrs (No Limit)</span>';
            if (num === 24) return '<span class="val-pill val-on">24 hrs (1 Day)</span>';
            return `<span class="val-pill val-on">${num} hrs</span>`;
        }
    }

    // Number check for ATR or percentage
    if (String(fieldName).includes('_atr')) {
        return `<span class="val-pill val-metric">${escapeHtml(val)}× ATR</span>`;
    }
    if (String(fieldName).includes('threshold') || String(fieldName).includes('net') || String(fieldName).includes('pct')) {
        const num = Number(val);
        if (!isNaN(num) && num > 0 && num < 1) {
            return `<span class="val-pill val-metric">${(num * 100).toFixed(2)}%</span>`;
        }
    }

    if (typeof val === 'object') {
        return `<code class="val-code">${escapeHtml(JSON.stringify(val))}</code>`;
    }
    return `<span class="val-pill val-plain">${escapeHtml(String(val))}</span>`;
}

function formatEngineBadge(engine) {
    const key = String(engine || '').toLowerCase().trim();
    const meta = ENGINE_METADATA[key] || {
        name: key ? key.charAt(0).toUpperCase() + key.slice(1) : 'System',
        tier: 'Automated',
        icon: '⚙️',
        className: 'engine-generic',
        tooltip: 'Automated strategy engine'
    };

    return `
        <div class="engine-badge ${meta.className}" title="${escapeHtml(meta.tooltip)}">
            <span class="engine-icon">${meta.icon}</span>
            <div class="engine-text">
                <span class="engine-name">${escapeHtml(meta.name)}</span>
                <span class="engine-tier">${escapeHtml(meta.tier)}</span>
            </div>
        </div>
    `;
}

function formatDecisionBadge(decision, engine, status) {
    const d = String(decision || '').toLowerCase().trim();
    if (d === 'keep') {
        return `<span class="chip chip-keep" title="Profit Factor verification passed — change confirmed in live runtime">✓ Keep (PF Passed)</span>`;
    }
    if (d === 'rollback') {
        return `<span class="chip chip-rollback" title="Profit Factor degraded >10% — change automatically rolled back">✕ Rollback</span>`;
    }
    if (d === 'watch') {
        return `<span class="chip chip-watch" title="Currently being monitored by live Profit Factor gate">⏱ Monitoring (Watch)</span>`;
    }
    if (d === 'approved') {
        return `<span class="chip chip-approved" title="Explicitly approved by operator">✓ Approved</span>`;
    }
    if (d === 'modified') {
        return `<span class="chip chip-modified" title="Modified by operator before applying">✎ Modified</span>`;
    }
    if (d === 'rejected') {
        return `<span class="chip chip-rejected" title="Rejected by operator">✕ Rejected</span>`;
    }

    // When decision is empty or unreviewed:
    const eng = String(engine || '').toLowerCase().trim();
    if (eng === 'decoder') {
        return `<span class="chip chip-auto-gov" title="Autonomous L1 rule: Auto-applied under exit governance policy (no manual review required)">⚡ Auto-Governance</span>`;
    }
    if (eng === 'adaptive') {
        return `<span class="chip chip-auto-adapt" title="Autonomous L0 adjustment: Real-time feedback nudge">⚡ Autonomous</span>`;
    }
    if (status === 'applied') {
        return `<span class="chip chip-auto" title="Automatically applied by governance pipeline">⚡ Auto-Applied</span>`;
    }
    if (status === 'pending_review') {
        return `<span class="chip chip-watch" title="Awaiting operator review on Learning page">⏳ Pending Review</span>`;
    }
    return `<span class="chip chip-neutral" title="Autonomous governance rule">Auto</span>`;
}

function formatStatusBadge(status) {
    const s = String(status || '').toLowerCase().trim();
    if (s === 'applied') {
        return `<span class="chip chip-applied">● Applied</span>`;
    }
    if (s === 'reverted') {
        return `<span class="chip chip-reverted">✕ Reverted</span>`;
    }
    if (s === 'stale') {
        return `<span class="chip chip-neutral">⊘ Stale</span>`;
    }
    return chip(status, status || '—');
}

function formatTimestamp(ts) {
    if (!ts) return '—';
    const cleanTs = String(ts).slice(0, 19).replace('T', ' ');
    const d = new Date(ts);
    let relative = '';
    if (!isNaN(d.getTime())) {
        const diffMs = Date.now() - d.getTime();
        const diffMins = Math.floor(diffMs / 60000);
        if (diffMins < 1) relative = 'Just now';
        else if (diffMins < 60) relative = `${diffMins}m ago`;
        else {
            const diffHours = Math.floor(diffMins / 60);
            if (diffHours < 24) relative = `${diffHours}h ago`;
            else {
                const diffDays = Math.floor(diffHours / 24);
                relative = `${diffDays}d ago`;
            }
        }
    }
    return `
        <div class="time-cell" title="Full timestamp: ${escapeHtml(ts)}">
            <span class="time-main">${escapeHtml(cleanTs)}</span>
            ${relative ? `<span class="time-rel">${escapeHtml(relative)}</span>` : ''}
        </div>
    `;
}

function formatSymbol(sym) {
    if (!sym || sym === '—') return '—';
    return `<span class="symbol-pill symbol-${escapeHtml(sym.toLowerCase())}">${escapeHtml(sym)}</span>`;
}

function toggleLedgerDetail(docId) {
    if (expandedLedgerRows.has(docId)) {
        expandedLedgerRows.delete(docId);
    } else {
        expandedLedgerRows.add(docId);
    }
    filterLedger();
}
window.toggleLedgerDetail = toggleLedgerDetail;

function renderLedgerRows(changes) {
    const tbody = document.getElementById('ledger-body');
    if (!tbody) return;

    if (!changes.length) {
        tbody.innerHTML = `<tr><td colspan="9" class="empty-state">No applied-changes ledger records match the selected filters.</td></tr>`;
        return;
    }

    const html = [];
    changes.forEach((c, idx) => {
        const docId = c.doc_id || `change-${idx}`;
        const isExpanded = expandedLedgerRows.has(docId);
        const meta = formatFieldInfo(c.field);

        html.push(`
            <tr class="ledger-row ${isExpanded ? 'row-expanded' : ''}" onclick="toggleLedgerDetail('${escapeHtml(docId)}')">
                <td class="col-expand">
                    <span class="expand-btn ${isExpanded ? 'open' : ''}">▶</span>
                </td>
                <td>${formatTimestamp(c.applied_at)}</td>
                <td>${formatSymbol(c.symbol)}</td>
                <td>${formatEngineBadge(c.engine)}</td>
                <td>${formatFieldCell(c.field)}</td>
                <td>${formatValDetailed(c.old_value, c.field)}</td>
                <td>${formatValDetailed(c.new_value, c.field)}</td>
                <td>${formatDecisionBadge(c.decision, c.engine, c.status)}</td>
                <td>${formatStatusBadge(c.status)}</td>
            </tr>
        `);

        if (isExpanded) {
            const evidenceHtml = c.evidence && Object.keys(c.evidence).length
                ? `<pre class="detail-json">${escapeHtml(JSON.stringify(c.evidence, null, 2))}</pre>`
                : '<span class="text-muted">No additional telemetry payload attached.</span>';

            html.push(`
                <tr class="ledger-detail-row">
                    <td colspan="9">
                        <div class="ledger-detail-card">
                            <div class="detail-grid">
                                <div class="detail-section">
                                    <h4>💡 Parameter Rationale</h4>
                                    <p class="detail-desc">${escapeHtml(meta.desc)}</p>
                                    <div class="detail-meta-item">
                                        <span class="detail-label">Configuration Key:</span>
                                        <code>${escapeHtml(c.field || '—')}</code>
                                    </div>
                                    <div class="detail-meta-item">
                                        <span class="detail-label">Category / Domain:</span>
                                        <span class="badge-cat">${escapeHtml(meta.category)}</span>
                                    </div>
                                </div>
                                <div class="detail-section">
                                    <h4>🎯 Trigger Reason &amp; Authority</h4>
                                    <div class="detail-meta-item">
                                        <span class="detail-label">Reason:</span>
                                        <span>${escapeHtml(c.reason || 'Phase 9 Governance Auto-Apply')}</span>
                                    </div>
                                    <div class="detail-meta-item">
                                        <span class="detail-label">Owner / Tier:</span>
                                        <span>${escapeHtml(c.owner || c.engine || 'governance')}</span>
                                    </div>
                                    <div class="detail-meta-item">
                                        <span class="detail-label">Config Fingerprint:</span>
                                        <code>${escapeHtml(c.config_fingerprint || '—')}</code>
                                    </div>
                                    <div class="detail-meta-item">
                                        <span class="detail-label">Document ID:</span>
                                        <span class="detail-id">${escapeHtml(docId)}</span>
                                    </div>
                                </div>
                                <div class="detail-section detail-evidence-col">
                                    <h4>📊 Telemetry &amp; Evidence Payload</h4>
                                    ${evidenceHtml}
                                </div>
                            </div>
                        </div>
                    </td>
                </tr>
            `);
        }
    });

    tbody.innerHTML = html.join('');
}

function filterLedger() {
    const search = (document.getElementById('ledger-search')?.value || '').toLowerCase().trim();
    const engineFilter = (document.getElementById('ledger-engine-filter')?.value || '').toLowerCase().trim();
    const symbolFilter = (document.getElementById('ledger-symbol-filter')?.value || '').toUpperCase().trim();

    const filtered = rawLedgerData.filter(c => {
        if (engineFilter && String(c.engine || '').toLowerCase() !== engineFilter) {
            return false;
        }
        if (symbolFilter && String(c.symbol || '').toUpperCase() !== symbolFilter) {
            return false;
        }
        if (search) {
            const fieldMeta = formatFieldInfo(c.field);
            const matchesField = String(c.field || '').toLowerCase().includes(search);
            const matchesName = fieldMeta.name.toLowerCase().includes(search);
            const matchesCat = fieldMeta.category.toLowerCase().includes(search);
            const matchesSymbol = String(c.symbol || '').toLowerCase().includes(search);
            const matchesEngine = String(c.engine || '').toLowerCase().includes(search);
            const matchesReason = String(c.reason || '').toLowerCase().includes(search);
            if (!matchesField && !matchesName && !matchesCat && !matchesSymbol && !matchesEngine && !matchesReason) {
                return false;
            }
        }
        return true;
    });

    const badge = document.getElementById('ledger-count-badge');
    if (badge) {
        badge.textContent = `${filtered.length} of ${rawLedgerData.length} records`;
    }

    renderLedgerRows(filtered);
}
window.filterLedger = filterLedger;

function resetLedgerFilters() {
    const searchInput = document.getElementById('ledger-search');
    if (searchInput) searchInput.value = '';
    const engineSelect = document.getElementById('ledger-engine-filter');
    if (engineSelect) engineSelect.value = '';
    const symbolSelect = document.getElementById('ledger-symbol-filter');
    if (symbolSelect) symbolSelect.value = '';
    filterLedger();
}
window.resetLedgerFilters = resetLedgerFilters;

function populateLedgerSymbolOptions(changes) {
    const select = document.getElementById('ledger-symbol-filter');
    if (!select) return;
    const currentVal = select.value;
    const symbols = Array.from(new Set(changes.map(c => String(c.symbol || '').toUpperCase()).filter(Boolean))).sort();
    
    select.innerHTML = '<option value="">All Symbols</option>' +
        symbols.map(s => `<option value="${s}" ${s === currentVal ? 'selected' : ''}>${s}</option>`).join('');
}

async function loadLedger() {
    const tbody = document.getElementById('ledger-body');
    try {
        const data = await fetchJSON(API_ENDPOINTS.GOVERNANCE.APPLIED_CHANGES('', 100));
        const changes = data.changes || [];
        rawLedgerData = changes;
        document.getElementById('stat-ledger').textContent = changes.length;

        populateLedgerSymbolOptions(changes);
        filterLedger();
    } catch (error) {
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="9" class="empty-state">Error loading ledger: ${escapeHtml(error.message)}</td></tr>`;
        }
    }
}

/* ---------------------------- adaptive actions ---------------------------- */

const ACTION_METADATA = {
    'intraday_shield': {
        label: 'Intraday Pattern Shield',
        shortLabel: 'Intraday Shield',
        icon: '🛡️',
        desc: 'Protects against adverse intraday patterns (ORB breakout, gap fade, morning reversal)',
        thresholdType: 'gate',
        badgeClass: 'badge-shield'
    },
    'peak_giveback_lock': {
        label: 'Peak Giveback Lock',
        shortLabel: 'Peak Giveback',
        icon: '📉',
        desc: 'Protects unrealized peak profit against sudden reversal from highs',
        thresholdType: 'r_multiple',
        badgeClass: 'badge-lock'
    },
    'break_even_lock': {
        label: 'Break-Even Stop Lock',
        shortLabel: 'Break-Even Lock',
        icon: '🔒',
        desc: 'Moves stop loss to entry price after initial gain milestone',
        thresholdType: 'r_multiple',
        badgeClass: 'badge-breakeven'
    },
    'scale_out': {
        label: 'Partial Scale-Out',
        shortLabel: 'Scale-Out',
        icon: '💰',
        desc: 'Takes partial profit at predetermined R-multiple targets',
        thresholdType: 'r_multiple',
        badgeClass: 'badge-scaleout'
    },
    'pyramid_add': {
        label: 'Pyramid Position Add',
        shortLabel: 'Pyramid Add',
        icon: '📈',
        desc: 'Adds position size to winning trade milestone',
        thresholdType: 'r_multiple',
        badgeClass: 'badge-pyramid'
    },
    'time_decay_exit': {
        label: 'Time Decay Exit',
        shortLabel: 'Time Decay',
        icon: '⏳',
        desc: 'Exits or tightens stop after max holding duration reached',
        thresholdType: 'days',
        badgeClass: 'badge-timedecay'
    },
    'trailing_stop': {
        label: 'Trailing Stop Loss',
        shortLabel: 'Trailing Stop',
        icon: '🎯',
        desc: 'Trails stop loss dynamically as price advances',
        thresholdType: 'atr',
        badgeClass: 'badge-trailing'
    },
    'stop_loss': {
        label: 'Stop Loss Exit',
        shortLabel: 'Stop Loss',
        icon: '🛑',
        desc: 'Standard protective stop loss triggered',
        thresholdType: 'price',
        badgeClass: 'badge-stoploss'
    },
    'take_profit': {
        label: 'Take Profit Exit',
        shortLabel: 'Take Profit',
        icon: '🎯',
        desc: 'Target profit milestone reached',
        thresholdType: 'price',
        badgeClass: 'badge-takeprofit'
    }
};

function formatActionType(actionType) {
    if (!actionType) return { label: '—', shortLabel: '—', icon: '⚡', desc: '', thresholdType: 'val', badgeClass: 'badge-neutral' };
    const key = String(actionType).toLowerCase().trim();
    if (ACTION_METADATA[key]) {
        return ACTION_METADATA[key];
    }
    // Dynamic fallback for any unmapped or future action types
    const cleanedLabel = key
        .replace(/_/g, ' ')
        .replace(/([a-z])([A-Z])/g, '$1 $2')
        .replace(/\b\w/g, l => l.toUpperCase());

    return { label: cleanedLabel, shortLabel: cleanedLabel, icon: '⚡', desc: '', thresholdType: 'val', badgeClass: 'badge-neutral' };
}

function formatAdaptiveThreshold(val, actionType) {
    if (val === null || val === undefined || isNaN(val)) return '—';
    const num = Number(val);
    const meta = formatActionType(actionType);
    if (meta.thresholdType === 'r_multiple') {
        return `${num.toFixed(2)}R`;
    }
    if (meta.thresholdType === 'days') {
        return `${num} days`;
    }
    if (meta.thresholdType === 'gate') {
        return `${num.toFixed(1)}x Gate`;
    }
    if (num > 0 && num < 1) {
        return `${(num * 100).toFixed(1)}%`;
    }
    return `${num.toFixed(2)}`;
}

let rawAdaptiveActions = [];
let rawAdaptiveStats = [];
let expandedAdaptiveDocs = new Set();
let selectedActionFilter = '';

function toggleAdaptiveDetail(docId) {
    if (expandedAdaptiveDocs.has(docId)) {
        expandedAdaptiveDocs.delete(docId);
    } else {
        expandedAdaptiveDocs.add(docId);
    }
    renderAdaptiveTable();
}

function selectAdaptiveActionCard(actionType) {
    const actionSelect = document.getElementById('adaptive-action-filter');
    if (selectedActionFilter === actionType) {
        selectedActionFilter = '';
        if (actionSelect) actionSelect.value = '';
    } else {
        selectedActionFilter = actionType;
        if (actionSelect) actionSelect.value = actionType;
    }
    filterAdaptiveActions();
}

function populateAdaptiveDropdowns(actions, stats) {
    const symSelect = document.getElementById('adaptive-symbol-filter');
    const actSelect = document.getElementById('adaptive-action-filter');
    if (!symSelect || !actSelect) return;

    const currentSym = symSelect.value;
    const currentAct = actSelect.value;

    const symbols = new Set();
    actions.forEach(a => {
        if (a.symbol) symbols.add(a.symbol);
    });

    const sortedSymbols = Array.from(symbols).sort();
    symSelect.innerHTML = '<option value="">All Symbols</option>' +
        sortedSymbols.map(s => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join('');
    if (currentSym && symbols.has(currentSym)) {
        symSelect.value = currentSym;
    }

    const actionTypes = new Set();
    stats.forEach(s => { if (s.action_type) actionTypes.add(s.action_type); });
    actions.forEach(a => { if (a.action_type) actionTypes.add(a.action_type); });

    const sortedActions = Array.from(actionTypes).sort();
    actSelect.innerHTML = '<option value="">All Actions</option>' +
        sortedActions.map(at => {
            const meta = formatActionType(at);
            return `<option value="${escapeHtml(at)}">${meta.icon} ${escapeHtml(meta.shortLabel || meta.label)}</option>`;
        }).join('');
    if (currentAct && actionTypes.has(currentAct)) {
        actSelect.value = currentAct;
    }
}

function renderAdaptiveStatsGrid() {
    const statsBox = document.getElementById('adaptive-stats');
    if (!statsBox) return;

    if (!rawAdaptiveStats.length) {
        statsBox.innerHTML = `<div class="empty-state">No adaptive engine actions recorded.</div>`;
        return;
    }

    statsBox.innerHTML = rawAdaptiveStats.map(s => {
        const meta = formatActionType(s.action_type);
        const isActive = selectedActionFilter === s.action_type;
        const winPercent = s.win_rate !== null ? (s.win_rate * 100).toFixed(0) + '%' : '—';
        const isPositiveWin = s.win_rate !== null && s.win_rate >= 0.5;

        return `
        <div class="adaptive-stat ${isActive ? 'active' : ''}" onclick="selectAdaptiveActionCard('${escapeHtml(s.action_type)}')">
            <div class="adaptive-stat-header">
                <span class="stat-icon">${meta.icon}</span>
                <div class="a-type" title="${escapeHtml(s.action_type)}">${escapeHtml(meta.shortLabel || meta.label)}</div>
            </div>
            <div class="stat-desc">${escapeHtml(meta.desc || 'Dynamic intraday threshold evaluation.')}</div>
            <div class="adaptive-stat-footer">
                <div class="stat-badge stat-fired"><strong>${s.fired}</strong> fired</div>
                <div class="stat-badge stat-resolved"><strong>${s.resolved}</strong> resolved</div>
                <div class="stat-badge stat-winrate ${isPositiveWin ? 'win-pos' : ''}">
                    win ${winPercent}
                </div>
            </div>
        </div>`;
    }).join('');
}

function filterAdaptiveActions() {
    const searchInput = document.getElementById('adaptive-search');
    const actionSelect = document.getElementById('adaptive-action-filter');
    const symSelect = document.getElementById('adaptive-symbol-filter');
    const statusSelect = document.getElementById('adaptive-status-filter');

    const q = (searchInput ? searchInput.value : '').toLowerCase().trim();
    const actionFilter = actionSelect ? actionSelect.value : '';
    const symFilter = symSelect ? symSelect.value : '';
    const statusFilter = statusSelect ? statusSelect.value : '';

    selectedActionFilter = actionFilter;
    renderAdaptiveStatsGrid();

    let filtered = rawAdaptiveActions.filter(a => {
        if (actionFilter && a.action_type !== actionFilter) return false;
        if (symFilter && a.symbol !== symFilter) return false;

        const outcome = a.outcome || {};
        const isResolved = !!outcome.resolved;
        if (statusFilter === 'pending' && isResolved) return false;
        if (statusFilter === 'resolved' && !isResolved) return false;

        if (q) {
            const ctx = a.context || {};
            const reason = String(ctx.reason || '').toLowerCase();
            const actionType = String(a.action_type || '').toLowerCase();
            const sym = String(a.symbol || '').toLowerCase();
            const orderId = String(a.entry_order_id || ctx.position_id || '').toLowerCase();
            const regime = String(a.macro_regime || ctx.macro_regime || '').toLowerCase();

            if (!reason.includes(q) && !actionType.includes(q) && !sym.includes(q) && !orderId.includes(q) && !regime.includes(q)) {
                return false;
            }
        }
        return true;
    });

    const badge = document.getElementById('adaptive-count-badge');
    if (badge) {
        badge.textContent = `${filtered.length} action${filtered.length === 1 ? '' : 's'}`;
    }

    renderAdaptiveTable(filtered);
}

function resetAdaptiveFilters() {
    const searchInput = document.getElementById('adaptive-search');
    const actionSelect = document.getElementById('adaptive-action-filter');
    const symSelect = document.getElementById('adaptive-symbol-filter');
    const statusSelect = document.getElementById('adaptive-status-filter');

    if (searchInput) searchInput.value = '';
    if (actionSelect) actionSelect.value = '';
    if (symSelect) symSelect.value = '';
    if (statusSelect) statusSelect.value = '';

    selectedActionFilter = '';
    filterAdaptiveActions();
}

function renderAdaptiveTable(actions = null) {
    const tbody = document.getElementById('adaptive-body');
    if (!tbody) return;

    const list = actions !== null ? actions : rawAdaptiveActions;
    if (!list.length) {
        tbody.innerHTML = `<tr><td colspan="8" class="empty-state">No matching adaptive actions found.</td></tr>`;
        return;
    }

    let rowsHtml = '';
    list.forEach(a => {
        const docId = a.doc_id || `${a.symbol}_${a.created_at}_${a.action_type}`;
        const outcome = a.outcome || {};
        const resolved = !!outcome.resolved;
        const meta = formatActionType(a.action_type);
        const ctx = a.context || {};
        const triggerReason = ctx.reason || '';
        const isExpanded = expandedAdaptiveDocs.has(docId);

        // Date & time formatting
        const rawDate = String(a.created_at || '');
        const datePart = rawDate.slice(0, 10);
        const timePart = rawDate.slice(11, 19);

        // Macro regime badge
        const macroRegime = a.macro_regime || ctx.macro_regime || 'NEUTRAL';

        // Threshold formatting
        const formattedThreshold = formatAdaptiveThreshold(a.threshold_used, a.action_type);

        // Outcome P&L formatting
        let pnlHtml = '—';
        if (resolved && outcome.final_pnl !== undefined && outcome.final_pnl !== null) {
            const num = Number(outcome.final_pnl);
            const isPos = num >= 0;
            const rVal = outcome.realized_r !== undefined && outcome.realized_r !== null ? ` (${Number(outcome.realized_r) >= 0 ? '+' : ''}${Number(outcome.realized_r).toFixed(2)}R)` : '';
            pnlHtml = `<span class="${isPos ? 'pnl-pos' : 'pnl-neg'}">${isPos ? '+' : ''}$${num.toFixed(2)}${rVal}</span>`;
        } else if (!resolved) {
            pnlHtml = `<span class="text-muted" style="font-size: 11px;">In-Flight</span>`;
        }

        // Price calculations for drawer
        const entryPrice = ctx.entry_price !== undefined ? Number(ctx.entry_price) : null;
        const currentPrice = ctx.current_price !== undefined ? Number(ctx.current_price) : null;
        let deltaHtml = '';
        if (entryPrice && currentPrice) {
            const diff = currentPrice - entryPrice;
            const pct = (diff / entryPrice) * 100;
            const diffPos = diff >= 0;
            deltaHtml = `
            <div class="field-item">
                <span class="field-label">Trigger Delta:</span>
                <span class="field-val ${diffPos ? 'pnl-pos' : 'pnl-neg'}">${diffPos ? '+' : ''}$${diff.toFixed(2)} (${diffPos ? '+' : ''}${pct.toFixed(2)}%)</span>
            </div>`;
        }

        const orderId = a.entry_order_id || ctx.position_id || '—';

        // Main table row
        rowsHtml += `
        <tr class="interactive-row ${isExpanded ? 'expanded-row' : ''}" onclick="toggleAdaptiveDetail('${escapeHtml(docId)}')">
            <td class="toggle-cell">
                <span class="accordion-arrow">${isExpanded ? '▼' : '▶'}</span>
            </td>
            <td class="timestamp-cell">
                <div class="date-main">${escapeHtml(datePart)}</div>
                <div class="time-sub">${escapeHtml(timePart)}</div>
            </td>
            <td class="symbol-cell">
                <span class="symbol-tag symbol-${escapeHtml(String(a.symbol || '').toLowerCase())}">${escapeHtml(a.symbol || '—')}</span>
            </td>
            <td class="action-cell">
                <div class="action-title-row">
                    <span class="action-icon">${meta.icon}</span>
                    <strong class="action-name">${escapeHtml(meta.label)}</strong>
                    <code class="action-raw">${escapeHtml(a.action_type)}</code>
                </div>
                ${triggerReason ? `<div class="trigger-reason-pill" title="${escapeHtml(triggerReason)}">⚡ ${escapeHtml(triggerReason)}</div>` : `<div class="action-desc-sub">${escapeHtml(meta.desc)}</div>`}
            </td>
            <td class="threshold-cell">
                <span class="val-pill">${escapeHtml(formattedThreshold)}</span>
            </td>
            <td class="regime-cell">
                <span class="regime-badge regime-${escapeHtml(macroRegime.toLowerCase())}">${escapeHtml(macroRegime)}</span>
            </td>
            <td class="status-cell">
                ${resolved ? '<span class="status-chip status-chip-resolved">✅ Resolved</span>' : '<span class="status-chip status-chip-pending">⏳ In-Flight</span>'}
            </td>
            <td class="pnl-cell">
                ${pnlHtml}
            </td>
        </tr>`;

        // Drawer row if expanded
        if (isExpanded) {
            rowsHtml += `
            <tr class="drawer-row">
                <td colspan="8" class="drawer-cell">
                    <div class="ledger-detail-panel adaptive-detail-panel">
                        <div class="adaptive-drawer-grid">
                            <!-- Card 1: Trigger & Diagnostics -->
                            <div class="adaptive-drawer-card">
                                <div class="card-header">
                                    <span class="card-icon">🎯</span>
                                    <strong>Trigger Diagnostics</strong>
                                </div>
                                <div class="card-body">
                                    <div class="field-item field-item-stacked">
                                        <span class="field-label">Trigger Reason:</span>
                                        <span class="field-val highlight-reason">${escapeHtml(triggerReason || meta.desc || 'Pattern condition met')}</span>
                                    </div>
                                    <div class="field-item">
                                        <span class="field-label">Action Category:</span>
                                        <span class="field-val"><span class="engine-badge ${meta.badgeClass}">${meta.icon} ${escapeHtml(meta.label)}</span></span>
                                    </div>
                                    <div class="field-item">
                                        <span class="field-label">Position / Order ID:</span>
                                        <span class="field-val"><code>${escapeHtml(orderId)}</code></span>
                                    </div>
                                    <div class="field-item">
                                        <span class="field-label">Triggered At:</span>
                                        <span class="field-val">${escapeHtml(rawDate.slice(0, 19).replace('T', ' '))}</span>
                                    </div>
                                </div>
                            </div>

                            <!-- Card 2: Price & Volatility Snapshot -->
                            <div class="adaptive-drawer-card">
                                <div class="card-header">
                                    <span class="card-icon">📊</span>
                                    <strong>Execution Metrics</strong>
                                </div>
                                <div class="card-body">
                                    <div class="field-item">
                                        <span class="field-label">Entry Price:</span>
                                        <span class="field-val">${entryPrice !== null ? '$' + entryPrice.toFixed(2) : '—'}</span>
                                    </div>
                                    <div class="field-item">
                                        <span class="field-label">Trigger Price:</span>
                                        <span class="field-val">${currentPrice !== null ? '$' + currentPrice.toFixed(2) : '—'}</span>
                                    </div>
                                    ${deltaHtml}
                                    <div class="field-item">
                                        <span class="field-label">Hourly ATR:</span>
                                        <span class="field-val">${ctx.atr !== undefined && ctx.atr !== null ? '$' + Number(ctx.atr).toFixed(3) : '—'}</span>
                                    </div>
                                    <div class="field-item">
                                        <span class="field-label">Threshold Applied:</span>
                                        <span class="field-val"><span class="val-pill">${escapeHtml(formattedThreshold)}</span></span>
                                    </div>
                                </div>
                            </div>

                            <!-- Card 3: Macro & Strategy Context -->
                            <div class="adaptive-drawer-card">
                                <div class="card-header">
                                    <span class="card-icon">🌐</span>
                                    <strong>Strategy Context</strong>
                                </div>
                                <div class="card-body">
                                    <div class="field-item">
                                        <span class="field-label">Macro Regime:</span>
                                        <span class="field-val"><span class="regime-badge regime-${escapeHtml(macroRegime.toLowerCase())}">${escapeHtml(macroRegime)}</span></span>
                                    </div>
                                    <div class="field-item">
                                        <span class="field-label">Archetype Source:</span>
                                        <span class="field-val">${escapeHtml(ctx.fundamental_source || 'Technical / Rules Engine')}</span>
                                    </div>
                                    ${ctx.fundamental_bucket ? `
                                    <div class="field-item">
                                        <span class="field-label">Fundamentals Bucket:</span>
                                        <span class="field-val">${escapeHtml(ctx.fundamental_bucket)}</span>
                                    </div>` : ''}
                                    ${ctx.bars_held !== undefined ? `
                                    <div class="field-item">
                                        <span class="field-label">Bars Held:</span>
                                        <span class="field-val">${ctx.bars_held} bars</span>
                                    </div>` : ''}
                                </div>
                            </div>

                            <!-- Card 4: Trade Outcome & Closed Feedback -->
                            <div class="adaptive-drawer-card">
                                <div class="card-header">
                                    <span class="card-icon">🏁</span>
                                    <strong>Trade Resolution &amp; Feedback</strong>
                                </div>
                                <div class="card-body">
                                    <div class="field-item">
                                        <span class="field-label">Status:</span>
                                        <span class="field-val">${resolved ? '<span class="status-chip status-chip-resolved">✅ Resolved</span>' : '<span class="status-chip status-chip-pending">⏳ In-Flight (Open Trade)</span>'}</span>
                                    </div>
                                    ${resolved ? `
                                    <div class="field-item">
                                        <span class="field-label">Final P&amp;L:</span>
                                        <span class="field-val ${outcome.final_pnl >= 0 ? 'pnl-pos' : 'pnl-neg'}">${outcome.final_pnl >= 0 ? '+' : ''}$${Number(outcome.final_pnl).toFixed(2)}</span>
                                    </div>
                                    ${outcome.realized_r !== null && outcome.realized_r !== undefined ? `
                                    <div class="field-item">
                                        <span class="field-label">Realized R:</span>
                                        <span class="field-val ${outcome.realized_r >= 0 ? 'pnl-pos' : 'pnl-neg'}">${outcome.realized_r >= 0 ? '+' : ''}${Number(outcome.realized_r).toFixed(2)}R</span>
                                    </div>` : ''}
                                    ${outcome.exit_reason ? `
                                    <div class="field-item field-item-stacked">
                                        <span class="field-label">Closed Exit Reason:</span>
                                        <span class="field-val highlight-reason">${escapeHtml(outcome.exit_reason)}</span>
                                    </div>` : ''}
                                    ` : `
                                    <div class="in-flight-note">
                                        <p>⚡ This action was triggered during an active position. Once the bot closes the trade, final P&amp;L and win rate will be linked to evaluate threshold adjustments.</p>
                                    </div>
                                    `}
                                </div>
                            </div>
                        </div>
                    </div>
                </td>
            </tr>`;
        }
    });

    tbody.innerHTML = rowsHtml;
}

async function loadAdaptive() {
    const statsBox = document.getElementById('adaptive-stats');
    const tbody = document.getElementById('adaptive-body');
    try {
        const data = await fetchJSON(API_ENDPOINTS.GOVERNANCE.ADAPTIVE_ACTIONS(100));
        rawAdaptiveActions = data.actions || [];
        rawAdaptiveStats = data.stats || [];

        const countBadge = document.getElementById('stat-adaptive');
        if (countBadge) {
            countBadge.textContent = rawAdaptiveActions.length;
        }

        const adaptiveCountBadge = document.getElementById('adaptive-count-badge');
        if (adaptiveCountBadge) {
            adaptiveCountBadge.textContent = `${rawAdaptiveActions.length} action${rawAdaptiveActions.length === 1 ? '' : 's'}`;
        }

        populateAdaptiveDropdowns(rawAdaptiveActions, rawAdaptiveStats);
        renderAdaptiveStatsGrid();
        filterAdaptiveActions();
    } catch (error) {
        if (statsBox) {
            statsBox.innerHTML = `<div class="empty-state">Error loading adaptive actions: ${escapeHtml(error.message)}</div>`;
        }
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="8" class="empty-state">Error loading actions: ${escapeHtml(error.message)}</td></tr>`;
        }
    }
}

/* --------------------------- telemetry archive --------------------------- */

let rawTelemetrySessions = [];

const EXIT_BRANCH_METADATA = {
    'overnight_gap': {
        name: 'Overnight Gap Protection',
        category: 'Safety & Gap',
        icon: '🌙',
        priority: 1,
        desc: 'Protects position if opening gap exceeds tolerable loss limit'
    },
    'emergency': {
        name: 'Emergency Circuit Breaker',
        category: 'Emergency',
        icon: '🚨',
        priority: 2,
        desc: 'Instant kill-switch exit triggered on severe market dislocation or flash drop'
    },
    'stop_loss': {
        name: 'Hard Stop Loss',
        category: 'Risk & Stops',
        icon: '🛑',
        priority: 3,
        desc: 'Standard hard stop-loss protective threshold'
    },
    'atr_stop': {
        name: 'Dynamic ATR Stop',
        category: 'Volatility Stop',
        icon: '📐',
        priority: 4,
        desc: 'Volatility-adjusted stop boundary derived from Average True Range multiples'
    },
    'trailing_stop': {
        name: 'Profit Trailing Stop',
        category: 'Profit Lock',
        icon: '📈',
        priority: 5,
        desc: 'Dynamic trailing stop ratcheting upward with price to lock in accrued gains'
    },
    'regime_flip': {
        name: 'Macro Regime Shift',
        category: 'Trend & Macro',
        icon: '🔄',
        priority: 6,
        desc: 'Exits trade when macro regime classification flips unfavorable'
    },
    'sma_slope': {
        name: 'SMA Slope Reversal',
        category: 'Trend Filter',
        icon: '📉',
        priority: 7,
        desc: 'Exits trade when 200 SMA slope reverses against position direction'
    },
    'profit_target': {
        name: 'Take Profit Target',
        category: 'Profit Targets',
        icon: '🎯',
        priority: 8,
        desc: 'Target profit milestone reached (percentage or ATR-coordinated target)'
    },
    'max_hold': {
        name: 'Max Holding Duration',
        category: 'Time Exit',
        icon: '⏳',
        priority: 9,
        desc: 'Opportunity-cost time exit when trade reaches maximum allowed hours'
    },
    'time_decay': {
        name: 'Time Decay Exit',
        category: 'Time Exit',
        icon: '⏳',
        priority: 9,
        desc: 'Liquidates or tightens stops on stale positions after max hold duration'
    },
    'intraday_shield': {
        name: 'Intraday Pattern Shield',
        category: 'Pattern Shield',
        icon: '🛡️',
        priority: 5,
        desc: 'Protects position against adverse intraday patterns (ORB breakout, gap fade, morning reversal)'
    },
    'intraday_pattern': {
        name: 'Intraday Pattern Shield',
        category: 'Pattern Shield',
        icon: '🛡️',
        priority: 5,
        desc: 'Protects position against adverse intraday patterns (ORB breakout, gap fade, morning reversal)'
    },
    'giveback': {
        name: 'Peak Giveback Protection',
        category: 'Profit Lock',
        icon: '📉',
        priority: 5,
        desc: 'Protects unrealized peak profit against sudden reversal from highs'
    },
    'break_even': {
        name: 'Break-Even Stop Lock',
        category: 'Risk & Stops',
        icon: '🔒',
        priority: 4,
        desc: 'Moves stop loss to entry price after initial gain milestone'
    },
    'hold': {
        name: 'Position Holding State',
        category: 'Hold State',
        icon: '⏸️',
        priority: 10,
        desc: 'Default cascade state maintained while position remains within target bounds'
    }
};

function formatBranchStatus(status, reason = '') {
    const s = String(status || '').toLowerCase().trim();
    if (s === 'fired') {
        return `<span class="chip chip-fired" title="This branch actively executed a trade exit in this session">🟢 Fired</span>`;
    }
    if (s === 'eligible') {
        return `<span class="chip chip-eligible" title="Branch was active and evaluated in cascade">🔵 Eligible</span>`;
    }
    if (s === 'dead') {
        return `<span class="chip chip-dead" title="Evaluated in cascade, but threshold condition was never met">🟣 Evaluated</span>`;
    }
    if (s === 'shadowed') {
        return `<span class="chip chip-shadowed" title="Not reached; preempted by higher-priority rule or conditions not tested">🟡 Shadowed</span>`;
    }
    if (s === 'ineligible') {
        return `<span class="chip chip-ineligible" title="${escapeHtml(reason || 'Branch disabled in configuration')}">⚪ Ineligible</span>`;
    }
    return chip(status, status ? status.toUpperCase() : '—');
}

function formatBranchCell(branchKey) {
    const meta = EXIT_BRANCH_METADATA[branchKey] || {
        name: branchKey.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase()),
        category: 'Exit Rule',
        icon: '⚡',
        priority: 99,
        desc: 'Exit cascade rule'
    };

    return `
        <div class="branch-cell" title="${escapeHtml(meta.desc)}">
            <div class="branch-title-row">
                <span class="branch-icon">${meta.icon}</span>
                <span class="branch-name">${escapeHtml(meta.name)}</span>
                <span class="branch-priority" title="Cascade Priority Rank: ${meta.priority}">P${meta.priority}</span>
            </div>
            <div class="branch-sub-row">
                <code class="branch-code">${escapeHtml(branchKey)}</code>
                <span class="branch-desc">${escapeHtml(meta.desc)}</span>
            </div>
        </div>
    `;
}

function formatCountPill(count, isFired = false) {
    const num = Number(count || 0);
    if (num > 0) {
        if (isFired) {
            return `<span class="count-pill count-fired" title="${num} trade exit(s) triggered">🎯 ${num} fired</span>`;
        }
        return `<span class="count-pill count-active" title="${num} cascade checks">⚡ ${num}</span>`;
    }
    return `<span class="count-pill count-zero">0</span>`;
}

function formatProximityMeter(proximity) {
    const proxNum = Math.max(0, Math.min(1, Number(proximity || 0)));
    const proxPct = Math.round(proxNum * 100);

    let meterClass = 'prox-low';
    let statusLabel = 'Safe';
    if (proxPct >= 100) {
        meterClass = 'prox-fired';
        statusLabel = 'Triggered';
    } else if (proxPct >= 80) {
        meterClass = 'prox-high';
        statusLabel = 'Near-Miss';
    } else if (proxPct >= 40) {
        meterClass = 'prox-mid';
        statusLabel = 'Tested';
    } else if (proxPct > 0) {
        meterClass = 'prox-low';
        statusLabel = 'Approached';
    }

    return `
        <div class="prox-container" title="Peak Proximity: ${(proxNum * 100).toFixed(1)}% reached toward exit threshold (${statusLabel})">
            <div class="prox-bar-bg">
                <div class="prox-bar-fill ${meterClass}" style="width: ${Math.max(proxPct, proxNum > 0 ? 5 : 0)}%;"></div>
            </div>
            <div class="prox-meta">
                <span class="prox-val ${proxNum > 0 ? 'prox-has-val' : ''}">${proxNum.toFixed(2)}</span>
                <span class="prox-pct">(${proxPct}%)</span>
                ${proxNum > 0 ? `<span class="prox-tag ${meterClass}">${statusLabel}</span>` : ''}
            </div>
        </div>
    `;
}

function renderTelemetrySessions(sessions) {
    const container = document.getElementById('telemetry-container');
    if (!container) return;

    if (!sessions.length) {
        container.innerHTML = `<div class="empty-state">No archived exit-telemetry sessions match the selected filters.</div>`;
        return;
    }

    container.innerHTML = sessions.map(s => {
        const symbol = String(s.symbol || '—').toUpperCase();
        const archivedRaw = s.archived_at || '';
        const archivedFormatted = archivedRaw ? String(archivedRaw).slice(0, 19).replace('T', ' ') : '—';

        let relativeTime = '';
        if (archivedRaw) {
            const d = new Date(archivedRaw);
            if (!isNaN(d.getTime())) {
                const diffMs = Date.now() - d.getTime();
                const diffMins = Math.floor(diffMs / 60000);
                if (diffMins < 60) relativeTime = `${diffMins}m ago`;
                else {
                    const diffHours = Math.floor(diffMins / 60);
                    if (diffHours < 24) relativeTime = `${diffHours}h ago`;
                    else relativeTime = `${Math.floor(diffHours / 24)}d ago`;
                }
            }
        }

        const branches = (s.inventory || {}).branches || {};
        let branchKeys = Object.keys(branches);

        // Sort by cascade priority
        branchKeys.sort((a, b) => {
            const pa = (EXIT_BRANCH_METADATA[a] && EXIT_BRANCH_METADATA[a].priority) || 99;
            const pb = (EXIT_BRANCH_METADATA[b] && EXIT_BRANCH_METADATA[b].priority) || 99;
            return pa - pb;
        });

        // Compute session aggregate stats
        let totalFired = 0;
        let totalEvaluated = 0;
        let maxProximity = 0;
        let firedBranches = [];

        branchKeys.forEach(branch => {
            const b = branches[branch] || {};
            const f = Number(b.fired || 0);
            const e = Number(b.evaluated || 0);
            const p = Number(b.proximity || 0);
            if (f > 0) {
                totalFired += f;
                firedBranches.push(branch);
            }
            totalEvaluated += e;
            if (p > maxProximity) maxProximity = p;
        });

        const branchRows = branchKeys.map(branch => {
            const b = branches[branch] || {};
            const statusStr = String(b.status || 'shadowed').toLowerCase();
            const statusBadge = formatBranchStatus(statusStr, b.ineligible_reason);

            return `
                <tr class="branch-row">
                    <td>${formatBranchCell(branch)}</td>
                    <td>${statusBadge}</td>
                    <td>${formatCountPill(b.evaluated, false)}</td>
                    <td>${formatCountPill(b.fired, true)}</td>
                    <td>${formatProximityMeter(b.proximity)}</td>
                </tr>`;
        }).join('');

        return `
            <div class="gov-card telemetry-card">
                <div class="gov-header">
                    <div class="gov-header-left">
                        <div class="gov-symbol">📊 ${escapeHtml(symbol)}</div>
                        <div class="session-stats-badges">
                            <span class="session-badge badge-branches">${branchKeys.length} Branches</span>
                            <span class="session-badge ${totalFired > 0 ? 'badge-fired' : 'badge-neutral'}">
                                ${totalFired > 0 ? `🎯 ${totalFired} Fired (${firedBranches.map(b => (EXIT_BRANCH_METADATA[b]?.name || b)).join(', ')})` : '0 Fired'}
                            </span>
                            <span class="session-badge ${totalEvaluated > 0 ? 'badge-active' : 'badge-neutral'}">
                                ⚡ ${totalEvaluated} Total Evaluations
                            </span>
                            <span class="session-badge ${maxProximity > 0.8 ? 'badge-alert' : (maxProximity > 0 ? 'badge-prox' : 'badge-neutral')}">
                                Max Proximity: ${(maxProximity * 100).toFixed(0)}%
                            </span>
                        </div>
                    </div>
                    <div class="gov-intent">
                        <span class="archive-label">Session Archived:</span>
                        <strong class="archive-date">${escapeHtml(archivedFormatted)}</strong>
                        ${relativeTime ? `<span class="archive-rel">(${escapeHtml(relativeTime)})</span>` : ''}
                    </div>
                </div>

                <div class="table-scroll">
                    <table class="data-table telemetry-table">
                        <thead>
                            <tr>
                                <th style="width: 32%;" title="Exit cascade rule name, priority order, and description">Exit Cascade Rule</th>
                                <th style="width: 20%;" title="Branch reachability status in this session">Reachability Status</th>
                                <th style="width: 12%;" title="Number of times cascade evaluated this branch">Evaluated</th>
                                <th style="width: 12%;" title="Number of times this branch triggered an actual exit">Fired</th>
                                <th style="width: 24%;" title="Highest percentage price approached triggering this rule">Peak Threshold Proximity</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${branchRows || '<tr><td colspan="5" class="empty-state">No branch inventory available for this session.</td></tr>'}
                        </tbody>
                    </table>
                </div>
            </div>`;
    }).join('');
}

function filterTelemetrySessions() {
    const search = (document.getElementById('telemetry-search')?.value || '').toLowerCase().trim();
    const symbolFilter = (document.getElementById('telemetry-symbol-filter')?.value || '').toUpperCase().trim();

    const filtered = rawTelemetrySessions.filter(s => {
        if (symbolFilter && String(s.symbol || '').toUpperCase() !== symbolFilter) {
            return false;
        }
        if (search) {
            const symMatch = String(s.symbol || '').toLowerCase().includes(search);
            const branches = (s.inventory || {}).branches || {};
            const branchMatch = Object.keys(branches).some(b => {
                const meta = EXIT_BRANCH_METADATA[b];
                return b.toLowerCase().includes(search) || (meta && meta.name.toLowerCase().includes(search));
            });
            if (!symMatch && !branchMatch) {
                return false;
            }
        }
        return true;
    });

    const badge = document.getElementById('telemetry-count-badge');
    if (badge) {
        badge.textContent = `${filtered.length} of ${rawTelemetrySessions.length} sessions`;
    }

    renderTelemetrySessions(filtered);
}
window.filterTelemetrySessions = filterTelemetrySessions;

function resetTelemetryFilters() {
    const searchInput = document.getElementById('telemetry-search');
    if (searchInput) searchInput.value = '';
    const symbolSelect = document.getElementById('telemetry-symbol-filter');
    if (symbolSelect) symbolSelect.value = '';
    filterTelemetrySessions();
}
window.resetTelemetryFilters = resetTelemetryFilters;

function populateTelemetrySymbols(sessions) {
    const select = document.getElementById('telemetry-symbol-filter');
    if (!select) return;
    const currentVal = select.value;
    const symbols = Array.from(new Set(sessions.map(s => String(s.symbol || '').toUpperCase()).filter(Boolean))).sort();

    select.innerHTML = '<option value="">All Symbols</option>' +
        symbols.map(s => `<option value="${s}" ${s === currentVal ? 'selected' : ''}>${s}</option>`).join('');
}

async function loadTelemetry() {
    const container = document.getElementById('telemetry-container');
    try {
        const data = await fetchJSON(API_ENDPOINTS.GOVERNANCE.EXIT_TELEMETRY_SNAPSHOTS('', 50));
        const sessions = data.sessions || [];
        rawTelemetrySessions = sessions;
        document.getElementById('stat-sessions').textContent = sessions.length;

        populateTelemetrySymbols(sessions);
        filterTelemetrySessions();
    } catch (error) {
        if (container) {
            container.innerHTML = `<div class="empty-state">Error loading sessions: ${escapeHtml(error.message)}</div>`;
        }
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