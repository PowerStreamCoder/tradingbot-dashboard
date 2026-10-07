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
let spSearchQuery = '';


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

    const statusBadge = document.getElementById('sp-run-status-badge');
    if (statusBadge) {
        statusBadge.textContent = '⏳ Running Live Scan...';
        statusBadge.style.background = 'rgba(245, 158, 11, 0.15)';
        statusBadge.style.color = '#f59e0b';
        statusBadge.style.borderColor = 'rgba(245, 158, 11, 0.3)';
    }

    const tbody = document.getElementById('stock-picks-tbody');
    if (tbody) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" class="sp-table-loading" style="padding: 48px 16px; text-align: center;">
                    <div class="sp-spinner"></div>
                    <div style="margin-top: 14px; font-weight: 600; font-size: 1.1em; color: #38bdf8;">🔄 Scanning dual-track markets with live data feeds...</div>
                    <div style="margin-top: 6px; font-size: 0.88em; color: #94a3b8;">Analyzing SEC EDGAR filings, Finnhub fundamentals & live options chains. Results will update automatically.</div>
                </td>
            </tr>
        `;
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
            const fbReasons = result.fallback_reasons || result.status_messages || [];
            if (pickCount > 0) {
                let toastMsg = `✅ Generated ${pickCount} picks in ${duration}s`;
                if (fbReasons.length > 0) {
                    toastMsg += ` (Auto-fallback applied for ${fbReasons.length} track(s))`;
                }
                showToast(toastMsg, 'success');
            } else {
                showToast(`ℹ️ 0 picks generated: ${result.message || 'No candidates met scoring criteria.'}`, 'warning');
            }

            if (statusBadge) {
                const nowTime = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                statusBadge.textContent = `✅ Live Scan: ${nowTime}`;
                statusBadge.style.background = 'rgba(16, 185, 129, 0.15)';
                statusBadge.style.color = '#34d399';
                statusBadge.style.borderColor = 'rgba(16, 185, 129, 0.3)';
            }

            // Immediately render the fresh run results returned from POST /run
            if (result.actionable_leads && result.actionable_leads.length > 0) {
                rawStockPickerData = result;
                updateStockPickerMetrics(result);
                renderStockPicksTable();
            }
            await loadStockPicks(true);
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

async function loadStockPicks(forceRefresh = false) {
    const tbody = document.getElementById('stock-picks-tbody');
    if (tbody && !rawStockPickerData) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" class="sp-table-loading">
                    <div class="sp-spinner"></div>
                    <div style="margin-top: 8px;">Loading dual-track stock picks...</div>
                </td>
            </tr>
        `;
    }

    try {
        const url = forceRefresh ? `/api/stock-picks?_t=${Date.now()}` : '/api/stock-picks';
        const response = await fetch(url, {
            cache: forceRefresh ? 'no-store' : 'default',
            headers: forceRefresh ? { 'Cache-Control': 'no-cache', 'Pragma': 'no-cache' } : {}
        });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();
        rawStockPickerData = data;

        const statusBadge = document.getElementById('sp-run-status-badge');
        if (statusBadge && data.run_timestamp) {
            try {
                const runDate = new Date(data.run_timestamp);
                const timeStr = runDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                statusBadge.textContent = `📋 Saved Run: ${timeStr}`;
                statusBadge.style.background = 'rgba(56, 189, 248, 0.15)';
                statusBadge.style.color = '#38bdf8';
                statusBadge.style.borderColor = 'rgba(56, 189, 248, 0.3)';
            } catch (e) {
                statusBadge.textContent = '📋 Saved Run';
            }
        }

        updateStockPickerMetrics(data);
        renderStockPicksTable();

    } catch (error) {
        console.error('Failed to load stock picks:', error);
        if (tbody) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="8" style="text-align:center; color: #ef4444; padding: 30px;">
                        <strong>Failed to load stock picks</strong><br>
                        <span style="font-size: 0.9em; color: #94a3b8; margin-top: 4px; display: inline-block;">${error.message}</span>
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
        btn.setAttribute('aria-selected', 'false');
    });

    const activeBtn = document.getElementById(`sp-filter-${filterType}`);
    if (activeBtn) {
        activeBtn.classList.add('active');
        activeBtn.setAttribute('aria-selected', 'true');
    }

    renderStockPicksTable();
}

