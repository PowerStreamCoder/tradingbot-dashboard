// P&L Reporting Dashboard JavaScript
// Simplified version - only P&L statement functionality
// Version: 2.4.0 - Fixed duplicate variable declarations

// Configuration
// Note: API_BASE_URL is declared in dashboard.js (loaded before this script)
// Note: BOT_NAMES is declared in dashboard.js (loaded before this script)
// Note: API_ENDPOINTS is declared in config.js (loaded before this script)
// Note: REFRESH_INTERVAL is declared in dashboard.js (loaded before this script)

// Config ready flag is managed by dashboard.js (loaded before this script)
// We'll use the shared BOT_NAMES populated by dashboard.js

// State for multi-select bot filter
let selectedBots = new Set(); // Empty set means "all bots"
let allBotsSelected = true;

/**
 * Setup multi-select bot filter with checkboxes
 * Called after bot configs are loaded
 */
function setupBotFilter() {
    const allBotsCheckbox = document.getElementById('allBotsCheckbox');
    const botCheckboxContainer = document.getElementById('botCheckboxContainer');
    const filterButton = document.getElementById('botFilterButton');
    const filterDropdown = document.getElementById('botFilterDropdown');

    if (!allBotsCheckbox || !botCheckboxContainer || !filterButton || !filterDropdown) {
        Logger.warn('PnLReporting', 'Bot filter elements not found', {
            allBotsCheckbox: !!allBotsCheckbox,
            botCheckboxContainer: !!botCheckboxContainer,
            filterButton: !!filterButton,
            filterDropdown: !!filterDropdown
        });
        return;
    }

    // Populate individual bot checkboxes
    botCheckboxContainer.innerHTML = '';
    Object.entries(BOT_NAMES).forEach(([botId, symbol]) => {
        const div = document.createElement('div');
        div.className = 'filter-option';

        const label = document.createElement('label');
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = botId;
        checkbox.className = 'bot-checkbox';
        checkbox.disabled = true; // Disabled when "All Bots" is checked

        const span = document.createElement('span');
        span.textContent = symbol;

        label.appendChild(checkbox);
        label.appendChild(span);
        div.appendChild(label);
        botCheckboxContainer.appendChild(div);

        // Listen for individual bot checkbox changes
        checkbox.addEventListener('change', handleBotCheckboxChange);
    });

    // Toggle dropdown on button click
    filterButton.addEventListener('click', (e) => {
        e.stopPropagation();
        e.preventDefault();
        const isVisible = filterDropdown.style.display === 'block';
        filterDropdown.style.display = isVisible ? 'none' : 'block';
        Logger.info('PnLReporting', `Filter dropdown ${isVisible ? 'closed' : 'opened'}`);
    });

    // Prevent dropdown from closing when clicking inside it
    filterDropdown.addEventListener('click', (e) => {
        e.stopPropagation();
    });

    // Close dropdown when clicking outside
    document.addEventListener('click', (e) => {
        if (!filterButton.contains(e.target) && !filterDropdown.contains(e.target)) {
            filterDropdown.style.display = 'none';
        }
    });

    // Handle "All Bots" checkbox
    allBotsCheckbox.addEventListener('change', handleAllBotsCheckboxChange);

    Logger.info('PnLReporting', `Bot filter setup with ${Object.keys(BOT_NAMES).length} bots`);
}

/**
 * Handle "All Bots" checkbox change
 */
function handleAllBotsCheckboxChange(e) {
    allBotsSelected = e.target.checked;
    const botCheckboxes = document.querySelectorAll('.bot-checkbox');

    if (allBotsSelected) {
        // Disable and uncheck individual bot checkboxes
        botCheckboxes.forEach(cb => {
            cb.disabled = true;
            cb.checked = false;
        });
        selectedBots.clear();
        updateFilterButtonText();
        loadPnLStatement();
    } else {
        // Enable individual bot checkboxes
        botCheckboxes.forEach(cb => {
            cb.disabled = false;
        });
    }
}

/**
 * Handle individual bot checkbox change
 */
