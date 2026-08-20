/**
 * Trade Charts Module - Daily Trade Visualization
 * Version: 2.0.0 - Code review fixes applied
 *
 * Features:
 * - Per-bot daily trade and active position visualization
 * - Shows both completed trades AND active bucket positions
 * - Entry/Exit price visualization for completed trades
 * - Reference prices for active buckets
 * - Trade sequence markers
 * - Automatic daily reset at market open
 * - Profit/Loss color coding
 */

// Chart instances storage (one per bot)
const tradeCharts = {};

// Bot names (loaded dynamically from /api/bot-configs)
let BOT_NAMES = {};
let configReady = false;

/**
 * Load bot configurations from API
 *
 * @returns {Promise<boolean>} True if successful, false on error
 * @throws Never throws - handles errors internally
 */
async function loadBotConfigs() {
    try {
        const response = await fetch(API_ENDPOINTS.BOT_CONFIGS);

        if (!response.ok) {
            throw new Error(`API returned ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();

        if (data.error) {
            Logger.error('TradeCharts', 'Failed to load bot configs', data.error);
            BOT_NAMES = {1: '⚠️ CONFIG ERROR'};
            configReady = false;
            return false;
        }

        // Validate data structure
        if (!data.bots || !Array.isArray(data.bots)) {
            Logger.error('TradeCharts', 'Invalid API response structure', data);
            BOT_NAMES = {1: '⚠️ CONFIG ERROR'};
            configReady = false;
            return false;
        }

        // Build BOT_NAMES mapping
        data.bots.forEach((bot, index) => {
            const botId = index + 1;
            BOT_NAMES[botId] = bot.symbol || `Bot ${botId}`;
        });

        configReady = true;

        Logger.info('TradeCharts', 'Bot configurations loaded', BOT_NAMES);

        return true;

    } catch (error) {
        Logger.error('TradeCharts', 'Error loading bot configs', error);
        BOT_NAMES = {1: '⚠️ CONFIG ERROR'};
        configReady = false;
        return false;
    }
}

// Market open time (9:30 AM ET)
const MARKET_OPEN_HOUR = 9;
const MARKET_OPEN_MINUTE = 30;

/**
 * Initialize trade charts for all active bots
 * @param {Array} trades - Array of completed trade objects from API
 * @param {Object} botData - Bot overview data with active buckets
 */
function initializeTradeCharts(trades, botData) {
    if ((!trades || trades.length === 0) && (!botData || Object.keys(botData).length === 0)) {
        console.log('No trades or active buckets available for charts');
        return;
    }

    // Group trades by bot
    const tradesByBot = groupTradesByBot(trades || []);

    // Get all bot IDs from both trades and bucket data
    const allBotIds = new Set([
        ...Object.keys(tradesByBot),
        ...Object.keys(botData || {}).map(k => k.replace('bot', ''))
    ]);

    // Create chart for each bot in its dedicated container
    allBotIds.forEach(botId => {
        const botTrades = tradesByBot[botId] || [];
        const botName = getBotName(parseInt(botId));
        const botBuckets = botData ? botData[`bot${botId}`] : null;

        // Find the bot's chart container
        const container = document.getElementById(`tradeChart-bot${botId}`);
        if (!container) {
            console.warn(`Chart container not found for bot ${botId}`);
            return;
        }

        // Clear container and create chart
        container.innerHTML = '';
        createBotTradeChart(botId, botName, botTrades, botBuckets, container);
    });
}

/**
 * Group trades by bot ID
 * @param {Array} trades - Array of trade objects
 * @returns {Object} Trades grouped by bot ID
 */
function groupTradesByBot(trades) {
    const grouped = {};

    trades.forEach(trade => {
        const botId = trade.botId || 1;
        if (!grouped[botId]) {
            grouped[botId] = [];
        }
        grouped[botId].push(trade);
    });

    return grouped;
}

/**
 * Get bot name from bot ID
 * @param {number} botId - Bot ID
 * @returns {string} Bot name
 */
function getBotName(botId) {
    return BOT_NAMES[botId] || `Bot ${botId}`;
}

/**
 * Filter trades for today only
 * @param {Array} trades - Array of trade objects
 * @returns {Array} Today's trades only
 */
function filterTodayTrades(trades) {
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    return trades.filter(trade => {
        const tradeDate = new Date(trade.closeTimestamp || trade.timestamp);
        tradeDate.setHours(0, 0, 0, 0);
        return tradeDate.getTime() === today.getTime();
    });
}

/**
 * Extract active buckets from bot data
 * @param {Object} botBuckets - Bot bucket data (bucket1, bucket2, etc.)
 * @returns {Array} Array of active bucket objects
 */
function extractActiveBuckets(botBuckets) {
    if (!botBuckets) return [];

    const activeBuckets = [];

    Object.keys(botBuckets).forEach((key, index) => {
        const bucket = botBuckets[key];

        // Check if bucket has data (entry price exists)
        if (bucket && bucket.entryPrice !== null && bucket.entryPrice !== undefined) {
            const bucketId = parseInt(key.replace('bucket', ''));

            // Determine bucket type based on reference prices
            let bucketType = 'Unknown';
            if (bucket.referencePriceBefore && bucket.entryPrice) {
                bucketType = bucket.entryPrice >= bucket.referencePriceBefore ? 'Upward' : 'Downward';
            }

            activeBuckets.push({
                id: bucketId,
                entry: bucket.entryPrice,
                refBefore: bucket.referencePriceBefore || bucket.entryPrice - 0.25,
                refAfter: bucket.referencePriceAfter || bucket.entryPrice + 0.50,
                type: bucketType,
                index: activeBuckets.length  // For x-axis positioning
            });
        }
    });

    return activeBuckets;
}

/**
 * Create trade chart for a specific bot
 * @param {string} botId - Bot ID
 * @param {string} botName - Bot display name
 * @param {Array} allTrades - All trades for this bot
 * @param {Object} botBuckets - Active bucket data for this bot
 * @param {HTMLElement} container - Container element
 */
function createBotTradeChart(botId, botName, allTrades, botBuckets, container) {
    // Filter for today's trades only
    const todayTrades = filterTodayTrades(allTrades);

    // Extract active buckets
    const activeBuckets = extractActiveBuckets(botBuckets);

    // Create chart container
    const chartDiv = document.createElement('div');
    chartDiv.style.marginBottom = '30px';
    chartDiv.style.padding = '20px';
    chartDiv.style.backgroundColor = '#f8f9fa';
    chartDiv.style.borderRadius = '8px';
    chartDiv.innerHTML = `
        <h3 style="margin-top: 0; color: #2c3e50;">${botName} - Today's Activity</h3>
        <div style="background: white; padding: 15px; border-radius: 6px;">
            <canvas id="tradeChart-${botId}" style="max-height: 400px;"></canvas>
        </div>
        <div id="tradeStats-${botId}" style="margin-top: 15px; padding: 10px; background: white; border-radius: 6px; font-size: 0.9em;">
            <strong>Statistics:</strong>
            <span id="statsContent-${botId}">Loading...</span>
        </div>
    `;
    container.appendChild(chartDiv);

    // Prepare chart data
    const chartData = prepareChartData(todayTrades, activeBuckets);

    // Update statistics
    updateTradeStatistics(botId, todayTrades, activeBuckets);

    // Create chart
    const canvas = document.getElementById(`tradeChart-${botId}`);
    const ctx = canvas.getContext('2d');

    // Destroy existing chart if exists
    if (tradeCharts[botId]) {
        tradeCharts[botId].destroy();
    }

    // Create new chart
    tradeCharts[botId] = new Chart(ctx, {
        type: 'scatter',
        data: {
            datasets: [
                {
                    label: 'Entry Price',
                    data: chartData.entries,
                    backgroundColor: '#3498db',
                    borderColor: '#2980b9',
                    pointRadius: 8,
                    pointHoverRadius: 10,
                    pointStyle: 'triangle',
                    showLine: false
                },
                {
                    label: 'Exit Price',
                    data: chartData.exits,
                    backgroundColor: chartData.exitColors,
                    borderColor: chartData.exitBorders,
                    pointRadius: 8,
                    pointHoverRadius: 10,
                    pointStyle: 'rectRot',
                    showLine: false
                },
                {
                    label: 'Active Bucket (Entry)',
                    data: chartData.activeBucketEntries,
                    backgroundColor: '#f39c12',
                    borderColor: '#e67e22',
                    pointRadius: 10,
                    pointHoverRadius: 12,
                    pointStyle: 'star',
                    showLine: false
                },
                {
                    label: 'Reference Before',
                    data: chartData.activeBucketRefBefore,
                    backgroundColor: '#95a5a6',
                    borderColor: '#7f8c8d',
                    pointRadius: 6,
                    pointHoverRadius: 8,
                    pointStyle: 'circle',
                    showLine: false
                },
                {
                    label: 'Reference After',
                    data: chartData.activeBucketRefAfter,
                    backgroundColor: '#9b59b6',
                    borderColor: '#8e44ad',
                    pointRadius: 6,
                    pointHoverRadius: 8,
                    pointStyle: 'circle',
                    showLine: false
                },
                {
                    label: 'Trade Path',
                    data: chartData.tradePaths,
                    backgroundColor: 'transparent',
                    borderColor: '#95a5a6',
                    borderWidth: 2,
                    borderDash: [5, 5],
                    pointRadius: 0,
                    showLine: true,
                    segment: {
                        borderColor: ctx => {
                            // Color code based on profit/loss
                            const trade = todayTrades[Math.floor(ctx.p0DataIndex / 2)];
                            if (trade && trade.profitLoss !== undefined) {
                                return trade.profitLoss > 0 ? '#27ae60' : '#e74c3c';
                            }
                            return '#95a5a6';
                        }
                    }
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            aspectRatio: 2.5,
            plugins: {
                title: {
                    display: true,
                    text: `Trades & Active Positions - ${new Date().toLocaleDateString()}`,
                    font: {
                        size: 16,
                        weight: 'bold'
                    }
                },
                legend: {
                    display: true,
                    position: 'top'
                },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            const datasetLabel = context.dataset.label || '';
                            const point = context.raw;

                            if (point.bucket) {
                                // Active bucket tooltip
                                const bucket = point.bucket;
                                return [
                                    `${datasetLabel}: $${context.parsed.y.toFixed(2)}`,
                                    `Bucket #${bucket.id}`,
                                    `Type: ${bucket.type}`,
                                    `Entry: $${bucket.entry.toFixed(2)}`,
                                    `Ref Before: $${bucket.refBefore.toFixed(2)}`,
                                    `Ref After: $${bucket.refAfter.toFixed(2)}`,
                                    `Status: 🟢 ACTIVE`
                                ];
                            } else if (point.trade) {
                                // Completed trade tooltip
                                const trade = point.trade;
                                const lines = [
                                    `${datasetLabel}: $${context.parsed.y.toFixed(2)}`,
                                    `Trade #${trade.seqNum || '?'}`,
                                    `Side: ${trade.bucketType || '?'}`,
                                    `Entry: $${trade.entryPrice?.toFixed(2) || '?'}`,
                                    `Exit: $${trade.exitPrice?.toFixed(2) || '?'}`,
                                    `P&L: $${trade.profitLoss?.toFixed(2) || '?'}`
                                ];

                                if (trade.validation) {
                                    lines.push(`Validation: ${trade.validation.matches ? '✓ Matched' : '⚠️ Mismatch'}`);
                                }

                                return lines;
                            }

                            return datasetLabel;
                        }
                    }
                }
            },
            scales: {
                x: {
                    type: 'linear',
                    position: 'bottom',
                    title: {
                        display: true,
                        text: 'Position Sequence'
                    },
                    ticks: {
                        stepSize: 1,
                        callback: function(value) {
                            return `#${Math.floor(value)}`;
                        }
                    }
                },
                y: {
                    title: {
                        display: true,
                        text: 'Price (USD)'
                    },
                    ticks: {
                        callback: function(value) {
                            return '$' + value.toFixed(2);
                        }
                    }
                }
            }
        }
    });
}

