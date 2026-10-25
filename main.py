"""
Trading Bot Dashboard Backend API
FastAPI application serving dashboard data with Firestore persistence

Version: 2.0.0 - Code review fixes applied
Features:
  - Bot and bucket overview (dynamic, up to 10 buckets per bot)
  - Trade history with bot names and bucket types
  - P&L statements (calculated client-side from trade data)
  - Date selector support for historical P&L analysis
  - Firestore persistence for indefinite data storage
  - Daily trade charts per bot with entry/exit visualization
  - Enhanced trade overview section
  - Trend indicators (upward/downward based on bucket directions)
  - Market regime/volatility display (adaptive trading configuration)
  - Configuration caching with version tracking (code review fix)
"""

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
import httpx
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Literal, Any  # v5.0.0: Added Literal for trading_mode validation
from pydantic import BaseModel
from google.cloud import firestore
import os
import re
import json
import time

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from io import BytesIO
import xlsxwriter
import secrets
from access_code import access_code as derive_access_code
from access_code import code_matches, secret_from_env
from threading import Lock
import logging

# Rate limiting
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# P&L Statement Engine
from services.pnl_statement_engine import aggregate_portfolio_pnl, PortfolioStatementResponse
from services.performance_engine import (
    calculate_engine_performance,
    EnginePerformanceResponse,
    clear_performance_cache,
)


# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)

# Initialize FastAPI app
app = FastAPI(
    title="Trading Bot Dashboard API",
    description="API for trading bot monitoring dashboard",
    version="2.0.0"
)

# Add rate limiter to app state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Bot Control API endpoint URL (default to VM IP)
BOT_CONTROL_API_URL = os.environ.get("BOT_CONTROL_API_URL", "http://34.46.214.78:8080")
BOT_CONTROL_API_KEY = os.environ.get("BOT_CONTROL_API_KEY", "")


def _bot_control_headers() -> Dict[str, str]:
    """Return headers for Bot Control API requests including API key if configured."""
    headers = {}
    if BOT_CONTROL_API_KEY:
        headers["X-API-Key"] = BOT_CONTROL_API_KEY
    return headers


# Session storage for authenticated users (Firestore-backed for persistence)
# This ensures sessions survive Cloud Run container restarts
authenticated_sessions = {}  # Local cache
sessions_ref = None  # Firestore collection reference (initialized after DashboardData)


def _now_naive() -> datetime:
    """Naive 'now' on the same basis as the naive datetimes this app stores."""
    return datetime.now()


def _as_naive_local(value) -> Optional[datetime]:
    """
    Coerce a stored session expiry to a naive datetime comparable to
    ``_now_naive()``.

    Sessions are written as naive datetimes, but Firestore hands them back
    tz-aware (DatetimeWithNanoseconds, UTC). Comparing the two raises
    TypeError, which silently disabled session persistence on restart and could
    500 POST endpoints, so every expiry comparison goes through this normalizer.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except Exception:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        value = value.astimezone().replace(tzinfo=None)
    return value


# Paths that unauthenticated MACHINE clients (the trading bots themselves) must
# be able to write, and which therefore bypass the session check.
#
# The middleware previously exempted *every* `POST /api/*` request
# (dashboard #79 / discrepancy D12), which left the order-placing endpoints
# (`/api/create-position`, `/api/close-position`, `/api/bot-control/*`,
# `/api/bot-control/switch-profile`) reachable by anyone with the URL - and none
# of those handlers checked the session themselves. The exemption is now scoped
# to the endpoints that genuinely have no browser session:
#
#   - `/api/logs/update`  - the bot's DashboardClient posts log lines here
#                           (trading/dashboard.py:826)
#
# The deprecated bot->dashboard writers (`/api/bot-overview/update`,
# `/api/trade-history/add`, `/api/pnl-statement/update`, `/api/pnl-sync`) are
# NOT exempt: bots write to Firestore directly since v2.0 and those endpoints
# actively warn when called, so leaving them authenticated surfaces any stale
# caller instead of silently trusting it. Add a path here only with evidence
# that an automated client with no session calls it.
MACHINE_WRITE_PATHS = frozenset({
    "/api/logs/update",
})


def is_valid_session(session_id: Optional[str]) -> bool:
    """
    Validate a session for API endpoints: it must exist AND still be unexpired.

    Endpoint auth checks only tested membership in `authenticated_sessions`,
    but expired entries linger in that local cache until some page GET happens
    to purge them - so POST endpoints (accept/reject/provision/run/...) happily
    kept serving requests from expired sessions.

    Mirrors the middleware's cache -> expiry -> Firestore fallback logic and
    purges expired sessions as it finds them.
    """
    if not session_id:
        return False

    expiry = _as_naive_local(authenticated_sessions.get(session_id))

    if expiry is None:
        # Not in local cache - another instance may have created it.
        if sessions_ref is None:
            return False
        try:
            session_doc = sessions_ref.document(session_id).get()
            if not session_doc.exists:
                return False
            expiry = _as_naive_local((session_doc.to_dict() or {}).get('expiry'))
        except Exception as e:
            print(f"Error checking session in Firestore: {e}")
            return False
        if expiry is None:
            return False
        if expiry <= _now_naive():
            # Expired in Firestore - clean up so it is not reloaded
            try:
                sessions_ref.document(session_id).delete()
            except Exception:
                pass
            return False
        authenticated_sessions[session_id] = expiry
        return True

    if expiry > _now_naive():
        return True

    # Expired: purge from local cache and Firestore
    print(f"Session expired: {session_id[:10]}...")
    authenticated_sessions.pop(session_id, None)
    if sessions_ref is not None:
        try:
            sessions_ref.document(session_id).delete()
        except Exception as e:
            print(f"Error deleting expired session from Firestore: {e}")
    return False

# Cache for trade history to reduce Firestore reads.
# Keyed by the lookback window (`days`) so callers requesting different windows
# never receive each other's results. The performance engine asks for 3650 days
# while the P&L statement asks for 365; sharing a single slot made one endpoint
# silently serve the other's (older or narrower) trade set.
_trade_history_cache = {
    'data': {},  # Keyed by days
    'timestamp': {},  # Keyed by days
    'ttl': {},  # Keyed by days -> TTL in seconds
    'lock': Lock()
}
TRADE_CACHE_TTL_SECONDS = 30  # Default cache TTL for 30 seconds
# Longer TTL for the heavy long-horizon performance query. It scans a wide
# window (up to the row cap) and only feeds analytics, so caching it longer
# keeps Firestore reads to a minimum while the UI shows the data "as of" time.
PERFORMANCE_TRADE_CACHE_TTL_SECONDS = 180
# Backend row cap for the trade-history query. Also reported to the performance
# engine so it can flag when a long window may be truncated by this cap.
TRADE_HISTORY_ROW_CAP = 500

# Cache for bot metrics to reduce Firestore reads
_bot_metrics_cache = {
    'data': {},  # Keyed by bot_id
    'timestamp': {},  # Keyed by bot_id
    'lock': Lock()
}
BOT_METRICS_CACHE_TTL_SECONDS = 30  # Cache for 30 seconds

# Cache for stock picks to reduce Firestore reads
_stock_picks_cache = {
    'data': None,
    'timestamp': None,
    'lock': Lock()
}
STOCK_PICKS_CACHE_TTL_SECONDS = 30  # Cache for 30 seconds

# Concurrency control for StockPicker execution
# The run flag is bound to the submitted future (see _bind_stockpicker_run) so
# it can only be released once the runner thread has ACTUALLY finished - a
# timeout on the await must not allow a second, concurrent pipeline run.
_stockpicker_running = False
_stockpicker_lock = Lock()
_stockpicker_future = None  # asyncio.Future of the in-flight run (event loop thread only)

# Shared executor: one long-lived worker instead of a new (leaked) thread pool
# per manual run.
from concurrent.futures import ThreadPoolExecutor
_stockpicker_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stockpicker-run")


def _release_stockpicker_run_flag() -> None:
    """Clear the in-flight run flag. Registered as a done-callback on the future."""
    global _stockpicker_running, _stockpicker_future
    with _stockpicker_lock:
        _stockpicker_running = False
        _stockpicker_future = None

# Rate limiting for StockPicker runs (per user session)
from collections import defaultdict
_stockpicker_run_history = defaultdict(list)  # session_id -> [timestamps]
MAX_STOCKPICKER_RUNS_PER_HOUR = 10

# Bot configuration cache (to avoid repeated file reads)
# Code review fix: Added version tracking for frontend cache invalidation
_bot_config_cache = {
    'config': None,
    'mtime': 0,
    'version': 0,  # Incremented on file changes
    'lock': Lock()
}

# Parameters cache for per-symbol bot configurations
_bot_params_cache = {
    'params': {},
    'mtimes': {},
    'lock': Lock()
}

# Metrics cache (to avoid expensive recalculation)
# Code review fix: Added caching for /api/metrics performance
_metrics_cache = {
    'data': None,
    'timestamp': 0,
    'ttl': 30,  # Cache for 30 seconds
    'lock': Lock()
}

def load_bot_configs():
    """
    Load bot configurations from bots.json with caching and version tracking.

    Returns the bots.json config with file modification time tracking.
    The config is cached and only reloaded if the file has changed.
    Version number increments on each reload for frontend cache invalidation.

    Returns:
        dict: {"bots": [...], "defaults": {...}, "version": int, "updated_at": str}

    Raises:
        FileNotFoundError: If bots.json not found
        json.JSONDecodeError: If JSON is invalid
    """
    # Note: _bot_config_cache is a module-level dict, no 'global' needed for dict modification

    # Try multiple config paths (canonical config repo first, then production bundled copy)
    config_paths = [
        os.path.join(os.path.dirname(__file__), "..", "tradingbot-config", "bots.json"),  # Canonical: sibling config repo
        os.path.join(os.path.dirname(__file__), "config", "bots.json"),  # Production fallback: bundled copy in Cloud Run image
    ]

    bots_config_path = None
    for path in config_paths:
        if os.path.exists(path):
            bots_config_path = path
            break

    if not bots_config_path:
        logger.error(f"bots.json not found in any of: {config_paths}")
        raise FileNotFoundError(f"bots.json not found. Tried: {config_paths}")

    with _bot_config_cache['lock']:
        try:
            stat = os.stat(bots_config_path)
            current_mtime = stat.st_mtime

            # Return cached config if file hasn't changed
            if _bot_config_cache['config'] is not None and _bot_config_cache['mtime'] == current_mtime:
                return _bot_config_cache['config']

            # Load fresh config
            with open(bots_config_path, 'r') as f:
                config = json.load(f)

            # Merge dynamically provisioned bots from Firestore stock_picks/current
            # This ensures provisioned bots survive Cloud Run container redeployments
            try:
                if dashboard_data and hasattr(dashboard_data, "stock_picks_ref") and dashboard_data.stock_picks_ref:
                    pdoc = dashboard_data.stock_picks_ref.document("current").get()
                    if getattr(pdoc, "exists", False):
                        pdata = pdoc.to_dict() or {}
                        already_accepted = pdata.get("already_accepted", [])
                        bots = config.get("bots", [])
                        known_client_ids = {b.get("client_id") for b in bots}
                        known_symbols = {(b.get("symbol") or "").upper() for b in bots}
                        merged_any = False
                        for p in already_accepted:
                            if p.get("status") == "PROVISIONED":
                                cid = p.get("client_id")
                                sym = (p.get("symbol") or p.get("ticker") or "").upper()
                                if cid and sym and cid not in known_client_ids and sym not in known_symbols:
                                    bot_entry = {
                                        "name": p.get("bot_id") or f"{sym.lower()}_sma",
                                        "symbol": sym,
                                        "client_id": int(cid),
                                        "script": "bots/UniversalSMABot.py",
                                        "strategy": p.get("strategy") or "sma_crossover",
                                        "enabled": True,
                                        "strategy_track": p.get("strategy_track") or "GROWTH",
                                        "initial_score": p.get("initial_score") or p.get("composite_score") or p.get("score"),
                                        "description": f"{sym} SMA 5/20 trader (provisioned via Stock Picker)"
                                    }
                                    bots.append(bot_entry)
                                    known_client_ids.add(cid)
                                    known_symbols.add(sym)
                                    merged_any = True
                        if merged_any:
                            config["bots"] = bots
                            try:
                                with open(bots_config_path, "w") as f_out:
                                    json.dump(config, f_out, indent=2)
                            except Exception:
                                pass
            except Exception as fs_err:
                logger.debug(f"Could not merge provisioned bots from Firestore: {fs_err}")

            # Increment version on reload (for frontend cache invalidation)
            _bot_config_cache['version'] += 1

            # Add version and timestamp to config
            config['version'] = _bot_config_cache['version']
            config['updated_at'] = datetime.fromtimestamp(current_mtime).isoformat()

            # Update cache
            _bot_config_cache['config'] = config
            _bot_config_cache['mtime'] = current_mtime

            logger.info(f"Loaded bot config from {bots_config_path} (mtime: {current_mtime}, version: {config['version']})")
            return config

        except FileNotFoundError:
            logger.error(f"bots.json not found at {bots_config_path}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in bots.json: {e}")
            raise

def load_bot_parameters(symbol: str) -> Optional[dict]:
    """
    Load bot parameters for a given symbol (e.g. 'IWM' or 'NVDA') from bots/{symbol.lower()}.json.
    Tries canonical config repo first, then production bundled copy.
    """
    if not symbol:
        return None
    symbol_clean = symbol.lower().strip()
    config_paths = [
        os.path.join(os.path.dirname(__file__), "..", "tradingbot-config", "bots", f"{symbol_clean}.json"),
        os.path.join(os.path.dirname(__file__), "config", "bots", f"{symbol_clean}.json"),
    ]

    file_path = None
    for p in config_paths:
        if os.path.exists(p):
            file_path = p
            break

    if not file_path:
        # Check Firestore for dynamically provisioned bot parameters
        try:
            db_client = getattr(dashboard_data, "db", None) or firestore.Client()
            bot_docs = list(db_client.collection("bots").where("symbol", "==", symbol_clean.upper()).limit(1).stream())
            if bot_docs:
                b_data = bot_docs[0].to_dict() or {}
                if b_data.get("bot_params"):
                    params = b_data["bot_params"]
                    with _bot_params_cache['lock']:
                        _bot_params_cache['params'][symbol_clean] = params
                        _bot_params_cache['mtimes'][symbol_clean] = time.time()
                    try:
                        local_path = os.path.join(os.path.dirname(__file__), "config", "bots", f"{symbol_clean}.json")
                        os.makedirs(os.path.dirname(local_path), exist_ok=True)
                        with open(local_path, "w") as f:
                            json.dump(params, f, indent=2)
                    except Exception:
                        pass
                    return params
        except Exception as e:
            logger.debug(f"Firestore lookup for {symbol_clean} bot params failed: {e}")
        return None

    with _bot_params_cache['lock']:
        try:
            mtime = os.stat(file_path).st_mtime
            if (symbol_clean in _bot_params_cache['params'] and
                _bot_params_cache['mtimes'].get(symbol_clean) == mtime):
                return _bot_params_cache['params'][symbol_clean]

            with open(file_path, 'r') as f:
                data = json.load(f)

            _bot_params_cache['params'][symbol_clean] = data
            _bot_params_cache['mtimes'][symbol_clean] = mtime
            logger.info(f"Loaded bot parameters for {symbol_clean.upper()} from {file_path}")
            return data
        except Exception as e:
            logger.error(f"Error loading bot parameters from {file_path}: {e}")
            return None

def get_client_id_for_bot(bot_id: int) -> int:
    """
    Map dashboard bot_id to trading bot client_id using bots.json.

    The dashboard uses bot_id (1, 2, 3...) for display purposes.
    The trading bots use client_id (3, 4, 5...) as their Firestore document ID.

    This function dynamically loads the mapping from bots.json instead of
    hardcoding it, making the dashboard truly multi-bot capable.

    Args:
        bot_id: Dashboard bot ID (e.g., 1)

    Returns:
        int: Trading bot client_id (e.g., 3)

    Note:
        If bot_id not found in config, returns bot_id unchanged as fallback.
        This maintains backward compatibility with manual bot configurations.
    """
    try:
        config = load_bot_configs()
        bots = config.get('bots', [])

        # Build mapping: dashboard uses 1-indexed bot_id, config has client_id
        # We'll match by position in the bots array for now
        # bot_id=1 maps to first bot's client_id, bot_id=2 to second, etc.

        if isinstance(bot_id, int) and 1 <= bot_id <= len(bots):
            bot_config = bots[bot_id - 1]  # Convert 1-indexed to 0-indexed
            client_id = bot_config.get('client_id', bot_id)
            logger.debug(f"Mapped bot_id={bot_id} to client_id={client_id} (symbol: {bot_config.get('symbol')})")
            return client_id
        else:
            logger.warning(f"bot_id={bot_id} not found in config, using as client_id (fallback)")
            return bot_id

    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Failed to load bot config, using bot_id={bot_id} as client_id: {e}")
        return bot_id

def resolve_bot_target(payload: dict) -> dict:
    """
    Resolve an order-routing request to one specific bot.

    Accepts either:
      * ``client_id`` (preferred, unambiguous) — the trading bot's client_id
        (e.g. 3..7), which is also its Firestore document suffix
        (``bots/bot_<client_id>``); or
      * ``bot_id`` (legacy) — the dashboard's 1-indexed position in ``bots.json``.

    Why the legacy form is dangerous: the bot-focus UI already holds a
    ``client_id`` (``CONFIG.botId = selectedBot.client_id``) but posted it as
    ``bot_id``, which ``get_client_id_for_bot`` re-interpreted as a 1-indexed
    position — so "create position" on the NVDA page wrote to ``bot_6`` (CAT)
    and was rejected only because CAT refuses a mismatched symbol. client_ids
    3..5 are *also* valid 1-indexed positions, so the two forms are genuinely
    ambiguous for those values.

    To make a wrong target impossible to route silently, the resolved bot's
    configured symbol is cross-checked against the requested symbol and any
    mismatch is refused.

    Returns:
        {"client_id": int, "symbol": str, "bot_name": str, "source": str}

    Raises:
        HTTPException: 400 when the request cannot be resolved to a known bot or
            the requested symbol does not match that bot's configuration;
            500 when ``bots.json`` cannot be read.
    """
    try:
        bots = load_bot_configs().get("bots", []) or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load bot config: {e}")

    requested_symbol = str(payload.get("symbol") or "").strip().upper()

    client_id = payload.get("client_id")
    source = "client_id"
    if client_id is None:
        bot_id = payload.get("bot_id")
        if bot_id is None:
            raise HTTPException(
                status_code=400,
                detail="Missing required field: client_id (or legacy bot_id)",
            )
        client_id = get_client_id_for_bot(bot_id)
        source = "bot_id(1-indexed legacy)"

    if not isinstance(client_id, int) or isinstance(client_id, bool):
        raise HTTPException(status_code=400, detail=f"Invalid client_id: {client_id!r}")

    match = next((b for b in bots if b.get("client_id") == client_id), None)
    if match is None:
        known = ", ".join(f"{b.get('symbol')}={b.get('client_id')}" for b in bots) or "none"
        raise HTTPException(
            status_code=400,
            detail=f"Unknown bot client_id={client_id}. Configured bots: {known}",
        )

    configured_symbol = str(match.get("symbol") or "").strip().upper()
    if not configured_symbol:
        raise HTTPException(
            status_code=500, detail=f"Bot client_id={client_id} has no symbol configured"
        )

    if requested_symbol and requested_symbol != configured_symbol:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Symbol mismatch: request is for {requested_symbol} but client_id={client_id} "
                f"trades {configured_symbol} (bot '{match.get('name')}'). Refusing to route the order."
            ),
        )

    return {
        "client_id": client_id,
        "symbol": configured_symbol,
        "bot_name": match.get("name"),
        "source": source,
    }

def get_valid_access_code():
    """Get today's access code.

    The derivation lives in ``access_code.py`` — the same module the
    operator's helper script (``scripts/dashboard_access_code.py``) uses — so
    the code printed for the operator and the code the server accepts can
    never disagree.

    With ``DASHBOARD_ACCESS_SECRET`` configured the code is derived from that
    secret; without it, it falls back to the legacy ``ddmmyy`` date code.
    """
    return derive_access_code(secret_from_env())

def get_login_page():
    """Generate login page HTML"""
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Trading Dashboard - Access Code Required</title>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
        }
        .login-container {
            background: white;
            padding: 40px;
            border-radius: 10px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.2);
            text-align: center;
            max-width: 400px;
            width: 90%;
        }
        h1 {
            color: #34495e;
            margin-bottom: 10px;
        }
        .subtitle {
            color: #7f8c8d;
            margin-bottom: 30px;
        }
        .hint {
            background: #ecf0f1;
            padding: 15px;
            border-radius: 5px;
            margin-bottom: 20px;
            font-size: 0.9em;
            color: #555;
        }
        input {
            width: 100%;
            padding: 12px;
            font-size: 16px;
            border: 2px solid #ddd;
            border-radius: 5px;
            margin-bottom: 20px;
            box-sizing: border-box;
            text-align: center;
            letter-spacing: 2px;
        }
        button {
            width: 100%;
            padding: 12px;
            font-size: 16px;
            background-color: #27ae60;
            color: white;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            transition: background-color 0.3s;
        }
        button:hover {
            background-color: #229954;
        }
        .error {
            color: #e74c3c;
            margin-top: 15px;
            display: none;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <h1>🔒 Trading Dashboard</h1>
        <p class="subtitle">Enter 6-digit Google Authenticator code</p>
        <form id="loginForm">
            <input type="text" id="accessCode" placeholder="Enter 6-digit code" maxlength="6" pattern="[0-9]{6}" inputmode="numeric" required autofocus>
            <button type="submit">Access Dashboard</button>
        </form>
        <div class="error" id="errorMsg">❌ Invalid code. Check Google Authenticator and try again.</div>
    </div>

    <script>
        document.getElementById('loginForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const code = document.getElementById('accessCode').value.trim().toUpperCase();
            const errorMsg = document.getElementById('errorMsg');

            try {
                const response = await fetch('/authenticate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ access_code: code })
                });

                if (response.ok) {
                    window.location.href = '/dashboard';
                } else {
                    errorMsg.style.display = 'block';
                    document.getElementById('accessCode').value = '';
                }
            } catch (error) {
                errorMsg.style.display = 'block';
            }
        });
    </script>
</body>
</html>"""

