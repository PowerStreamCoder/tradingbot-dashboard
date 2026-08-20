// Trading Bot Dashboard JavaScript
// Version: 2.0.0 - Code review fixes applied
// Features: Real-time bot monitoring, trade history, P&L with date selectors, daily trade charts,
//           trend indicators, and market regime/volatility display

// Configuration
const API_BASE_URL = '/api';
const REFRESH_INTERVAL = 30000; // 30 seconds auto-refresh

// Bot ID to name mapping (loaded dynamically from /api/bot-configs)
let BOT_NAMES = {};
let BOT_CONFIGS = null;
let configReady = false;

/**
 * Load bot configurations from API
 * Populates BOT_NAMES mapping for use throughout the dashboard
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
            Logger.error('Dashboard', 'Failed to load bot configs', data.error);
            BOT_NAMES = {'⚠️ CONFIG ERROR': '⚠️ CONFIG ERROR'};
            configReady = false;
            return false;
        }

        // Validate data structure
        if (!data.bots || !Array.isArray(data.bots)) {
            Logger.error('Dashboard', 'Invalid API response structure', data);
            BOT_NAMES = {'⚠️ CONFIG ERROR': '⚠️ CONFIG ERROR'};
            configReady = false;
            return false;
        }

        // Store full config for reference
        BOT_CONFIGS = data;

        // Build BOT_NAMES mapping: index+1 -> symbol
        BOT_NAMES = {};
        data.bots.forEach((bot, index) => {
            const botId = index + 1;
            BOT_NAMES[botId] = bot.symbol || `Bot ${botId}`;
        });

        configReady = true;

        Logger.info('Dashboard', 'Bot configurations loaded', BOT_NAMES);

        return true;

    } catch (error) {
        Logger.error('Dashboard', 'Error loading bot configs', error);
        BOT_NAMES = {'⚠️ CONFIG ERROR': '⚠️ CONFIG ERROR'};
        configReady = false;
        return false;
    }
}

// Initialize dashboard on page load
document.addEventListener('DOMContentLoaded', async function() {
    Logger.info('Dashboard', 'Dashboard initializing...');

    // Load bot configurations first (required for BOT_NAMES)
    await loadBotConfigs();

    // Wait for config readiness
    if (!configReady) {
        Logger.warn('Dashboard', 'Config not ready, waiting...');
        await new Promise(resolve => setTimeout(resolve, 100));

        if (!configReady) {
            Logger.error('Dashboard', 'Config still not ready after wait');
            alert('⚠️ Configuration Error\n\nFailed to load bot configuration. Dashboard may not display correctly.');
        }
    }

    updateLastUpdateTime();
    updatePnLHeaders();  // Set bot names in P&L headers
    initializeDateSelectors(loadPnLStatement);  // Set up date selectors
    initializeExportModal();  // Set up export functionality
    initializeBotControls();  // Set up bot control buttons
    loadDashboardData();
    updateBotStatus();  // Check bot status on load

    // Set up auto-refresh
    setInterval(loadDashboardData, REFRESH_INTERVAL);
    setInterval(updateLastUpdateTime, 1000);
    setInterval(updateBotStatus, 30000);  // Update bot status every 30 seconds
});

/**
 * Initialize export modal functionality
 */
function initializeExportModal() {
    const exportBtn = document.getElementById('exportBtn');
    const exportModal = document.getElementById('exportModal');
    const exportCancelBtn = document.getElementById('exportCancelBtn');
    const exportConfirmBtn = document.getElementById('exportConfirmBtn');
    const startMonthInput = document.getElementById('exportStartMonth');
    const endMonthInput = document.getElementById('exportEndMonth');

    // Set default values to current month
    const today = new Date();
    const currentMonth = `${today.getFullYear()}-${(today.getMonth() + 1).toString().padStart(2, '0')}`;
    startMonthInput.value = currentMonth;
    endMonthInput.value = currentMonth;

    // Show modal
    exportBtn.addEventListener('click', () => {
        exportModal.style.display = 'flex';
    });

    // Hide modal
    exportCancelBtn.addEventListener('click', () => {
        exportModal.style.display = 'none';
    });

    // Export trades
    exportConfirmBtn.addEventListener('click', () => {
        const startMonth = startMonthInput.value;
        const endMonth = endMonthInput.value;

        if (!startMonth || !endMonth) {
            alert('Please select both start and end months');
            return;
        }

        // Trigger download
        const url = `${API_BASE_URL}/trade-history/export?start_month=${startMonth}&end_month=${endMonth}`;
        window.location.href = url;

        // Close modal
        exportModal.style.display = 'none';
    });

    // Close modal on outside click
    exportModal.addEventListener('click', (e) => {
        if (e.target === exportModal) {
            exportModal.style.display = 'none';
        }
    });
}


// Update P&L table headers with bot names
function updatePnLHeaders() {
    // Update all periods (day, week, month)
    ['day', 'week', 'month'].forEach(period => {
        // Bot 1
        const bot1Header = document.getElementById(`${period}-bot1-header`);
        if (bot1Header) {
            bot1Header.textContent = BOT_NAMES[1] || 'Bot 1';
        }

        // Bot 2
        const bot2Header = document.getElementById(`${period}-bot2-header`);
        if (bot2Header) {
            bot2Header.textContent = BOT_NAMES[2] || 'Bot 2';
        }
    });
}

// Update the last update timestamp
function updateLastUpdateTime() {
    const now = new Date();
    const timeString = now.toLocaleString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
    document.getElementById('lastUpdate').textContent = timeString;
}

