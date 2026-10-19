/**
 * Trading Engine Performance & Benchmarking Dashboard Client
 * Version: 2.0.0
 *
 * All metrics are treated as nullable: values the engine could not measure are
 * rendered as "--" rather than being coerced to 0.
 */

// State
let currentPeriod = 'YTD';
let currentMode = 'all';
let currentBotScope = 'ALL';
let currentBenchmark = 'SPY';
let performanceChart = null;
let lastPerformanceData = null;

const NOT_AVAILABLE = '--';

// Auto-refresh control. Performance analytics are not real-time, so polling is
// deliberately slow and fully pausable to keep backend/Firestore usage minimal.
const AUTO_REFRESH_STORAGE_KEY = 'perfAutoRefreshMs';
const DEFAULT_AUTO_REFRESH_MS = 60000; // 1 minute
const ALLOWED_AUTO_REFRESH_MS = [0, 30000, 60000, 300000]; // Off / 30s / 1m / 5m

let autoRefreshTimer = null;
let autoRefreshMs = DEFAULT_AUTO_REFRESH_MS;
let isLoading = false; // in-flight guard: never stack overlapping requests

// =============================================================================
// PERFORMANCE SECTION & TILE LOADING INDICATOR SYSTEM
// =============================================================================

let activeLoadToken = 0;
let progressBarTimer = null;
const pendingPerfSections = new Set();

const PERF_SECTION_CONFIG = {
    scorecard: {
        badgeId: 'perfBadge-scorecard',
        tileId: 'tileScorecard',
        label: 'health scorecard'
    },
    kpi: {
        badgeId: 'perfBadge-kpi',
        tileId: 'tileKpis',
        label: 'KPI metrics'
    },
    sec605: {
        badgeId: 'perfBadge-sec605',
        tileId: 'tileSec605',
        label: 'SEC 605'
    },
    instCta: {
        badgeId: 'perfBadge-instCta',
        tileId: 'tileInstCta',
        label: 'CTA benchmark'
    },
    buyHold: {
        badgeId: 'perfBadge-buyHold',
        tileId: 'tileBuyHold',
        label: 'buy & hold defense'
    },
    radar: {
        badgeId: 'perfBadge-radar',
        tileId: 'tileRadarChart',
        label: 'radar chart'
    },
    bots: {
        badgeId: 'perfBadge-bots',
        tileId: 'tileBots',
        label: 'bot attribution'
    },
    diagnostics: {
        badgeId: 'perfBadge-diagnostics',
        tileId: 'tileDiagnostics',
        label: 'diagnostics'
    }
};

/**
 * Start the top slim progress bar with smooth progressive increments
 */
function startProgressBar() {
    const container = document.getElementById('perfProgressContainer');
    const bar = document.getElementById('perfProgressBar');
    if (!container || !bar) return;

    if (progressBarTimer) {
        clearInterval(progressBarTimer);
        progressBarTimer = null;
    }

    bar.style.opacity = '1';
    bar.style.width = '15%';
    container.style.display = 'block';

    let progress = 15;
    progressBarTimer = setInterval(() => {
        if (progress < 85) {
            const step = Math.max(1, Math.floor((85 - progress) / 5));
            progress += step;
            bar.style.width = `${progress}%`;
        } else {
            clearInterval(progressBarTimer);
            progressBarTimer = null;
        }
    }, 120);
}

/**
 * Finish the top slim progress bar and fade it out
 */
function finishProgressBar() {
    const container = document.getElementById('perfProgressContainer');
    const bar = document.getElementById('perfProgressBar');
    if (progressBarTimer) {
        clearInterval(progressBarTimer);
        progressBarTimer = null;
    }
    if (!container || !bar) return;

    bar.style.width = '100%';
    setTimeout(() => {
        bar.style.opacity = '0';
        setTimeout(() => {
            container.style.display = 'none';
            bar.style.width = '0%';
        }, 350);
    }, 200);
}

/**
 * Begin performance loading state across dashboard tiles and sections
 * @param {string} reason - Loading trigger reason (e.g. 'initial', 'period', 'refresh')
 */
