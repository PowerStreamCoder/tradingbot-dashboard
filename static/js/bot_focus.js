/**
 * Bot Focus Dashboard - Frontend JavaScript
 * Handles real-time updates, chart rendering, and data fetching
 * Symbol-agnostic: Loads bot configuration dynamically from API
 * Version: 2.0.0 - Code review fixes applied
 */

// Configuration (will be loaded dynamically from /api/bot-configs)
let CONFIG = null;
let configReady = false;
let configVersion = null;

// Default configuration template (used if API fails)
const DEFAULT_CONFIG = {
    botId: 1,
    symbol: '⚠️ CONFIG ERROR',
    botName: 'unknown',
    updateInterval: UPDATE_INTERVALS.BOT_OVERVIEW,
    chartUpdateInterval: UPDATE_INTERVALS.CHART,
    tablesUpdateInterval: UPDATE_INTERVALS.TABLES,
    logsUpdateInterval: UPDATE_INTERVALS.LOGS,
    apiEndpoints: API_ENDPOINTS
};

// State
let priceChart = null;
let updateTimer = null;
let lastUpdateTimestamps = {
    positions: 0,
    orders: 0,
    logs: 0
};

// Cache DOM elements (optimization - avoid repeated queries)
const ELEMENTS = {};

// v5.0.0: Profile cache to reduce API calls
// Cache profile for 60 seconds to avoid fetching on every trade history update
let _profileCache = {
    value: null,           // Current profile ("paper" or "live")
    timestamp: 0,          // Last fetch time
    ttl: 60000             // Cache TTL: 60 seconds
};

/**
 * Load bot configuration from API
 * Fetches /api/bot-configs and populates bot selector dropdown
 *
 * @returns {Promise<boolean>} True if successful, false on error
 * @throws Never throws - handles errors internally
 * @sideEffects Sets global CONFIG, configReady flag, configVersion, populates bot selector
 */
async function loadBotConfig() {
    try {
        const response = await fetch(API_ENDPOINTS.BOT_CONFIGS);

        if (!response.ok) {
            throw new Error(`API returned ${response.status}: ${response.statusText}`);
        }

        const data = await response.json();

        // Check for API error
        if (data.error) {
            Logger.error('BotFocus', 'Configuration error from API', data.error);
            showError('Configuration Error', data.error);
            useFallbackConfig();
            return false;
        }

        // Validate data structure
        if (!data.bots || !Array.isArray(data.bots)) {
            Logger.error('BotFocus', 'Invalid API response structure', data);
            showError('Configuration Error', 'Invalid response from API - missing bots array');
            useFallbackConfig();
            return false;
        }

        const activeBots = data.bots.filter(b => b.enabled);

        if (activeBots.length === 0) {
            Logger.warn('BotFocus', 'No active bots found in configuration');
            showError('No Active Bots', 'No enabled bots found in configuration. Please enable at least one bot in bots.json.');
            useFallbackConfig();
            return false;
        }

        // Populate bot selector dropdown
        populateBotSelector(activeBots, data.bots);

        // Check if user has a saved bot preference
        const savedBotId = localStorage.getItem('selectedBotId');
        let selectedBot = activeBots[0]; // Default to first bot

        if (savedBotId) {
            const preferredBot = activeBots.find(b => b.client_id === parseInt(savedBotId));
            if (preferredBot) {
                selectedBot = preferredBot;
            }
        }

        // Validate required fields
        const requiredFields = ['client_id', 'symbol', 'name', 'script'];
        const missingFields = requiredFields.filter(field => !selectedBot[field]);

        if (missingFields.length > 0) {
            Logger.error('BotFocus', 'Invalid bot configuration - missing fields', missingFields);
            showError(
                'Invalid Bot Configuration',
                `Missing required fields: ${missingFields.join(', ')}`
            );
            useFallbackConfig();
            return false;
        }

        // Build configuration
        CONFIG = {
            botId: selectedBot.client_id,  // Use client_id as bot_id for Firestore queries
            symbol: selectedBot.symbol.toUpperCase(),  // Normalize symbol
            botName: selectedBot.name,
            strategy: selectedBot.strategy || 'unknown',
            updateInterval: UPDATE_INTERVALS.BOT_OVERVIEW,
            chartUpdateInterval: UPDATE_INTERVALS.CHART,
            tablesUpdateInterval: UPDATE_INTERVALS.TABLES,
            logsUpdateInterval: UPDATE_INTERVALS.LOGS,
            apiEndpoints: API_ENDPOINTS
        };

        // Store version for update detection
        configVersion = data.version || Date.now();
        configReady = true;

        Logger.info('BotFocus', 'Bot configuration loaded', CONFIG);

        // Cache config to localStorage for offline fallback
        try {
            localStorage.setItem('lastGoodConfig', JSON.stringify({
                symbol: CONFIG.symbol,
                botId: CONFIG.botId,
                botName: CONFIG.botName,
                timestamp: Date.now()
            }));
        } catch (e) {
            Logger.warn('BotFocus', 'Failed to cache config to localStorage', e);
        }

        // Update page title and symbol references
        updateSymbolReferences();

        return true;

    } catch (error) {
        Logger.error('BotFocus', 'Failed to load bot configuration', error);
        showError('Configuration Load Failed', `Could not load bot configuration: ${error.message}. Check console for details.`);
        useFallbackConfig();
        return false;
    }
}

/**
 * Populate bot selector dropdown with all bots
 * @param {Array} activeBots - Enabled bots
 * @param {Array} allBots - All bots (enabled and disabled)
 */
function populateBotSelector(activeBots, allBots) {
    const selector = document.getElementById('botSelector');
    if (!selector) {
        Logger.warn('BotFocus', 'Bot selector element not found');
        return;
    }

    // Clear existing options
    selector.innerHTML = '';

    // Add enabled bots
    if (activeBots.length > 0) {
        activeBots.forEach(bot => {
            const option = document.createElement('option');
            option.value = bot.client_id;
            option.textContent = `${bot.symbol} - ${bot.name}`;
            if (bot.capital_override) {
                option.textContent += ` ($${(bot.capital_override / 1000).toFixed(0)}k)`;
            }
            selector.appendChild(option);
        });
    }

    // Add disabled bots if any (grayed out, informational)
    const disabledBots = allBots.filter(b => !b.enabled);
    if (disabledBots.length > 0) {
        const disabledGroup = document.createElement('optgroup');
        disabledGroup.label = '── Disabled ──';
        disabledBots.forEach(bot => {
            const option = document.createElement('option');
            option.value = bot.client_id;
            option.textContent = `${bot.symbol} - ${bot.name} (disabled)`;
            option.disabled = true;
            option.style.color = '#718096';
            disabledGroup.appendChild(option);
        });
        selector.appendChild(disabledGroup);
    }

    // Set selected value if saved preference exists
    const savedBotId = localStorage.getItem('selectedBotId');
    if (savedBotId) {
        selector.value = savedBotId;
    }

    Logger.info('BotFocus', `Bot selector populated with ${activeBots.length} active bots, ${disabledBots.length} disabled`);
}

/**
 * Handle bot selector change event
 * Reloads dashboard with selected bot's data
 */
async function handleBotSelectorChange(event) {
    const selectedBotId = parseInt(event.target.value);

    // Validate input
    if (isNaN(selectedBotId) || selectedBotId < 1) {
        Logger.error('BotFocus', 'Invalid bot ID selected', event.target.value);
        showError('Invalid Selection', 'Please select a valid bot');
        return;
    }

    Logger.info('BotFocus', `Bot selector changed to client_id: ${selectedBotId}`);

    // Stop updates first to prevent race conditions
    stopUpdates();

    try {
        // Save preference
        localStorage.setItem('selectedBotId', selectedBotId.toString());

        // Reload configuration (will pick up the saved preference)
        const success = await loadBotConfig();

        if (success) {
            // Only restart if config loaded successfully
            startUpdates();
            showNotification(`Switched to ${CONFIG.symbol} bot`, 'success');
        } else {
            showError('Failed to switch bot', 'Could not load bot configuration');
        }
    } catch (error) {
        Logger.error('BotFocus', 'Error switching bots', error);
        showError('Error', 'Failed to switch bots');
    }
}

/**
 * Use fallback configuration
 * Attempts to load from localStorage cache, otherwise uses safe defaults
 */
function useFallbackConfig() {
    // Try to load last good config from cache
    try {
        const cached = localStorage.getItem('lastGoodConfig');
        if (cached) {
            const cachedConfig = JSON.parse(cached);
            const age = Date.now() - cachedConfig.timestamp;

            // Use cache if less than 24 hours old
            if (age < 86400000) {
                Logger.info('BotFocus', 'Using cached config from localStorage', cachedConfig);
                CONFIG = {
                    ...DEFAULT_CONFIG,
                    symbol: cachedConfig.symbol,
                    botName: cachedConfig.botName,
                    botId: cachedConfig.botId
                };
                configReady = false; // Mark as not ready (using stale cache)
                updateSymbolReferences();
                return;
            }
        }
    } catch (e) {
        Logger.warn('BotFocus', 'Failed to load cached config', e);
    }

    // Fall back to error indicator
    CONFIG = {
        ...DEFAULT_CONFIG,
        symbol: '⚠️ CONFIG ERROR',
        botName: 'unknown',
        botId: 1
    };
    configReady = false;

    Logger.warn('BotFocus', 'Using fallback configuration with error indicator', CONFIG);
    updateSymbolReferences();
}

/**
 * Update all symbol references in the page
 * Called after config is loaded to populate dynamic elements
 */
function updateSymbolReferences() {
    if (!CONFIG) return;

    // Cache DOM elements on first call (performance optimization)
    if (!ELEMENTS.pageTitle) {
        ELEMENTS.pageTitle = document.getElementById('pageTitle');
        ELEMENTS.chartSymbol = document.getElementById('chartSymbol');
        ELEMENTS.symbolPlaceholders = Array.from(document.querySelectorAll('.symbol-placeholder'));
    }

    // Update page title
    document.title = `${CONFIG.symbol} Focus - SMA Bot Dashboard`;

    // Update header
    if (ELEMENTS.pageTitle) {
        ELEMENTS.pageTitle.textContent = `${CONFIG.symbol} Focus Dashboard`;
    }

    // Update chart title
    if (ELEMENTS.chartSymbol) {
        ELEMENTS.chartSymbol.textContent = CONFIG.symbol;
    }

    // Update all elements with class 'symbol-placeholder'
    ELEMENTS.symbolPlaceholders.forEach(el => {
        el.textContent = CONFIG.symbol;
    });

    Logger.debug('Updated symbol references to:', CONFIG.symbol);
}

/**
 * Show error notification to user
 * Creates a dismissible toast notification with styling
 *
 * @param {string} title - Error title
 * @param {string} message - Error details
 */