// Main function to load all dashboard data
async function loadDashboardData() {
    // Check config readiness
    if (!configReady) {
        Logger.warn('Dashboard', 'Config not ready, skipping data load');
        return;
    }

    try {
        // Load bot overview first to create chart containers
        const botData = await loadBotOverview();

        // Then load trade history, P&L, and logs in parallel
        await Promise.all([
            loadTradeHistory(botData),  // Pass bot data to trade history
            loadPnLStatement(),
            loadLogs()  // Load logs for all bots
        ]);
    } catch (error) {
        Logger.error('Dashboard', 'Error loading dashboard data', error);
    }
}

// Load Bot and Bucket Overview (Tile 1)
async function loadBotOverview() {
    try {
        const response = await fetch(`${API_BASE_URL}/bot-overview`, {
            credentials: 'include'  // Send cookies for authentication
        });
        const data = await response.json();

        console.log('[BOT-OVERVIEW] Received data:', data);
        console.log('[BOT-OVERVIEW] Data keys:', Object.keys(data));

        const container = document.getElementById('botOverviewContainer');
        container.innerHTML = '';

        console.log('[BOT-OVERVIEW] Container cleared, creating sections...');

        // Create sections for all configured bots dynamically
        const knownBots = Object.keys(BOT_NAMES).map(id => parseInt(id));

        for (const botId of knownBots) {
            const botName = BOT_NAMES[botId] || `Bot ${botId}`;
            const botKey = `bot${botId}`;
            const buckets = data[botKey] || {};  // Use empty object if no data for this bot

            console.log(`[BOT-OVERVIEW] Creating section for bot ${botId} (${botName}), has data: ${!!data[botKey]}`);

            // Create bot section (will show "No active buckets" if empty)
            const botSection = createBotSection(botName, botId, buckets);
            container.appendChild(botSection);
        }

        console.log('[BOT-OVERVIEW] All sections created');

        // Return bot data for chart initialization
        return data;
    } catch (error) {
        console.error('Error loading bot overview:', error);
        return {};
    }
}

