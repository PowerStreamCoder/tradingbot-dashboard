/**
 * StockPicker Tab - Display top 5 daily picks
 * Integrated with existing dashboard patterns
 *
 * Features:
 * - On-demand execution with "Run Now" button
 * - Auto-refresh every 60 seconds
 * - Toast notifications instead of blocking alerts
 * - Loading states for better UX
 * - Error handling with retry suggestions
 */

// =============================================================================
// STATE MANAGEMENT
// =============================================================================

let stockPickerRefreshInterval = null;
let isRunningStockPicker = false;
let lastTabSwitchTime = 0;  // For debouncing tab switches


// =============================================================================
// TOAST NOTIFICATIONS (Better UX than alert())
// =============================================================================

/**
 * Show a toast notification (non-blocking, auto-dismisses after 5 seconds).
 *
 * @param {string} message - Message to display
 * @param {string} type - 'info', 'success', 'error', or 'warning'
 */
function showToast(message, type = 'info') {
    // Create toast element
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 15px 20px;
        background: ${type === 'success' ? '#27ae60' : type === 'error' ? '#e74c3c' : type === 'warning' ? '#f39c12' : '#3498db'};
        color: white;
        border-radius: 5px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
        z-index: 10000;
        max-width: 400px;
        animation: slideIn 0.3s ease-out;
        font-size: 14px;
        line-height: 1.5;
    `;
    toast.textContent = message;

    // Add to page
    document.body.appendChild(toast);

    // Auto-remove after 5 seconds
    setTimeout(() => {
        toast.style.animation = 'slideOut 0.3s ease-in';
        setTimeout(() => toast.remove(), 300);
    }, 5000);
}

// Add CSS animation for toasts (inject once)
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
    `;
    document.head.appendChild(style);
}


// =============================================================================
// MANUAL TRIGGER (Run Now Button)
// =============================================================================

/**
 * Trigger manual StockPicker run via POST /api/stock-picks/run.
 *
 * Features:
 * - Prevents concurrent runs (button disabled while running)
 * - Shows progress toast (non-blocking)
 * - Displays results or errors
 * - Auto-refreshes table on success
 */
async function runStockPickerNow() {
    // Prevent concurrent runs (check local flag)
    if (isRunningStockPicker) {
        showToast('StockPicker is already running. Please wait...', 'warning');
        return;
    }

    // Disable button and update UI
    const runButton = document.getElementById('run-stockpicker-btn');
    if (runButton) {
        runButton.disabled = true;
        runButton.innerHTML = '⏳ Running...';
    }

    isRunningStockPicker = true;

    // Show progress toast (non-blocking, unlike alert())
    showToast('🔄 StockPicker running... This may take 30-90 seconds.', 'info');

    try {
        const response = await fetch('/api/stock-picks/run', {
            method: 'POST'
        });

        // Handle HTTP errors
        if (!response.ok) {
            if (response.status === 429) {
                throw new Error('Rate limit exceeded: 10 runs per hour. Please wait before trying again.');
            } else if (response.status === 409) {
                throw new Error('StockPicker is already running in another session. Please wait.');
            } else {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
        }

        const result = await response.json();

        // Handle successful response
        if (result.status === 'success') {
            const pickCount = result.pick_count || 0;
            const duration = result.duration_seconds || 0;

            showToast(
                `✅ Success! Generated ${pickCount} picks in ${duration}s`,
                'success'
            );

            // Reload picks immediately to show new results
            loadStockPicks();
        } else {
            // Error response (status !== 'success')
            showToast(`❌ ${result.message || 'StockPicker run failed'}`, 'error');
        }

    } catch (error) {
        console.error('Failed to run StockPicker:', error);
        showToast(`❌ Failed to run StockPicker: ${error.message}`, 'error');
    } finally {
        // Always re-enable button and reset flag
        isRunningStockPicker = false;
        if (runButton) {
            runButton.disabled = false;
            runButton.innerHTML = '▶️ Run Now';
        }
    }
}


// =============================================================================
// DATA FETCHING (GET /api/stock-picks)
// =============================================================================

/**
 * Load and display stock picks from API.
 *
 * Features:
 * - Shows loading state while fetching
 * - Updates metrics cards (pick count, avg explosiveness, last run time)
 * - Populates table with picks
 * - Handles empty states and errors gracefully
 */
async function loadStockPicks() {
    const tbody = document.getElementById('stock-picks-tbody');

    // Show loading state
    if (tbody) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" style="text-align:center; padding: 20px; color: #95a5a6;">
                    ⏳ Loading picks...
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

        // Check if data is stale or represents an error state
        const isStaleOrError = (
            !data.picks ||
            data.picks.length === 0 ||
            data.status === 'ranking_failed' ||
            data.status === 'no_news' ||
            data.status === 'error' ||
            data.error
        );

        if (isStaleOrError) {
            // Show clean "Press Run Now" message instead of stale error messages
            document.getElementById('sp-pick-count').textContent = '-';
            document.getElementById('sp-avg-explosive').textContent = '-';
            document.getElementById('sp-last-run').textContent = 'Not run yet';

            if (tbody) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="8" style="text-align:center; padding: 40px; color: #95a5a6;">
                            <div style="font-size: 1.1em; margin-bottom: 10px;">
                                📊 No stock picks available
                            </div>
                            <div style="font-size: 1em; color: #3498db;">
                                Press <strong>"▶️ Run Now"</strong> button to retrieve fresh picks
                            </div>
                        </td>
                    </tr>
                `;
            }
            return;
        }

        // Data is fresh - update metrics and table normally
        updateStockPickerMetrics(data);
        updateStockPickerTable(data);

    } catch (error) {
        console.error('Failed to load stock picks:', error);

        // Network/API error - show error state
        document.getElementById('sp-pick-count').textContent = 'Error';
        document.getElementById('sp-avg-explosive').textContent = '-';
        document.getElementById('sp-last-run').textContent = 'Error';

        if (tbody) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="8" style="text-align:center; color: #e74c3c; padding: 20px;">
                        <strong>Failed to load stock picks</strong><br>
                        <span style="font-size: 0.9em;">${error.message}</span><br>
                        <span style="font-size: 0.85em; color: #95a5a6;">Try refreshing the page</span>
                    </td>
                </tr>
            `;
        }
    }
}


