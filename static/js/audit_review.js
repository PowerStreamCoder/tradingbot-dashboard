/**
 * Trade Log Auditor Review Dashboard JavaScript
 * Fetches audit runs, findings, and markdown reports from FastAPI backend.
 */

let allFindings = [];
let allRuns = [];
let activeStatus = 'active'; // 'active' | 'resolved' | 'all'
let selectedCategory = 'all';
let selectedSymbol = 'all';
let searchQuery = '';
let activeTab = 'findings';
let currentReportRunId = null;

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[ch]));
}

function showToast(message, type = 'success') {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = message;
    toast.className = `toast ${type} show`;
    setTimeout(() => toast.classList.remove('show'), 3000);
}

async function fetchJSON(url) {
    const res = await fetch(url);
    if (!res.ok) {
        let errText = `HTTP ${res.status}`;
        try {
            const body = await res.json();
            if (body && body.detail) errText = body.detail;
        } catch (_) {}
        throw new Error(errText);
    }
    return res.json();
}

/**
 * Lightweight deterministic markdown-to-HTML parser for audit reports
 */
function renderMarkdown(md) {
    if (!md) return '<p style="color: #94a3b8;">No report markdown available for this run.</p>';

    const lines = md.split('\n');
    let html = '';
    let inTable = false;
    let tableHeaderDone = false;
    let inCodeBlock = false;
    let codeBlockLang = '';
    let codeBuffer = [];

    for (let i = 0; i < lines.length; i++) {
        let line = lines[i];

        // Code blocks
        if (line.trim().startsWith('```')) {
            if (inCodeBlock) {
                html += `<pre><code>${escapeHtml(codeBuffer.join('\n'))}</code></pre>`;
                inCodeBlock = false;
                codeBuffer = [];
            } else {
                inCodeBlock = true;
                codeBlockLang = line.trim().slice(3).trim();
                codeBuffer = [];
            }
            continue;
        }

        if (inCodeBlock) {
            codeBuffer.push(line);
            continue;
        }

        // Table detection
        if (line.trim().startsWith('|') && line.trim().endsWith('|')) {
            const cells = line.trim().slice(1, -1).split('|').map(c => c.trim());
            // Check if separator line (e.g. |---|---|)
            if (cells.every(c => /^:?-+:?$/.test(c))) {
                tableHeaderDone = true;
                continue;
            }

            if (!inTable) {
                inTable = true;
                tableHeaderDone = false;
                html += '<table><tbody>';
            }

            const tag = !tableHeaderDone ? 'th' : 'td';
            html += '<tr>' + cells.map(c => `<${tag}>${formatInline(c)}</${tag}>`).join('') + '</tr>';
            continue;
        } else if (inTable) {
            html += '</tbody></table>';
            inTable = false;
            tableHeaderDone = false;
        }

        const trimmed = line.trim();
        if (!trimmed) {
            continue;
        }

        // Headers
        if (trimmed.startsWith('### ')) {
            html += `<h3>${formatInline(trimmed.slice(4))}</h3>`;
        } else if (trimmed.startsWith('## ')) {
            html += `<h2>${formatInline(trimmed.slice(3))}</h2>`;
        } else if (trimmed.startsWith('# ')) {
            html += `<h1>${formatInline(trimmed.slice(2))}</h1>`;
        } else if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
            html += `<ul><li>${formatInline(trimmed.slice(2))}</li></ul>`;
        } else if (trimmed.startsWith('> ')) {
            html += `<blockquote>${formatInline(trimmed.slice(2))}</blockquote>`;
        } else {
            html += `<p>${formatInline(trimmed)}</p>`;
        }
    }

    if (inTable) {
        html += '</tbody></table>';
    }
    if (inCodeBlock) {
        html += `<pre><code>${escapeHtml(codeBuffer.join('\n'))}</code></pre>`;
    }

    return html;
}

function formatInline(text) {
    let out = escapeHtml(text);
    // Bold
    out = out.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    // Inline code
    out = out.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Italics
    out = out.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    return out;
}

/* ------------------------------- TAB SWITCHING ------------------------------- */