// Create a bot section with dynamic buckets and indicators
// Displays bot name, trend indicator (based on bucket directions),
// current price, and market regime information (volatility-based adaptive settings)
function createBotSection(botName, botId, buckets) {
    const section = document.createElement('div');
    section.className = 'bot-section';

    // Get all bucket IDs that have actual data (not null/empty)
    const bucketIds = Object.keys(buckets)
        .filter(key => key.startsWith('bucket'))
        .map(key => ({
            id: parseInt(key.replace('bucket', '')),
            data: buckets[key]
        }))
        .filter(bucket => bucket.data && bucket.data.entryPrice !== null)  // Only show buckets with data
        .map(bucket => bucket.id)
        .sort((a, b) => a - b);

    if (bucketIds.length === 0) {
        const trendInfo = determineBotTrend(buckets);
        const priceInfo = formatCurrentPrice(buckets.currentPrice, buckets.referencePrice);
        // Pass regime data as object with all three properties
        const regimeInfo = formatRegimeInfo({
            regime: buckets.regime,
            volatility: buckets.volatility,
            confidence: buckets.confidence
        });

        // Create header for no-buckets state
        const header = document.createElement('h3');
        header.innerHTML = `${botName}${priceInfo} <span style="font-size: 0.9em; margin-left: 8px; ${trendInfo.style}">${trendInfo.indicator} ${trendInfo.label}</span>${regimeInfo}`;
        section.appendChild(header);

        // Add "No active buckets" message
        const noBucketsMsg = document.createElement('p');
        noBucketsMsg.style.cssText = 'padding: 10px; color: #666;';
        noBucketsMsg.textContent = 'No active buckets';
        section.appendChild(noBucketsMsg);

        // Add chart container for this bot (even when no buckets, for trade history chart)
        const chartContainer = document.createElement('div');
        chartContainer.id = `tradeChart-bot${botId}`;
        chartContainer.className = 'bot-trade-chart-container';
        chartContainer.style.marginTop = '20px';
        section.appendChild(chartContainer);

        // Still create logs container even when no buckets
        const logsContainer = document.createElement('div');
        logsContainer.id = `logs-bot${botId}`;
        logsContainer.className = 'bot-logs-container';
        logsContainer.style.cssText = `
            margin-top: 20px;
            background-color: #2c3e50;
            border-radius: 8px;
            padding: 15px;
            font-family: 'Courier New', monospace;
            color: #ecf0f1;
            font-size: 0.85em;
            max-height: 400px;
            overflow-y: auto;
        `;

        const logsHeader = document.createElement('h4');
        logsHeader.textContent = 'Recent Logs (Last 15 lines)';
        logsHeader.style.cssText = `
            margin: 0 0 10px 0;
            color: #ecf0f1;
            font-size: 1em;
            font-family: 'Segoe UI', sans-serif;
            font-weight: 600;
        `;
        logsContainer.appendChild(logsHeader);

        const logsContent = document.createElement('pre');
        logsContent.id = `logs-content-bot${botId}`;
        logsContent.style.cssText = `
            margin: 0;
            padding: 10px;
            background-color: #1a252f;
            border-radius: 4px;
            white-space: pre-wrap;
            word-wrap: break-word;
            line-height: 1.5;
        `;
        logsContent.textContent = 'Loading logs...';
        logsContainer.appendChild(logsContent);

        section.appendChild(logsContainer);

        return section;
    }

    // Normal flow: Bot has active buckets
    // Create header with trend indicator, current price, and regime status
    const header = document.createElement('h3');
    const trendInfo = determineBotTrend(buckets);
    const priceInfo = formatCurrentPrice(buckets.currentPrice, buckets.referencePrice);
    const regimeInfo = formatRegimeInfo({
        regime: buckets.regime,
        volatility: buckets.volatility,
        confidence: buckets.confidence
    });
    header.innerHTML = `${botName}${priceInfo} <span style="font-size: 0.9em; margin-left: 8px; ${trendInfo.style}">${trendInfo.indicator} ${trendInfo.label}</span>${regimeInfo}`;
    section.appendChild(header);

    // Create table
    const table = document.createElement('table');
    table.className = 'data-table';

    // Create table header
    const thead = document.createElement('thead');
    const headerRow1 = document.createElement('tr');

    // First column: "Bucket"
    const th1 = document.createElement('th');
    th1.rowSpan = 2;
    th1.textContent = 'Bucket';
    headerRow1.appendChild(th1);

    // Bucket columns header
    const th2 = document.createElement('th');
    th2.colSpan = bucketIds.length;
    th2.textContent = 'Buckets';
    headerRow1.appendChild(th2);

    thead.appendChild(headerRow1);

    // Second header row with bucket numbers and types
    const headerRow2 = document.createElement('tr');
    bucketIds.forEach(bucketId => {
        const th = document.createElement('th');
        const bucket = buckets[`bucket${bucketId}`];
        const bucketType = determineBucketType(bucket);
        th.innerHTML = `Bucket ${bucketId}<br><span style="font-size: 0.85em; font-weight: normal;">(${bucketType})</span>`;
        headerRow2.appendChild(th);
    });
    thead.appendChild(headerRow2);
    table.appendChild(thead);

    // Create table body
    const tbody = document.createElement('tbody');

    // Row 1: Reference price before Entry
    const row1 = document.createElement('tr');
    const label1 = document.createElement('td');
    label1.className = 'label';
    label1.innerHTML = '📍 <strong>Reference price before Entry</strong>';
    label1.style.cssText = 'background-color: #fff3cd; font-weight: 600;';
    row1.appendChild(label1);
    bucketIds.forEach(bucketId => {
        const td = document.createElement('td');
        const bucket = buckets[`bucket${bucketId}`];
        td.textContent = formatPrice(bucket?.referencePriceBefore);
        // Highlight reference point with yellow background
        td.style.cssText = 'background-color: #fff3cd; color: #856404; font-weight: 600; border-left: 3px solid #ffc107;';
        row1.appendChild(td);
    });
    tbody.appendChild(row1);

    // Row 2: Entry Price
    const row2 = document.createElement('tr');
    const label2 = document.createElement('td');
    label2.className = 'label';
    label2.textContent = 'Entry Price';
    row2.appendChild(label2);
    bucketIds.forEach(bucketId => {
        const td = document.createElement('td');
        const bucket = buckets[`bucket${bucketId}`];
        td.textContent = formatPrice(bucket?.entryPrice);
        row2.appendChild(td);
    });
    tbody.appendChild(row2);

    // Row 3: Reference price after Entry
    const row3 = document.createElement('tr');
    const label3 = document.createElement('td');
    label3.className = 'label';
    label3.innerHTML = '🎯 <strong>Reference price after Entry</strong>';
    label3.style.cssText = 'background-color: #d1ecf1; font-weight: 600;';
    row3.appendChild(label3);
    bucketIds.forEach(bucketId => {
        const td = document.createElement('td');
        const bucket = buckets[`bucket${bucketId}`];
        td.textContent = formatPrice(bucket?.referencePriceAfter);
        // Highlight reference point with blue background
        td.style.cssText = 'background-color: #d1ecf1; color: #0c5460; font-weight: 600; border-left: 3px solid #17a2b8;';
        row3.appendChild(td);
    });
    tbody.appendChild(row3);

    // Row 4: Expected Profit Booking Price
    const row4 = document.createElement('tr');
    const label4 = document.createElement('td');
    label4.className = 'label';
    label4.textContent = 'Expected Profit Booking Price';
    row4.appendChild(label4);
    bucketIds.forEach(bucketId => {
        const td = document.createElement('td');
        const bucket = buckets[`bucket${bucketId}`];
        const profitPrice = calculateProfitBookingPrice(bucket);
        td.textContent = formatPrice(profitPrice);
        // Style the cell with green background to highlight target price
        td.style.cssText = 'background-color: #d4edda; color: #155724; font-weight: 600;';
        row4.appendChild(td);
    });
    tbody.appendChild(row4);

    table.appendChild(tbody);
    section.appendChild(table);

    // Add chart container for this bot (will be populated by trade-charts.js)
    const chartContainer = document.createElement('div');
    chartContainer.id = `tradeChart-bot${botId}`;
    chartContainer.className = 'bot-trade-chart-container';
    chartContainer.style.marginTop = '20px';
    section.appendChild(chartContainer);

    // Add logs container for this bot
    const logsContainer = document.createElement('div');
    logsContainer.id = `logs-bot${botId}`;
    logsContainer.className = 'bot-logs-container';
    logsContainer.style.cssText = `
        margin-top: 20px;
        background-color: #2c3e50;
        border-radius: 8px;
        padding: 15px;
        font-family: 'Courier New', monospace;
        color: #ecf0f1;
        font-size: 0.85em;
        max-height: 400px;
        overflow-y: auto;
    `;

    const logsHeader = document.createElement('h4');
    logsHeader.textContent = 'Recent Logs (Last 15 lines)';
    logsHeader.style.cssText = `
        margin: 0 0 10px 0;
        color: #ecf0f1;
        font-size: 1em;
        font-family: 'Segoe UI', sans-serif;
        font-weight: 600;
    `;
    logsContainer.appendChild(logsHeader);

    const logsContent = document.createElement('pre');
    logsContent.id = `logs-content-bot${botId}`;
    logsContent.style.cssText = `
        margin: 0;
        padding: 10px;
        background-color: #1a252f;
        border-radius: 4px;
        white-space: pre-wrap;
        word-wrap: break-word;
        line-height: 1.5;
    `;
    logsContent.textContent = 'Loading logs...';
    logsContainer.appendChild(logsContent);

    section.appendChild(logsContainer);

    return section;
}