// =============================================================================
// UI UPDATES
// =============================================================================

/**
 * Update metrics cards with pick data.
 *
 * Updates:
 * - Active pick count
 * - Average explosiveness score
 * - Last run timestamp (formatted as "Aug 12, 2:30 PM")
 * - Data sources used (if element exists)
 *
 * @param {Object} data - Stock picks data from API
 */
function updateStockPickerMetrics(data) {
    // Pick count
    const pickCount = data.pick_count || 0;
    const pickCountEl = document.getElementById('sp-pick-count');
    if (pickCountEl) {
        pickCountEl.textContent = pickCount;
    }

    // Average explosiveness (formatted to 1 decimal place)
    const avgExplosive = data.avg_explosiveness || 0;
    const avgExplosiveEl = document.getElementById('sp-avg-explosive');
    if (avgExplosiveEl) {
        avgExplosiveEl.textContent = avgExplosive.toFixed(1);
    }

    // Last run timestamp (formatted as human-readable)
    let lastRunText = 'Never';
    if (data.run_timestamp) {
        try {
            const runDate = new Date(data.run_timestamp);
            lastRunText = runDate.toLocaleString('en-US', {
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
            });
        } catch (e) {
            // If date parsing fails, use raw timestamp
            lastRunText = data.run_timestamp;
        }
    }
    const lastRunEl = document.getElementById('sp-last-run');
    if (lastRunEl) {
        lastRunEl.textContent = lastRunText;
    }

    // Update sources indicator (optional element)
    const sourcesEl = document.getElementById('sp-sources-used');
    if (sourcesEl && data.sources_used) {
        sourcesEl.textContent = data.sources_used.join(', ');
    }
}


/**
 * Update the stock picks table with data.
 *
 * Features:
 * - Displays 8 columns per pick (rank, industry, ticker, scores, catalyst, metrics)
 * - Color-coded metrics (green for good, red for bad)
 * - Truncates long catalyst text with ellipsis
 * - Shows empty state with clear message if no picks
 *
 * @param {Object} data - Stock picks data from API
 */
function updateStockPickerTable(data) {
    const tbody = document.getElementById('stock-picks-tbody');
    if (!tbody) return;

    // Handle empty state (no picks available)
    if (!data.picks || data.picks.length === 0) {
        const message = data.message || 'No picks available yet';
        const suggestion = data.status === 'empty'
            ? '<br><span style="font-size: 0.9em; color: #3498db;">Click "▶️ Run Now" to generate picks</span>'
            : '';

        tbody.innerHTML = `
            <tr>
                <td colspan="8" style="text-align:center; padding: 20px; color: #95a5a6;">
                    ${message}${suggestion}
                </td>
            </tr>
        `;
        return;
    }

    // Populate table with picks
    tbody.innerHTML = data.picks.map((pick, index) => {
        const rank = index + 1;
        const ticker = pick.ticker || '-';
        const industry = pick.industry || 'Unknown';
        const composite = pick.composite_score || 0;
        const explosive = pick.explosiveness || 0;
        const fundamental = pick.fundamental_score;
        const catalyst = pick.catalyst || '';

        return `
            <tr>
                <td style="text-align: center; padding: 12px 8px;"><strong style="font-size: 1.1em;">${rank}</strong></td>
                <td style="padding: 12px 8px;"><span class="badge badge-industry">${industry}</span></td>
                <td style="text-align: center; padding: 12px 8px;"><strong style="font-size: 1.1em; color: #3498db;">${ticker}</strong></td>
                <td style="text-align: right; font-weight: 600; color: #2ecc71; padding: 12px 12px;">${composite.toFixed(1)}</td>
                <td style="text-align: right; color: ${explosive > 0 ? '#e67e22' : '#95a5a6'}; padding: 12px 12px;">${explosive.toFixed(1)}</td>
                <td style="text-align: right; color: #3498db; padding: 12px 12px;">${fundamental != null ? fundamental.toFixed(1) : '-'}</td>
                <td class="catalyst-cell" title="${catalyst}">${catalyst}</td>
                <td class="metrics-cell">${formatPickMetrics(pick)}</td>
            </tr>
        `;
    }).join('');
}


