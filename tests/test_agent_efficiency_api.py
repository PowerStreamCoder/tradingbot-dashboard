#!/usr/bin/env python3
"""
Unit tests for Agent Efficiency Monitoring endpoints in main.py (Phase 2 / Issue #139).

Covers:
  - Auth gating for UI page (/agent-efficiency -> login page if unauthenticated)
  - Auth gating for API endpoints (/api/agent-efficiency/* -> 401 if unauthenticated)
  - GET /agent-efficiency (serves HTML page when authenticated, 404 when missing)
  - GET /api/agent-efficiency/summary
  - GET /api/agent-efficiency/bot/{symbol}
  - GET /api/agent-efficiency/history
  - GET /api/agent-efficiency/alerts
  - POST /api/agent-efficiency/daily-digest
  - Singleton getter/setter functions (get_agent_efficiency_service, set_agent_efficiency_service)
"""

import datetime
from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient

import main
from services.agent_efficiency_service import AgentEfficiencyService

SESSION_ID = "agent-eff-test-session-12345"
FUTURE = datetime.datetime.now() + datetime.timedelta(hours=2)


@pytest.fixture
def client():
    # Setup authenticated session
    main.authenticated_sessions[SESSION_ID] = FUTURE
    c = TestClient(main.app)
    yield c
    main.authenticated_sessions.pop(SESSION_ID, None)


@pytest.fixture
def mock_service():
    service = MagicMock(spec=AgentEfficiencyService)
    service._db = MagicMock()
    service.get_summary.return_value = {
        "timestamp": "2026-11-21T12:00:00Z",
        "active_bots": ["NVDA", "IWM"],
        "fleet_health": "HEALTHY",
        "aggregate_alpha": {
            "mean_rolling_score": 0.42,
            "r_multiple_retention_rate": 0.88,
            "giveback_preservation_score": 0.76,
            "break_even_conversion_rate": 0.65,
        },
        "agent_scores": {},
        "sla_metrics": {},
        "active_alerts_count": 0,
    }
    service.get_bot_efficiency.return_value = {
        "symbol": "NVDA",
        "parameters": {},
        "scores": {},
        "recent_actions": [],
    }
    service.get_history.return_value = {
        "agent": "all",
        "window_days": 7,
        "timestamps": ["2026-11-20T00:00:00Z"],
        "rolling_scores": [0.4],
        "latency_p95": [120.0],
    }
    service.get_alerts.return_value = {
        "alerts": []
    }

    # Inject mock into main
    original_service = main._agent_efficiency_service_instance
    main.set_agent_efficiency_service(service)
    yield service
    main.set_agent_efficiency_service(original_service)


# ---------------------------------------------------------------------------
# Auth Gating Tests
# ---------------------------------------------------------------------------

def test_agent_efficiency_page_unauthenticated(client):
    """Unauthenticated access to /agent-efficiency should return the login page HTML."""
    response = client.get("/agent-efficiency")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Trading Dashboard - Access Code Required" in response.text


def test_agent_efficiency_api_unauthenticated(client):
    """Unauthenticated access to /api/agent-efficiency/* should return 401."""
    endpoints = [
        "/api/agent-efficiency/summary",
        "/api/agent-efficiency/bot/NVDA",
        "/api/agent-efficiency/history",
        "/api/agent-efficiency/alerts",
    ]
    for ep in endpoints:
        resp = client.get(ep)
        assert resp.status_code == 401
        assert resp.json() == {"detail": "Authentication required"}

    post_resp = client.post("/api/agent-efficiency/daily-digest")
    assert post_resp.status_code == 401


# ---------------------------------------------------------------------------
# Page Serving Tests
# ---------------------------------------------------------------------------

def test_agent_efficiency_page_authenticated(client):
    """Authenticated request should serve agent_efficiency.html."""
    response = client.get("/agent-efficiency", cookies={"dashboard_session": SESSION_ID})
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Agent Efficiency" in response.text


def test_agent_efficiency_page_not_found(client):
    """If template file does not exist, return 404."""
    with patch("os.path.exists", return_value=False):
        response = client.get("/agent-efficiency", cookies={"dashboard_session": SESSION_ID})
        assert response.status_code == 404
        assert "Agent efficiency page not found" in response.json().get("detail", "")


