/**
 * Dashboard Configuration
 * Centralized API endpoints and constants
 * Version: 1.0.0
 */

// Debug mode (automatically enabled for localhost)
const DEBUG = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';

/**
 * API Endpoints
 * Centralized endpoint configuration to avoid duplication
 */
const API_ENDPOINTS = {
    BOT_CONFIGS: '/api/bot-configs',
    BOT_OVERVIEW: '/api/bot-overview',
    BOT_STATUS: (symbol) => `/api/bot-status/${symbol}`,
    BOT_METRICS: (botId, days = 90) => `/api/bot-metrics/${botId}?days=${days}`,
    HISTORICAL_BARS: (symbol, bars = 200) => `/api/historical-bars/${symbol}?bars=${bars}`,
    SMA_INDICATORS: (symbol) => `/api/sma-indicators/${symbol}`,
    TRADE_HISTORY: '/api/trade-history/all',
    OPTION_EVENTS: (entryOrderId) => `/api/option-events/${entryOrderId}`,
    LOGS: (botId) => `/api/logs?botId=${botId}`,
    BOT_CONTROL: {
        CURRENT_PROFILE: '/api/bot-control/current-profile',
        SWITCH_PROFILE: '/api/bot-control/switch-profile',
        START: '/api/bot-control/restart',
        STOP: '/api/bot-control/stop'
    },
    POSITION: {
        CREATE: '/api/create-position',
        CLOSE: '/api/close-position'
    },
    // Lowercase aliases for backward compatibility
    botOverview: '/api/bot-overview',
    botStatus: (symbol) => `/api/bot-status/${symbol}`,
    botMetrics: (botId, days = 90) => `/api/bot-metrics/${botId}?days=${days}`,
    historicalBars: (symbol, bars = 200) => `/api/historical-bars/${symbol}?bars=${bars}`,
    logs: (botId) => `/api/logs?botId=${botId}`
};

/**
 * Update intervals (milliseconds)
 */
const UPDATE_INTERVALS = {
    BOT_OVERVIEW: 5000,      // Bot position data
    BOT_STATUS: 5000,        // Health metrics
    CHART: 60000,            // Price chart (Alpha Vantage rate limit)
    TABLES: 10000,           // Trade history, positions
    LOGS: 10000,             // Bot logs
    PROFILE_CHECK: 10000     // Profile indicator
};

/**
 * Conditional logging utility
 * Only logs in debug mode or localhost
 */
const Logger = {
    debug: (...args) => {
        if (DEBUG) {
            const timestamp = new Date().toISOString();
            console.log(`[${timestamp}]`, ...args);
        }
    },

    info: (component, message, ...args) => {
        const timestamp = new Date().toISOString();
        console.log(`[${timestamp}] [${component}] ${message}`, ...args);
    },

    warn: (component, message, ...args) => {
        const timestamp = new Date().toISOString();
        console.warn(`[${timestamp}] [${component}] ${message}`, ...args);
    },

    error: (component, message, error) => {
        const timestamp = new Date().toISOString();
        console.error(`[${timestamp}] [${component}] ${message}`, error);

        // Optional: Send to backend for monitoring (production only)
        if (!DEBUG && window.location.hostname !== 'localhost') {
            // Future enhancement: POST to /api/log-error
        }
    }
};

/**
 * Export for use in other modules
 */
if (typeof window !== 'undefined') {
    window.API_ENDPOINTS = API_ENDPOINTS;
    window.UPDATE_INTERVALS = UPDATE_INTERVALS;
    window.Logger = Logger;
    window.DEBUG = DEBUG;
}