/**
 * Prepare chart data from trades and active buckets
 * @param {Array} trades - Array of completed trade objects
 * @param {Array} activeBuckets - Array of active bucket objects
 * @returns {Object} Chart data structure
 */
function prepareChartData(trades, activeBuckets) {
    const entries = [];
    const exits = [];
    const exitColors = [];
    const exitBorders = [];
    const tradePaths = [];
    const activeBucketEntries = [];
    const activeBucketRefBefore = [];
    const activeBucketRefAfter = [];

    // Add completed trades
    trades.forEach((trade, index) => {
        const tradeIndex = index + 1;

        // Entry point
        entries.push({
            x: tradeIndex,
            y: trade.entryPrice || 0,
            trade: trade
        });

        // Exit point
        const isProfit = (trade.profitLoss || 0) > 0;
        exits.push({
            x: tradeIndex,
            y: trade.exitPrice || 0,
            trade: trade
        });
        exitColors.push(isProfit ? '#27ae60' : '#e74c3c');
        exitBorders.push(isProfit ? '#229954' : '#c0392b');

        // Trade path (entry to exit)
        tradePaths.push({
            x: tradeIndex,
            y: trade.entryPrice || 0,
            trade: trade
        });
        tradePaths.push({
            x: tradeIndex,
            y: trade.exitPrice || 0,
            trade: trade
        });
    });

    // Add active buckets (positioned after completed trades)
    const startIndex = trades.length + 1;
    activeBuckets.forEach((bucket, index) => {
        const bucketIndex = startIndex + index;

        // Entry point (orange star)
        activeBucketEntries.push({
            x: bucketIndex,
            y: bucket.entry,
            bucket: bucket
        });

        // Reference price before entry (gray circle)
        activeBucketRefBefore.push({
            x: bucketIndex,
            y: bucket.refBefore,
            bucket: bucket
        });

        // Reference price after entry (purple circle)
        activeBucketRefAfter.push({
            x: bucketIndex,
            y: bucket.refAfter,
            bucket: bucket
        });
    });

    return {
        entries,
        exits,
        exitColors,
        exitBorders,
        tradePaths,
        activeBucketEntries,
        activeBucketRefBefore,
        activeBucketRefAfter
    };
}

