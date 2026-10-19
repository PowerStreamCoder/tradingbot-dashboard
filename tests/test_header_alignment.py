"""
Tests for header dimensions, structure, and navigation bar alignment across all dashboard pages.
Ensures seamless switching between tabs without vertical jumping or dimensional shifts.
"""

import pytest
import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
STATIC_DIR = Path(__file__).parent.parent / "static"


def test_bot_focus_header_markup():
    bot_focus_path = TEMPLATES_DIR / "bot_focus.html"
    assert bot_focus_path.exists()
    content = bot_focus_path.read_text(encoding="utf-8")

    # Bot focus header exists
    assert 'id="botFocusHeader"' in content
    # Stock picker header exists
    assert 'id="stockPickerHeader"' in content

    # Mode indicator is inside header-right, not floating as an independent top-level element
    header_right_match = re.search(r'<div class="header-right">(.*?)</div>\s*</header>', content, re.DOTALL)
    assert header_right_match is not None, "header-right must be present in botFocusHeader"
    header_right_content = header_right_match.group(1)

    assert 'class="mode-indicator"' in header_right_content, "mode-indicator must be inside header-right"
    assert 'id="modeBadge"' in header_right_content, "modeBadge must be preserved inside header-right"
    assert 'id="pauseBotBtn"' in header_right_content
    assert 'id="switchProfileBtn"' in header_right_content


def test_navigation_bar_consistency_across_templates():
    template_files = [
        "bot_focus.html",
        "index.html",
        "performance.html",
        "learning_review.html",
        "governance_review.html"
    ]

    expected_nav_order = [
        "/bot-focus",
        "/pnl-reporting",
        "/performance",
        "/learning-review",
        "/governance-review",
        "/bot-focus?show=stockpicker"
    ]

    for fname in template_files:
        fpath = TEMPLATES_DIR / fname
        assert fpath.exists(), f"{fname} not found"
        content = fpath.read_text(encoding="utf-8")

        # Every template has a nav-bar
        assert '<nav class="nav-bar">' in content, f"{fname} missing nav-bar"

        # Verify all nav hrefs in expected order
        nav_match = re.search(r'<nav class="nav-bar">(.*?)</nav>', content, re.DOTALL)
        assert nav_match is not None, f"Could not extract nav-bar in {fname}"
        nav_html = nav_match.group(1)

        for href in expected_nav_order:
            assert f'href="{href}"' in nav_html, f"Missing href={href} in {fname}"


def test_header_css_dimensions_and_alignment():
    css_path = STATIC_DIR / "css" / "bot_focus.css"
    assert css_path.exists()
    css_content = css_path.read_text(encoding="utf-8")

    # .bot-header min-height is 98px and box-sizing is border-box
    assert "min-height: 98px;" in css_content
    assert "box-sizing: border-box;" in css_content

    # Desktop height constraint
    assert ".bot-header {\n        height: 98px;" in css_content or ".bot-header {\n        height: 98px" in css_content

    # No fixed positioning on mode-indicator
    mode_ind_match = re.search(r'\.mode-indicator\s*\{([^}]+)\}', css_content)
    assert mode_ind_match is not None
    assert "position: fixed" not in mode_ind_match.group(1)

    # Standardized 32px height on header buttons and mode badge
    assert ".header-right .btn {" in css_content
    assert "height: 32px;" in css_content

    # Header-left h1 line-height and font-size
    assert "line-height: 28px;" in css_content
    assert "font-size: 22px;" in css_content


def test_custom_bot_dropdown_visibility_and_stacking():
    """Verify that the bot header and dropdown menu have proper stacking context to render above the nav-bar."""
    css_path = STATIC_DIR / "css" / "bot_focus.css"
    assert css_path.exists()
    css_content = css_path.read_text(encoding="utf-8")

    # .bot-header has position: relative and z-index: 100 to sit above nav-bar in stacking context
    bot_header_match = re.search(r'\.bot-header\s*\{([^}]+)\}', css_content)
    assert bot_header_match is not None
    assert "position: relative;" in bot_header_match.group(1)
    assert "z-index: 100;" in bot_header_match.group(1)

    # .nav-bar has z-index lower than .bot-header
    nav_bar_match = re.search(r'\.nav-bar\s*\{([^}]+)\}', css_content)
    assert nav_bar_match is not None
    assert "z-index: 10;" in nav_bar_match.group(1)

    # .bot-selector-custom-wrapper has positioning and z-index
    wrapper_match = re.search(r'\.bot-selector-custom-wrapper\s*\{([^}]+)\}', css_content)
    assert wrapper_match is not None
    assert "position: relative;" in wrapper_match.group(1)
    assert "z-index: 110;" in wrapper_match.group(1)

    # .custom-bot-menu has absolute position, high z-index, and solid background
    menu_match = re.search(r'\.custom-bot-menu\s*\{([^}]+)\}', css_content)
    assert menu_match is not None
    assert "position: absolute;" in menu_match.group(1)
    assert "z-index: 1000;" in menu_match.group(1)
    assert "max-height:" in menu_match.group(1)


