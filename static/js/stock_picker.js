/**
 * StockPicker Tab - Dual-Track Leads, Evidence Dossier & HITL Gateway
 *
 * Features:
 * - Dual-Track: Track 1 (Growth Equity) and Track 2 (Covered Call Income)
 * - State-Aware: Previously accepted/provisioned stocks are stamped PROVISIONED
 *   with direct bot links, while fresh leads are promoted into review slots.
 * - Multi-Dimensional Evidence Dossier: Clickable SEC link, XAI thesis (3 bull / 3 risk),
 *   institutional data, and options yield metrics.
 * - HITL Provisioning Gateway: Accept & Provision Bot modal with capital allocation,
 *   Reject modal with structured reason tags and 7-day cooldown.
 */

// =============================================================================
// STATE MANAGEMENT
// =============================================================================

let stockPickerRefreshInterval = null;
let isRunningStockPicker = false;
let lastTabSwitchTime = 0;
let rawStockPickerData = null;
let currentTrackFilter = 'all';
let selectedSymbolForAction = null;
let selectedTrackForAction = null;


// =============================================================================
// TOAST NOTIFICATIONS
// =============================================================================

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 15px 20px;
        background: ${type === 'success' ? '#10b981' : type === 'error' ? '#ef4444' : type === 'warning' ? '#f59e0b' : '#3b82f6'};
        color: white;
        border-radius: 8px;
        box-shadow: 0 10px 15px -3px rgba(0,0,0,0.4);
        z-index: 10002;
        max-width: 420px;
        animation: slideIn 0.3s ease-out;
        font-size: 14px;
        line-height: 1.5;
    `;
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => {
        toast.style.animation = 'slideOut 0.3s ease-in';
        setTimeout(() => toast.remove(), 300);
    }, 5000);
}

if (!document.getElementById('toast-styles')) {
    const style = document.createElement('style');
    style.id = 'toast-styles';
    style.textContent = `
        @keyframes slideIn {
            from { transform: translateX(400px); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        @keyframes slideOut {
            from { transform: translateX(0); opacity: 1; }
            to { transform: translateX(400px); opacity: 0; }
        }
        .dossier-drawer {
            background: #0f172a;
            border-left: 3px solid #38bdf8;
            padding: 16px 20px;
            font-size: 0.9em;
            color: #cbd5e1;
        }
        .dossier-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 16px;
            margin-top: 10px;
        }
        .dossier-card {
            background: #1e293b;
            border-radius: 8px;
            padding: 12px 14px;
            border: 1px solid #334155;
        }
        .dossier-card h4 {
            margin: 0 0 8px 0;
            font-size: 0.95em;
            color: #94a3b8;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
    `;
    document.head.appendChild(style);
}


// =============================================================================
// MANUAL TRIGGER (Run Now)
// =============================================================================

async function runStockPickerNow() {
    if (isRunningStockPicker) {
        showToast('StockPicker is already running. Please wait...', 'warning');
        return;
    }

    const runButton = document.getElementById('run-stockpicker-btn');
    if (runButton) {
        runButton.disabled = true;
        runButton.innerHTML = '⏳ Running...';
    }

    isRunningStockPicker = true;
    showToast('🔄 Scanning dual-track markets with Gemini Flash & alternative data...', 'info');

    try {
        const response = await fetch('/api/stock-picks/run', {
            method: 'POST'
        });

        if (!response.ok) {
            if (response.status === 429) {
                throw new Error('Rate limit exceeded: 10 runs per hour.');
            } else if (response.status === 409) {
                throw new Error('StockPicker is already running in another session.');
            } else {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
        }

        const result = await response.json();

        if (result.status === 'success') {
            const pickCount = result.pick_count || 0;
            const duration = result.duration_seconds || 0;
            showToast(`✅ Generated ${pickCount} picks in ${duration}s`, 'success');
            loadStockPicks();
        } else {
            showToast(`❌ ${result.message || 'StockPicker run failed'}`, 'error');
        }

    } catch (error) {
        console.error('Failed to run StockPicker:', error);
        showToast(`❌ Failed to run StockPicker: ${error.message}`, 'error');
    } finally {
        isRunningStockPicker = false;
        if (runButton) {
            runButton.disabled = false;
            runButton.innerHTML = '▶️ Run Now';
        }
    }
}


// =============================================================================
// DATA FETCHING & FILTERING
// =============================================================================

async function loadStockPicks() {
    const tbody = document.getElementById('stock-picks-tbody');
    if (tbody && !rawStockPickerData) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" style="text-align:center; padding: 20px; color: #95a5a6;">
                    ⏳ Loading dual-track stock picks...
                </td>
            </tr>
        `;
    }

    try {
        const response = await fetch('/api/stock-picks');
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();
        rawStockPickerData = data;

        updateStockPickerMetrics(data);
        renderStockPicksTable();

    } catch (error) {
        console.error('Failed to load stock picks:', error);
        if (tbody) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="8" style="text-align:center; color: #ef4444; padding: 20px;">
                        <strong>Failed to load stock picks</strong><br>
                        <span style="font-size: 0.9em;">${error.message}</span>
                    </td>
                </tr>
            `;
        }
    }
}

function filterStockPicks(filterType) {
    currentTrackFilter = filterType;
    document.querySelectorAll('.sp-tab-btn').forEach(btn => {
        btn.classList.remove('active');
        btn.style.background = '#1a202c';
        btn.style.color = '#cbd5e0';
    });

    const activeBtn = document.getElementById(`sp-filter-${filterType}`);
    if (activeBtn) {
        activeBtn.classList.add('active');
        activeBtn.style.background = '#2d3748';
        activeBtn.style.color = '#ffffff';
    }

    renderStockPicksTable();
}


// =============================================================================
// UI RENDERING
// =============================================================================

function updateStockPickerMetrics(data) {
    const growthCount = data.summary?.growth_actionable_count ?? (data.growth_picks ? data.growth_picks.length : 0);
    const incomeCount = data.summary?.income_actionable_count ?? (data.income_picks ? data.income_picks.length : 0);
    const acceptedCount = data.summary?.already_accepted_count ?? (data.already_accepted ? data.already_accepted.length : 0);

    const elGrowth = document.getElementById('sp-growth-count');
    if (elGrowth) elGrowth.textContent = growthCount;

    const elIncome = document.getElementById('sp-income-count');
    if (elIncome) elIncome.textContent = incomeCount;

    const elAccepted = document.getElementById('sp-accepted-count');
    if (elAccepted) elAccepted.textContent = acceptedCount;

    const elTabAccepted = document.getElementById('sp-tab-accepted-num');
    if (elTabAccepted) elTabAccepted.textContent = acceptedCount;

    const elLastRun = document.getElementById('sp-last-run');
    if (elLastRun && data.run_timestamp) {
        try {
            const runDate = new Date(data.run_timestamp);
            elLastRun.textContent = runDate.toLocaleString('en-US', {
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
            });
        } catch (e) {
            elLastRun.textContent = data.run_timestamp;
        }
    }
}

function renderStockPicksTable() {
    const tbody = document.getElementById('stock-picks-tbody');
    if (!tbody || !rawStockPickerData) return;

    const data = rawStockPickerData;
    let listToRender = [];

    const growthList = data.growth_picks || (data.picks || []).filter(p => p.strategy_track === 'GROWTH' && p.status === 'PENDING_REVIEW');
    const incomeList = data.income_picks || (data.picks || []).filter(p => p.strategy_track === 'INCOME' && p.status === 'PENDING_REVIEW');
    const alreadyAccepted = data.already_accepted || (data.picks || []).filter(p => p.status === 'PROVISIONED' || p.status === 'ACCEPTED');

    if (currentTrackFilter === 'growth') {
        listToRender = growthList;
    } else if (currentTrackFilter === 'income') {
        listToRender = incomeList;
    } else if (currentTrackFilter === 'accepted') {
        listToRender = alreadyAccepted;
    } else {
        // 'all': Show actionable leads first, followed by already provisioned
        listToRender = [...growthList, ...incomeList, ...alreadyAccepted];
    }

    if (!listToRender || listToRender.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" style="text-align:center; padding: 40px; color: #94a3b8;">
                    <div style="font-size: 1.1em; margin-bottom: 8px;">No candidates in this view</div>
                    <div style="font-size: 0.9em; color: #38bdf8;">Click "▶️ Run Now" to scan fresh market opportunities</div>
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = listToRender.map((pick, index) => {
        const rank = index + 1;
        const sym = pick.ticker || pick.symbol || '-';
        const track = pick.strategy_track || 'GROWTH';
        const score = pick.composite_score != null ? pick.composite_score.toFixed(1) : (pick.score != null ? pick.score.toFixed(1) : '-');
        const status = pick.status || 'PENDING_REVIEW';
        const catalyst = pick.catalyst || pick.fundamental_reasons || 'Algorithmically identified opportunity';
        const dossier = pick.evidence_dossier || {};

        const trackBadge = track === 'INCOME'
            ? `<span style="background: rgba(16,185,129,0.15); color: #34d399; border: 1px solid #059669; padding: 3px 8px; border-radius: 12px; font-size: 0.75em; font-weight: 600;">🛡️ Income</span>`
            : `<span style="background: rgba(129,140,248,0.15); color: #a5b4fc; border: 1px solid #4f46e5; padding: 3px 8px; border-radius: 12px; font-size: 0.75em; font-weight: 600;">🚀 Growth</span>`;

        let statusBadge = '';
        let actionButtons = '';

        if (status === 'PROVISIONED') {
            const botId = pick.bot_id || `${sym.lower()}_sma`;
            statusBadge = `<span style="background: rgba(16,185,129,0.2); color: #10b981; border: 1px solid #10b981; padding: 4px 10px; border-radius: 12px; font-size: 0.8em; font-weight: 600;">🤖 ${pick.status_label || 'Bot Active'}</span>`;
            actionButtons = `
                <a href="${pick.dashboard_link || `/bot/${botId}`}" style="display: inline-block; padding: 6px 12px; border-radius: 6px; background: #3b82f6; color: white; text-decoration: none; font-size: 0.85em; font-weight: 600;">
                    📊 View Bot Focus
                </a>
            `;
        } else if (status === 'ACCEPTED') {
            statusBadge = `<span style="background: rgba(59,130,246,0.2); color: #60a5fa; border: 1px solid #3b82f6; padding: 4px 10px; border-radius: 12px; font-size: 0.8em; font-weight: 600;">🔵 Accepted</span>`;
            actionButtons = `
                <button onclick="openAcceptModal('${sym}', '${track}')" style="padding: 6px 12px; border-radius: 6px; border: none; background: #10b981; color: white; font-size: 0.85em; font-weight: 600; cursor: pointer;">
                    🚀 Provision Bot
                </button>
            `;
        } else if (status === 'REJECTED') {
            statusBadge = `<span style="background: rgba(239,68,68,0.2); color: #f87171; border: 1px solid #ef4444; padding: 4px 10px; border-radius: 12px; font-size: 0.8em; font-weight: 600;">🔴 Rejected</span>`;
            actionButtons = `<span style="font-size: 0.8em; color: #94a3b8;">In 7-day cooldown</span>`;
        } else {
            // PENDING_REVIEW
            statusBadge = `<span style="background: rgba(245,158,11,0.2); color: #fbbf24; border: 1px solid #f59e0b; padding: 4px 10px; border-radius: 12px; font-size: 0.8em; font-weight: 600;">🟡 Actionable Lead</span>`;
            actionButtons = `
                <div style="display: flex; gap: 6px; justify-content: center;">
                    <button onclick="openAcceptModal('${sym}', '${track}')" title="Accept & Provision Bot" style="padding: 5px 10px; border-radius: 5px; border: none; background: #10b981; color: white; font-size: 0.8em; font-weight: 600; cursor: pointer;">
                        ✅ Accept
                    </button>
                    <button onclick="openRejectModal('${sym}')" title="Reject candidate" style="padding: 5px 10px; border-radius: 5px; border: none; background: #ef4444; color: white; font-size: 0.8em; font-weight: 600; cursor: pointer;">
                        ❌ Reject
                    </button>
                </div>
            `;
        }

        const metricsHtml = formatPickMetrics(pick);

        return `
            <tr style="border-bottom: 1px solid #334155;">
                <td style="text-align: center; padding: 12px 6px;"><strong>${rank}</strong></td>
                <td style="padding: 12px 8px;">${trackBadge}</td>
                <td style="text-align: center; padding: 12px 8px;"><strong style="color: #38bdf8; font-size: 1.1em;">${sym}</strong></td>
                <td style="text-align: right; font-weight: 700; color: #10b981; padding: 12px 8px;">${score}</td>
                <td style="text-align: center; padding: 12px 8px;">${statusBadge}</td>
                <td style="padding: 12px 10px; font-size: 0.9em; max-width: 320px;" title="${catalyst}">
                    ${catalyst.length > 80 ? catalyst.substring(0, 80) + '...' : catalyst}
                </td>
                <td style="padding: 12px 10px; font-size: 0.85em;">
                    <div>${metricsHtml}</div>
                    <div style="margin-top: 4px;">
                        <button onclick="toggleDossier('${sym}')" style="background: transparent; border: 1px solid #475569; color: #38bdf8; border-radius: 4px; padding: 2px 8px; font-size: 0.8em; cursor: pointer;">
                            🔍 Evidence Dossier
                        </button>
                    </div>
                </td>
                <td style="text-align: center; padding: 12px 8px;">
                    ${actionButtons}
                </td>
            </tr>
            <tr id="dossier-row-${sym}" style="display: none;">
                <td colspan="8" style="padding: 0;">
                    <div class="dossier-drawer">
                        <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #334155; padding-bottom: 8px;">
                            <div>
                                <strong style="color: #38bdf8; font-size: 1.05em;">Multi-Dimensional Evidence Dossier: ${sym}</strong>
                                <span style="margin-left: 10px; font-size: 0.85em; color: #94a3b8;">${dossier.industry || pick.industry || ''}</span>
                            </div>
                            <div>
                                <a href="${dossier.sec_filing_url || 'https://www.sec.gov/edgar/searchedgar/companysearch'}" target="_blank" rel="noopener noreferrer" style="color: #38bdf8; text-decoration: underline; font-weight: 600; font-size: 0.85em;">
                                    🔗 Clickable SEC EDGAR Filing
                                </a>
                            </div>
                        </div>

                        <div class="dossier-grid">
                            <!-- Bull Drivers -->
                            <div class="dossier-card">
                                <h4 style="color: #34d399;">🐂 Key Bull Drivers</h4>
                                <ul style="margin: 0; padding-left: 18px; font-size: 0.85em; color: #f1f5f9;">
                                    ${(dossier.bull_drivers || ['Strong revenue growth momentum', 'Institutional order flow alignment']).map(d => `<li style="margin-bottom: 4px;">${d}</li>`).join('')}
                                </ul>
                            </div>

                            <!-- Risk Warnings -->
                            <div class="dossier-card">
                                <h4 style="color: #f87171;">⚠️ Key Risk Warnings</h4>
                                <ul style="margin: 0; padding-left: 18px; font-size: 0.85em; color: #f1f5f9;">
                                    ${(dossier.risk_warnings || ['Market regime volatility sensitivity', 'Macro cyclical exposure']).map(r => `<li style="margin-bottom: 4px;">${r}</li>`).join('')}
                                </ul>
                            </div>

                            <!-- Alternative Data & Solvency -->
                            <div class="dossier-card">
                                <h4 style="color: #38bdf8;">🏛️ Alternative Data & Catalysts</h4>
                                <div style="font-size: 0.85em; color: #cbd5e1;">
                                    <div><strong>USAspending Awards:</strong> ${(dossier.usaspending_contracts && dossier.usaspending_contracts.length) ? `${dossier.usaspending_contracts.length} active awards` : 'No recent public federal awards'}</div>
                                    <div style="margin-top: 4px;"><strong>Congressional Trading:</strong> ${(dossier.congressional_trades && dossier.congressional_trades.length) ? `${dossier.congressional_trades.length} filings detected` : 'Neutral insider/congressional flow'}</div>
                                    <div style="margin-top: 4px;"><strong>Solvency Rating:</strong> ${dossier.solvency_rating || 'Adequate'}</div>
                                </div>
                            </div>
                        </div>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

function toggleDossier(sym) {
    const row = document.getElementById(`dossier-row-${sym}`);
    if (row) {
        row.style.display = row.style.display === 'none' ? 'table-row' : 'none';
    }
}

function formatPickMetrics(pick) {
    const metrics = [];
    if (pick.strategy_track === 'INCOME') {
        if (pick.monthly_yield_est != null) {
            metrics.push(`<span style="color: #10b981; font-weight: 600;">Monthly: ${pick.monthly_yield_est}%</span>`);
        }
        if (pick.annualized_yield_est != null) {
            metrics.push(`<span>Ann: ${pick.annualized_yield_est}%</span>`);
        }
        if (pick.implied_volatility != null) {
            metrics.push(`<span>IV: ${pick.implied_volatility}%</span>`);
        }
    } else {
        if (pick.revenue_yoy != null) {
            const revPercent = (pick.revenue_yoy * 100).toFixed(0);
            const revColor = pick.revenue_yoy > 0 ? '#10b981' : '#ef4444';
            metrics.push(`<span style="color: ${revColor}">Rev: ${revPercent}%</span>`);
        }
        if (pick.gross_margin != null) {
            metrics.push(`GM: ${(pick.gross_margin * 100).toFixed(0)}%`);
        }
        if (pick.debt_to_equity != null) {
            const de = pick.debt_to_equity.toFixed(0);
            const deColor = pick.debt_to_equity < 100 ? '#10b981' : (pick.debt_to_equity > 250 ? '#ef4444' : '#94a3b8');
            metrics.push(`<span style="color: ${deColor}">D/E: ${de}</span>`);
        }
    }

    return metrics.length > 0 ? metrics.join(' | ') : '-';
}


// =============================================================================
// HITL PROVISIONING & REJECT MODALS
// =============================================================================

function openAcceptModal(sym, track) {
    selectedSymbolForAction = sym;
    selectedTrackForAction = track;

    const elSym = document.getElementById('modal-accept-symbol');
    const elTrack = document.getElementById('modal-accept-track');
    const modal = document.getElementById('sp-accept-modal');

    if (elSym) elSym.textContent = sym;
    if (elTrack) elTrack.value = track === 'INCOME' ? 'Track 2: Covered Call Income' : 'Track 1: Growth Equity';
    if (modal) modal.style.display = 'block';
}

function closeAcceptModal() {
    const modal = document.getElementById('sp-accept-modal');
    if (modal) modal.style.display = 'none';
    selectedSymbolForAction = null;
    selectedTrackForAction = null;
}

async function confirmProvisionBot() {
    if (!selectedSymbolForAction) return;

    const sym = selectedSymbolForAction;
    const track = selectedTrackForAction || 'GROWTH';
    const capitalInput = document.getElementById('modal-accept-capital');
    const capital = capitalInput ? parseFloat(capitalInput.value) || 10000 : 10000;

    const btn = document.getElementById('modal-confirm-provision-btn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '⏳ Provisioning...';
    }

    try {
        const response = await fetch('/api/stock-picks/provision-bot', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                symbol: sym,
                strategy_track: track,
                capital_allocation: capital
            })
        });

        if (!response.ok) {
            throw new Error(`Provisioning failed: ${response.statusText}`);
        }

        const res = await response.json();
        showToast(`✅ Successfully provisioned bot ${res.bot_id} (client_id: ${res.client_id})!`, 'success');
        closeAcceptModal();
        loadStockPicks();

    } catch (e) {
        console.error('Error provisioning bot:', e);
        showToast(`❌ Provisioning error: ${e.message}`, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '🚀 Provision Bot Now';
        }
    }
}

