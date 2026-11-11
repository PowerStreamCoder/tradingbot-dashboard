"""Unit tests for Phase 5 Bot Control Proxy Endpoints and Pre-Flight Gates (Issue #113 / Issue #97).

Covers:
- Dependency 0 (#97): Silent fallback removal on /api/bot-control/current-profile (returns 502 on unreachable)
- Proxy endpoints: /enable, /disable, /reset-override, /promote, /demote, /emergency-flatten-and-demote
- Preflight gate status preservation (409 Conflict with blocking_reasons)
- Emergency flatten & demote proxying and timeout handling
- Bot name and symbol input validation (fails closed on malformed input)
- Runtime state override merging in /api/bot-configs from Firestore bot_runtime_controls
- X-API-Key machine client authentication
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient
import httpx

from main import app, authenticated_sessions


@pytest.fixture
def auth_client():
    """Client authenticated via valid session cookie."""
    client = TestClient(app)
    session_id = "test-session-phase5-bot-control"
    authenticated_sessions[session_id] = datetime.now() + timedelta(hours=2)
    client.cookies.set("dashboard_session", session_id)
    yield client
    authenticated_sessions.pop(session_id, None)


@pytest.fixture
def api_key_client(monkeypatch):
    """Client authenticated via BOT_CONTROL_API_KEY header."""
    mock_token = "dummy-test-token"
    monkeypatch.setenv("BOT_CONTROL_API_KEY", mock_token)
    import main
    main.BOT_CONTROL_API_KEY = mock_token
    client = TestClient(app)
    client.headers.update({"X-API-Key": mock_token})
    yield client


# ============================================================================
#  Dependency 0 (Issue #97): Silent Fallback Removal in get_current_profile
# ============================================================================

def test_current_profile_success(auth_client):
    """Returns profile when VM is healthy."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"profile": "paper"}

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        res = auth_client.get("/api/bot-control/current-profile")
        assert res.status_code == 200
        assert res.json() == {"profile": "paper"}


def test_current_profile_fails_closed_with_502_when_vm_unreachable(auth_client):
    """AC2: Unreachable VM must NOT return HTTP 200 with fallback; must return HTTP 502."""
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=httpx.ConnectError("Connection refused")):
        res = auth_client.get("/api/bot-control/current-profile")
        assert res.status_code == 502
        data = res.json()
        assert "VM Bot Control API unreachable" in data["detail"]
        assert "fallback" not in str(data)
        assert "vm_unreachable" not in data


# ============================================================================
#  Proxy Endpoints: Enable / Disable / Reset Override
# ============================================================================

def test_proxy_enable_bot_success(auth_client):
    mock_resp = MagicMock()
    mock_resp.is_error = False
    mock_resp.json.return_value = {"status": "success", "message": "Bot enabled"}

    with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_resp):
        res = auth_client.post("/api/bot-control/bots/afrm_sma/enable")
        assert res.status_code == 200
        assert res.json()["status"] == "success"


def test_proxy_disable_bot_success(auth_client):
    mock_resp = MagicMock()
    mock_resp.is_error = False
    mock_resp.json.return_value = {"status": "success", "message": "Bot disabled"}

    with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_resp):
        res = auth_client.post("/api/bot-control/bots/afrm_sma/disable")
        assert res.status_code == 200
        assert res.json()["status"] == "success"


def test_proxy_reset_bot_override_success(auth_client):
    mock_resp = MagicMock()
    mock_resp.is_error = False
    mock_resp.json.return_value = {"status": "success", "cleared": True}

    with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_resp):
        res = auth_client.post("/api/bot-control/bots/afrm_sma/reset-override")
        assert res.status_code == 200
        assert res.json()["cleared"] is True


# ============================================================================
#  Proxy Endpoints: Promotion with Pre-Flight Gates
# ============================================================================

def test_proxy_promote_bot_preserves_409_gate_blocking(auth_client):
    """When pre-flight gates fail on VM, 409 Conflict and blocking reasons pass through."""
    mock_resp = MagicMock()
    mock_resp.is_error = True
    mock_resp.status_code = 409
    mock_resp.json.return_value = {
        "status": "blocked",
        "can_promote": False,
        "blocking_reasons": ["[gate1_flatness] 50 shares detected on paper gateway"]
    }

    with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_resp):
        res = auth_client.post(
            "/api/bot-control/bots/afrm_sma/promote",
            json={"confirm_live": True, "symbol": "AFRM"}
        )
        assert res.status_code == 409
        data = res.json()
        assert data["status"] == "blocked"
        assert len(data["blocking_reasons"]) == 1


def test_proxy_promote_bot_success(auth_client):
    """When all gates pass, promotion succeeds with 200 OK and live profile."""
    mock_resp = MagicMock()
    mock_resp.is_error = False
    mock_resp.json.return_value = {
        "status": "success",
        "profile": "live",
        "enabled": True,
        "message": "Promotion completed successfully"
    }

    with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_resp):
        res = auth_client.post(
            "/api/bot-control/bots/afrm_sma/promote",
            json={"confirm_live": True, "symbol": "AFRM"}
        )
        assert res.status_code == 200
        assert res.json()["profile"] == "live"


def test_proxy_promote_requires_body(auth_client):
    """Calling promote without body returns 400 Bad Request."""
    res = auth_client.post("/api/bot-control/bots/afrm_sma/promote")
    assert res.status_code in (400, 422)


# ============================================================================
#  Proxy Endpoints: Demotion (Path A) and Emergency Flatten (Path B)
# ============================================================================