function showError(title, message) {
    const errorDiv = document.createElement('div');
    errorDiv.className = 'error-notification';
    errorDiv.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        background: linear-gradient(135deg, #f56565 0%, #e53e3e 100%);
        color: white;
        padding: 20px;
        border-radius: 8px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        z-index: 9999;
        max-width: 400px;
        animation: slideInRight 0.3s ease;
    `;
    errorDiv.innerHTML = `
        <div style="display: flex; align-items: start; gap: 12px;">
            <div style="font-size: 24px;">⚠️</div>
            <div style="flex: 1;">
                <strong style="display: block; margin-bottom: 8px; font-size: 1.1em;">${title}</strong>
                <div style="font-size: 0.95em; line-height: 1.4;">${message}</div>
            </div>
        </div>
        <button onclick="this.parentElement.remove()" style="margin-top: 12px; padding: 8px 16px; background: white; color: #e53e3e; border: none; border-radius: 4px; cursor: pointer; font-weight: 600; width: 100%;">
            Dismiss
        </button>
    `;
    document.body.appendChild(errorDiv);

    // Auto-dismiss after 10 seconds
    setTimeout(() => {
        if (errorDiv.parentElement) {
            errorDiv.style.animation = 'slideOutRight 0.3s ease';
            setTimeout(() => errorDiv.remove(), 300);
        }
    }, 10000);

    // Add animation styles if not already present
    if (!document.getElementById('notification-animations')) {
        const style = document.createElement('style');
        style.id = 'notification-animations';
        style.textContent = `
            @keyframes slideInRight {
                from { transform: translateX(100%); opacity: 0; }
                to { transform: translateX(0); opacity: 1; }
            }
            @keyframes slideOutRight {
                from { transform: translateX(0); opacity: 1; }
                to { transform: translateX(100%); opacity: 0; }
            }
        `;
        document.head.appendChild(style);
    }
}

/**
 * Utility function to safely update element text content
 * @param {string} elementId - The ID of the element to update
 * @param {string} value - The value to set
 * @param {string} className - Optional class name to add
 */
function updateElement(elementId, value, className = null) {
    const element = document.getElementById(elementId);
    if (element) {
        element.textContent = value;
        if (className) {
            element.className = className;
        }
    }
}

// Initialize dashboard
document.addEventListener('DOMContentLoaded', async () => {
    Logger.info('BotFocus', 'Dashboard initializing...');

    // Load bot configuration first
    const configLoaded = await loadBotConfig();

    if (!configLoaded) {
        Logger.warn('BotFocus', 'Dashboard starting with fallback configuration');
    }

    // Wait for config to be ready before proceeding
    if (!configReady) {
        Logger.warn('BotFocus', 'Config not ready, waiting...');
        await new Promise(resolve => setTimeout(resolve, 100));

        if (!configReady) {
            showError('Configuration Not Ready', 'Dashboard may not function correctly. Some features will be disabled.');
        }
    }

    Logger.info('BotFocus', `Bot Focus Dashboard initialized for ${CONFIG.symbol}`);

    // Clear cached table state on page load to force fresh render
    const tbody = document.getElementById('positionOrdersTableBody');
    if (tbody) {
        tbody.dataset.lastContent = '';
    }

    initializeChart();
    fetchInitialData();
    startPeriodicUpdates();
    attachEventListeners();

    // Start config update polling
    startConfigUpdatePolling();
});

/**
 * Start config update polling
 * Checks for configuration changes every 5 minutes
 */
function startConfigUpdatePolling() {
    setInterval(checkForConfigUpdates, 300000); // 5 minutes
}

/**
 * Check for configuration updates
 * Polls backend for version changes and prompts user to reload
 */
async function checkForConfigUpdates() {
    try {
        const response = await fetch(API_ENDPOINTS.BOT_CONFIGS);
        const data = await response.json();

        if (data.version && configVersion && data.version > configVersion) {
            Logger.info('BotFocus', 'Configuration updated detected', {
                old: configVersion,
                new: data.version
            });

            // Offer reload
            if (confirm('Bot configuration has been updated. Reload dashboard to apply changes?')) {
                location.reload();
            } else {
                // Update version to avoid repeated prompts
                configVersion = data.version;
            }
        }
    } catch (error) {
        Logger.debug('Config update check failed (non-critical):', error);
    }
}

/**
 * Attach event listeners to UI buttons
 */
function attachEventListeners() {
    // Bot selector dropdown
    const botSelector = document.getElementById('botSelector');
    if (botSelector) {
        botSelector.addEventListener('change', handleBotSelectorChange);
    }

    // Pause/Resume Bot button
    const pauseBotBtn = document.getElementById('pauseBotBtn');
    if (pauseBotBtn) {
        pauseBotBtn.addEventListener('click', handlePauseResumeBot);
    }

    // Create Position button
    const createPositionBtn = document.getElementById('createPositionBtn');
    if (createPositionBtn) {
        createPositionBtn.addEventListener('click', handleCreatePosition);
    }

    // Close Position button (if exists)
    const closePositionBtn = document.getElementById('closePositionBtn');
    if (closePositionBtn) {
        closePositionBtn.addEventListener('click', handleClosePosition);
    }

    // Profile switch button
    const switchProfileBtn = document.getElementById('switchProfileBtn');
    if (switchProfileBtn) {
        switchProfileBtn.addEventListener('click', handleProfileSwitch);
    }

    // Chart control buttons
    const chartBtns = document.querySelectorAll('.chart-btn');
    chartBtns.forEach((btn, index) => {
        btn.addEventListener('click', (e) => handleChartButtonClick(e, index));
    });
}

/**
 * Handle Pause/Resume Bot button click
 */
async function handlePauseResumeBot() {
    const btn = document.getElementById('pauseBotBtn');
    if (!btn) return;

    const isPaused = btn.classList.contains('paused');
    const action = isPaused ? 'restart' : 'stop';
    const actionText = isPaused ? 'Resume' : 'Pause';

    // Confirm action
    if (!confirm(`Are you sure you want to ${actionText.toLowerCase()} the ${CONFIG.symbol} trading bot?`)) {
        return;
    }

    // Disable button during operation
    btn.disabled = true;
    btn.innerHTML = `<span>⏳</span> ${actionText}ing...`;

    try {
        const response = await fetch(`/api/bot-control/${action}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });

        const data = await response.json();

        if (response.ok && data.status === 'success') {
            // Toggle button state
            if (isPaused) {
                // Resuming - button becomes Pause
                btn.classList.remove('paused');
                btn.classList.add('btn-danger');
                btn.innerHTML = '<span>⏸</span> Pause Bot';
            } else {
                // Pausing - button becomes Resume
                btn.classList.add('paused');
                btn.classList.remove('btn-danger');
                btn.innerHTML = '<span>▶</span> Resume Bot';
            }

            // Show success message
            showNotification(`Bot ${actionText.toLowerCase()}ed successfully`, 'success');
        } else {
            throw new Error(data.message || `Failed to ${action} bot`);
        }
    } catch (error) {
        console.error(`Error ${action}ing bot:`, error);
        showNotification(`Error: ${error.message}`, 'error');

        // Reset button
        btn.innerHTML = isPaused ? '<span>▶</span> Resume Bot' : '<span>⏸</span> Pause Bot';
    } finally {
        btn.disabled = false;
    }
}

/**
 * Show notification message to user
 */
