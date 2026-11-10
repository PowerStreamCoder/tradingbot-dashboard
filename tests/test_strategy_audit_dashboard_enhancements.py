"""
Tests for Strategy Audit UI Enhancements in tradingbot-dashboard.
Verifies:
1. templates/bot_focus.html contains the SMA 20 Slope widget.
2. static/js/bot_focus.js contains getActiveBuckets, stepped stop loss badges,
   trend residence badge, pyramid indicator, and new exit reason mappings.
3. Node execution of JS logic verifying getActiveBuckets and formatExitReason.
"""

import subprocess
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
STATIC_DIR = Path(__file__).parent.parent / "static"


def test_bot_focus_template_sma20_slope():
    """Verify bot_focus.html contains SMA 20 Slope in strategy grid."""
    html_path = TEMPLATES_DIR / "bot_focus.html"
    assert html_path.exists(), "bot_focus.html must exist"
    content = html_path.read_text(encoding="utf-8")

    assert 'id="sma20Slope"' in content
    assert "SMA 20 Slope" in content


def test_bot_focus_js_functions_and_badges():
    """Verify static/js/bot_focus.js includes strategy enhancements."""
    js_path = STATIC_DIR / "js" / "bot_focus.js"
    assert js_path.exists(), "bot_focus.js must exist"
    content = js_path.read_text(encoding="utf-8")

    # getActiveBuckets helper
    assert "function getActiveBuckets(" in content
    assert "function getActiveBucket(" in content

    # Badges and indicators in position table
    assert "Trend Residence" in content
    assert "Pyramided" in content
    assert "+1.0R Floor" in content
    assert "Scratch Lock" in content
    assert "Buffer Lock" in content

    # Exit reason mappings
    assert "regime_flip_to_bear_loss_cut" in content
    assert "sma_slope_reversal" in content
    assert "regime_flip" in content
    assert "intraday_peak_giveback_shield" in content