# ---------------------------------------------------------------------------
# API Endpoint Tests
# ---------------------------------------------------------------------------

def test_get_agent_efficiency_summary_success(client, mock_service):
    """GET /api/agent-efficiency/summary returns summary payload and headers."""
    response = client.get("/api/agent-efficiency/summary", cookies={"dashboard_session": SESSION_ID})
    assert response.status_code == 200
    data = response.json()
    assert data["fleet_health"] == "HEALTHY"
    assert data["aggregate_alpha"]["mean_rolling_score"] == 0.42
    assert response.headers.get("cache-control") == "no-cache, no-store, must-revalidate"
    mock_service.get_summary.assert_called_once()


def test_get_agent_efficiency_summary_error(client, mock_service):
    """GET /api/agent-efficiency/summary handles exceptions with 500."""
    mock_service.get_summary.side_effect = RuntimeError("Firestore unavailable")
    response = client.get("/api/agent-efficiency/summary", cookies={"dashboard_session": SESSION_ID})
    assert response.status_code == 500
    assert "Failed to fetch efficiency summary" in response.json().get("detail", "")


def test_get_bot_agent_efficiency_success(client, mock_service):
    """GET /api/agent-efficiency/bot/{symbol} returns bot-level data."""
    response = client.get("/api/agent-efficiency/bot/nvda", cookies={"dashboard_session": SESSION_ID})
    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "NVDA"
    assert response.headers.get("cache-control") == "no-cache, no-store, must-revalidate"
    mock_service.get_bot_efficiency.assert_called_once_with(symbol="NVDA")


def test_get_bot_agent_efficiency_empty_symbol(client, mock_service):
    """GET /api/agent-efficiency/bot/{symbol} with whitespace returns 400."""
    response = client.get("/api/agent-efficiency/bot/%20", cookies={"dashboard_session": SESSION_ID})
    assert response.status_code == 400
    assert "Symbol parameter is required" in response.json().get("detail", "")


def test_get_bot_agent_efficiency_error(client, mock_service):
    """GET /api/agent-efficiency/bot/{symbol} handles service failure with 500."""
    mock_service.get_bot_efficiency.side_effect = ValueError("Symbol parse error")
    response = client.get("/api/agent-efficiency/bot/NVDA", cookies={"dashboard_session": SESSION_ID})
    assert response.status_code == 500
    assert "Failed to fetch bot efficiency" in response.json().get("detail", "")