function showNotification(message, type = 'info') {
    // Create notification element
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.textContent = message;
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 15px 20px;
        background: ${type === 'success' ? '#48bb78' : type === 'error' ? '#f56565' : '#4299e1'};
        color: white;
        border-radius: 8px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
        z-index: 10000;
        font-weight: 500;
        animation: slideIn 0.3s ease;
    `;

    document.body.appendChild(notification);

    // Remove after 3 seconds
    setTimeout(() => {
        notification.style.animation = 'slideOut 0.3s ease';
        setTimeout(() => notification.remove(), 300);
    }, 3000);
}

/**
 * Handle profile switch button click
 *
 * CRITICAL SAFETY FEATURE: This function implements a two-step confirmation
 * process when switching to live trading mode to prevent accidental activation
 * of real-money trading.
 *
 * Flow:
 * 1. Fetch current profile from backend
 * 2. Determine target profile (paper ↔ live)
 * 3. Show first confirmation dialog
 * 4. If switching to LIVE, show second "Real Money" warning dialog
 * 5. Disable button and show loading state
 * 6. Call backend API to switch profile
 * 7. Backend checks for open positions (blocks if any exist)
 * 8. Backend forwards to VM API
 * 9. VM updates profile file and restarts bots
 * 10. Update UI badge and refresh data
 *
 * Safety features:
 * - Double confirmation for live mode
 * - Position blocking on backend (cannot switch with open positions)
 * - Rate limiting on VM (5-second cooldown)
 * - Visual feedback (loading state, notifications)
 *
 * @async
 * @returns {Promise<void>}
 */

/**
 * Get current profile with caching (v5.0.0)
 *
 * Fetches the current trading profile (paper/live) with 60-second cache.
 * Reduces API calls from once per trade history update (every 10s) to once per minute.
 *
 * @param {boolean} forceRefresh - Bypass cache and fetch fresh value
 * @returns {Promise<string>} Current profile ("paper" or "live")
 */
async function getCurrentProfile(forceRefresh = false) {
    const now = Date.now();
    const age = now - _profileCache.timestamp;

    // Return cached value if valid and not forcing refresh
    if (!forceRefresh && _profileCache.value && age < _profileCache.ttl) {
        console.log(`[PROFILE] Using cached value: ${_profileCache.value.toUpperCase()} (age: ${Math.round(age/1000)}s)`);
        return _profileCache.value;
    }

    // Fetch fresh value
    try {
        console.log('[PROFILE] Fetching from API...');
        const response = await fetch('/api/bot-control/current-profile');

        if (response.ok) {
            const data = await response.json();
            _profileCache.value = data.profile || 'paper';
            _profileCache.timestamp = now;
            console.log(`[PROFILE] Fetched: ${_profileCache.value.toUpperCase()}`);
        } else {
            console.warn(`[PROFILE] API returned ${response.status}, defaulting to PAPER`);
            _profileCache.value = 'paper';
            _profileCache.timestamp = now;
        }
    } catch (error) {
        console.error('[PROFILE] Fetch failed, defaulting to PAPER:', error);
        _profileCache.value = 'paper';
        _profileCache.timestamp = now;
    }

    return _profileCache.value;
}

/**
 * Invalidate profile cache (v5.0.0)
 *
 * Forces next getCurrentProfile() call to fetch fresh value.
 * Called after profile switches to immediately reflect the change.
 */
function invalidateProfileCache() {
    console.log('[PROFILE] Cache invalidated');
    _profileCache.timestamp = 0;
}

async function handleProfileSwitch() {
    const btn = document.getElementById('switchProfileBtn');
    if (!btn) return;

    try {
        // Step 1: Check if bots are currently running via bot overview data
        // Bot overview has real-time status from Firestore heartbeat
        console.log('[PROFILE-SWITCH] Checking bot status via bot overview...');
        const overviewResponse = await fetch(CONFIG.apiEndpoints.botOverview);
        const overviewData = await overviewResponse.json();

        // Check if any bot has recent heartbeat (active within last 2 minutes)
        const botKey = `bot${CONFIG.botId}`;
        const botData = overviewData[botKey];
        let botsRunning = false;

        if (botData && botData.lastHeartbeat) {
            const heartbeatTime = new Date(botData.lastHeartbeat);
            const now = new Date();
            const ageMinutes = (now - heartbeatTime) / 1000 / 60;
            botsRunning = ageMinutes < 2; // Active if heartbeat within 2 minutes
            console.log(`[PROFILE-SWITCH] Bot heartbeat age: ${ageMinutes.toFixed(1)} minutes, running: ${botsRunning}`);
        }

        if (botsRunning) {
            // Bots are running - show clear message to stop them first
            console.log('[PROFILE-SWITCH] Bots are running, prompting user to stop them first');
            alert('⚠️ BOTS ARE STILL RUNNING\n\n' +
                  'You must STOP the bots before switching profiles.\n\n' +
                  'Steps:\n' +
                  '1. Click the "⏸ Pause Bots" button above\n' +
                  '2. Wait for bots to stop (status shows "Stopped")\n' +
                  '3. Then try switching profiles again\n\n' +
                  'This safety check prevents switching profiles during active trading.');
            return;
        }

        console.log('[PROFILE-SWITCH] Bots are stopped, proceeding with switch...');

        // Step 2: Get current profile from backend
        console.log('[PROFILE-SWITCH] Fetching current profile...');
        const currentResponse = await fetch('/api/bot-control/current-profile');
        const currentData = await currentResponse.json();
        const currentProfile = currentData.profile;
        const newProfile = currentProfile === 'paper' ? 'live' : 'paper';

        console.log(`[PROFILE-SWITCH] Current: ${currentProfile}, Target: ${newProfile}`);

        // Step 3: First confirmation dialog
        const action = newProfile === 'live' ? 'LIVE TRADING (REAL MONEY)' : 'PAPER TRADING (TEST MODE)';
        console.log(`[PROFILE-SWITCH] Showing first confirmation for: ${action}`);

        if (!confirm(`Switch to ${action}?`)) {
            console.log('[PROFILE-SWITCH] User cancelled at first confirmation');
            return;
        }

        // Step 4: Second confirmation ONLY for live mode (extra safety)
        if (newProfile === 'live') {
            console.log('[PROFILE-SWITCH] Showing second "Real Money" warning...');

            if (!confirm('⚠️ ARE YOU SURE?\n\nThis will enable LIVE TRADING with REAL MONEY.\n\nBots will restart automatically.\n\nClick OK to proceed.')) {
                console.log('[PROFILE-SWITCH] User cancelled at second confirmation');
                return;
            }
        }

        console.log('[PROFILE-SWITCH] User confirmed, proceeding with switch...');

        // Step 5: Disable button and show loading state
        btn.disabled = true;
        btn.innerHTML = '<span>⏳</span> Switching...';

        // Step 6: Call backend API to switch profile
        console.log(`[PROFILE-SWITCH] Calling API: POST /api/bot-control/switch-profile (profile=${newProfile})`);

        const response = await fetch('/api/bot-control/switch-profile', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({profile: newProfile})
        });

        const result = await response.json();

        // Step 7: Handle response
        if (response.ok && result.status === 'success') {
            console.log(`[PROFILE-SWITCH] ✅ SUCCESS: Switched to ${newProfile} mode`);
            showNotification(`✓ Switched to ${newProfile.toUpperCase()} mode. Bots restarting...`, 'success');

            // v5.0.0: Invalidate profile cache to force immediate refresh
            invalidateProfileCache();

            // Update mode indicator immediately (optimistic UI update)
            updateModeIndicator(newProfile);

            // Reset pause button to active state (bots auto-restart after profile switch)
            const pauseBtn = document.getElementById('pauseBotBtn');
            if (pauseBtn) {
                pauseBtn.classList.remove('paused');
                pauseBtn.classList.add('btn-danger');
                pauseBtn.innerHTML = '<span>⏸</span> Pause Bot';
                pauseBtn.disabled = false;
                console.log('[PROFILE-SWITCH] Reset pause button to active state (bots restarting)');
            }

            // Refresh data after 5 seconds to allow bots to restart
            console.log('[PROFILE-SWITCH] Scheduling data refresh in 5 seconds...');
            setTimeout(() => {
                console.log('[PROFILE-SWITCH] Refreshing dashboard data...');
                fetchInitialData();
            }, 5000);
        } else {
            // Error from backend (e.g., positions are open, invalid profile)
            // FastAPI returns errors as {"detail": "message"}, but VM API uses {"message": "..."}
            const errorMsg = result.detail || result.message || 'Switch failed';
            console.error(`[PROFILE-SWITCH] Backend error: ${errorMsg}`);
            throw new Error(errorMsg);
        }

    } catch (error) {
        console.error(`[PROFILE-SWITCH] ❌ ERROR: ${error.message}`);
        showNotification(`❌ Profile switch failed: ${error.message}`, 'error');
    } finally {
        // Step 8: Re-enable button and update text
        const btn = document.getElementById('switchProfileBtn');
        if (btn) {
            btn.disabled = false;
            updateSwitchButtonText();
        }
    }
}

/**
 * Update mode indicator badge in header
 *
 * Changes the badge color and text based on current trading mode:
 * - Paper mode: Blue badge with "📝 PAPER MODE"
 * - Live mode: Red pulsing badge with "⚠️ LIVE MODE"
 *
 * The pulsing animation on live mode serves as a constant visual reminder
 * that real money is at risk.
 *
 * @param {string} profile - Current profile ("paper" or "live")
 */
function updateModeIndicator(profile) {
    const badge = document.getElementById('modeBadge');
    if (!badge) return;

    console.log(`[MODE-INDICATOR] Updating badge to: ${profile}`);

    if (profile === 'live') {
        // LIVE MODE: Red pulsing badge for high visibility
        badge.textContent = '⚠️ LIVE MODE';
        badge.className = 'mode-badge live';
    } else {
        // PAPER MODE: Blue static badge
        badge.textContent = '📝 PAPER MODE';
        badge.className = 'mode-badge paper';
    }
}

/**
 * Update switch button text based on current mode
 *
 * Fetches current profile from backend and updates:
 * 1. Button text to show opposite mode ("Switch to LIVE" or "Switch to PAPER")
 * 2. Mode indicator badge color and text
 *
 * This function is called:
 * - On page load (fetchInitialData)
 * - Every 10 seconds (startPeriodicUpdates)
 * - After successful profile switch
 *
 * The periodic update ensures the dashboard always shows the correct mode
 * even if the profile is changed from another source (e.g., CLI, direct VM access)
 *
 * @async
 * @returns {Promise<void>}
 */
async function updateSwitchButtonText() {
    const btn = document.getElementById('switchProfileBtn');
    if (!btn) return;

    try {
        // Fetch current profile from backend
        const response = await fetch('/api/bot-control/current-profile');
        const data = await response.json();
        const currentProfile = data.profile;
        const targetProfile = currentProfile === 'paper' ? 'live' : 'paper';

        // Update button text to show opposite mode with better icons
        if (targetProfile === 'live') {
            btn.innerHTML = `<span>🚀</span> Switch to LIVE`;
            btn.className = 'btn btn-profile-switch switch-to-live';
            btn.title = 'Switch to live trading mode (real money)';
        } else {
            btn.innerHTML = `<span>🧪</span> Switch to PAPER`;
            btn.className = 'btn btn-profile-switch switch-to-paper';
            btn.title = 'Switch to paper trading mode (test mode)';
        }

        // Update mode indicator badge
        updateModeIndicator(currentProfile);

        console.log(`[MODE-INDICATOR] Current profile: ${currentProfile}, Button shows: Switch to ${targetProfile}`);

    } catch (error) {
        console.error('[MODE-INDICATOR] Failed to fetch current profile:', error);
        // Don't show error notification - this is a background update
        // Button will retry on next periodic update
    }
}

/**
 * Handle Close Position button click
 */
async function handleClosePosition() {
    const btn = document.getElementById('closePositionBtn');
    if (!btn || btn.disabled) return;

    // Get selected position
    const selectedRadio = document.querySelector('input[name="selectedPosition"]:checked');
    if (!selectedRadio) {
        showNotification('Please select a position to close', 'error');
        return;
    }

    const positionId = selectedRadio.value;

    // Parse position ID: format is "botId-symbol-bucketGroup-bucketId"
    const parts = positionId.split('-');
    if (parts.length < 4) {
        showNotification('Invalid position ID format', 'error');
        return;
    }

    const botId = parseInt(parts[0], 10);
    const symbol = parts[1];
    const bucketGroup = parts[2];
    const bucketId = parseInt(parts[3], 10);

    // Confirm action
    if (!confirm(`Close the selected ${CONFIG.symbol} position?\n\nThis will:\n• Sell all shares at market price\n• Close any associated orders (stop loss, profit target)\n• Buy back any sold covered calls\n\nContinue?`)) {
        return;
    }

    // Disable button during operation
    btn.disabled = true;
    btn.innerHTML = '<span>⏳</span> Closing...';

    try {
        const response = await fetch('/api/close-position', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                bot_id: botId,
                symbol: symbol,
                bucket_group: bucketGroup,
                bucket_id: bucketId,
                close_covered_calls: true
            })
        });

        const data = await response.json();

        if (response.ok && data.success) {
            showNotification(data.message || 'Position closed successfully', 'success');
            // Refresh data to show updated state
            setTimeout(() => {
                fetchInitialData();
            }, 1000);
        } else {
            throw new Error(data.detail || data.message || 'Failed to close position');
        }
    } catch (error) {
        console.error('Error closing position:', error);
        showNotification(`Error: ${error.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<span>✕</span> Close Position';
    }
}

/**
 * Handle Create Position button click
 */
async function handleCreatePosition() {
    const btn = document.getElementById('createPositionBtn');
    if (!btn) return;

    // Confirm action
    if (!confirm(`Create a new LONG position for ${CONFIG.symbol}?\n\n• Buy 100 shares at current market price\n• Sell covered call immediately\n• Uses SMA crossover config (2.0x ATR target, 5% stop loss)\n\nThis will bypass all strategy checks. Continue?`)) {
        return;
    }

    // Disable button during operation
    btn.disabled = true;
    btn.innerHTML = '<span>⏳</span> Creating...';

    try {
        const response = await fetch('/api/create-position', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                bot_id: CONFIG.botId,
                symbol: CONFIG.symbol
            })
        });

        const result = await response.json();

        if (response.ok && result.success) {
            alert(`✅ ${result.message}\n\nThe bot will create the position on its next update cycle (within 2-5 seconds).`);
            console.log('Create position command sent successfully');

            // Poll for command result
            setTimeout(() => checkCommandResult(), 3000);
        } else {
            const errorMsg = result.detail || result.message || 'Unknown error';
            alert(`❌ Failed to create position:\n\n${errorMsg}`);
            console.error('Create position failed:', result);
        }
    } catch (error) {
        alert(`❌ Error creating position:\n\n${error.message}`);
        console.error('Create position error:', error);
    } finally {
        // Re-enable button
        btn.disabled = false;
        btn.innerHTML = '<span>+</span> Create Position';
    }
}

/**
 * Check for command result from bot (last_command_result in Firestore)
 */
async function checkCommandResult() {
    try {
        const response = await fetch(`${CONFIG.apiEndpoints.botOverview}`);
        const data = await response.json();

        // Check if there's a recent command result
        const botData = data.data?.bot1;
        if (botData?.last_command_result) {
            const result = botData.last_command_result;
            const timestamp = new Date(result.timestamp);
            const ageSeconds = (Date.now() - timestamp.getTime()) / 1000;

            // Only show if result is less than 10 seconds old
            if (ageSeconds < 10) {
                if (result.success) {
                    alert(`✅ Position Created Successfully!\n\n${result.message}\n\nBucket: ${result.bucket_group}-${result.bucket_id}\nEntry Price: $${result.entry_price?.toFixed(2) || '--'}`);
                } else {
                    alert(`❌ Position Creation Failed\n\n${result.message}`);
                }
            }
        }
    } catch (error) {
        console.error('Error checking command result:', error);
    }
}

/**
 * Handle chart control button clicks
 * @param {Event} e - Click event
 * @param {number} index - Button index (0=Indicators, 1=Trades)
 */
function handleChartButtonClick(e, index) {
    const btn = e.currentTarget;
    const wasActive = btn.classList.contains('active');

    switch(index) {
        case 0: // Indicators button
            btn.classList.toggle('active');
            const showIndicators = btn.classList.contains('active');
            console.log(`Indicators button clicked: ${showIndicators ? 'SHOW' : 'HIDE'} SMA lines`);
            toggleChartIndicators(showIndicators);
            break;
        // Trades button removed - trade markers feature not implemented
    }
}

/**
 * Toggle chart indicators (SMA lines)
 */
function toggleChartIndicators(show) {
    if (!priceChart) return;

    // Toggle SMA 5, SMA 20, and SMA 200 datasets
    priceChart.data.datasets[1].hidden = !show; // SMA 5
    priceChart.data.datasets[2].hidden = !show; // SMA 20
    priceChart.data.datasets[3].hidden = !show; // SMA 200
    priceChart.update();
}

/**
 * Initialize the price chart using Chart.js
 */