function beginPerformanceLoading(reason = 'initial') {
    activeLoadToken++;
    const currentToken = activeLoadToken;
    pendingPerfSections.clear();

    // 1. Start top slim progress bar
    startProgressBar();

    // 2. Global status pill in bot-header metadata
    const globalStatus = document.getElementById('perfStatusPill');
    const globalText = document.getElementById('perfStatusText');
    if (globalStatus) {
        globalStatus.style.display = 'inline-flex';
        globalStatus.style.opacity = '1';
        globalStatus.className = 'bot-switch-status-pill loading';
        if (globalText) {
            let msg = 'Loading performance analytics...';
            if (reason === 'refresh') msg = 'Refreshing performance metrics...';
            else if (reason.startsWith('period')) msg = `Loading ${currentPeriod} metrics...`;
            else if (reason.startsWith('mode')) msg = `Loading ${currentMode.toUpperCase()} mode...`;
            else if (reason.startsWith('bot')) msg = `Loading ${currentBotScope} bot metrics...`;
            else if (reason.startsWith('benchmark')) msg = `Benchmarking against ${currentBenchmark}...`;
            globalText.textContent = msg;
        }
    }

    // 3. Mark each section as loading with explicit badge and tile styling
    Object.keys(PERF_SECTION_CONFIG).forEach(secKey => {
        pendingPerfSections.add(secKey);
        setPerfSectionLoadingIndicator(secKey, true);
    });

    // 4. Set table and container placeholders
    setPerformanceLoadingPlaceholders();

    // 5. Safety timeout to prevent any indicator from staying stuck indefinitely
    setTimeout(() => {
        if (activeLoadToken === currentToken && pendingPerfSections.size > 0) {
            pendingPerfSections.forEach(secKey => {
                completePerfSectionLoading(secKey, true);
            });
            finishProgressBar();
        }
    }, 15000);

    return currentToken;
}

/**
 * Set section-level loading indicator badge and tile styling
 */
function setPerfSectionLoadingIndicator(secKey, isLoading, isError = false) {
    const cfg = PERF_SECTION_CONFIG[secKey];
    if (!cfg) return;

    const badge = document.getElementById(cfg.badgeId);
    const tile = cfg.tileId ? document.getElementById(cfg.tileId) : null;

    if (isLoading) {
        if (tile) tile.classList.add('tile-loading');
        if (secKey === 'kpi') {
            document.querySelectorAll('#tileKpis .statement-kpi-card').forEach(card => card.classList.add('tile-loading'));
        }
        if (badge) {
            badge.style.display = 'inline-flex';
            badge.style.opacity = '1';
            badge.className = 'section-switch-badge loading';
            badge.innerHTML = `<span class="badge-spinner"></span> <span class="badge-text">Loading ${cfg.label}...</span>`;
        }
    } else {
        if (tile) tile.classList.remove('tile-loading');
        if (secKey === 'kpi') {
            document.querySelectorAll('#tileKpis .statement-kpi-card').forEach(card => card.classList.remove('tile-loading'));
        }
        if (badge) {
            if (isError) {
                badge.className = 'section-switch-badge error';
                badge.innerHTML = `<span class="badge-icon">⚠️</span> <span class="badge-text">Failed loading ${cfg.label}</span>`;
                setTimeout(() => {
                    if (badge.classList.contains('error')) {
                        badge.style.display = 'none';
                    }
                }, 4000);
            } else {
                badge.className = 'section-switch-badge updated';
                badge.innerHTML = `<span class="badge-icon">✓</span> <span class="badge-text">${cfg.label} updated</span>`;
                setTimeout(() => {
                    if (badge.classList.contains('updated')) {
                        badge.style.opacity = '0';
                        setTimeout(() => {
                            badge.style.display = 'none';
                            badge.style.opacity = '1';
                        }, 300);
                    }
                }, 1800);
            }
        }
    }
}

/**
 * Mark a performance section as completed after render
 */
function completePerfSectionLoading(secKey, isError = false) {
    if (!pendingPerfSections.has(secKey)) return;
    pendingPerfSections.delete(secKey);
    setPerfSectionLoadingIndicator(secKey, false, isError);

    // Hide radar chart overlay if radar finished
    if (secKey === 'radar') {
        const radarOverlay = document.getElementById('radarSwitchOverlay');
        if (radarOverlay) {
            radarOverlay.style.opacity = '0';
            setTimeout(() => {
                radarOverlay.style.display = 'none';
                radarOverlay.style.opacity = '1';
            }, 250);
        }
    }

    // When all sections finish, update the global header status pill and finish progress bar
    if (pendingPerfSections.size === 0) {
        finishProgressBar();
        const globalStatus = document.getElementById('perfStatusPill');
        const globalText = document.getElementById('perfStatusText');
        if (globalStatus) {
            globalStatus.className = 'bot-switch-status-pill updated';
            if (globalText) {
                globalText.textContent = '✓ Performance data updated';
            }
            setTimeout(() => {
                if (globalStatus.classList.contains('updated')) {
                    globalStatus.style.opacity = '0';
                    setTimeout(() => {
                        globalStatus.style.display = 'none';
                        globalStatus.style.opacity = '1';
                    }, 400);
                }
            }, 2200);
        }
    }
}