def test_bot_focus_js_node_execution():
    """Execute node script to verify runtime behavior of JS functions."""
    js_path = STATIC_DIR / "js" / "bot_focus.js"
    config_path = STATIC_DIR / "js" / "config.js"
    utils_path = STATIC_DIR / "js" / "utils.js"
    test_script = f"""
    const fs = require('fs');
    const vm = require('vm');

    const utilsCode = fs.readFileSync('{utils_path}', 'utf-8');
    const configCode = fs.readFileSync('{config_path}', 'utf-8');
    const botFocusCode = fs.readFileSync('{js_path}', 'utf-8');

    // Create minimal sandbox environment
    const sandbox = {{
        window: {{
            location: {{ hostname: 'localhost', search: '' }},
            addEventListener: () => {{}}
        }},
        document: {{
            getElementById: () => null,
            querySelector: () => null,
            querySelectorAll: () => [],
            addEventListener: () => {{}}
        }},
        console: console
    }};

    vm.createContext(sandbox);

    // Evaluate prerequisites
    vm.runInContext(utilsCode, sandbox);
    vm.runInContext(configCode, sandbox);
    vm.runInContext(botFocusCode, sandbox);

    sandbox.CONFIG = {{ symbol: 'NVDA', botId: 1 }};

    // 1. Verify formatExitReason
    if (typeof sandbox.formatExitReason !== 'function') {{
        throw new Error('formatExitReason is not a function');
    }}
    const expectedExitReasons = {{
        'regime_flip_to_bear_loss_cut': '🐻 Regime Flip Loss Cut',
        'regime_flip': '🔄 Regime Flip Exit',
        'sma_slope_reversal': '📉 SMA Slope Reversal',
        'break_even_lock_exit': '🔒 Break-Even Lock Exit',
        'intraday_peak_giveback_shield': '🛡️ Peak Giveback Shield',
        'orb_breakdown_shield': '🛡️ ORB Breakdown Shield',
        'gap_up_fade_shield': '🛡️ Gap Fade Shield',
        'premarket_trap_shield': '🛡️ Premarket Trap Shield',
        'morning_distribution_shield': '🛡️ Morning Distribution Shield'
    }};
    for (const [key, expected] of Object.entries(expectedExitReasons)) {{
        const actual = sandbox.formatExitReason(key);
        if (actual !== expected) {{
            throw new Error('Exit reason mismatch for ' + key);
        }}
    }}

    // 2. Verify getActiveBuckets multi-bucket handling
    if (typeof sandbox.getActiveBuckets !== 'function') {{
        throw new Error('getActiveBuckets is not a function');
    }}

    const mockBotData = {{
        botId: 1,
        bucket1: {{
            entryPrice: 120.5,
            quantity: 100,
            side: 'long',
            residence_active: true
        }},
        bucket2: {{
            entryPrice: 125.0,
            quantity: 100,
            side: 'long',
            pyramid_executed: true,
            pyramid_add_quantity: 50
        }},
        bucket3: {{ entryPrice: null, quantity: 0 }}
    }};

    const active = sandbox.getActiveBuckets(mockBotData);
    if (active.length !== 2) {{
        throw new Error('Expected 2 active buckets, got ' + active.length);
    }}
    if (active[0].bucketNumber !== 1 || !active[0].residence_active) {{
        throw new Error('Bucket 1 attributes incorrect');
    }}
    if (active[1].bucketNumber !== 2 || !active[1].pyramid_executed) {{
        throw new Error('Bucket 2 attributes incorrect');
    }}

    // Verify getActiveBucket fallback
    const first = sandbox.getActiveBucket(mockBotData);
    if (!first || first.bucketNumber !== 1) {{
        throw new Error('getActiveBucket should return first active bucket');
    }}

    // 3. Verify updatePositionOrdersTable multi-bucket rendering & badges
    let tableHtml = '';
    const mockTbody = {{
        dataset: {{}},
        set innerHTML(val) {{ tableHtml = val; }},
        get innerHTML() {{ return tableHtml; }},
        querySelectorAll: () => []
    }};
    const mockStatusEl = {{ textContent: '', style: {{}} }};

    sandbox.document.getElementById = (id) => {{
        if (id === 'positionOrdersTableBody') return mockTbody;
        if (id === 'positionOrdersStatus') return mockStatusEl;
        return null;
    }};

    const multiBucketBotData = {{
        botId: 1,
        currentPrice: 130.0,
        bucket1: {{
            entryPrice: 120.0,
            quantity: 100,
            side: 'long',
            stopLossPrice: 124.0,
            break_even_stage: 'profit_floor',
            tiered_profit_locked: true,
            residence_active: true
        }},
        bucket2: {{
            entryPrice: 125.0,
            quantity: 150,
            side: 'long',
            stopLossPrice: 125.0,
            break_even_stage: 'cost_basis',
            pyramid_executed: true,
            pyramid_add_quantity: 50
        }}
    }};

    sandbox.updatePositionOrdersTable(multiBucketBotData);

    if (!tableHtml.includes('B#1') || !tableHtml.includes('B#2')) {{
        throw new Error('Table HTML missing multi-bucket badges');
    }}
    if (!tableHtml.includes('Trend Residence')) {{
        throw new Error('Table HTML missing Trend Residence badge');
    }}
    if (!tableHtml.includes('Pyramided (+50)')) {{
        throw new Error('Table HTML missing Pyramided indicator');
    }}
    if (!tableHtml.includes('+1.0R Floor')) {{
        throw new Error('Table HTML missing +1.0R Floor stop loss badge');
    }}
    if (!tableHtml.includes('Scratch Lock')) {{
        throw new Error('Table HTML missing Scratch Lock stop loss badge');
    }}
    const expectedMultiStatus = '2 positions active (Buckets: #1, #2)';
    if (!mockStatusEl.textContent.includes(expectedMultiStatus)) {{
        throw new Error('Status mismatch: ' + mockStatusEl.textContent);
    }}

    // 4. Verify single-bucket residence status and buffer lock badge
    mockTbody.dataset = {{}};
    const singleBucketBotData = {{
        botId: 1,
        currentPrice: 130.0,
        bucket1: {{
            entryPrice: 120.0,
            quantity: 100,
            side: 'long',
            stopLossPrice: 121.0,
            break_even_stage: 'buffer',
            residence_active: true
        }}
    }};

    sandbox.updatePositionOrdersTable(singleBucketBotData);

    if (!tableHtml.includes('Buffer Lock')) {{
        throw new Error('Table HTML missing Buffer Lock badge');
    }}
    const expectedResidenceTag = ' (🏡 Residence Active)';
    if (!mockStatusEl.textContent.includes(expectedResidenceTag)) {{
        throw new Error('Status string missing residence tag');
    }}

    console.log('ALL_JS_CHECKS_PASSED');
    """

    res = subprocess.run(
        ["node", "-e", test_script],
        capture_output=True,
        text=True
    )
    assert res.returncode == 0, f"Node verification failed: {res.stderr}"
    assert "ALL_JS_CHECKS_PASSED" in res.stdout