function initializeChart() {
    const ctx = document.getElementById('priceChart');
    if (!ctx) return;

    priceChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [], // Timestamps
            datasets: [
                {
                    label: 'Price',
                    data: [],
                    borderColor: '#4299e1',
                    backgroundColor: 'rgba(66, 153, 225, 0.1)',
                    borderWidth: 2.5,
                    pointRadius: 0,
                    pointHoverRadius: 5,
                    pointHoverBackgroundColor: '#4299e1',
                    pointHoverBorderColor: '#ffffff',
                    pointHoverBorderWidth: 2,
                    tension: 0.1,
                    fill: true,
                },
                {
                    label: 'SMA 5',
                    data: [],
                    borderColor: '#48bb78',
                    backgroundColor: 'transparent',
                    borderWidth: 2,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    borderDash: [8, 4],
                    hidden: false,  // Initially visible
                    tension: 0.2,
                },
                {
                    label: 'SMA 20',
                    data: [],
                    borderColor: '#ed8936',
                    backgroundColor: 'transparent',
                    borderWidth: 2,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    borderDash: [8, 4],
                    hidden: false,  // Initially visible
                    tension: 0.2,
                },
                {
                    label: 'SMA 200',
                    data: [],
                    borderColor: '#9f7aea',  // Purple for regime indicator
                    backgroundColor: 'transparent',
                    borderWidth: 2,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    borderDash: [12, 6],  // Longer dashes to distinguish from SMA5/20
                    hidden: false,  // Initially visible
                    tension: 0.2,
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                intersect: false,
                mode: 'index',
            },
            plugins: {
                legend: {
                    display: true,
                    position: 'top',
                    labels: {
                        color: '#a0aec0',
                        font: {
                            size: 12,
                            family: 'Inter, system-ui, sans-serif'
                        },
                        padding: 15,
                        usePointStyle: true,
                        pointStyle: 'circle'
                    }
                },
                tooltip: {
                    enabled: true,
                    backgroundColor: 'rgba(30, 36, 66, 0.95)',
                    titleColor: '#e2e8f0',
                    bodyColor: '#a0aec0',
                    borderColor: '#4299e1',
                    borderWidth: 1,
                    padding: 12,
                    displayColors: true,
                    callbacks: {
                        label: function(context) {
                            let label = context.dataset.label || '';
                            if (label) {
                                label += ': ';
                            }
                            if (context.parsed.y !== null) {
                                label += '$' + context.parsed.y.toFixed(2);
                            }
                            return label;
                        }
                    }
                }
            },
            scales: {
                x: {
                    grid: {
                        color: '#2d3748',
                        drawBorder: false,
                        lineWidth: 1,
                    },
                    ticks: {
                        color: '#a0aec0',
                        font: {
                            size: 11,
                            family: 'Inter, system-ui, sans-serif'
                        },
                        maxRotation: 0,
                        autoSkipPadding: 10
                    }
                },
                y: {
                    position: 'right',
                    grid: {
                        color: '#2d3748',
                        drawBorder: false,
                        lineWidth: 1,
                    },
                    ticks: {
                        color: '#a0aec0',
                        font: {
                            size: 11,
                            family: 'Inter, system-ui, sans-serif'
                        },
                        callback: function(value) {
                            return '$' + value.toFixed(2);
                        },
                        padding: 8
                    },
                    beginAtZero: false  // Don't force Y-axis to start at 0
                }
            },
            animation: {
                duration: 750,
                easing: 'easeInOutQuart'
            }
        }
    });
}

/**
 * Fetch initial data on page load
 */
async function fetchInitialData() {
    try {
        await Promise.all([
            updateBotOverview(),
            updateBotStatus(),
            updateTradesData(),
            updateTradeHistory(),  // NEW: Load trade history table
            updateLogsData(),
            updateSwitchButtonText(),  // Load current profile
        ]);
    } catch (error) {
        console.error('Error fetching initial data:', error);
    }
}

/**
 * Start periodic updates
 */
function startPeriodicUpdates() {
    // Update bot overview and position data
    updateTimer = setInterval(async () => {
        await updateBotOverview();
        await updateBotStatus();
        updateMarketClock();
    }, CONFIG.updateInterval);

    // Update chart less frequently (Alpha Vantage rate limits)
    setInterval(async () => {
        await updateChartData();
    }, CONFIG.chartUpdateInterval);

    // Update tables less frequently to reduce flickering
    setInterval(async () => {
        const now = Date.now();
        if (now - lastUpdateTimestamps.positions > CONFIG.tablesUpdateInterval) {
            await updateTradesData();
            await updateTradeHistory();  // NEW: Update trade history
            lastUpdateTimestamps.positions = now;
        }
        if (now - lastUpdateTimestamps.orders > CONFIG.tablesUpdateInterval) {
            updateOrdersTableDebounced();
            lastUpdateTimestamps.orders = now;
        }
    }, CONFIG.tablesUpdateInterval);

    // Update profile indicator every 10 seconds
    setInterval(updateSwitchButtonText, 10000);

    // Update logs less frequently to reduce flickering
    setInterval(async () => {
        const now = Date.now();
        if (now - lastUpdateTimestamps.logs > CONFIG.logsUpdateInterval) {
            await updateLogsData();
            lastUpdateTimestamps.logs = now;
        }
    }, CONFIG.logsUpdateInterval);

    // Initial fetch for chart
    updateChartData();

    // Initial fetch for trades and logs
    updateTradesData();
    updateTradeHistory();  // NEW: Initial trade history load
    updateLogsData();
}

/**
 * Debounced update for chart info to prevent flickering
 */
/**
 * Update chart info section (Last Price, Session Change)
 * No debouncing - updates immediately when data changes
 */
function updateChartInfoDebounced(lastPrice, sessionChange, sessionChangeClass) {
    // Ensure values are visible by updating the elements
    const lastPriceEl = document.getElementById('lastPrice');
    const sessionChangeEl = document.getElementById('sessionChange');

    if (lastPrice && lastPriceEl) {
        lastPriceEl.textContent = lastPrice;
        lastPriceEl.style.display = 'block';
        lastPriceEl.style.visibility = 'visible';
    }
    if (sessionChange && sessionChangeEl) {
        sessionChangeEl.textContent = sessionChange;
        sessionChangeEl.className = `value ${sessionChangeClass}`;
        sessionChangeEl.style.display = 'block';
        sessionChangeEl.style.visibility = 'visible';
    }
}

/**
 * Update positions table (called every 5 seconds)
 * No debouncing - relies on accurate change detection to prevent flicker
 */
function updatePositionsTableDebounced(botData) {
    updatePositionsTable(botData);
}

/**
 * Update orders table (called every 5 seconds)
 * No debouncing - relies on accurate change detection to prevent flicker
 */
function updateOrdersTableDebounced() {
    updateOrdersTable();
}

/**
 * Update market clock
 */
function updateMarketClock() {
    const now = new Date();
    const options = {
        timeZone: 'America/New_York',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false
    };
    const timeString = now.toLocaleTimeString('en-US', options);

    const clockEl = document.getElementById('marketClock');
    if (clockEl) {
        clockEl.textContent = timeString + ' ET';
    }
}

/**
 * Clear stale position data when Firestore is empty
 */
function clearStalePositionData() {
    console.warn('Firestore data is empty - clearing stale browser cache');

    // Clear position display
    const posSymbol = document.getElementById('posSymbol');
    const posQuantity = document.getElementById('posQuantity');
    const posAvgPrice = document.getElementById('posAvgPrice');
    const posMarkPrice = document.getElementById('posMarkPrice');
    const posUnrealizedPnl = document.getElementById('posUnrealizedPnl');
    const posPnlPercent = document.getElementById('posPnlPercent');

    if (posSymbol) posSymbol.textContent = CONFIG.symbol || 'N/A';
    if (posQuantity) posQuantity.textContent = '--';
    if (posAvgPrice) posAvgPrice.textContent = '--';
    if (posMarkPrice) posMarkPrice.textContent = '--';
    if (posUnrealizedPnl) {
        posUnrealizedPnl.textContent = '$0.00';
        posUnrealizedPnl.className = 'value';
    }
    if (posPnlPercent) {
        posPnlPercent.textContent = '0.00%';
        posPnlPercent.className = 'value';
    }

    // Clear position status
    const positionStatus = document.getElementById('positionStatus');
    if (positionStatus) {
        positionStatus.textContent = 'No Position';
        positionStatus.className = 'position-status';
    }

    // Clear positions table
    const tbody = document.getElementById('positionsTableBody');
    if (tbody) {
        tbody.innerHTML = '<tr><td colspan="10" style="text-align: center; color: #888;">No active positions (Firestore data syncing...)</td></tr>';
        tbody.dataset.lastContent = ''; // Clear cache
    }

    // Clear global bot data cache
    window.latestBotData = null;
}

/**
 * Update bot overview data (position, performance, health)
 */
async function updateBotOverview() {
    // Check config readiness
    if (!configReady) {
        Logger.warn('BotFocus', 'Config not ready, skipping bot overview update');
        return;
    }

    try {
        const response = await fetch(CONFIG.apiEndpoints.botOverview);
        const data = await response.json();

        const botKey = `bot${CONFIG.botId}`;
        const botData = data[botKey];  // Fixed: data is the direct bot data, not nested under 'data'

        if (!botData) {
            Logger.warn('BotFocus', `No bot data found for bot ${CONFIG.botId}`);
            // Clear stale data when Firestore returns empty
            clearStalePositionData();
            return;
        }

        // Get current price from multiple sources
        // Priority: 1) botData.currentPrice, 2) active bucket currentPrice, 3) sma5 from bot-status
        let currentPrice = botData.currentPrice;

        // Check active bucket for current price if not in botData
        if (!currentPrice) {
            for (let i = 1; i <= 10; i++) {
                const bucket = botData[`bucket${i}`];
                if (bucket && bucket.currentPrice) {
                    currentPrice = bucket.currentPrice;
                    break;
                }
            }
        }

        // Store current price in botData for other functions
        botData.currentPrice = currentPrice;

        // Update strategy display
        if (botData.strategy) {
            const strategyDisplay = botData.strategy === 'sma_crossover'
                ? 'SMA 5/20 Crossover'
                : 'Momentum';
            updateElement('botStrategy', strategyDisplay);
            // Store strategy globally for log filtering
            window.activeStrategy = botData.strategy;
        }

        // Update current price display - ALWAYS update even if no position
        // Use SMA5 as fallback if no currentPrice available
        const displayPrice = currentPrice || window.latestBotData?.sma5 || null;

        if (displayPrice) {
            // Get reference price for session change calculation
            // Priority: 1) active position entry price, 2) yesterday's close, 3) current price as baseline
            const activeBucket = getActiveBucket(botData);
            const referencePrice = activeBucket?.entryPrice || displayPrice;

            const sessionChange = displayPrice - referencePrice;
            const sessionChangePct = (sessionChange / referencePrice) * 100;
            const changeClass = sessionChange >= 0 ? 'positive' : 'negative';
            const changeSign = sessionChange >= 0 ? '+' : '';

            updateChartInfoDebounced(
                `$${displayPrice.toFixed(2)}`,
                `${changeSign}${sessionChangePct.toFixed(2)}%`,
                changeClass
            );
        } else {
            // No price data available - show placeholders
            updateChartInfoDebounced('--', '--', '');
        }

        // Update position data with debouncing
        updatePositionData(botData);

        // Update SMA values
        updateSMAStatus(botData);

        // Store botData globally for tables
        window.latestBotData = botData;

        // Update merged position/orders table (replaces separate position and orders tables)
        updatePositionOrdersTable(botData);

    } catch (error) {
        console.error('Error updating bot overview:', error);
    }
}

// Cache for capital data (refreshed less frequently than bot status)
let capitalCache = {
    value: null,
    timestamp: 0,
    ttl: 60000 // 60 seconds cache
};

/**
 * Fetch capital configuration from bot-overview
 * Cached to avoid excessive API calls
 */
async function fetchCapitalConfig() {
    const now = Date.now();

    // Return cached value if still valid
    if (capitalCache.value !== null && (now - capitalCache.timestamp) < capitalCache.ttl) {
        return capitalCache.value;
    }

    try {
        const response = await fetch('/api/bot-overview', {
            credentials: 'include'
        });
        const data = await response.json();

        // Extract capital for current bot (bot3 for IWM)
        const botKey = `bot${CONFIG.botId}`;
        const botData = data[botKey] || {};
        const capital = botData.capital_per_bucket_long || 5000; // Fallback to 5000

        // Update cache
        capitalCache.value = capital;
        capitalCache.timestamp = now;

        return capital;
    } catch (error) {
        console.error('Failed to fetch capital config:', error);
        // Return cached value or fallback
        return capitalCache.value || 5000;
    }
}

/**
 * Update bot health and status metrics
 */
