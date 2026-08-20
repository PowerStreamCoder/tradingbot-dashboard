/**
 * Common utility functions for the trading dashboard
 * Shared across all dashboard pages to avoid code duplication
 */

/**
 * Format a price value as currency
 * @param {number} price - The price to format
 * @param {number} decimals - Number of decimal places (default: 2)
 * @returns {string} Formatted price string (e.g., "$123.45")
 */
function formatPrice(price, decimals = 2) {
    if (price === null || price === undefined) return '-';
    return '$' + parseFloat(price).toFixed(decimals);
}

/**
 * Format a currency amount with sign
 * @param {number} amount - The amount to format
 * @returns {string} Formatted currency string with sign (e.g., "+$123.45" or "-$50.00")
 */
function formatCurrency(amount) {
    if (amount === null || amount === undefined) return '$0.00';
    const sign = amount >= 0 ? '+' : '';
    return sign + '$' + Math.abs(amount).toFixed(2);
}

/**
 * Format a timestamp as a human-readable date/time
 * @param {string|Date} timestamp - The timestamp to format
 * @returns {string} Formatted date/time string
 */
function formatTimestamp(timestamp) {
    if (!timestamp) return '-';
    const date = new Date(timestamp);
    return date.toLocaleString('en-US', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

/**
 * Format a percentage value with sign
 * @param {number} value - The percentage value to format
 * @returns {string} Formatted percentage string (e.g., "+5.23%" or "-2.15%")
 */
function formatPercent(value) {
    if (value === null || value === undefined) return '0.00%';
    const sign = value >= 0 ? '+' : '';
    return `${sign}${value.toFixed(2)}%`;
}

/**
 * Update an element's content if it has changed (prevents unnecessary DOM updates)
 * @param {string} id - Element ID
 * @param {string} value - New content value
 * @param {string} className - Optional CSS class to add
 */
function updateElement(id, value, className = null) {
    const element = document.getElementById(id);
    if (!element) return;

    if (element.textContent !== value) {
        element.textContent = value;
    }

    if (className) {
        element.className = className;
    }
}