# Middleware to check access code
@app.middleware("http")
async def check_access_code(request: Request, call_next):
    """Check if user has valid access code (today's date in ddmmyy format)"""

    # Skip authentication for static files, authentication endpoint, health check, bot POST requests, and logs API
    if (request.url.path.startswith("/static") or
        request.url.path == "/authenticate" or
        request.url.path == "/api/health" or
        request.url.path == "/api/bot-configs" or  # Allow bot configs for dashboard initialization
        request.url.path.startswith("/api/bot-params") or  # Allow bot parameter inspection
        request.url.path.startswith("/api/historical-bars/") or  # Allow historical bars for charts
        request.url.path == "/api/logs" or  # Allow logs API for frontend
        request.url.path == "/api/learning-candidates" or  # Allow learning candidate LISTING (GET); decisions are POST and require auth
        request.url.path == "/api/learning-candidates/stats" or  # Allow learning stats (GET)
        request.url.path.startswith("/api/option-events/") or  # Allow option events API for modal
        request.url.path == "/api/bot-control/current-profile" or  # Allow read-only profile inspection
        request.url.path in MACHINE_WRITE_PATHS or  # Allow bot-internal writes (see MACHINE_WRITE_PATHS)
        (request.method in ("OPTIONS",))):  # Allow CORS preflight
        # Allow bot POST requests, health checks, logs, learning endpoints, option events, and bot control without authentication
        return await call_next(request)

    # Check if session cookie exists
    session_id = request.cookies.get("dashboard_session")

    # If session exists, check if valid (first check local cache, then Firestore if missing)
    if session_id:
        # Check local cache first
        if session_id in authenticated_sessions:
            session_expiry = _as_naive_local(authenticated_sessions[session_id])
            if session_expiry and session_expiry > _now_naive():
                # Session still valid - allow access
                print(f"Valid session (cache): {session_id[:10]}... for path: {request.url.path}")
                return await call_next(request)
            else:
                # Session expired, remove it
                print(f"Session expired: {session_id[:10]}...")
                authenticated_sessions.pop(session_id, None)
                # Also delete from Firestore
                try:
                    sessions_ref.document(session_id).delete()
                except Exception as e:
                    print(f"Error deleting expired session from Firestore: {e}")
        else:
            # Not in local cache, check Firestore (might have been created by another instance)
            try:
                session_doc = sessions_ref.document(session_id).get()
                if session_doc.exists:
                    session_data = session_doc.to_dict()
                    session_expiry = _as_naive_local(session_data.get('expiry'))
                    if session_expiry and session_expiry > _now_naive():
                        # Valid session from Firestore, add to local cache
                        authenticated_sessions[session_id] = session_expiry
                        print(f"Valid session (Firestore): {session_id[:10]}... for path: {request.url.path}")
                        return await call_next(request)
            except Exception as e:
                print(f"Error checking session in Firestore: {e}")

        print(f"No valid session for path: {request.url.path}, cookie: {session_id[:10]}...")

    # If trying to access main page or dashboard without auth, show login
    if (
        request.url.path in ["/", "/dashboard", "/bot-focus", "/nvda-focus", "/pnl-reporting", "/learning-review", "/governance-review"]
        or request.url.path.startswith("/bot/")
    ):
        return HTMLResponse(content=get_login_page(), status_code=200)

    # For API endpoints without auth, return 401 (any method: GET/DELETE/POST/PUT)
    if request.url.path.startswith("/api"):
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required"}
        )

    return await call_next(request)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Get the directory paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

# Mount static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Data Models
class BucketData(BaseModel):
    """Bucket status data model for active trading positions."""
    # Original fields (v1.0)
    referencePriceBefore: Optional[float] = None
    entryPrice: Optional[float] = None
    referencePriceAfter: Optional[float] = None

    # Position basics (v3.1.3)
    side: Optional[str] = None  # "long" or "short"
    quantity: Optional[float] = None  # Number of shares
    entryTime: Optional[str] = None  # ISO timestamp
    currentPrice: Optional[float] = None  # Live price

    # P&L tracking (v3.1.3)
    unrealizedPnL: Optional[float] = None  # Live P&L in dollars
    unrealizedPnLPct: Optional[float] = None  # Live P&L as percentage

    # Exit targets (v3.1.3)
    profitTargetPrice: Optional[float] = None
    stopLossPrice: Optional[float] = None
    trailingStopPrice: Optional[float] = None

    # Market context (v3.1.3)
    regime: Optional[str] = None  # "trending", "sideways", etc.

    # Covered call info (v3.1.3)
    optionSold: Optional[bool] = None
    optionStrike: Optional[float] = None
    optionExpiration: Optional[str] = None  # ISO timestamp
    optionPremium: Optional[float] = None  # Premium collected

    # Covered call monitoring (v3.1.4)
    optionDaysToExpiry: Optional[int] = None  # Days remaining
    optionBuybackThreshold: Optional[float] = None  # Price threshold
    optionApproachingStrike: Optional[bool] = None  # Alert flag

class RegimeData(BaseModel):
    """
    Market regime information for adaptive trading.

    Sent by bots using adaptive configuration to display volatility-based
    parameter adjustments on the dashboard.

    Attributes:
        regime: Market condition ("trending", "sideways", "low_volatility")
        volatility: Standard deviation of returns (e.g., 0.004 = 0.4%)
        confidence: Classification confidence score (0.0 to 1.0)
    """
    regime: Optional[str] = None
    volatility: Optional[float] = None
    confidence: Optional[float] = None

class BotData(BaseModel):
    bucket1: BucketData
    bucket2: BucketData
    bucket3: BucketData
    bucket4: BucketData
    bucket5: BucketData

class Trade(BaseModel):
    """Trade record model with bot identification and metadata."""
    referencePriceBefore: float
    entryPrice: float
    referencePriceAfter: float
    exitPrice: float
    timestamp: str  # ISO format datetime
    profitLoss: float  # Net P/L in USD
    botId: Optional[int] = None  # Bot ID (e.g., 1=NVDA, 2=MSFT - dynamically assigned)
    botName: Optional[str] = None  # Bot name for display
    bucketType: Optional[str] = None  # "Upward" (long) or "Downward" (short)
    pnl_sync: Optional[Dict] = None  # Real-time P&L reconciliation with IBKR
    validation: Optional[Dict] = None  # P&L validation data

    # Trading Mode Tracking (v5.0.0) - Distinguishes paper vs live trades for dashboard filtering
    # Optional with "paper" default ensures backward compatibility with existing trades in Firestore
    # Literal type validation prevents invalid values (only "paper" or "live" allowed)
    # Values: "paper" (simulated trading) or "live" (real money)
    trading_mode: Optional[Literal["paper", "live"]] = "paper"

class PnLBotData(BaseModel):
    trades: int
    pnl: float

class PnLPeriod(BaseModel):
    bot1: PnLBotData
    bot2: PnLBotData
    total: PnLBotData

class PnLStatement(BaseModel):
    day: PnLPeriod
    week: PnLPeriod
    month: PnLPeriod

# In-memory data storage with Firestore persistence
class DashboardData:
    def __init__(self):
        print("Initializing DashboardData...")
        try:
            # Initialize Firestore client (uses default project from Cloud Run environment)
            self.db = firestore.Client()
            print("Firestore client initialized")

            # Collections
            self.bot_data_ref = self.db.collection('bot_overview')
            self.trades_ref = self.db.collection('trades')
            self.logs_ref = self.db.collection('bot_logs')
            self.stock_picks_ref = self.db.collection('stock_picks')  # StockPicker integration
            self.execution_metrics_ref = self.db.collection('execution_metrics')

            # Load data from Firestore on startup
            self.bot_data = self._load_bot_data()
            self.trades = self._load_trades()
            self.logs = self._load_logs()

            print(f"Loaded {len(self.trades)} trades and {len(self.bot_data)} bots")
        except Exception as e:
            print(f"ERROR initializing DashboardData: {e}")
            # Initialize with empty data if Firestore fails
            self.bot_data = {}
            self.trades = []
            self.logs = {}

    def _load_bot_data(self) -> Dict:
        """Load bot overview data from Firestore"""
        try:
            doc = self.bot_data_ref.document('current').get()
            if doc.exists:
                return doc.to_dict().get('data', {})
        except Exception as e:
            print(f"Error loading bot data from Firestore: {e}")
        return {}

    def _save_bot_data(self):
        """Save bot overview data to Firestore"""
        try:
            self.bot_data_ref.document('current').set({
                'data': self.bot_data,
                'updated_at': firestore.SERVER_TIMESTAMP
            })
        except Exception as e:
            print(f"Error saving bot data to Firestore: {e}")

    def _load_trades(self, limit: int = 25) -> List[Dict]:
        """Load trades from Firestore

        Args:
            limit: Number of trades to load (default 25 for display)
        """
        try:
            trades_query = self.trades_ref.order_by('timestamp', direction=firestore.Query.DESCENDING).limit(limit)
            trades = []
            for doc in trades_query.stream():
                trade = doc.to_dict()

                # Attach doc ID if available
                doc_id_val = getattr(doc, 'id', None)
                if doc_id_val and isinstance(doc_id_val, str):
                    trade['id'] = doc_id_val
                    trade['doc_id'] = doc_id_val

                # Ensure trade_id is available at top level
                if 'trade_id' not in trade and 'analysis_metadata' in trade and isinstance(trade.get('analysis_metadata'), dict):
                    trade['trade_id'] = trade['analysis_metadata'].get('trade_id')

                # Filter out test trades (TEST, TESTBOT symbols)
                # Check both 'symbol' and 'botName' fields (tests use botName)
                symbol = trade.get('symbol', '').upper()
                bot_name = trade.get('botName', '').upper()
                if symbol in ('TEST', 'TESTBOT') or bot_name in ('TEST', 'TESTBOT'):
                    continue  # Skip test trades

                # Map sequence fields to seqNum for frontend compatibility
                # Prioritize sequence_display (new MMXXXX format), fallback to sequence_number (old format)
                if 'sequence_display' in trade:
                    trade['seqNum'] = trade['sequence_display']
                elif 'sequence_number' in trade:
                    trade['seqNum'] = trade['sequence_number']
                elif 'seqNum' not in trade:
                    trade['seqNum'] = '?'  # Fallback for old trades
                trades.append(trade)
            return trades
        except Exception as e:
            print(f"Error loading trades from Firestore: {e}")
            return []

    def _load_execution_metrics(self, limit: int = 500) -> List[Dict]:
        """Load execution metrics snapshots or records from Firestore if collection exists."""
        try:
            if not hasattr(self, 'execution_metrics_ref') or self.execution_metrics_ref is None:
                return []
            try:
                docs = list(self.execution_metrics_ref.order_by('ts', direction=firestore.Query.DESCENDING).limit(limit).stream())
            except Exception:
                try:
                    docs = list(self.execution_metrics_ref.order_by('timestamp', direction=firestore.Query.DESCENDING).limit(limit).stream())
                except Exception:
                    docs = list(self.execution_metrics_ref.limit(limit).stream())
            results = [doc.to_dict() for doc in docs if doc.exists]
            results.sort(key=lambda d: str(d.get("ts") or d.get("timestamp") or ""), reverse=True)
            return results
        except Exception as e:
            logger.debug(f"Execution metrics not available in Firestore: {e}")
            return []

    def _load_logs(self) -> Dict:
        """Load bot logs from Firestore (last 15 lines per bot)"""
        logs = {}
        try:
            for doc in self.logs_ref.stream():
                bot_id = doc.id
                log_data = doc.to_dict()
                logs[bot_id] = log_data.get('lines', [])[-15:]
        except Exception as e:
            print(f"Error loading logs from Firestore: {e}")
        return logs

    def _save_logs(self, bot_id: str, log_lines: List[str]):
        """Save bot logs to Firestore"""
        try:
            self.logs_ref.document(str(bot_id)).set({
                'lines': log_lines[-15:],  # Keep only last 15 lines
                'updated_at': firestore.SERVER_TIMESTAMP
            })
        except Exception as e:
            print(f"Error saving logs for bot {bot_id} to Firestore: {e}")


    def calculate_pnl(self) -> Dict:
        """
        Calculate P&L from trade history for current periods.
        Note: v1.2.0+ calculates P&L client-side for date selector support.
        This endpoint remains for backward compatibility but is not used by the frontend.
        """
        now = datetime.now()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = day_start - timedelta(days=day_start.weekday())
        month_start = day_start.replace(day=1)

        # Initialize P/L structure
        pnl = {
            "day": {"bot1": {"trades": 0, "pnl": 0.0}, "bot2": {"trades": 0, "pnl": 0.0}, "total": {"trades": 0, "pnl": 0.0}},
            "week": {"bot1": {"trades": 0, "pnl": 0.0}, "bot2": {"trades": 0, "pnl": 0.0}, "total": {"trades": 0, "pnl": 0.0}},
            "month": {"bot1": {"trades": 0, "pnl": 0.0}, "bot2": {"trades": 0, "pnl": 0.0}, "total": {"trades": 0, "pnl": 0.0}}
        }

        # Process each trade
        for trade in self.trades:
            try:
                trade_time = datetime.fromisoformat(trade["timestamp"].replace('Z', '+00:00')).replace(tzinfo=None)
                bot_id = trade.get("botId", 1)  # Default to bot1 if not specified
                bot_key = f"bot{bot_id}"
                pnl_value = trade.get("profitLoss", 0.0)

                # Day P/L
                if trade_time >= day_start:
                    pnl["day"][bot_key]["trades"] += 1
                    pnl["day"][bot_key]["pnl"] += pnl_value
                    pnl["day"]["total"]["trades"] += 1
                    pnl["day"]["total"]["pnl"] += pnl_value

                # Week P/L
                if trade_time >= week_start:
                    pnl["week"][bot_key]["trades"] += 1
                    pnl["week"][bot_key]["pnl"] += pnl_value
                    pnl["week"]["total"]["trades"] += 1
                    pnl["week"]["total"]["pnl"] += pnl_value

                # Month P/L
                if trade_time >= month_start:
                    pnl["month"][bot_key]["trades"] += 1
                    pnl["month"][bot_key]["pnl"] += pnl_value
                    pnl["month"]["total"]["trades"] += 1
                    pnl["month"]["total"]["pnl"] += pnl_value

            except Exception as e:
                print(f"Error processing trade: {e}")
                continue

        # Round all P/L values to 2 decimal places
        for period in ["day", "week", "month"]:
            for key in ["bot1", "bot2", "total"]:
                pnl[period][key]["pnl"] = round(pnl[period][key]["pnl"], 2)

        return pnl

    def get_pnl_data(self) -> Dict:
        """Alias for calculate_pnl for backward compatibility."""
        return self.calculate_pnl()

# Initialize dashboard data
dashboard_data = DashboardData()

# Initialize Firestore sessions collection
sessions_ref = dashboard_data.db.collection('dashboard_sessions')

# Load existing sessions from Firestore on startup
def load_sessions_from_firestore():
    """Load valid sessions from Firestore on startup"""
    # Note: authenticated_sessions is a module-level dict, no 'global' needed for dict modification
    try:
        now = _now_naive()
        for doc in sessions_ref.stream():
            session_data = doc.to_dict()
            expiry = _as_naive_local(session_data.get('expiry'))
            if expiry and expiry > now:
                authenticated_sessions[doc.id] = expiry
        print(f"Loaded {len(authenticated_sessions)} active sessions from Firestore")
    except Exception as e:
        print(f"Error loading sessions from Firestore: {e}")

load_sessions_from_firestore()

# API Endpoints

@app.post("/authenticate")
async def authenticate(request: Request):
    """Authenticate user with access code"""
    try:
        body = await request.json()
        access_code = body.get("access_code", "")

        # Validate the access code. The comparison is constant-time, and the
        # expected code is derived from the configured secret - a date-only
        # code is a shared convention, not a credential (see access_code.py).
        if code_matches(access_code, secret_from_env()):
            # Generate session ID
            session_id = secrets.token_urlsafe(32)
            # Kept on the same naive-time basis as every other session datetime
            # so comparisons via _as_naive_local() stay consistent.
            expiry_time = _now_naive() + timedelta(hours=24)

            # Store session in memory (expires in 24 hours)
            authenticated_sessions[session_id] = expiry_time

            # Also persist to Firestore so it survives container restarts
            try:
                sessions_ref.document(session_id).set({
                    'expiry': expiry_time,
                    'created_at': firestore.SERVER_TIMESTAMP
                })
            except Exception as e:
                print(f"Error saving session to Firestore: {e}")

            print(f"User authenticated with session: {session_id[:10]}...")

            # Create response with session cookie
            response = Response(content=json.dumps({"status": "success"}), media_type="application/json")
            response.set_cookie(
                key="dashboard_session",
                value=session_id,
                max_age=86400,  # 24 hours
                httponly=True,
                secure=False,  # Set to False for development, True for production HTTPS
                samesite="lax"  # Allow cookie to be sent with same-site requests
            )
            return response
        else:
            raise HTTPException(status_code=401, detail="Invalid access code")
    except Exception as e:
        print(f"Authentication error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/", response_class=HTMLResponse)
async def root():
    """Login page (handled by middleware)"""
    # This will be intercepted by middleware
    pass