/**
 * Set loading placeholders inside tables, containers, and overlays
 */
function setPerformanceLoadingPlaceholders() {
    // 1. SEC Rule 605 table
    const tableSec605 = document.getElementById('tableSec605');
    if (tableSec605) {
        tableSec605.innerHTML = `
            <tr>
                <td colspan="4" class="table-switch-loading">
                    <div class="table-switch-spinner-wrap">
                        <span class="section-switch-spinner"></span>
                        <div class="table-switch-text">Evaluating SEC Rule 605 Execution Standard...</div>
                        <div class="table-switch-subtext">Calculating slippage, fill rate, and price improvement against FINRA / IBKR standards</div>
                    </div>
                </td>
            </tr>
        `;
    }

    // 2. Institutional CTA table
    const tableInstCta = document.getElementById('tableInstCta');
    if (tableInstCta) {
        tableInstCta.innerHTML = `
            <tr>
                <td colspan="4" class="table-switch-loading">
                    <div class="table-switch-spinner-wrap">
                        <span class="section-switch-spinner"></span>
                        <div class="table-switch-text">Evaluating Institutional CTA Benchmarks...</div>
                        <div class="table-switch-subtext">Benchmarking Sharpe, Sortino, and alpha edge against Barclay CTA index</div>
                    </div>
                </td>
            </tr>
        `;
    }

    // 3. Buy and Hold Defense table
    const tableBuyHold = document.getElementById('tableBuyHold');
    if (tableBuyHold) {
        tableBuyHold.innerHTML = `
            <tr>
                <td colspan="4" class="table-switch-loading">
                    <div class="table-switch-spinner-wrap">
                        <span class="section-switch-spinner"></span>
                        <div class="table-switch-text">Evaluating Buy-and-Hold Risk Suppression...</div>
                        <div class="table-switch-subtext">Comparing drawdown suppression and downside protection vs ${currentBenchmark}</div>
                    </div>
                </td>
            </tr>
        `;
    }

    // 4. Bots Attribution table
    const botsTable = document.getElementById('botsTableBody');
    if (botsTable) {
        botsTable.innerHTML = `
            <tr>
                <td colspan="11" class="table-switch-loading">
                    <div class="table-switch-spinner-wrap">
                        <span class="section-switch-spinner"></span>
                        <div class="table-switch-text">Aggregating Bot Attribution &amp; Performance...</div>
                        <div class="table-switch-subtext">Loading trade execution, win rates, and rating scores across active bots</div>
                    </div>
                </td>
            </tr>
        `;
    }

    // 5. Diagnostics grid placeholder
    const diagGrid = document.getElementById('diagnosticsGrid');
    if (diagGrid) {
        diagGrid.innerHTML = `
            <div class="diagnostics-loading-placeholder">
                <div class="table-switch-spinner-wrap">
                    <span class="section-switch-spinner"></span>
                    <div class="table-switch-text">Scanning Engine Telemetry &amp; Diagnostics...</div>
                    <div class="table-switch-subtext">Analyzing execution logs and generating prioritized recommendations</div>
                </div>
            </div>
        `;
    }

    // 6. Radar Chart overlay
    const radarOverlay = document.getElementById('radarSwitchOverlay');
    if (radarOverlay) {
        radarOverlay.style.display = 'flex';
        radarOverlay.style.opacity = '1';
    }
}

// Initialize when DOM ready
document.addEventListener('DOMContentLoaded', () => {
    initControls();
    loadPerformanceData();
    initAutoRefresh();
});

/**
 * Schedule (or stop) the automatic refresh. Honors a user preference stored in
 * localStorage, skips ticks while the tab is hidden, and re-syncs once when the
 * tab becomes visible again so a background tab costs nothing.
 */
function initAutoRefresh() {
    // Read the stored preference defensively: a missing key must fall back to
    // the default (not to 0 = "Off"), and localStorage can throw when blocked.
    let stored = NaN;
    try {
        const raw = localStorage.getItem(AUTO_REFRESH_STORAGE_KEY);
        if (raw !== null) stored = Number(raw);
    } catch (e) {
        stored = NaN;
    }
    autoRefreshMs = ALLOWED_AUTO_REFRESH_MS.includes(stored) ? stored : DEFAULT_AUTO_REFRESH_MS;

    const select = document.getElementById('refreshInterval');
    if (select) {
        select.value = String(autoRefreshMs);
        select.addEventListener('change', () => {
            autoRefreshMs = Number(select.value) || 0;
            try {
                localStorage.setItem(AUTO_REFRESH_STORAGE_KEY, String(autoRefreshMs));
            } catch (e) {
                /* persistence is best-effort */
            }
            scheduleAutoRefresh();
        });
    }

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
            // Catch up immediately, then resume the normal cadence.
            loadPerformanceData();
            scheduleAutoRefresh();
        } else {
            stopAutoRefresh();
        }
    });

    scheduleAutoRefresh();
}

