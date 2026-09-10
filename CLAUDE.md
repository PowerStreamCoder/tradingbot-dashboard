<!-- Auto-generated from AGENT_CONTEXT.md — do not edit manually -->
<!-- Auto-generated from AGENT_CONTEXT.md — do not edit manually -->
# Dashboard — AI Agent Context

## Main entry points
- API routes: `main.py` (FastAPI app, ~2800 lines — all routes defined here)
- Frontend (NVDA/bot focus view): `static/js/bot_focus.js` (primary UI logic)
- Frontend (main dashboard): `static/js/dashboard.js`
- Frontend (P&L reporting): `static/js/pnl-reporting.js`
- Frontend (stock picker): `static/js/stock_picker.js`
- Frontend shared config: `static/js/config.js` (API_ENDPOINTS, UPDATE_INTERVALS)
- Frontend utilities: `static/js/utils.js`, `static/js/pnl-utils.js`
- HTML templates: `templates/` (Jinja2 — bot_focus.html, index.html, learning_review.html)
- Bot config (read at startup): `config/bots.json`
- Stock picker logic: `stockpicker/core.py`, `stockpicker/runner.py`
- Tests: `tests/` (test_api.py, test_bot_control.py, test_close_position.py, test_dashboard_api.py)

## Important commands
- Start dev server: `cd tradingbot-dashboard && python main.py` → http://localhost:8080
- Run all tests: `pytest tests/ -v`
- Run API tests only: `pytest tests/api -q`
- Deploy: `git push origin main` → GitHub Actions → Cloud Run (~3 min, automatic)
- Production URL: https://trading-dashboard-879908494858.us-central1.run.app

