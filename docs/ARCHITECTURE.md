# Trading Bot Dashboard - Architecture

**Version:** 2.0.0  
**Last Updated:** August 16, 2026  
**Status:** Production

---

## 📋 Table of Contents

1. [Overview](#overview)
2. [High-Level Architecture](#high-level-architecture)
3. [Technology Stack](#technology-stack)
4. [Application Architecture](#application-architecture)
5. [Data Flow](#data-flow)
6. [API Endpoints](#api-endpoints)
7. [Frontend Architecture](#frontend-architecture)
8. [Database Schema](#database-schema-firestore)
9. [Profile Management System](#profile-management-system)
10. [Logging Architecture](#logging-architecture)
11. [Deployment Architecture](#deployment-architecture)
12. [Security](#security)
13. [Performance Optimization](#performance-optimization)
14. [Monitoring & Observability](#monitoring--observability)
15. [Future Enhancements](#future-enhancements)
16. [Summary](#summary)

---

## Overview

The Trading Bot Dashboard is a **real-time web application** that monitors and controls multiple trading bots running on a Google Cloud VM. It provides:

- **Bot monitoring** - Real-time bot status and bucket positions
- **Trade history** - Complete trade log with P&L tracking
- **P&L statements** - Daily/weekly/monthly performance metrics
- **Bot control** - Stop/restart bots remotely
- **Profile switching** - Switch between paper (test) and live (real money) trading modes
- **IBKR reconciliation** - Validate bot P&L against broker data

### Key Characteristics

- **Serverless** - Runs on Google Cloud Run (auto-scaling)
- **Real-time** - Updates every 30 seconds
- **Persistent** - Data stored in Google Firestore
- **RESTful** - JSON API endpoints
- **Responsive** - Works on desktop and mobile
- **Safety-first** - Multiple safeguards prevent accidental live trading activation

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER BROWSER                            │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              index.html (Dashboard UI)                    │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │  │
│  │  │ Bot Overview │  │ Trade History│  │ P&L Statement│   │  │
│  │  └──────────────┘  └──────────────┘  └──────────────┘   │  │
│  │  ┌─────────────────────────────────────────────────┐     │  │
│  │  │  Mode Indicator: 📝 PAPER MODE / ⚠️ LIVE MODE   │     │  │
│  │  │  [Switch to LIVE/PAPER Button]                  │     │  │
│  │  └─────────────────────────────────────────────────┘     │  │
│  └──────────────────────────────────────────────────────────┘  │
│           │                     ↑                               │
│           │ API Requests        │ JSON Responses                │
│           ↓                     │                               │
└─────────────────────────────────────────────────────────────────┘
                                  │
                                  │ HTTPS
                                  │
┌─────────────────────────────────────────────────────────────────┐
│               GOOGLE CLOUD RUN (Dashboard Backend)              │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    main.py (FastAPI)                      │  │
│  │  ┌────────────────────────────────────────────────────┐  │  │
│  │  │  API Endpoints                                     │  │  │
│  │  │  • /api/bot-overview                               │  │  │
│  │  │  • /api/trade-history                              │  │  │
│  │  │  • /api/bot-control/stop                           │  │  │
│  │  │  • /api/bot-control/restart                        │  │  │
│  │  │  • /api/bot-control/current-profile (NEW)         │  │  │
│  │  │  • /api/bot-control/switch-profile (NEW)          │  │  │
│  │  └────────────────────────────────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                    │                          │
                    │ Read/Write               │ SSH Commands
                    ↓                          ↓
┌─────────────────────────────┐   ┌──────────────────────────────┐
│   GOOGLE FIRESTORE          │   │   GOOGLE COMPUTE ENGINE VM   │
│  ┌────────────────────────┐ │   │  ┌──────────────────────────┐│
│  │ bot_overview           │ │   │  │ Trading Bots             ││
│  │ trade_history          │ │   │  │  • nvda-bot.service      ││
│  │ logs                   │ │   │  │  • msft-bot.service      ││
│  │ heartbeats             │ │   │  │  • bot-control-api       ││
│  └────────────────────────┘ │   │  └──────────────────────────┘│
└─────────────────────────────┘   └──────────────────────────────┘
                    ↑
                    │ Write Updates
                    │
┌─────────────────────────────────────────────────────────────────┐
│                TRADING BOTS (on VM)                             │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  trading/dashboard.py (HTTP Client)                       │  │
│  │  • Sends bot status updates                               │  │
│  │  • Sends trade data                                        │  │
│  │  • Sends heartbeats                                        │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Technology Stack

### Backend
- **Framework**: FastAPI 0.115.0 (Python async web framework)
- **Server**: Uvicorn (ASGI server)
- **Database**: Google Firestore (NoSQL document database)
- **Authentication**: Firebase Admin SDK
- **Deployment**: Google Cloud Run (serverless containers)

### Frontend
- **HTML5**: Structure
- **CSS3**: Styling (custom, no frameworks)
- **JavaScript**: Vanilla JS (no frameworks)
- **Charts**: Chart.js 4.4.0 (trade visualizations)
- **Data Format**: JSON

### Infrastructure
- **Cloud Platform**: Google Cloud Platform (GCP)
- **Container**: Docker
- **Region**: us-central1
- **Auto-scaling**: 0 to N instances based on traffic

---

## Application Architecture

### Backend Structure (main.py)

```python
main.py (40,299 lines)
│
├── Imports & Configuration
│   ├── FastAPI, Uvicorn
│   ├── Firebase Admin SDK
│   └── Environment variables
│
├── Data Models (Pydantic)
│   ├── BotOverviewUpdate
│   ├── TradeHistoryAdd
│   ├── PNLStatementUpdate
│   └── Validation schemas
│
├── Firebase Initialization
│   ├── Firestore client
│   └── Collections setup
│
├── Dashboard Data Manager
│   ├── bot_data (in-memory cache)
│   ├── trade_history (in-memory cache)
│   └── Firestore sync methods
│
├── Middleware
│   └── CORS (Cross-Origin Resource Sharing)
│
├── Static Files Mount
│   └── /static -> static/ directory
│
└── API Endpoints
    ├── GET  /dashboard           (serve index.html)
    ├── GET  /api/bot-overview    (get bot status)
    ├── POST /api/bot-overview/update (update bot status)
    ├── GET  /api/trade-history   (get trades)
    ├── POST /api/trade-history/add (add trade)
    ├── POST /api/trade-history/reconcile (IBKR reconciliation)
    ├── GET  /api/logs            (get bot logs)
    ├── POST /api/logs/sync       (sync bot logs)
    ├── POST /api/bot-control/stop (stop bots)
    ├── POST /api/bot-control/restart (restart bots)
    ├── GET  /api/bot-control/status (get bot status)
    ├── GET  /api/regime-data     (get market regime)
    └── POST /api/regime-data/update (update regime)
```

### Frontend Structure

```
templates/
└── index.html (main dashboard page)
    ├── HTML Structure
    │   ├── Header (title, last update)
    │   ├── Bot Control Panel (stop/restart buttons)
    │   ├── Trade Overview (recent trades per bot)
    │   ├── Bot Overview Tile (buckets grid)
    │   ├── Trade History Tile (table with filters)
    │   └── P&L Statement Tile (day/week/month tabs)
    │
    └── Script Imports
        ├── Chart.js (CDN)
        ├── trade-charts.js
        └── dashboard.js

static/
├── css/
│   └── dashboard.css (4,471 lines)
│       ├── Global styles
│       ├── Grid layout (3 tiles)
│       ├── Bot status indicators
│       ├── Trade table styling
│       ├── P&L color coding
│       └── Responsive design
│
└── js/
    ├── dashboard.js (44,468 lines)
    │   ├── Data fetching (fetch API)
    │   ├── DOM manipulation
    │   ├── Event listeners
    │   ├── Date pickers
    │   ├── Bot control logic
    │   ├── P&L calculations
    │   └── Auto-refresh (30s interval)
    │
    └── trade-charts.js (18,703 lines)
        ├── Chart.js configuration
        ├── Trade visualization
        ├── Entry/exit markers
        └── Price trend lines
```

---

## Data Flow

### 1. Bot Status Update Flow

```
Trading Bot (VM)
    │
    │ 1. Bot executes trade
    │
    ↓
trading/dashboard.py
    │
    │ 2. POST /api/bot-overview/update
    │    {
    │      "NVDA": {
    │        "bucket1": {
    │          "bucket_type": "Upward",
    │          "entry_price": 150.50,
    │          "reference_price_before": 150.25,
    │          "reference_price_after": 151.00
    │        }
    │      }
    │    }
    │
    ↓
Dashboard Backend (main.py)
    │
    │ 3. Validate request data
    │ 4. Update Firestore bot_overview collection
    │ 5. Update in-memory cache
    │
    ↓
Firestore Database
    │
    │ 6. Data persisted
    │
User Browser
    │
    │ 7. Polls GET /api/bot-overview every 30s
    │
    ↓
Dashboard Backend
    │
    │ 8. Return cached data (fast response)
    │
    ↓
Browser JavaScript (dashboard.js)
    │
    │ 9. Update DOM elements
    │ 10. Display updated bucket status
```

### 2. Trade History Flow

```
Trading Bot (VM)
    │
    │ 1. Trade exits (position closed)
    │
    ↓
trading/dashboard.py
    │
    │ 2. Calculate P&L
    │ 3. POST /api/trade-history/add
    │    {
    │      "bot": "NVDA",
    │      "bucket_type": "Upward",
    │      "entry_price": 150.50,
    │      "exit_price": 151.50,
    │      "pnl": 100.00,
    │      "timestamp": "2026-06-02T14:30:00Z",
    │      "week_number": 23,
    │      "sequence_number": 5
    │    }
    │
    ↓
Dashboard Backend
    │
    │ 4. Validate trade data
    │ 5. Assign sequence number (if missing)
    │ 6. Store in Firestore trade_history collection
    │ 7. Update in-memory cache
    │
    ↓
Firestore Database
    │
    │ 8. Trade stored permanently
    │
User Browser
    │
    │ 9. Fetches GET /api/trade-history
    │
    ↓
dashboard.js
    │
    │ 10. Filter by date range
    │ 11. Calculate P&L totals
    │ 12. Update trade table
    │ 13. Update P&L statement (day/week/month)
```

### 3. Bot Control Flow

```
User Browser
    │
    │ 1. Clicks "Stop Bots" button
    │
    ↓
dashboard.js
    │
    │ 2. POST /api/bot-control/stop
    │
    ↓
Dashboard Backend (Cloud Run)
    │
    │ 3. SSH to VM via gcloud compute ssh
    │ 4. Execute: sudo systemctl stop nvda-bot msft-bot
    │
    ↓
VM (trading-bot-vm)
    │
    │ 5. Systemd stops bot services gracefully
    │
    ↓
Dashboard Backend
    │
    │ 6. Return success/failure response
    │
    ↓
dashboard.js
    │
    │ 7. Show notification
    │ 8. Poll status to confirm
```

---

## API Endpoints

### Bot Overview

#### GET `/api/bot-overview`
**Description**: Get current bot status and bucket positions

**Response**:
```json
{
  "NVDA": {
    "bucket1": {
      "bucket_type": "Upward",
      "entry_price": 150.50,
      "reference_price_before": 150.25,
      "reference_price_after": 151.00
    },
    "bucket2": { ... }
  },
  "MSFT": { ... }
}
```

#### POST `/api/bot-overview/update`
**Description**: Update bot status (called by trading bots)

**Request Body**:
```json
{
  "NVDA": {
    "bucket1": {
      "bucket_type": "Upward",
      "entry_price": 150.50,
      "reference_price_before": 150.25,
      "reference_price_after": 151.00
    }
  }
}
```

---

### Trade History

#### GET `/api/trade-history`
**Description**: Get all trades

**Response**:
```json
[
  {
    "bot": "NVDA",
    "bucket_type": "Upward",
    "entry_price": 150.50,
    "exit_price": 151.50,
    "pnl": 100.00,
    "timestamp": "2026-06-02T14:30:00Z",
    "week_number": 23,
    "sequence_number": 5,
    "ibkr_status": "matched"
  }
]
```

#### POST `/api/trade-history/add`
**Description**: Add a completed trade (called by bots)

**Request Body**:
```json
{
  "bot": "NVDA",
  "bucket_type": "Upward",
  "entry_price": 150.50,
  "exit_price": 151.50,
  "pnl": 100.00,
  "timestamp": "2026-06-02T14:30:00Z"
}
```

#### POST `/api/trade-history/reconcile`
**Description**: Reconcile bot trades with IBKR Flex Query data

**Request Body**:
```json
{
  "start_date": "2026-06-01",
  "end_date": "2026-06-02"
}
```

**Response**:
```json
{
  "status": "success",
  "reconciled_count": 15,
  "matched": 13,
  "mismatched": 1,
  "not_found": 1
}
```

---

### Bot Control

#### POST `/api/bot-control/stop`
**Description**: Stop all trading bots

**Response**:
```json
{
  "status": "success",
  "message": "Bots stopped successfully"
}
```

#### POST `/api/bot-control/restart`
**Description**: Restart all trading bots

**Response**:
```json
{
  "status": "success",
  "message": "Bots restarted successfully"
}
```

#### GET `/api/bot-control/status`
**Description**: Get bot service status

**Response**:
```json
{
  "nvda-bot": "active",
  "msft-bot": "active",
  "bot-control-api": "active"
}
```

#### GET `/api/bot-control/current-profile` (NEW v2.0)
**Description**: Get current trading profile (paper or live mode)

**Response**:
```json
{
  "profile": "paper"
}
```

**Notes**:
- Proxies request to VM bot control API
- Returns "paper" (test mode) or "live" (real money mode)
- Called every 10 seconds to update mode indicator badge

#### POST `/api/bot-control/switch-profile` (NEW v2.0)
**Description**: Switch trading profile between paper and live modes

**Request Body**:
```json
{
  "profile": "live"
}
```

**Response (Success)**:
```json
{
  "status": "success",
  "profile": "live",
  "message": "Profile switched to LIVE mode. Bots restarted.",
  "profile_confirmed": true
}
```

**Response (Position Blocking)**:
```json
{
  "status": "error",
  "message": "Cannot switch profile: 2 open position(s). Close all positions first. (NVDA/upward: 100 shares; MSFT/long: 50 shares)"
}
```

**Response (Bots Running)**:
```json
{
  "status": "error",
  "message": "Bots must be stopped before switching profiles. Stop bots first."
}
```

**Response (Rate Limited)**:
```json
{
  "status": "rate_limited",
  "message": "Rate limit: Wait 3.5s before switch-profile again"
}
```

**Safety Checks**:
1. Validates profile parameter ("paper" or "live" only)
2. Queries Firestore for open positions (blocks if ANY exist)
3. Forwards to VM API if safe to proceed
4. VM checks bot status (must be stopped)
5. VM updates profile file and restarts bots

**Flow**:
```
Frontend → Dashboard Backend (position check)
         → VM API (profile update)
         → Update /home/i030983/.trading_profile
         → Restart trading-bot-manager.service
         → Verify from systemd logs
         → Return success
```

---

### Logs

#### GET `/api/logs`
**Description**: Get bot logs

**Query Parameters**:
- `bot` - Bot name (NVDA, MSFT)
- `limit` - Number of log entries (default: 100)

**Response**:
```json
{
  "logs": [
    {
      "timestamp": "2026-06-02T14:30:00Z",
      "level": "INFO",
      "message": "Trade executed: Upward bucket entry at $150.50"
    }
  ]
}
```

#### POST `/api/logs/sync`
**Description**: Sync logs from bot to Firestore (called by bots)

---

### Market Regime

#### GET `/api/regime-data`
**Description**: Get current market regime/volatility data

**Response**:
```json
{
  "NVDA": {
    "regime": "trending",
    "volatility": 0.025,
    "confidence": 0.85
  }
}
```

---

## Frontend Architecture

### Component Breakdown

#### 1. Bot Control Panel
**Location**: Top of dashboard  
**Elements**:
- Stop Bots button (red)
- Restart Bots button (green)
- Status indicator (⚡ Active / ⏸ Inactive)

**JavaScript**:
```javascript
// Event listeners for bot control
document.getElementById('stopBotsBtn').addEventListener('click', stopBots);
document.getElementById('restartBotsBtn').addEventListener('click', restartBots);

// Poll status every 30 seconds
setInterval(updateBotStatus, 30000);
```

#### 2. Trade Overview (Per Bot)
**Location**: Below control panel  
**Purpose**: Show recent trades at a glance

**Data Source**: `/api/trade-history` (filtered to last 5 trades per bot)

**Display**:
- Bot name with trend indicator (📈/📉)
- Recent trade P&L (color-coded)
- Total day P&L

#### 3. Bot Overview Tile
**Location**: Tile 1 (left)  
**Purpose**: Show active bucket positions

**Data Source**: `/api/bot-overview`

**Display**:
- Grid of buckets per bot
- Bucket type (Upward ↑ / Downward ↓)
- Reference prices (before/after entry)
- Entry price

**Update Frequency**: Every 30 seconds

#### 4. Trade History Tile
**Location**: Tile 2 (center)  
**Purpose**: Detailed trade log with filtering

**Data Source**: `/api/trade-history`

**Features**:
- Date range filter
- Bot filter (NVDA/MSFT/All)
- Export to Excel
- IBKR reconciliation button
- Expandable trade details
- Sequence numbers

**Calculations** (client-side):
- P&L totals by bot
- P&L totals by date range
- Trade counts

#### 5. P&L Statement Tile
**Location**: Tile 3 (right)  
**Purpose**: Performance metrics

**Tabs**:
- Day Statement (date picker)
- Week Statement (week selector)
- Month Statement (month/year selector)

**Metrics** (per bot + total):
- Number of trades
- Profit/Loss in USD
- Color coding (green = profit, red = loss)

**Data Source**: Client-side calculation from trade history

---

## Profile Management System

### Overview

The Profile Management System allows switching trading bots between **paper trading** (test mode) and **live trading** (real money) modes through the dashboard. This is a critical feature that requires multiple safety mechanisms to prevent accidental activation of real-money trading.

### Architecture Components

```
┌─────────────────────────────────────────────────────────────────┐
│                    PROFILE SWITCH FLOW                          │
└─────────────────────────────────────────────────────────────────┘

1. USER CLICKS "Switch to LIVE" button
   ↓
2. FRONTEND: First confirmation dialog
   ↓
3. FRONTEND: Second "Real Money" warning (LIVE only)
   ↓
4. FRONTEND: POST /api/bot-control/switch-profile
   ↓
5. DASHBOARD BACKEND: Check Firestore for open positions
   │
   ├─→ Positions found? → Return HTTP 400 (blocked)
   │
   └─→ No positions → Forward to VM API
       ↓
6. VM BOT CONTROL API: Validate profile
   ↓
7. VM: Check bot status (must be stopped)
   │
   ├─→ Bots running? → Return HTTP 400 (blocked)
   │
   └─→ Bots stopped → Continue
       ↓
8. VM: Backup current profile file
   ↓
9. VM: Write new profile to /home/i030983/.trading_profile
   ↓
10. VM: Restart trading-bot-manager.service
    ↓
11. VM: Verify profile loaded from systemd logs
    ↓
12. VM: Return success to dashboard
    ↓
13. DASHBOARD: Update mode indicator badge
    ↓
14. DASHBOARD: Refresh data after 5 seconds
```

### Key Files

#### Frontend
- **`nvda_focus.html`** - Mode indicator badge (fixed position header) and switch button
- **`nvda_focus.js`** - Event handlers, confirmation dialogs, API calls
- **`nvda_focus.css`** - Badge styling (blue for paper, red pulsing for live)

#### Backend (Cloud Run)
- **`dashboard/main.py`**
  - `GET /api/bot-control/current-profile` - Query current mode
  - `POST /api/bot-control/switch-profile` - Position check + proxy to VM

#### VM Bot Control API
- **`tradingbots/trading/bot_control_api.py`**
  - `GET /current-profile` - Read from `/home/i030983/.trading_profile`
  - `POST /switch-profile` - Update file, restart service, verify

#### System Configuration
- **`/home/i030983/.trading_profile`** - Environment file containing `TRADING_PROFILE=paper` or `TRADING_PROFILE=live`
- **`tradingbots/services/trading-bot-manager.service`** - systemd service file with `EnvironmentFile=/home/i030983/.trading_profile`

### Safety Mechanisms

1. **Double Confirmation (Frontend)**
   - First dialog: "Switch to LIVE TRADING (REAL MONEY)?"
   - Second dialog: "⚠️ ARE YOU SURE? This will enable LIVE TRADING with REAL MONEY."
   - Only applies when switching TO live mode

2. **Position Blocking (Dashboard Backend)**
   - Queries Firestore `bot_overview` collection
   - Counts all buckets with `shares > 0`
   - Returns HTTP 400 if ANY positions exist
   - Error message includes position details (e.g., "Cannot switch: 2 open positions. Close all positions first. (NVDA/upward: 100 shares; MSFT/long: 50 shares)")

3. **Bot Status Check (VM API)**
   - Checks if `trading-bot-manager.service` is active
   - Returns HTTP 400 if bots are running
   - Forces user to stop bots first (prevents mid-trade profile change)

4. **Rate Limiting (VM API)**
   - 5-second minimum between operations
   - Prevents rapid switching that could crash systemd
   - Returns HTTP 429 if rate limited

5. **Visual Warning (Frontend)**
   - Paper mode: Blue static badge "📝 PAPER MODE"
   - Live mode: Red pulsing badge "⚠️ LIVE MODE"
   - Pulsing animation serves as constant reminder

6. **Audit Logging (VM API)**
   - All switch attempts logged with timestamp
   - Logs include: profile, user action, success/failure
   - Searchable via `journalctl -u bot-control-api.service`

### Profile Differences

| Parameter | Paper Mode | Live Mode |
|-----------|-----------|-----------|
| **TWS Port** | 4002 | 7496 |
| **Market Data** | Delayed (15-min) | Real-time |
| **Capital per Bucket (Long)** | $5,000 | $22,000 |
| **Capital per Bucket (Short)** | $1,000 | $2,000 |
| **Server-side Trailing Stops** | Disabled | Enabled |
| **Market Hours Check** | Skipped | Enforced |
| **Order Timeout** | 90 seconds | 30 seconds |

### Data Flow

#### Query Current Profile
```
Browser → Dashboard (/api/bot-control/current-profile)
        → VM (http://136.115.134.1:8080/current-profile)
        → Read /home/i030983/.trading_profile
        → Return {"profile": "paper" or "live"}
```

#### Switch Profile
```
Browser → Dashboard (/api/bot-control/switch-profile)
        → Check Firestore for positions
        → If positions exist: Return HTTP 400
        → If no positions: Forward to VM
        → VM validates and checks bot status
        → VM updates profile file
        → VM restarts bots
        → VM verifies from logs
        → Return success
        → Browser updates badge
```

### Error Handling

| Error | HTTP Status | Message | User Action |
|-------|-------------|---------|-------------|
| Open positions | 400 | "Cannot switch: 2 open positions. Close all positions first." | Close all positions |
| Bots running | 400 | "Bots must be stopped before switching profiles." | Click "Pause Bot" button |
| Rate limited | 429 | "Rate limit: Wait 3.5s before switch-profile again" | Wait and retry |
| Invalid profile | 400 | "Invalid profile. Must be 'paper' or 'live'" | Check request format |
| VM unreachable | 500 | "Failed to communicate with bot control API" | Check VM status |

### Periodic Updates

The dashboard polls the current profile every 10 seconds to ensure the mode indicator stays synchronized even if the profile is changed from another source (e.g., CLI, direct VM access).

```javascript
// In startPeriodicUpdates()
setInterval(updateSwitchButtonText, 10000);
```

### CLI Usage

Users can also switch profiles via command line:

```bash
# Check current profile
curl http://136.115.134.1:8080/current-profile

# Switch to live
curl -X POST http://136.115.134.1:8080/switch-profile \
     -H "Content-Type: application/json" \
     -d '{"profile": "live"}'
```

Or via Claude skill:
```bash
/switch-trading-profile
```

### Rollback Procedure

If a profile switch causes issues:

1. **Via Dashboard**: Click "Switch to PAPER" button
2. **Via CLI**: 
   ```bash
   curl -X POST http://136.115.134.1:8080/switch-profile \
        -H "Content-Type: application/json" \
        -d '{"profile": "paper"}'
   ```
3. **Manual (SSH to VM)**:
   ```bash
   # Restore from backup
   cp /home/i030983/backups/trading_profile_backup_YYYYMMDD_HHMMSS.txt \
      /home/i030983/.trading_profile
   
   # Restart bots
   sudo systemctl restart trading-bot-manager.service
   ```

---

## Database Schema (Firestore)

### Collection: `bot_overview`

**Document ID**: Bot symbol (e.g., "NVDA")

**Schema**:
```json
{
  "bucket1": {
    "bucket_type": "Upward",
    "entry_price": 150.50,
    "reference_price_before": 150.25,
    "reference_price_after": 151.00
  },
  "bucket2": { ... },
  "bucket3": { ... }
}
```

**Indexes**: None required

---

### Collection: `trade_history`

**Document ID**: Auto-generated

**Schema**:
```json
{
  "bot": "NVDA",
  "bucket_type": "Upward",
  "entry_price": 150.50,
  "exit_price": 151.50,
  "reference_price_before": 150.25,
  "reference_price_after": 151.00,
  "pnl": 100.00,
  "timestamp": "2026-06-02T14:30:00Z",
  "week_number": 23,
  "sequence_number": 5,
  "ibkr_status": "pending",
  "ibkr_pnl": null,
  "ibkr_matched_at": null
}
```

**Indexes**:
- `timestamp` (descending) - for date range queries
- `bot` + `timestamp` (composite) - for bot-specific queries

---

### Collection: `logs`

**Document ID**: Auto-generated

**Schema**:
```json
{
  "bot": "NVDA",
  "timestamp": "2026-06-02T14:30:00Z",
  "level": "INFO",
  "message": "Trade executed successfully",
  "context": { ... }
}
```

**Indexes**:
- `bot` + `timestamp` (composite)

---

### Collection: `heartbeats`

**Document ID**: Bot symbol

**Schema**:
```json
{
  "bot": "NVDA",
  "last_heartbeat": "2026-06-02T14:30:00Z",
  "status": "active",
  "version": "1.8.0"
}
```

**TTL**: 5 minutes (if no heartbeat, bot considered inactive)

---

### Collection: `regime_data`

**Document ID**: Bot symbol

**Schema**:
```json
{
  "bot": "NVDA",
  "regime": "trending",
  "volatility": 0.025,
  "confidence": 0.85,
  "updated_at": "2026-06-02T14:30:00Z"
}
```

---

## Logging Architecture

**Version:** 1.6.0 (Strategy-Aware Display)  
**Last Updated:** 2026-07-21

### Overview

The dashboard displays **strategy-aware logs** synced from trading bot log files. Logs are filtered both server-side (priority selection) and client-side (strategy filtering) to show only relevant information.

### Log Flow Architecture

```
Trading Bot                    Dashboard Backend              Browser
    ↓                               ↓                          ↓
[Log to file]              [Read log file]            [Fetch from API]
    ↓                               ↓                          ↓
base.py                     main.py: /api/logs        nvda_focus.js
    ↓                               ↓                          ↓
~/trading_logs/            [Filter important]         [Filter by strategy]
  bot1.log ────────────────> [Last 15 lines] ─────────> Display in UI
(ALL logs)                  (Priority only)          (Strategy-aware)
```

### Three-Stage Filtering

#### Stage 1: Bot Log File (Complete)
- **Location:** `~/trading_logs/curve_paper_nvda_bot{N}.log`
- **Content:** ALL logs from both strategies (no filtering)
- **Format:** `2026-07-21 14:30:45 | INFO | [TRADE] Entry signal detected`
- **Purpose:** Complete audit trail

#### Stage 2: Dashboard Backend (Priority)
- **Endpoint:** `/api/logs`
- **Process:** Reads last 100 lines, filters to 15 most important
- **Excludes:**
  - DEBUG logs
  - Routine dashboard sync confirmations
  - Heartbeat messages
  - Internal system messages
- **Prioritizes:**
  - Trade entries/exits
  - P&L results
  - Warnings and errors
  - Regime changes
- **Output:** JSON array of log strings

#### Stage 3: Dashboard Frontend (Strategy-Aware)
- **File:** `static/js/nvda_focus.js`
- **Process:** Filters logs based on active strategy
- **Logic:**
  ```javascript
  function updateLogsData(logs) {
      const activeStrategy = window.activeStrategy;  // From bot_overview
      
      logs.forEach(log => {
          const hasSMATag = log.includes('[SMA]');
          const hasMomentumTag = log.includes('[MOMENTUM]');
          
          // Hide irrelevant strategy logs
          let shouldHide = false;
          if (activeStrategy === 'momentum' && hasSMATag) {
              shouldHide = true;  // Hide SMA logs in Momentum mode
          } else if (activeStrategy === 'sma_crossover' && hasMomentumTag) {
              shouldHide = true;  // Hide Momentum logs in SMA mode
          }
          
          if (!shouldHide) {
              displayLog(log);
          }
      });
  }
  ```

### Log Categories Displayed

| Category | Description | Always Shown? |
|----------|-------------|---------------|
| `[TRADE]` | Entry/exit signals, order execution | ✅ Yes |
| `[SYSTEM]` | Connection, heartbeat, commands | ✅ Yes |
| `[OPTION]` | Covered call management | ✅ Yes |
| `[PNL]` | P&L validation results | ✅ Yes |
| `[REGIME]` | Market regime changes | ✅ Yes |
| `[CONFIG]` | Startup configuration | ✅ Yes |
| `[BUCKET]` | Position bucket updates | ✅ Yes |
| `[SMA]` | SMA crossover signals | ✅ Only in SMA mode |
| `[MOMENTUM]` | Momentum/MACD signals | ✅ Only in Momentum mode |

### Strategy Detection

The dashboard automatically detects active strategy from Firestore:

```javascript
// In updateBotOverview()
if (botData.strategy) {
    const strategyDisplay = botData.strategy === 'sma_crossover'
        ? 'SMA 5/20 Crossover'
        : 'Momentum';
    updateElement('botStrategy', strategyDisplay);
    window.activeStrategy = botData.strategy;  // Used for log filtering
}
```

The trading bot writes strategy to Firestore on startup:

```python
# In base.py __init__()
self.dashboard.update_strategy_name(config.strategy_name)

# In dashboard.py
def update_strategy_name(self, strategy_name: str) -> bool:
    """Update active strategy name in Firestore."""
    data = {
        f"bot{self.bot_id}": {
            "strategy": strategy_name  # 'momentum' or 'sma_crossover'
        }
    }
    return self._update_firestore_bot_data(data)
```

### API Endpoint: `/api/logs`

**Method:** GET  
**Parameters:**
- `bot_id` (optional): Filter logs by bot ID (1=NVDA, 2=MSFT, etc.)
- `level` (optional): Filter by log level (INFO, WARNING, ERROR)

**Response:**
```json
{
  "logs": [
    "2026-07-21 14:30:45 | INFO | [TRADE] Entry signal detected at $145.50",
    "2026-07-21 14:31:02 | INFO | [SYSTEM] Order placed: BUY 10 NVDA @ $145.50",
    "2026-07-21 14:31:15 | INFO | [TRADE] Entry filled: 10 shares @ $145.48",
    "2026-07-21 14:32:30 | WARNING | [PNL] P&L sync pending (IBKR delay)"
  ],
  "bot_id": 1,
  "timestamp": "2026-07-21T14:32:45Z"
}
```

**Implementation:**
```python
@app.get("/api/logs")
async def get_logs(bot_id: int = 1, level: str = "INFO"):
    """
    Get recent log lines from trading bot log file.
    Filters for important logs only (no DEBUG, no routine sync messages).
    """
    log_file = f"~/trading_logs/curve_paper_nvda_bot{bot_id}.log"
    
    # Read last 100 lines
    with open(log_file, 'r') as f:
        lines = f.readlines()
        last_lines = lines[-100:]
    
    # Filter for important logs
    filtered_logs = []
    exclude_patterns = ["DEBUG |", "Dashboard bucket", "Heartbeat sent"]
    include_patterns = ["Entry", "Exit", "Order", "P&L", "ERROR", "WARNING"]
    
    for line in last_lines:
        if not any(p in line for p in exclude_patterns):
            if any(p in line for p in include_patterns):
                filtered_logs.append(line.strip())
    
    return {
        "logs": filtered_logs[-15:],  # Last 15 important logs
        "bot_id": bot_id,
        "timestamp": datetime.now().isoformat()
    }
```

### Dashboard Display Features

#### Real-Time Updates
- **Polling interval:** 5 seconds
- **Auto-scroll:** Scrolls to bottom on new log
- **Color coding:**
  - 🔴 Red: ERROR logs
  - 🟡 Yellow: WARNING logs
  - ⚪ White: INFO logs

#### Log Filtering Controls (Future Enhancement)
- Toggle strategy-specific logs on/off
- Filter by log category ([TRADE], [SYSTEM], etc.)
- Search/filter by keyword
- Export logs to CSV/JSON

### Storage in Firestore

Logs are NOT stored in Firestore for long-term retention. The dashboard reads logs on-demand from bot log files.

**Why not store in Firestore?**
- Log files are the source of truth (90-day retention)
- Firestore is for real-time state, not historical logs
- Cheaper to read from files than store millions of log entries
- Firestore documents have 1 MB size limit

**Exception:** Recent logs (last 15) are synced to `bot_overview` collection for quick dashboard loading:

```json
{
  "bot1": {
    "strategy": "momentum",
    "logs": [
      "2026-07-21 14:30:45 | INFO | [TRADE] Entry signal detected",
      "2026-07-21 14:31:02 | INFO | [SYSTEM] Order placed",
      ...
    ],
    "logs_updated_at": "2026-07-21T14:32:45Z"
  }
}
```

### Benefits

#### 1. Strategy-Aware Display
- Users only see logs relevant to active strategy
- No confusion from irrelevant strategy messages
- Cleaner, more focused dashboard

#### 2. Priority Filtering
- Most important logs appear first
- Routine confirmations hidden
- Errors and warnings highlighted

#### 3. Real-Time Monitoring
- 5-second update interval
- Auto-scroll to latest log
- Color-coded severity levels

#### 4. Historical Access
- Full log history available in log files (90 days)
- Dashboard shows recent activity (last 15 logs)
- Can fetch more logs via API if needed

### Related Documentation
- **[STRATEGY_LOG_FILTERING.md](../../STRATEGY_LOG_FILTERING.md)** - Complete logging architecture
- **[LOG_CATEGORIES.md](../../tradingbots/docs/LOG_CATEGORIES.md)** - Log category reference
- **Trading Bot ARCHITECTURE.md** - Bot-side logging implementation

---

## Deployment Architecture

### Google Cloud Run Configuration

**Service Name**: `trading-dashboard`  
**Region**: `us-central1`  
**Container**: Docker (built from Dockerfile)  
**Port**: 8080  
**Auto-scaling**: 0 to 100 instances  
**Min Instances**: 0 (scale to zero when idle)  
**Max Instances**: 100  
**CPU**: 1 vCPU  
**Memory**: 512 MB  
**Timeout**: 300 seconds  

### Environment Variables

```bash
GOOGLE_CLOUD_PROJECT=project-dfe26779-9849-4c97-9b6
PORT=8080
FIRESTORE_DATABASE=(default)
VM_NAME=trading-bot-vm
VM_ZONE=us-central1-a
```

### Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
```

### Deployment Command

```bash
gcloud run deploy trading-dashboard \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --project project-dfe26779-9849-4c97-9b6
```

---

## Security

### Authentication
- **Currently**: No authentication (allow-unauthenticated)
- **Future**: Firebase Authentication
  - Email/password login
  - Session tokens
  - Role-based access

### CORS Configuration
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### Data Protection
- **Firestore**: IAM-based access control
- **Cloud Run**: Private service account
- **VM Access**: IAP tunnel only (no public SSH)

### Best Practices
- ✅ HTTPS only (enforced by Cloud Run)
- ✅ Environment variables for secrets
- ✅ No hardcoded credentials
- ❌ TODO: Add authentication
- ❌ TODO: Rate limiting
- ❌ TODO: Input sanitization

---

## Performance Optimization

### Caching Strategy
- **In-memory cache**: bot_data, trade_history
- **Firestore queries**: Minimized by caching
- **Frontend**: Client-side P&L calculations

### Response Times
- `/api/bot-overview`: ~50ms (cached)
- `/api/trade-history`: ~100ms (cached)
- `/dashboard`: ~20ms (static file)

### Auto-scaling
- Scales from 0 to N instances based on traffic
- Cold start: ~2 seconds
- Warm instance: ~50ms response time

---

## Monitoring & Observability

### Cloud Run Metrics
- Request count
- Request latency (p50, p95, p99)
- Error rate
- Instance count
- CPU/memory utilization

### Firestore Metrics
- Read/write operations
- Storage usage
- Index usage

### Logs
- Cloud Run logs (stdout/stderr)
- Firestore audit logs
- Bot heartbeat logs

---

## Future Enhancements

### Planned Features
1. **Authentication** - Firebase Auth with email/password
2. **Alerts** - Email/SMS notifications for trades
3. **Advanced Charts** - More visualization options
4. **Mobile App** - Native iOS/Android app
5. **Historical Analysis** - Backtest performance
6. **Multiple Strategies** - Support different trading strategies
7. **Risk Management** - Portfolio risk metrics
8. **Trade Replay** - Step through historical trades

### Technical Debt
- Add unit tests (pytest)
- Add integration tests
- Improve error handling
- Add request validation
- Implement rate limiting
- Add API documentation (OpenAPI/Swagger)

---

## Summary

The Trading Bot Dashboard is a **production-ready**, **real-time web application** built on Google Cloud Platform with:

- ✅ **Serverless architecture** (Cloud Run)
- ✅ **Persistent storage** (Firestore)
- ✅ **Real-time updates** (30-second polling)
- ✅ **Bot control** (stop/restart via SSH)
- ✅ **IBKR reconciliation** (P&L validation)
- ✅ **Responsive design** (desktop/mobile)
- ✅ **Auto-scaling** (0 to N instances)

**Performance**: Fast (~50ms API responses)  
**Cost**: ~$1-5/month (free tier eligible)  
**Uptime**: 99.9%+ (Cloud Run SLA)

For detailed API reference, see [README.md](README.md).  
For deployment instructions, see [QUICKSTART.md](QUICKSTART.md).  
For version history, see [CHANGELOG.md](CHANGELOG.md).
