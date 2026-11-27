/**
 * #495/D3 regression: the governance telemetry archive must show the shield
 * branch at its real cascade priority and surface shield→trailing max-floor
 * re-attributions (a label-only surface with no branch counters).
 *
 * Run with: node tests/js/test_telemetry_shield_overlap.js
 * Exits non-zero on failure so it can be wrapped by pytest (see
 * tests/test_governance_api.py::test_telemetry_js_surfaces_shield_attribution).
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const JS = fs.readFileSync(
    path.join(__dirname, '..', '..', 'static', 'js', 'governance_review.js'),
    'utf8'
);

const container = { innerHTML: '' };
const searchEl = { value: '' };
const symbolEl = { value: '' };
const badgeEl = { textContent: '' };

global.document = {
    getElementById: id => ({
        'telemetry-container': container,
        'telemetry-search': searchEl,
        'telemetry-symbol-filter': symbolEl,
        'telemetry-count-badge': badgeEl,
    }[id] || null),
    addEventListener() {}, // keep the DOMContentLoaded bootstrap dormant
};
global.window = global;

// Top-level `const` is script-scoped in vm; expose the pieces under test.
vm.runInThisContext(
    JS + '\nglobal.__meta = EXIT_BRANCH_METADATA;'
       + '\nglobal.__render = renderTelemetrySessions;'
       + '\nglobal.__filter = filterTelemetrySessions;'
       + '\nglobal.__setSessions = s => { rawTelemetrySessions = s; };',
    { filename: 'governance_review.js' }
);

function assert(cond, msg) {
    if (!cond) {
        console.error('FAIL:', msg);
        process.exitCode = 1;
    }
}

const meta = global.__meta;
assert(meta.peak_giveback_shield, 'shield must be in the branch metadata');
assert(meta.peak_giveback_shield.priority === 5, 'shield must be cascade priority 5');
assert(meta.trailing_stop.priority === 6, 'trailing stop must shift to priority 6');
assert(meta.hold.priority === 11, 'hold must be the priority-11 sentinel');

const SESSION = {
    symbol: 'NVDA',
    archived_at: '2026-11-27T15:00:00Z',
    inventory: {
        branches: {
            peak_giveback_shield: {
                evaluated: 2, fired: 0, status: 'dead', proximity: 0.4,
            },
        },
        _shield_overlap: {
            reattributed: 1,
            events: [{ shield_floor: 132, trailing_floor: 134, tick_price: 130 }],
        },
        _weekend_guard: { exits: 0, carried: 1, deferred: 0 },
    },
};

global.__render([SESSION]);
let html = container.innerHTML;
assert(html.includes('Peak Giveback Shield') && html.includes('P5'),
    'shield branch must render with its real priority');
assert(html.includes('🧭 1 Re-attributed'), 're-attribution badge must be visible');
assert(html.includes('$132.00') && html.includes('$134.00') && html.includes('$130.00'),
    'measured floors and tick price must be auditable in the card');
assert(html.includes('🌙 1 Guard outcomes'), 'weekend guard surface must be visible');
assert(html.includes('fill price and exit behavior are unchanged'),
    'panel must state this is label-only');

// Sessions archived before #493/#495 carried these sections only in `snapshot`.
container.innerHTML = '';
global.__render([{
    symbol: 'AMD',
    archived_at: '2026-11-26T15:00:00Z',
    inventory: { branches: {} },
    snapshot: {
        _shield_overlap: {
            reattributed: 1,
            events: [{ shield_floor: 9, trailing_floor: 10, tick_price: 8 }],
        },
    },
}]);
html = container.innerHTML;
assert(html.includes('🧭 1 Re-attributed'), 'legacy snapshot fallback must work');
assert(html.includes('$9.00') && html.includes('$10.00'), 'legacy floors must render');

// Searching for the attribution must find an otherwise empty branch inventory.
global.__setSessions([SESSION]);
searchEl.value = 'reattrib';
symbolEl.value = '';
global.__filter();
assert(badgeEl.textContent === '1 of 1 sessions',
    `reattribution search must match the session, got: ${badgeEl.textContent}`);

// Untrusted event payloads must not become markup.
container.innerHTML = '';
global.__render([{
    symbol: 'NVDA',
    archived_at: '2026-11-27T15:00:00Z',
    inventory: {
        branches: {},
        _shield_overlap: {
            reattributed: 1,
            events: [{ shield_floor: '<img src=x onerror=alert(1)>', trailing_floor: 1, tick_price: 1 }],
        },
    },
}]);
assert(!container.innerHTML.includes('<img'), 'attribution floors must be HTML-escaped');

if (!process.exitCode) console.log('telemetry shield-overlap JS regressions passed');