// Determine bucket type (direction) from prices
// Compares entry price to reference price before entry to determine if bucket is upward or downward
function determineBucketType(bucket) {
    if (!bucket || !bucket.entryPrice || !bucket.referencePriceBefore) {
        return 'Unknown';
    }
    // If entry price > reference before, it's an upward bucket (long)
    // If entry price < reference before, it's a downward bucket (short)
    return bucket.entryPrice >= bucket.referencePriceBefore ? 'Upward' : 'Downward';
}

// Calculate expected profit booking price for a bucket
// Uses profit_target_net from config (0.7% = 0.007)
// For Upward buckets (long): Entry Price × (1 + 0.007) = Entry Price × 1.007
// For Downward buckets (short): Entry Price × (1 - 0.007) = Entry Price × 0.993
function calculateProfitBookingPrice(bucket) {
    if (!bucket || !bucket.entryPrice) {
        return null;
    }

    const PROFIT_TARGET_NET = 0.007;  // 0.7% from config.py
    const bucketType = determineBucketType(bucket);

    if (bucketType === 'Upward') {
        // Long position: profit when price goes up
        return bucket.entryPrice * (1 + PROFIT_TARGET_NET);
    } else if (bucketType === 'Downward') {
        // Short position: profit when price goes down
        return bucket.entryPrice * (1 - PROFIT_TARGET_NET);
    }

    return null;
}

// Determine overall bot trend based on active buckets
// Analyzes all active buckets and returns the dominant trend direction
// Used to display trend indicator (📈 Uptrend, 📉 Downtrend, ↔️ Mixed) in bot header
function determineBotTrend(buckets) {
    // Get all buckets with data
    const activeBuckets = Object.keys(buckets)
        .filter(key => key.startsWith('bucket'))
        .map(key => buckets[key])
        .filter(bucket => bucket && bucket.entryPrice !== null && bucket.referencePriceBefore !== null);

    if (activeBuckets.length === 0) {
        return {
            indicator: '➖',
            label: 'No Trend',
            style: 'color: #95a5a6;'
        };
    }

    // Count upward and downward buckets
    let upwardCount = 0;
    let downwardCount = 0;

    activeBuckets.forEach(bucket => {
        if (bucket.entryPrice >= bucket.referencePriceBefore) {
            upwardCount++;
        } else {
            downwardCount++;
        }
    });

    // Determine overall trend based on majority
    if (upwardCount > downwardCount) {
        return {
            indicator: '📈',
            label: 'Uptrend',
            style: 'color: #27ae60; font-weight: 600;'
        };
    } else if (downwardCount > upwardCount) {
        return {
            indicator: '📉',
            label: 'Downtrend',
            style: 'color: #e74c3c; font-weight: 600;'
        };
    } else {
        // Equal number of upward and downward buckets
        return {
            indicator: '↔️',
            label: 'Mixed',
            style: 'color: #f39c12; font-weight: 600;'
        };
    }
}

// Format regime information for display
// Converts market regime data from adaptive configuration into visual display
// Shows regime type, volatility percentage, and confidence score
// Regime types: trending (high volatility), sideways (range-bound), low_volatility (minimal movement)
function formatRegimeInfo(regimeData) {
    // Handle both flat and nested regime data structures
    // Flat: regimeData = { regime: 'sideways', volatility: 0.004, confidence: 0.8 }
    // Nested (legacy): regimeData = { regime: { regime: 'sideways', ... } }
    let regime, volatility, confidence;

    if (!regimeData) {
        return '';  // No regime data available
    }

    // Check if data is nested (legacy format)
    if (regimeData.regime && typeof regimeData.regime === 'object') {
        regime = regimeData.regime.regime;
        volatility = regimeData.regime.volatility;
        confidence = regimeData.regime.confidence;
    } else {
        // Flat format (current)
        regime = regimeData.regime || regimeData;
        volatility = regimeData.volatility;
        confidence = regimeData.confidence;
    }

    if (!regime || regime === 'unknown') {
        return '';  // No valid regime data
    }

    // Determine regime label and styling based on detected market condition
    let label, icon, color;

    switch(regime) {
        case 'trending':
            label = 'Trending Market';
            icon = '🔥';
            color = '#e67e22';  // Orange - indicates high volatility, strong moves
            break;
        case 'sideways':
            label = 'Sideways Market';
            icon = '↔️';
            color = '#3498db';  // Blue - indicates range-bound action
            break;
        case 'low_volatility':
            label = 'Low Volatility';
            icon = '😴';
            color = '#95a5a6';  // Gray - indicates minimal price movement
            break;
        default:
            return '';  // Unknown regime type
    }

    // Format volatility and confidence for display
    const volDisplay = volatility ? `Vol: ${(volatility * 100).toFixed(2)}%` : '';
    const confDisplay = confidence ? `Conf: ${(confidence * 100).toFixed(0)}%` : '';

    // Build the regime info HTML with metrics
    const details = [volDisplay, confDisplay].filter(d => d).join(' | ');
    const detailsHtml = details ? `<span style="font-size: 0.75em; font-weight: normal;"> (${details})</span>` : '';

    return ` <span style="font-size: 0.85em; margin-left: 12px; color: ${color}; font-weight: 600;">${icon} ${label}${detailsHtml}</span>`;
}