@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/bot-focus", response_class=HTMLResponse)
@app.get("/nvda-focus", response_class=HTMLResponse)
async def bot_focus():
    """
    Serve the Bot Focus single-bot dashboard page (symbol-agnostic)

    Accessible via:
    - /bot-focus (primary route)
    - /dashboard (legacy alias)
    - /nvda-focus (legacy alias)
    """
    bot_focus_path = os.path.join(TEMPLATES_DIR, "bot_focus.html")
    try:
        return FileResponse(bot_focus_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Bot Focus dashboard not found")

@app.get("/bot/{bot_id}")
async def bot_legacy_redirect(bot_id: str):
    """
    Redirect legacy /bot/{bot_id} (e.g. /bot/3 or /bot/iwm_sma) to /bot-focus?bot={bot_id}
    """
    return RedirectResponse(url=f"/bot-focus?bot={bot_id}", status_code=302)

@app.get("/pnl-reporting", response_class=HTMLResponse)
async def pnl_reporting():
    """Serve the P&L Reporting page"""
    pnl_path = os.path.join(TEMPLATES_DIR, "index.html")
    try:
        return FileResponse(pnl_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="P&L Reporting page not found")

# ---------------------------------------------------------------------------
# Performance feature helpers
# ---------------------------------------------------------------------------
# The trade-history query is capped at 500 docs, so requesting the maximum
# window simply returns the most recent 500 trades while allowing long-horizon
# periods (ALL / 1Y) to reach further back than the default 365-day window.
PERFORMANCE_HISTORY_LOOKBACK_DAYS = 3650

# Benchmark price series cache: {key: {"data": Optional[List[float]], "timestamp": datetime}}
_benchmark_series_cache: Dict[str, Any] = {}
_benchmark_series_lock = Lock()
BENCHMARK_SERIES_TTL_SECONDS = 900  # 15 minutes


def _period_lookback_days(period: str) -> int:
    """Map a dashboard period identifier to a calendar-day lookback window."""
    return {
        "1D": 2,
        "1W": 8,
        "1M": 31,
        "QTD": 95,
        "YTD": 370,
        "1Y": 370,
        "ALL": PERFORMANCE_HISTORY_LOOKBACK_DAYS,
    }.get((period or "").upper(), 370)


def _fetch_benchmark_price_series(
    symbol: str, period: str, now: Optional[datetime] = None
) -> Optional[List[float]]:
    """
    Fetch a daily close price series for a benchmark symbol so the engine can
    compute *real* buy-and-hold drawdown and volatility.

    Returns None on any failure - the engine treats missing benchmark data as
    "unavailable" rather than inventing values. Cached for 15 minutes to avoid
    hammering the provider on the 30s dashboard refresh.
    """
    key = f"{symbol.upper()}_{period.upper()}"
    now = now or datetime.now(timezone.utc)
    with _benchmark_series_lock:
        entry = _benchmark_series_cache.get(key)
        if entry and (now - entry["timestamp"]).total_seconds() < BENCHMARK_SERIES_TTL_SECONDS:
            return entry["data"]

    series: Optional[List[float]] = None
    try:
        import yfinance as yf

        start = now - timedelta(days=_period_lookback_days(period) + 5)
        hist = yf.Ticker(symbol).history(
            start=start.strftime("%Y-%m-%d"),
            end=now.strftime("%Y-%m-%d"),
            interval="1d",
        )
        if hist is not None and not hist.empty:
            # Filter out NaN/invalid closes
            closes = [float(c) for c in hist["Close"].tolist() if c == c and c > 0]
            if len(closes) >= 2:
                series = closes
    except Exception as e:
        logger.warning(f"Benchmark price series fetch failed for {symbol}: {e}")

    with _benchmark_series_lock:
        _benchmark_series_cache[key] = {"data": series, "timestamp": now}
    return series


@app.get("/performance", response_class=HTMLResponse)
async def performance_page():
    """Serve the Trading Engine Performance & Benchmarking page"""
    perf_path = os.path.join(TEMPLATES_DIR, "performance.html")
    if not os.path.exists(perf_path):
        raise HTTPException(status_code=404, detail="Performance dashboard page not found")
    return FileResponse(perf_path)

@app.get("/api/pnl-statement", response_model=PortfolioStatementResponse)
async def get_pnl_statement(
    period: str = "YTD",
    bot: str = "ALL",
    mode: str = "all",
    benchmark: str = "SPY",
    response: Response = None
):
    """
    Get Institutional Multi-Bot P&L Statement and Portfolio Analytics.
    Aggregates closed trades, active buckets, risk metrics, cashflow waterfall, and visual series.
    Supports filtering by period, bot, and trading mode (paper, live, or all/both).
    """
    try:
        # Load trades using existing trade history logic (lookback 365 days)
        trade_res = await get_all_trade_history(days=365, response=None)
        trades_list = trade_res.get("trades", []) if isinstance(trade_res, dict) else []

        # Load bot configs
        configs = load_bot_configs().get("bots", [])

        # Load bot overview data
        bot_overview_raw = dashboard_data._load_bot_data() or {}

        # Load account overview if exists in Firestore
        account_overview = None
        try:
            acct_doc = dashboard_data.db.collection('account_overview').document('current').get()
            if acct_doc.exists:
                account_overview = acct_doc.to_dict()
        except Exception:
            pass

        statement = aggregate_portfolio_pnl(
            trades=trades_list,
            bot_overview_data=bot_overview_raw,
            bot_configs=configs,
            account_overview_data=account_overview,
            period=period,
            bot_scope=bot,
            mode=mode,
            benchmark_symbol=benchmark
        )


        if response:
            cache_status = "HIT" if statement.data_quality.cached else "MISS"
            response.headers["X-Cache-Status"] = f"{cache_status} (age: {statement.data_quality.cache_age_seconds}s)"

        return statement
    except Exception as e:
        logger.error(f"Error generating P&L statement: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate P&L statement: {str(e)}")


@app.get("/api/engine-performance", response_model=EnginePerformanceResponse)
async def get_engine_performance_endpoint(
    period: str = "YTD",
    bot: str = "ALL",
    mode: str = "all",
    benchmark: str = "SPY",
    force: bool = False,
    response: Response = None
):
    """
    Get comprehensive Trading Engine Performance, Scorecard, and Public Benchmarks.
    Evaluates execution quality, risk-adjusted returns, per-bot attribution, and diagnostic levers.

    Pass ``force=true`` to bypass the analysis/trade/benchmark caches and recompute
    from the latest Firestore data (used by the dashboard's manual Refresh).
    """
    try:
        if force:
            # Manual refresh: drop the analysis cache, this window's trade cache
            # and the benchmark series so the caller never sees stale data.
            clear_performance_cache()
            with _trade_history_cache['lock']:
                _trade_history_cache['data'].pop(PERFORMANCE_HISTORY_LOOKBACK_DAYS, None)
                _trade_history_cache['timestamp'].pop(PERFORMANCE_HISTORY_LOOKBACK_DAYS, None)
                _trade_history_cache['ttl'].pop(PERFORMANCE_HISTORY_LOOKBACK_DAYS, None)
            with _benchmark_series_lock:
                _benchmark_series_cache.clear()

        # Use the maximum window so ALL / 1Y periods can reach beyond one year
        # (the query still returns at most TRADE_HISTORY_ROW_CAP trades).
        trade_res = await get_all_trade_history(days=PERFORMANCE_HISTORY_LOOKBACK_DAYS, response=None)
        trades_list = trade_res.get("trades", []) if isinstance(trade_res, dict) else []

        configs = load_bot_configs().get("bots", [])
        bot_overview_raw = dashboard_data._load_bot_data() or {}

        account_overview = None
        try:
            acct_doc = dashboard_data.db.collection('account_overview').document('current').get()
            if acct_doc.exists:
                account_overview = acct_doc.to_dict()
        except Exception:
            pass

        # Only fetch benchmark price history when there is engine data to compare
        benchmark_series = None
        if trades_list:
            benchmark_series = _fetch_benchmark_price_series(benchmark, period)

        # Safely fetch execution metrics if collection is populated (Issue #300 / #299 transport sink)
        exec_metrics_list = None
        try:
            exec_metrics_list = dashboard_data._load_execution_metrics(limit=500)
        except Exception:
            pass

        perf_data = calculate_engine_performance(
            trades=trades_list,
            bot_overview_data=bot_overview_raw,
            bot_configs=configs,
            account_overview_data=account_overview,
            period=period,
            bot_scope=bot,
            mode=mode,
            benchmark_symbol=benchmark,
            benchmark_price_series=benchmark_series,
            history_limit=TRADE_HISTORY_ROW_CAP,
            execution_metrics_data=exec_metrics_list,
        )

        if response:
            cache_status = "HIT" if perf_data.cached else "MISS"
            response.headers["X-Cache-Status"] = f"{cache_status} (age: {perf_data.cache_age_seconds}s)"

        return perf_data
    except Exception as e:
        logger.error(f"Error generating engine performance report: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate engine performance: {str(e)}")


@app.get("/api/bot-configs")
async def get_bot_configs():
    """
    Get all bot configurations from bots.json with caching and version tracking.

    Returns the list of bots with their symbols, client IDs, and enabled status.
    This allows the dashboard to dynamically adapt to bot configuration changes.

    The response includes a version number that increments on each config reload,
    allowing the frontend to detect changes and prompt users to reload.

    Returns:
        {
            "bots": [
                {
                    "name": "iwm_sma",
                    "symbol": "IWM",
                    "client_id": 3,
                    "script": "bots/UniversalSMABot.py",
                    "strategy": "sma_crossover",
                    "enabled": true,
                    "description": "IWM SMA 5/20 crossover trader",
                    "capital_override": 35000  // Optional per-symbol capital
                }
            ],
            "version": 1,
            "updated_at": "2026-08-19T10:30:00"
        }

    Error cases:
        - If bots.json not found: returns {"bots": [], "error": "Configuration file not found", "version": 0}
        - If JSON parse error: returns {"bots": [], "error": "Invalid JSON format", "version": 0}
    """
    try:
        # Use cached loader with version tracking
        config = load_bot_configs()

        # Return bots list with version info
        return {
            "bots": config.get("bots", []),
            "version": config.get("version", 0),
            "updated_at": config.get("updated_at", datetime.now().isoformat())
        }

    except FileNotFoundError:
        logger.error("bots.json not found")
        return {
            "bots": [],
            "error": "Configuration file not found. Please ensure bots.json exists in tradingbots/config/",
            "version": 0
        }
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in bots.json: {e}")
        return {
            "bots": [],
            "error": f"Invalid JSON format in bots.json: {str(e)}",
            "version": 0
        }
    except Exception as e:
        logger.error(f"Error loading bot configs: {e}")
        return {
            "bots": [],
            "error": f"Unexpected error loading configuration: {str(e)}",
            "version": 0
        }

@app.get("/api/bot-params/{symbol}")
async def get_bot_params(symbol: str):
    """
    Get detailed strategy and execution parameters for a bot by symbol (e.g., IWM, NVDA).
    Returns the parsed JSON parameter profile.
    """
    params = load_bot_parameters(symbol)
    if not params:
        raise HTTPException(status_code=404, detail=f"Bot parameters for symbol '{symbol}' not found")
    return {
        "status": "success",
        "symbol": symbol.upper(),
        "parameters": params
    }

@app.get("/api/bot-params")
async def get_all_bot_params():
    """
    Get all available bot parameter profiles.
    """
    result = {}
    try:
        cfg = load_bot_configs()
        bots = cfg.get("bots", [])
        for b in bots:
            sym = b.get("symbol")
            if sym:
                p = load_bot_parameters(sym)
                if p:
                    result[sym.upper()] = p
        return {"status": "success", "bots": result}
    except Exception as e:
        logger.error(f"Error fetching all bot parameters: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)}
        )


@app.get("/api/health")
@limiter.limit("60/minute")
async def health_check(request: Request):
    """
    Health check endpoint for monitoring and load balancers.

    Checks:
    - API server is running
    - Firestore connection is healthy (with 5s timeout)
    - Bot configuration can be loaded

    Returns:
        200 OK: {"status": "healthy", "timestamp": "...", "checks": {...}}
        503 Service Unavailable: {"status": "unhealthy", "errors": [...]}
    """
    import asyncio
    from functools import partial

    checks = {
        "api": "ok",
        "firestore": "unknown",
        "config": "unknown"
    }
    errors = []

    # Check Firestore connection (with timeout)
    try:
        from google.api_core import exceptions as gcp_exceptions

        loop = asyncio.get_event_loop()
        await asyncio.wait_for(
            loop.run_in_executor(
                None,
                partial(dashboard_data.db.collection('bot_status').limit(1).get)
            ),
            timeout=5.0
        )
        checks["firestore"] = "ok"
    except asyncio.TimeoutError:
        checks["firestore"] = "error"
        errors.append("Firestore: Connection timeout (5s)")
        logger.warning("Firestore health check timed out after 5 seconds")
    except gcp_exceptions.GoogleAPIError as e:
        # Expected Firestore errors (connectivity, permissions, etc.)
        checks["firestore"] = "error"
        errors.append(f"Firestore: {type(e).__name__}")
        logger.warning(f"Firestore health check failed: {e}")
    except Exception as e:
        # Unexpected errors - should be investigated
        checks["firestore"] = "error"
        errors.append(f"Firestore: Unexpected error - {type(e).__name__}")
        logger.error(f"Unexpected error in Firestore health check: {e}", exc_info=True)

    # Check configuration loading
    try:
        config = load_bot_configs()
        if config.get("bots") is not None:
            checks["config"] = "ok"
        else:
            checks["config"] = "error"
            errors.append("Config: No bots found")
    except Exception as e:
        checks["config"] = "error"
        errors.append(f"Config: {str(e)}")
        logger.error(f"Config health check failed: {e}")

    # Determine overall health
    is_healthy = all(v == "ok" for v in checks.values())

    response = {
        "status": "healthy" if is_healthy else "unhealthy",
        "timestamp": datetime.now().isoformat(),
        "checks": checks
    }

    if errors:
        response["errors"] = errors

    status_code = 200 if is_healthy else 503
    return JSONResponse(content=response, status_code=status_code)

@app.get("/api/metrics")
@limiter.limit("60/minute")
async def get_metrics(request: Request):
    """
    System metrics endpoint for monitoring and observability.

    Returns:
        - Bot count (enabled/disabled/total)
        - Trade count (last 24h)
        - P&L summary (day/week/month)
        - Active sessions count
        - Config version

    Performance: Cached for 30 seconds to avoid expensive recalculation
    """
    # Note: _metrics_cache is a module-level dict, no 'global' needed for dict modification

    with _metrics_cache['lock']:
        now_time = datetime.now().timestamp()

        # Return cached if fresh
        if (_metrics_cache['data'] is not None and
            now_time - _metrics_cache['timestamp'] < _metrics_cache['ttl']):
            logger.debug("Returning cached metrics")
            return _metrics_cache['data']

        try:
            # Load config for bot metrics
            config = load_bot_configs()
            bots = config.get("bots", [])
            enabled_bots = [b for b in bots if b.get("enabled", False)]

            # Calculate trade metrics
            now = datetime.now()
            day_ago = now - timedelta(days=1)
            trades_24h = len([
                t for t in dashboard_data.trades
                if datetime.fromisoformat(t["timestamp"].replace('Z', '+00:00')).replace(tzinfo=None) >= day_ago
            ])

            # Get P&L summary
            pnl_data = dashboard_data.get_pnl_data()

            # Count active sessions
            active_sessions = len([
                expiry for expiry in authenticated_sessions.values()
                if expiry > now
            ])

            # Build metrics response
            metrics = {
                "timestamp": now.isoformat(),
                "bots": {
                    "total": len(bots),
                    "enabled": len(enabled_bots),
                    "disabled": len(bots) - len(enabled_bots),
                    "symbols": [b["symbol"] for b in enabled_bots]
                },
                "trades": {
                    "last_24h": trades_24h,
                    "total": len(dashboard_data.trades)
                },
                "pnl": {
                    "day": pnl_data["day"]["total"]["pnl"],
                    "week": pnl_data["week"]["total"]["pnl"],
                    "month": pnl_data["month"]["total"]["pnl"]
                },
                "sessions": {
                    "active": active_sessions
                },
                "config": {
                    "version": config.get("version", 0),
                    "last_updated": config.get("updated_at", "unknown")
                }
            }

            # Cache result
            _metrics_cache['data'] = metrics
            _metrics_cache['timestamp'] = now_time

            logger.debug("Metrics calculated and cached")
            return metrics

        except Exception as e:
            logger.error(f"Error generating metrics: {e}", exc_info=True)
            return JSONResponse(
                content={
                    "error": "Failed to generate metrics",
                    "timestamp": datetime.now().isoformat()
                },
                status_code=500
            )

@app.get("/api/bot-overview")
async def get_bot_overview():
    """
    Get bot and bucket overview data.

    Filters buckets to show only ACTIVE buckets (non-None entry prices).
    This prevents displaying cleared/exited bucket slots.

    Performance: Uses in-memory cache to avoid redundant Firestore queries.
    The cache is kept fresh by bot write operations.
    """
    try:
        # v4.1.5 FIX: Always reload from Firestore to avoid stale cache
        # The bot writes directly to Firestore, bypassing this cache
        bot_data_raw = dashboard_data._load_bot_data()
        dashboard_data.bot_data = bot_data_raw  # Update cache for other endpoints

        if not bot_data_raw:
            return {}

        # Deep copy to avoid modifying the original
        bot_data = {}

        for bot_id, bot_info in bot_data_raw.items():
            bot_data[bot_id] = {}

            # Filter buckets: only include those with non-None entryPrice
            for key, value in bot_info.items():
                if key.startswith('bucket'):
                    # Check if this bucket has an active entry
                    if isinstance(value, dict) and value.get('entryPrice') is not None:
                        bot_data[bot_id][key] = value
                else:
                    # Copy non-bucket fields as-is
                    bot_data[bot_id][key] = value

        return bot_data

    except Exception as e:
        print(f"ERROR in get_bot_overview: {e}")
        # Fallback to unfiltered data if filtering fails
        return dashboard_data.bot_data

@app.get("/api/bot-status/{symbol}")
async def get_bot_status(symbol: str, response: Response = None):
    """
    Get bot health/status data for NVDA Focus dashboard.

    Returns:
        - last_heartbeat: Timestamp of last heartbeat
        - is_trading: Whether bot is actively trading
        - active_buckets: Number of active positions
        - realized_pnl: Realized P&L
        - market_status: Market state (open/closed)
        - broker_connected: Inferred from recent heartbeat
        - sma5, sma20, sma_signal, sma_spread_pct: SMA indicator data

    Performance: Adds HTTP cache headers to enable browser caching (5 seconds).
    """
    try:
        symbol_lower = symbol.lower()
        status_doc = dashboard_data.db.collection('bot_status').document(symbol_lower).get()

        if not status_doc.exists:
            return {
                "error": f"Bot status not found for {symbol}",
                "broker_connected": False,
                "is_trading": False
            }

        status_data = status_doc.to_dict()

        # Calculate heartbeat freshness (consider stale if > 5 minutes old)
        last_heartbeat = status_data.get('last_heartbeat')
        heartbeat_age_seconds = None
        broker_connected = False

        if last_heartbeat:
            age_delta = datetime.now() - last_heartbeat.replace(tzinfo=None)
            heartbeat_age_seconds = age_delta.total_seconds()
            broker_connected = heartbeat_age_seconds < 300  # 5 minutes

        result = {
            "symbol": status_data.get('symbol', symbol.upper()),
            "last_heartbeat": last_heartbeat.isoformat() if last_heartbeat else None,
            "heartbeat_age_seconds": heartbeat_age_seconds,
            "is_trading": status_data.get('is_trading', False),
            "active_buckets": status_data.get('active_buckets', 0),
            "realized_pnl": status_data.get('realized_pnl', 0.0),
            "market_status": status_data.get('market_status', 'unknown'),
            "broker_connected": broker_connected,
            "updated_at": status_data.get('updated_at').isoformat() if status_data.get('updated_at') else None,

            # SMA data (new in v2.2.0)
            "sma5": status_data.get('sma5'),
            "sma20": status_data.get('sma20'),
            "sma200": status_data.get('sma200'),  # SMA200 for regime detection
            "sma_signal": status_data.get('sma_signal'),  # Bot writes 'sma_signal' to bot_status
            "sma_spread_pct": status_data.get('sma_spread_pct'),  # Bot calculates and writes this

            # SMA Crossover Status (new in v2.3.0)
            "crossover_confirmation": status_data.get('crossover_confirmation'),  # "CONFIRMED", "PENDING (n/m ticks)", or None
            "last_crossover": status_data.get('last_crossover'),  # ISO timestamp of last crossover
            "entry_funnel": status_data.get('entry_funnel')  # Confirm-to-fill entry funnel telemetry
        }

        # Add cache header - browser can cache for 5 seconds
        if response:
            response.headers['Cache-Control'] = 'max-age=5, must-revalidate'

        return result
    except Exception as e:
        print(f"Error fetching bot status for {symbol}: {e}")
        return {
            "error": str(e),
            "broker_connected": False,
            "is_trading": False
        }

@app.get("/api/logs")
async def get_logs(botId: int = 1):
    """
    Get recent logs for a specific bot (last 15 lines).

    Returns array of log objects formatted for frontend display.
    If no logs available, returns sample logs for testing.
    """
    try:
        bot_logs = dashboard_data.logs.get(str(botId), [])

        # If no logs, return empty array (frontend will show "No logs available")
        if not bot_logs:
            return []

        # Convert string logs to structured format for frontend
        formatted_logs = []
        for log_line in bot_logs[-20:]:  # Last 20 lines
            # Parse log line format: "timestamp | severity | message"
            # Example: "2026-07-13 14:22:35 | INFO | Bar received"
            parts = log_line.split('|')

            if len(parts) >= 3:
                timestamp = parts[0].strip()
                severity = parts[1].strip()
                message = '|'.join(parts[2:]).strip()
            else:
                # Fallback for unparseable logs
                timestamp = ""
                severity = "INFO"
                message = log_line

            formatted_logs.append({
                "timestamp": timestamp,
                "severity": severity,
                "component": "Bot",
                "event": "Activity",
                "message": message
            })

        return formatted_logs

    except Exception as e:
        print(f"Error fetching logs: {e}")
        return []

@app.get("/api/trade-history")
async def get_trade_history():
    """
    Get trade history (last 25 trades from Firestore for display).
    Used by frontend to display in table.
    """
    try:
        # Reload trades from Firestore on each request for fresh data
        fresh_trades = dashboard_data._load_trades(limit=25)
        return {"trades": fresh_trades}
    except Exception as e:
        # Return valid JSON error response instead of letting FastAPI return HTML
        print(f"ERROR in /api/trade-history: {e}")
        import traceback
        traceback.print_exc()
        return {"trades": [], "error": str(e), "status": "error"}

@app.get("/api/trade-history/all")
async def get_all_trade_history(days: int = 60, response: Response = None):
    """
    Get all trades from the last N days for P&L calculations.
    Used by frontend for week/month statement calculations.

    Parameters:
        days: Number of days to look back (default 60 days = ~2 months)

    Returns all trades within date range (no limit).

    Performance: Uses 30-second cache to reduce Firestore reads from ~15,000/min to ~4/min.

    NOTE: Enhanced to include option summary fields (Phase 3 - Unified Dashboard Update):
          - stock_pnl: Stock-only P&L
          - option_pnl: Total option P&L
          - option_event_count: Number of option events
          These fields provide backward compatibility for trades without option data.
    """
    try:
        now = datetime.now()

        # The long-horizon performance window is expensive (it scans up to the
        # row cap) and only feeds analytics, so it is cached longer than the
        # short statement window. TTL is derived from the window, never from a
        # caller-supplied value, so the cache cannot be pinned externally.
        ttl_seconds = (
            PERFORMANCE_TRADE_CACHE_TTL_SECONDS
            if days >= PERFORMANCE_HISTORY_LOOKBACK_DAYS
            else TRADE_CACHE_TTL_SECONDS
        )

        # Check cache (thread-safe), scoped to this lookback window
        with _trade_history_cache['lock']:
            cached_data = _trade_history_cache['data'].get(days)
            cached_time = _trade_history_cache['timestamp'].get(days)
            cached_ttl = _trade_history_cache['ttl'].get(days, TRADE_CACHE_TTL_SECONDS)

            if cached_data and cached_time:
                age_seconds = (now - cached_time).total_seconds()
                if age_seconds < cached_ttl:
                    if response:
                        response.headers['X-Cache-Status'] = f'HIT (age: {int(age_seconds)}s)'
                    return cached_data

        # Cache miss - fetch from Firestore
        if response:
            response.headers['X-Cache-Status'] = 'MISS'

        # Calculate start date
        start_date = datetime.now() - timedelta(days=days)

        # Query all trades since start_date (capped to prevent unbounded growth)
        trades_query = dashboard_data.trades_ref.where(
            'timestamp', '>=', start_date.isoformat()
        ).order_by('timestamp', direction=firestore.Query.DESCENDING).limit(TRADE_HISTORY_ROW_CAP)

        trades = []
        for doc in trades_query.stream():
            trade = doc.to_dict()

            # Attach doc ID if available
            doc_id_val = getattr(doc, 'id', None)
            if doc_id_val and isinstance(doc_id_val, str):
                trade['id'] = doc_id_val
                trade['doc_id'] = doc_id_val

            # Ensure trade_id is available at top level
            if 'trade_id' not in trade and 'analysis_metadata' in trade and isinstance(trade.get('analysis_metadata'), dict):
                trade['trade_id'] = trade['analysis_metadata'].get('trade_id')

            # Filter out test trades (TEST, TESTBOT symbols)
            # Check both 'symbol' and 'botName' fields (tests use botName)
            symbol = trade.get('symbol', '').upper()
            bot_name = trade.get('botName', '').upper()
            if symbol in ('TEST', 'TESTBOT') or bot_name in ('TEST', 'TESTBOT'):
                continue  # Skip test trades

            # Map sequence_number to seqNum for frontend compatibility
            if 'sequence_number' in trade:
                trade['seqNum'] = trade['sequence_number']
            elif 'seqNum' not in trade:
                trade['seqNum'] = '?'

            # Ensure option fields exist for backward compatibility
            # (old trades won't have these fields)
            if 'stock_pnl' not in trade:
                # Old trade: stock_pnl equals total profitLoss (no options)
                trade['stock_pnl'] = trade.get('profitLoss', 0)

            if 'option_pnl' not in trade:
                trade['option_pnl'] = 0

            if 'option_event_count' not in trade:
                trade['option_event_count'] = 0

            trades.append(trade)

        result = {"trades": trades, "days": days, "count": len(trades)}

        # Update cache (scoped to this lookback window)
        with _trade_history_cache['lock']:
            _trade_history_cache['data'][days] = result
            _trade_history_cache['timestamp'][days] = now
            _trade_history_cache['ttl'][days] = ttl_seconds

        return result
    except Exception as e:
        print(f"Error loading trades for P&L: {e}")
        return {"trades": [], "days": days, "count": 0, "error": str(e)}

@app.get("/api/bot-metrics/{bot_id}")
async def get_bot_metrics(bot_id: int, days: int = 90, response: Response = None):
    """
    Get pre-calculated performance metrics for a bot.
    Returns only 6 aggregate values instead of full trade history.

    This endpoint is designed for the dashboard performance metrics display,
    reducing bandwidth from 200-400 KB to ~150 bytes (99.96% reduction).

    Performance: Uses 30-second cache to reduce Firestore reads by 99%.

    Metrics:
    - today_pnl: Today's realized P&L
    - net_pnl: Total realized P&L over period
    - total_trades: Number of completed trades
    - win_rate: Percentage of profitable trades
    - sharpe_ratio: Risk-adjusted return metric
    - max_drawdown: Largest peak-to-trough decline

    Parameters:
        bot_id: Bot ID (dynamically assigned, e.g., 1 for NVDA)
        days: Number of days to look back (default 90)
    """
    try:
        now = datetime.now()

        # Check cache (thread-safe)
        with _bot_metrics_cache['lock']:
            cached_data = _bot_metrics_cache['data'].get(bot_id)
            cached_time = _bot_metrics_cache['timestamp'].get(bot_id)

            if cached_data and cached_time:
                age_seconds = (now - cached_time).total_seconds()
                if age_seconds < BOT_METRICS_CACHE_TTL_SECONDS:
                    if response:
                        response.headers['X-Cache-Status'] = f'HIT (age: {int(age_seconds)}s)'
                    return cached_data

        # Cache miss - fetch from Firestore and calculate metrics
        if response:
            response.headers['X-Cache-Status'] = 'MISS'

        start_date = datetime.now() - timedelta(days=days)

        # Query trades for this bot (with limit to prevent unbounded growth)
        trades_query = dashboard_data.trades_ref.where(
            'timestamp', '>=', start_date.isoformat()
        ).order_by('timestamp', direction=firestore.Query.DESCENDING).limit(500)

        trades = []
        for doc in trades_query.stream():
            trade = doc.to_dict()

            # Filter test trades
            symbol = trade.get('symbol', '').upper()
            bot_name = trade.get('botName', '').upper()
            if symbol in ('TEST', 'TESTBOT') or bot_name in ('TEST', 'TESTBOT'):
                continue

            # Filter by bot_id
            if trade.get('botId') == bot_id:
                trades.append(trade)

        # Calculate metrics
        if not trades:
            result = {
                "today_pnl": 0,
                "net_pnl": 0,
                "total_trades": 0,
                "win_rate": 0,
                "sharpe_ratio": 0,
                "max_drawdown": 0
            }
        else:
            # Today's P&L
            today = datetime.now().date()
            def trade_total_pnl(t):
                if 'stock_pnl' in t:
                    return t.get('stock_pnl', 0) + t.get('option_pnl', 0)
                return t.get('profitLoss', 0)

            today_pnl = sum(
                trade_total_pnl(t)
                for t in trades
                if datetime.fromisoformat(t['timestamp']).date() == today
            )

            # Net P&L
            net_pnl = sum(trade_total_pnl(t) for t in trades)

            # Win rate
            profitable = sum(1 for t in trades if trade_total_pnl(t) > 0)
            win_rate = (profitable / len(trades) * 100) if trades else 0

            # Sharpe ratio
            returns = [trade_total_pnl(t) for t in trades]
            mean_return = sum(returns) / len(returns) if returns else 0
            variance = sum((r - mean_return) ** 2 for r in returns) / len(returns) if len(returns) > 1 else 0
            std_dev = variance ** 0.5
            sharpe_ratio = (mean_return / std_dev) if std_dev > 0 else 0

            # Max drawdown
            cumulative_pnl = []
            running_total = 0
            for t in sorted(trades, key=lambda x: x['timestamp']):
                running_total += trade_total_pnl(t)
                cumulative_pnl.append(running_total)

            max_drawdown = 0
            peak = cumulative_pnl[0] if cumulative_pnl else 0
            for value in cumulative_pnl:
                if value > peak:
                    peak = value
                drawdown = peak - value
                if drawdown > max_drawdown:
                    max_drawdown = drawdown

            result = {
                "today_pnl": round(today_pnl, 2),
                "net_pnl": round(net_pnl, 2),
                "total_trades": len(trades),
                "win_rate": round(win_rate, 1),
                "sharpe_ratio": round(sharpe_ratio, 2),
                "max_drawdown": round(max_drawdown, 2)
            }

        # Update cache
        with _bot_metrics_cache['lock']:
            _bot_metrics_cache['data'][bot_id] = result
            _bot_metrics_cache['timestamp'][bot_id] = now

        # Add cache header
        if response:
            response.headers['Cache-Control'] = 'max-age=30, must-revalidate'

        return result

    except Exception as e:
        print(f"ERROR in get_bot_metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/trade-history/export")
async def export_trade_history(start_month: str, end_month: str):
    """
    Export trade history to Excel for a date range.
    Parameters:
        start_month: YYYY-MM format (e.g., "2026-05")
        end_month: YYYY-MM format (e.g., "2026-05")
    Returns Excel file download
    """
    try:
        from fastapi.responses import StreamingResponse

        # Parse start and end dates
        start_date = datetime.strptime(f"{start_month}-01", "%Y-%m-%d")
        # Get last day of end month
        if end_month:
            year, month = map(int, end_month.split('-'))
            if month == 12:
                end_date = datetime(year + 1, 1, 1) - timedelta(days=1)
            else:
                end_date = datetime(year, month + 1, 1) - timedelta(days=1)
        else:
            end_date = datetime.now()

        end_date = end_date.replace(hour=23, minute=59, second=59)

        # Query Firestore for trades in date range
        trades = []
        query = dashboard_data.trades_ref.order_by('timestamp', direction=firestore.Query.DESCENDING)

        for doc in query.stream():
            trade = doc.to_dict()

            # Filter out test trades (TEST, TESTBOT symbols)
            # Check both 'symbol' and 'botName' fields (tests use botName)
            symbol = trade.get('symbol', '').upper()
            bot_name = trade.get('botName', '').upper()
            if symbol in ('TEST', 'TESTBOT') or bot_name in ('TEST', 'TESTBOT'):
                continue  # Skip test trades

            trade_time = datetime.fromisoformat(trade['timestamp'].replace('Z', '+00:00')).replace(tzinfo=None)
            if start_date <= trade_time <= end_date:
                trades.append(trade)

        # Create Excel file in memory
        output = BytesIO()
        workbook = xlsxwriter.Workbook(output)
        worksheet = workbook.add_worksheet('Trade History')

        # Define formats
        header_format = workbook.add_format({
            'bold': True,
            'bg_color': '#34495e',
            'font_color': 'white',
            'border': 1
        })

        profit_format = workbook.add_format({'font_color': '#27ae60', 'num_format': '$#,##0.00'})
        loss_format = workbook.add_format({'font_color': '#e74c3c', 'num_format': '$#,##0.00'})
        price_format = workbook.add_format({'num_format': '$#,##0.00'})

        # Write headers
        headers = ['Seq #', 'Bot Name', 'Bucket Type', 'Ref Price Before', 'Entry Price',
                   'Ref Price After', 'Exit Price', 'Timestamp', 'P/L (USD)']

        for col, header in enumerate(headers):
            worksheet.write(0, col, header, header_format)

        # Sort trades by timestamp (oldest first for display)
        sorted_trades = sorted(trades, key=lambda t: datetime.fromisoformat(t['timestamp'].replace('Z', '+00:00')).replace(tzinfo=None))

        # Write data rows (newest first in Excel)
        for row, trade in enumerate(reversed(sorted_trades), start=1):
            # Use stored seqNum from database (or '?' if missing)
            seq_num = trade.get('seqNum', '?')
            worksheet.write(row, 0, seq_num)
            worksheet.write(row, 1, trade.get('botName', 'Unknown'))
            worksheet.write(row, 2, trade.get('bucketType', 'Unknown'))
            worksheet.write(row, 3, trade.get('referencePriceBefore', 0), price_format)
            worksheet.write(row, 4, trade.get('entryPrice', 0), price_format)
            worksheet.write(row, 5, trade.get('referencePriceAfter', 0), price_format)
            worksheet.write(row, 6, trade.get('exitPrice', 0), price_format)

            # Format timestamp
            timestamp = trade.get('timestamp', '')
            if timestamp:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00')).replace(tzinfo=None)
                worksheet.write(row, 7, dt.strftime('%Y-%m-%d %H:%M:%S'))

            # Write P/L with color
            pnl = trade.get('profitLoss', 0)
            pnl_format = profit_format if pnl >= 0 else loss_format
            worksheet.write(row, 8, pnl, pnl_format)

        # Adjust column widths
        worksheet.set_column(0, 0, 8)   # Seq #
        worksheet.set_column(1, 1, 12)  # Bot Name
        worksheet.set_column(2, 2, 15)  # Bucket Type
        worksheet.set_column(3, 6, 15)  # Prices
        worksheet.set_column(7, 7, 20)  # Timestamp
        worksheet.set_column(8, 8, 12)  # P/L

        workbook.close()
        output.seek(0)

        # Return as downloadable file
        filename = f"trade_history_{start_month}_to_{end_month}.xlsx"

        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")

@app.get("/api/pnl-statement")
async def get_pnl_statement():
    """
    DEPRECATED: Dashboard calculates P&L client-side from trade data (v1.2.0+).
    This endpoint is kept for backward compatibility only and is not used by the frontend.

    Frontend calls /api/trade-history and calculates P&L locally for instant
    date selector updates without server round-trips.
    """
    return dashboard_data.calculate_pnl()

@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.3.1",
        "trades_count": len(dashboard_data.trades),
        "bot_data_keys": list(dashboard_data.bot_data.keys())
    }

