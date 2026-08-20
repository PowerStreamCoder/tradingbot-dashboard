/**
 * P&L Calculation utilities for trading dashboard
 * Shared across dashboard.js and pnl-reporting.js
 */

/**
 * Calculate ISO week number for a given date.
 * ISO week starts on Monday; week 1 contains the year's first Thursday.
 * @param {Date} date - Date to calculate week number for
 * @returns {number} ISO week number (1-53)
 */
function getWeekNumber(date) {
    const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
    const dayNum = d.getUTCDay() || 7;
    d.setUTCDate(d.getUTCDate() + 4 - dayNum);
    const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
    return Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
}

/**
 * Calculate P&L for a specific day.
 * @param {Array} trades - All trades from backend
 * @param {Date|string} selectedDate - Date to calculate P&L for
 * @returns {Object} P&L breakdown by bot and total
 */
function calculatePnLForDay(trades, selectedDate) {
    const dayStart = new Date(selectedDate);
    dayStart.setHours(0, 0, 0, 0);
    const dayEnd = new Date(selectedDate);
    dayEnd.setHours(23, 59, 59, 999);

    return calculatePnLForRange(trades, dayStart, dayEnd);
}

/**
 * Calculate P&L for a specific ISO week.
 * Week runs Monday-Sunday based on ISO 8601 standard.
 * @param {Array} trades - All trades from backend
 * @param {string} weekValue - Week in YYYY-W## format (e.g., "2026-W22")
 * @returns {Object} P&L breakdown by bot and total
 */
function calculatePnLForWeek(trades, weekValue) {
    // weekValue format: "2026-W22"
    const [year, week] = weekValue.split('-W').map(Number);

    // Get first day of the week (Monday)
    const jan4 = new Date(year, 0, 4);
    const dayOfWeek = jan4.getDay() || 7;
    const weekStart = new Date(year, 0, 4 - dayOfWeek + 1 + (week - 1) * 7);
    weekStart.setHours(0, 0, 0, 0);

    // Get last day of the week (Sunday)
    const weekEnd = new Date(weekStart);
    weekEnd.setDate(weekStart.getDate() + 6);
    weekEnd.setHours(23, 59, 59, 999);

    return calculatePnLForRange(trades, weekStart, weekEnd);
}

/**
 * Calculate P&L for a specific month.
 * Filters trades where timestamp falls within the selected month (1st - last day).
 * @param {Array} trades - All trades from backend
 * @param {string} monthValue - Month in YYYY-MM format (e.g., "2026-05")
 * @returns {Object} P&L breakdown by bot and total
 */
function calculatePnLForMonth(trades, monthValue) {
    // monthValue format: "2026-05"
    const [year, month] = monthValue.split('-').map(Number);

    const monthStart = new Date(year, month - 1, 1);
    monthStart.setHours(0, 0, 0, 0);

    const monthEnd = new Date(year, month, 0);
    monthEnd.setHours(23, 59, 59, 999);

    return calculatePnLForRange(trades, monthStart, monthEnd);
}

/**
 * Generic P&L calculation for any date range.
 * Used by day/week/month calculators. Sums up profitLoss for all trades in range.
 * @param {Array} trades - All trades from backend
 * @param {Date} startDate - Range start (inclusive)
 * @param {Date} endDate - Range end (inclusive)
 * @returns {Object} {bot1: {trades, pnl}, bot2: {trades, pnl}, total: {trades, pnl}}
 */
function calculatePnLForRange(trades, startDate, endDate) {
    const pnl = {
        bot1: { trades: 0, pnl: 0.0 },
        bot2: { trades: 0, pnl: 0.0 },
        total: { trades: 0, pnl: 0.0 }
    };

    trades.forEach(trade => {
        try {
            const tradeDate = new Date(trade.timestamp);
            if (tradeDate >= startDate && tradeDate <= endDate) {
                const botId = trade.botId || 1;
                const botKey = `bot${botId}`;
                const pnlValue = trade.profitLoss || 0;

                pnl[botKey].trades += 1;
                pnl[botKey].pnl += pnlValue;
                pnl.total.trades += 1;
                pnl.total.pnl += pnlValue;
            }
        } catch (e) {
            console.error('Error processing trade:', e, trade);
        }
    });

    // Round P&L values
    pnl.bot1.pnl = Math.round(pnl.bot1.pnl * 100) / 100;
    pnl.bot2.pnl = Math.round(pnl.bot2.pnl * 100) / 100;
    pnl.total.pnl = Math.round(pnl.total.pnl * 100) / 100;

    return pnl;
}