// Format current price for display in bot header
function formatCurrentPrice(price, referencePrice) {
    if (!price || price === null || price === undefined) {
        return '';  // No price data available
    }

    // Format current price with $ sign and 2 decimal places
    const formattedPrice = `$${price.toFixed(2)}`;

    // Display current price in a distinct style to stand out
    let priceDisplay = ` <span style="font-size: 0.95em; margin-left: 10px; color: #2c3e50; font-weight: 700; background-color: #ecf0f1; padding: 4px 10px; border-radius: 4px;">${formattedPrice}</span>`;

    // Add reference price if available
    if (referencePrice && referencePrice !== null && referencePrice !== undefined) {
        const formattedRef = `$${referencePrice.toFixed(2)}`;
        priceDisplay += ` <span style="font-size: 0.85em; margin-left: 8px; color: #856404; font-weight: 600; background-color: #fff3cd; padding: 4px 10px; border-radius: 4px; border-left: 3px solid #ffc107;">📍 Ref: ${formattedRef}</span>`;
    }

    return priceDisplay;
}

// Update bot bucket data in the table (deprecated - kept for compatibility)
function updateBotBuckets(botId, buckets) {
    // This function is no longer used - tables are built dynamically
    console.log('updateBotBuckets is deprecated');
}

// Load Trade History (Tile 2)
async function loadTradeHistory(botData) {
    try {
        const response = await fetch(`${API_BASE_URL}/trade-history`, {
            credentials: 'include'  // Send cookies for authentication
        });

        if (!response.ok) {
            throw new Error(`API returned ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();

        const tbody = document.getElementById('tradeHistoryBody');
        tbody.innerHTML = '';

        if (data.trades && data.trades.length > 0) {
            // Use sequence numbers from database (no calculation needed)
            data.trades.forEach((trade) => {
                const seqNum = trade.seqNum || '?';  // Use stored seqNum or '?' if missing
                const row = createTradeRow(seqNum, trade);
                tbody.appendChild(row);
            });

            // Initialize trade charts with all trades AND bot data
            if (typeof window.TradeCharts !== 'undefined') {
                window.TradeCharts.initialize(data.trades, botData || {});
                window.TradeCharts.checkAndReset();  // Check if charts need daily reset
            }
        } else {
            tbody.innerHTML = '<tr><td colspan="6" class="empty-state">No trades found</td></tr>';

            // Still initialize charts with just bot data (active buckets)
            if (typeof window.TradeCharts !== 'undefined' && botData) {
                window.TradeCharts.initialize([], botData);
            }
        }
    } catch (error) {
        console.error('Error loading trade history:', error);
        // Show error state instead of mock data
        const tbody = document.getElementById('tradeHistoryBody');
        tbody.innerHTML = `<tr><td colspan="6" class="empty-state" style="color: #e74c3c;">⚠️ Error loading trades: ${error.message}<br>Please refresh the page or check your connection.</td></tr>`;
    }
}

// Create a trade history row
function createTradeRow(seqNum, trade) {
    const row = document.createElement('tr');

    // Sequence Number
    const seqCell = document.createElement('td');
    seqCell.textContent = seqNum;
    seqCell.style.textAlign = 'center';
    seqCell.style.fontWeight = '500';
    row.appendChild(seqCell);

    // Trading Mode Badge
    const modeCell = document.createElement('td');
    modeCell.style.textAlign = 'center';
    const mode = trade.trading_mode || 'paper';
    const modeBadge = document.createElement('span');
    modeBadge.className = `mode-badge mode-${mode}`;
    modeBadge.textContent = mode.toUpperCase();
    modeBadge.style.cssText = `
        padding: 4px 10px;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        display: inline-block;
    `;
    modeCell.appendChild(modeBadge);
    row.appendChild(modeCell);

    // Trade Info (Bot name and bucket type)
    const infoCell = document.createElement('td');
    const botName = trade.botName || `Bot ${trade.botId || '?'}`;
    const bucketType = trade.bucketType || 'Unknown';
    infoCell.innerHTML = `
        <div style="font-weight: 500;">${botName}</div>
        <div style="font-size: 0.9em; color: #666;">${bucketType} Bucket</div>
    `;
    row.appendChild(infoCell);

    // Trade Details
    const detailsCell = document.createElement('td');
    detailsCell.innerHTML = `
        <div class="trade-info">
            <div class="trade-info-row" style="background-color: #fff3cd; padding: 4px 8px; border-left: 3px solid #ffc107; margin-bottom: 2px;">
                <span class="trade-info-label" style="font-weight: 600;">📍 Reference price before Entry:</span>
                <span class="trade-info-value" style="color: #856404; font-weight: 600;">${formatPrice(trade.referencePriceBefore)}</span>
            </div>
            <div class="trade-info-row">
                <span class="trade-info-label">Entry Price:</span>
                <span class="trade-info-value">${formatPrice(trade.entryPrice)}</span>
            </div>
            <div class="trade-info-row" style="background-color: #d1ecf1; padding: 4px 8px; border-left: 3px solid #17a2b8; margin-bottom: 2px;">
                <span class="trade-info-label" style="font-weight: 600;">🎯 Reference price after Entry:</span>
                <span class="trade-info-value" style="color: #0c5460; font-weight: 600;">${formatPrice(trade.referencePriceAfter)}</span>
            </div>
            <div class="trade-info-row">
                <span class="trade-info-label">Exit Price:</span>
                <span class="trade-info-value">${formatPrice(trade.exitPrice)}</span>
            </div>
        </div>
    `;
    row.appendChild(detailsCell);

    // Trade Timestamp
    const timeCell = document.createElement('td');
    timeCell.textContent = formatTimestamp(trade.timestamp);
    row.appendChild(timeCell);

    // PnL with validation (Bot vs IBKR)
    const pnlCell = document.createElement('td');
    const pnlValue = trade.profitLoss;
    pnlCell.textContent = formatCurrency(pnlValue);
    pnlCell.className = pnlValue >= 0 ? 'profit' : 'loss';
    row.appendChild(pnlCell);

    // P&L Sync (real-time reconciliation from bot)
    const syncCell = document.createElement('td');
    syncCell.style.fontSize = '0.85em';
    syncCell.style.lineHeight = '1.4';

    if (trade.pnl_sync) {
        const sync = trade.pnl_sync;
        const hasIssue = sync.has_discrepancy || false;

        // Build sync info display
        let syncHtml = '<div style="padding: 4px;">';

        // Position check
        if (sync.bot_position !== undefined && sync.ibkr_position !== undefined) {
            const posMatch = sync.bot_position === sync.ibkr_position;
            syncHtml += `<div style="color: ${posMatch ? '#27ae60' : '#e74c3c'};">`;
            syncHtml += `Position: Bot=${sync.bot_position}, IBKR=${sync.ibkr_position}`;
            if (!posMatch) syncHtml += ` ⚠️`;
            syncHtml += `</div>`;
        }

        // Realized P&L
        if (sync.bot_realized_pnl !== undefined && sync.ibkr_realized_pnl !== undefined) {
            const diff = Math.abs(sync.bot_realized_pnl - sync.ibkr_realized_pnl);
            const matches = diff < 0.10;
            syncHtml += `<div style="color: ${matches ? '#27ae60' : '#e74c3c'}; font-weight: 500;">`;
            syncHtml += `Realized: Bot=${formatCurrency(sync.bot_realized_pnl)}, IBKR=${formatCurrency(sync.ibkr_realized_pnl)}`;
            if (!matches) syncHtml += ` ⚠️`;
            syncHtml += `</div>`;
        }

        // Unrealized P&L
        if (sync.bot_unrealized_pnl !== undefined && sync.ibkr_unrealized_pnl !== undefined) {
            const diff = Math.abs(sync.bot_unrealized_pnl - sync.ibkr_unrealized_pnl);
            const matches = diff < 5.0;
            syncHtml += `<div style="color: ${matches ? '#555' : '#f39c12'};">`;
            syncHtml += `Unrealized: Bot=${formatCurrency(sync.bot_unrealized_pnl)}, IBKR=${formatCurrency(sync.ibkr_unrealized_pnl)}`;
            if (!matches) syncHtml += ` ⚠️`;
            syncHtml += `</div>`;
        }

        // Discrepancy message
        if (sync.discrepancy_message) {
            syncHtml += `<div style="color: #e74c3c; margin-top: 4px; font-weight: 500;">⚠️ ${sync.discrepancy_message}</div>`;
        }

        syncHtml += '</div>';
        syncCell.innerHTML = syncHtml;
    } else {
        syncCell.innerHTML = `<span style="color: #95a5a6;">—</span>`;
        syncCell.style.textAlign = 'center';
    }
    row.appendChild(syncCell);

    // Actions (Delete button)
    const actionsCell = document.createElement('td');
    actionsCell.style.textAlign = 'center';

    const deleteBtn = document.createElement('button');
    deleteBtn.textContent = '🗑️ Delete';
    deleteBtn.className = 'delete-btn';
    deleteBtn.style.cssText = `
        padding: 6px 12px;
        background-color: #e74c3c;
        color: white;
        border: none;
        border-radius: 4px;
        cursor: pointer;
        font-size: 0.9em;
        transition: background-color 0.2s;
    `;
    deleteBtn.onmouseover = () => deleteBtn.style.backgroundColor = '#c0392b';
    deleteBtn.onmouseout = () => deleteBtn.style.backgroundColor = '#e74c3c';
    deleteBtn.onclick = () => confirmDeleteTrade(trade);

    actionsCell.appendChild(deleteBtn);
    row.appendChild(actionsCell);

    return row;
}

/**
 * Load P&L Statement with date-based filtering (v1.2.0).
 * Fetches all trades from backend, then filters client-side based on selected dates.
 * Recalculates P&L instantly when date selectors change - no server round-trip needed.
 */
async function loadPnLStatement() {
    try {
        // Fetch all trades for P&L calculations (last 200)
        const response = await fetch(`${API_BASE_URL}/trade-history/all`, {
            credentials: 'include'  // Send cookies for authentication
        });
        const data = await response.json();
        const trades = data.trades || [];

        // Get selected dates
        const daySelector = document.getElementById('day-selector');
        const weekSelector = document.getElementById('week-selector');
        const weekYearSelector = document.getElementById('week-year-selector');
        const monthSelector = document.getElementById('month-selector');
        const monthYearSelector = document.getElementById('month-year-selector');

        // Calculate P&L for each period
        const dayPnL = calculatePnLForDay(trades, new Date(daySelector.value));

        // Build week value from year and week selectors
        const weekValue = `${weekYearSelector.value}-W${weekSelector.value.toString().padStart(2, '0')}`;
        const weekPnL = calculatePnLForWeek(trades, weekValue);

        // Build month value from month and year selectors
        const monthValue = `${monthYearSelector.value}-${monthSelector.value.toString().padStart(2, '0')}`;
        const monthPnL = calculatePnLForMonth(trades, monthValue);

        // Update UI
        updatePnLPeriod('day', dayPnL);
        updatePnLPeriod('week', weekPnL);
        updatePnLPeriod('month', monthPnL);
    } catch (error) {
        console.error('Error loading P&L statement:', error);
    }
}

// Load and display logs for all bots
async function loadLogs() {
    try {
        const response = await fetch(`${API_BASE_URL}/logs`, {
            credentials: 'include'  // Send cookies for authentication
        });
        const data = await response.json();

        console.log('[LOGS] Received data:', data);
        console.log('[LOGS] Keys:', Object.keys(data));

        // Update logs for each bot
        for (const [botId, logLines] of Object.entries(data)) {
            const elementId = `logs-content-bot${botId}`;
            const logsContent = document.getElementById(elementId);

            console.log(`[LOGS] Bot ${botId}: element=${elementId}, found=${!!logsContent}, lines=${logLines?.length}`);

            if (logsContent) {
                if (logLines && logLines.length > 0) {
                    logsContent.textContent = logLines.join('\n');
                    console.log(`[LOGS] Bot ${botId}: Updated with ${logLines.length} lines`);
                } else {
                    logsContent.textContent = 'No logs available yet...';
                    console.log(`[LOGS] Bot ${botId}: No logs available`);
                }
            } else {
                console.warn(`[LOGS] Bot ${botId}: Element #${elementId} not found in DOM!`);
            }
        }
    } catch (error) {
        console.error('Error loading logs:', error);
    }
}