function scheduleAutoRefresh() {
    stopAutoRefresh();
    if (!autoRefreshMs || document.visibilityState === 'hidden') {
        return;
    }
    autoRefreshTimer = setInterval(() => {
        if (document.visibilityState === 'visible') {
            loadPerformanceData(false, true);
        }
    }, autoRefreshMs);
}

function stopAutoRefresh() {
    if (autoRefreshTimer) {
        clearInterval(autoRefreshTimer);
        autoRefreshTimer = null;
    }
}

function initControls() {
    // Period buttons
    document.querySelectorAll('#timelineTabs .timeline-tab').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('#timelineTabs .timeline-tab').forEach(b => b.classList.remove('active'));
            e.currentTarget.classList.add('active');
            currentPeriod = e.currentTarget.dataset.period || 'YTD';
            loadPerformanceData(false, false, `period (${currentPeriod})`);
        });
    });

    // Mode buttons
    document.querySelectorAll('#modeTabs .mode-tab').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('#modeTabs .mode-tab').forEach(b => b.classList.remove('active'));
            e.currentTarget.classList.add('active');
            currentMode = e.currentTarget.dataset.mode || 'all';
            loadPerformanceData(false, false, `mode (${currentMode})`);
        });
    });

    // Benchmark selector
    const benchmarkSelect = document.getElementById('benchmarkSelect');
    if (benchmarkSelect) {
        benchmarkSelect.value = currentBenchmark;
    }
}

function setBotScope(scope) {
    currentBotScope = scope;
    document.querySelectorAll('#botScopePills .bot-scope-pill').forEach(pill => {
        if (pill.dataset.scope === scope) {
            pill.classList.add('active');
        } else {
            pill.classList.remove('active');
        }
    });
    loadPerformanceData(false, false, `bot (${scope})`);
}

function setBenchmark(benchmark) {
    currentBenchmark = benchmark || 'SPY';
    loadPerformanceData(false, false, `benchmark (${currentBenchmark})`);
}

async function loadPerformanceData(force = false, isSilent = false, reason = '') {
    // In-flight guard: a slow request must never stack up overlapping calls
    // (which would multiply backend/Firestore load).
    if (isLoading) return;
    isLoading = true;

    if (!isSilent) {
        const loadReason = reason || (force ? 'refresh' : (currentPeriod ? `period (${currentPeriod})` : 'initial'));
        beginPerformanceLoading(loadReason);
    }

    const syncStatus = document.getElementById('syncStatusText');
    const refreshBtn = document.getElementById('btnRefreshPerformance');
    if (syncStatus) syncStatus.innerText = force ? 'Refreshing…' : 'Syncing...';
    if (refreshBtn) refreshBtn.disabled = true;

    try {
        let url = `/api/engine-performance?period=${currentPeriod}&mode=${currentMode}&bot=${currentBotScope}&benchmark=${currentBenchmark}`;
        const opts = {};
        if (force) {
            // Manual refresh: bypass both the browser HTTP cache and the
            // server-side analysis/trade/benchmark caches.
            url += `&force=1&_t=${Date.now()}`;
            opts.cache = 'no-store';
        }
        const res = await fetch(url, opts);
        if (!res.ok) throw new Error(`HTTP error ${res.status}`);
        const data = await res.json();
        lastPerformanceData = data;

        renderScorecard(data.scorecard);
        completePerfSectionLoading('scorecard');

        renderKPIs(data.executive_kpis);
        completePerfSectionLoading('kpi');

        renderBenchmarks(data.benchmarks);
        completePerfSectionLoading('sec605');
        completePerfSectionLoading('instCta');
        completePerfSectionLoading('buyHold');

        renderBots(data.bots);
        completePerfSectionLoading('bots');

        renderDiagnostics(data.diagnostics);
        completePerfSectionLoading('diagnostics');

        renderBenchmarkChart(data.scorecard);
        completePerfSectionLoading('radar');

        updateBenchmarkBadge(data.executive_kpis);
        renderFreshness(data);

        if (syncStatus) syncStatus.innerText = 'Live Feed Connected';
    } catch (err) {
        console.error('Failed to load performance data:', err);
        if (syncStatus) syncStatus.innerText = 'Sync Error';
        Object.keys(PERF_SECTION_CONFIG).forEach(secKey => {
            completePerfSectionLoading(secKey, true);
        });
        finishProgressBar();
        const globalStatus = document.getElementById('perfStatusPill');
        const globalText = document.getElementById('perfStatusText');
        if (globalStatus) {
            globalStatus.className = 'bot-switch-status-pill error';
            if (globalText) globalText.textContent = '⚠️ Performance sync failed';
            setTimeout(() => {
                if (globalStatus.classList.contains('error')) {
                    globalStatus.style.display = 'none';
                }
            }, 4000);
        }
    } finally {
        if (refreshBtn) refreshBtn.disabled = false;
        isLoading = false;
    }
}