# Update endpoints (for future integration with real trading bots)

@app.post("/api/bot-overview/update")
async def update_bot_overview(data: Dict):
    """
    DEPRECATED: Bots now write directly to Firestore (v2.0+).
    This endpoint is kept for backward compatibility with old bot versions only.

    Update bot overview data and persist to Firestore.
    Called by trading bots when bucket states change.
    Merges updates with existing data to preserve other buckets.
    """
    # Note: v2.0+ bots write directly to Firestore and don't call this endpoint
    # This code path should never execute in production
    print("WARNING: Deprecated /api/bot-overview/update endpoint called")
    return {"status": "error", "message": "Deprecated endpoint - bots should write directly to Firestore"}

@app.post("/api/bot-overview/delete-bucket")
async def delete_bucket(data: Dict):
    """
    Delete a specific bucket from bot_overview in Firestore.
    Used to clean up stale/duplicate bucket data.

    Expected data: {"bot_id": "msft-bot", "bucket_id": 3}
    """
    try:
        bot_id = data.get("bot_id")
        bucket_id = data.get("bucket_id")

        if not bot_id or bucket_id is None:
            raise HTTPException(status_code=400, detail="bot_id and bucket_id are required")

        # Get the document reference (data is stored in bot_overview/current)
        doc_ref = dashboard_data.bot_data_ref.document('current')

        # Delete the specific bucket field from data.bot_id.bucketN
        bucket_key = f"bucket{bucket_id}"
        field_path = f"data.{bot_id}.{bucket_key}"
        doc_ref.update({field_path: firestore.DELETE_FIELD})

        # Also update in-memory cache
        if bot_id in dashboard_data.bot_data:
            dashboard_data.bot_data[bot_id].pop(bucket_key, None)

        return {
            "status": "success",
            "message": f"Deleted {bucket_key} from {bot_id}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/logs/update")
async def update_logs(data: Dict):
    """
    Update logs for a bot. Keeps only last 15 lines per bot.
    Expected data format: {"botId": 1, "logs": ["line1", "line2", ...]}
    """
    try:
        bot_id = data.get("botId")
        logs = data.get("logs", [])

        if bot_id is None:
            raise HTTPException(status_code=400, detail="botId is required")

        # Keep only last 15 lines
        log_lines = logs[-15:]
        dashboard_data.logs[str(bot_id)] = log_lines

        # Persist to Firestore
        dashboard_data._save_logs(bot_id, log_lines)

        return {"status": "success", "message": f"Logs updated for bot {bot_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/pnl-sync")
async def pnl_sync(data: Dict):
    """
    Log P&L synchronization data from bots.
    Compares bot's calculated P&L against IBKR's reported P&L.
    Expected data: {
        "botId": 1,
        "bot_realized_pnl": -340.47,
        "bot_unrealized_pnl": -346.95,
        "ibkr_realized_pnl": -340.47,
        "ibkr_unrealized_pnl": 0.00,
        "ibkr_daily_pnl": -340.47,
        "discrepancy_amount": 0.70,
        "has_discrepancy": false,
        "discrepancy_message": null
    }
    """
    try:
        # For now, just acknowledge receipt
        # In the future, could store in Firestore for historical tracking
        bot_id = data.get("botId")
        if bot_id is None:
            raise HTTPException(status_code=400, detail="botId is required")

        # Log for debugging
        print(f"P&L sync received for bot {bot_id}: "
              f"Bot realized={data.get('bot_realized_pnl')}, "
              f"IBKR unrealized={data.get('ibkr_unrealized_pnl')}, "
              f"Has discrepancy={data.get('has_discrepancy')}")

        return {"status": "success", "message": f"P&L sync logged for bot {bot_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/trade-history/add")