async function updateBotStatus() {
    try {
        const startTime = Date.now();
        const response = await fetch(`/api/bot-status/${CONFIG.symbol}`, {
            credentials: 'include'
        });
        const apiLatency = Date.now() - startTime;

        const data = await response.json();

        // Update API Latency
        updateElement('apiLatency', `${apiLatency} ms`);

        // Update Broker Connection
        const brokerConnected = data.broker_connected && data.is_trading;
        const connStatusEl = document.getElementById('brokerConnection');
        if (connStatusEl) {
            const dot = connStatusEl.querySelector('.status-dot');
            if (brokerConnected) {
                connStatusEl.innerHTML = '<span class="status-dot" style="background-color: #48bb78;"></span> Connected';
            } else {
                connStatusEl.innerHTML = '<span class="status-dot" style="background-color: #f56565;"></span> Disconnected';
            }
        }

        // Update Data Feed (based on market status)
        const marketStatus = data.market_status || 'unknown';
        updateElement('dataFeed', marketStatus === 'open' ? 'Live' : 'Closed');

        // Update Heartbeat
        // Update Heartbeat (bot health indicator next to clock)
        if (data.heartbeat_age_seconds !== null && data.heartbeat_age_seconds < 30) {
            updateElement('heartbeat', '✓'); // Bot is healthy (< 30s)
        } else if (data.heartbeat_age_seconds !== null) {
            updateElement('heartbeat', `${Math.round(data.heartbeat_age_seconds)}s`); // Show age if stale
        } else {
            updateElement('heartbeat', '--');
        }

        // Update Last Signal
        if (data.last_heartbeat) {
            const lastSignalTime = new Date(data.last_heartbeat);
            const now = new Date();
            const diffSeconds = Math.floor((now - lastSignalTime) / 1000);
            updateElement('lastSignal', formatTimeSince(diffSeconds));
        } else {
            updateElement('lastSignal', '--');
        }

        // Update Risk Controls in top bar
        // Fetch capital from bot-overview (cached with 60s TTL)
        const capitalPerBucket = await fetchCapitalConfig();

        // Daily Loss Limit: 5% of capital per bucket
        const dailyLossLimit = capitalPerBucket > 0 ? -(capitalPerBucket * 0.05) : null;
        const dailyLossEl = document.getElementById('topDailyLossLimit');
        if (dailyLossEl) {
            dailyLossEl.textContent = dailyLossLimit !== null
                ? `-$${Math.abs(dailyLossLimit).toLocaleString('en-US', {maximumFractionDigits: 0})}`
                : '--';
        }

        // Max Drawdown: 5% threshold
        const maxDrawdown = dailyLossLimit; // Same as daily loss limit
        const maxDrawdownPct = 5;
        const maxDrawdownEl = document.getElementById('topMaxDrawdown');
        if (maxDrawdownEl) {
            maxDrawdownEl.textContent = maxDrawdown !== null
                ? `-$${Math.abs(maxDrawdown).toLocaleString('en-US', {maximumFractionDigits: 0})} (${maxDrawdownPct}%)`
                : '-- (--)';
        }

        // Exposure: Calculate from active buckets and capital
        const activeBuckets = data.active_buckets || 0;
        const exposure = activeBuckets > 0 ? Math.abs(activeBuckets * capitalPerBucket) : 0;
        const exposureEl = document.getElementById('topExposure');
        if (exposureEl) {
            exposureEl.textContent = `$${exposure.toLocaleString('en-US', {maximumFractionDigits: 0})}`;
        }

        // Margin Used: Paper trading uses 0% margin
        const marginUsedEl = document.getElementById('topMarginUsed');
        if (marginUsedEl) {
            marginUsedEl.textContent = '0%';
        }

        // Kill Switch: Shows whether bot is actively trading
        const topKillSwitchEl = document.getElementById('topKillSwitch');
        if (topKillSwitchEl && data.is_trading !== undefined && data.is_trading !== null) {
            const isArmed = data.is_trading;
            topKillSwitchEl.innerHTML = isArmed
                ? '<span class="status-dot" style="background-color: #48bb78;"></span> Armed'
                : '<span class="status-dot" style="background-color: #f56565;"></span> Triggered';
        }

        // Update SMA Crossover Status (new data from bot)
        if (data.sma5 !== null && data.sma5 !== undefined) {
            updateElement('sma5', `$${data.sma5.toFixed(2)}`);
        }
        if (data.sma20 !== null && data.sma20 !== undefined) {
            updateElement('sma20', `$${data.sma20.toFixed(2)}`);
        }
        if (data.sma200 !== null && data.sma200 !== undefined) {
            updateElement('sma200', `$${data.sma200.toFixed(2)}`);
        }
        if (data.sma_spread_pct !== null && data.sma_spread_pct !== undefined) {
            const spreadColor = data.sma_spread_pct > 0 ? '#48bb78' : '#f56565';
            const spreadEl = document.getElementById('smaSpread');
            if (spreadEl) {
                spreadEl.textContent = `${data.sma_spread_pct > 0 ? '+' : ''}${data.sma_spread_pct.toFixed(2)}%`;
                spreadEl.style.color = spreadColor;
            }
        }
        if (data.sma_signal) {
            const signalEl = document.getElementById('strategySignal');
            if (signalEl) {
                signalEl.textContent = data.sma_signal;
                if (data.sma_signal === 'LONG') {
                    signalEl.style.color = '#48bb78';
                } else if (data.sma_signal === 'SHORT') {
                    signalEl.style.color = '#f56565';
                } else {
                    signalEl.style.color = '#a0aec0';
                }
            }
        }

        // Update crossover confirmation status (NEW)
        if (data.crossover_confirmation !== undefined && data.crossover_confirmation !== null) {
            updateElement('tickConfirmation', data.crossover_confirmation);
        } else {
            updateElement('tickConfirmation', 'N/A');
        }

        // Update last crossover timestamp (NEW)
        if (data.last_crossover) {
            try {
                const crossoverDate = new Date(data.last_crossover);
                const now = new Date();
                const diffMs = now - crossoverDate;
                const diffMinutes = Math.floor(diffMs / 60000);
                const diffHours = Math.floor(diffMs / 3600000);

                let displayTime;
                if (diffMinutes < 60) {
                    displayTime = `${diffMinutes}m ago`;
                } else if (diffHours < 24) {
                    displayTime = `${diffHours}h ago`;
                } else {
                    displayTime = crossoverDate.toLocaleString('en-US', {
                        month: 'short',
                        day: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit',
                        hour12: false
                    });
                }
                updateElement('lastCrossover', displayTime);
            } catch (e) {
                console.error('Error parsing last_crossover timestamp:', e);
                updateElement('lastCrossover', 'N/A');
            }
        } else {
            updateElement('lastCrossover', 'N/A');
        }

        // Update Last Price and Session Change if not already set from bot-overview
        // Use SMA5 as a reliable proxy for current price
        const lastPriceEl = document.getElementById('lastPrice');
        const sessionChangeEl = document.getElementById('sessionChange');

        if (data.sma5 && lastPriceEl && sessionChangeEl) {
            // Only update if showing placeholder or stale data
            if (lastPriceEl.textContent === '--' || lastPriceEl.textContent === '') {
                updateChartInfoDebounced(
                    `$${data.sma5.toFixed(2)}`,
                    `0.00%`,  // Default to 0% if no reference price
                    ''
                );
            }
        }

        // Store latest bot data for other functions
        window.latestBotData = data;

        // Orders table updated separately to reduce calls

    } catch (error) {
        console.error('Error updating bot status:', error);
    }
}

/**
 * Update position data
 */
function updatePositionData(botData) {
    // Find first active bucket
    let activeBucket = null;
    for (let i = 1; i <= 10; i++) {
        const bucket = botData[`bucket${i}`];
        if (bucket && bucket.entryPrice) {
            activeBucket = bucket;
            break;
        }
    }

    if (activeBucket) {
        // Position is active
        const side = activeBucket.side || 'long';
        updateElement('positionStatus', `${side.toUpperCase()} ACTIVE`, 'position-status');
        updateElement('posAvgPrice', `$${activeBucket.entryPrice.toFixed(2)}`);

        // Use actual current price from bot (updated every 5 seconds)
        const currentPrice = activeBucket.currentPrice || botData.currentPrice || activeBucket.entryPrice;
        updateElement('posMarkPrice', `$${currentPrice.toFixed(2)}`);

        // Use actual quantity and P&L from bot (if available)
        const quantity = activeBucket.quantity || 100; // Default to 100 if not provided

        // Calculate unrealized P&L from stock position
        const stockUnrealizedPnl = activeBucket.unrealizedPnL !== undefined
            ? activeBucket.unrealizedPnL
            : (currentPrice - activeBucket.entryPrice) * quantity;

        // Add covered call gains/losses
        // optionPremium is already the TOTAL premium for the contract (not per share)
        let coveredCallGain = 0;
        if (activeBucket.optionSold && activeBucket.optionPremium) {
            const premiumReceived = activeBucket.optionPremium; // Already total for contract

            // If we have current option value, calculate actual gain/loss
            // Otherwise, assume we keep the full premium (conservative estimate)
            if (activeBucket.optionCurrentValue !== undefined) {
                // Current P&L = Premium Received - Current Option Value
                coveredCallGain = premiumReceived - activeBucket.optionCurrentValue;
            } else {
                // Conservative: assume we keep the premium (no buyback needed)
                coveredCallGain = premiumReceived;
            }
        }

        // Total unrealized P&L includes both stock position and covered call P&L
        const totalUnrealizedPnl = stockUnrealizedPnl + coveredCallGain;

        // Calculate P&L percentage based on TOTAL investment (stock position value)
        // Total investment = entryPrice × quantity
        const totalInvestment = activeBucket.entryPrice * quantity;
        const pnlPercent = activeBucket.unrealizedPnLPct !== undefined
            ? activeBucket.unrealizedPnLPct * 100  // Convert 0.0014 to 0.14%
            : (totalUnrealizedPnl / totalInvestment) * 100;

        // Format with explicit sign and indicator arrow
        const pnlSign = totalUnrealizedPnl >= 0 ? '+' : '-';
        const pnlIndicator = totalUnrealizedPnl >= 0 ? '↑' : '↓';
        const pnlLabel = totalUnrealizedPnl >= 0 ? 'GAIN' : 'LOSS';

        updateElement('posUnrealizedPnl',
            `${pnlIndicator} ${pnlSign}$${Math.abs(totalUnrealizedPnl).toFixed(2)} ${pnlLabel}`,
            totalUnrealizedPnl >= 0 ? 'positive' : 'negative');
        updateElement('posPnlPercent', formatPercent(pnlPercent), pnlPercent >= 0 ? 'positive' : 'negative');

        // Use actual stop loss and target from bot (if available)
        const stopLoss = activeBucket.stopLossPrice || (activeBucket.entryPrice * 0.95); // Fallback to 5%
        const target = activeBucket.profitTargetPrice || (activeBucket.entryPrice * 1.025); // Fallback to 2.5%
        updateElement('posStopLoss', `$${stopLoss.toFixed(2)}`);
        updateElement('posTarget', `$${target.toFixed(2)}`);

        // Update quantity display (if element exists)
        if (document.getElementById('posQuantity')) {
            updateElement('posQuantity', quantity.toFixed(0));
        }
    } else {
        // No active position
        updateElement('positionStatus', 'No Position', 'position-status');
        updateElement('posAvgPrice', '-');
        updateElement('posMarkPrice', '-');
        updateElement('posUnrealizedPnl', '$0.00');
        updateElement('posPnlPercent', '0.00%');
        updateElement('posStopLoss', '-');
        updateElement('posTarget', '-');
        if (document.getElementById('posQuantity')) {
            updateElement('posQuantity', '--');
        }
    }
}

/**
 * Update SMA status
 */
function updateSMAStatus(botData) {
    // Update Bot Signal based on crossover signal from bot
    if (botData.sma_signal) {
        const signal = botData.sma_signal; // "LONG" or "SHORT"
        const signalClass = signal === 'LONG' ? 'positive' : signal === 'SHORT' ? 'negative' : '';
        updateElement('botSignal', signal, signalClass);
    } else if (botData.sma5 && botData.sma20) {
        // Fallback: Calculate signal from SMA values if sma_signal is not available
        const signal = botData.sma5 > botData.sma20 ? 'LONG' : 'SHORT';
        const signalClass = signal === 'LONG' ? 'positive' : 'negative';
        updateElement('botSignal', signal, signalClass);
    } else {
        updateElement('botSignal', '--');
    }

    // Update Market Regime (read from bot_overview data)
    // Regime is stored in bot_overview, not bot_status
    const regime = botData.regime || null;
    if (regime) {
        const regimeEl = document.getElementById('marketRegime');
        if (regimeEl) {
            regimeEl.textContent = regime;  // "BULL", "BEAR", "NEUTRAL"

            // Color-code regime to match Bot Signal styling
            if (regime === 'BULL') {
                regimeEl.className = 'value positive';
            } else if (regime === 'BEAR') {
                regimeEl.className = 'value negative';
            } else {
                regimeEl.className = 'value';
            }
        }
    } else {
        // No regime data available
        const regimeEl = document.getElementById('marketRegime');
        if (regimeEl) {
            regimeEl.textContent = '--';
            regimeEl.className = 'value';
        }
    }
}