function handleBotCheckboxChange(e) {
    const botId = e.target.value;

    if (e.target.checked) {
        selectedBots.add(botId);
    } else {
        selectedBots.delete(botId);
    }

    updateFilterButtonText();

    // Only reload if at least one bot is selected
    if (selectedBots.size > 0) {
        loadPnLStatement();
    }
}

/**
 * Update filter button text based on selection
 */
function updateFilterButtonText() {
    const filterText = document.getElementById('botFilterText');
    if (!filterText) return;

    if (allBotsSelected || selectedBots.size === 0) {
        filterText.textContent = 'All Bots';
    } else if (selectedBots.size === 1) {
        const botId = Array.from(selectedBots)[0];
        filterText.textContent = BOT_NAMES[botId] || `Bot ${botId}`;
    } else {
        const botNames = Array.from(selectedBots)
            .map(id => BOT_NAMES[id] || `Bot ${id}`)
            .join(', ');
        filterText.textContent = botNames.length > 30
            ? `${selectedBots.size} Bots Selected`
            : botNames;
    }
}

/**
 * Update P&L table column headers based on selected bots
 */
function updatePnLHeaders() {
    let headerText;

    if (allBotsSelected || selectedBots.size === 0) {
        headerText = 'All Bots';
    } else if (selectedBots.size === 1) {
        const botId = Array.from(selectedBots)[0];
        headerText = BOT_NAMES[botId] || `Bot ${botId}`;
    } else {
        const botNames = Array.from(selectedBots)
            .map(id => BOT_NAMES[id] || `Bot ${id}`)
            .join(', ');
        headerText = botNames.length > 20
            ? `${selectedBots.size} Bots`
            : botNames;
    }

    // Update headers for all three periods
    ['day', 'week', 'month'].forEach(period => {
        const headerElement = document.getElementById(`${period}-bot1-header`);
        if (headerElement) {
            headerElement.textContent = headerText;
        }
    });

    Logger.info('PnLReporting', `Updated headers to: ${headerText}`);
}

// Initialize dashboard on page load
document.addEventListener('DOMContentLoaded', async function() {
    Logger.info('PnLReporting', 'P&L Reporting Dashboard initializing...');

    // Note: Bot configs are already loaded by dashboard.js
    // Wait a moment for dashboard.js to finish loading config
    await new Promise(resolve => setTimeout(resolve, 100));

    // Setup multi-select bot filter
    setupBotFilter();

    // Set initial headers to "All Bots" (override dashboard.js hardcoded values)
    updatePnLHeaders();

    Logger.info('PnLReporting', 'Filter setup and headers updated');

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
        const allTrades = data.trades || [];

        // Filter trades by selected bots
        let filteredTrades;
        if (allBotsSelected || selectedBots.size === 0) {
            // Show all bots
            filteredTrades = allTrades;
        } else {
            // Filter to selected bots only
            filteredTrades = allTrades.filter(trade =>
                selectedBots.has(String(trade.botId))
            );
        }

        Logger.info('PnLReporting', `Filtered ${allTrades.length} trades to ${filteredTrades.length} for ${selectedBots.size || 'all'} bot(s)`);

        // Get selected dates
        const daySelector = document.getElementById('day-selector');
        const weekSelector = document.getElementById('week-selector');
        const weekYearSelector = document.getElementById('week-year-selector');
        const monthSelector = document.getElementById('month-selector');
        const monthYearSelector = document.getElementById('month-year-selector');

        // Calculate P&L for each period using filtered trades
        const dayPnL = calculatePnLForDay(filteredTrades, new Date(daySelector.value));

        const weekValue = `${weekYearSelector.value}-W${weekSelector.value.toString().padStart(2, '0')}`;
        const weekPnL = calculatePnLForWeek(filteredTrades, weekValue);

        const monthValue = `${monthYearSelector.value}-${monthSelector.value.toString().padStart(2, '0')}`;
        const monthPnL = calculatePnLForMonth(filteredTrades, monthValue);

        // Update column headers with selected bot names
        updatePnLHeaders();

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
