/**
 * Trading Engine Performance & Benchmarking Dashboard Client
 * Version: 1.0.0
 */

// State
let currentPeriod = 'YTD';
let currentMode = 'all';
let currentBotScope = 'ALL';
let currentBenchmark = 'SPY';
let performanceChart = null;

// Initialize when DOM ready
document.addEventListener('DOMContentLoaded', () => {
    initControls();
    loadPerformanceData();
    // Auto-refresh every 30 seconds
    setInterval(loadPerformanceData, 30000);
});

function initControls() {
    // Period buttons
    document.querySelectorAll('#timelineTabs .timeline-tab').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('#timelineTabs .timeline-tab').forEach(b => b.classList.remove('active'));
            e.currentTarget.classList.add('active');
            currentPeriod = e.currentTarget.dataset.period || 'YTD';
            loadPerformanceData();
        });
    });

    // Mode buttons
    document.querySelectorAll('#modeTabs .mode-tab').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('#modeTabs .mode-tab').forEach(b => b.classList.remove('active'));
            e.currentTarget.classList.add('active');
            currentMode = e.currentTarget.dataset.mode || 'all';
            loadPerformanceData();
        });
    });
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
    loadPerformanceData();
}

async function loadPerformanceData() {
    const syncStatus = document.getElementById('syncStatusText');
    if (syncStatus) syncStatus.innerText = 'Syncing...';

    try {
        const url = `/api/engine-performance?period=${currentPeriod}&mode=${currentMode}&bot=${currentBotScope}&benchmark=${currentBenchmark}`;
        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP error ${res.status}`);
        const data = await res.json();

        renderScorecard(data.scorecard);
        renderKPIs(data.executive_kpis);
        renderBenchmarks(data.benchmarks);
        renderBots(data.bots);
        renderDiagnostics(data.diagnostics);
        renderBenchmarkChart(data.scorecard, data.benchmarks);

        const lastUp = document.getElementById('lastUpdate');
        if (lastUp) {
            const now = new Date();
            lastUp.innerText = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        }
        if (syncStatus) syncStatus.innerText = 'Live Feed Connected';
    } catch (err) {
        console.error('Failed to load performance data:', err);
        if (syncStatus) syncStatus.innerText = 'Sync Error';
    }
}

function renderScorecard(scorecard) {
    if (!scorecard) return;

    const gradeEl = document.getElementById('scorecardGrade');
    if (gradeEl) {
        gradeEl.innerText = scorecard.overall_grade;
        gradeEl.className = 'grade-badge-huge';
        if (scorecard.overall_grade.startsWith('A')) gradeEl.classList.add('grade-a');
        else if (scorecard.overall_grade.startsWith('B')) gradeEl.classList.add('grade-b');
        else gradeEl.classList.add('grade-c');
    }

    const numEl = document.getElementById('scorecardNumeric');
    if (numEl) numEl.innerText = `${scorecard.overall_score} / 100`;

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
    if (bar) bar.style.width = `${Math.min(100, Math.max(0, val))}%`;
    if (lbl) lbl.innerText = `${val}/100`;
}

function renderKPIs(kpis) {
    if (!kpis) return;

    setVal('kpiNav', formatCurrency(kpis.nav));
    setVal('kpiNetPnl', formatCurrency(kpis.total_net_pnl, true));
    colorize('kpiNetPnl', kpis.total_net_pnl);

    setVal('kpiSharpe', kpis.annualized_sharpe != null ? kpis.annualized_sharpe.toFixed(2) : '--');
    setVal('kpiSortino', kpis.sortino_ratio != null ? kpis.sortino_ratio.toFixed(2) : '--');
    setVal('kpiCalmar', kpis.calmar_ratio != null ? kpis.calmar_ratio.toFixed(2) : '--');

    setVal('kpiProfitFactor', kpis.profit_factor != null ? kpis.profit_factor.toFixed(2) : '--');
    setVal('kpiExpectancy', formatCurrency(kpis.expectancy_dollars, true));
    colorize('kpiExpectancy', kpis.expectancy_dollars);

    setVal('kpiSlippage', `$${kpis.avg_slippage_per_share.toFixed(3)}/sh`);
    setVal('kpiEqRatio', `${kpis.effective_over_quoted_ratio_pct.toFixed(1)}%`);

    setVal('kpiMaxDd', `${kpis.max_drawdown_pct.toFixed(2)}%`);
    setVal('kpiDdReduction', `${kpis.mdd_reduction_vs_benchmark_pct.toFixed(1)}% safer vs SPY`);
}

function renderBenchmarks(benchmarks) {
    if (!benchmarks) return;

    // SEC Rule 605
    renderBenchmarkTable('tableSec605', benchmarks.sec_rule_605);

    // Institutional CTA
    renderBenchmarkTable('tableInstCta', benchmarks.institutional_cta);

    // Buy & Hold
    renderBenchmarkTable('tableBuyHold', benchmarks.buy_and_hold);
}

function renderBenchmarkTable(tableId, list) {
    const tbody = document.getElementById(tableId);
    if (!tbody || !list) return;

    tbody.innerHTML = list.map(m => `
        <tr>
            <td>
                <div style="font-weight: 600; color: #f8fafc;">${m.name}</div>
                <div style="font-size: 0.75rem; color: #94a3b8; margin-top: 0.2rem;">${m.description}</div>
            </td>
            <td class="font-mono" style="font-weight: 700; color: #38bdf8;">${m.engine_formatted}</td>
            <td class="font-mono" style="color: #94a3b8;">${m.benchmark_formatted}</td>
            <td>
                <span class="status-pill status-${m.status.toLowerCase()}">${m.status}</span>
            </td>
        </tr>
    `).join('');
}

function renderBots(bots) {
    const tbody = document.getElementById('botsTableBody');
    const pillsContainer = document.getElementById('botScopePills');
    if (!tbody || !bots) return;

    // Render table
    tbody.innerHTML = bots.map(b => `
        <tr>
            <td>
                <div class="symbol-badge">
                    <span>${b.symbol}</span>
                    <span style="font-size: 0.72rem; color: #94a3b8; font-weight: normal;">(${b.strategy})</span>
                </div>
            </td>
            <td>
                <span class="bot-badge-tag badge-${b.status_badge.toLowerCase()}">${b.status_badge}</span>
            </td>
            <td class="font-mono" style="font-weight: 700;">${b.rating_grade} <span style="font-size: 0.75rem; color: #94a3b8;">(${b.rating_score})</span></td>
            <td class="font-mono">${b.trades_count}</td>
            <td class="font-mono">${b.win_rate_pct.toFixed(1)}%</td>
            <td class="font-mono" style="color: ${b.net_pnl >= 0 ? '#34d399' : '#f87171'}; font-weight: 600;">
                ${formatCurrency(b.net_pnl, true)}
            </td>
            <td class="font-mono">${b.profit_factor != null ? b.profit_factor.toFixed(2) : '--'}</td>
            <td class="font-mono">${b.sharpe_ratio != null ? b.sharpe_ratio.toFixed(2) : '--'}</td>
            <td class="font-mono" style="color: #fbbf24;">${b.max_drawdown_pct.toFixed(2)}%</td>
            <td class="font-mono">$${b.avg_slippage_per_share.toFixed(3)}/sh</td>
            <td style="font-size: 0.8rem; color: #cbd5e1; max-width: 280px; white-space: normal;">
                ${b.diagnostic_finding}
            </td>
        </tr>
    `).join('');

    // Dynamic scope pills if not built
    if (pillsContainer && pillsContainer.children.length <= 1) {
        bots.forEach(b => {
            const pill = document.createElement('div');
            pill.className = `bot-scope-pill ${currentBotScope === b.symbol ? 'active' : ''}`;
            pill.dataset.scope = b.symbol;
            pill.onclick = () => setBotScope(b.symbol);
            pill.innerHTML = `<span class="scope-dot" style="background:#38bdf8;"></span> ${b.symbol}`;
            pillsContainer.appendChild(pill);
        });
    }

    const activeBotsCount = document.getElementById('activeBots');
    if (activeBotsCount) activeBotsCount.innerText = `${bots.length} Active`;
}

function renderDiagnostics(diagnostics) {
    const grid = document.getElementById('diagnosticsGrid');
    if (!grid || !diagnostics) return;

    grid.innerHTML = diagnostics.map(d => `
        <div class="diag-card diag-${d.level.toLowerCase()}">
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

function renderBenchmarkChart(scorecard, benchmarks) {
    const ctx = document.getElementById('benchmarkRadarChart');
    if (!ctx) return;

    if (performanceChart) {
        performanceChart.destroy();
    }

    const labels = [
        'Execution Quality',
        'Risk-Adjusted Alpha',
        'Trade Expectancy',
        'Capital Preservation',
        'Cost Efficiency'
    ];

    const engineData = [
        scorecard.execution_score,
        scorecard.risk_adjusted_score,
        scorecard.expectancy_score,
        scorecard.capital_preservation_score,
        90
    ];

    const benchmarkData = [80, 70, 65, 55, 75]; // Institutional median baseline

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
                    label: 'Barclay CTA / Institutional Standard',
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
                    suggestedMin: 30,
                    suggestedMax: 100,
                    ticks: {
                        backdropColor: 'transparent',
                        color: '#64748b',
                        stepSize: 20
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
    window.open(`/api/trade-history/export`, '_blank');
}