def test_get_agent_efficiency_history_success(client, mock_service):
    """GET /api/agent-efficiency/history with query parameters."""
    response = client.get(
        "/api/agent-efficiency/history?agent=AdaptivePnLAgent&window=14",
        cookies={"dashboard_session": SESSION_ID}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["agent"] == "all"  # mock returned value
    assert response.headers.get("cache-control") == "no-cache, no-store, must-revalidate"
    mock_service.get_history.assert_called_once_with(agent="AdaptivePnLAgent", window_days=14)


def test_get_agent_efficiency_history_clamped_window(client, mock_service):
    """GET /api/agent-efficiency/history clamps window between 1 and 90."""
    client.get("/api/agent-efficiency/history?window=-5", cookies={"dashboard_session": SESSION_ID})
    mock_service.get_history.assert_called_with(agent="all", window_days=1)

    client.get("/api/agent-efficiency/history?window=150", cookies={"dashboard_session": SESSION_ID})
    mock_service.get_history.assert_called_with(agent="all", window_days=90)


def test_get_agent_efficiency_history_error(client, mock_service):
    """GET /api/agent-efficiency/history handles exceptions with 500."""
    mock_service.get_history.side_effect = RuntimeError("DB stream error")
    response = client.get("/api/agent-efficiency/history", cookies={"dashboard_session": SESSION_ID})
    assert response.status_code == 500
    assert "Failed to fetch efficiency history" in response.json().get("detail", "")


def test_get_agent_efficiency_alerts_success(client, mock_service):
    """GET /api/agent-efficiency/alerts returns active alerts."""
    response = client.get("/api/agent-efficiency/alerts", cookies={"dashboard_session": SESSION_ID})
    assert response.status_code == 200
    data = response.json()
    assert "alerts" in data
    assert response.headers.get("cache-control") == "no-cache, no-store, must-revalidate"
    mock_service.get_alerts.assert_called_once()


def test_get_agent_efficiency_alerts_error(client, mock_service):
    """GET /api/agent-efficiency/alerts handles exceptions with 500."""
    mock_service.get_alerts.side_effect = RuntimeError("Alert query failed")
    response = client.get("/api/agent-efficiency/alerts", cookies={"dashboard_session": SESSION_ID})
    assert response.status_code == 500
    assert "Failed to fetch efficiency alerts" in response.json().get("detail", "")


# ---------------------------------------------------------------------------
# Daily Digest Endpoint Tests
# ---------------------------------------------------------------------------

def test_send_agent_efficiency_daily_digest_no_webhook(client, mock_service):
    """POST /api/agent-efficiency/daily-digest generates digest without dispatch when no URL provided."""
    mock_service.generate_daily_rollup.return_value = {"date": "2026-11-21"}
    with patch("services.agent_efficiency_notifier.AgentEfficiencyNotifier.dispatch_webhook") as mock_dispatch:
        response = client.post(
            "/api/agent-efficiency/daily-digest",
            cookies={"dashboard_session": SESSION_ID}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "digest" in data
        assert "text" in data["digest"]
        assert "html" in data["digest"]
        assert data["dispatched"] is False
        assert data["rollup"] == {"date": "2026-11-21"}
        mock_dispatch.assert_not_called()
        mock_service.generate_daily_rollup.assert_called_once()


def test_send_agent_efficiency_daily_digest_with_webhook(client, mock_service):
    """POST /api/agent-efficiency/daily-digest dispatches alert when webhook URL is provided."""
    with patch("services.agent_efficiency_notifier.AgentEfficiencyNotifier.dispatch_webhook", return_value=True) as mock_dispatch:
        response = client.post(
            "/api/agent-efficiency/daily-digest?webhook_url=https://example.com/webhook",
            cookies={"dashboard_session": SESSION_ID}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["dispatched"] is True
        mock_dispatch.assert_called_once()


def test_send_agent_efficiency_daily_digest_rollup_failure_graceful(client, mock_service):
    """POST /api/agent-efficiency/daily-digest gracefully succeeds even if daily rollup persistence fails."""
    mock_service.generate_daily_rollup.side_effect = RuntimeError("Rollup DB error")
    with patch("services.agent_efficiency_notifier.AgentEfficiencyNotifier.dispatch_webhook") as mock_dispatch:
        response = client.post(
            "/api/agent-efficiency/daily-digest",
            cookies={"dashboard_session": SESSION_ID}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["rollup"] is None
        assert data["dispatched"] is False
        mock_dispatch.assert_not_called()


def test_send_agent_efficiency_daily_digest_error(client, mock_service):
    """POST /api/agent-efficiency/daily-digest handles failure with 500."""
    mock_service.get_summary.side_effect = RuntimeError("Digest build failed")
    response = client.post(
        "/api/agent-efficiency/daily-digest",
        cookies={"dashboard_session": SESSION_ID}
    )
    assert response.status_code == 500
    assert "Failed to generate daily digest" in response.json().get("detail", "")


# ---------------------------------------------------------------------------
# Singleton Getter / Setter Tests
# ---------------------------------------------------------------------------

def test_singleton_getter_and_setter():
    """Verify get_agent_efficiency_service and set_agent_efficiency_service behavior."""
    main.set_agent_efficiency_service(None)
    with patch.object(main.dashboard_data, "db", MagicMock()):
        svc1 = main.get_agent_efficiency_service()
        assert isinstance(svc1, AgentEfficiencyService)
        # Should return same instance
        svc2 = main.get_agent_efficiency_service()
        assert svc1 is svc2

    # Test re-binding db if initially None
    main.set_agent_efficiency_service(None)
    with patch.object(main.dashboard_data, "db", None):
        svc3 = main.get_agent_efficiency_service()
        assert svc3._db is None

    fake_db = MagicMock()
    with patch.object(main.dashboard_data, "db", fake_db):
        svc4 = main.get_agent_efficiency_service()
        assert svc4 is svc3
        assert svc4._db is fake_db

    main.set_agent_efficiency_service(None)
