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
        "learning_review.html",
        "governance_review.html"
    ]

    expected_nav_order = [
        "/bot-focus",
        "/pnl-reporting",
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
