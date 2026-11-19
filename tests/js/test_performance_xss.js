/**
 * Regression test for Issue #146 F13: backend strings must be HTML-escaped
 * before being interpolated into innerHTML by the performance scorecard.
 *
 * Run with: node tests/js/test_performance_xss.js
 * Exits non-zero on failure so it can be wrapped by pytest (see
 * tests/test_performance_tab.py::test_performance_js_escapes_untrusted_strings).
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const JS = fs.readFileSync(
    path.join(__dirname, '..', '..', 'static', 'js', 'performance.js'),
    'utf8'
);

function makeEl() {
    return {
        innerHTML: '',
        innerText: '',
        className: '',
        dataset: {},
        style: {},
        onclick: null,
        appendChild() {},
    };
}

const elements = {};
const ids = [
    'tableSec605', 'tableInstCta', 'tableBuyHold', 'tableMachinery',
    'botsTableBody', 'botScopePills', 'activeBots', 'diagnosticsGrid',
];
ids.forEach(id => { elements[id] = makeEl(); });

global.document = {
    getElementById: id => elements[id] || null,
    addEventListener() {}, // no-op: do not run the DOMContentLoaded bootstrap
    createElement: () => makeEl(),
};

// Load the client into the current context: function declarations become
// globals, while the DOMContentLoaded callback stays dormant.
vm.runInThisContext(JS, { filename: 'performance.js' });

const PAYLOAD = '<img src=x onerror=alert(1)>"';

function assert(cond, msg) {
    if (!cond) {
        console.error('FAIL:', msg);
        process.exitCode = 1;
    }
}

// 1. Benchmark table
renderBenchmarkTable('tableSec605', [{
    name: PAYLOAD, description: PAYLOAD, engine_formatted: PAYLOAD,
    benchmark_formatted: PAYLOAD, status: PAYLOAD, engine_value: 1,
}]);
let html = elements.tableSec605.innerHTML;
assert(html.includes('&lt;img src=x onerror=alert(1)&gt;'), 'benchmark name/description escaped');
assert(!html.includes('<img'), 'benchmark table has no raw <img>');
assert(!html.includes('"' + PAYLOAD), 'benchmark table has no attribute breakout');

// 2. Bots table (also exercises the scope-pill symbol injection)
renderBots([{
    symbol: PAYLOAD, strategy: PAYLOAD, status: PAYLOAD,
    status_badge: PAYLOAD, rating_grade: PAYLOAD, rating_score: 90,
    trades_count: 10, win_rate_pct: 50, net_pnl: 1, profit_factor: 1,
    sharpe_ratio: 1, max_drawdown_pct: 1, avg_slippage_per_share: 0.1,
    diagnostic_finding: PAYLOAD,
}]);
html = elements.botsTableBody.innerHTML;
assert(html.includes('&lt;img'), 'bot strings escaped');
assert(!html.includes('<img'), 'bots table has no raw <img>');
html = elements.botScopePills.innerHTML;
// Pills are built via child elements, so verify the helper directly too.
assert(escapeHtml(PAYLOAD) === '&lt;img src=x onerror=alert(1)&gt;&quot;',
    'escapeHtml handles angle brackets and double quotes');

// 3. Diagnostics (including the href attribute)
renderDiagnostics([{
    level: PAYLOAD, category: PAYLOAD, title: PAYLOAD, detail: PAYLOAD,
    recommended_action: PAYLOAD, guide_reference: PAYLOAD,
}]);
html = elements.diagnosticsGrid.innerHTML;
assert(html.includes('&lt;img'), 'diagnostic strings escaped');
assert(!html.includes('<img'), 'diagnostics have no raw <img>');
assert(!html.includes('onerror=alert(1)"'), 'diagnostic href has no attribute breakout');

if (!process.exitCode) {
    console.log('F13 XSS regression: all checks passed');
}
