// P&L Reporting Dashboard JavaScript
// Simplified version - only P&L statement functionality
// Version: 2.0.0 - Code review fixes applied

// Configuration
const API_BASE_URL = '/api';
const REFRESH_INTERVAL = 30000; // 30 seconds auto-refresh

// Bot ID to name mapping (loaded dynamically from /api/bot-configs)
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
            Logger.error('PnLReporting', 'Failed to load bot configs', data.error);
            BOT_NAMES = {1: '⚠️ CONFIG ERROR'};
            configReady = false;
            return false;
        }

        // Validate data structure
        if (!data.bots || !Array.isArray(data.bots)) {
            Logger.error('PnLReporting', 'Invalid API response structure', data);
            BOT_NAMES = {1: '⚠️ CONFIG ERROR'};
            configReady = false;
            return false;
        }

        // Build BOT_NAMES mapping: index+1 -> symbol
        data.bots.forEach((bot, index) => {
            const botId = index + 1;
            BOT_NAMES[botId] = bot.symbol || `Bot ${botId}`;
        });

        configReady = true;

        Logger.info('PnLReporting', 'Bot configurations loaded', BOT_NAMES);

        return true;

    } catch (error) {
        Logger.error('PnLReporting', 'Error loading bot configs', error);
        BOT_NAMES = {1: '⚠️ CONFIG ERROR'};
        configReady = false;
        return false;
    }
}

// Add custom styles for P&L tables
const style = document.createElement('style');
style.textContent = `
    .pnl-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
        gap: 24px;
        margin-top: 24px;
    }

    .pnl-card {
        background: white;
        border-radius: 12px;
        padding: 24px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        border: 1px solid #e2e8f0;
    }

    .pnl-card-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 20px;
        padding-bottom: 16px;
        border-bottom: 2px solid #e2e8f0;
    }

    .pnl-card-header h3 {
        margin: 0;
        font-size: 1.3em;
        font-weight: 700;
        color: #1a202c;
    }

    .date-selector {
        padding: 8px 12px;
        border: 1px solid #cbd5e0;
        border-radius: 6px;
        font-size: 0.9em;
        background: white;
        cursor: pointer;
        transition: all 0.2s;
    }

    .date-selector:hover {
        border-color: #4299e1;
    }

    .date-selector:focus {
        outline: none;
        border-color: #4299e1;
        box-shadow: 0 0 0 3px rgba(66, 153, 225, 0.1);
    }

    .date-selector-group {
        display: flex;
        gap: 8px;
    }

    .pnl-table {
        width: 100%;
        border-collapse: collapse;
    }

    .pnl-table thead th {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 12px;
        text-align: left;
        font-weight: 600;
        font-size: 0.9em;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }

    .pnl-table thead th:first-child {
        border-radius: 8px 0 0 0;
    }

    .pnl-table thead th:last-child {
        border-radius: 0 8px 0 0;
    }

    .pnl-table tbody tr {
        border-bottom: 1px solid #e2e8f0;
        transition: background-color 0.2s;
    }

    .pnl-table tbody tr:hover {
        background-color: #f7fafc;
    }

    .pnl-table tbody tr.total-row {
        background-color: #edf2f7;
        font-weight: 600;
    }

    .pnl-table tbody tr.total-row:hover {
        background-color: #e2e8f0;
    }

    .pnl-table td {
        padding: 14px 12px;
        font-size: 0.95em;
    }

    .metric-label {
        color: #4a5568;
        font-weight: 500;
    }

    .metric-value {
        text-align: center;
        color: #2d3748;
        font-weight: 600;
        font-size: 1.05em;
    }

    .pnl-value {
        text-align: center;
        font-weight: 700;
        font-size: 1.1em;
        font-family: 'Courier New', monospace;
    }

    .pnl-value.positive {
        color: #38a169;
    }

    .pnl-value.negative {
        color: #e53e3e;
    }

    .section {
        margin: 24px 0;
    }

    .section-header {
        margin-bottom: 24px;
    }

    .section-header h2 {
        margin: 0 0 8px 0;
        font-size: 1.8em;
        font-weight: 700;
        color: #1a202c;
    }

    .section-subtitle {
        margin: 0;
        color: #718096;
        font-size: 1em;
    }
`;
document.head.appendChild(style);

// Initialize dashboard on page load
document.addEventListener('DOMContentLoaded', async function() {
    Logger.info('PnLReporting', 'P&L Reporting Dashboard initializing...');

    // Load bot configurations first
    await loadBotConfigs();

    // Wait for config readiness
    if (!configReady) {
        Logger.warn('PnLReporting', 'Config not ready, waiting...');
        await new Promise(resolve => setTimeout(resolve, 100));

        if (!configReady) {
            Logger.error('PnLReporting', 'Config still not ready after wait');
            alert('⚠️ Configuration Error\n\nFailed to load bot configuration. Dashboard may not display correctly.');
        }
    }

    updateLastUpdateTime();
    initializeDateSelectors(loadPnLStatement);
    loadPnLStatement();

    // Set up auto-refresh
    setInterval(loadPnLStatement, REFRESH_INTERVAL);
    setInterval(updateLastUpdateTime, 1000);
});

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

/**
 * Load P&L Statement with date-based filtering.
 * Fetches all trades from backend, then filters client-side based on selected dates.
 */
async function loadPnLStatement() {
    try {
        // Fetch all trades for P&L calculations
        const response = await fetch(`${API_BASE_URL}/trade-history/all`, {
            credentials: 'include'
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

        const weekValue = `${weekYearSelector.value}-W${weekSelector.value.toString().padStart(2, '0')}`;
        const weekPnL = calculatePnLForWeek(trades, weekValue);

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

// Export API for external use
window.pnlReportingAPI = {
    refresh: loadPnLStatement
};