/**
 * Show confirmation dialog and delete trade if confirmed.
 * @param {Object} trade - Trade object with timestamp and details
 */
function confirmDeleteTrade(trade) {
    const botName = trade.botName || `Bot ${trade.botId || '?'}`;
    const timestamp = formatTimestamp(trade.timestamp);
    const pnl = formatCurrency(trade.profitLoss);

    const message = `Are you sure you want to delete this trade?\n\n` +
                    `Bot: ${botName}\n` +
                    `Timestamp: ${timestamp}\n` +
                    `P/L: ${pnl}\n` +
                    `Entry: ${formatPrice(trade.entryPrice)}\n` +
                    `Exit: ${formatPrice(trade.exitPrice)}\n\n` +
                    `This action cannot be undone.`;

    if (confirm(message)) {
        deleteTrade(trade.timestamp);
    }
}

/**
 * Delete a trade from Firestore via API.
 * @param {string} timestamp - ISO timestamp of the trade to delete
 */
async function deleteTrade(timestamp) {
    try {
        const response = await fetch(`${API_BASE_URL}/trade-history/delete?timestamp=${encodeURIComponent(timestamp)}`, {
            method: 'DELETE',
            credentials: 'include',  // Send cookies for authentication
            headers: {
                'Content-Type': 'application/json'
            }
        });

        const result = await response.json();

        if (response.ok && result.status === 'success') {
            alert(`✅ Trade deleted successfully!`);
            // Reload trade history to reflect deletion
            await loadTradeHistory();
            // Reload P&L to update calculations
            await loadPnLStatement();
        } else {
            alert(`❌ Error deleting trade: ${result.message || result.detail || 'Unknown error'}`);
        }
    } catch (error) {
        console.error('Error deleting trade:', error);
        alert(`❌ Failed to delete trade: ${error.message}`);
    }
}