/**
 * Format key financial metrics for display.
 *
 * Formats metrics with color coding:
 * - Revenue YoY: Green if positive, red if negative
 * - Gross/Operating Margin: Percentage display
 * - Debt/Equity: Green if < 100, red if > 250, gray otherwise
 * - Current Ratio: Green if >= 1.2, red if < 1.2
 *
 * @param {Object} pick - Pick object with financial metrics
 * @returns {string} HTML string with formatted metrics
 */
function formatPickMetrics(pick) {
    const metrics = [];

    // Revenue YoY (most important metric)
    if (pick.revenue_yoy != null) {
        const revPercent = (pick.revenue_yoy * 100).toFixed(0);
        const revColor = pick.revenue_yoy > 0 ? '#27ae60' : '#e74c3c';
        metrics.push(`<span style="color: ${revColor}">Rev: ${revPercent}%</span>`);
    }

    // Gross Margin
    if (pick.gross_margin != null) {
        const gmPercent = (pick.gross_margin * 100).toFixed(0);
        metrics.push(`GM: ${gmPercent}%`);
    }

    // Operating Margin
    if (pick.operating_margin != null) {
        const omPercent = (pick.operating_margin * 100).toFixed(0);
        metrics.push(`OM: ${omPercent}%`);
    }

    // Debt to Equity (<100 good, >250 bad)
    if (pick.debt_to_equity != null) {
        const de = pick.debt_to_equity.toFixed(0);
        const deColor = pick.debt_to_equity < 100
            ? '#27ae60'  // Green: low debt
            : (pick.debt_to_equity > 250 ? '#e74c3c' : '#95a5a6');  // Red: high debt, Gray: medium
        metrics.push(`<span style="color: ${deColor}">D/E: ${de}</span>`);
    }

    // Current Ratio (>=1.2 good, <1.2 bad)
    if (pick.current_ratio != null) {
        const cr = pick.current_ratio.toFixed(1);
        const crColor = pick.current_ratio >= 1.2 ? '#27ae60' : '#e74c3c';
        metrics.push(`<span style="color: ${crColor}">CR: ${cr}</span>`);
    }

    // Join metrics with separator (or show dash if none available)
    return metrics.length > 0
        ? metrics.join(' <span style="color: #95a5a6;">|</span> ')
        : '-';
}


// =============================================================================
// AUTO-REFRESH
// =============================================================================

/**
 * Start auto-refresh for stock picker tab (60-second interval).
 *
 * Features:
 * - Debouncing to prevent rapid tab switches from creating multiple intervals
 * - Immediate load on start
 * - Safe interval cleanup
 */
function startStockPickerRefresh() {
    // Debounce rapid tab switches (prevent multiple intervals)
    const now = Date.now();
    if (now - lastTabSwitchTime < 1000) {
        return;  // Ignore if last switch was < 1 second ago
    }
    lastTabSwitchTime = now;

    // Clear any existing interval
    if (stockPickerRefreshInterval) {
        clearInterval(stockPickerRefreshInterval);
        stockPickerRefreshInterval = null;
    }

    // Load immediately
    loadStockPicks();

    // Refresh every 60 seconds
    stockPickerRefreshInterval = setInterval(loadStockPicks, 60000);
}


/**
 * Stop auto-refresh when leaving tab.
 *
 * Cleanup function to prevent unnecessary API calls when tab not visible.
 */
function stopStockPickerRefresh() {
    if (stockPickerRefreshInterval) {
        clearInterval(stockPickerRefreshInterval);
        stockPickerRefreshInterval = null;
    }
}


/**
 * Called when Stock Picker tab becomes active.
 * Hook this into your existing tab switching logic.
 */
function onStockPickerTabActive() {
    startStockPickerRefresh();
}


/**
 * Called when Stock Picker tab becomes inactive.
 */
function onStockPickerTabInactive() {
    stopStockPickerRefresh();
}


// =============================================================================
// EXPORTS (Make functions available globally)
// =============================================================================

if (typeof window !== 'undefined') {
    window.loadStockPicks = loadStockPicks;
    window.runStockPickerNow = runStockPickerNow;
    window.startStockPickerRefresh = startStockPickerRefresh;
    window.stopStockPickerRefresh = stopStockPickerRefresh;
    window.onStockPickerTabActive = onStockPickerTabActive;
    window.onStockPickerTabInactive = onStockPickerTabInactive;
}
