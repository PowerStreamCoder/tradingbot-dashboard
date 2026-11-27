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

import main
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
    assert 'id="fleetControlModal"' not in stock_picker_block

    assert 'id="graduateLiveModal"' in content
    assert 'id="emergencyFlattenModal"' in content
    assert 'id="fleetControlModal"' in content
    assert content.index('id="emergencyFlattenModal"') < content.index('id="fleetControlModal"')


# ============================================================================
#  Fleet Emergency Controls proxies and UI contract (Issue #493)
# ============================================================================

FLEET_POST_ACTIONS = (
    ("cancel-all", "confirm_fleet_cancel_all"),
    ("flatten-all", "confirm_fleet_flatten_all"),
    ("clear", "confirm_fleet_clear"),
)


@pytest.mark.parametrize("action,token", FLEET_POST_ACTIONS)
def test_proxy_fleet_post_success_passthrough(auth_client, monkeypatch, action, token):
    """POST bodies reach the exact VM path with the operator's tokens unchanged."""
    proxy_token = "fleet-key"
    monkeypatch.setattr(main, "BOT_CONTROL_API_KEY", proxy_token)
    upstream_path = f"/api/bot-control/fleet/{action}"
    payload = {token: True, "scope": "ALL", "requested_by": "dashboard"}

    mock_resp = MagicMock()
    mock_resp.is_error = False
    mock_resp.json.return_value = {"status": "success", "action": action}
    request_mock = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient.request", new=request_mock):
        res = auth_client.post(upstream_path, json=payload)

    assert res.status_code == 200
    assert res.json() == {"status": "success", "action": action}
    assert request_mock.await_count == 1
    call_kwargs = request_mock.await_args.kwargs
    assert call_kwargs["method"] == "POST"
    assert call_kwargs["url"].endswith(upstream_path)
    assert call_kwargs["json"] == payload
    assert call_kwargs["headers"]["X-API-Key"] == proxy_token


@pytest.mark.parametrize("action,token", FLEET_POST_ACTIONS)
def test_proxy_fleet_post_requires_body(auth_client, action, token):
    res = auth_client.post(f"/api/bot-control/fleet/{action}")
    assert res.status_code == 400
    assert "Missing JSON body" in res.json()["detail"]


@pytest.mark.parametrize("action,token", FLEET_POST_ACTIONS)
def test_proxy_fleet_post_preserves_upstream_error(auth_client, action, token):
    mock_resp = MagicMock()
    mock_resp.is_error = True
    mock_resp.status_code = 409
    mock_resp.json.return_value = {"status": "blocked", "detail": "halt already active"}
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_resp):
        res = auth_client.post(
            f"/api/bot-control/fleet/{action}",
            json={token: True, "scope": "ALL", "requested_by": "dashboard"},
        )
    assert res.status_code == 409
    assert res.json() == {"status": "blocked", "detail": "halt already active"}


@pytest.mark.parametrize("action,token", FLEET_POST_ACTIONS)
def test_proxy_fleet_post_502_on_timeout(auth_client, action, token):
    with patch(
        "httpx.AsyncClient.request",
        new_callable=AsyncMock,
        side_effect=httpx.TimeoutException("VM timed out"),
    ):
        res = auth_client.post(
            f"/api/bot-control/fleet/{action}",
            json={token: True, "scope": "ALL", "requested_by": "dashboard"},
        )
    assert res.status_code == 502
    assert "VM Bot Control API unreachable" in res.json()["detail"]


def test_proxy_fleet_status_success_and_exact_path(auth_client):
    mock_resp = MagicMock()
    mock_resp.is_error = False
    mock_resp.json.return_value = {
        "halt_entries": True,
        "generation": 7,
        "action": "cancel_all",
        "command_id": "cmd-7",
        "acks": {"afrm_sma": {"status": "ok", "cancelled_count": 2}},
    }
    request_mock = AsyncMock(return_value=mock_resp)
    with patch("httpx.AsyncClient.request", new=request_mock):
        res = auth_client.get("/api/bot-control/fleet/status")
    assert res.status_code == 200
    assert res.json()["halt_entries"] is True
    call_kwargs = request_mock.await_args.kwargs
    assert call_kwargs["method"] == "GET"
    assert call_kwargs["url"].endswith("/api/bot-control/fleet/status")


def test_proxy_fleet_status_error_passthrough_and_timeout(auth_client):
    mock_resp = MagicMock()
    mock_resp.is_error = True
    mock_resp.status_code = 503
    mock_resp.json.return_value = {"detail": "ledger unavailable"}
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_resp):
        res = auth_client.get("/api/bot-control/fleet/status")
    assert res.status_code == 503
    assert res.json() == {"detail": "ledger unavailable"}

    with patch(
        "httpx.AsyncClient.request",
        new_callable=AsyncMock,
        side_effect=httpx.ConnectError("refused"),
    ):
        res = auth_client.get("/api/bot-control/fleet/status")
    assert res.status_code == 502