function switchMainTab(tab) {
    activeTab = tab;
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.dash-filter-group .dash-tab-btn').forEach(btn => btn.classList.remove('active'));

    if (tab === 'findings') {
        const t = document.getElementById('tabFindings');
        if (t) t.classList.add('active');
        const b = document.getElementById('tabBtnFindings');
        if (b) b.classList.add('active');
    } else if (tab === 'latestReport') {
        const t = document.getElementById('tabLatestReport');
        if (t) t.classList.add('active');
        const b = document.getElementById('tabBtnLatestReport');
        if (b) b.classList.add('active');
        if (!currentReportRunId && allRuns.length > 0) {
            renderLatestReport(allRuns[0]);
        }
    } else if (tab === 'runHistory') {
        const t = document.getElementById('tabRunHistory');
        if (t) t.classList.add('active');
        const b = document.getElementById('tabBtnRunHistory');
        if (b) b.classList.add('active');
        renderRunHistory();
    }
}

/* ------------------------------- DATA FETCHING ------------------------------- */

async function loadAllAuditData() {
    const statusText = document.getElementById('syncStatusText');
    if (statusText) statusText.textContent = 'Refreshing...';

    try {
        const [runsData, findingsData] = await Promise.all([
            fetchJSON('/api/audit/runs?limit=50').catch(err => {
                console.warn('Failed to load runs:', err);
                return { runs: [] };
            }),
            fetchJSON('/api/audit/findings?limit=200').catch(err => {
                console.warn('Failed to load findings:', err);
                return { findings: [] };
            })
        ]);

        allRuns = runsData.runs || [];
        allFindings = findingsData.findings || [];

        updateKpiCards();
        renderFindings();
        renderRunHistory();

        if (allRuns.length > 0) {
            renderLatestReport(allRuns[0]);
        } else {
            const reportCont = document.getElementById('latest-run-report-container');
            if (reportCont) reportCont.innerHTML = '<p style="color: #94a3b8;">No audit runs found in Firestore.</p>';
        }

        if (statusText) statusText.textContent = 'Auditor Synchronized';
        showToast('Audit data refreshed', 'success');
    } catch (err) {
        console.error('Error loading audit data:', err);
        if (statusText) statusText.textContent = 'Sync Error';
        showToast(`Failed to load audit data: ${err.message}`, 'error');
    }
}

function updateKpiCards() {
    const lastRun = allRuns[0] || null;

    // Stat 1: Last Run Status
    const statStatus = document.getElementById('stat-last-status');
    if (statStatus) {
        if (!lastRun) {
            statStatus.textContent = 'No Runs';
            statStatus.className = 'value';
        } else if (lastRun.inconclusive) {
            statStatus.innerHTML = '<span class="status-pill-inconclusive">INCONCLUSIVE</span>';
        } else {
            const st = String(lastRun.status || 'PASS').toUpperCase();
            if (st === 'PASS' || st === 'PASSED') {
                statStatus.innerHTML = '<span class="status-pill-pass">PASS</span>';
            } else {
                statStatus.innerHTML = `<span class="status-pill-defects">${escapeHtml(st)}</span>`;
            }
        }
    }

    // Stat 2: Checks Coverage
    const statCoverage = document.getElementById('stat-coverage');
    if (statCoverage) {
        if (!lastRun) {
            statCoverage.textContent = '-';
        } else {
            const exec = lastRun.checks_executed ?? 14;
            const skipMap = lastRun.skipped_checks || {};
            let skipped = typeof skipMap === 'object' ? Object.keys(skipMap).length : 0;
            const outcomes = lastRun.check_outcomes || {};
            const outcomeIds = Object.keys(outcomes);
            if (outcomeIds.length) {
                const ran = outcomeIds.filter(id => (outcomes[id] || {}).outcome === 'ran').length;
                skipped = outcomeIds.length - ran;
                statCoverage.innerHTML = `<span style="color: #38bdf8;">${ran}</span> / ${outcomeIds.length} <span style="font-size: 0.7em; color: #94a3b8;">(${skipped} not run)</span>`;
            } else {
                statCoverage.innerHTML = `<span style="color: #38bdf8;">${exec}</span> / ${exec + skipped} <span style="font-size: 0.7em; color: #94a3b8;">(${skipped} skipped)</span>`;
            }
        }
    }

    // Stat 3 & 4: Active vs Resolved Findings
    let activeCount = 0;
    let resolvedCount = 0;
    allFindings.forEach(f => {
        const s = String(f.status || '').toLowerCase();
        if (s === 'open' || s === 'recurring') activeCount++;
        else if (s === 'resolved') resolvedCount++;
    });

    const statActive = document.getElementById('stat-active-findings');
    if (statActive) statActive.textContent = activeCount;

    const statResolved = document.getElementById('stat-resolved-findings');
    if (statResolved) statResolved.textContent = resolvedCount;

    // Badges in toolbar
    const bFindings = document.getElementById('badge-findings-count');
    if (bFindings) bFindings.textContent = allFindings.length;

    const bRuns = document.getElementById('badge-runs-count');
    if (bRuns) bRuns.textContent = allRuns.length;

    const bActive = document.getElementById('badge-status-active');
    if (bActive) bActive.textContent = activeCount;

    const bResolved = document.getElementById('badge-status-resolved');
    if (bResolved) bResolved.textContent = resolvedCount;

    const bAll = document.getElementById('badge-status-all');
    if (bAll) bAll.textContent = allFindings.length;
}