async def add_trade(trade: Trade):
    """
    DEPRECATED: Bots now write directly to Firestore (v2.0+).
    This endpoint is kept for backward compatibility with old bot versions only.

    In v2.0+, bots call dashboard.log_trade() which writes directly to Firestore
    with proper sequence_number field. This endpoint is not used.
    """
    # Note: v2.0+ bots write directly to Firestore and don't call this endpoint
    # This code path should never execute in production
    print("WARNING: Deprecated /api/trade-history/add endpoint called")
    return {"status": "error", "message": "Deprecated endpoint - bots should write directly to Firestore"}

@app.post("/api/pnl-statement/update")
async def update_pnl_statement(data: PnLStatement):
    """
    DEPRECATED: Dashboard calculates P&L client-side from trade data (v1.2.0+).
    This endpoint is kept for backward compatibility only and is not used.
    """
    print("WARNING: Deprecated /api/pnl-statement/update endpoint called")
    return {"status": "error", "message": "Deprecated endpoint - P&L calculated client-side"}

@app.delete("/api/trade-history/delete")
async def delete_trade(
    timestamp: Optional[str] = None,
    trade_id: Optional[str] = None,
    entry_order_id: Optional[str] = None,
    doc_id: Optional[str] = None
):
    """
    Delete a trade from Firestore and in-memory caches.
    Can match by timestamp, doc_id, entry_order_id, or trade_id.
    Ensures zero discrepancies:
    - Deletes trade document from Firestore
    - Cleans up associated option events
    - Invalidates 30s trade history cache (_trade_history_cache)
    - Invalidates 30s bot metrics cache (_bot_metrics_cache)
    - Updates in-memory dashboard_data.trades list
    """
    try:
        docs = []

        # 1. Query by timestamp if provided (for backwards compatibility with existing clients/tests)
        if timestamp:
            query = dashboard_data.trades_ref.where('timestamp', '==', timestamp)
            docs = list(query.stream())

        # 2. Query by doc_id if provided and not yet found
        if not docs and doc_id:
            try:
                doc_snap = dashboard_data.trades_ref.document(str(doc_id)).get()
                if getattr(doc_snap, 'exists', False):
                    docs.append(doc_snap)
            except Exception as e:
                print(f"Warning looking up doc_id {doc_id}: {e}")

        # 3. Query by entryOrderId if provided and not yet found
        if not docs and entry_order_id:
            try:
                eid_int = int(entry_order_id)
                query = dashboard_data.trades_ref.where('entryOrderId', '==', eid_int)
                docs = list(query.stream())
            except (ValueError, TypeError):
                pass
            if not docs:
                query = dashboard_data.trades_ref.where('entryOrderId', '==', str(entry_order_id))
                docs = list(query.stream())

        # 4. Query by trade_id in analysis_metadata if provided and not yet found
        if not docs and trade_id:
            query = dashboard_data.trades_ref.where('analysis_metadata.trade_id', '==', trade_id)
            docs = list(query.stream())
            if not docs:
                query = dashboard_data.trades_ref.where('trade_id', '==', trade_id)
                docs = list(query.stream())

        if docs:
            deleted_count = 0
            timestamps_to_remove = set()
            entry_orders_to_clean = set()
            doc_ids_to_remove = set()

            for doc in docs:
                d_id = getattr(doc, 'id', None)
                if d_id and isinstance(d_id, str):
                    doc_ids_to_remove.add(d_id)

                trade_dict = {}
                if hasattr(doc, 'to_dict') and callable(doc.to_dict):
                    trade_dict = doc.to_dict() or {}

                doc_ts = trade_dict.get('timestamp') or timestamp
                if doc_ts:
                    timestamps_to_remove.add(doc_ts)

                e_id = trade_dict.get('entryOrderId') or trade_dict.get('analysis_metadata', {}).get('entry_order_id') or entry_order_id
                if e_id is not None:
                    entry_orders_to_clean.add(e_id)

                doc.reference.delete()
                deleted_count += 1

            # Clean up associated option events if any
            for e_id in entry_orders_to_clean:
                try:
                    if hasattr(dashboard_data, 'db') and dashboard_data.db is not None:
                        opt_ref = dashboard_data.db.collection('option_events')
                        try:
                            opt_query = opt_ref.where('entryOrderId', '==', int(e_id))
                            for opt_doc in opt_query.stream():
                                opt_doc.reference.delete()
                        except (ValueError, TypeError):
                            pass
                except Exception as e:
                    print(f"Warning cleaning option events for {e_id}: {e}")

            # Invalidate trade history cache
            with _trade_history_cache['lock']:
                _trade_history_cache['data'].clear()
                _trade_history_cache['timestamp'].clear()
                _trade_history_cache['ttl'].clear()

            # Invalidate bot metrics cache
            with _bot_metrics_cache['lock']:
                _bot_metrics_cache['data'].clear()
                _bot_metrics_cache['timestamp'].clear()

            # Remove from in-memory cache
            dashboard_data.trades = [
                t for t in dashboard_data.trades
                if (not t.get('id') or t.get('id') not in doc_ids_to_remove)
                and (not t.get('timestamp') or t.get('timestamp') not in timestamps_to_remove)
                and (not t.get('entryOrderId') or (t.get('entryOrderId') not in entry_orders_to_clean and str(t.get('entryOrderId')) not in {str(x) for x in entry_orders_to_clean}))
            ]

            return {
                "status": "success",
                "message": f"Deleted {deleted_count} trade(s)",
                "deleted": deleted_count
            }
        else:
            raise HTTPException(status_code=404, detail="Trade not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/bot-control/stop")
async def stop_bots():
    """
    Stop both trading bots via Bot Control API on VM.

    Sends HTTP request to Flask API running on VM.
    """
    try:
        # Call VM API to stop bots
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f'{BOT_CONTROL_API_URL}/stop-bots',
                headers=_bot_control_headers()
            )
            if response.is_success:
                data = response.json()
                return {"status": data.get("status", "success"), "message": data.get("message", "Bots stopped")}
            else:
                try:
                    err_data = response.json()
                    detail_msg = err_data.get("message") or err_data.get("detail") or response.text
                except Exception:
                    detail_msg = response.text
                raise HTTPException(status_code=response.status_code, detail=f"Bot control API error: {detail_msg}")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with bot control API: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/bot-control/restart")
async def restart_bots():
    """
    Restart both trading bots via Bot Control API on VM.

    Sends HTTP request to Flask API running on VM.
    Bots will reload latest code and configuration on restart.
    """
    try:
        # Call VM API to restart bots
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f'{BOT_CONTROL_API_URL}/restart-bots',
                headers=_bot_control_headers()
            )
            if response.is_success:
                data = response.json()
                return {"status": data.get("status", "success"), "message": data.get("message", "Bots restarted")}
            else:
                try:
                    err_data = response.json()
                    detail_msg = err_data.get("message") or err_data.get("detail") or response.text
                except Exception:
                    detail_msg = response.text
                raise HTTPException(status_code=response.status_code, detail=f"Bot control API error: {detail_msg}")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with bot control API: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/bot-control/reset/{bot_name}")
async def reset_bot(bot_name: str):
    """
    Reset a specific bot: close positions, backup state, delete state, restart bot.

    Args:
        bot_name: Bot symbol in lowercase (e.g., 'nvda')

    Sends HTTP request to Flask API running on VM (port 8080).
    The VM API will execute the full reset script.

    Returns:
        JSON with status and detailed log of reset steps
    """
    # Validate bot name format (lowercase alphanumeric)
    if not bot_name or not bot_name.isalnum():
        raise HTTPException(status_code=400, detail=f"Invalid bot name format: {bot_name}")

    try:
        # Call VM API to reset bot
        async with httpx.AsyncClient(timeout=120.0) as client:  # 2 minute timeout for reset
            response = await client.post(
                f'{BOT_CONTROL_API_URL}/reset-bot/{bot_name}',
                headers=_bot_control_headers()
            )
            response.raise_for_status()
            data = response.json()
            return data
    except httpx.HTTPError as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with bot control API: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/close-position")