/**
 * Update trades data for performance metrics
 * Now uses lightweight /api/bot-metrics endpoint instead of fetching full trade history
 */
async function updateTradesData() {
    try {
        // Use new lightweight metrics endpoint - reduces bandwidth by 99.96%
        const response = await fetch(`/api/bot-metrics/${CONFIG.botId}?days=90`);
        if (!response.ok) throw new Error('Failed to fetch metrics');

        const metrics = await response.json();

        // Update performance metrics display
        updateElement('todayPnl', formatCurrency(metrics.today_pnl), metrics.today_pnl >= 0 ? 'positive' : 'negative');
        updateElement('netPnl', formatCurrency(metrics.net_pnl), metrics.net_pnl >= 0 ? 'positive' : 'negative');
        updateElement('totalTrades', metrics.total_trades);
        updateElement('winRate', `${metrics.win_rate.toFixed(1)}%`);
        updateElement('sharpeRatio', metrics.sharpe_ratio !== null ? metrics.sharpe_ratio.toFixed(2) : '--');
        updateElement('maxDrawdown', formatCurrency(metrics.max_drawdown), metrics.max_drawdown < 0 ? 'negative' : '');

        // Note: Positions table is updated by updateBotOverview() which has real-time position data

    } catch (error) {
        console.error('Error updating metrics:', error);
    }
}

/**
 * Update chart data
 */
async function updateChartData() {
    try {
        // Fetch historical bars from API (increased to 200 for SMA 200 visibility)
        const response = await fetch(CONFIG.apiEndpoints.historicalBars(CONFIG.symbol, 200));
        let bars = await response.json();

        if (!bars || bars.length === 0) {
            console.warn('No historical bar data from API, using synthetic data');
            // Generate synthetic data around current price for display
            const currentPrice = window.latestBotData?.currentPrice || 204;
            bars = generateSyntheticBars(currentPrice, 50);
        }

        // Extract data for chart including SMA values from bars
        const labels = [];
        const prices = [];
        const sma5 = [];
        const sma20 = [];
        const sma200 = [];  // SMA200 for regime detection

        bars.forEach(bar => {
            // Format timestamp for display
            const date = new Date(bar.timestamp);
            const timeStr = date.toLocaleTimeString('en-US', {
                hour: '2-digit',
                minute: '2-digit',
                hour12: false
            });
            labels.push(timeStr);
            prices.push(bar.close);
            // SMA values are pre-calculated by the bot's trading strategy on 1-hour bars
            // and stored in Firestore. Dashboard does NOT recalculate - it displays the
            // EXACT SMA values that the bot uses for trading decisions.
            sma5.push(bar.sma5 || null);
            sma20.push(bar.sma20 || null);
            sma200.push(bar.sma200 || null);  // SMA200 added for regime-based entry logic
        });

        // Add empty data points to push current price to 98% of chart width
        // Calculate how many empty points to add (2% of total displayed points)
        const emptyPointsCount = Math.ceil(labels.length * 0.0204); // 2/98 = 0.0204
        for (let i = 0; i < emptyPointsCount; i++) {
            labels.push(''); // Empty label for future time slots
            prices.push(null); // Null to not draw line
            sma5.push(null); // Extend SMA5 with nulls
            sma20.push(null); // Extend SMA20 with nulls
            sma200.push(null); // Extend SMA200 with nulls
        }

        if (priceChart) {
            // Store current zoom/pan state if needed
            const wasZoomed = priceChart.options.scales.y.min !== undefined;

            // Update chart data
            priceChart.data.labels = labels;
            priceChart.data.datasets[0].data = prices;
            priceChart.data.datasets[1].data = sma5;
            priceChart.data.datasets[2].data = sma20;
            priceChart.data.datasets[3].data = sma200;  // SMA200 dataset

            // Calculate Y-axis range from actual price data ONLY (not including padding nulls)
            // This ensures scale stays consistent when toggling indicators
            let minPrice = Infinity;
            let maxPrice = -Infinity;

            // Only check the original data before we added empty padding points
            const actualPrices = prices.slice(0, prices.length - emptyPointsCount);
            const actualSma5 = sma5.slice(0, prices.length - emptyPointsCount);
            const actualSma20 = sma20.slice(0, prices.length - emptyPointsCount);
            const actualSma200 = sma200.slice(0, prices.length - emptyPointsCount);

            // Check all four datasets (only actual data, not nulls)
            [actualPrices, actualSma5, actualSma20, actualSma200].forEach(dataset => {
                dataset.forEach(value => {
                    if (value !== null && value !== undefined && !isNaN(value)) {
                        minPrice = Math.min(minPrice, value);
                        maxPrice = Math.max(maxPrice, value);
                    }
                });
            });

            // Set explicit Y-axis bounds with 2% padding
            if (minPrice !== Infinity && maxPrice !== -Infinity) {
                const range = maxPrice - minPrice;
                const padding = range * 0.02;
                priceChart.options.scales.y.min = minPrice - padding;
                priceChart.options.scales.y.max = maxPrice + padding;

                console.log(`Y-axis locked: $${(minPrice - padding).toFixed(2)} to $${(maxPrice + padding).toFixed(2)}`);
            }

            // Update chart with animation only on first load
            const updateMode = wasZoomed ? 'none' : 'active';
            priceChart.update(updateMode);

            console.log(`Chart updated: ${prices.length} bars (${emptyPointsCount} padding), Y-axis: $${minPrice.toFixed(2)}-$${maxPrice.toFixed(2)}, SMA5: ${sma5.filter(v => v !== null).length} points, SMA20: ${sma20.filter(v => v !== null).length} points`);
        }

    } catch (error) {
        Logger.error('BotFocus', 'Failed to update chart data', error);
        console.error('Error updating chart data:', error);
    }
}

/**
 * Generate synthetic bar data for chart display when API is unavailable
 */
function generateSyntheticBars(currentPrice, count) {
    const bars = [];
    const now = new Date();

    for (let i = count - 1; i >= 0; i--) {
        const timestamp = new Date(now.getTime() - (i * 5 * 60 * 1000)); // 5 min bars
        const variance = (Math.random() - 0.5) * 2; // Random walk
        const price = currentPrice + variance;

        bars.push({
            timestamp: timestamp.toISOString(),
            open: price - 0.2,
            high: price + 0.5,
            low: price - 0.5,
            close: price,
            volume: Math.floor(Math.random() * 500000) + 100000
        });
    }

    return bars;
}

/**
 * Calculate Sharpe Ratio (simplified - assumes daily returns)
 */
function calculateSharpeRatio(trades) {
    if (!trades || trades.length < 2) return null;

    // Calculate returns
    const returns = trades.map(t => t.profitLoss || 0);

    // Calculate average return
    const avgReturn = returns.reduce((sum, r) => sum + r, 0) / returns.length;

    // Calculate standard deviation
    const variance = returns.reduce((sum, r) => sum + Math.pow(r - avgReturn, 2), 0) / returns.length;
    const stdDev = Math.sqrt(variance);

    if (stdDev === 0) return null;

    // Sharpe ratio (simplified, without risk-free rate)
    return avgReturn / stdDev;
}

/**
 * Calculate Max Drawdown
 */
function calculateMaxDrawdown(trades) {
    if (!trades || trades.length === 0) return null;

    // Sort trades by timestamp
    const sortedTrades = [...trades].sort((a, b) => {
        return new Date(a.timestamp) - new Date(b.timestamp);
    });

    // Calculate cumulative P&L
    let cumulativePnL = 0;
    let peak = 0;
    let maxDrawdown = 0;

    sortedTrades.forEach(trade => {
        cumulativePnL += trade.profitLoss || 0;

        // Update peak if we've reached a new high
        if (cumulativePnL > peak) {
            peak = cumulativePnL;
        }

        // Calculate drawdown from peak
        const drawdown = cumulativePnL - peak;

        // Update max drawdown if this is worse
        if (drawdown < maxDrawdown) {
            maxDrawdown = drawdown;
        }
    });

    return maxDrawdown;
}

/**
 * Update trade history table (v5.0.0 - Added trading mode filtering)
 *
 * Fetches all trades and filters by current profile mode (paper/live).
 * This ensures users only see trades matching their current trading mode,
 * preventing confusion between simulated and real money trades.
 *
 * Flow:
 * 1. Get current profile (paper or live) from cache or backend
 * 2. Fetch all trades from Firestore
 * 3. Filter trades where trading_mode matches current profile
 * 4. Display last 25 filtered trades with MODE badge
 * 5. Update trade count to show which mode is displayed
 */
async function updateTradeHistory() {
    try {
        // v5.0.0: Get current profile with caching (reduces API calls)
        const currentProfile = await getCurrentProfile();

        // Fetch all trades (no limit) then filter for current profile
        console.log('[TRADE HISTORY] Fetching all trades...');
        const response = await fetch('/api/trade-history/all');
        const data = await response.json();

        const tbody = document.getElementById('tradeHistoryBody');
        if (!tbody) {
            console.error('Trade history table body not found');
            return;
        }

        // Extract trades array
        const trades = Array.isArray(data) ? data : (data.trades || []);
        console.log(`[TRADE HISTORY] Total trades fetched: ${trades.length}`);

        // v5.0.0: Filter trades by current profile mode (paper vs live)
        // Trades without trading_mode field default to "paper" for backward compatibility
        const filteredTrades = trades.filter(trade => {
            const tradeMode = trade.trading_mode || 'paper';
            return tradeMode === currentProfile;
        });
        console.log(`[TRADE HISTORY] Filtered to ${filteredTrades.length} ${currentProfile.toUpperCase()} trades`);

        // Display filtered trades (last 25)
        const allTrades = filteredTrades.slice(0, 25);

        console.log(`[TRADE HISTORY] Loaded ${allTrades.length} ${currentProfile.toUpperCase()} trades (${trades.length} total)`);

        // Cache trades for modal performance optimization
        window._cachedTradeHistory = allTrades;

        // Update count display with filter info
        const countEl = document.getElementById('tradeCount');
        if (countEl) {
            countEl.textContent = `Last ${allTrades.length} ${currentProfile.toUpperCase()} trades`;
        }

        if (allTrades.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="15" style="text-align: center; padding: 20px; color: #a0aec0;">
                        No ${currentProfile} trades found
                    </td>
                </tr>
            `;
            return;
        }

        // Build table rows with option columns (Phase 4 - Unified Dashboard Update)
        tbody.innerHTML = allTrades.map(trade => {
            const seqNum = trade.sequence_display || trade.seqNum || trade.sequence_number || '?';
            const symbol = trade.symbol || trade.analysis_metadata?.symbol || CONFIG.symbol || 'N/A';
            const entryPrice = trade.entryPrice || 0;
            const exitPrice = trade.exitPrice || 0;
            const side = (trade.analysis_metadata?.side || trade.bucketType || 'unknown').toUpperCase();
            const quantity = trade.analysis_metadata?.quantity || trade.quantity || 0;
            const reason = formatExitReason(trade.analysis_metadata?.exit_reason || trade.reason || 'unknown');

            // Trading mode badge
            const mode = trade.trading_mode || 'paper';
            const modeBadge = `<span class="mode-badge mode-${mode}">${mode.toUpperCase()}</span>`;

            // Extract option data (with backward compatibility)
            const stockPnl = trade.stock_pnl !== undefined ? trade.stock_pnl : trade.profitLoss;
            const optionPnl = trade.option_pnl || 0;
            const totalPnl = trade.profitLoss || 0;
            const optionEventCount = trade.option_event_count || 0;

            // Format P&L values
            const stockPnlClass = stockPnl >= 0 ? 'positive' : 'negative';
            const stockPnlSign = stockPnl >= 0 ? '+' : '';
            const optionPnlClass = optionPnl >= 0 ? 'positive' : 'negative';
            const optionPnlSign = optionPnl >= 0 ? '+' : '';
            const totalPnlClass = totalPnl >= 0 ? 'positive' : 'negative';
            const totalPnlSign = totalPnl >= 0 ? '+' : '';

            // Format option badge
            const optionBadge = optionEventCount > 0
                ? `<span class="option-badge" title="${optionEventCount} option events" onclick="showTradeDetails('${trade.trade_id || ''}', '${trade.entryOrderId}')">[${optionEventCount} 🔗]</span>`
                : '<span class="option-badge-empty">[-]</span>';

            // Format entry and exit times
            const entryTime = formatTradeTimestamp(trade.entryTime || trade.analysis_metadata?.entry_time);
            const exitTime = formatTradeTimestamp(trade.exitTime || trade.analysis_metadata?.exit_time);

            return `
                <tr>
                    <td style="text-align: center; font-weight: 500;">${seqNum}</td>
                    <td style="text-align: center;">${modeBadge}</td>
                    <td style="font-weight: 600; color: #4299e1;">${symbol}</td>
                    <td>$${entryPrice.toFixed(2)}</td>
                    <td style="font-size: 0.85em; color: #a0aec0;">${entryTime}</td>
                    <td>$${exitPrice.toFixed(2)}</td>
                    <td style="font-size: 0.85em; color: #a0aec0;">${exitTime}</td>
                    <td class="${stockPnlClass}"><strong>${stockPnlSign}$${Math.abs(stockPnl).toFixed(2)}</strong></td>
                    <td class="${optionPnlClass}">${optionPnl !== 0 ? `${optionPnlSign}$${Math.abs(optionPnl).toFixed(2)}` : '-'}</td>
                    <td class="${totalPnlClass}"><strong>${totalPnlSign}$${Math.abs(totalPnl).toFixed(2)}</strong></td>
                    <td class="option-badge-cell">${optionBadge}</td>
                    <td><span class="side-badge ${side.toLowerCase()}">${side}</span></td>
                    <td>${quantity}</td>
                    <td style="font-size: 0.9em;">${reason}</td>
                    <td><button class="view-details-btn" onclick="showTradeDetails('${trade.trade_id || ''}', '${trade.entryOrderId}')">View</button></td>
                </tr>
            `;
        }).join('');

    } catch (error) {
        console.error('Error updating trade history:', error);
        const tbody = document.getElementById('tradeHistoryBody');
        if (tbody) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="15" style="text-align: center; padding: 20px; color: #f56565;">
                        ⚠️ Error loading trades: ${error.message}
                    </td>
                </tr>
            `;
        }
    }
}