function handleStockPickerSearch(query) {
    spSearchQuery = (query || '').toLowerCase().trim();
    const clearBtn = document.getElementById('sp-search-clear-btn');
    if (clearBtn) {
        clearBtn.style.display = spSearchQuery ? 'block' : 'none';
    }
    renderStockPicksTable();
}

function clearStockPickerSearch() {
    spSearchQuery = '';
    const input = document.getElementById('sp-search-input');
    if (input) input.value = '';
    const clearBtn = document.getElementById('sp-search-clear-btn');
    if (clearBtn) clearBtn.style.display = 'none';
    renderStockPicksTable();
}


// =============================================================================
// UI RENDERING
// =============================================================================

function updateStockPickerMetrics(data) {
    const growthCount = data.summary?.growth_actionable_count ?? (data.growth_picks ? data.growth_picks.length : 0);
    const incomeCount = data.summary?.income_actionable_count ?? (data.income_picks ? data.income_picks.length : 0);
    const acceptedCount = data.summary?.already_accepted_count ?? (data.already_accepted ? data.already_accepted.length : 0);
    const totalCount = growthCount + incomeCount + acceptedCount;

    const elGrowth = document.getElementById('sp-growth-count');
    if (elGrowth) elGrowth.textContent = growthCount;

    const elIncome = document.getElementById('sp-income-count');
    if (elIncome) elIncome.textContent = incomeCount;

    const elAccepted = document.getElementById('sp-accepted-count');
    if (elAccepted) elAccepted.textContent = acceptedCount;

    // Update filter pill badges
    const elTabAll = document.getElementById('sp-tab-all-num');
    if (elTabAll) elTabAll.textContent = totalCount;

    const elTabGrowth = document.getElementById('sp-tab-growth-num');
    if (elTabGrowth) elTabGrowth.textContent = growthCount;

    const elTabIncome = document.getElementById('sp-tab-income-num');
    if (elTabIncome) elTabIncome.textContent = incomeCount;

    const elTabAccepted = document.getElementById('sp-tab-accepted-num');
    if (elTabAccepted) elTabAccepted.textContent = acceptedCount;

    // Data Quality & Degradation Metrics
    const dq = data.overall_data_quality || data.summary?.overall_data_quality || 'FULL';
    const degradedCount = data.summary?.degraded_candidate_count ?? (data.degraded_candidate_count ?? 0);
    const missingSources = data.summary?.missing_sources_detected || data.missing_sources_detected || [];

    const elDqVal = document.getElementById('sp-data-quality-val');
    const elDqCard = document.getElementById('sp-data-quality-card');
    if (elDqVal) {
        elDqVal.textContent = dq;
    }
    if (elDqCard) {
        if (dq === 'DEGRADED') {
            elDqCard.style.background = 'linear-gradient(135deg, #991b1b 0%, #ef4444 100%)';
        } else if (dq === 'PARTIAL' || degradedCount > 0) {
            elDqCard.style.background = 'linear-gradient(135deg, #b45309 0%, #f59e0b 100%)';
        } else {
            elDqCard.style.background = 'linear-gradient(135deg, #065f46 0%, #10b981 100%)';
        }
    }

    const alertBanner = document.getElementById('sp-degradation-alert');
    const alertText = document.getElementById('sp-degradation-alert-text');
    if (alertBanner) {
        if (degradedCount > 0 || dq !== 'FULL') {
            alertBanner.style.display = 'flex';
            if (alertText) {
                const missingTxt = missingSources.length > 0 ? ` (Missing feeds: ${missingSources.join(', ')})` : '';
                alertText.innerHTML = `<strong>Data Degradation Alert:</strong> ${degradedCount} candidate(s) scored with incomplete market or filing feeds${missingTxt}. Scoring factors were adjusted to neutral baseline.`;
            }
        } else {
            alertBanner.style.display = 'none';
        }
    }

    // Auto-fallback / informational pipeline messages banner
    const fallbackBanner = document.getElementById('sp-fallback-info-alert');
    const fallbackText = document.getElementById('sp-fallback-info-text');
    const fallbackReasons = data.fallback_reasons || data.status_messages || data.summary?.fallback_reasons || [];
    const informationalMsg = data.message;

    if (fallbackBanner) {
        if (fallbackReasons.length > 0) {
            fallbackBanner.style.display = 'flex';
            if (fallbackText) {
                const listItems = fallbackReasons.map(r => `<div style="margin-bottom: 3px;">• ${r}</div>`).join('');
                fallbackText.innerHTML = `<strong>Pipeline Notice:</strong><div style="margin-top: 4px;">${listItems}</div>`;
            }
        } else if (informationalMsg && (informationalMsg.includes('fallback') || informationalMsg.includes('Auto-fallback'))) {
            fallbackBanner.style.display = 'flex';
            if (fallbackText) {
                fallbackText.innerHTML = `<strong>Pipeline Notice:</strong> <span>${informationalMsg}</span>`;
            }
        } else {
            fallbackBanner.style.display = 'none';
        }
    }

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

    // Filter by search query if active
    if (spSearchQuery) {
        listToRender = listToRender.filter(pick => {
            const sym = (pick.ticker || pick.symbol || '').toLowerCase();
            const ind = (pick.industry || pick.evidence_dossier?.industry || '').toLowerCase();
            const cat = (pick.catalyst || pick.fundamental_reasons || '').toLowerCase();
            const track = (pick.strategy_track || '').toLowerCase();
            return sym.includes(spSearchQuery) || ind.includes(spSearchQuery) || cat.includes(spSearchQuery) || track.includes(spSearchQuery);
        });
    }

    if (!listToRender || listToRender.length === 0) {
        if (spSearchQuery) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="8" class="sp-empty-state">
                        <div class="sp-empty-icon">🔍</div>
                        <div class="sp-empty-title">No Candidates Matching "${spSearchQuery}"</div>
                        <div class="sp-empty-desc">No stocks found matching your keyword. Try clearing the filter or searching for another symbol.</div>
                        <button class="sp-empty-cta" onclick="clearStockPickerSearch()">✕ Clear Search Filter</button>
                    </td>
                </tr>
            `;
        } else {
            tbody.innerHTML = `
                <tr>
                    <td colspan="8" class="sp-empty-state">
                        <div class="sp-empty-icon">🎯</div>
                        <div class="sp-empty-title">No candidates in this view</div>
                        <div class="sp-empty-desc">Scan the dual-track market universe to identify fresh growth momentum and covered call opportunities.</div>
                        <button class="sp-empty-cta" onclick="runStockPickerNow()">▶️ Run StockPicker Scan</button>
                    </td>
                </tr>
            `;
        }
        return;
    }

    tbody.innerHTML = listToRender.map((pick, index) => {
        const rank = index + 1;
        const sym = (pick.ticker || pick.symbol || '-').toUpperCase();
        const track = pick.strategy_track || 'GROWTH';
        const score = pick.composite_score != null ? pick.composite_score.toFixed(1) : (pick.score != null ? pick.score.toFixed(1) : '-');
        const status = pick.status || 'PENDING_REVIEW';
        const catalyst = pick.catalyst || pick.fundamental_reasons || 'Algorithmically identified opportunity';
        const dossier = pick.evidence_dossier || {};
        const industry = pick.industry || dossier.industry || '';

        const trackBadge = track === 'INCOME'
            ? `<span class="sp-track-badge income"><span class="track-icon">🛡️</span> Income</span>`
            : `<span class="sp-track-badge growth"><span class="track-icon">🚀</span> Growth</span>`;

        let statusBadge = '';
        let actionButtons = '';

        if (status === 'PROVISIONED') {
            const botId = pick.bot_id || `${sym.toLowerCase()}_sma`;
            statusBadge = `<span class="sp-status-pill provisioned"><span class="status-pulse green"></span> 🤖 ${pick.status_label || 'Bot Active'}</span>`;
            actionButtons = `
                <a href="${pick.dashboard_link || `/bot/${botId}`}" class="sp-btn sp-btn-view">
                    <span>📊</span> View Bot
                </a>
            `;
        } else if (status === 'ACCEPTED') {
            statusBadge = `<span class="sp-status-pill accepted"><span class="status-pulse blue"></span> 🔵 Accepted</span>`;
            actionButtons = `
                <button onclick="openAcceptModal('${sym}', '${track}')" class="sp-btn sp-btn-accept">
                    <span>🚀</span> Provision
                </button>
            `;
        } else if (status === 'PROVISIONING_FAILED') {
            statusBadge = `<span class="sp-status-pill failed"><span class="status-pulse red"></span> ⚠️ Failed</span>`;
            actionButtons = `
                <div style="display: flex; flex-direction: column; gap: 4px; align-items: center;">
                    <button onclick="openAcceptModal('${sym}', '${track}')" title="Retry Provisioning" class="sp-btn sp-btn-retry">
                        🔄 Retry
                    </button>
                    <button onclick="openRejectModal('${sym}')" title="Reject candidate" class="sp-btn sp-btn-reject" style="padding: 2px 8px; font-size: 0.74em;">
                        ✕ Pass
                    </button>
                </div>
            `;
        } else if (status === 'REJECTED') {
            statusBadge = `<span class="sp-status-pill rejected">🔴 Cooldown</span>`;
            actionButtons = `<span class="sp-muted-text">7d Cooldown</span>`;
        } else {
            // PENDING_REVIEW
            statusBadge = `<span class="sp-status-pill pending"><span class="status-pulse amber"></span> 🟡 Actionable</span>`;
            actionButtons = `
                <div class="sp-actions-cell">
                    <button onclick="openAcceptModal('${sym}', '${track}')" title="Accept & Provision Bot" class="sp-btn sp-btn-accept">
                        <span>✅</span> Accept
                    </button>
                    <button onclick="openRejectModal('${sym}')" title="Reject candidate" class="sp-btn sp-btn-reject">
                        <span>✕</span> Pass
                    </button>
                </div>
            `;
        }

        // Score Pill Styling & Conviction Tier
        let scoreClass = 'score-high';
        let convictionLabel = 'Strong';
        const numScore = parseFloat(score);
        if (isNaN(numScore) || numScore < 50) {
            scoreClass = 'score-speculative';
            convictionLabel = 'Speculative';
        } else if (numScore < 65) {
            scoreClass = 'score-low';
            convictionLabel = 'Baseline';
        } else if (numScore < 80) {
            scoreClass = 'score-med';
            convictionLabel = 'Moderate';
        } else {
            scoreClass = 'score-high';
            convictionLabel = 'Strong';
        }

        const metricsHtml = formatPickMetrics(pick);

        return `
            <tr class="sp-row">
                <td style="text-align: center;">
                    <span class="sp-rank-badge">${rank}</span>
                </td>
                <td>${trackBadge}</td>
                <td style="text-align: center;">
                    <div class="sp-ticker-cell">
                        <span class="sp-ticker-symbol">${sym}</span>
                        ${industry ? `<span class="sp-ticker-industry" title="${industry}">${industry}</span>` : ''}
                    </div>
                </td>
                <td style="text-align: center;">
                    <div class="sp-score-wrapper" title="Quantitative Conviction: ${score} / 100 (${convictionLabel} Conviction Tier)">
                        <div class="sp-score-pill ${scoreClass}">
                            <span>${score}</span>
                        </div>
                        <span class="sp-conviction-badge ${scoreClass}">${convictionLabel}</span>
                        ${pick.is_degraded ? `
                            <span class="sp-degraded-tag" title="${(pick.degradation_warnings || []).join('; ') || 'Data feeds partial'}">
                                ⚠️ Degraded
                            </span>
                        ` : ''}
                    </div>
                </td>
                <td style="text-align: center;">
                    ${statusBadge}
                </td>
                <td>
                    <div class="sp-thesis-cell">
                        <div class="sp-thesis-text" title="${catalyst}">${catalyst}</div>
                        ${status === 'PROVISIONING_FAILED' ? `
                            <div class="sp-failure-box">
                                <div><strong>Stage:</strong> ${pick.failure_stage || 'CONFIG'} | <strong>Reason:</strong> ${pick.failure_reason || 'Provisioning script error'}</div>
                                <div class="sp-failure-action">💡 ${pick.suggested_action || 'Review permissions and retry.'}</div>
                            </div>
                        ` : ''}
                    </div>
                </td>
                <td>
                    <div class="sp-metrics-cell">
                        <div class="sp-metrics-chips">${metricsHtml}</div>
                        <button class="sp-dossier-toggle-btn" id="dossier-btn-${sym}" onclick="toggleDossier('${sym}')">
                            <span>🔍 Evidence Dossier</span>
                            <span class="dossier-arrow" id="dossier-arrow-${sym}">▾</span>
                        </button>
                    </div>
                </td>
                <td style="text-align: center;">
                    ${actionButtons}
                </td>
            </tr>
            <tr id="dossier-row-${sym}" style="display: none;">
                <td colspan="8" style="padding: 0;">
                    <div class="dossier-drawer">
                        <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(255, 255, 255, 0.08); padding-bottom: 10px; margin-bottom: 12px;">
                            <div>
                                <strong style="color: #38bdf8; font-size: 1.05em; letter-spacing: 0.03em;">Multi-Dimensional Evidence Dossier: ${sym}</strong>
                                <span style="margin-left: 10px; font-size: 0.85em; color: #94a3b8;">${industry}</span>
                            </div>
                            <div>
                                <a href="${dossier.sec_filing_url || 'https://www.sec.gov/edgar/searchedgar/companysearch'}" target="_blank" rel="noopener noreferrer" style="display: inline-flex; align-items: center; gap: 5px; padding: 4px 10px; border-radius: 6px; background: rgba(56, 189, 248, 0.12); border: 1px solid rgba(56, 189, 248, 0.35); color: #38bdf8; text-decoration: none; font-weight: 600; font-size: 0.82em; transition: all 0.2s;">
                                    <span>🔗 Clickable SEC EDGAR Filing ↗</span>
                                </a>
                            </div>
                        </div>

                        <div class="dossier-grid">
                            <!-- Bull Drivers -->
                            <div class="dossier-card">
                                <h4 style="color: #34d399;">🐂 Key Bull Drivers</h4>
                                <ul style="margin: 0; padding-left: 18px; font-size: 0.85em; color: #f1f5f9; line-height: 1.5;">
                                    ${(dossier.bull_drivers || ['Strong revenue growth momentum', 'Institutional order flow alignment']).map(d => `<li style="margin-bottom: 4px;">${d}</li>`).join('')}
                                </ul>
                            </div>

                            <!-- Risk Warnings -->
                            <div class="dossier-card">
                                <h4 style="color: #f87171;">⚠️ Key Risk Warnings</h4>
                                <ul style="margin: 0; padding-left: 18px; font-size: 0.85em; color: #f1f5f9; line-height: 1.5;">
                                    ${(dossier.risk_warnings || ['Market regime volatility sensitivity', 'Macro cyclical exposure']).map(r => `<li style="margin-bottom: 4px;">${r}</li>`).join('')}
                                </ul>
                            </div>

                            <!-- Alternative Data & Solvency -->
                            <div class="dossier-card">
                                <h4 style="color: #38bdf8;">🏛️ Alternative Data & Catalysts</h4>
                                <div style="font-size: 0.85em; color: #cbd5e1; line-height: 1.6;">
                                    <div><strong>Conviction Rating:</strong> <span style="font-weight: 700;" class="${scoreClass}">${score} / 100 (${convictionLabel} Tier)</span></div>
                                    <div><strong>USAspending Awards:</strong> ${(dossier.usaspending_contracts && dossier.usaspending_contracts.length) ? `${dossier.usaspending_contracts.length} active awards` : 'No recent public federal awards'}</div>
                                    <div><strong>Congressional Trading:</strong> ${(dossier.congressional_trades && dossier.congressional_trades.length) ? `${dossier.congressional_trades.length} filings detected` : 'Neutral insider/congressional flow'}</div>
                                    <div><strong>Solvency Rating:</strong> ${dossier.solvency_rating || 'Adequate'}</div>
                                    <div><strong>Data Quality:</strong> <span style="font-weight: 600; color: ${dossier.data_quality === 'FULL' ? '#34d399' : (dossier.data_quality === 'PARTIAL' ? '#fbbf24' : '#f87171')};">${dossier.data_quality || (pick.is_degraded ? 'PARTIAL' : 'FULL')}</span></div>
                                </div>
                            </div>

                            <!-- Data Quality & Degradation Warning (if applicable) -->
                            ${(dossier.is_degraded || pick.is_degraded || (dossier.degradation_warnings && dossier.degradation_warnings.length > 0)) ? `
                                <div class="dossier-card" style="border: 1px solid #f59e0b; background: rgba(245,158,11,0.08); grid-column: 1 / -1;">
                                    <h4 style="color: #fbbf24; display: flex; align-items: center; gap: 6px;">
                                        <span>⚠️</span> Data Quality & Scoring Degradation Notice
                                    </h4>
                                    <div style="font-size: 0.85em; color: #fde68a;">
                                        <div><strong>Missing / Incomplete Feeds:</strong> ${(dossier.missing_sources && dossier.missing_sources.length) ? dossier.missing_sources.join(', ') : (pick.missing_sources ? pick.missing_sources.join(', ') : 'Partial inputs')}</div>
                                        <ul style="margin: 6px 0 0 0; padding-left: 18px; color: #fef3c7;">
                                            ${(dossier.degradation_warnings || pick.degradation_warnings || ['Scoring factors with missing data defaulted to neutral baseline']).map(w => `<li style="margin-bottom: 3px;">${w}</li>`).join('')}
                                        </ul>
                                        <div style="margin-top: 6px; font-size: 0.8em; color: #fde047;">ℹ️ Note: Scoring confidence is degraded because one or more fundamental, options, or catalyst feeds were unavailable. The algorithm adjusted missing inputs to baseline neutrality.</div>
                                    </div>
                                </div>
                            ` : ''}
                        </div>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

function toggleDossier(sym) {
    const row = document.getElementById(`dossier-row-${sym}`);
    const arrow = document.getElementById(`dossier-arrow-${sym}`);
    if (row) {
        const isOpen = row.style.display !== 'none';
        row.style.display = isOpen ? 'none' : 'table-row';
        if (arrow) {
            if (isOpen) {
                arrow.classList.remove('open');
            } else {
                arrow.classList.add('open');
            }
        }
    }
}

function formatPickMetrics(pick) {
    const metrics = [];
    if (pick.strategy_track === 'INCOME') {
        if (pick.monthly_yield_est != null) {
            metrics.push(`<span class="sp-metric-chip" style="color: #10b981; font-weight: 600;">Yield: ${pick.monthly_yield_est}%/mo</span>`);
        }
        if (pick.annualized_yield_est != null) {
            metrics.push(`<span class="sp-metric-chip">Ann: ${pick.annualized_yield_est}%</span>`);
        }
        if (pick.implied_volatility != null) {
            metrics.push(`<span class="sp-metric-chip">IV: ${pick.implied_volatility}%</span>`);
        }
    } else {
        if (pick.revenue_yoy != null) {
            const revPercent = (pick.revenue_yoy * 100).toFixed(0);
            const revColor = pick.revenue_yoy > 0 ? '#10b981' : '#ef4444';
            metrics.push(`<span class="sp-metric-chip" style="color: ${revColor}">Rev: ${revPercent}%</span>`);
        }
        if (pick.gross_margin != null) {
            metrics.push(`<span class="sp-metric-chip">GM: ${(pick.gross_margin * 100).toFixed(0)}%</span>`);
        }
        if (pick.debt_to_equity != null) {
            const de = pick.debt_to_equity.toFixed(0);
            const deColor = pick.debt_to_equity < 100 ? '#10b981' : (pick.debt_to_equity > 250 ? '#ef4444' : '#94a3b8');
            metrics.push(`<span class="sp-metric-chip" style="color: ${deColor}">D/E: ${de}</span>`);
        }
    }

    return metrics.length > 0 ? metrics.join('') : '<span class="sp-muted-text">-</span>';
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

    // Check if candidate is degraded and warn operator
    const degWarnModal = document.getElementById('modal-degradation-warning');
    const degDetailsModal = document.getElementById('modal-degradation-details');
    if (degWarnModal && degDetailsModal && rawStockPickerData) {
        const allCandidates = (rawStockPickerData.actionable_leads || [])
            .concat(rawStockPickerData.growth_picks || [])
            .concat(rawStockPickerData.income_picks || [])
            .concat(rawStockPickerData.already_accepted || [])
            .concat(rawStockPickerData.picks || []);
        const cand = allCandidates.find(c => (c.ticker || c.symbol || '').toUpperCase() === sym.toUpperCase());
        if (cand && (cand.is_degraded || cand.data_quality !== 'FULL' || (cand.missing_sources && cand.missing_sources.length > 0))) {
            const missing = (cand.missing_sources && cand.missing_sources.length) ? cand.missing_sources.join(', ') : 'Partial feeds';
            const warnList = cand.degradation_warnings && cand.degradation_warnings.length ? cand.degradation_warnings.join(' • ') : 'Scoring adjusted to baseline neutrality.';
            degDetailsModal.innerHTML = `Missing inputs: <strong>${missing}</strong>. ${warnList} Please review parameters carefully before provisioning.`;
            degWarnModal.style.display = 'block';
        } else {
            degWarnModal.style.display = 'none';
        }
    }

    if (modal) modal.style.display = 'block';

    refreshParameterAdvice();
}

async function refreshParameterAdvice() {
    if (!selectedSymbolForAction) return;
    const sym = selectedSymbolForAction;
    const track = selectedTrackForAction || 'GROWTH';
    const capitalInput = document.getElementById('modal-accept-capital');
    const capital = capitalInput ? parseFloat(capitalInput.value) || 10000 : 10000;

    const summaryEl = document.getElementById('modal-advice-summary');
    const bulletsEl = document.getElementById('modal-advice-bullets');
    const badgeEl = document.getElementById('modal-advice-risk-badge');

    try {
        const res = await fetch(`/api/stock-picks/parameter-advice?symbol=${encodeURIComponent(sym)}&strategy_track=${encodeURIComponent(track)}&capital_allocation=${capital}`);
        if (res.ok) {
            const advice = await res.json();
            if (badgeEl) {
                badgeEl.textContent = `${advice.risk_tier} Risk`;
                badgeEl.style.color = advice.risk_tier === 'High' ? '#f87171' : (advice.risk_tier === 'Low' ? '#34d399' : '#fbbf24');
                badgeEl.style.borderColor = advice.risk_tier === 'High' ? '#ef4444' : (advice.risk_tier === 'Low' ? '#10b981' : '#f59e0b');
            }
            if (summaryEl) summaryEl.textContent = advice.advisory_summary || '';
            if (bulletsEl) {
                bulletsEl.innerHTML = (advice.expert_rationale || []).map(b => `<div style="margin-bottom: 2px;">• ${b}</div>`).join('');
            }

            const p = advice.recommended_params || {};
            const atr = p.atr_parameters || {};
            const cc = p.covered_calls || {};
            const elStop = document.getElementById('modal-param-trailing-stop');
            if (elStop && atr.trailing_stop_atr_multiplier) elStop.value = atr.trailing_stop_atr_multiplier;
            const elShares = document.getElementById('modal-param-min-shares');
            if (elShares && atr.position_min_shares) elShares.value = atr.position_min_shares;
            const elMode = document.getElementById('modal-param-cc-mode');
            if (elMode && cc.cc_mode) elMode.value = cc.cc_mode;
            const elExp = document.getElementById('modal-param-call-exp-days');
            if (elExp && cc.call_expiration_days) elExp.value = cc.call_expiration_days;
        }
    } catch (err) {
        console.error('Error fetching parameter advice:', err);
    }
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

    const elStop = document.getElementById('modal-param-trailing-stop');
    const elShares = document.getElementById('modal-param-min-shares');
    const elMode = document.getElementById('modal-param-cc-mode');
    const elExp = document.getElementById('modal-param-call-exp-days');

    const customParams = {};
    if (elStop && elStop.value) customParams.trailing_stop_atr_multiplier = parseFloat(elStop.value);
    if (elShares && elShares.value) customParams.position_min_shares = parseInt(elShares.value);
    if (elMode && elMode.value) customParams.cc_mode = elMode.value;
    if (elExp && elExp.value) customParams.call_expiration_days = parseInt(elExp.value);

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
                capital_allocation: capital,
                custom_params: customParams
            })
        });

        if (!response.ok) {
            let errorMsg = response.statusText;
            try {
                const errData = await response.json();
                if (errData.detail) errorMsg = errData.detail;
            } catch (_) {}
            throw new Error(errorMsg);
        }

        const res = await response.json();
        showToast(`✅ Successfully provisioned bot ${res.bot_id} (client_id: ${res.client_id})!`, 'success');
        closeAcceptModal();
        loadStockPicks();

    } catch (e) {
        console.error('Error provisioning bot:', e);
        showToast(`❌ ${e.message}`, 'error');
        // Refresh picks so table displays PROVISIONING_FAILED state if recorded
        loadStockPicks();
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

function renderReadyState() {
    const tbody = document.getElementById('stock-picks-tbody');
    if (tbody && !rawStockPickerData) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" style="text-align: center; padding: 50px 20px; color: #94a3b8;">
                    <div style="font-size: 2.2em; margin-bottom: 10px;">🎯</div>
                    <div style="font-size: 1.15em; font-weight: 600; color: #f1f5f9; margin-bottom: 6px;">Ready to Scan Dual-Track Markets</div>
                    <div style="font-size: 0.9em; max-width: 500px; margin: 0 auto 18px auto; line-height: 1.45;">
                        Click <strong>Run Now</strong> to trigger a real-time scan across Growth &amp; Income tracks, or load the last saved run from history.
                    </div>
                    <div style="display: flex; gap: 12px; justify-content: center; align-items: center;">
                        <button onclick="runStockPickerNow()" style="padding: 9px 18px; background: #27ae60; color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: 600; font-size: 0.95em;">
                            ▶️ Run Now
                        </button>
                        <button onclick="loadStockPicks(true)" style="padding: 9px 18px; background: #334155; color: #cbd5e1; border: 1px solid #475569; border-radius: 6px; cursor: pointer; font-weight: 500; font-size: 0.95em;">
                            📋 Load Last Saved Run
                        </button>
                    </div>
                </td>
            </tr>
        `;
    }
    const statusBadge = document.getElementById('sp-run-status-badge');
    if (statusBadge && !rawStockPickerData) {
        statusBadge.textContent = 'Ready to Run';
        statusBadge.style.background = 'rgba(56, 189, 248, 0.15)';
        statusBadge.style.color = '#38bdf8';
        statusBadge.style.borderColor = 'rgba(56, 189, 248, 0.3)';
    }
}

function startStockPickerRefresh() {
    const now = Date.now();
    if (now - lastTabSwitchTime < 1000) return;
    lastTabSwitchTime = now;

    if (stockPickerRefreshInterval) {
        clearInterval(stockPickerRefreshInterval);
        stockPickerRefreshInterval = null;
    }

    // Do NOT automatically pre-load data before the operator triggers a run.
    // If a run has already occurred in this session, keep auto-refresh active.
    if (rawStockPickerData) {
        loadStockPicks();
        stockPickerRefreshInterval = setInterval(loadStockPicks, 60000);
    } else {
        renderReadyState();
    }
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