// Bot Control Functions
function initializeBotControls() {
    const stopBtn = document.getElementById('stopBotsBtn');
    const restartBtn = document.getElementById('restartBotsBtn');
    const resetNvdaBtn = document.getElementById('resetNvdaBotBtn');
    const resetMsftBtn = document.getElementById('resetMsftBotBtn');

    stopBtn.addEventListener('click', stopBots);
    restartBtn.addEventListener('click', restartBots);
    resetNvdaBtn.addEventListener('click', () => showResetConfirmation('nvda'));
    resetMsftBtn.addEventListener('click', () => showResetConfirmation('msft'));

    // Add hover effects
    stopBtn.addEventListener('mouseover', () => stopBtn.style.backgroundColor = '#c0392b');
    stopBtn.addEventListener('mouseout', () => stopBtn.style.backgroundColor = '#e74c3c');

    restartBtn.addEventListener('mouseover', () => restartBtn.style.backgroundColor = '#229954');
    restartBtn.addEventListener('mouseout', () => restartBtn.style.backgroundColor = '#27ae60');

    resetNvdaBtn.addEventListener('mouseover', () => resetNvdaBtn.style.backgroundColor = '#d35400');
    resetNvdaBtn.addEventListener('mouseout', () => resetNvdaBtn.style.backgroundColor = '#e67e22');

    resetMsftBtn.addEventListener('mouseover', () => resetMsftBtn.style.backgroundColor = '#d35400');
    resetMsftBtn.addEventListener('mouseout', () => resetMsftBtn.style.backgroundColor = '#e67e22');

    // Initialize reset modal controls
    initializeResetModal();
}

async function stopBots() {
    if (!confirm('Are you sure you want to stop both trading bots?')) {
        return;
    }

    try {
        const response = await fetch(`${API_BASE_URL}/bot-control/stop`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' }
        });

        const result = await response.json();

        if (response.ok) {
            alert('✅ Bots stopped successfully!');
            updateBotStatus();
        } else {
            alert(`❌ Error stopping bots: ${result.detail || 'Unknown error'}`);
        }
    } catch (error) {
        console.error('Error stopping bots:', error);
        alert(`❌ Failed to stop bots: ${error.message}`);
    }
}