function openRejectModal(sym) {
    selectedSymbolForAction = sym;
    const elSym = document.getElementById('modal-reject-symbol');
    const modal = document.getElementById('sp-reject-modal');

    if (elSym) elSym.textContent = sym;
    if (modal) modal.style.display = 'block';
}

function closeRejectModal() {
    const modal = document.getElementById('sp-reject-modal');
    if (modal) modal.style.display = 'none';
    selectedSymbolForAction = null;
}

async function confirmRejectLead() {
    if (!selectedSymbolForAction) return;

    const sym = selectedSymbolForAction;
    const reasonEl = document.getElementById('modal-reject-reason');
    const notesEl = document.getElementById('modal-reject-notes');

    const reason = reasonEl ? reasonEl.value : 'OPERATOR_DISCRETION';
    const notes = notesEl ? notesEl.value : '';

    const btn = document.getElementById('modal-confirm-reject-btn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '⏳ Processing...';
    }

    try {
        const response = await fetch('/api/stock-picks/reject', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                symbol: sym,
                reason: reason,
                notes: notes
            })
        });

        if (!response.ok) {
            throw new Error(`Rejection failed: ${response.statusText}`);
        }

        showToast(`Candidate ${sym} rejected (7-day cooldown applied)`, 'info');
        closeRejectModal();
        loadStockPicks();

    } catch (e) {
        console.error('Error rejecting candidate:', e);
        showToast(`❌ Error rejecting: ${e.message}`, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Confirm Rejection';
        }
    }
}


