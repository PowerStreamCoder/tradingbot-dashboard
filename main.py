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
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
import httpx
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Literal  # v5.0.0: Added Literal for trading_mode validation
from pydantic import BaseModel
from google.cloud import firestore
import os
import json
from io import BytesIO
import xlsxwriter
import secrets
from threading import Lock
import logging

# Rate limiting
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

# Session storage for authenticated users (Firestore-backed for persistence)
# This ensures sessions survive Cloud Run container restarts
authenticated_sessions = {}  # Local cache
sessions_ref = None  # Firestore collection reference (initialized after DashboardData)

# Cache for trade history to reduce Firestore reads
_trade_history_cache = {
    'data': None,
    'timestamp': None,
    'lock': Lock()
}
TRADE_CACHE_TTL_SECONDS = 30  # Cache for 30 seconds

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
_stockpicker_running = False
_stockpicker_lock = Lock()

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

    # Try multiple config paths (local first, then parent directory for development)
    config_paths = [
        os.path.join(os.path.dirname(__file__), "config", "bots.json"),  # Production: dashboard/config/bots.json
        os.path.join(os.path.dirname(__file__), "..", "tradingbots", "config", "bots.json")  # Development: ../tradingbots/config/bots.json
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

        if 1 <= bot_id <= len(bots):
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

def get_valid_access_code():
    """Get today's access code in ddmmyy format"""
    return datetime.now().strftime("%d%m%y")

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
        <p class="subtitle">Enter access code to continue</p>
        <form id="loginForm">
            <input type="text" id="accessCode" placeholder="Enter 6-digit code" maxlength="6" pattern="[0-9]{6}" required autofocus>
            <button type="submit">Access Dashboard</button>
        </form>
        <div class="error" id="errorMsg">❌ Invalid access code. Please try again.</div>
    </div>

    <script>
        document.getElementById('loginForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const code = document.getElementById('accessCode').value;
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
        request.url.path.startswith("/api/historical-bars/") or  # Allow historical bars for charts
        request.url.path == "/api/logs" or  # Allow logs API for frontend
        request.url.path.startswith("/api/learning-candidates") or  # Allow learning endpoints
        request.url.path.startswith("/api/option-events/") or  # Allow option events API for modal
        request.url.path.startswith("/api/bot-control/") or  # Allow bot control API for profile switching
        (request.method == "POST" and request.url.path.startswith("/api/"))):
        # Allow bot POST requests, health checks, logs, learning endpoints, option events, and bot control without authentication
        return await call_next(request)

    # Check if session cookie exists
    session_id = request.cookies.get("dashboard_session")

    # If session exists, check if valid (first check local cache, then Firestore if missing)
    if session_id:
        # Check local cache first
        if session_id in authenticated_sessions:
            session_expiry = authenticated_sessions[session_id]
            if session_expiry > datetime.now():
                # Session still valid - allow access
                print(f"Valid session (cache): {session_id[:10]}... for path: {request.url.path}")
                return await call_next(request)
            else:
                # Session expired, remove it
                print(f"Session expired: {session_id[:10]}...")
                del authenticated_sessions[session_id]
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
                    session_expiry = session_data.get('expiry')
                    if session_expiry and session_expiry > datetime.now():
                        # Valid session from Firestore, add to local cache
                        authenticated_sessions[session_id] = session_expiry
                        print(f"Valid session (Firestore): {session_id[:10]}... for path: {request.url.path}")
                        return await call_next(request)
            except Exception as e:
                print(f"Error checking session in Firestore: {e}")

        print(f"No valid session for path: {request.url.path}, cookie: {session_id[:10]}...")

    # If trying to access main page or dashboard without auth, show login
    if request.url.path in ["/", "/dashboard", "/bot-focus", "/pnl-reporting", "/learning-review"]:
        return HTMLResponse(content=get_login_page(), status_code=200)

    # For API GET/DELETE endpoints without auth, return 401
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
                trade_time = datetime.fromisoformat(trade["timestamp"].replace('Z', '+00:00'))
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

# Initialize dashboard data
dashboard_data = DashboardData()

# Initialize Firestore sessions collection
sessions_ref = dashboard_data.db.collection('dashboard_sessions')

# Load existing sessions from Firestore on startup
def load_sessions_from_firestore():
    """Load valid sessions from Firestore on startup"""
    # Note: authenticated_sessions is a module-level dict, no 'global' needed for dict modification
    try:
        now = datetime.now()
        for doc in sessions_ref.stream():
            session_data = doc.to_dict()
            expiry = session_data.get('expiry')
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

        # Validate access code
        if access_code == get_valid_access_code():
            # Generate session ID
            session_id = secrets.token_urlsafe(32)
            expiry_time = datetime.now() + timedelta(hours=24)

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

@app.get("/pnl-reporting", response_class=HTMLResponse)
async def pnl_reporting():
    """Serve the P&L Reporting page"""
    pnl_path = os.path.join(TEMPLATES_DIR, "index.html")
    try:
        return FileResponse(pnl_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="P&L Reporting page not found")

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
                if datetime.fromisoformat(t["timestamp"].replace('Z', '+00:00')) >= day_ago
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
            "last_crossover": status_data.get('last_crossover')  # ISO timestamp of last crossover
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

        # Check cache (thread-safe)
        with _trade_history_cache['lock']:
            cached_data = _trade_history_cache['data']
            cached_time = _trade_history_cache['timestamp']

            if cached_data and cached_time:
                age_seconds = (now - cached_time).total_seconds()
                if age_seconds < TRADE_CACHE_TTL_SECONDS:
                    if response:
                        response.headers['X-Cache-Status'] = f'HIT (age: {int(age_seconds)}s)'
                    return cached_data

        # Cache miss - fetch from Firestore
        if response:
            response.headers['X-Cache-Status'] = 'MISS'

        # Calculate start date
        start_date = datetime.now() - timedelta(days=days)

        # Query all trades since start_date (limited to 500 to prevent unbounded growth)
        trades_query = dashboard_data.trades_ref.where(
            'timestamp', '>=', start_date.isoformat()
        ).order_by('timestamp', direction=firestore.Query.DESCENDING).limit(500)

        trades = []
        for doc in trades_query.stream():
            trade = doc.to_dict()

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

        # Update cache
        with _trade_history_cache['lock']:
            _trade_history_cache['data'] = result
            _trade_history_cache['timestamp'] = now

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
            today_pnl = sum(
                t.get('profitLoss', 0)
                for t in trades
                if datetime.fromisoformat(t['timestamp']).date() == today
            )

            # Net P&L
            net_pnl = sum(t.get('profitLoss', 0) for t in trades)

            # Win rate
            profitable = sum(1 for t in trades if t.get('profitLoss', 0) > 0)
            win_rate = (profitable / len(trades) * 100) if trades else 0

            # Sharpe ratio
            returns = [t.get('profitLoss', 0) for t in trades]
            mean_return = sum(returns) / len(returns) if returns else 0
            variance = sum((r - mean_return) ** 2 for r in returns) / len(returns) if len(returns) > 1 else 0
            std_dev = variance ** 0.5
            sharpe_ratio = (mean_return / std_dev) if std_dev > 0 else 0

            # Max drawdown
            cumulative_pnl = []
            running_total = 0
            for t in sorted(trades, key=lambda x: x['timestamp']):
                running_total += t.get('profitLoss', 0)
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

            trade_time = datetime.fromisoformat(trade['timestamp'].replace('Z', '+00:00'))
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
        sorted_trades = sorted(trades, key=lambda t: datetime.fromisoformat(t['timestamp'].replace('Z', '+00:00')))

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
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
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
async def delete_trade(timestamp: str):
    """
    Delete a trade by timestamp from Firestore and in-memory cache.
    Requires exact ISO timestamp match.
    """
    try:
        # Find and delete from Firestore
        query = dashboard_data.trades_ref.where('timestamp', '==', timestamp)
        docs = list(query.stream())

        if docs:
            deleted_count = 0
            for doc in docs:
                doc.reference.delete()
                deleted_count += 1

            # Remove from in-memory cache
            dashboard_data.trades = [t for t in dashboard_data.trades if t.get('timestamp') != timestamp]

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

    Sends HTTP request to Flask API running on VM (port 8080).
    """
    try:
        import httpx
        # Call VM API to stop bots
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post('http://136.115.134.1:8080/stop-bots')
            response.raise_for_status()
            data = response.json()
            return {"status": data.get("status"), "message": data.get("message")}
    except httpx.HTTPError as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with bot control API: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/bot-control/restart")
async def restart_bots():
    """
    Restart both trading bots via Bot Control API on VM.

    Sends HTTP request to Flask API running on VM (port 8080).
    Bots will reload latest code and configuration on restart.
    """
    try:
        import httpx
        # Call VM API to restart bots
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post('http://136.115.134.1:8080/restart-bots')
            response.raise_for_status()
            data = response.json()
            return {"status": data.get("status"), "message": data.get("message")}
    except httpx.HTTPError as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with bot control API: {str(e)}")
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
        import httpx
        # Call VM API to reset bot
        async with httpx.AsyncClient(timeout=120.0) as client:  # 2 minute timeout for reset
            response = await client.post(f'http://136.115.134.1:8080/reset-bot/{bot_name}')
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
            "bot_id": 1,
            "symbol": "NVDA",
            "bucket_group": "upward",
            "bucket_id": 0,
            "close_covered_calls": true
        }

    Returns:
        JSON with success status and message
    """
    try:
        body = await request.json()
        bot_id = body.get('bot_id')
        symbol = body.get('symbol')
        bucket_group = body.get('bucket_group')
        bucket_id = body.get('bucket_id')
        close_covered_calls = body.get('close_covered_calls', True)

        # Validate required fields
        if not all([bot_id, symbol, bucket_group is not None, bucket_id is not None]):
            raise HTTPException(status_code=400, detail="Missing required fields")

        # Map dashboard bot_id to trading bot client_id dynamically from bots.json
        # Dashboard bot_id=1 (first bot) -> client_id from config (e.g., 3)
        # The trading bot uses client_id as its Firestore document ID
        db = firestore.Client()
        client_id = get_client_id_for_bot(bot_id)
        bot_ref = db.collection('bots').document(f'bot_{client_id}')
        print(f"Using bot_{client_id} for symbol {symbol} (dashboard bot_id={bot_id})")

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
            "bot_id": 1,
            "symbol": "NVDA"
        }

    Returns:
        JSON with success status and message
    """
    try:
        body = await request.json()
        bot_id = body.get('bot_id')
        symbol = body.get('symbol')

        # Validate required fields
        if not all([bot_id, symbol]):
            raise HTTPException(status_code=400, detail="Missing required fields: bot_id and symbol")

        # Map dashboard bot_id to trading bot client_id dynamically from bots.json
        client_id = get_client_id_for_bot(bot_id)

        # Update Firestore to trigger bot to create position
        db = firestore.Client()
        bot_ref = db.collection('bots').document(f'bot_{client_id}')

        # Check if bot document exists, create if not
        bot_doc = bot_ref.get()
        if not bot_doc.exists:
            # Create initial bot document
            bot_ref.set({
                'bot_id': bot_id,
                'client_id': client_id,
                'symbol': symbol,
                'created_at': datetime.now().isoformat(),
                'pending_command': None
            })

        # Create a create_position command
        create_command = {
            'command': 'create_position',
            'timestamp': datetime.now().isoformat(),
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
            'message': f'Create position command sent for {symbol} (100 shares + covered call). Bot will process it on next update cycle.'
        }

    except HTTPException:
        raise
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
    2. Cloud Run backend proxies to VM (136.115.134.1:8080)
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
            response = await client.get('http://136.115.134.1:8080/current-profile')
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
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                current_response = await client.get('http://136.115.134.1:8080/current-profile')
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

        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                'http://136.115.134.1:8080/switch-profile',
                json={'profile': profile}
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
            "by_type": {"eliminate": 3, "amplify": 1, "new_rule": 1},
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
            "by_type": {"eliminate": 0, "amplify": 0, "new_rule": 0},
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

        # Return the requested number of most recent bars (default 50, max 100)
        requested_bars = min(bars, 100)
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
    if not session_id or session_id not in authenticated_sessions:
        raise HTTPException(status_code=401, detail="Unauthorized")

    now = datetime.now()

    # Check cache first (thread-safe)
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
    if not session_id or session_id not in authenticated_sessions:
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
        global _stockpicker_running
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
    from concurrent.futures import ThreadPoolExecutor

    # Thread pool executor for running blocking code
    executor = ThreadPoolExecutor(max_workers=1)
    start_time = time.time()

    try:
        logger.info(f"Manual StockPicker run triggered by session: {session_id[:10]}...")

        # Import here to avoid circular dependencies
        from stockpicker.runner import run_stockpicker

        # Run in thread pool with 120-second timeout
        loop = asyncio.get_event_loop()
        picks = await asyncio.wait_for(
            loop.run_in_executor(executor, run_stockpicker),
            timeout=120.0
        )

        duration = round(time.time() - start_time, 2)

        # Invalidate cache so next GET request fetches fresh data
        with _stock_picks_cache['lock']:
            _stock_picks_cache['data'] = None
            _stock_picks_cache['timestamp'] = None

        # Build response
        return {
            'status': 'success',
            'picks': picks if picks else [],
            'pick_count': len(picks) if picks else 0,
            'message': (
                f'Generated {len(picks)} picks successfully'
                if picks else
                'No picks generated (no explosive news found with threshold ≥7.5)'
            ),
            'duration_seconds': duration
        }

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

    finally:
        # Always release the lock (even on timeout/error)
        with _stockpicker_lock:
            _stockpicker_running = False


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