/**
 * Show the *data* freshness (the newest trade actually evaluated), never the
 * browser fetch time - so "as of" cannot claim to be newer than the underlying
 * trades. Falls back to an explicit "no trades" note when the period is empty.
 */
function renderFreshness(data) {
    const lastUp = document.getElementById('lastUpdate');
    if (!lastUp) return;

    const fmt = (iso) => {
        if (!iso) return null;
        const d = new Date(iso);
        if (isNaN(d.getTime())) return null;
        return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    };

    const asOf = fmt(data && data.data_as_of);
    if (asOf) {
        lastUp.innerText = data.history_truncated ? `${asOf} (capped)` : asOf;
    } else {
        lastUp.innerText = 'no trades in period';
    }

    const parts = [];
    if (data && data.data_oldest && data.data_as_of) {
        parts.push(`Trades from ${data.data_oldest} to ${data.data_as_of}`);
    }
    if (data && data.history_truncated) {
        parts.push('Backend row cap reached - older trades may not be included.');
    }
    if (data && data.cached) {
        parts.push(`Served from cache (${data.cache_age_seconds}s old).`);
    }
    lastUp.title = parts.join(' | ');
}

function refreshPerformance() {
    loadPerformanceData(true);
}

function updateBenchmarkBadge(kpis) {
    const badge = document.getElementById('buyHoldBadge');
    if (!badge || !kpis) return;
    const basis = (kpis.metrics_basis && kpis.metrics_basis['capital.benchmark_mdd']) || 'unavailable';
    const suffix = basis === 'measured' ? '' : ' (no history)';
    badge.innerText = `${currentBenchmark} Benchmark${suffix}`;
}

function renderScorecard(scorecard) {
    if (!scorecard) return;

    const grade = scorecard.overall_grade || 'N/A';
    const gradeEl = document.getElementById('scorecardGrade');
    if (gradeEl) {
        gradeEl.innerText = grade;
        gradeEl.className = 'grade-badge-huge';
        if (grade.startsWith('A')) gradeEl.classList.add('grade-a');
        else if (grade.startsWith('B')) gradeEl.classList.add('grade-b');
        else if (grade === 'N/A') gradeEl.classList.add('grade-na');
        else gradeEl.classList.add('grade-c');
    }

    const numEl = document.getElementById('scorecardNumeric');
    if (numEl) {
        numEl.innerText = scorecard.overall_score != null
            ? `${scorecard.overall_score} / 100`
            : 'Not rated';
    }

    const confEl = document.getElementById('scorecardConfidence');
    if (confEl) {
        const conf = scorecard.sample_confidence || 'INSUFFICIENT';
        confEl.innerText = `Sample: ${conf}`;
        confEl.className = 'scorecard-confidence';
        if (conf === 'ADEQUATE') confEl.classList.add('confidence-adequate');
        else if (conf === 'LOW') confEl.classList.add('confidence-low');
    }

    // Pillar progress
    setBar('barExec', 'scoreExec', scorecard.execution_score);
    setBar('barRisk', 'scoreRisk', scorecard.risk_adjusted_score);
    setBar('barExp', 'scoreExp', scorecard.expectancy_score);
    setBar('barCap', 'scoreCap', scorecard.capital_preservation_score);

    const narrativeEl = document.getElementById('scorecardNarrative');
    if (narrativeEl) narrativeEl.innerText = scorecard.summary_assessment;
}

function setBar(barId, scoreId, val) {
    const bar = document.getElementById(barId);
    const lbl = document.getElementById(scoreId);
    const hasValue = val != null && !isNaN(val);
    if (bar) bar.style.width = hasValue ? `${Math.min(100, Math.max(0, val))}%` : '0%';
    if (lbl) lbl.innerText = hasValue ? `${val}/100` : NOT_AVAILABLE;
}