// =============================================================================
// AUTO-REFRESH & TAB HOOKS
// =============================================================================

function startStockPickerRefresh() {
    const now = Date.now();
    if (now - lastTabSwitchTime < 1000) return;
    lastTabSwitchTime = now;

    if (stockPickerRefreshInterval) {
        clearInterval(stockPickerRefreshInterval);
        stockPickerRefreshInterval = null;
    }

    loadStockPicks();
    stockPickerRefreshInterval = setInterval(loadStockPicks, 60000);
}

function stopStockPickerRefresh() {
    if (stockPickerRefreshInterval) {
        clearInterval(stockPickerRefreshInterval);
        stockPickerRefreshInterval = null;
    }
}

function onStockPickerTabActive() {
    startStockPickerRefresh();
}

function onStockPickerTabInactive() {
    stopStockPickerRefresh();
}


// =============================================================================
// EXPORTS
// =============================================================================

if (typeof window !== 'undefined') {
    window.loadStockPicks = loadStockPicks;
    window.runStockPickerNow = runStockPickerNow;
    window.filterStockPicks = filterStockPicks;
    window.toggleDossier = toggleDossier;
    window.openAcceptModal = openAcceptModal;
    window.closeAcceptModal = closeAcceptModal;
    window.confirmProvisionBot = confirmProvisionBot;
    window.openRejectModal = openRejectModal;
    window.closeRejectModal = closeRejectModal;
    window.confirmRejectLead = confirmRejectLead;
    window.startStockPickerRefresh = startStockPickerRefresh;
    window.stopStockPickerRefresh = stopStockPickerRefresh;
    window.onStockPickerTabActive = onStockPickerTabActive;
    window.onStockPickerTabInactive = onStockPickerTabInactive;
}