/**
 * Format exit reason for display
 */
function formatExitReason(reason) {
    const reasonMap = {
        'profit_target': 'Profit Target',
        'stop_loss': 'Stop Loss',
        'trailing_stop': 'Trailing Stop',
        'sma_reversal': 'SMA Reversal',
        'market_close': 'Market Close',
        'manual': 'Manual Exit',
        'unknown': 'Unknown'
    };
    return reasonMap[reason] || reason.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
}

/**
 * Format trade timestamp for display
 */
function formatTradeTimestamp(timestamp) {
    if (!timestamp) return '--';

    try {
        const date = new Date(timestamp);

        // Always show date and time for all trades (not just older ones)
        // This ensures clarity even when viewing positions closed earlier today
        return date.toLocaleString('en-US', {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false,
            timeZone: 'America/New_York'  // Force Eastern Time to match trading hours
        });
    } catch (e) {
        return timestamp.substring(0, 16).replace('T', ' ');
    }
}

/**
 * Update logs data
 */
async function updateLogsData() {
    try {
        const response = await fetch(CONFIG.apiEndpoints.logs(CONFIG.botId));
        const data = await response.json();

        const logsContainer = document.getElementById('logsContainer');
        if (!logsContainer) return;

        // Ensure logs is an array
        const logs = Array.isArray(data) ? data : (data.logs && Array.isArray(data.logs) ? data.logs : []);

        // Check if data has changed to avoid flickering
        const newContent = JSON.stringify(logs.slice(-20));
        if (logsContainer.dataset.lastContent === newContent) {
            return; // No changes, don't update
        }
        logsContainer.dataset.lastContent = newContent;

        // Get current active filter before updating
        const activeFilterBtn = document.querySelector('.filter-btn.active');
        const activeFilter = activeFilterBtn ? activeFilterBtn.textContent.trim().toLowerCase() : 'all';

        // Clear and repopulate
        logsContainer.innerHTML = '';

        if (logs.length === 0) {
            logsContainer.innerHTML = '<div class="log-entry" style="text-align: center; color: #a0aec0;">No logs available</div>';
            return;
        }

        logs.slice(-20).reverse().forEach(log => {
            const logEntry = document.createElement('div');
            logEntry.className = 'log-entry';

            // Strategy-based filtering: hide logs with incompatible strategy tags
            const logMessage = log.message || '';
            const activeStrategy = window.activeStrategy || 'momentum'; // default to momentum

            // Check if log has a strategy tag
            const hasSMATag = logMessage.includes('[SMA]');
            const hasMomentumTag = logMessage.includes('[MOMENTUM]');

            // Hide SMA logs when using momentum strategy, and vice versa
            let shouldHideByStrategy = false;
            if (activeStrategy === 'momentum' && hasSMATag) {
                shouldHideByStrategy = true;
            } else if (activeStrategy === 'sma_crossover' && hasMomentumTag) {
                shouldHideByStrategy = true;
            }

            // Apply severity filter immediately when creating the entry
            const severity = (log.severity || 'Info').toLowerCase();
            const shouldShow = activeFilter === 'all' ||
                              (activeFilter === 'warn' && severity === 'warning') ||
                              (activeFilter === severity);

            if (!shouldShow || shouldHideByStrategy) {
                logEntry.style.display = 'none';
            }

            logEntry.innerHTML = `
                <span class="log-time">${log.timestamp || '--:--:--'}</span>
                <span class="log-severity ${log.severity?.toLowerCase() || 'info'}">${log.severity || 'Info'}</span>
                <span class="log-component">${log.component || 'System'}</span>
                <span class="log-event">${log.event || 'Event'}</span>
                <span class="log-message">${log.message || ''}</span>
            `;
            logsContainer.appendChild(logEntry);
        });

    } catch (error) {
        console.error('Error updating logs:', error);
    }
}

/**
 * Helper function to get the first active bucket from bot data
 */
function getActiveBucket(botData) {
    if (!botData) return null;

    // Check buckets 1-10 for first active one
    for (let i = 1; i <= 10; i++) {
        const bucket = botData[`bucket${i}`];
        if (bucket && bucket.entryPrice && bucket.quantity) {
            return bucket;
        }
    }
    return null;
}

/**
 * Update merged position and orders table
 * Combines position info with associated orders (stop loss, profit target, covered call)
 */
function updatePositionOrdersTable(botData) {
    const tbody = document.getElementById('positionOrdersTableBody');
    const statusEl = document.getElementById('positionOrdersStatus');

    if (!tbody) {
        console.error('Position/Orders table body not found');
        return;
    }

    // Get active bucket from bot data
    const activeBucket = getActiveBucket(botData);

    // FORCE UPDATE: Clear cache to fix stuck state
    if (!activeBucket || !activeBucket.quantity) {
        tbody.dataset.lastContent = ''; // Clear cache for empty state
    }

    // Round values to avoid floating point precision issues in hash
    const roundPrice = (val) => val ? Math.round(val * 100) / 100 : null;
    const roundPnL = (val) => val ? Math.round(val * 100) / 100 : null;

    if (!activeBucket || !activeBucket.quantity) {
        // No active position - check if already showing empty state
        const emptyStateHash = JSON.stringify({ hasPosition: false });
        if (tbody.dataset.lastContent === emptyStateHash) {
            return; // Already showing empty state
        }

        tbody.dataset.lastContent = emptyStateHash;
        tbody.innerHTML = `
            <tr>
                <td colspan="8" style="text-align: center; padding: 20px; color: #a0aec0;">
                    No active position
                </td>
            </tr>
        `;
        if (statusEl) statusEl.textContent = 'No active position';
        return;
    }

    // Calculate display values
    const symbol = CONFIG.symbol || 'N/A';
    const quantity = activeBucket.quantity || 100;
    const avgPrice = activeBucket.entryPrice || 0;
    const markPrice = activeBucket.currentPrice || botData.currentPrice || avgPrice;

    // Calculate unrealized P&L from stock position
    const stockUnrealizedPnL = activeBucket.unrealizedPnL !== undefined
        ? activeBucket.unrealizedPnL
        : ((markPrice - avgPrice) * quantity);

    // Add covered call gains/losses
    let coveredCallGain = 0;
    if (activeBucket.optionSold && activeBucket.optionPremium) {
        const premiumReceived = activeBucket.optionPremium;
        if (activeBucket.optionCurrentValue !== undefined) {
            coveredCallGain = premiumReceived - activeBucket.optionCurrentValue;
        } else {
            coveredCallGain = premiumReceived;
        }
    }

    // Total unrealized P&L
    const unrealizedPnL = stockUnrealizedPnL + coveredCallGain;
    const pnlPercent = (unrealizedPnL / (avgPrice * quantity)) * 100;

    // Orders info
    const stopLoss = activeBucket.stopLossPrice || null;
    const target = activeBucket.profitTargetPrice || null;

    // Covered call info
    let coveredCallDisplay = '--';
    if (activeBucket.optionSold && activeBucket.optionStrike && activeBucket.optionExpiration) {
        const expiration = new Date(activeBucket.optionExpiration).toLocaleDateString('en-US', {
            month: 'short',
            day: 'numeric'
        });
        const premium = activeBucket.optionPremium || 0;
        coveredCallDisplay = `${expiration} $${activeBucket.optionStrike}C<br><small style="color: #48bb78;">+$${premium.toFixed(2)}</small>`;
    }

    const strategyTag = 'SMA Crossover';

    // Hash the display values for change detection
    const newContent = JSON.stringify({
        hasPosition: true,
        quantity: quantity,
        entryPrice: roundPrice(avgPrice),
        markPrice: roundPrice(markPrice),
        unrealizedPnL: roundPnL(unrealizedPnL),
        pnlPercent: roundPrice(pnlPercent),
        stopLoss: roundPrice(stopLoss),
        target: roundPrice(target),
        optionSold: activeBucket.optionSold,
        optionStrike: roundPrice(activeBucket.optionStrike),
        optionPremium: roundPrice(activeBucket.optionPremium)
    });

    if (tbody.dataset.lastContent === newContent) {
        return; // No changes, don't update
    }

    tbody.dataset.lastContent = newContent;

    const pnlClass = unrealizedPnL >= 0 ? 'positive' : 'negative';
    const pnlSign = unrealizedPnL >= 0 ? '+' : '';
    const pnlIndicator = unrealizedPnL >= 0 ? '↑' : '↓';

    // Generate unique position ID
    const positionId = `${botData.botId || 1}-${symbol}-${activeBucket.group || 'unknown'}-${activeBucket.bucket_id || 0}`;

    // Build the merged row
    tbody.innerHTML = `
        <tr class="position-row" data-position-id="${positionId}">
            <td style="text-align: center;">
                <input type="radio" name="selectedPosition" value="${positionId}" class="position-selector">
            </td>
            <td><strong>${symbol}</strong></td>
            <td>
                <div style="font-weight: 600;">${quantity} shares</div>
                <div style="font-size: 0.85em; color: #a0aec0;">
                    Entry: $${avgPrice.toFixed(2)} | Mark: $${markPrice.toFixed(2)}
                </div>
            </td>
            <td>
                <div class="${pnlClass}" style="font-weight: 700; font-size: 1.05em;">
                    ${pnlIndicator} ${pnlSign}$${Math.abs(unrealizedPnL).toFixed(2)}
                </div>
                <div class="${pnlClass}" style="font-size: 0.85em;">
                    ${pnlSign}${pnlPercent.toFixed(2)}%
                </div>
            </td>
            <td>
                ${stopLoss ? `<span class="order-badge stop">$${stopLoss.toFixed(2)}</span>` : '<span style="color: #718096;">--</span>'}
            </td>
            <td>
                ${target ? `<span class="order-badge target">$${target.toFixed(2)}</span>` : '<span style="color: #718096;">--</span>'}
            </td>
            <td style="font-size: 0.9em;">
                ${coveredCallDisplay}
            </td>
            <td><span class="strategy-tag">${strategyTag}</span></td>
        </tr>
    `;

    // Update status
    if (statusEl) {
        const side = (activeBucket.side || 'long').toUpperCase();
        statusEl.textContent = `${side} - 1 position active`;
    }

    // Add event listener for position selection
    const radioButtons = tbody.querySelectorAll('.position-selector');
    radioButtons.forEach(radio => {
        radio.addEventListener('change', (e) => {
            const positionId = e.target.value;
            console.log('Position selected:', positionId);
            // Enable/disable close position button based on selection
            const closeBtn = document.getElementById('closePositionBtn');
            if (closeBtn) {
                closeBtn.disabled = !e.target.checked;
            }
        });
    });
}