def test_bot_switch_loading_indicators():
    """Verify that section-level and table-level indicators exist for bot/symbol switching."""
    html_path = TEMPLATES_DIR / "bot_focus.html"
    assert html_path.exists()
    html_content = html_path.read_text(encoding="utf-8")

    # Global status pill in bot-header metadata
    assert 'id="botSwitchStatusPill"' in html_content
    assert 'id="botSwitchStatusText"' in html_content

    # All section-level switch badges in headers
    expected_badges = [
        "switchBadge-chart",
        "switchBadge-performance",
        "switchBadge-position",
        "switchBadge-health",
        "switchBadge-strategy",
        "switchBadge-orders",
        "switchBadge-params",
        "switchBadge-tradeHistory",
        "switchBadge-logs",
    ]
    for badge_id in expected_badges:
        assert f'id="{badge_id}"' in html_content, f"Missing badge {badge_id} in bot_focus.html"

    # Chart switch overlay
    assert 'id="chartSwitchOverlay"' in html_content

    # CSS styles exist
    css_path = STATIC_DIR / "css" / "bot_focus.css"
    assert css_path.exists()
    css_content = css_path.read_text(encoding="utf-8")
    assert ".section-switch-badge" in css_content
    assert ".bot-switch-status-pill" in css_content
    assert ".table-switch-loading" in css_content
    assert ".chart-switch-overlay" in css_content

    # JS logic exists
    js_path = STATIC_DIR / "js" / "bot_focus.js"
    assert js_path.exists()
    js_content = js_path.read_text(encoding="utf-8")
    assert "BOT_SECTION_CONFIG" in js_content
    assert "function beginBotSwitchLoading(" in js_content
    assert "function setSectionLoadingIndicator(" in js_content
    assert "function completeSectionSwitchLoading(" in js_content
    assert "function setTableLoadingPlaceholders(" in js_content


def test_performance_loading_indicators():
    """Verify that section-level, table-level, and progress indicators exist for performance dashboard."""
    html_path = TEMPLATES_DIR / "performance.html"
    assert html_path.exists()
    html_content = html_path.read_text(encoding="utf-8")

    # Global status pill & progress bar
    assert 'id="perfStatusPill"' in html_content
    assert 'id="perfStatusText"' in html_content
    assert 'id="perfProgressContainer"' in html_content
    assert 'id="perfProgressBar"' in html_content

    # All section-level switch badges in performance.html
    expected_badges = [
        "perfBadge-scorecard",
        "perfBadge-kpi",
        "perfBadge-sec605",
        "perfBadge-instCta",
        "perfBadge-buyHold",
        "perfBadge-radar",
        "perfBadge-bots",
        "perfBadge-diagnostics",
    ]
    for badge_id in expected_badges:
        assert f'id="{badge_id}"' in html_content, f"Missing badge {badge_id} in performance.html"

    # Radar switch overlay
    assert 'id="radarSwitchOverlay"' in html_content

    # CSS styles exist
    css_path = STATIC_DIR / "css" / "performance.css"
    assert css_path.exists()
    css_content = css_path.read_text(encoding="utf-8")
    assert ".perf-progress-container" in css_content
    assert ".perf-progress-bar" in css_content
    assert ".tile-loading" in css_content
    assert ".diagnostics-loading-placeholder" in css_content

    # JS logic exists
    js_path = STATIC_DIR / "js" / "performance.js"
    assert js_path.exists()
    js_content = js_path.read_text(encoding="utf-8")
    assert "PERF_SECTION_CONFIG" in js_content
    assert "function beginPerformanceLoading(" in js_content
    assert "function setPerfSectionLoadingIndicator(" in js_content
    assert "function completePerfSectionLoading(" in js_content
    assert "function setPerformanceLoadingPlaceholders(" in js_content
    assert "function startProgressBar(" in js_content
    assert "function finishProgressBar(" in js_content