function renderKPIs(kpis) {
    if (!kpis) return;

    setVal('kpiNav', formatCurrency(kpis.nav));
    setVal('kpiNetPnl', formatCurrency(kpis.total_net_pnl, true));
    colorize('kpiNetPnl', kpis.total_net_pnl);

    setVal('kpiSharpe', fmtNumber(kpis.annualized_sharpe, 2));
    setVal('kpiSortino', fmtNumber(kpis.sortino_ratio, 2));
    setVal('kpiCalmar', fmtNumber(kpis.calmar_ratio, 2));

    setVal('kpiProfitFactor', fmtNumber(kpis.profit_factor, 2));
    setVal('kpiExpectancy', formatCurrency(kpis.expectancy_dollars, true));
    colorize('kpiExpectancy', kpis.expectancy_dollars);

    setVal('kpiSlippage', kpis.avg_slippage_per_share != null
        ? `$${kpis.avg_slippage_per_share.toFixed(3)}/sh` : NOT_AVAILABLE);
    setVal('kpiFillConf', kpis.fill_confirmation_rate_pct != null
        ? `${kpis.fill_confirmation_rate_pct.toFixed(1)}%` : NOT_AVAILABLE);

    setVal('kpiMaxDd', kpis.max_drawdown_pct != null
        ? `${kpis.max_drawdown_pct.toFixed(2)}%` : NOT_AVAILABLE);

    const ddReduction = kpis.mdd_reduction_vs_benchmark_pct;
    setVal('kpiDdReduction', ddReduction != null
        ? `${ddReduction.toFixed(1)}% vs ${currentBenchmark} B&H` : `No ${currentBenchmark} comparison`);
}

function renderBenchmarks(benchmarks) {
    if (!benchmarks) return;

    renderBenchmarkTable('tableSec605', benchmarks.sec_rule_605);
    renderBenchmarkTable('tableInstCta', benchmarks.institutional_cta);
    renderBenchmarkTable('tableBuyHold', benchmarks.buy_and_hold);
}

function renderBenchmarkTable(tableId, list) {
    const tbody = document.getElementById(tableId);
    if (!tbody || !list) return;

    tbody.innerHTML = list.map(m => {
        const status = (m.status || 'UNMEASURED').toLowerCase();
        const valueColor = m.engine_value == null ? '#94a3b8' : '#38bdf8';
        return `
        <tr>
            <td>
                <div style="font-weight: 600; color: #f8fafc;">${m.name}</div>
                <div style="font-size: 0.75rem; color: #94a3b8; margin-top: 0.2rem;">${m.description}</div>
            </td>
            <td class="font-mono" style="font-weight: 700; color: ${valueColor};">${m.engine_formatted || NOT_AVAILABLE}</td>
            <td class="font-mono" style="color: #94a3b8;">${m.benchmark_formatted || NOT_AVAILABLE}</td>
            <td>
                <span class="status-pill status-${status}">${m.status}</span>
            </td>
        </tr>
    `;
    }).join('');
}