async def close_position(request: Request):
    """
    Close a specific position (stock + covered calls if any).

    Request body:
        {
            "client_id": 4,          # preferred: the bot's client_id
            "bot_id": 2,             # legacy alternative: 1-indexed position in bots.json
            "symbol": "NVDA",        # must match the resolved bot's config
            "bucket_group": "upward",
            "bucket_id": 0,
            "close_covered_calls": true
        }

    Returns:
        JSON with success status and message
    """
    try:
        body = await request.json()
        symbol = body.get('symbol')
        bucket_group = body.get('bucket_group')
        bucket_id = body.get('bucket_id')
        close_covered_calls = body.get('close_covered_calls', True)

        # Validate required fields
        if not all([symbol, bucket_group is not None, bucket_id is not None]):
            raise HTTPException(status_code=400, detail="Missing required fields")

        # Resolve to exactly one bot; refuses a client_id/symbol mismatch
        # instead of writing the command to the wrong bot.
        target = resolve_bot_target(body)
        client_id = target["client_id"]

        db = firestore.Client()
        bot_ref = db.collection('bots').document(f'bot_{client_id}')
        print(
            f"Using bot_{client_id} for symbol {target['symbol']} "
            f"(source={target['source']}, requested symbol={symbol})"
        )

        # Create a close_position command
        close_command = {
            'command': 'close_position',
            'timestamp': datetime.now().isoformat(),
            'params': {
                'symbol': symbol,
                'bucket_group': bucket_group,
                'bucket_id': bucket_id,
                'close_covered_calls': close_covered_calls
            }
        }

        # Write command to Firestore
        bot_ref.update({
            'pending_command': close_command
        })

        return {
            'success': True,
            'message': f'Close position command sent for {symbol} (bucket {bucket_group}-{bucket_id}). Bot will process it on next update cycle.'
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/create-position")
async def create_position(request: Request):
    """
    Create a new position (buy 100 shares + sell covered call immediately).

    This bypasses all SMA/momentum checks and creates a position at the current market price.
    Uses SMA crossover configuration: profit target = 2.0x ATR, 5% stop loss.

    Request body:
        {
            "client_id": 4,          # preferred: the bot's client_id
            "bot_id": 2,             # legacy alternative: 1-indexed position in bots.json
            "symbol": "NVDA"         # optional; must match the resolved bot's config
        }

    Returns:
        JSON with success status and message
    """
    try:
        body = await request.json()

        # Resolve to exactly one bot; refuses a client_id/symbol mismatch
        # instead of writing the command to the wrong bot. The symbol is
        # optional and defaults to the resolved bot's configured symbol.
        target = resolve_bot_target(body)
        client_id = target["client_id"]
        symbol = target["symbol"]

        # Update Firestore to trigger bot to create position
        db = firestore.Client()
        bot_ref = db.collection('bots').document(f'bot_{client_id}')

        # Check if bot document exists, create if not
        bot_doc = bot_ref.get()
        if not bot_doc.exists:
            # Create initial bot document
            bot_ref.set({
                'bot_id': client_id,
                'client_id': client_id,
                'symbol': symbol,
                'created_at': datetime.now().isoformat(),
                'pending_command': None
            })

        cmd_timestamp = datetime.now().isoformat()

        # Create a create_position command
        create_command = {
            'command': 'create_position',
            'timestamp': cmd_timestamp,
            'params': {
                'symbol': symbol,
                'quantity': 100,  # Always 100 shares for covered calls
                'side': 'long',
                'sell_covered_call_immediately': True,
                'use_sma_config': True  # Use SMA crossover config (2.0x ATR target, 5% stop)
            }
        }

        # Write command to Firestore (use set with merge to handle both create and update)
        bot_ref.set({
            'pending_command': create_command
        }, merge=True)

        return {
            'success': True,
            'command_timestamp': cmd_timestamp,
            'message': f'Create position command sent for {symbol} (100 shares + covered call). Bot will process it on next update cycle.'
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/bot-command-status/{client_id}")
async def get_bot_command_status(client_id: int):
    """
    Get the pending command and last command execution status for a bot.
    Used by the dashboard UI to monitor real-time command processing.

    Returns:
        {
            "success": True,
            "client_id": client_id,
            "pending_command": dict or None,
            "last_command_result": dict or None,
            "has_active_position": bool
        }
    """
    try:
        db = firestore.Client()
        bot_ref = db.collection('bots').document(f'bot_{client_id}')
        doc = bot_ref.get()
        if not doc.exists:
            return {
                "success": True,
                "client_id": client_id,
                "pending_command": None,
                "last_command_result": None,
                "has_active_position": False
            }

        data = doc.to_dict() or {}
        has_active_position = bool(data.get("active_position"))
        if not has_active_position:
            for k, v in data.items():
                if k.startswith("bucket") and isinstance(v, dict):
                    if v.get("entryPrice") is not None and v.get("quantity"):
                        has_active_position = True
                        break

        return {
            "success": True,
            "client_id": client_id,
            "pending_command": data.get("pending_command"),
            "last_command_result": data.get("last_command_result"),
            "has_active_position": has_active_position
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/bot-control/current-profile")
async def get_current_profile():
    """
    Get current trading profile from VM bot control API.

    This endpoint proxies the request to the VM's bot control API to determine
    if the trading bots are running in paper (test) or live (real money) mode.

    Flow:
    1. Dashboard frontend calls this endpoint
    2. Cloud Run backend proxies to VM (BOT_CONTROL_API_URL, default 34.46.214.78:8080)
    3. VM reads /home/i030983/.trading_profile file
    4. Returns profile to dashboard

    Returns:
        JSON: {"profile": "paper" or "live"}

    Fallback: If VM is unreachable, defaults to "paper" (safer default)
    """
    try:
        import httpx
        logger.info("[PROFILE] Fetching current profile from VM")

        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f'{BOT_CONTROL_API_URL}/current-profile',
                headers=_bot_control_headers()
            )
            response.raise_for_status()
            data = response.json()

            logger.info(f"[PROFILE] Current profile: {data.get('profile', 'unknown')}")
            return data

    except httpx.HTTPError as e:
        logger.warning(f"[PROFILE] VM API unreachable: {str(e)} - Defaulting to 'paper' mode")
        # Fallback to paper mode (safer default when VM is unreachable)
        return {"profile": "paper", "source": "fallback", "vm_unreachable": True}
    except Exception as e:
        logger.warning(f"[PROFILE] Unexpected error: {str(e)} - Defaulting to 'paper' mode")
        # Fallback to paper mode
        return {"profile": "paper", "source": "fallback", "error": str(e)}

@app.post("/api/bot-control/switch-profile")
async def switch_profile(request: Request):
    """
    Switch trading profile (paper ↔ live) via Bot Control API on VM.

    CRITICAL SAFETY FEATURE: This endpoint checks for open positions before
    allowing the profile switch. This prevents accidentally switching to live
    mode while test positions are open, or switching to paper mode while real
    money positions are active.

    COMPLIANCE: All switch attempts are logged to Firestore for audit trail.

    Flow:
    1. Dashboard sends switch request with {"profile": "paper" or "live"}
    2. Backend queries Firestore bot_overview for open positions
    3. If ANY positions exist (shares > 0), return HTTP 400 error
    4. If no positions, proxy request to VM API
    5. VM updates profile file and restarts bots
    6. Log switch attempt to Firestore audit trail
    7. Return success to frontend

    Safety checks:
    - Profile validation (only "paper" or "live")
    - Position blocking (cannot switch with open positions)
    - VM-side rate limiting (5-second cooldown)

    Request body:
        {"profile": "paper" or "live"}

    Returns:
        JSON: {
            "status": "success/error",
            "profile": "paper" or "live",
            "message": "...",
            "profile_confirmed": true/false
        }

    Raises:
        HTTPException:
            400: Invalid profile or open positions exist
            500: VM API communication failure
    """
    # Initialize variables for audit trail
    switch_attempt = {
        'timestamp': datetime.now(),
        'status': 'failed',
        'from_profile': None,
        'to_profile': None,
        'user_ip': request.client.host if request.client else 'unknown',
        'positions_blocked': False,
        'position_count': 0,
        'error_message': None
    }

    try:
        # Parse request body
        body = await request.json()
        profile = body.get('profile')
        switch_attempt['to_profile'] = profile

        logger.info(f"[SWITCH-PROFILE] Request to switch to: {profile}")

        # Get current profile first (for audit trail)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                current_response = await client.get(
                    f'{BOT_CONTROL_API_URL}/current-profile',
                    headers=_bot_control_headers()
                )
                if current_response.status_code == 200:
                    current_data = current_response.json()
                    switch_attempt['from_profile'] = current_data.get('profile', 'unknown')
                    logger.info(f"[SWITCH-PROFILE] Current profile: {switch_attempt['from_profile']}")
        except Exception as e:
            logger.warning(f"[SWITCH-PROFILE] Could not fetch current profile: {e}")
            switch_attempt['from_profile'] = 'unknown'

        # Validate profile parameter
        if profile not in ['paper', 'live']:
            switch_attempt['error_message'] = f"Invalid profile: {profile}"
            logger.error(f"[SWITCH-PROFILE] Invalid profile requested: {profile}")
            raise HTTPException(status_code=400, detail="Invalid profile. Must be 'paper' or 'live'")

        # CRITICAL SAFETY CHECK: Query Firestore for open positions
        # This prevents switching modes with active positions which could cause:
        # - Confusion about which positions are real vs test
        # - Incorrect P&L calculations
        # - Risk management issues
        logger.info("[SWITCH-PROFILE] Checking for open positions in Firestore...")

        db = firestore.Client()
        bot_overview = db.collection('bot_overview').document('overview').get()

        if bot_overview.exists:
            data = bot_overview.to_dict()
            bots = data.get('bots', [])

            # Count total positions across all bots
            total_positions = 0
            position_details = []

            for bot in bots:
                bot_name = bot.get('name', 'Unknown')
                for bucket in bot.get('buckets', []):
                    shares = bucket.get('shares', 0)
                    if shares > 0:
                        total_positions += 1
                        symbol = bucket.get('symbol', 'Unknown')
                        bucket_type = bucket.get('bucket_type', 'Unknown')
                        position_details.append(f"{bot_name}/{bucket_type}: {shares} shares of {symbol}")

            # Update audit trail with position info
            switch_attempt['position_count'] = total_positions
            switch_attempt['position_details'] = position_details

            # Block profile switch if ANY positions are open
            if total_positions > 0:
                switch_attempt['positions_blocked'] = True
                switch_attempt['error_message'] = f"{total_positions} open position(s)"

                logger.warning(f"[SWITCH-PROFILE] BLOCKED: {total_positions} open position(s) found")
                logger.warning(f"[SWITCH-PROFILE] Positions: {position_details}")

                # Show first 3 positions in error message
                positions_str = "; ".join(position_details[:3])
                if len(position_details) > 3:
                    positions_str += f" and {len(position_details) - 3} more"

                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot switch profile: {total_positions} open position(s). Close all positions first. ({positions_str})"
                )

            logger.info("[SWITCH-PROFILE] ✓ Position check passed: No open positions")
        else:
            logger.info("[SWITCH-PROFILE] ✓ No bot_overview document (no positions)")

        # No positions - safe to proceed with switch
        logger.info(f"[SWITCH-PROFILE] Forwarding switch request to VM API (profile={profile})")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f'{BOT_CONTROL_API_URL}/switch-profile',
                json={'profile': profile},
                headers=_bot_control_headers()
            )

            # Parse response body (even if error status)
            result = response.json()

            logger.info(f"[SWITCH-PROFILE] VM response status={response.status_code}: {result.get('status')} - {result.get('message')}")

            # Check if VM returned an error
            if response.status_code != 200 or result.get('status') == 'error':
                # VM returned error - extract the actual error message
                error_msg = result.get('message', f'VM returned status {response.status_code}')
                switch_attempt['error_message'] = error_msg
                logger.warning(f"[SWITCH-PROFILE] VM rejected request: {error_msg}")
                raise HTTPException(status_code=400, detail=error_msg)

            # SUCCESS
            switch_attempt['status'] = 'success'
            switch_attempt['profile_confirmed'] = result.get('profile_confirmed', False)
            logger.info(f"[SWITCH-PROFILE] ✅ SUCCESS: Profile switched to {profile}")

            return result

    except HTTPException as e:
        # Re-raise HTTP exceptions (validation errors, position blocking)
        switch_attempt['error_message'] = str(e.detail)
        raise
    except httpx.HTTPError as e:
        switch_attempt['error_message'] = f"VM API error: {str(e)}"
        logger.error(f"[SWITCH-PROFILE] VM API communication failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to communicate with bot control API: {str(e)}")
    except Exception as e:
        switch_attempt['error_message'] = f"Unexpected error: {str(e)}"
        logger.error(f"[SWITCH-PROFILE] Unexpected error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # ALWAYS log to Firestore audit trail (success or failure)
        try:
            db = firestore.Client()
            audit_ref = db.collection('profile_switch_audit').add(switch_attempt)
            logger.info(f"[SWITCH-PROFILE] Audit trail logged: {audit_ref[1].id}")
        except Exception as audit_error:
            # Don't fail the request if audit logging fails, but log the error
            logger.error(f"[SWITCH-PROFILE] Failed to write audit trail: {audit_error}")

@app.get("/api/bot-control/profile-audit")
async def get_profile_audit(limit: int = 50):
    """
    Get profile switch audit trail from Firestore.

    Returns recent profile switch attempts (both successful and failed) for
    compliance and troubleshooting purposes.

    Query Parameters:
        limit: Maximum number of records to return (default: 50, max: 200)

    Returns:
        JSON: {
            "total": 15,
            "switches": [
                {
                    "id": "abc123",
                    "timestamp": "2026-08-16T10:30:00Z",
                    "status": "success",
                    "from_profile": "paper",
                    "to_profile": "live",
                    "user_ip": "192.168.1.100",
                    "positions_blocked": false,
                    "position_count": 0,
                    "error_message": null
                }
            ]
        }
    """
    try:
        # Validate limit
        limit = min(max(1, limit), 200)

        logger.info(f"[AUDIT] Fetching profile switch audit trail (limit={limit})")

        db = firestore.Client()

        # Query audit trail, ordered by timestamp descending
        audit_docs = (
            db.collection('profile_switch_audit')
            .order_by('timestamp', direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )

        switches = []
        for doc in audit_docs:
            data = doc.to_dict()
            # Convert Firestore timestamp to ISO string
            if 'timestamp' in data and data['timestamp']:
                data['timestamp'] = data['timestamp'].isoformat()
            switches.append({
                'id': doc.id,
                **data
            })

        logger.info(f"[AUDIT] Retrieved {len(switches)} audit records")

        return {
            'total': len(switches),
            'switches': switches
        }

    except Exception as e:
        logger.error(f"[AUDIT] Failed to fetch audit trail: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch audit trail: {str(e)}")

# Legacy /api/bot-control/status endpoint removed (2026-08-16)
# This endpoint checked Firestore heartbeats for old nvda-bot/msft-bot services
# Now using bot overview data directly in frontend (nvda_focus.js)
# for real-time bot status checks


# ============================================================================
#  Learning Candidates API Routes (Adaptive Learning System)
# ============================================================================

@app.get("/learning-review", response_class=HTMLResponse)
async def learning_review_page():
    """Serve the learning review dashboard page."""
    learning_review_path = os.path.join(TEMPLATES_DIR, "learning_review.html")
    try:
        return FileResponse(learning_review_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Learning review page not found")


@app.get("/api/learning-candidates")
async def get_learning_candidates():
    """
    Get all learning candidates from Firestore.

    Returns:
        {
            "findings": [
                {
                    "finding_id": "TIME_15",
                    "type": "eliminate",
                    "priority": "high",
                    "title": "Avoid trading at 15:00",
                    "description": "...",
                    "evidence": {...},
                    "proposed_rule": {...},
                    "backtest_results": {...},
                    "status": "pending_review",
                    "created_at": "2026-06-05T22:00:00"
                },
                ...
            ]
        }
    """
    try:
        db = firestore.Client()
        findings_ref = db.collection("learning_candidates")

        # Get all documents
        docs = findings_ref.stream()
        findings = []

        for doc in docs:
            data = doc.to_dict()
            findings.append(data)

        # Sort by priority (high -> medium -> low) then by created date
        priority_order = {"high": 0, "medium": 1, "low": 2}
        findings.sort(key=lambda x: (
            priority_order.get(x.get("priority", "low"), 3),
            x.get("created_at", "")
        ), reverse=False)

        return {"findings": findings}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch learning candidates: {str(e)}")


class LearningDecision(BaseModel):
    """Request model for learning candidate decision."""
    finding_id: str
    decision: str  # "approved" | "modified" | "rejected"
    user_notes: Optional[str] = ""


@app.post("/api/learning-candidates/decision")
async def handle_learning_decision(decision: LearningDecision):
    """
    Handle user's decision on a learning candidate.

    Args:
        decision: LearningDecision with finding_id, decision, and optional notes

    Returns:
        {"status": "success", "message": "Finding approved"}
    """
    try:
        db = firestore.Client()
        doc_ref = db.collection("learning_candidates").document(decision.finding_id)

        # Verify document exists
        doc = doc_ref.get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail="Finding not found")

        # Update status
        update_data = {
            "status": decision.decision,
            "user_notes": decision.user_notes,
            f"{decision.decision}_at": datetime.now().isoformat()
        }

        doc_ref.update(update_data)

        # If approved, you would trigger strategy update here
        # For now, just log it
        if decision.decision == "approved":
            # TODO: Implement strategy update logic
            # This would update bot config files and trigger redeployment
            pass

        return {
            "status": "success",
            "message": f"Finding {decision.decision} successfully",
            "finding_id": decision.finding_id
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process decision: {str(e)}")


@app.get("/api/learning-candidates/stats")
async def get_learning_stats():
    """
    Get statistics about learning candidates.

    Returns:
        {
            "total_pending": 5,
            "total_approved": 10,
            "total_rejected": 3,
            "by_priority": {"high": 2, "medium": 3, "low": 0},
            "by_type": {"eliminate": 3, "amplify": 1, "new_rule": 1,
                        "config_change": 0, "observation": 0},
            "potential_savings": 450.50
        }
    """
    try:
        db = firestore.Client()
        findings_ref = db.collection("learning_candidates")
        docs = findings_ref.stream()

        stats = {
            "total_pending": 0,
            "total_approved": 0,
            "total_rejected": 0,
            "by_priority": {"high": 0, "medium": 0, "low": 0},
            "by_type": {"eliminate": 0, "amplify": 0, "new_rule": 0,
                        "config_change": 0, "observation": 0},
            "potential_savings": 0.0
        }

        for doc in docs:
            data = doc.to_dict()
            status = data.get("status", "pending_review")

            if status == "pending_review":
                stats["total_pending"] += 1
            elif status == "approved":
                stats["total_approved"] += 1
            elif status == "rejected":
                stats["total_rejected"] += 1

            priority = data.get("priority", "low")
            if priority in stats["by_priority"]:
                stats["by_priority"][priority] += 1

            finding_type = data.get("type", "new_rule")
            if finding_type in stats["by_type"]:
                stats["by_type"][finding_type] += 1

            # Calculate potential savings from backtest results
            if status == "pending_review":
                backtest = data.get("backtest_results", {})
                improvement = backtest.get("pnl_improvement", 0)
                stats["potential_savings"] += improvement

        return stats

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch learning stats: {str(e)}")


# ---------------------------------------------------------------------------
# Governance review: exit-governance mirror, applied-changes ledger, adaptive
# engine actions, and the per-session exit-telemetry archive (Phase 6/7/9).
# ---------------------------------------------------------------------------

@app.get("/governance-review", response_class=HTMLResponse)
async def governance_review_page():
    """Serve the governance review dashboard page."""
    governance_path = os.path.join(TEMPLATES_DIR, "governance_review.html")
    try:
        return FileResponse(governance_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Governance review page not found")


@app.get("/api/exit-governance")
async def get_exit_governance():
    """
    Get the latest exit-governance mirror payload per symbol (Phase 7 live
    surfacing writes the strategy's decoded ExitDiff to the ``exit_governance``
    collection; this exposes it for the operator's review UI).

    Returns:
        {"governance": [{"symbol": "...", "data": {...}}]}
    """
    try:
        db = firestore.Client()
        docs = db.collection("exit_governance").stream()
        entries = []
        for doc in docs:
            data = doc.to_dict() or {}
            data.setdefault("doc_id", doc.id)
            if "auto_apply_governance" not in data:
                sym = data.get("symbol") or doc.id
                bot_params = load_bot_parameters(sym) if sym else None
                if bot_params and "auto_apply_governance" in bot_params:
                    data["auto_apply_governance"] = bool(bot_params["auto_apply_governance"])
                else:
                    data["auto_apply_governance"] = True  # TradingBotConfig default
            entries.append(data)
        return {"governance": entries}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch exit governance: {str(e)}")


@app.get("/api/applied-changes")
async def get_applied_changes(symbol: str = "", limit: int = 100):
    """
    Get applied-changes ledger records (Phase 9 M1 unified audit trail).

    Args:
        symbol: optional per-symbol filter
        limit: max records returned (newest first)

    Returns:
        {"changes": [{"engine": "...", "field": "...", "old_value": ...,
                      "new_value": ..., "status": ..., "applied_at": ...}]}
    """
    try:
        limit = max(1, min(limit, 500))
        db = firestore.Client()
        docs = db.collection("applied_changes").stream()
        changes = []
        for doc in docs:
            data = doc.to_dict() or {}
            data.setdefault("doc_id", doc.id)
            if symbol and data.get("symbol") != symbol:
                continue
            changes.append(data)
        changes.sort(key=lambda c: c.get("applied_at", ""), reverse=True)
        return {"changes": changes[:limit]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch applied changes: {str(e)}")


@app.get("/api/adaptive-actions")
async def get_adaptive_actions(limit: int = 100):
    """
    Get adaptive feedback engine actions (``adaptive_action_log``) with
    per-action resolution stats.

    Returns:
        {
            "actions": [ {...}, ... ],
            "stats": [ {"action_type": ..., "resolved": n, "unresolved": n,
                        "fired": n, "win_rate": 0.62} ]
        }
    """
    try:
        limit = max(1, min(limit, 500))
        db = firestore.Client()
        docs = db.collection("adaptive_action_log").stream()
        actions = []
        per_type = {}
        for doc in docs:
            data = doc.to_dict() or {}
            data.setdefault("doc_id", doc.id)
            actions.append(data)
            a_type = data.get("action_type", "unknown")
            bucket = per_type.setdefault(a_type, {"resolved": 0, "unresolved": 0, "wins": 0})
            if (data.get("outcome") or {}).get("resolved"):
                bucket["resolved"] += 1
                if (data.get("outcome") or {}).get("final_pnl", 0) > 0:
                    bucket["wins"] += 1
            else:
                bucket["unresolved"] += 1
        actions.sort(key=lambda a: a.get("created_at", ""), reverse=True)
        stats = []
        for a_type, bucket in sorted(per_type.items()):
            fired = bucket["resolved"] + bucket["unresolved"]
            stats.append({
                "action_type": a_type,
                "fired": fired,
                "resolved": bucket["resolved"],
                "unresolved": bucket["unresolved"],
                "win_rate": round(bucket["wins"] / bucket["resolved"], 3) if bucket["resolved"] else None,
            })
        return {"actions": actions[:limit], "stats": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch adaptive actions: {str(e)}")


def _has_telemetry_analytical_value(data: dict) -> bool:
    inv = data.get("inventory") or {}
    branches = inv.get("branches") or {}
    total_eval = sum(int(b.get("evaluated", 0) or 0) for b in branches.values())
    total_fired = sum(int(b.get("fired", 0) or 0) for b in branches.values())
    adaptive = inv.get("_adaptive_pnl") or {}
    total_eval += int(adaptive.get("evaluated", 0) or 0)
    total_fired += int(adaptive.get("fired", 0) or 0)
    stream = inv.get("_verdict_stream") or {}
    total_contended = sum(int(v or 0) for v in (stream.get("shadowed_satisfied") or {}).values())
    return (total_eval + total_fired + total_contended) > 0


@app.get("/api/exit-telemetry/snapshots")
async def get_exit_telemetry_snapshots(symbol: str = "", limit: int = 50, include_empty: bool = False):
    """
    Get the per-session exit-telemetry snapshot archive (Phase 6/7 follow-up).

    Args:
        symbol: optional per-symbol filter
        limit: max sessions returned (newest first)
        include_empty: whether to return zero-activity / unexercised sessions (default False)

    Returns:
        {"sessions": [{"symbol": ..., "session_id": ..., "archived_at": ...,
                       "inventory": {...}}]}
    """
    try:
        limit = max(1, min(limit, 500))
        fetch_limit = min(limit * 3, 500) if not include_empty else limit
        db = firestore.Client()
        docs = db.collection("exit_telemetry_snapshots").order_by(
            "archived_at", direction="DESCENDING").limit(fetch_limit).stream()
        sessions = []
        for doc in docs:
            data = doc.to_dict() or {}
            data.setdefault("doc_id", doc.id)
            if symbol and data.get("symbol") != symbol:
                continue
            if not include_empty and not _has_telemetry_analytical_value(data):
                continue
            sessions.append(data)
            if len(sessions) >= limit:
                break
        return {"sessions": sessions}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch exit telemetry sessions: {str(e)}")


@app.get("/api/historical-bars/{symbol}")
async def get_historical_bars(symbol: str, bars: int = 50):
    """
    Get historical price bars for charting from Firestore.

    BAR SIZE: 1 hour (matches bot trading timeframe)

    This returns the SAME 1-hour bars that the bot uses for SMA calculations
    and trading decisions, ensuring chart display matches bot logic exactly.
    The bot writes bars to Firestore with pre-calculated SMA5, SMA20, and SMA200
    values - the dashboard displays these exact values without recalculation.

    Args:
        symbol: Stock symbol (e.g., "NVDA")
        bars: Number of recent bars to return (default 50, max 100)

    Returns:
        [
            {
                "timestamp": "2026-07-13T10:00:00",
                "open": 210.13,
                "high": 211.00,
                "low": 209.57,
                "close": 210.97,
                "volume": 1234567,
                "sma5": 207.66,
                "sma20": 205.78,
                "sma200": 203.45
            },
            ...
        ]
    """
    try:
        # Get bot_id from symbol (nvda, msft, etc.)
        bot_id = symbol.lower()

        # Read historical bars from Firestore (stored by bot)
        doc = dashboard_data.db.collection('historical_bars').document(bot_id).get()

        if not doc.exists:
            print(f"No historical bars found for {bot_id}")
            return []

        data = doc.to_dict()
        stored_bars = data.get('bars', [])

        if not stored_bars:
            return []

        # Return the requested number of most recent bars (default 50, max 250)
        requested_bars = min(bars, 250)
        result = stored_bars[-requested_bars:] if len(stored_bars) > requested_bars else stored_bars

        print(f"Returning {len(result)} historical bars for {symbol} (from IBKR via Firestore)")
        return result

    except Exception as e:
        print(f"Error fetching historical bars from Firestore: {e}")
        return []


# ============================================================================
# OPTION EVENTS API (Phase 3 - Unified Dashboard Update)
# ============================================================================
# These endpoints support the new option events tracking architecture.
# Design: DESIGN_unified_dashboard_update.md
# Implementation Date: 2026-07-26
# ============================================================================

@app.get("/api/option-events/{entry_order_id}")
async def get_option_events(entry_order_id: int):
    """
    Fetch all option events linked to a trade by entry order ID.

    Returns events sorted by timestamp (chronological order).
    Used by the trade details modal to display option timeline.

    Performance: Returns empty result instantly for non-existent data.
    """
    try:
        # Query option events with limit (performance optimization)
        events_ref = dashboard_data.db.collection('option_events') \
            .where('stock_entry_order_id', '==', entry_order_id) \
            .limit(100) \
            .stream()

        events = []
        for doc in events_ref:
            event_data = doc.to_dict()
            events.append(event_data)

        # Sort by timestamp in Python (no Firestore index needed)
        events.sort(key=lambda x: x.get('timestamp', ''))

        return {
            "status": "success",
            "entry_order_id": entry_order_id,
            "event_count": len(events),
            "events": events
        }

    except Exception as e:
        logger.error(f"Failed to fetch option events: {e}")
        return {
            "status": "error",
            "message": str(e),
            "events": []
        }


@app.get("/api/stock-picks")
async def get_stock_picks(request: Request, response: Response):
    """
    Get current stock picks (top 5 daily picks from StockPicker).

    Features:
    - Response caching (30s TTL) to reduce Firestore reads
    - X-Cache-Status header shows cache hit/miss
    - Authentication required

    Returns:
        - picks: List of 0-5 dicts with ticker, scores, fundamentals
        - run_timestamp: When picks were last generated (ISO string)
        - pick_count: Number of picks (0-5)
        - avg_explosiveness: Average explosiveness score
        - sources_used: List of data sources used
        - message: Status message (if no picks)
        - status: Status code ('success', 'no_news', 'error', etc.)
    """
    # Check authentication (using existing pattern - same as other endpoints)
    session_id = request.cookies.get("dashboard_session")
    if not is_valid_session(session_id):
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Set strict anti-caching headers so browsers never display stale runs
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'

    now = datetime.now()

    # Check cache first (thread-safe), unless client requested a fresh run via _t
    force_refresh = request.query_params.get("_t") is not None
    if not force_refresh:
        with _stock_picks_cache['lock']:
            cached_data = _stock_picks_cache['data']
            cached_time = _stock_picks_cache['timestamp']

            if cached_data and cached_time:
                age_seconds = (now - cached_time).total_seconds()
                if age_seconds < STOCK_PICKS_CACHE_TTL_SECONDS:
                    response.headers['X-Cache-Status'] = f'HIT (age: {int(age_seconds)}s)'
                    return cached_data

    # Cache miss - fetch from Firestore
    response.headers['X-Cache-Status'] = 'MISS'

    try:
        # Read from Firestore (using existing pattern)
        doc = dashboard_data.stock_picks_ref.document('current').get()

        if doc.exists:
            data = doc.to_dict()

            # Convert Firestore Timestamp to ISO string for JSON serialization
            if 'run_timestamp' in data and data['run_timestamp']:
                try:
                    data['run_timestamp'] = data['run_timestamp'].isoformat()
                except AttributeError:
                    # Already a string (shouldn't happen, but handle gracefully)
                    pass

            # Cache the result
            with _stock_picks_cache['lock']:
                _stock_picks_cache['data'] = data
                _stock_picks_cache['timestamp'] = now

            return data
        else:
            # No picks available yet (not an error - just empty state)
            empty_result = {
                'picks': [],
                'pick_count': 0,
                'run_timestamp': None,
                'avg_explosiveness': 0,
                'sources_used': [],
                'message': 'No picks available yet - click "Run Now" to generate picks',
                'status': 'empty'
            }

            # Cache empty result too (avoid repeated Firestore reads)
            with _stock_picks_cache['lock']:
                _stock_picks_cache['data'] = empty_result
                _stock_picks_cache['timestamp'] = now

            return empty_result

    except Exception as e:
        logger.exception("Error fetching stock picks")
        raise HTTPException(status_code=500, detail=f"Failed to fetch stock picks: {str(e)}")


@app.post("/api/stock-picks/run")
async def run_stock_picker(request: Request):
    """
    Trigger StockPicker run on-demand (manual trigger).

    This runs the full pipeline: fetch news, rank, score, select top 5.
    Results are written to Firestore and immediately available.

    Features:
    - Concurrency control (only one run at a time globally)
    - Rate limiting (10 runs per hour per user)
    - Timeout (120 seconds max)
    - Invalidates cache after successful run

    Returns:
        - status: "success" or "error"
        - picks: List of selected picks (if successful)
        - pick_count: Number of picks generated (0-5)
        - message: Status message
        - duration_seconds: How long the run took

    Rate Limit:
        HTTP 429 if user exceeds 10 runs per hour

    Errors:
        - 401: Unauthorized (no valid session)
        - 429: Rate limit exceeded
        - 409: Already running (conflict)
        - 500: Internal error
        - 504: Timeout (exceeded 2 minutes)
    """
    # =========================================================================
    # AUTHENTICATION CHECK (using existing pattern - same as other endpoints)
    # =========================================================================
    session_id = request.cookies.get("dashboard_session")
    if not is_valid_session(session_id):
        raise HTTPException(status_code=401, detail="Unauthorized")

    # =========================================================================
    # RATE LIMITING (10 runs per hour per user)
    # =========================================================================
    now = datetime.now()
    one_hour_ago = now - timedelta(hours=1)

    # Clean old entries and check limit
    _stockpicker_run_history[session_id] = [
        ts for ts in _stockpicker_run_history[session_id] if ts > one_hour_ago
    ]

    if len(_stockpicker_run_history[session_id]) >= MAX_STOCKPICKER_RUNS_PER_HOUR:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {MAX_STOCKPICKER_RUNS_PER_HOUR} runs per hour. "
                   f"Please wait before trying again."
        )

    # Record this run attempt
    _stockpicker_run_history[session_id].append(now)

    # =========================================================================
    # CONCURRENCY CONTROL (only one run at a time globally)
    # =========================================================================
    with _stockpicker_lock:
        global _stockpicker_running, _stockpicker_future
        # Opportunistic recovery: if the previous run's done-callback has not
        # been dispatched yet, but the future is already finished, free the slot.
        if _stockpicker_running and _stockpicker_future is not None and _stockpicker_future.done():
            _stockpicker_running = False
            _stockpicker_future = None
        if _stockpicker_running:
            raise HTTPException(
                status_code=409,
                detail="StockPicker is already running. Please wait for current run to complete."
            )
        _stockpicker_running = True

    # =========================================================================
    # RUN STOCKPICKER WITH TIMEOUT
    # =========================================================================
    import time
    import asyncio
    start_time = time.time()

    try:
        # Import here to avoid circular dependencies
        from stockpicker.runner import run_stockpicker

        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(_stockpicker_executor, run_stockpicker)
    except Exception:
        # Could not even start the run - release the slot we just claimed,
        # otherwise every subsequent run would be rejected with 409 forever.
        _release_stockpicker_run_flag()
        logger.exception("Could not submit StockPicker run to executor")
        raise HTTPException(status_code=503, detail="Could not start StockPicker run")

    # The concurrency flag is released ONLY when the runner thread actually
    # finishes. A timeout below cancels our *wait*, not the thread - so the
    # flag must not be reset in a finally block (that allowed a second
    # pipeline run to execute concurrently with the first).
    _stockpicker_future = future
    future.add_done_callback(lambda _f: _release_stockpicker_run_flag())

    try:
        logger.info(f"Manual StockPicker run triggered by session: {session_id[:10]}...")

        # Run in thread pool with 120-second timeout.
        # asyncio.shield() prevents wait_for() from cancelling the underlying
        # future, keeping the done-callback tied to real thread completion.
        picks = await asyncio.wait_for(
            asyncio.shield(future),
            timeout=120.0
        )

        duration = round(time.time() - start_time, 2)

        # Invalidate cache so next GET request fetches fresh data
        with _stock_picks_cache['lock']:
            _stock_picks_cache['data'] = None
            _stock_picks_cache['timestamp'] = None

        picks_list = picks.get('picks', []) if isinstance(picks, dict) else (picks or [])
        custom_msg = picks.get('message') if isinstance(picks, dict) else None
        status_msgs = picks.get('status_messages', []) if isinstance(picks, dict) else []
        fallback_reasons = picks.get('fallback_reasons', []) if isinstance(picks, dict) else []

        # Build response
        resp = {
            'status': 'success',
            'picks': picks_list,
            'pick_count': len(picks_list),
            'message': custom_msg or (
                f'Generated {len(picks_list)} picks successfully'
                if picks_list else
                'No picks generated (no explosive news found with threshold ≥7.5)'
            ),
            'status_messages': status_msgs,
            'fallback_reasons': fallback_reasons,
            'duration_seconds': duration
        }
        if isinstance(picks, dict):
            resp['actionable_leads'] = picks.get('actionable_leads', [])
            resp['growth_picks'] = picks.get('growth_picks', [])
            resp['income_picks'] = picks.get('income_picks', [])
            resp['already_accepted'] = picks.get('already_accepted', [])
            resp['rejected_cooldown'] = picks.get('rejected_cooldown', [])
            resp['summary'] = picks.get('summary', {})
            resp['overall_data_quality'] = picks.get('overall_data_quality', 'FULL')
            resp['degraded_candidate_count'] = picks.get('degraded_candidate_count', 0)
            resp['missing_sources_detected'] = picks.get('missing_sources_detected', [])
            resp['run_timestamp'] = datetime.now(timezone.utc).isoformat()
        return resp

    except asyncio.TimeoutError:
        # Timeout after 2 minutes
        duration = round(time.time() - start_time, 2)
        logger.error(f"StockPicker run exceeded 120-second timeout (ran for {duration}s)")

        return {
            'status': 'error',
            'picks': [],
            'pick_count': 0,
            'message': f'StockPicker run exceeded 2-minute timeout (ran for {duration}s). '
                      'This may indicate an issue with external APIs.',
            'duration_seconds': duration
        }

    except Exception as e:
        # Any other error
        duration = round(time.time() - start_time, 2)
        logger.exception(f"Manual StockPicker run failed after {duration}s")

        return {
            'status': 'error',
            'picks': [],
            'pick_count': 0,
            'message': f'StockPicker run failed: {str(e)}',
            'duration_seconds': duration
        }

    # NOTE: no `finally` clause here. _stockpicker_running is released by the
    # done-callback registered above, i.e. only once the runner thread has
    # genuinely finished (including after a 120s timeout, where the thread
    # keeps running in the background and must keep holding the slot).


# =============================================================================
# STOCKPICKER HITL & BOT PROVISIONING GATEWAYS
# =============================================================================

@app.post("/api/stock-picks/accept")
async def accept_stock_pick(request: Request):
    """
    Accept a StockPicker lead candidate.
    Advances status: PENDING_REVIEW -> ACCEPTED.
    """
    session_id = request.cookies.get("dashboard_session")
    if not is_valid_session(session_id):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    symbol = (body.get("symbol") or "").upper().strip()
    if not symbol or not re.match(r"^[A-Z0-9.\-]{1,10}$", symbol):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    notes = body.get("notes", "")

    try:
        from stockpicker.firestore_utils import run_stock_pick_rmw

        doc_ref = dashboard_data.stock_picks_ref.document("current")
        now_str = datetime.now(timezone.utc).isoformat()

        def _mutate_accept(data):
            found = False
            target_candidate = None
            for key in ["actionable_leads", "growth_picks", "income_picks", "picks"]:
                if key in data and isinstance(data[key], list):
                    for item in data[key]:
                        if (item.get("ticker") or item.get("symbol") or "").upper() == symbol:
                            item["status"] = "ACCEPTED"
                            item["accepted_at"] = now_str
                            item["notes"] = notes
                            found = True
                            if not target_candidate:
                                target_candidate = dict(item)

            if not found:
                target_candidate = {
                    "symbol": symbol,
                    "ticker": symbol,
                    "status": "ACCEPTED",
                    "accepted_at": now_str,
                    "notes": notes,
                }

            # Stamp the fields the In-Flight Sentinel needs as its ΔS baseline
            # and Track-2 switch. UI-accepted leads store `composite_score`, not
            # `score`, so without this the sentinel saw no score and defaulted to
            # a fabricated 75.0 baseline + always-GROWTH track.
            if not target_candidate.get("strategy_track"):
                target_candidate["strategy_track"] = (
                    target_candidate.get("track")
                    or (body.get("strategy_track") or "").upper()
                    or "GROWTH"
                )
            if target_candidate.get("initial_score") is None:
                target_candidate["initial_score"] = (
                    target_candidate.get("composite_score")
                    or target_candidate.get("fundamental_score")
                    or target_candidate.get("score")
                )
            if target_candidate.get("composite_score") is None and target_candidate.get("initial_score") is not None:
                target_candidate["composite_score"] = target_candidate["initial_score"]
            if target_candidate.get("score") is None and target_candidate.get("initial_score") is not None:
                target_candidate["score"] = target_candidate["initial_score"]

            already_accepted = data.get("already_accepted", [])
            exists_idx = next((i for i, x in enumerate(already_accepted) if (x.get("ticker") or x.get("symbol") or "").upper() == symbol), None)
            if exists_idx is not None:
                already_accepted[exists_idx].update(target_candidate)
            else:
                already_accepted.append(target_candidate)
            data["already_accepted"] = already_accepted

            for key in ["actionable_leads", "growth_picks", "income_picks"]:
                if key in data and isinstance(data[key], list):
                    data[key] = [x for x in data[key] if (x.get("ticker") or x.get("symbol") or "").upper() != symbol]
            return True

        # Read-modify-write `stock_picks/current` atomically. The document's HITL
        # arrays make every transition a full read-modify-write; a bare
        # get()->set() is last-write-wins across Cloud Run instances. The helper
        # uses a Firestore transaction in production and a plain RMW in tests /
        # local dev (see stockpicker/firestore_utils.py). `False` here means the
        # document was missing, so it maps to the same 404 as before.
        if not run_stock_pick_rmw(_mutate_accept, doc_ref=doc_ref, db=getattr(dashboard_data, "db", None)):
            raise HTTPException(status_code=404, detail="No stock picks found")

        with _stock_picks_cache["lock"]:
            _stock_picks_cache["data"] = None
            _stock_picks_cache["timestamp"] = None

        return {"status": "success", "symbol": symbol, "lead_status": "ACCEPTED", "updated_at": now_str}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error accepting stock pick {symbol}")
        raise HTTPException(status_code=500, detail=f"Failed to accept stock pick: {str(e)}")


@app.post("/api/stock-picks/reject")
async def reject_stock_pick(request: Request):
    """
    Reject a StockPicker lead candidate with structured reason tag.
    Advances status: PENDING_REVIEW -> REJECTED and enforces 7-day cooldown.
    """
    session_id = request.cookies.get("dashboard_session")
    if not is_valid_session(session_id):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    symbol = (body.get("symbol") or "").upper().strip()
    if not symbol or not re.match(r"^[A-Z0-9.\-]{1,10}$", symbol):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    reason = body.get("reason", "OPERATOR_DISCRETION")
    notes = body.get("notes", "")

    try:
        from stockpicker.firestore_utils import run_stock_pick_rmw

        doc_ref = dashboard_data.stock_picks_ref.document("current")
        now_dt = datetime.now(timezone.utc)
        now_str = now_dt.isoformat()
        cooldown_expires = (now_dt + timedelta(days=7)).isoformat()

        def _mutate_reject(data):
            for key in ["actionable_leads", "growth_picks", "income_picks", "picks"]:
                if key in data and isinstance(data[key], list):
                    data[key] = [x for x in data[key] if (x.get("ticker") or x.get("symbol") or "").upper() != symbol]

            rejected_cooldown = data.get("rejected_cooldown", [])
            rej_entry = {
                "symbol": symbol,
                "ticker": symbol,
                "status": "REJECTED",
                "rejection_reason": reason,
                "notes": notes,
                "rejected_at": now_str,
                "cooldown_expires": cooldown_expires
            }
            idx = next((i for i, x in enumerate(rejected_cooldown) if (x.get("ticker") or x.get("symbol") or "").upper() == symbol), None)
            if idx is not None:
                rejected_cooldown[idx] = rej_entry
            else:
                rejected_cooldown.append(rej_entry)
            data["rejected_cooldown"] = rejected_cooldown
            return True

        # Same atomic RMW as accept (see stockpicker/firestore_utils.py); the
        # `rejected_cooldown` array is the shared resource being raced on.
        if not run_stock_pick_rmw(_mutate_reject, doc_ref=doc_ref, db=getattr(dashboard_data, "db", None)):
            raise HTTPException(status_code=404, detail="No stock picks found")

        with _stock_picks_cache["lock"]:
            _stock_picks_cache["data"] = None
            _stock_picks_cache["timestamp"] = None

        return {"status": "success", "symbol": symbol, "lead_status": "REJECTED", "cooldown_expires": cooldown_expires}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error rejecting stock pick {symbol}")
        raise HTTPException(status_code=500, detail=f"Failed to reject stock pick: {str(e)}")


@app.get("/api/stock-picks/parameter-advice")
async def get_parameter_advice(
    request: Request,
    symbol: str,
    strategy_track: str = "GROWTH",
    capital_allocation: float = 10000.0,
    cc_mode: Optional[str] = None
):
    """
    Get dynamic Quant/AI expert parameter advice for a candidate symbol.
    """
    session_id = request.cookies.get("dashboard_session")
    if not is_valid_session(session_id):
        raise HTTPException(status_code=401, detail="Unauthorized")

    sym = symbol.upper().strip()
    if not sym or not re.match(r"^[A-Z0-9.\-]{1,10}$", sym):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    from stockpicker.parameter_advisor import generate_expert_parameter_advice

    price = None
    atr_pct = None
    iv = None
    solvency = None

    try:
        cached = {}
        with _stock_picks_cache['lock']:
            cached = _stock_picks_cache.get('data') or {}
        if not cached:
            doc = dashboard_data.stock_picks_ref.document('current').get()
            if doc.exists:
                cached = doc.to_dict() or {}
        all_picks = (cached.get("actionable_leads", []) +
                     cached.get("growth_picks", []) +
                     cached.get("income_picks", []) +
                     cached.get("already_accepted", []))
        match = next((p for p in all_picks if (p.get("ticker") or p.get("symbol") or "").upper() == sym), None)
        if match:
            price = match.get("current_price")
            atr_val = match.get("est_atr_pct")
            atr_pct = (atr_val / 100.0) if (atr_val and atr_val > 0) else None
            iv = match.get("implied_volatility")
            dossier = match.get("evidence_dossier") or {}
            solvency = dossier.get("solvency_rating")
            is_degraded = match.get("is_degraded") or dossier.get("is_degraded", False)
            missing_sources = match.get("missing_sources") or dossier.get("missing_sources", [])
            degradation_warnings = match.get("degradation_warnings") or dossier.get("degradation_warnings", [])
    except Exception:
        pass

    try:
        advice = generate_expert_parameter_advice(
            symbol=sym,
            strategy_track=strategy_track,
            capital_allocation=capital_allocation,
            current_price=price,
            est_atr_pct=atr_pct,
            implied_volatility=iv,
            solvency_rating=solvency,
            is_degraded=is_degraded if 'is_degraded' in locals() else False,
            missing_sources=missing_sources if 'missing_sources' in locals() else None,
            degradation_warnings=degradation_warnings if 'degradation_warnings' in locals() else None,
            cc_mode=cc_mode
        )
        return advice
    except Exception as e:
        logger.exception(f"Error generating parameter advice for {sym}")
        raise HTTPException(status_code=500, detail=f"Failed to generate parameter advice: {e}")


@app.post("/api/stock-picks/provision-bot")
async def provision_bot_from_pick(request: Request):
    """
    Automated bot provisioning workflow with expert parameter tailoring and rollback:
    1. Allocate next sequential client_id
    2. Instantiate bots/{symbol.lower()}.json using expert quant defaults or customized parameters
    3. Update bots.json registry
    4. Update Firestore stock_picks/current status to PROVISIONED
    5. On error: rollback registry changes and record PROVISIONING_FAILED status with root-cause reason
    """
    session_id = request.cookies.get("dashboard_session")
    if not is_valid_session(session_id):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    symbol = (body.get("symbol") or "").upper().strip()
    if not symbol or not re.match(r"^[A-Z0-9.\-]{1,10}$", symbol):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    strategy_track = (body.get("strategy_track") or "GROWTH").upper()
    capital_allocation = float(body.get("capital_allocation") or 10000.0)
    strategy = body.get("strategy", "sma_crossover")
    custom_params = body.get("custom_params") or {}

    config_paths = [
        os.path.join(os.path.dirname(__file__), "..", "tradingbot-config", "bots.json"),
        os.path.join(os.path.dirname(__file__), "config", "bots.json"),
    ]
    bots_config_path = None
    for p in config_paths:
        if os.path.exists(p):
            bots_config_path = p
            break

    stage = "LOCATING_CONFIG"
    bots_json_modified = False
    written_bot_files = []
    bot_id = f"{symbol.lower()}_sma"

    try:
        if not bots_config_path:
            raise RuntimeError("bots.json configuration file not found in known config paths")

        # Load the real candidate record so (a) the expert advisor is fed actual
        # price / ATR / solvency instead of its $100 & 2% defaults, and (b) the
        # registry + accepted record carry the score/track the Sentinel needs
        # for ΔS and the INCOME (Track-2) switch.
        pick_ctx = {}
        try:
            _pdoc = dashboard_data.stock_picks_ref.document("current").get()
            if getattr(_pdoc, "exists", False):
                _pdata = _pdoc.to_dict() or {}
                _pool = (_pdata.get("actionable_leads", []) + _pdata.get("growth_picks", []) +
                         _pdata.get("income_picks", []) + _pdata.get("already_accepted", []))
                pick_ctx = next((p for p in _pool
                                 if (p.get("ticker") or p.get("symbol") or "").upper() == symbol), {}) or {}
        except Exception as _pick_err:
            logger.info(f"[PROVISION] Candidate context lookup skipped for {symbol}: {_pick_err}")

        initial_score = (pick_ctx.get("initial_score") or pick_ctx.get("composite_score")
                         or pick_ctx.get("fundamental_score") or pick_ctx.get("score"))

        stage = "UPDATING_BOTS_REGISTRY"
        with _bot_config_cache['lock']:
            with open(bots_config_path, "r") as f:
                bots_config = json.load(f)

            bots = bots_config.get("bots", [])
            for b in bots:
                if b.get("symbol", "").upper() == symbol:
                    return {
                        "status": "already_exists",
                        "symbol": symbol,
                        "bot_id": b.get("name"),
                        "client_id": b.get("client_id"),
                        "message": f"Bot for {symbol} is already registered as {b.get('name')} (client_id: {b.get('client_id')})"
                    }

            max_id = max([b.get("client_id", 0) for b in bots], default=2)
            next_id = max(3, max_id + 1)

            new_bot_entry = {
                "name": bot_id,
                "symbol": symbol,
                "client_id": next_id,
                "script": "bots/UniversalSMABot.py",
                "strategy": strategy,
                "enabled": True,
                "strategy_track": strategy_track,
                "initial_score": initial_score,
                "description": f"{symbol} SMA 5/20 trader ({strategy_track.lower()})"
            }
            bots.append(new_bot_entry)
            bots_config["bots"] = bots

            with open(bots_config_path, "w") as f:
                json.dump(bots_config, f, indent=2)
            bots_json_modified = True

            _bot_config_cache["config"] = None
            _bot_config_cache["mtime"] = None

        stage = "WRITING_BOT_PARAMS"
        target_dirs = [
            os.path.join(os.path.dirname(bots_config_path), "bots")
        ]
        alt_bot_dir = os.path.join(os.path.dirname(__file__), "config", "bots")
        if os.path.exists(alt_bot_dir) and alt_bot_dir not in target_dirs:
            target_dirs.append(alt_bot_dir)

        from stockpicker.parameter_advisor import generate_expert_parameter_advice

        # F6: feed the advisor the real candidate metrics (None when unknown ->
        # parameter_advisor's explicit defaults), rather than calling it with no
        # inputs at all and silently sizing against a fabricated $100 / 2% ATR.
        _atr_val = pick_ctx.get("est_atr_pct")
        _dossier = pick_ctx.get("evidence_dossier") or {}
        chosen_cc_mode = (custom_params.get("cc_mode") or "").upper().strip()
        advice = generate_expert_parameter_advice(
            symbol=symbol,
            strategy_track=strategy_track,
            capital_allocation=capital_allocation,
            current_price=pick_ctx.get("current_price") or pick_ctx.get("price"),
            est_atr_pct=(_atr_val / 100.0) if (_atr_val and _atr_val > 0) else None,
            implied_volatility=pick_ctx.get("implied_volatility"),
            solvency_rating=_dossier.get("solvency_rating"),
            is_degraded=bool(pick_ctx.get("is_degraded") or _dossier.get("is_degraded", False)),
            missing_sources=pick_ctx.get("missing_sources") or _dossier.get("missing_sources", []),
            degradation_warnings=pick_ctx.get("degradation_warnings") or _dossier.get("degradation_warnings", []),
            cc_mode=chosen_cc_mode or None,
        )
        recommended = advice["recommended_params"]

        template_path = os.path.join(os.path.dirname(bots_config_path), "bots", "nvda.json")
        bot_params = {}
        if os.path.exists(template_path):
            with open(template_path, "r") as f:
                bot_params = json.load(f)

        # Merge base with expert quant parameters
        bot_params["symbol"] = symbol
        bot_params["description"] = f"{symbol} automated trader - {strategy_track} strategy ({advice['risk_tier']} risk)"

        if "capital" not in bot_params:
            bot_params["capital"] = {}
        bot_params["capital"].update(recommended["capital"])
        if custom_params.get("capital_per_bucket_long"):
            bot_params["capital"]["capital_per_bucket_long"] = float(custom_params["capital_per_bucket_long"])

        if "atr_parameters" not in bot_params:
            bot_params["atr_parameters"] = {}
        bot_params["atr_parameters"].update(recommended["atr_parameters"])
        if custom_params.get("trailing_stop_atr_multiplier"):
            bot_params["atr_parameters"]["trailing_stop_atr_multiplier"] = float(custom_params["trailing_stop_atr_multiplier"])
        if custom_params.get("position_min_shares"):
            bot_params["atr_parameters"]["position_min_shares"] = int(custom_params["position_min_shares"])

        if "covered_calls" not in bot_params:
            bot_params["covered_calls"] = {}
        bot_params["covered_calls"].update(recommended["covered_calls"])
        if chosen_cc_mode == "EQUITY" or custom_params.get("use_covered_calls") is False:
            bot_params["covered_calls"]["use_covered_calls"] = False
            bot_params["covered_calls"]["cc_mode"] = "EQUITY"
            bot_params["covered_calls"]["call_expiration_days"] = 0
        elif chosen_cc_mode:
            bot_params["covered_calls"]["use_covered_calls"] = True
            bot_params["covered_calls"]["cc_mode"] = chosen_cc_mode
            if custom_params.get("call_expiration_days") is not None:
                bot_params["covered_calls"]["call_expiration_days"] = int(custom_params["call_expiration_days"])
        elif custom_params.get("call_expiration_days") is not None:
            bot_params["covered_calls"]["call_expiration_days"] = int(custom_params["call_expiration_days"])

        for b_dir in target_dirs:
            os.makedirs(b_dir, exist_ok=True)
            target_json = os.path.join(b_dir, f"{symbol.lower()}.json")
            with open(target_json, "w") as f:
                json.dump(bot_params, f, indent=2)
            # Track for rollback so a later failure cannot leave an orphan
            # bot config that is absent from bots.json.
            written_bot_files.append(target_json)

        stage = "UPDATING_FIRESTORE"
        now_str = datetime.now(timezone.utc).isoformat()
        try:
            db_client = getattr(dashboard_data, "db", None) or firestore.Client()
            db_client.collection("bots").document(f"bot_{next_id}").set({
                "bot_id": bot_id,
                "client_id": next_id,
                "symbol": symbol,
                "strategy_track": strategy_track,
                "bot_params": bot_params,
                "provisioned_at": now_str
            }, merge=True)
        except Exception as fs_bot_err:
            logger.warning(f"Could not persist bot params to Firestore bots/bot_{next_id}: {fs_bot_err}")

        try:
            from stockpicker.firestore_utils import run_stock_pick_rmw

            doc_ref = dashboard_data.stock_picks_ref.document("current")

            def _mutate_provision(data):
                nonlocal pick_ctx
                # Locate candidate in data if pick_ctx was empty during pre-fetch
                if not pick_ctx:
                    for k in ["actionable_leads", "growth_picks", "income_picks", "already_accepted"]:
                        for x in data.get(k, []):
                            if (x.get("ticker") or x.get("symbol") or "").upper() == symbol:
                                pick_ctx = dict(x)
                                break
                        if pick_ctx:
                            break

                for k in ["actionable_leads", "growth_picks", "income_picks"]:
                    if k in data and isinstance(data[k], list):
                        data[k] = [x for x in data[k] if (x.get("ticker") or x.get("symbol") or "").upper() != symbol]

                already_accepted = data.get("already_accepted", [])
                match = next((x for x in already_accepted if (x.get("ticker") or x.get("symbol") or "").upper() == symbol), None)
                if match:
                    match["status"] = "PROVISIONED"
                    match["bot_id"] = bot_id
                    match["client_id"] = next_id
                    match["provisioned_at"] = now_str
                    match["dashboard_link"] = f"/bot-focus?bot={next_id}"
                    match["status_label"] = f"Bot Active (client_id: {next_id})"
                    match.pop("provisioning_error", None)
                    match.pop("failure_stage", None)
                    # Persist the Sentinel's ΔS baseline & Track-2 switch.
                    if not match.get("strategy_track"):
                        match["strategy_track"] = strategy_track
                    if match.get("initial_score") is None and initial_score is not None:
                        match["initial_score"] = initial_score
                    if match.get("composite_score") is None and initial_score is not None:
                        match["composite_score"] = initial_score
                    if match.get("score") is None and initial_score is not None:
                        match["score"] = initial_score
                    if pick_ctx:
                        for field in ["evidence_dossier", "catalyst", "fundamental_reasons", "industry", "current_price", "est_atr_pct"]:
                            if not match.get(field) and pick_ctx.get(field):
                                match[field] = pick_ctx[field]
                else:
                    cand_entry = dict(pick_ctx) if pick_ctx else {}
                    cand_entry.update({
                        "symbol": symbol,
                        "ticker": symbol,
                        "strategy_track": strategy_track,
                        "initial_score": initial_score,
                        "composite_score": cand_entry.get("composite_score") if cand_entry.get("composite_score") is not None else initial_score,
                        "score": cand_entry.get("score") if cand_entry.get("score") is not None else initial_score,
                        "status": "PROVISIONED",
                        "bot_id": bot_id,
                        "client_id": next_id,
                        "provisioned_at": now_str,
                        "dashboard_link": f"/bot-focus?bot={next_id}",
                        "status_label": f"Bot Active (client_id: {next_id})"
                    })
                    already_accepted.append(cand_entry)
                data["already_accepted"] = already_accepted
                return True

            # Atomic RMW (see stockpicker/firestore_utils.py). A missing document
            # is a no-op, matching the previous `if doc.exists` guard, and any
            # write error stays non-fatal for the already-provisioned bot.
            run_stock_pick_rmw(_mutate_provision, doc_ref=doc_ref, db=getattr(dashboard_data, "db", None))
        except Exception as fs_err:
            logger.warning(f"Could not update Firestore stock picks status: {fs_err}")

        with _bot_config_cache["lock"]:
            _bot_config_cache["config"] = None
            _bot_config_cache["mtime"] = None
        with _stock_picks_cache["lock"]:
            _stock_picks_cache["data"] = None
            _stock_picks_cache["timestamp"] = None

        # Step 4: Notify VM Bot Control API if reachable (Phase 5 hot-reload hook)
        try:
            import httpx
            async with httpx.AsyncClient(timeout=4.0) as client:
                vm_resp = await client.post(
                    f"{BOT_CONTROL_API_URL}/provision-bot",
                    json={
                        "bot_id": bot_id,
                        "client_id": next_id,
                        "symbol": symbol,
                        "strategy_track": strategy_track
                    },
                    headers=_bot_control_headers()
                )
                logger.info(f"VM /provision-bot response for {symbol}: {vm_resp.status_code}")
        except Exception as vm_err:
            logger.info(f"VM bot control API notification skipped or unreachable (non-fatal): {vm_err}")

        return {
            "status": "success",
            "bot_id": bot_id,
            "client_id": next_id,
            "symbol": symbol,
            "message": f"Successfully provisioned bot {bot_id} (client_id: {next_id}) for {symbol}",
            "applied_params": bot_params.get("capital")
        }

    except Exception as e:
        logger.exception(f"Error provisioning bot for {symbol} at stage {stage}")
        # Rollback bots.json addition if failure happened afterwards
        if bots_json_modified and bots_config_path and os.path.exists(bots_config_path):
            try:
                with _bot_config_cache['lock']:
                    with open(bots_config_path, "r") as f:
                        rollback_cfg = json.load(f)
                    rollback_cfg["bots"] = [b for b in rollback_cfg.get("bots", []) if b.get("name") != bot_id]
                    with open(bots_config_path, "w") as f:
                        json.dump(rollback_cfg, f, indent=2)
                    _bot_config_cache["config"] = None
                    _bot_config_cache["mtime"] = None
                logger.info(f"Successfully rolled back bots.json entry for {bot_id}")
            except Exception as rb_err:
                logger.error(f"Rollback failed for {bot_id}: {rb_err}")

        # Remove orphan bot parameter files written before the failure, so a
        # rolled-back bots.json entry does not leave a dangling bots/{symbol}.json.
        for _bf in written_bot_files:
            try:
                if os.path.exists(_bf):
                    os.remove(_bf)
                    logger.info(f"Rollback removed bot params file {_bf}")
            except Exception as rb_file_err:
                logger.error(f"Rollback failed to remove {_bf}: {rb_file_err}")

        # Update Firestore with PROVISIONING_FAILED status and failure reason
        try:
            doc_ref = dashboard_data.stock_picks_ref.document("current")
            doc = doc_ref.get()
            if doc.exists:
                data = doc.to_dict() or {}
                now_fail = datetime.now(timezone.utc).isoformat()
                suggested_actions = {
                    "LOCATING_CONFIG": "Verify bots.json exists in tradingbot-config or config/ directory.",
                    "UPDATING_BOTS_REGISTRY": "Inspect bots.json file write permissions and disk availability.",
                    "WRITING_BOT_PARAMS": "Ensure config/bots/ directory exists with write permissions.",
                    "UPDATING_FIRESTORE": "Check Firestore database connectivity and retry."
                }
                action_text = suggested_actions.get(stage, "Check application logs, verify permissions, and retry.")

                for track_key in ["actionable_leads", "growth_picks", "income_picks"]:
                    for item in data.get(track_key, []):
                        if (item.get("ticker") or item.get("symbol") or "").upper() == symbol:
                            item["status"] = "PROVISIONING_FAILED"
                            item["failure_reason"] = str(e)
                            item["failure_stage"] = stage
                            item["failed_at"] = now_fail
                            item["suggested_action"] = action_text

                for item in data.get("already_accepted", []):
                    if (item.get("ticker") or item.get("symbol") or "").upper() == symbol:
                        item["status"] = "PROVISIONING_FAILED"
                        item["failure_reason"] = str(e)
                        item["failure_stage"] = stage
                        item["failed_at"] = now_fail
                        item["suggested_action"] = action_text

                doc_ref.set(data)
                with _stock_picks_cache["lock"]:
                    _stock_picks_cache["data"] = None
                    _stock_picks_cache["timestamp"] = None
        except Exception as fs_fail_err:
            logger.warning(f"Could not persist PROVISIONING_FAILED status to Firestore: {fs_fail_err}")

        raise HTTPException(
            status_code=500,
            detail=f"Provisioning failed at stage [{stage}]: {str(e)}"
        )


# =============================================================================
# STOCKPICKER CLOSED-LOOP FEEDBACK ENDPOINTS (Phase 7)
# =============================================================================

@app.get("/api/stock-picks/feedback/status")
async def get_feedback_status(request: Request):
    """
    Get the current status of the Closed-Loop Feedback Engine (Phase 7):
    - Factor IC ledger & active dynamic weight multipliers (Loop A)
    - Execution performance feedback aggregates (Loop B)
    - Rejection pattern analysis & suggested filter adjustments
    """
    session_id = request.cookies.get("dashboard_session")
    if not is_valid_session(session_id):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        from stockpicker.feedback_engine import (
            compute_execution_summary,
            load_weight_adjustments,
        )

        db = dashboard_data.db
        factor_ledger = {}
        rejection_analysis = {}

        if db:
            fl_doc = db.collection("stockpicker_factor_ledger").document("current").get()
            if fl_doc.exists:
                factor_ledger = fl_doc.to_dict()

            rej_doc = db.collection("stockpicker_rejection_analysis").document("current").get()
            if rej_doc.exists:
                rejection_analysis = rej_doc.to_dict()

            execution_summary = compute_execution_summary(db)
            weight_multipliers = load_weight_adjustments(db)
        else:
            execution_summary = {"status": "no_database"}
            weight_multipliers = {}

        return {
            "status": "success",
            "factor_ledger": factor_ledger,
            "weight_multipliers": weight_multipliers,
            "execution_summary": execution_summary,
            "rejection_analysis": rejection_analysis,
        }
    except Exception as e:
        logger.exception("Error getting feedback status")
        raise HTTPException(status_code=500, detail=f"Failed to fetch feedback status: {e}")


@app.post("/api/stock-picks/feedback/run-audit")
async def trigger_feedback_audit(request: Request):
    """
    Manually trigger the full closed-loop feedback engine:
    1. Forward alpha audit (t+1, t+5, t+20 vs benchmarks)
    2. Factor IC computation & dynamic weight adaptation
    3. Trade execution summary
    4. Rejection pattern analysis
    5. 90-day archive TTL cleanup
    """
    session_id = request.cookies.get("dashboard_session")
    if not is_valid_session(session_id):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        from stockpicker.feedback_engine import run_feedback_loop
        db = dashboard_data.db
        if not db:
            raise HTTPException(status_code=500, detail="Firestore database unavailable")

        results = run_feedback_loop(db)
        return {"status": "success", "feedback_results": results}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error running feedback audit")
        raise HTTPException(status_code=500, detail=f"Feedback audit failed: {e}")


@app.post("/api/stock-picks/feedback/execution")
async def submit_execution_feedback(request: Request):
    """
    Ingest trade execution feedback from trading bots when a position is closed.
    Links realized P&L and holding duration back to the immutable stock pick snapshot.
    """
    session_id = request.cookies.get("dashboard_session")
    if not is_valid_session(session_id):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    pick_id = body.get("pick_id")
    if not pick_id:
        raise HTTPException(status_code=400, detail="Missing required field: pick_id")

    realized_pnl = body.get("realized_pnl")
    if realized_pnl is None:
        raise HTTPException(status_code=400, detail="Missing required field: realized_pnl")

    trade_duration_days = body.get("trade_duration_days", 1)
    exit_reason = body.get("exit_reason", "manual_exit")
    slippage = body.get("slippage")
    execution_mode = body.get("execution_mode", "PAPER")
    metadata = body.get("metadata", {})

    try:
        from stockpicker.feedback_engine import ingest_execution_feedback
        db = dashboard_data.db
        res = ingest_execution_feedback(
            db=db,
            pick_id=pick_id,
            realized_pnl=float(realized_pnl),
            trade_duration_days=int(trade_duration_days),
            exit_reason=str(exit_reason),
            slippage=float(slippage) if slippage is not None else None,
            execution_mode=str(execution_mode),
            metadata=metadata,
        )
        return res
    except Exception as e:
        logger.exception(f"Error ingesting execution feedback for {pick_id}")
        raise HTTPException(status_code=500, detail=f"Failed to ingest feedback: {e}")


# =============================================================================
# IN-FLIGHT THESIS SENTINEL ENDPOINTS (Phase 6)
# =============================================================================

@app.get("/api/stock-picks/sentinel/status")
async def get_sentinel_status(request: Request):
    """
    Get current thesis telemetry and degradation states for all active bot positions.
    Returns status: 'disabled' if the feature is switched off.
    """
    session_id = request.cookies.get("dashboard_session")
    if not is_valid_session(session_id):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        from stockpicker.sentinel import audit_in_flight_positions, is_sentinel_enabled
        if not is_sentinel_enabled():
            return {
                "status": "disabled",
                "enabled": False,
                "message": "In-Flight Thesis Sentinel & Downstream Adaptive Gateway is currently switched off.",
            }
        db = dashboard_data.db
        summary = audit_in_flight_positions(db)
        return summary
    except Exception as e:
        logger.exception("Error checking sentinel status")
        raise HTTPException(status_code=500, detail=f"Failed to check sentinel status: {e}")


@app.post("/api/stock-picks/sentinel/audit")
async def trigger_sentinel_audit(request: Request):
    """
    Trigger manual pre-market in-flight position health audit.
    Evaluates score deltas, flags invalidations, and communicates action instructions to bots.
    If switched off, returns disabled status unless ?force=true is provided.
    """
    session_id = request.cookies.get("dashboard_session")
    if not is_valid_session(session_id):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        force = request.query_params.get("force", "").lower() in ("true", "1", "yes")
        from stockpicker.sentinel import audit_in_flight_positions, is_sentinel_enabled
        if not is_sentinel_enabled() and not force:
            return {
                "status": "disabled",
                "enabled": False,
                "message": "In-Flight Thesis Sentinel is currently switched off. Set SENTINEL_ENABLED=true or pass ?force=true to audit."
            }
        db = dashboard_data.db
        summary = audit_in_flight_positions(db, force=force)
        return {"status": "success", "sentinel_audit": summary}
    except Exception as e:
        logger.exception("Error running sentinel audit")
        raise HTTPException(status_code=500, detail=f"Failed to run sentinel audit: {e}")


@app.post("/api/stock-picks/sentinel/toggle")
async def toggle_sentinel(request: Request):
    """
    Enable or disable the In-Flight Thesis Sentinel feature at runtime.
    Body: {"enabled": true} or {"enabled": false}
    """
    session_id = request.cookies.get("dashboard_session")
    if not is_valid_session(session_id):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        data = await request.json()
        enable = bool(data.get("enabled", False))
        from stockpicker.sentinel import set_sentinel_enabled, is_sentinel_enabled
        set_sentinel_enabled(enable)
        return {
            "status": "success",
            "enabled": is_sentinel_enabled(),
            "message": f"In-Flight Thesis Sentinel has been {'enabled' if enable else 'disabled'}."
        }
    except Exception as e:
        logger.exception("Error toggling sentinel status")
        raise HTTPException(status_code=500, detail=f"Failed to toggle sentinel: {e}")


@app.get("/api/entry-decisions")
async def get_entry_decisions(limit: int = 50, symbol: Optional[str] = None):
    """
    Get latest entry decisions (ALLOWED vs BLOCKED) from Firestore entry_decisions collection.

    Query params:
        limit: Number of records to return (default: 50, max: 200)
        symbol: Optional filter by ticker symbol (e.g. "IWM")

    Returns:
        {"decisions": [...]}
    """
    try:
        db = firestore.Client()
        query = db.collection("entry_decisions")

        if symbol:
            query = query.where(filter=firestore.FieldFilter("symbol", "==", symbol.upper()))

        docs = query.limit(min(limit, 200)).stream()
        decisions = []

        for doc in docs:
            d = doc.to_dict()
            d["id"] = doc.id
            if "timestamp" in d and hasattr(d["timestamp"], "isoformat"):
                d["timestamp"] = d["timestamp"].isoformat()
            decisions.append(d)

        return {"status": "success", "count": len(decisions), "decisions": decisions}

    except Exception as e:
        logger.error(f"Error fetching entry decisions: {e}")
        return {"status": "error", "message": str(e), "decisions": []}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    host = os.environ.get("HOST", "0.0.0.0")  # nosec B104
    uvicorn.run(app, host=host, port=port)
