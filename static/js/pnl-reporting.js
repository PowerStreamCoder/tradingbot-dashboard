/**
 * Institutional Multi-Bot P&L Statement & Portfolio Analytics Controller
 * Version: 3.0.0
 * Connects to FastAPI endpoint GET /api/pnl-statement
 */

let currentPeriod = 'YTD';
let currentBotScope = 'ALL';
let currentMode = 'all';
let currentStatementData = null;
let equityChart = null;
let donutChart = null;

// Bot color palette mapping
const BOT_COLORS = {
    'NVDA': '#10b981',
    'IWM': '#06b6d4',
    'SPY': '#f59e0b',
    'QQQ': '#ec4899',
    'DEFAULT': '#818cf8'
};

function getBotColor(symbol) {
    const sym = (symbol || '').toUpperCase();
    return BOT_COLORS[sym] || BOT_COLORS.DEFAULT;
}

/**
 * Format currency with signs
 */
function formatCurrency(val, includePlus = false) {
    if (val === null || val === undefined || isNaN(val)) return '$0.00';
    const num = Number(val);
    const formatted = Math.abs(num).toLocaleString('en-US', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });
    if (num < 0) return `-$${formatted}`;
    if (includePlus && num > 0) return `+$${formatted}`;
    return `$${formatted}`;
}

function formatPercent(val, includePlus = false) {
    if (val === null || val === undefined || isNaN(val)) return '0.0%';
    const num = Number(val);
    const formatted = Math.abs(num).toFixed(2);
    if (num < 0) return `-${formatted}%`;
    if (includePlus && num > 0) return `+${formatted}%`;
    return `${formatted}%`;
}

/**
 * Main Data Fetcher
 */