function renderBots(bots) {
    const tbody = document.getElementById('botsTableBody');
    const pillsContainer = document.getElementById('botScopePills');
    if (!tbody || !bots) return;

    if (!bots.length) {
        tbody.innerHTML = `<tr><td colspan="11" style="text-align:center; color:#94a3b8; padding:1.5rem;">
            No configured bots or trades in this period.</td></tr>`;
    } else {
        tbody.innerHTML = bots.map(b => `
            <tr>
                <td>
                    <div class="symbol-badge">
                        <span>${b.symbol}</span>
                        <span style="font-size: 0.72rem; color: #94a3b8; font-weight: normal;">(${b.strategy})</span>
                        ${b.status && b.status !== 'ACTIVE' ? `<span class="bot-badge-tag badge-na" style="margin-left:0.35rem;">${b.status}</span>` : ''}
                    </div>
                </td>
                <td>
                    <span class="bot-badge-tag badge-${(b.status_badge || 'low_sample').toLowerCase()}">${b.status_badge}</span>
                </td>
                <td class="font-mono" style="font-weight: 700;">${b.rating_grade}${b.rating_score != null ? ` <span style="font-size: 0.75rem; color: #94a3b8;">(${b.rating_score})</span>` : ''}</td>
                <td class="font-mono">${b.trades_count}</td>
                <td class="font-mono">${b.win_rate_pct.toFixed(1)}%</td>
                <td class="font-mono" style="color: ${b.net_pnl >= 0 ? '#34d399' : '#f87171'}; font-weight: 600;">
                    ${formatCurrency(b.net_pnl, true)}
                </td>
                <td class="font-mono">${fmtNumber(b.profit_factor, 2)}</td>
                <td class="font-mono">${fmtNumber(b.sharpe_ratio, 2)}</td>
                <td class="font-mono" style="color: #fbbf24;">${b.max_drawdown_pct != null ? b.max_drawdown_pct.toFixed(2) + '%' : NOT_AVAILABLE}</td>
                <td class="font-mono">${b.avg_slippage_per_share != null ? '$' + b.avg_slippage_per_share.toFixed(3) + '/sh' : NOT_AVAILABLE}</td>
                <td style="font-size: 0.8rem; color: #cbd5e1; max-width: 280px; white-space: normal;">
                    ${b.diagnostic_finding}
                </td>
            </tr>
        `).join('');
    }

    // Rebuild the scope pills from the current bot list on every render. Bots
    // are added and removed over time, so a build-once list would (a) never
    // show a newly added bot and (b) keep showing a removed one forever. If the
    // current selection no longer exists, fall back to ALL so the header never
    // claims a bot-scoped view the backend did not produce.
    if (pillsContainer) {
        const symbols = bots.map(b => b.symbol).filter(Boolean);
        if (currentBotScope !== 'ALL' && !symbols.includes(currentBotScope)) {
            currentBotScope = 'ALL';
        }
        pillsContainer.innerHTML = '';
        const addPill = (scope, label, color) => {
            const pill = document.createElement('div');
            pill.className = `bot-scope-pill ${currentBotScope === scope ? 'active' : ''}`;
            pill.dataset.scope = scope;
            pill.onclick = () => setBotScope(scope);
            pill.innerHTML = `<span class="scope-dot" style="background:${color};"></span> ${label}`;
            pillsContainer.appendChild(pill);
        };
        addPill('ALL', 'All Bots Combined', '#818cf8');
        symbols.forEach(sym => addPill(sym, sym, '#38bdf8'));
    }

    const activeBotsCount = document.getElementById('activeBots');
    if (activeBotsCount) activeBotsCount.innerText = `${bots.length}`;
}

function renderDiagnostics(diagnostics) {
    const grid = document.getElementById('diagnosticsGrid');
    if (!grid || !diagnostics) return;

    grid.innerHTML = diagnostics.map(d => `
        <div class="diag-card diag-${(d.level || 'info').toLowerCase()}">
            <div class="diag-header">
                <span class="diag-category">${d.category}</span>
                <span style="font-size: 0.72rem; font-weight: 700; color: #94a3b8;">${d.level}</span>
            </div>
            <div class="diag-title">${d.title}</div>
            <div class="diag-detail">${d.detail}</div>
            <div class="diag-action">
                <span>💡</span>
                <span><strong>Action:</strong> ${d.recommended_action}</span>
            </div>
            <a href="https://github.com/PowerStreamCoder/tradingbot-documentation/blob/main/${d.guide_reference}" target="_blank" class="diag-ref">
                📖 View in Tuning &amp; Evaluation Guide &rarr;
            </a>
        </div>
    `).join('');
}

function renderBenchmarkChart(scorecard) {
    const ctx = document.getElementById('benchmarkRadarChart');
    if (!ctx || !scorecard) return;

    if (performanceChart) {
        performanceChart.destroy();
    }

    // Only the four measurable pillars; unavailable pillars are plotted as 0
    // and are not silently replaced with assumed values.
    const labels = [
        'Execution Quality',
        'Risk-Adjusted Alpha',
        'Trade Expectancy',
        'Capital Preservation'
    ];

    const engineData = [
        scorecard.execution_score != null ? scorecard.execution_score : 0,
        scorecard.risk_adjusted_score != null ? scorecard.risk_adjusted_score : 0,
        scorecard.expectancy_score != null ? scorecard.expectancy_score : 0,
        scorecard.capital_preservation_score != null ? scorecard.capital_preservation_score : 0
    ];

    // Static published institutional reference baseline (clearly labelled).
    const benchmarkData = [80, 70, 65, 55];

    performanceChart = new Chart(ctx, {
        type: 'radar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Trading Engine Actual',
                    data: engineData,
                    backgroundColor: 'rgba(56, 189, 248, 0.25)',
                    borderColor: '#38bdf8',
                    borderWidth: 2,
                    pointBackgroundColor: '#38bdf8',
                    pointBorderColor: '#fff',
                },
                {
                    label: 'Institutional Reference (static)',
                    data: benchmarkData,
                    backgroundColor: 'rgba(148, 163, 184, 0.15)',
                    borderColor: '#94a3b8',
                    borderWidth: 2,
                    pointBackgroundColor: '#94a3b8',
                    pointBorderColor: '#fff',
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                r: {
                    angleLines: { color: 'rgba(255, 255, 255, 0.1)' },
                    grid: { color: 'rgba(255, 255, 255, 0.08)' },
                    pointLabels: {
                        color: '#cbd5e1',
                        font: { size: 11, family: 'Inter' }
                    },
                    suggestedMin: 0,
                    suggestedMax: 100,
                    ticks: {
                        backdropColor: 'transparent',
                        color: '#64748b',
                        stepSize: 25
                    }
                }
            },
            plugins: {
                legend: {
                    position: 'top',
                    labels: { color: '#e2e8f0', font: { family: 'Inter' } }
                }
            }
        }
    });
}