/**
 * Initialize date selector dropdowns for P&L views
 * Sets up day, week, month, and year selectors with current values
 * @param {Function} onChangeCallback - Function to call when selectors change
 */
function initializeDateSelectors(onChangeCallback) {
    const today = new Date();
    const currentYear = today.getFullYear();
    const currentWeek = getWeekNumber(today);
    const currentMonth = today.getMonth() + 1;

    // Day selector - set to today (native date input works on all browsers)
    const daySelector = document.getElementById('day-selector');
    if (daySelector) {
        daySelector.valueAsDate = today;
        daySelector.addEventListener('change', onChangeCallback);
    }

    // Week selector - populate year dropdown
    const weekYearSelector = document.getElementById('week-year-selector');
    if (weekYearSelector) {
        for (let year = currentYear - 2; year <= currentYear + 1; year++) {
            const option = document.createElement('option');
            option.value = year;
            option.textContent = year;
            if (year === currentYear) option.selected = true;
            weekYearSelector.appendChild(option);
        }
        weekYearSelector.addEventListener('change', onChangeCallback);
    }

    // Week selector - populate week dropdown (1-53)
    const weekSelector = document.getElementById('week-selector');
    if (weekSelector) {
        for (let week = 1; week <= 53; week++) {
            const option = document.createElement('option');
            option.value = week;
            option.textContent = `Week ${week}`;
            if (week === currentWeek) option.selected = true;
            weekSelector.appendChild(option);
        }
        weekSelector.addEventListener('change', onChangeCallback);
    }

    // Month selector - populate month dropdown
    const monthSelector = document.getElementById('month-selector');
    if (monthSelector) {
        const months = ['January', 'February', 'March', 'April', 'May', 'June',
                        'July', 'August', 'September', 'October', 'November', 'December'];
        months.forEach((monthName, index) => {
            const option = document.createElement('option');
            option.value = index + 1;
            option.textContent = monthName;
            if (index + 1 === currentMonth) option.selected = true;
            monthSelector.appendChild(option);
        });
        monthSelector.addEventListener('change', onChangeCallback);
    }

    // Month year selector - populate year dropdown
    const monthYearSelector = document.getElementById('month-year-selector');
    if (monthYearSelector) {
        for (let year = currentYear - 2; year <= currentYear + 1; year++) {
            const option = document.createElement('option');
            option.value = year;
            option.textContent = year;
            if (year === currentYear) option.selected = true;
            monthYearSelector.appendChild(option);
        }
        monthYearSelector.addEventListener('change', onChangeCallback);
    }
}

/**
 * Update P&L display for a specific period (day/week/month)
 * @param {string} period - Period identifier ('day', 'week', 'month')
 * @param {Object} data - P&L data with bot1, bot2, and total properties
 */
function updatePnLPeriod(period, data) {
    if (!data) return;

    // Bot 1 data
    const bot1TradesEl = document.getElementById(`${period}-bot1-trades`);
    if (bot1TradesEl) bot1TradesEl.textContent = data.bot1.trades || 0;

    const bot1Pnl = document.getElementById(`${period}-bot1-pnl`);
    if (bot1Pnl) {
        bot1Pnl.textContent = formatCurrency(data.bot1.pnl);
        bot1Pnl.className = `pnl-value ${data.bot1.pnl >= 0 ? 'positive' : 'negative'}`;
    }

    // Bot 2 data
    const bot2TradesEl = document.getElementById(`${period}-bot2-trades`);
    if (bot2TradesEl) bot2TradesEl.textContent = data.bot2.trades || 0;

    const bot2Pnl = document.getElementById(`${period}-bot2-pnl`);
    if (bot2Pnl) {
        bot2Pnl.textContent = formatCurrency(data.bot2.pnl);
        bot2Pnl.className = `pnl-value ${data.bot2.pnl >= 0 ? 'positive' : 'negative'}`;
    }

    // Total data
    const totalTradesEl = document.getElementById(`${period}-total-trades`);
    if (totalTradesEl) totalTradesEl.textContent = data.total.trades || 0;

    const totalPnl = document.getElementById(`${period}-total-pnl`);
    if (totalPnl) {
        totalPnl.textContent = formatCurrency(data.total.pnl);
        totalPnl.className = `pnl-value ${data.total.pnl >= 0 ? 'positive' : 'negative'}`;
    }
}