async function loadStatement(period = currentPeriod, botScope = currentBotScope, mode = currentMode) {
    currentPeriod = period;
    currentBotScope = botScope;
    currentMode = mode;

    const syncStatus = document.getElementById('syncStatusText');
    if (syncStatus) syncStatus.textContent = 'Syncing statement...';

    try {
        const url = `/api/pnl-statement?period=${encodeURIComponent(period)}&bot=${encodeURIComponent(botScope)}&mode=${encodeURIComponent(mode)}`;
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(`HTTP error ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();
        currentStatementData = data;

        // Render all UI modules
        renderExecutiveKPIs(data.executive_kpis);
        renderCashflowWaterfall(data.cashflow_waterfall);
        renderBotAttributionTable(data.bot_attribution);
        renderEquityChart(data.equity_curve);
        renderCalendarHeatmap(data.calendar_heatmap);
        renderDonutChart(data.bot_attribution);
        renderRiskInsights(data.risk_insights);
        updateScopePills(data.bot_attribution);

        // Header metadata
        const activeBotsElem = document.getElementById('activeBots');
        if (activeBotsElem) {
            const modeText = mode === 'paper' ? 'Paper' : (mode === 'live' ? 'Live' : 'Multi-Mode');
            activeBotsElem.textContent = `${data.bot_attribution.length} Bots (${modeText})`;
        }
        const lastUpdateElem = document.getElementById('lastUpdate');
        if (lastUpdateElem) {
            lastUpdateElem.textContent = new Date().toLocaleTimeString();
        }

        if (syncStatus) {
            const cacheStatus = data.data_quality?.cached ? ' (Cached)' : '';
            syncStatus.textContent = `Live Feed Connected${cacheStatus}`;
        }
    } catch (err) {
        console.error('Failed to load P&L statement:', err);
        if (syncStatus) syncStatus.textContent = 'Sync Error';
    }
}


/**
 * Render Executive KPIs
 */
function renderExecutiveKPIs(kpis) {
    if (!kpis) return;

    // NAV
    const kpiNav = document.getElementById('kpiNav');
    if (kpiNav) kpiNav.textContent = formatCurrency(kpis.nav);

    const kpiNavGrowth = document.getElementById('kpiNavGrowth');
    if (kpiNavGrowth) {
        kpiNavGrowth.textContent = formatPercent(kpis.nav_growth_pct, true);
        kpiNavGrowth.className = `stat-badge ${kpis.nav_growth_pct >= 0 ? 'pos' : 'neg'}`;
    }

    const kpiStartingNav = document.getElementById('kpiStartingNav');
    if (kpiStartingNav) kpiStartingNav.textContent = formatCurrency(kpis.starting_nav);

    // Total Net PnL
    const kpiTotalPnl = document.getElementById('kpiTotalPnl');
    if (kpiTotalPnl) {
        kpiTotalPnl.textContent = formatCurrency(kpis.total_net_pnl, true);
        kpiTotalPnl.className = `kpi-card-value font-mono ${kpis.total_net_pnl >= 0 ? 'val-pos' : 'val-neg'}`;
    }

    const kpiRealizedSplit = document.getElementById('kpiRealizedSplit');
    if (kpiRealizedSplit) {
        kpiRealizedSplit.textContent = `Realized: ${formatCurrency(kpis.realized_pnl, true)}`;
        kpiRealizedSplit.className = `stat-badge ${kpis.realized_pnl >= 0 ? 'pos' : 'neg'}`;
    }

    const kpiUnrealizedSplit = document.getElementById('kpiUnrealizedSplit');
    if (kpiUnrealizedSplit) {
        kpiUnrealizedSplit.textContent = formatCurrency(kpis.unrealized_pnl, true);
    }

    // Profit Factor & Win Rate
    const kpiPF = document.getElementById('kpiProfitFactor');
    if (kpiPF) {
        const pfVal = kpis.profit_factor !== null && kpis.profit_factor !== undefined ? kpis.profit_factor.toFixed(2) : '--';
        kpiPF.innerHTML = `${pfVal} <span class="kpi-unit">(PF)</span>`;
    }

    const kpiWinRate = document.getElementById('kpiWinRate');
    if (kpiWinRate) {
        kpiWinRate.textContent = `${kpis.win_rate_pct.toFixed(1)}% Win Rate`;
        kpiWinRate.className = `stat-badge ${kpis.win_rate_pct >= 50 ? 'pos' : 'neg'}`;
    }

    const kpiTotalTrades = document.getElementById('kpiTotalTrades');
    if (kpiTotalTrades) kpiTotalTrades.textContent = `${kpis.total_trades} Total Trades`;

    // Alpha vs Benchmark
    const kpiAlpha = document.getElementById('kpiAlpha');
    if (kpiAlpha) {
        kpiAlpha.textContent = `${formatPercent(kpis.alpha_pct, true)} Alpha`;
        kpiAlpha.className = `kpi-card-value font-mono ${kpis.alpha_pct >= 0 ? 'val-pos' : 'val-neg'}`;
    }

    const kpiBotReturn = document.getElementById('kpiBotReturn');
    if (kpiBotReturn) kpiBotReturn.textContent = `Bot: ${formatPercent(kpis.nav_growth_pct, true)}`;

    const kpiSpyReturn = document.getElementById('kpiSpyReturn');
    if (kpiSpyReturn) kpiSpyReturn.textContent = formatPercent(kpis.benchmark_return_pct, true);

    // Sharpe & Drawdown
    const kpiSharpe = document.getElementById('kpiSharpe');
    if (kpiSharpe) {
        const sVal = kpis.sharpe_ratio !== null && kpis.sharpe_ratio !== undefined ? kpis.sharpe_ratio.toFixed(2) : '--';
        kpiSharpe.innerHTML = `${sVal} <span class="kpi-unit">Sharpe</span>`;
    }

    const kpiDrawdown = document.getElementById('kpiDrawdown');
    if (kpiDrawdown) {
        kpiDrawdown.textContent = `${formatPercent(kpis.max_drawdown_pct)} Max DD`;
    }

    const kpiSortino = document.getElementById('kpiSortino');
    if (kpiSortino) {
        kpiSortino.textContent = kpis.sortino_ratio !== null && kpis.sortino_ratio !== undefined ? kpis.sortino_ratio.toFixed(2) : '--';
    }

    // Free Cash & Margin
    const kpiFreeCash = document.getElementById('kpiFreeCash');
    if (kpiFreeCash) kpiFreeCash.textContent = formatCurrency(kpis.free_cash);

    const kpiCashBuffer = document.getElementById('kpiCashBuffer');
    if (kpiCashBuffer) kpiCashBuffer.textContent = `${kpis.cash_buffer_pct.toFixed(1)}% Buffer`;

    const kpiMarginUtil = document.getElementById('kpiMarginUtil');
    if (kpiMarginUtil) kpiMarginUtil.textContent = `${kpis.margin_utilization_pct.toFixed(1)}%`;
}

/**
 * Render Cashflow Waterfall
 */
function renderCashflowWaterfall(wf) {
    if (!wf) return;

    const grossWins = document.getElementById('waterfallGrossWins');
    if (grossWins) grossWins.textContent = formatCurrency(wf.gross_wins, true);

    const grossLosses = document.getElementById('waterfallGrossLosses');
    if (grossLosses) grossLosses.textContent = formatCurrency(wf.gross_losses);

    const optYield = document.getElementById('waterfallOptionYield');
    if (optYield) optYield.textContent = formatCurrency(wf.option_premium_harvested, true);

    const comms = document.getElementById('waterfallCommissions');
    if (comms) comms.textContent = formatCurrency(wf.commissions_and_fees);

    const financing = document.getElementById('waterfallFinancing');
    if (financing) financing.textContent = formatCurrency(wf.net_financing, true);

    const netCash = document.getElementById('waterfallNetCash');
    if (netCash) netCash.textContent = formatCurrency(wf.net_cash_generated, true);
}

/**
 * Render Multi-Bot Attribution Table
 */
function renderBotAttributionTable(bots) {
    const tbody = document.getElementById('statementTableBody');
    const badge = document.getElementById('attributionCountBadge');
    if (!tbody) return;

    if (!bots || bots.length === 0) {
        tbody.innerHTML = `<tr><td colspan="14" style="text-align:center; padding: 24px; color: var(--text-muted);">No bot attribution records found for this mode and period.</td></tr>`;
        if (badge) badge.textContent = '0 Bots Tracked';
        return;
    }

    if (badge) badge.textContent = `${bots.length} Bots Tracked`;

    let html = '';
    let totalAlloc = 0;
    let totalTrades = 0;
    let totalWins = 0;
    let totalLosses = 0;
    let totalGross = 0;
    let totalComms = 0;
    let totalRealized = 0;
    let totalUnrealized = 0;
    let totalNet = 0;

    bots.forEach(b => {
        const color = getBotColor(b.symbol);
        const pnlClass = b.total_net_pnl >= 0 ? 'val-pos' : 'val-neg';
        const realizedClass = b.realized_pnl >= 0 ? 'val-pos' : 'val-neg';
        const unrealizedClass = b.unrealized_pnl >= 0 ? 'val-pos' : 'val-neg';

        const bMode = (b.trading_mode || 'PAPER').toUpperCase();
        const modeBadgeHtml = bMode === 'LIVE'
            ? '<span class="badge-mode live">🟢 LIVE</span>'
            : (bMode === 'PAPER' 
                ? '<span class="badge-mode paper">🧪 PAPER</span>' 
                : '<span class="badge-mode both">🔄 BOTH</span>');

        totalAlloc += b.allocated_capital;
        totalTrades += b.trades_count;
        totalWins += b.winning_trades;
        totalLosses += b.losing_trades;
        totalGross += b.gross_pnl;
        totalComms += b.commissions;
        totalRealized += b.realized_pnl;
        totalUnrealized += b.unrealized_pnl;
        totalNet += b.total_net_pnl;

        html += `
        <tr>
            <td class="sticky-col">
                <span class="bot-tag-badge" style="background:${color};">${b.symbol}</span>
                <strong>${b.bot_id}</strong>
                <div style="font-size:11px;color:var(--text-muted);">${b.strategy}</div>
            </td>
            <td class="font-mono">${formatCurrency(b.allocated_capital)} (${b.allocation_pct}%)</td>
            <td><span class="stat-badge ${b.status === 'RUNNING' ? 'pos' : 'neutral'}">${b.status}</span></td>
            <td>${modeBadgeHtml}</td>
            <td class="font-mono">${b.trades_count} (${b.winning_trades} / ${b.losing_trades})</td>
            <td class="font-mono ${b.win_rate_pct >= 50 ? 'val-pos' : 'val-neg'}">${b.win_rate_pct.toFixed(1)}%</td>
            <td class="font-mono">${b.profit_factor !== null && b.profit_factor !== undefined ? b.profit_factor.toFixed(2) : '--'}</td>
            <td class="font-mono">${b.payoff_ratio !== null && b.payoff_ratio !== undefined ? `1 : ${b.payoff_ratio.toFixed(2)}` : '--'}</td>
            <td class="font-mono ${b.gross_pnl >= 0 ? 'val-pos' : 'val-neg'}">${formatCurrency(b.gross_pnl, true)}</td>
            <td class="font-mono val-neg">${formatCurrency(b.commissions)}</td>
            <td class="font-mono ${realizedClass}">${formatCurrency(b.realized_pnl, true)}</td>
            <td class="font-mono ${unrealizedClass}">${formatCurrency(b.unrealized_pnl, true)}</td>
            <td class="font-mono ${pnlClass}"><strong>${formatCurrency(b.total_net_pnl, true)}</strong></td>
            <td class="font-mono ${pnlClass}">${formatPercent(b.roc_pct, true)}</td>
        </tr>
        `;
    });

    // Summary Row
    const winRateTotal = totalTrades > 0 ? (totalWins / totalTrades * 100).toFixed(1) : '0.0';
    const rocTotal = totalAlloc > 0 ? (totalNet / totalAlloc * 100).toFixed(1) : '0.0';

    html += `
    <tr class="total-summary-row">
        <td class="sticky-col">Combined Total (${bots.length} Bots)</td>
        <td class="font-mono">${formatCurrency(totalAlloc)} (100%)</td>
        <td><span class="stat-badge pos">Operational</span></td>
        <td><span class="badge-mode ${currentMode === 'all' ? 'both' : currentMode}">${currentMode.toUpperCase()}</span></td>
        <td class="font-mono">${totalTrades} (${totalWins} / ${totalLosses})</td>
        <td class="font-mono val-pos">${winRateTotal}%</td>
        <td class="font-mono">--</td>
        <td class="font-mono">--</td>
        <td class="font-mono ${totalGross >= 0 ? 'val-pos' : 'val-neg'}">${formatCurrency(totalGross, true)}</td>
        <td class="font-mono val-neg">${formatCurrency(totalComms)}</td>
        <td class="font-mono ${totalRealized >= 0 ? 'val-pos' : 'val-neg'}">${formatCurrency(totalRealized, true)}</td>
        <td class="font-mono ${totalUnrealized >= 0 ? 'val-pos' : 'val-neg'}">${formatCurrency(totalUnrealized, true)}</td>
        <td class="font-mono ${totalNet >= 0 ? 'val-pos' : 'val-neg'}"><strong>${formatCurrency(totalNet, true)}</strong></td>
        <td class="font-mono val-pos">+${rocTotal}%</td>
    </tr>
    `;

    tbody.innerHTML = html;
}


/**
 * Render Equity Growth Chart (Chart.js)
 */
function renderEquityChart(eqData) {
    const canvas = document.getElementById('equityGrowthChart');
    if (!canvas || !eqData) return;

    const ctx = canvas.getContext('2d');
    if (equityChart) {
        equityChart.destroy();
    }

    const gradient = ctx.createLinearGradient(0, 0, 0, 300);
    gradient.addColorStop(0, 'rgba(99, 102, 241, 0.45)');
    gradient.addColorStop(1, 'rgba(99, 102, 241, 0.0)');

    equityChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: eqData.labels,
            datasets: [
                {
                    label: 'Bot Portfolio Return (%)',
                    data: eqData.portfolio_returns_pct,
                    borderColor: '#6366f1',
                    borderWidth: 3,
                    backgroundColor: gradient,
                    fill: true,
                    tension: 0.35,
                    pointBackgroundColor: '#818cf8',
                    pointBorderColor: '#080c1e',
                    pointRadius: 4,
                    pointHoverRadius: 6
                },
                {
                    label: 'SPY Benchmark (%)',
                    data: eqData.benchmark_returns_pct,
                    borderColor: '#64748b',
                    borderWidth: 2,
                    borderDash: [5, 5],
                    backgroundColor: 'transparent',
                    fill: false,
                    tension: 0.3,
                    pointRadius: 2
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: '#151c3d',
                    titleColor: '#f8fafc',
                    bodyColor: '#cbd5e1',
                    borderColor: 'rgba(99, 122, 185, 0.3)',
                    borderWidth: 1,
                    padding: 12,
                    callbacks: {
                        label: function(ctx) {
                            return ` ${ctx.dataset.label}: ${formatPercent(ctx.parsed.y, true)}`;
                        }
                    }
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: { color: '#94a3b8', font: { family: 'JetBrains Mono', size: 11 } }
                },
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: {
                        color: '#94a3b8',
                        font: { family: 'JetBrains Mono', size: 11 },
                        callback: function(v) { return formatPercent(v, true); }
                    }
                }
            }
        }
    });
}

/**
 * Render Calendar Heatmap
 */
function renderCalendarHeatmap(heatmapDays) {
    const grid = document.getElementById('calendarHeatmapGrid');
    if (!grid) return;

    if (!heatmapDays || heatmapDays.length === 0) {
        grid.innerHTML = `<div style="grid-column: span 7; text-align:center; padding: 24px; color:var(--text-muted);">No calendar trading sessions in this period.</div>`;
        return;
    }

    let html = '';
    heatmapDays.forEach(day => {
        let chipClass = 'pnl-gain-light';
        if (day.intensity === 'heavy_gain') chipClass = 'pnl-gain-heavy';
        else if (day.intensity === 'heavy_loss') chipClass = 'pnl-loss-heavy';
        else if (day.intensity === 'light_loss') chipClass = 'pnl-loss-light';

        const pnlSign = day.pnl >= 0 ? '+' : '';
        const pnlColorClass = day.pnl >= 0 ? 'val-pos' : 'val-neg';

        html += `
        <div class="cal-day-cell ${chipClass}">
            <span class="cal-day-num">${day.date.substring(5)}</span>
            <span class="cal-day-pnl font-mono ${pnlColorClass}">${pnlSign}$${Math.abs(day.pnl).toLocaleString()}</span>
            <span class="cal-day-trades">${day.trades} trades</span>
        </div>
        `;
    });

    grid.innerHTML = html;
}

/**
 * Render Donut Chart
 */
function renderDonutChart(bots) {
    const canvas = document.getElementById('botContributionDonut');
    const footer = document.getElementById('donutSummaryFooter');
    if (!canvas || !bots || bots.length === 0) return;

    const ctx = canvas.getContext('2d');
    if (donutChart) {
        donutChart.destroy();
    }

    const labels = bots.map(b => b.symbol);
    const data = bots.map(b => Math.max(0, b.total_net_pnl));
    const colors = bots.map(b => getBotColor(b.symbol));

    donutChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: data,
                backgroundColor: colors,
                borderColor: '#080c1e',
                borderWidth: 3,
                hoverOffset: 6
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '70%',
            plugins: {
                legend: { display: false }
            }
        }
    });

    // Populate footer
    if (footer) {
        const total = data.reduce((a, b) => a + b, 0);
        let fHtml = '';
        bots.forEach(b => {
            const val = Math.max(0, b.total_net_pnl);
            const pct = total > 0 ? ((val / total) * 100).toFixed(1) : '0.0';
            const col = getBotColor(b.symbol);
            fHtml += `
            <div>
                <span style="font-size:11px;color:var(--text-muted);">${b.symbol}</span>
                <div class="donut-bot-share-val font-mono" style="color:${col};">${pct}%</div>
            </div>
            `;
        });
        footer.innerHTML = fHtml;
    }
}

/**
 * Render Risk Insights
 */
function renderRiskInsights(insights) {
    const grid = document.getElementById('riskInsightsGrid');
    if (!grid || !insights) return;

    let html = '';
    insights.forEach(item => {
        let icon = '💡';
        let iconColor = '#818cf8';
        let iconBg = 'rgba(99, 102, 241, 0.15)';

        if (item.type === 'ALPHA') {
            icon = '🎯';
            iconColor = '#10b981';
            iconBg = 'rgba(16, 185, 129, 0.15)';
        } else if (item.severity === 'WARNING') {
            icon = '⚠️';
            iconColor = '#f59e0b';
            iconBg = 'rgba(245, 158, 11, 0.15)';
        } else if (item.type === 'RISK') {
            icon = '🛡️';
            iconColor = '#06b6d4';
            iconBg = 'rgba(6, 182, 212, 0.15)';
        }

        html += `
        <div class="insight-card">
            <div class="insight-icon-box" style="background:${iconBg}; color:${iconColor};">
                ${icon}
            </div>
            <div>
                <h4>${item.title}</h4>
                <p>${item.description}</p>
            </div>
        </div>
        `;
    });

    grid.innerHTML = html;
}

/**
 * Populate Bot Scope Pills
 */
function updateScopePills(bots) {
    const container = document.getElementById('botScopePills');
    if (!container || !bots) return;

    let html = `
    <div class="bot-scope-pill ${currentBotScope === 'ALL' ? 'active' : ''}" data-scope="ALL" onclick="setBotScope('ALL')">
        <span class="scope-dot" style="background:#818cf8;"></span> All Bots Combined
    </div>
    `;

    bots.forEach(b => {
        const col = getBotColor(b.symbol);
        const isActive = currentBotScope.toUpperCase() === b.symbol.toUpperCase();
        html += `
        <div class="bot-scope-pill ${isActive ? 'active' : ''}" data-scope="${b.symbol}" onclick="setBotScope('${b.symbol}')">
            <span class="scope-dot" style="background:${col};"></span> ${b.symbol}
        </div>
        `;
    });

    container.innerHTML = html;
}

/**
 * Interactive Controls
 */
function setStatementPeriod(period) {
    document.querySelectorAll('.timeline-tab').forEach(t => {
        t.classList.toggle('active', t.getAttribute('data-period') === period);
    });
    loadStatement(period, currentBotScope, currentMode);
}

function setTradingMode(mode) {
    currentMode = mode;
    document.querySelectorAll('.mode-tab').forEach(t => {
        t.classList.toggle('active', t.getAttribute('data-mode') === mode);
    });
    loadStatement(currentPeriod, currentBotScope, currentMode);
}

function setBotScope(scope) {
    document.querySelectorAll('.bot-scope-pill').forEach(p => {
        p.classList.toggle('active', p.getAttribute('data-scope') === scope);
    });
    loadStatement(currentPeriod, scope, currentMode);
}

function exportStatementCSV() {
    if (!currentStatementData) {
        alert('Statement data not yet loaded.');
        return;
    }

    const rows = [
        ['Bot ID', 'Symbol', 'Strategy', 'Mode', 'Allocation ($)', 'Allocation (%)', 'Trades', 'Wins', 'Losses', 'Win Rate (%)', 'Profit Factor', 'Gross P&L ($)', 'Commissions ($)', 'Realized P&L ($)', 'Unrealized P&L ($)', 'Total Net P&L ($)', 'ROC (%)']
    ];

    currentStatementData.bot_attribution.forEach(b => {
        rows.push([
            b.bot_id,
            b.symbol,
            b.strategy,
            b.trading_mode || 'PAPER',
            b.allocated_capital,
            b.allocation_pct,
            b.trades_count,
            b.winning_trades,
            b.losing_trades,
            b.win_rate_pct,
            b.profit_factor || 'N/A',
            b.gross_pnl,
            b.commissions,
            b.realized_pnl,
            b.unrealized_pnl,
            b.total_net_pnl,
            b.roc_pct
        ]);
    });

    const csvContent = "data:text/csv;charset=utf-8," + rows.map(e => e.join(",")).join("\n");
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `pnl_statement_${currentPeriod}_${currentMode}_${currentBotScope}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

// Auto-run on page load
document.addEventListener('DOMContentLoaded', () => {
    loadStatement('YTD', 'ALL', 'all');
    // Refresh periodically (every 45s)
    setInterval(() => {
        loadStatement(currentPeriod, currentBotScope, currentMode);
    }, 45000);
});