/* ------------------------------- FINDINGS VIEW ------------------------------- */

function selectAuditStatusTab(btn, status) {
    document.querySelectorAll('#audit-status-tabs .dash-tab-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    activeStatus = status;
    renderFindings();
}

function handleAuditSearch(val) {
    searchQuery = (val || '').trim().toLowerCase();
    const clearBtn = document.getElementById('audit-search-clear-btn');
    if (clearBtn) {
        clearBtn.style.display = searchQuery ? 'block' : 'none';
    }
    renderFindings();
}

function clearAuditSearch() {
    searchQuery = '';
    const input = document.getElementById('audit-search');
    if (input) input.value = '';
    const clearBtn = document.getElementById('audit-search-clear-btn');
    if (clearBtn) clearBtn.style.display = 'none';
    renderFindings();
}

function filterFindings() {
    const catSelect = document.getElementById('filter-audit-category');
    if (catSelect) selectedCategory = catSelect.value;
    const symSelect = document.getElementById('filter-audit-symbol');
    if (symSelect) selectedSymbol = symSelect.value;
    renderFindings();
}

function renderFindings() {
    const container = document.getElementById('findings-container');
    if (!container) return;

    let filtered = allFindings.filter(f => {
        const s = String(f.status || '').toLowerCase();
        if (activeStatus === 'active' && !(s === 'open' || s === 'recurring')) return false;
        if (activeStatus === 'resolved' && s !== 'resolved') return false;

        if (selectedCategory !== 'all') {
            const cat = String(f.category || '').toUpperCase();
            if (cat !== selectedCategory) return false;
        }

        if (selectedSymbol !== 'all') {
            const sym = String(f.symbol || '').toUpperCase();
            if (sym !== selectedSymbol) return false;
        }

        if (searchQuery) {
            const hay = (
                (f.title || '') + ' ' +
                (f.description || '') + ' ' +
                (f.check_id || '') + ' ' +
                (f.symbol || '') + ' ' +
                JSON.stringify(f.evidence || {})
            ).toLowerCase();
            if (!hay.includes(searchQuery)) return false;
        }

        return true;
    });

    if (filtered.length === 0) {
        container.innerHTML = `
            <div style="background: rgba(30, 41, 59, 0.4); border: 1px dashed rgba(255, 255, 255, 0.1); border-radius: 10px; padding: 32px; text-align: center; color: #94a3b8;">
                <div style="font-size: 2em; margin-bottom: 8px;">✅</div>
                <div style="font-size: 1.1em; color: #f8fafc; font-weight: 500;">No Findings Matching Filter</div>
                <p style="margin-top: 6px; font-size: 0.9em;">All trades reconciled cleanly or no findings recorded under this status.</p>
            </div>
        `;
        return;
    }

    let html = '';
    filtered.forEach(f => {
        const sev = String(f.severity || 'low').toLowerCase();
        const sevClass = `badge-${sev}`;
        const status = String(f.status || 'open').toLowerCase();
        const statusClass = `badge-${status}`;
        const cat = String(f.category || 'INTEGRITY').toUpperCase();
        const sym = String(f.symbol || 'ALL').toUpperCase();

        let evidenceStr = '';
        if (f.evidence) {
            try {
                evidenceStr = typeof f.evidence === 'string' ? f.evidence : JSON.stringify(f.evidence, null, 2);
            } catch (_) {
                evidenceStr = String(f.evidence);
            }
        }

        html += `
            <div class="finding-card">
                <div class="finding-header">
                    <div class="finding-title">
                        <span>${escapeHtml(f.title || f.check_id || 'Audit Finding')}</span>
                    </div>
                    <div class="finding-meta">
                        <span class="badge-pill ${sevClass}">${escapeHtml(sev)}</span>
                        <span class="badge-pill ${statusClass}">${escapeHtml(status)}</span>
                        <span class="badge-pill badge-category">${escapeHtml(cat)}</span>
                        <span class="badge-pill badge-symbol">${escapeHtml(sym)}</span>
                    </div>
                </div>
                <div style="color: #cbd5e1; font-size: 0.92em; line-height: 1.5;">
                    ${escapeHtml(f.description || '')}
                </div>
                ${evidenceStr ? `
                    <div class="finding-evidence">
                        <div style="font-size: 0.8em; color: #94a3b8; margin-bottom: 4px; text-transform: uppercase;">Evidence Context:</div>
                        <code>${escapeHtml(evidenceStr)}</code>
                    </div>
                ` : ''}
                <div class="finding-footer">
                    <span><strong>Check ID:</strong> <code>${escapeHtml(f.check_id || 'unknown')}</code></span>
                    <span><strong>Key:</strong> <code>${escapeHtml(f.key || f.finding_id || '')}</code></span>
                    ${f.run_count ? `<span><strong>Seen Runs:</strong> ${f.run_count}</span>` : ''}
                    ${f.first_seen ? `<span><strong>First Seen:</strong> ${escapeHtml(f.first_seen)}</span>` : ''}
                    ${f.last_seen ? `<span><strong>Last Seen:</strong> ${escapeHtml(f.last_seen)}</span>` : ''}
                    ${f.resolved_at ? `<span><strong>Resolved:</strong> ${escapeHtml(f.resolved_at)}</span>` : ''}
                </div>
            </div>
        `;
    });

    container.innerHTML = html;
}

/* ------------------------------- REPORT VIEW ------------------------------- */

async function viewRunReport(runId) {
    switchMainTab('latestReport');
    const container = document.getElementById('latest-run-report-container');
    if (container) container.innerHTML = '<div class="loading">Loading report markdown...</div>';

    try {
        const runDoc = await fetchJSON(`/api/audit/runs/${encodeURIComponent(runId)}`);
        renderLatestReport(runDoc);
    } catch (err) {
        if (container) {
            container.innerHTML = `<div style="color: #ef4444;">Failed to load run report: ${escapeHtml(err.message)}</div>`;
        }
    }
}

function renderCheckOutcomes(outcomes) {
    const container = document.getElementById('latest-run-outcomes');
    if (!container) return;
    if (!outcomes || typeof outcomes !== 'object' || Object.keys(outcomes).length === 0) {
        container.innerHTML = '';
        return;
    }

    const badgeClass = {
        'ran': 'badge-low',
        'skipped': 'badge-medium',
        'insufficient_sample': 'badge-medium',
        'source_unavailable': 'badge-high',
        'error': 'badge-high',
    };
    let rows = '';
    Object.keys(outcomes).sort().forEach(checkId => {
        const info = outcomes[checkId] || {};
        const outcome = String(info.outcome || 'unknown');
        const cls = badgeClass[outcome] || 'badge-medium';
        rows += `
            <tr>
                <td><code>${escapeHtml(checkId)}</code></td>
                <td><span class="badge-pill ${cls}">${escapeHtml(outcome)}</span></td>
                <td>${escapeHtml(String(info.mode || '-'))}</td>
                <td style="color: #94a3b8;">${escapeHtml(String(info.reason || ''))}</td>
            </tr>
        `;
    });
    container.innerHTML = `
        <div style="background: rgba(30, 41, 59, 0.7); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 10px; padding: 16px 20px;">
            <h3 style="margin-top: 0; color: #f8fafc; font-size: 1.1em;">Check Outcomes</h3>
            <p style="color: #94a3b8; font-size: 0.85em; margin-top: 0;">
                Every registered check reports an outcome — a check that did not run is never a clean pass.
            </p>
            <div style="overflow-x: auto;">
                <table class="runs-table">
                    <thead><tr><th>Check</th><th>Outcome</th><th>Mode</th><th>Reason</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        </div>
    `;
}

function renderLatestReport(run) {
    if (!run) return;
    currentReportRunId = run.run_id;

    const meta = document.getElementById('latest-run-meta');
    if (meta) {
        const exec = run.checks_executed ?? 14;
        const skipMap = run.skipped_checks || {};
        const skipped = typeof skipMap === 'object' ? Object.keys(skipMap).length : 0;
        const outcomes = run.check_outcomes || {};
        const notRun = Object.keys(outcomes).filter(
            id => (outcomes[id] || {}).outcome && outcomes[id].outcome !== 'ran'
        ).length;
        meta.innerHTML = `
            <div style="font-size: 1.1em; font-weight: 600; color: #f8fafc;">
                Report for Run: <code>${escapeHtml(run.run_id || 'unknown')}</code>
            </div>
            <span class="badge-pill ${run.inconclusive ? 'badge-medium' : 'badge-low'}">
                ${run.inconclusive ? 'INCONCLUSIVE' : escapeHtml(run.status || 'PASS')}
            </span>
            <span class="badge-pill badge-symbol">Date: ${escapeHtml(run.trade_date || 'latest')}</span>
            <span class="badge-pill badge-category">Executed: ${exec}</span>
            <span class="badge-pill ${skipped > 0 ? 'badge-high' : 'badge-low'}">Skipped: ${skipped}</span>
            <span class="badge-pill ${notRun > 0 ? 'badge-high' : 'badge-low'}">Not run: ${notRun}</span>
        `;
    }

    renderCheckOutcomes(run.check_outcomes);

    const reportCont = document.getElementById('latest-run-report-container');
    if (reportCont) {
        if (run.report_markdown) {
            reportCont.innerHTML = renderMarkdown(run.report_markdown);
        } else {
            reportCont.innerHTML = `
                <div style="color: #94a3b8; padding: 16px;">
                    <h3>Run Summary</h3>
                    <p><strong>Status:</strong> ${escapeHtml(run.status || 'UNKNOWN')}</p>
                    <p><strong>Trade Date:</strong> ${escapeHtml(run.trade_date || 'N/A')}</p>
                    <p><strong>Findings Count:</strong> ${escapeHtml(String(run.findings_count || 0))}</p>
                    <p><strong>Checks Executed:</strong> ${escapeHtml(String(run.checks_executed || 0))}</p>
                    ${run.skipped_checks ? `<pre>${escapeHtml(JSON.stringify(run.skipped_checks, null, 2))}</pre>` : ''}
                </div>
            `;
        }
    }
}

/* ------------------------------- RUN HISTORY VIEW ------------------------------- */

function renderRunHistory() {
    const tbody = document.getElementById('runs-table-body');
    if (!tbody) return;

    if (allRuns.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: #94a3b8;">No historical runs found.</td></tr>';
        return;
    }

    let rows = '';
    allRuns.forEach(r => {
        const exec = r.checks_executed ?? 14;
        const skipMap = r.skipped_checks || {};
        const skipped = typeof skipMap === 'object' ? Object.keys(skipMap).length : 0;
        const findings = r.findings_count ?? (r.summary ? (r.summary.findings_count || 0) : 0);
        const status = r.inconclusive ? 'INCONCLUSIVE' : String(r.status || 'PASS').toUpperCase();
        let statusHtml = '';
        if (r.inconclusive) {
            statusHtml = '<span class="status-pill-inconclusive">INCONCLUSIVE</span>';
        } else if (status === 'PASS' || status === 'PASSED') {
            statusHtml = '<span class="status-pill-pass">PASS</span>';
        } else {
            statusHtml = `<span class="status-pill-defects">${escapeHtml(status)}</span>`;
        }

        rows += `
            <tr>
                <td><strong>${escapeHtml(r.trade_date || '-')}</strong></td>
                <td><code>${escapeHtml(r.run_id || '-')}</code></td>
                <td>${statusHtml}</td>
                <td>${exec}</td>
                <td>${skipped > 0 ? `<span style="color: #fb923c;">${skipped}</span>` : '0'}</td>
                <td>${findings > 0 ? `<span style="color: #f87171; font-weight: 600;">${findings}</span>` : '0'}</td>
                <td style="color: #94a3b8; font-size: 0.85em;">${escapeHtml(r.created_at || '-')}</td>
                <td>
                    <button class="btn btn-action" onclick="viewRunReport('${escapeHtml(r.run_id || '')}')" style="padding: 4px 10px; font-size: 0.82em;">
                        📄 View Report
                    </button>
                </td>
            </tr>
        `;
    });

    tbody.innerHTML = rows;
}

// Initial bootstrap on DOM ready
document.addEventListener('DOMContentLoaded', () => {
    loadAllAuditData();
});