/**
 * Update trade statistics display
 * @param {string} botId - Bot ID
 * @param {Array} trades - Today's completed trades
 * @param {Array} activeBuckets - Active bucket positions
 */
function updateTradeStatistics(botId, trades, activeBuckets) {
    const statsContent = document.getElementById(`statsContent-${botId}`);
    if (!statsContent) return;

    const totalTrades = trades.length;
    const activeBucketCount = activeBuckets.length;

    if (totalTrades === 0 && activeBucketCount === 0) {
        statsContent.textContent = 'No trades or active positions today';
        return;
    }

    let statsHTML = '';

    // Active buckets statistics
    if (activeBucketCount > 0) {
        statsHTML += `<span style="margin-right: 20px; color: #f39c12;">⭐ Active Buckets: ${activeBucketCount}</span>`;
    }

    // Completed trades statistics
    if (totalTrades > 0) {
        const profitableTrades = trades.filter(t => (t.profitLoss || 0) > 0).length;
        const losingTrades = totalTrades - profitableTrades;
        const totalPnL = trades.reduce((sum, t) => sum + (t.profitLoss || 0), 0);
        const avgPnL = totalPnL / totalTrades;
        const winRate = totalTrades > 0 ? (profitableTrades / totalTrades * 100) : 0;

        // Check validation status
        const validatedTrades = trades.filter(t => t.validation && t.validation.validated).length;
        const matchedTrades = trades.filter(t => t.validation && t.validation.matches).length;

        statsHTML += `
            <span style="margin-right: 20px;">📊 Completed: ${totalTrades}</span>
            <span style="margin-right: 20px; color: #27ae60;">✓ Wins: ${profitableTrades}</span>
            <span style="margin-right: 20px; color: #e74c3c;">✗ Losses: ${losingTrades}</span>
            <span style="margin-right: 20px;">💰 Total P&L: <strong style="color: ${totalPnL >= 0 ? '#27ae60' : '#e74c3c'}">$${totalPnL.toFixed(2)}</strong></span>
            <span style="margin-right: 20px;">📈 Avg P&L: $${avgPnL.toFixed(2)}</span>
            <span style="margin-right: 20px;">🎯 Win Rate: ${winRate.toFixed(1)}%</span>
            <span>✓ Validated: ${matchedTrades}/${validatedTrades}</span>
        `;
    }

    statsContent.innerHTML = statsHTML;
}

/**
 * Check if charts need reset (new trading day)
 * @returns {boolean} True if should reset
 */
function shouldResetCharts() {
    const now = new Date();
    const lastReset = localStorage.getItem('lastChartReset');

    if (!lastReset) {
        return true;
    }

    const lastResetDate = new Date(lastReset);
    const today = new Date();
    today.setHours(MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE, 0, 0);

    // Reset if it's a new day and past market open
    if (now >= today && lastResetDate < today) {
        return true;
    }

    return false;
}

/**
 * Mark charts as reset for today
 */
function markChartsReset() {
    localStorage.setItem('lastChartReset', new Date().toISOString());
}

/**
 * Auto-reset charts at market open
 */
function checkAndResetCharts() {
    if (shouldResetCharts()) {
        console.log('Resetting charts for new trading day');
        markChartsReset();
        // Reload dashboard data to refresh charts
        if (typeof loadDashboardData === 'function') {
            loadDashboardData();
        }
    }
}

// Export functions for use in dashboard.js
if (typeof window !== 'undefined') {
    window.TradeCharts = {
        initialize: initializeTradeCharts,
        checkAndReset: checkAndResetCharts
    };
}