/**
 * Update positions table (DEPRECATED - replaced by updatePositionOrdersTable)
 */
function updatePositionsTable(botData) {
    // Redirect to merged table
    updatePositionOrdersTable(botData);
}

/**
 * Update orders table (DEPRECATED - replaced by updatePositionOrdersTable)
 */
function updateOrdersTable() {
    // Orders are now shown in the merged position/orders table
    // This function is kept for compatibility but does nothing
    console.log('[DEPRECATED] updateOrdersTable called - now handled by updatePositionOrdersTable');
}

/**
 * Helper function to format time since (seconds ago)
 */
function formatTimeSince(seconds) {
    if (seconds < 60) {
        return `${Math.floor(seconds)}s ago`;
    } else if (seconds < 3600) {
        return `${Math.floor(seconds / 60)}m ago`;
    } else if (seconds < 86400) {
        return `${Math.floor(seconds / 3600)}h ago`;
    } else {
        return `${Math.floor(seconds / 86400)}d ago`;
    }
}

// ============================================================================
// TRADE DETAILS MODAL (Phase 4 - Unified Dashboard Update)
// ============================================================================

/**
 * Show trade details modal with option events timeline
 */
async function showTradeDetails(tradeId, entryOrderId) {
    // Parse entryOrderId to integer (passed as string from HTML onclick)
    const parsedEntryOrderId = parseInt(entryOrderId, 10);

    if (isNaN(parsedEntryOrderId)) {
        console.error('Invalid entry order ID provided:', entryOrderId);
        alert(`⚠️ Invalid entry order ID: ${entryOrderId}\n\nThis is likely a bug. Please check the browser console for details.`);
        return;
    }

    try {
        // Show loading indicator
        document.body.style.cursor = 'wait';

        // Optimization: Get trade from cache (already loaded in trade history table)
        // This avoids re-fetching all 25 trades just to find one
        let trade = null;
        const cachedTrades = window._cachedTradeHistory || [];
        trade = cachedTrades.find(t => t.entryOrderId === parsedEntryOrderId);

        // Fetch option events (only fetch what we don't have)
        const optionEventsResponse = await fetch(`/api/option-events/${parsedEntryOrderId}`);

        if (!optionEventsResponse.ok) {
            throw new Error(`API request failed: ${optionEventsResponse.status}`);
        }

        const optionEventsData = await optionEventsResponse.json();

        // If trade not in cache, fall back to API (shouldn't happen)
        if (!trade) {
            console.warn('Trade not in cache, fetching from API...');
            const tradesResponse = await fetch('/api/trade-history/all');
            const tradesData = await tradesResponse.json();
            const trades = tradesData.trades || [];
            trade = trades.find(t => t.entryOrderId === parsedEntryOrderId);
        }

        if (!trade) {
            console.error('Trade not found for entry order ID:', parsedEntryOrderId);
            alert(`⚠️ Trade Not Found\n\nEntry Order ID: ${parsedEntryOrderId}\n\nThis trade may have been deleted or the data is not yet synchronized.`);
            document.body.style.cursor = 'default';
            return;
        }

        const optionEvents = optionEventsData.events || [];

        // Compute option P&L from live events (more reliable than trade.option_pnl which may be stale/missing)
        const optionPnlFromEvents = optionEvents.reduce((sum, e) => sum + (e.premium || 0), 0);

        // v5.0.0: Get trading mode for badge display
        const tradingMode = trade.trading_mode || 'paper';
        const modeBadge = `<span class="mode-badge mode-${tradingMode}" style="margin-left: 10px; padding: 4px 10px; border-radius: 4px; font-size: 0.8em;">${tradingMode.toUpperCase()}</span>`;

        // Render modal
        const modalHtml = `
            <div class="trade-modal-overlay" onclick="closeTradeModal()">
                <div class="trade-modal-content" onclick="event.stopPropagation()">
                    <div class="trade-modal-header">
                        <h2>Trade #${trade.sequence_number || '?'} ${modeBadge} - ${(trade.analysis_metadata?.side || trade.bucketType || 'UNKNOWN').toUpperCase()} ${trade.analysis_metadata?.quantity || trade.quantity || 0} shares</h2>
                        <button class="modal-close-btn" onclick="closeTradeModal()">✕</button>
                    </div>

                    <div class="trade-modal-body">
                        <!-- Stock Position Section -->
                        <div class="modal-section">
                            <h3>Stock Position</h3>
                            <div class="trade-details-grid">
                                <div class="detail-row">
                                    <span class="label">Entry:</span>
                                    <span class="value">$${trade.entryPrice?.toFixed(2) || 'N/A'} @ ${formatTime(trade.entryTime || trade.analysis_metadata?.entry_time)} (Order: ${trade.entryOrderId || 'N/A'})</span>
                                </div>
                                <div class="detail-row">
                                    <span class="label">Exit:</span>
                                    <span class="value">$${trade.exitPrice?.toFixed(2) || 'N/A'} @ ${formatTime(trade.exitTime || trade.analysis_metadata?.exit_time)} (Order: ${trade.exitOrderId || 'N/A'})</span>
                                </div>
                                <div class="detail-row">
                                    <span class="label">Stock P&L:</span>
                                    <span class="value ${(trade.stock_pnl || 0) >= 0 ? 'profit' : 'loss'}">
                                        ${(trade.stock_pnl || 0) >= 0 ? '+' : ''}$${Math.abs(trade.stock_pnl || 0).toFixed(2)}
                                    </span>
                                </div>
                            </div>
                        </div>

                        <!-- Option Events Section -->
                        ${optionEvents.length > 0 ? renderOptionEventsSection(optionEvents) : ''}

                        <!-- Summary Section -->
                        <div class="modal-section modal-summary">
                            <h3>Summary</h3>
                            <div class="summary-grid">
                                <div class="summary-row">
                                    <span class="label">Stock P&L:</span>
                                    <span class="value">${(trade.stock_pnl || 0) >= 0 ? '+' : ''}$${Math.abs(trade.stock_pnl || 0).toFixed(2)}</span>
                                </div>
                                ${optionPnlFromEvents !== 0 ? `
                                    <div class="summary-row">
                                        <span class="label">Option P&L:</span>
                                        <span class="value">${optionPnlFromEvents >= 0 ? '+' : ''}$${Math.abs(optionPnlFromEvents).toFixed(2)} (${getUniqueOptionCount(optionEvents)} ${getUniqueOptionCount(optionEvents) === 1 ? 'call' : 'calls'})</span>
                                    </div>
                                ` : ''}
                                <div class="summary-row total">
                                    <span class="label">Total P&L:</span>
                                    ${(() => { const totalPnl = (trade.stock_pnl || trade.profitLoss || 0) + optionPnlFromEvents; return `<span class="value ${totalPnl >= 0 ? 'profit' : 'loss'}">${totalPnl >= 0 ? '+' : ''}$${Math.abs(totalPnl).toFixed(2)}</span>`; })()}
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;

        document.body.insertAdjacentHTML('beforeend', modalHtml);
        document.body.style.cursor = 'default';

    } catch (error) {
        console.error('Error showing trade details:', error);
        document.body.style.cursor = 'default';
        alert(`❌ Error Loading Trade Details\n\n${error.message}\n\nPlease check:\n• Browser console for detailed error\n• Network tab for failed API calls\n• Dashboard backend logs`);
    }
}

/**
 * Close trade details modal
 */
function closeTradeModal() {
    const modal = document.querySelector('.trade-modal-overlay');
    if (modal) {
        modal.remove();
    }
}

/**
 * Render option events section
 */
function renderOptionEventsSection(events) {
    // Group events by option contract (strike + expiration)
    const groupedEvents = groupOptionEvents(events);

    let html = `
        <div class="modal-section option-events-section">
            <h3>Option Events Timeline</h3>
            <div class="option-events-container">
    `;

    groupedEvents.forEach((group, index) => {
        const callNumber = index + 1;
        const strike = group[0].strike;
        const expiration = formatDate(group[0].expiration);

        // Calculate net P&L for this option
        const netPnl = group.reduce((sum, event) => sum + (event.premium || 0), 0);

        html += `
            <div class="option-group">
                <div class="option-group-header">
                    <span class="option-label">📅 Call #${callNumber} - $${strike.toFixed(2)} strike (exp ${expiration})</span>
                </div>
                <table class="option-events-table">
                    <thead>
                        <tr>
                            <th>Time</th>
                            <th>Action</th>
                            <th>Price</th>
                            <th>Premium</th>
                            <th>Stock Price</th>
                            <th>Order ID</th>
                        </tr>
                    </thead>
                    <tbody>
        `;

        group.forEach(event => {
            const action = getEventAction(event.event_type);
            const premiumClass = (event.premium || 0) >= 0 ? 'profit' : 'loss';

            html += `
                <tr class="option-event-row ${event.event_type}">
                    <td class="time">${formatTime(event.timestamp)}</td>
                    <td class="action">${action.icon} ${action.label}</td>
                    <td class="price">$${(Math.abs(event.premium || 0) / 100).toFixed(2)}</td>
                    <td class="premium ${premiumClass}">
                        ${(event.premium || 0) >= 0 ? '+' : ''}$${(event.premium || 0).toFixed(2)}
                    </td>
                    <td class="stock-price">$${event.stock_price_at_event?.toFixed(2) || 'N/A'}</td>
                    <td class="order-id">${event.option_order_id || 'N/A'}</td>
                </tr>
            `;
        });

        html += `
                    </tbody>
                </table>
                <div class="option-group-footer">
                    <span class="net-label">Net P&L:</span>
                    <span class="net-value ${netPnl >= 0 ? 'profit' : 'loss'}">
                        ${netPnl >= 0 ? '+' : ''}$${netPnl.toFixed(2)}
                    </span>
                </div>
            </div>
        `;
    });

    html += `
            </div>
        </div>
    `;

    return html;
}

/**
 * Group option events by contract (strike + expiration)
 */
function groupOptionEvents(events) {
    const groups = {};

    events.forEach(event => {
        const key = `${event.strike}_${event.expiration}`;
        if (!groups[key]) {
            groups[key] = [];
        }
        groups[key].push(event);
    });

    // Sort each group by timestamp
    Object.values(groups).forEach(group => {
        group.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
    });

    return Object.values(groups);
}

/**
 * Get display info for event type
 */
function getEventAction(eventType) {
    const actions = {
        'sell': { icon: '📈', label: 'SELL' },
        'buyback': { icon: '📉', label: 'BUY' },
        'option_assignment': { icon: '📌', label: 'ASSIGNED' },
        'option_expiration': { icon: '⏰', label: 'EXPIRED' }
    };
    return actions[eventType] || { icon: '?', label: eventType.toUpperCase() };
}

/**
 * Count unique option contracts (by strike + expiration)
 */
function getUniqueOptionCount(events) {
    if (!events || events.length === 0) return 0;
    const unique = new Set();
    events.forEach(event => {
        const key = `${event.strike}_${event.expiration}`;
        unique.add(key);
    });
    return unique.size;
}

/**
 * Format time (HH:MM AM/PM)
 */
function formatTime(timestamp) {
    if (!timestamp) return 'N/A';
    const date = new Date(timestamp);
    return date.toLocaleTimeString('en-US', {
        hour: 'numeric',
        minute: '2-digit',
        hour12: true
    });
}

/**
 * Format date (MM/DD from YYYYMMDD)
 */
function formatDate(dateString) {
    if (!dateString) return 'N/A';
    // Format: YYYYMMDD → MM/DD
    const month = dateString.substring(4, 6);
    const day = dateString.substring(6, 8);
    return `${month}/${day}`;
}

/**
 * Cleanup on page unload
 */
window.addEventListener('beforeunload', () => {
    if (updateTimer) {
        clearInterval(updateTimer);
    }
});

// Orders table will be initialized by periodic updates (no need for setTimeout)