function fmtNumber(val, decimals) {
    if (val == null || isNaN(val)) return NOT_AVAILABLE;
    return Number(val).toFixed(decimals);
}

function formatCurrency(val, withSign = false) {
    if (val == null || isNaN(val)) return '$0.00';
    const sign = withSign && val > 0 ? '+' : '';
    return `${sign}$${Math.abs(val).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function setVal(id, text) {
    const el = document.getElementById(id);
    if (el) el.innerText = text;
}

function colorize(id, val) {
    const el = document.getElementById(id);
    if (!el) return;
    if (val > 0) {
        el.style.color = '#34d399';
    } else if (val < 0) {
        el.style.color = '#f87171';
    } else {
        el.style.color = '#cbd5e1';
    }
}

function exportPerformanceCSV() {
    if (!lastPerformanceData) {
        window.alert('No performance data loaded yet.');
        return;
    }
    const rows = [];
    rows.push(['Metric', 'Value', 'Basis']);
    const k = lastPerformanceData.executive_kpis || {};
    const basis = k.metrics_basis || {};
    const add = (label, value, basisKey) => rows.push([label, value == null ? 'N/A' : value, basisKey ? (basis[basisKey] || 'measured') : 'measured']);

    rows.push(['Section', 'Engine Scorecard', '']);
    rows.push(['Overall Grade', lastPerformanceData.scorecard.overall_grade, 'measured']);
    add('Overall Score', lastPerformanceData.scorecard.overall_score);
    rows.push(['Sample Confidence', lastPerformanceData.scorecard.sample_confidence, 'measured']);
    add('Execution Score', lastPerformanceData.scorecard.execution_score);
    add('Risk-Adjusted Score', lastPerformanceData.scorecard.risk_adjusted_score);
    add('Expectancy Score', lastPerformanceData.scorecard.expectancy_score);
    add('Capital Preservation Score', lastPerformanceData.scorecard.capital_preservation_score);

    rows.push(['Section', 'Executive KPIs', '']);
    add('NAV', k.nav);
    add('Total Net P&L', k.total_net_pnl);
    add('Total Trades', k.total_trades);
    add('Win Rate %', k.win_rate_pct);
    add('Profit Factor', k.profit_factor);
    add('Annualized Sharpe', k.annualized_sharpe, 'risk.sharpe');
    add('Sortino', k.sortino_ratio, 'risk.sortino');
    add('Calmar', k.calmar_ratio, 'risk.calmar');
    add('Max Drawdown %', k.max_drawdown_pct, 'capital.max_drawdown');
    add('Fill Confirmation %', k.fill_confirmation_rate_pct, 'execution.fill_confirmation');
    add('Reconciliation %', k.reconciliation_rate_pct, 'execution.reconciliation');
    add('Avg Slippage /share', k.avg_slippage_per_share, 'execution.slippage');
    add(`Benchmark Max DD % (${currentBenchmark})`, k.benchmark_mdd_pct, 'capital.benchmark_mdd');

    rows.push(['Section', 'Per-Bot Attribution', '']);
    rows.push(['Symbol', 'Grade', 'Score', 'Trades', 'WinRate %', 'NetPnL', 'ProfitFactor', 'Sharpe', 'MaxDD %', 'Slippage']);
    (lastPerformanceData.bots || []).forEach(b => {
        rows.push([
            b.symbol, b.rating_grade, b.rating_score == null ? 'N/A' : b.rating_score,
            b.trades_count, b.win_rate_pct, b.net_pnl,
            b.profit_factor == null ? 'N/A' : b.profit_factor,
            b.sharpe_ratio == null ? 'N/A' : b.sharpe_ratio,
            b.max_drawdown_pct == null ? 'N/A' : b.max_drawdown_pct,
            b.avg_slippage_per_share == null ? 'N/A' : b.avg_slippage_per_share
        ]);
    });

    const csv = rows.map(r => r.map(cell => {
        const s = String(cell == null ? '' : cell);
        return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    }).join(',')).join('\n');

    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `engine-performance_${currentPeriod}_${currentMode}_${currentBenchmark}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}