## Common mappings
- All API route definitions: `main.py` (search for `@app.get` / `@app.post`)
- API endpoint constants: `API_ENDPOINTS` object in `static/js/config.js`
- Polling intervals: `UPDATE_INTERVALS` in `static/js/config.js` (BOT_OVERVIEW: 15s, CHART: 60s, TABLES/LOGS: 30s)
- Event listeners (UI wiring): `attachEventListeners()` in `static/js/bot_focus.js`
- DOM updates helper: `updateElement(id, val, cls)` in `static/js/bot_focus.js`
- Chart rendering: `initializeChart()`, `updateChartData()`, `updateChart()` in `static/js/bot_focus.js`
- Bot position data: `GET /api/bot-overview` → Firestore `bot_overview` collection
- Bot health/status: `GET /api/bot-status/{symbol}` → Firestore `bot_status` collection
- Price chart data: `GET /api/historical-bars/{symbol}` → Firestore `historical_bars` collection
- Trade history: `GET /api/trade-history/all` → Firestore `trades` collection
- Bot logs: `GET /api/logs?botId=X` → Firestore `bot_logs` collection
- Stock picks: `GET /api/stock-picks`, `POST /api/stock-picks/run`
- Bot control (stop/start/reset): `POST /api/bot-control/stop|restart|reset/{bot_name}`
- Profile switching: `GET /api/bot-control/current-profile`, `POST /api/bot-control/switch-profile`
- P&L statement: `GET /api/pnl-statement`
- Authentication: `POST /authenticate` (access code = today's date in ddmmyy format)

## Debugging rules
- For API errors: inspect `main.py` route handler and Firestore collection access first
- For UI changes that seem invisible: check `dataset.lastContent` on DOM elements — it suppresses duplicate updates; clear it or set a force-update flag
- For chart rendering issues: check SMA array length — must match chart data length exactly; extend when adding padding
- For event listener issues: check `attachEventListeners()` in `bot_focus.js` — listeners added inline won't survive re-renders
- For bot control failures: the bot control API is a separate service; check `tradingbot-services/` and the GCP VM
- For auth issues: access code is `datetime.now().strftime("%d%m%y")` — changes daily
- Never read the entire repository unless explicitly required

## Architecture notes
- Firestore is the source of truth; trading bots write, dashboard only reads — never write to Firestore from dashboard JS
- The one exception: dashboard writes to `dashboard_sessions`, `profile_switch_audit`, `learning_candidates` via POST endpoints
- SMA arrays must be extended when adding chart padding or Chart.js throws index-out-of-bounds errors
- Alpha Vantage rate limits chart refresh to 60s minimum interval
- `bot_focus.js` / `bot_focus.html` is the NVDA-focused view (primary); `dashboard.js` / `index.html` is the overview view
- Access code rotates daily — hardcoding it will break auth

## Firestore collections
| Collection | Written by | Read by | Purpose |
|---|---|---|---|
| `bot_overview` | trading bots | dashboard | Position data, SMA values, bucket |
| `bot_status` | trading bots | dashboard | Health metrics, strategy state |
| `trades` | trading bots | dashboard | Trade history |
| `bot_logs` | trading bots | dashboard | Bot log messages |
| `historical_bars` | trading bots / Alpha Vantage | dashboard | OHLCV price data |
| `option_events` | trading bots | dashboard | Options chain events |
| `stock_picks` | stock picker | dashboard | Stock screener results |
| `bots` | dashboard | dashboard | Per-bot state (bot_{client_id}) |
| `dashboard_sessions` | dashboard | dashboard | Auth sessions |
| `profile_switch_audit` | dashboard | dashboard | Profile change log |
| `learning_candidates` | dashboard | dashboard | ML learning review queue |

### bot_overview document shape
`{ symbol, position_size, avg_cost, current_price, unrealized_pnl, sma5, sma20, bucket, last_updated }`

### bot_status document shape
`{ symbol, health, last_trade_time, error_count, strategy_state }`

## Task recipes

### Add a new GET API endpoint
1. Add route in `main.py`: `@app.get("/api/your-endpoint")`
2. Add constant to `API_ENDPOINTS` in `static/js/config.js`
3. Add fetch call in the appropriate polling function in `bot_focus.js`
4. Wire any UI trigger in `attachEventListeners()` in `bot_focus.js`

### Add a new POST API endpoint (bot control action)
1. Add route in `main.py`: `@app.post("/api/your-action")`
2. Add to `API_ENDPOINTS.BOT_CONTROL` in `static/js/config.js`
3. Add handler function in `bot_focus.js`
4. Register button click in `attachEventListeners()`

### Add a new chart feature
1. Update `updateChartData()` or `initializeChart()` in `bot_focus.js`
2. If adding a new data series, extend its array to match chart data length
3. Always call `priceChart.update()` after data changes
4. Test auto-scale enabled and disabled

### Deploy dashboard
`git push origin main` → GitHub Actions auto-builds and deploys to Cloud Run

## Documentation
- System overview: `../tradingbot-documentation/guides/SYSTEM_OVERVIEW.md`
- Architecture overview: `../tradingbot-documentation/architecture/ARCHITECTURE.md`
- Dashboard architecture: `../tradingbot-documentation/architecture/DASHBOARD_ARCHITECTURE.md`
- Bot strategy: `../tradingbot-documentation/architecture/bot_strategy_document.md`
- StockPicker user guide: `../tradingbot-documentation/featureDocs/STOCKPICKER_GUIDE.md`
- StockPicker technical: `../tradingbot-documentation/featureDocs/STOCKPICKER_TECHNICAL.md`
- Deployment guide: `../tradingbot-documentation/deployment/README_DEPLOY.md`
- Dashboard deployment: `../tradingbot-documentation/deployment/DASHBOARD_DEPLOYMENT.md`
- Operator runbook: `../tradingbot-documentation/guides/OPERATOR_RUNBOOK.md`
- Changelog: `../tradingbot-documentation/CHANGELOG.md`

## Code Knowledge Graph
_Auto-updated 2026-09-10. Full graph: `tradingbot-tools/knowledge-graph/output/`_

### Key Classes
| Class | File | Role |
|---|---|---|
| `AsyncFillMonitor` | `execution_manager.py` | Tracks orders that timed out but may fill asynchronously. |
| `BasePaperTrader` | `base.py` | Base class for IBKR paper trading bots. |
| `BotConfig` | `services/bot_manager.py` | Configuration for a single bot. |
| `BotData` | `main.py` |  |
| `BotManager` | `services/bot_manager.py` | Manages multiple trading bot processes. |
| `BotProcess` | `services/bot_manager.py` | Running bot process information. |
| `Bucket` | `base.py` | A position slot with allocated capital. |
| `BucketData` | `main.py` | Bucket status data model for active trading positions. |
| `CircuitBreakerState` | `exit_handler.py` | Circuit breaker state for retry loop rate limiting. |
| `CoveredCallManager` | `options_manager.py` | Manages covered call strategy decisions. |
| `DashboardClient` | `dashboard.py` | Client for communicating with the trading dashboard API. |
| `DashboardConfig` | `dashboard.py` | Configuration for dashboard integration. |
| `DashboardData` | `main.py` |  |
| `EntryFilterChain` | `entry_filters.py` | Orchestrates multiple entry filters in sequence. |
| `EntryHandlerConfig` | `entry_handler.py` | Configuration for UnifiedEntryHandler behaviour. |

### Trade Entry Lifecycle
SMACrossoverTrader detects SMA5/SMA20 crossover on 1-hour bar → EntryFilterChain validates: RegimeFilter, MultiTimeframeFilter, volatility guards → UnifiedEntryHandler.execute_entry() → ExecutionManager.execute() → BasePaperTrader.on_entry_fill()

### Trade Exit Lifecycle
UnifiedExitHandler monitors per price tick: ATR profit target (2.0x ATR), trailing stop (0.5x ATR from peak), hard stop (-5%) → Exit condition met → ExecutionManager.execute() → BasePaperTrader.on_exit_fill() → DashboardClient.log_trade()

### Covered Call Lifecycle
SMACrossoverTrader checks covered call eligibility on each bar when in long position → CoveredCallManager.check_and_sell_call() → ExecutionManager.execute() → on_call_fill() → DashboardClient.log_option_event(type='sold')

### SMA Signal Pipeline
BasePaperTrader.reqHistoricalData() → SMACrossoverTrader._compute_sma() → SMACrossoverTrader detects crossover signal (LONG_ENTRY | LONG_EXIT | NEUTRAL) → BasePaperTrader._store_historical_bars_to_firestore() → SMACrossoverTrader._send_heartbeat()

### Bot Heartbeat
BasePaperTrader._heartbeat_loop() → Writes last_heartbeat timestamp, connection status, and SMA values to Firestore bot_status/{bot_id}

### For deeper questions
Read `tradingbot-tools/knowledge-graph/output/bots_graph.md` for code structure
or `output/flows.md` for detailed data flow narratives.

<!-- END CODE KNOWLEDGE GRAPH -->