async function restartBots() {
    if (!confirm('Are you sure you want to restart both trading bots?')) {
        return;
    }

    try {
        const response = await fetch(`${API_BASE_URL}/bot-control/restart`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' }
        });

        const result = await response.json();

        if (response.ok) {
            alert('✅ Bots restarted successfully!');
            setTimeout(updateBotStatus, 2000);  // Wait 2 seconds for bots to start
        } else {
            alert(`❌ Error restarting bots: ${result.detail || 'Unknown error'}`);
        }
    } catch (error) {
        console.error('Error restarting bots:', error);
        alert(`❌ Failed to restart bots: ${error.message}`);
    }
}

async function updateBotStatus() {
    try {
        const response = await fetch(`${API_BASE_URL}/bot-control/status`, {
            credentials: 'include'
        });

        const status = await response.json();

        const statusIcon = document.getElementById('botStatusIcon');
        const statusText = document.getElementById('botStatusText');
        const statusContainer = document.getElementById('botStatus');

        if (status.both_running) {
            statusIcon.textContent = '✅';
            statusText.textContent = 'Both bots running';
            statusContainer.style.backgroundColor = '#d4edda';
            statusContainer.style.color = '#155724';
        } else if (status.nvda === 'active' || status.msft === 'active') {
            statusIcon.textContent = '⚠️';
            statusText.textContent = 'One bot running';
            statusContainer.style.backgroundColor = '#fff3cd';
            statusContainer.style.color = '#856404';
        } else {
            statusIcon.textContent = '❌';
            statusText.textContent = 'Bots stopped';
            statusContainer.style.backgroundColor = '#f8d7da';
            statusContainer.style.color = '#721c24';
        }
    } catch (error) {
        console.error('Error fetching bot status:', error);
    }
}

/**
 * Initialize reset modal controls
 */
function initializeResetModal() {
    const resetModal = document.getElementById('resetBotModal');
    const resetCancelBtn = document.getElementById('resetBotCancelBtn');
    const resetConfirmBtn = document.getElementById('resetBotConfirmBtn');
    const resetProgressModal = document.getElementById('resetProgressModal');
    const resetProgressCloseBtn = document.getElementById('resetProgressCloseBtn');

    // Cancel button - close confirmation modal
    resetCancelBtn.addEventListener('click', () => {
        resetModal.style.display = 'none';
    });

    // Confirm button - execute reset
    resetConfirmBtn.addEventListener('click', () => {
        const botName = resetConfirmBtn.getAttribute('data-bot-name');
        resetModal.style.display = 'none';
        executeResetBot(botName);
    });

    // Close progress modal when done
    resetProgressCloseBtn.addEventListener('click', () => {
        resetProgressModal.style.display = 'none';
        updateBotStatus(); // Refresh bot status
        loadDashboardData(); // Refresh dashboard data
    });
}

/**
 * Show reset confirmation modal
 */
function showResetConfirmation(botName) {
    const botUpper = botName.toUpperCase();
    const resetModal = document.getElementById('resetBotModal');
    const resetBotNameSpan = document.getElementById('resetBotName');
    const resetConfirmBtn = document.getElementById('resetBotConfirmBtn');

    resetBotNameSpan.textContent = botUpper;
    resetConfirmBtn.setAttribute('data-bot-name', botName);

    resetModal.style.display = 'flex';
}

/**
 * Execute bot reset
 */
async function executeResetBot(botName) {
    const botUpper = botName.toUpperCase();
    const progressModal = document.getElementById('resetProgressModal');
    const progressBotName = document.getElementById('resetProgressBotName');
    const progressLog = document.getElementById('resetProgressLog');
    const progressCloseBtn = document.getElementById('resetProgressCloseBtn');

    // Show progress modal
    progressBotName.textContent = botUpper;
    progressLog.textContent = `Initializing ${botUpper} bot reset...\n`;
    progressCloseBtn.style.display = 'none';
    progressModal.style.display = 'flex';

    try {
        progressLog.textContent += `Sending reset request to server...\n`;

        const response = await fetch(`${API_BASE_URL}/bot-control/reset/${botName}`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' }
        });

        const result = await response.json();

        if (response.ok && result.status === 'success') {
            // Display reset log
            progressLog.textContent = result.log.join('\n') + '\n';
            progressLog.textContent += `\n✅ ${botUpper} BOT RESET COMPLETE!\n`;
            progressLog.style.color = '#155724';
            progressLog.style.backgroundColor = '#d4edda';
        } else {
            // Display error
            const errorLog = result.log ? result.log.join('\n') : result.message;
            progressLog.textContent = errorLog + '\n';
            progressLog.textContent += `\n❌ RESET FAILED\n`;
            progressLog.style.color = '#721c24';
            progressLog.style.backgroundColor = '#f8d7da';
        }

        // Show close button
        progressCloseBtn.style.display = 'block';

    } catch (error) {
        console.error('Error resetting bot:', error);
        progressLog.textContent += `\n❌ ERROR: ${error.message}\n`;
        progressLog.style.color = '#721c24';
        progressLog.style.backgroundColor = '#f8d7da';
        progressCloseBtn.style.display = 'block';
    }
}


// Export functions for external use
window.dashboardAPI = {
    refresh: loadDashboardData,
    loadBotOverview,
    loadTradeHistory,
    loadPnLStatement,
    stopBots,
    restartBots,
    updateBotStatus
};