def test_fleet_proxies_use_dedicated_timeouts(auth_client):
    """Flatten gets a longer VM timeout; status stays short and non-blocking."""
    cases = (
        ("post", "/api/bot-control/fleet/cancel-all", 30.0),
        ("post", "/api/bot-control/fleet/flatten-all", 60.0),
        ("post", "/api/bot-control/fleet/clear", 30.0),
        ("get", "/api/bot-control/fleet/status", 15.0),
    )
    mock_resp = MagicMock()
    mock_resp.is_error = False
    mock_resp.json.return_value = {"status": "ok"}

    for method_name, path, expected_timeout in cases:
        client_mock = MagicMock()
        client_mock.__aenter__ = AsyncMock(return_value=client_mock)
        client_mock.__aexit__ = AsyncMock(return_value=None)
        client_mock.request = AsyncMock(return_value=mock_resp)
        with patch("main.httpx.AsyncClient", return_value=client_mock) as ctor:
            res = getattr(auth_client, method_name)(
                path,
                **({"json": {"confirm_fleet_clear": True, "scope": "ALL"}} if method_name == "post" else {}),
            )
        assert res.status_code == 200, path
        assert ctor.call_args.kwargs["timeout"] == expected_timeout


def test_fleet_proxies_require_auth(api_key_client):
    """Machine API key is accepted; a bare client gets 401 for these paths."""
    mock_resp = MagicMock()
    mock_resp.is_error = False
    mock_resp.json.return_value = {"status": "success"}
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=mock_resp):
        res = api_key_client.post(
            "/api/bot-control/fleet/cancel-all",
            json={"confirm_fleet_cancel_all": True, "scope": "ALL", "requested_by": "dashboard"},
        )
    assert res.status_code == 200

    unauthenticated = TestClient(app)
    assert unauthenticated.get("/api/bot-control/fleet/status").status_code == 401
    assert unauthenticated.post("/api/bot-control/fleet/flatten-all", json={}).status_code == 401


def test_fleet_control_js_confirmation_contract():
    """Static contract check: distinct tokens, session cookies, no native dialogs."""
    from pathlib import Path

    root = Path(__file__).parent.parent
    js = (root / "static" / "js" / "bot_focus.js").read_text(encoding="utf-8")
    config_js = (root / "static" / "js" / "config.js").read_text(encoding="utf-8")

    for endpoint in ("FLEET_CANCEL_ALL", "FLEET_FLATTEN_ALL", "FLEET_CLEAR", "FLEET_STATUS"):
        assert f"{endpoint}: '/api/bot-control/fleet/" in config_js

    start = js.index("const FLEET_ACTION_CONFIG")
    end = js.index("Reset bot runtime override to config baseline")
    fleet_js = js[start:end]

    for token in ("confirm_fleet_cancel_all", "confirm_fleet_flatten_all", "confirm_fleet_clear"):
        assert token in fleet_js
    assert "'FLATTEN ALL'" in fleet_js
    assert "'CLEAR'" in fleet_js
    assert "scope: 'ALL'" in fleet_js
    assert "requested_by: 'dashboard'" in fleet_js
    assert fleet_js.count("credentials: 'include'") >= 2
    assert "function refreshFleetStatus" in fleet_js
    assert "function renderFleetStatus" in fleet_js
    assert "function executeFleetAction" in fleet_js
    assert "function validateFleetActionConfirmation" in fleet_js
    assert "alert(" not in fleet_js
    assert "confirm(" not in fleet_js

    # Distinct per-action gating: cancel needs only its checkbox; flatten/clear
    # additionally require a typed token and a distinct server-side body token.
    assert "bodyToken: 'confirm_fleet_cancel_all'" in fleet_js
    assert "bodyToken: 'confirm_fleet_flatten_all'" in fleet_js
    assert "token: 'FLATTEN ALL'" in fleet_js
    assert "bodyToken: 'confirm_fleet_clear'" in fleet_js
    assert "token: 'CLEAR'" in fleet_js
    assert "btn.disabled = !confirmed" in fleet_js
    assert "finally {" in fleet_js
    assert "validateFleetActionConfirmation(key));" in fleet_js
    assert "refreshBtn.disabled = false" in fleet_js

    # Fail-closed diagnostics must be visible to the operator, not swallowed.
    assert "addLine('status_error', status.error)" in fleet_js
    assert "addLine('command_executable', status.command_executable)" in fleet_js
    assert "e.key !== 'Escape'" in fleet_js


def test_fleet_control_assets_are_cache_busted():
    """config.js changes must bump its query string or browsers keep stale endpoints."""
    from pathlib import Path

    template = (Path(__file__).parent.parent / "templates" / "bot_focus.html").read_text(
        encoding="utf-8"
    )
    config_js = (Path(__file__).parent.parent / "static" / "js" / "config.js").read_text(
        encoding="utf-8"
    )
    assert all(
        f"FLEET_{name}: '/api/bot-control/fleet/" in config_js
        for name in ("CANCEL_ALL", "FLATTEN_ALL", "CLEAR", "STATUS")
    )

    def version_of(asset):
        marker = f"/static/js/{asset}.js?v="
        start = template.index(marker) + len(marker)
        end = template.index('"', start)
        return tuple(int(part) for part in template[start:end].split("."))

    assert version_of("config") >= (2, 8, 0)
    assert version_of("bot_focus") >= (2, 9, 8)