def test_proxy_demote_preserves_409_conflict_on_open_positions(auth_client):
    mock_resp = MagicMock()
    mock_resp.is_error = True
    mock_resp.status_code = 409
    mock_resp.json.return_value = {
        "status": "blocked",
        "message": "Cannot demote: open positions exist"
    }

    with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_resp):
        res = auth_client.post("/api/bot-control/bots/afrm_sma/demote")
        assert res.status_code == 409
        assert res.json()["status"] == "blocked"


def test_proxy_emergency_flatten_success(auth_client):
    mock_resp = MagicMock()
    mock_resp.is_error = False
    mock_resp.json.return_value = {
        "status": "success",
        "profile": "paper",
        "message": "Emergency flatten completed and demoted to paper"
    }

    with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_resp):
        res = auth_client.post(
            "/api/bot-control/bots/afrm_sma/emergency-flatten-and-demote",
            json={"confirm_emergency_flatten": True, "symbol": "AFRM"}
        )
        assert res.status_code == 200
        assert res.json()["profile"] == "paper"


def test_proxy_emergency_flatten_502_on_vm_unreachable(auth_client):
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock, side_effect=httpx.TimeoutException("VM timed out")):
        res = auth_client.post(
            "/api/bot-control/bots/afrm_sma/emergency-flatten-and-demote",
            json={"confirm_emergency_flatten": True, "symbol": "AFRM"}
        )
        assert res.status_code == 502
        assert "VM Bot Control API unreachable" in res.json()["detail"]


# ============================================================================
#  Input Validation
# ============================================================================

def test_proxy_rejects_invalid_bot_name(auth_client):
    """Malicious or invalid bot names return 400."""
    res = auth_client.post("/api/bot-control/bots/..%2Fhack/enable")
    assert res.status_code in (400, 404)

    res_special = auth_client.post("/api/bot-control/bots/bad;name/enable")
    assert res_special.status_code == 400


def test_verify_flatness_proxy(auth_client):
    mock_resp = MagicMock()
    mock_resp.is_error = False
    mock_resp.json.return_value = {"is_flat": True, "symbol": "AFRM"}

    with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_resp):
        res = auth_client.get("/api/bot-control/verify-flatness/AFRM")
        assert res.status_code == 200
        assert res.json()["is_flat"] is True

    # Bad symbol format
    bad_res = auth_client.get("/api/bot-control/verify-flatness/INVALID--SYMBOL$")
    assert bad_res.status_code == 400


# ============================================================================
#  Firestore Mirror Merging in /api/bot-configs
# ============================================================================

def test_bot_configs_merges_runtime_state_overrides(auth_client):
    """Dynamic runtime overrides in Firestore bot_runtime_controls are merged into bot-configs."""
    mock_doc = MagicMock()
    mock_doc.id = "afrm_sma"
    mock_doc.to_dict.return_value = {
        "profile": "live",
        "trading_mode": "live",
        "enabled": False
    }

    mock_db = MagicMock()
    mock_db.collection.return_value.stream.return_value = [mock_doc]

    with patch("main.dashboard_data.db", mock_db):
        res = auth_client.get("/api/bot-configs")
        assert res.status_code == 200
        data = res.json()
        afrm = next((b for b in data["bots"] if b.get("name") == "afrm_sma"), None)
        if afrm:
            assert afrm["profile"] == "live"
            assert afrm["trading_mode"] == "live"
            assert afrm["enabled"] is False
            assert afrm["is_overridden"] is True

    # Test that an override identical to baseline does NOT mark is_overridden=True
    mock_baseline_doc = MagicMock()
    mock_baseline_doc.id = "afrm_sma"
    mock_baseline_doc.to_dict.return_value = {
        "enabled": True,
        "profile": "paper",
    }
    mock_db2 = MagicMock()
    mock_db2.collection.return_value.stream.return_value = [mock_baseline_doc]

    with patch("main.dashboard_data.db", mock_db2):
        res2 = auth_client.get("/api/bot-configs")
        assert res2.status_code == 200
        data2 = res2.json()
        afrm2 = next((b for b in data2["bots"] if b.get("name") == "afrm_sma"), None)
        if afrm2:
            assert afrm2["enabled"] is True
            assert afrm2["is_overridden"] is False


# ============================================================================
#  API Key Authentication for Machine Clients
# ============================================================================

def test_api_key_auth_allows_proxy_access(api_key_client):
    """Machine requests with valid X-API-Key bypass browser session checks."""
    mock_resp = MagicMock()
    mock_resp.is_error = False
    mock_resp.json.return_value = {"status": "success", "message": "Bot enabled"}

    with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_resp):
        res = api_key_client.post("/api/bot-control/bots/afrm_sma/enable")
        assert res.status_code == 200


def test_graduate_and_flatten_modals_outside_stock_picker_section():
    """Verify that graduateLiveModal and emergencyFlattenModal are not inside stock-picker-section."""
    from pathlib import Path
    template_path = Path(__file__).parent.parent / "templates" / "bot_focus.html"
    assert template_path.exists()
    content = template_path.read_text(encoding="utf-8")

    sp_start = content.find('id="stock-picker-section"')
    assert sp_start != -1
    sp_end = content.find('</section>', sp_start)
    assert sp_end != -1

    stock_picker_block = content[sp_start:sp_end]
    assert 'id="graduateLiveModal"' not in stock_picker_block
    assert 'id="emergencyFlattenModal"' not in stock_picker_block

    assert 'id="graduateLiveModal"' in content
    assert 'id="emergencyFlattenModal"' in content
