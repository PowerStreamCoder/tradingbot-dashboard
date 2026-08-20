# Changelog

All notable changes to the Trading Bot Dashboard project are documented here.

## [1.8.0] - 2026-06-01

### Added
- **Bot Control Buttons** - Manual bot management from dashboard
  - ⏹ **Stop Bots** - Gracefully stop both NVDA and MSFT bots via systemctl
  - 🔄 **Restart Bots** - Restart both bots to reload latest code/config
  - **Status Indicator** - Real-time bot status display (⚡ Active, ⏸ Inactive)
  - Polls status every 30 seconds automatically
  - Requires dashboard to be running on same VM as bots

- **IBKR Flex Query Reconciliation** - Match bot trades with IBKR data
  - ✓ **Reconcile Button** - On-demand reconciliation for visible trades
  - **IBKR Status Column** - Visual status indicators in trade history:
    - ✅ **Matched** - Bot P&L matches IBKR (within $0.10)
    - ⚠️ **Mismatch** - Bot P&L differs from IBKR (shows difference)
    - ❌ **Not Found** - Trade not in IBKR report
    - ⏳ **Pending** - Not yet reconciled
  - **P&L Comparison** - Displays bot gross P&L vs IBKR realized P&L
  - **Setup Guide** - Complete step-by-step guide in [FLEX_QUERY_SETUP.md](../tradingbots/docs/FLEX_QUERY_SETUP.md)
  - Configuration via [config/flex_config.json](../config/flex_config.json)
  - API endpoint: `POST /api/trade-history/reconcile`

### Changed
- **Sequence Number Fix** - Weekly sequence numbers now reset correctly
  - Fixed bug where trades on same Monday had different "week starts"
  - Now normalizes week start to Monday 00:00:00 UTC
  - Prevents duplicate sequence #1 assignments

- **Log Sync Frequency** - Increased from every 10 bars to every bar
  - Logs now sync every 2 minutes (was 20 minutes)
  - Reduces log delay from 10+ minutes to real-time

### Removed
- **Per-Trade P&L Validation** - Removed unreliable IBKR validation
  - Old validation relied on `realizedPNL` field in execution reports
  - IBKR paper trading doesn't populate this field reliably
  - Replaced with Flex Query reconciliation (on-demand, more accurate)
  - See [flex_reconciliation.py](../tradingbots/trading/flex_reconciliation.py)

### Deprecated
- **pnl_sync.py module** - Removed entirely
  - Functionality replaced by flex_reconciliation.py
  - Old per-trade validation code removed from base.py

### Fixed
- **Regime Detection Display** - Fixed adaptive config not loading
  - Python bytecode cache was preventing new config from being read
  - Added bytecode cleanup to deployment process
  - Regime info now displays correctly after 30 bars (~60 minutes)

### Documentation
- Added [FLEX_QUERY_SETUP.md](../tradingbots/docs/FLEX_QUERY_SETUP.md) - Complete Flex Query setup guide
- Updated [README.md](README.md) with v1.8.0 features
- Updated inline code comments in main.py and flex_reconciliation.py

### Code Cleanup
- Removed all Python bytecode files (`__pycache__/`, `*.pyc`)
- Removed old launcher scripts from PaperTrade root
- Removed `/tests` directory (no longer needed)
- Removed `/scripts` directory (unused)
- Removed `/.tools` directory (unused)
- Consolidated dashboard documentation (kept only README, CHANGELOG, QUICKSTART)
- Removed old release folder (v1.0.0)

---

## [1.3.0] - 2026-05-29

### Added
- **Access Code Authentication** - Daily access code required to view dashboard
  - Login screen with today's date in ddmmyy format (e.g., 290526 for May 29, 2026)
  - Session-based authentication (24-hour sessions)
  - HTTP-only secure cookies
  - Trading bots can still POST data without authentication

- **Export to Excel Feature** - Export trades with date range selector
  - Green "Export to Excel" button in Trade History header
  - Modal dialog to select From Month and To Month
  - Downloads Excel file with formatted data
  - Includes sequence numbers, color-coded P/L
  - File format: `trade_history_YYYY-MM_to_YYYY-MM.xlsx`

- **Sequence Number Column** - Weekly trade counter
  - New first column "Seq #" in Trade History table
  - Counter resets to 1 every Monday
  - Helps track weekly trading activity
  - Included in Excel exports

### Changed
- **Trade History Limit** - Changed from 100 to 25 entries
  - Table now shows last 25 trades for cleaner UI
  - Older trades accessible via Excel export
  - Reduces page load time

- **Table Structure** - Added "Seq #" as first column
  - Table now has 6 columns (was 5)
  - Sequence numbers calculated client-side

### Security
- ✅ Dashboard protected with daily access code
- ✅ Session management with secure cookies
- ✅ API endpoints require authentication
- ⚠️ Bot POST endpoints remain open (by design)

---

## [1.2.2] - 2026-05-29

### Added
- **Trade Deletion Feature** - Delete individual trades from dashboard
  - Red "Delete" button in each trade row (new "Actions" column)
  - Confirmation dialog before deletion (shows trade details)
  - API endpoint: `DELETE /api/trade-history/delete?timestamp=...`
  - Automatic refresh of Trade History and P&L after deletion
  - Permanent deletion from Firestore database
  - Success/error messages for user feedback
  - See [TRADE_DELETION.md](TRADE_DELETION.md) for full documentation

### Changed
- Trade History table now has 5 columns (added "Actions" column)
- Empty state colspan updated from 4 to 5

### Security Note
- ⚠️ Delete endpoint is currently public (no authentication)
- Recommended to add authentication for production use
- See TRADE_DELETION.md for security recommendations

---

## [1.2.1] - 2026-05-29

### Fixed
- **Safari Compatibility** - Week and month selectors not opening
  - Replaced HTML5 `<input type="week">` with dropdown selects (Year + Week)
  - Replaced HTML5 `<input type="month">` with dropdown selects (Month + Year)
  - Now works universally across Chrome, Firefox, Safari, Edge
  - See [BROWSER_COMPATIBILITY_FIX.md](BROWSER_COMPATIBILITY_FIX.md) for details

### Changed
- Week selector now shows as two dropdowns: Year | Week (e.g., "2026" | "Week 22")
- Month selector now shows as two dropdowns: Month | Year (e.g., "May" | "2026")
- Day selector unchanged (native date picker still works in Safari)

---

## [1.2.0] - 2026-05-29

### Added
- **Date Selectors for P&L Statements** - Major feature
  - Day Statement: Date picker to select any specific day
  - Week Statement: Week picker to select any week of the year
  - Month Statement: Month/year picker to select any month
  - Client-side filtering for instant P&L recalculation
  - Historical P&L analysis for any past date
  - See [DATE_SELECTORS.md](DATE_SELECTORS.md) for detailed documentation

### Changed
- P&L calculation moved from server-side to client-side
  - Frontend fetches all trades and filters based on selected dates
  - Instant updates when changing dates (no API round-trip)
  - Backend `/api/pnl-statement` endpoint kept for backward compatibility
- Enhanced JavaScript documentation with JSDoc comments
- Updated Python backend with detailed docstrings

### Fixed
- N/A (no bug fixes in this release)

---

## [1.1.0] - 2026-05-29

### Added
- **Firestore Integration** - Data persistence
  - Bot overview data stored in Firestore
  - Trade history stored indefinitely (last 100 cached in memory)
  - Automatic data loading on dashboard startup
  - See [FIRESTORE_INTEGRATION.md](FIRESTORE_INTEGRATION.md) for details
- **Bot Names Display**
  - Shows "NVDA", "MSFT", "SAP" instead of "Bot 1", "Bot 2", "Bot 3"
  - Bot name mapping in frontend (BOT_NAMES constant)
  - Backend tracks botName field in trades
- **Bucket Type Indicators**
  - Shows "Upward" (long) or "Downward" (short) for each bucket
  - Displayed in Bot/Bucket Overview headers
  - Included in Trade History entries
- **Dynamic Bucket Display**
  - Supports up to 10 buckets per bot (vs hardcoded 5)
  - Dynamically builds table columns based on active buckets
  - Filters out empty buckets (null entryPrice)
- **Trade History Enhancements**
  - Added botName field to trades
  - Added bucketType field to trades
  - Changed "Trade Number" to "Trade Info"
  - Shows bot name and bucket type per trade

### Changed
- Bot overview update strategy changed from replace to merge
  - Preserves buckets not in update payload
  - Allows bots to independently update their buckets
  - Fixes issue where only 1 bucket was visible
- Dashboard client (trading/dashboard.py) enhanced
  - Automatically includes botName in trade logs
  - Automatically includes bucketType based on position side
  - Logs more detailed trade information
- UI text improvements
  - Removed "Tile 1:", "Tile 2:", "Tile 3:" prefixes
  - Changed "Tile 3: Profit N Loss Statement" to "P&L Statements"

### Fixed
- Empty buckets showing "(Unknown)" type
  - Now filters out buckets with null entryPrice before display
- Dashboard bucket data getting replaced
  - Changed to merge strategy so multiple buckets persist
- Firestore 403 permissions error
  - Granted roles/datastore.owner to Cloud Run service account

---

## [1.0.0] - 2026-05-29

### Added
- **Initial Release** - Trading Bot Dashboard
- **3-Tile Dashboard Layout**
  - Tile 1: Bot and Bucket Overview
  - Tile 2: Trade History
  - Tile 3: P&L Statements (Day/Week/Month)
- **FastAPI Backend**
  - RESTful API endpoints for data access
  - Health check endpoint
  - Bot overview, trade history, and P&L endpoints
- **Frontend Dashboard**
  - HTML5 + CSS + Vanilla JavaScript
  - Auto-refresh every 30 seconds
  - Responsive design
  - Color-coded P/L (green=profit, red=loss)
- **Google Cloud Run Deployment**
  - Containerized with Docker
  - Auto-scaling serverless deployment
  - Public HTTPS endpoint
  - deploy.sh script for easy deployment
- **Mock Data Support**
  - Sample data for development and testing
  - Demonstrates dashboard functionality
- **API Integration Ready**
  - POST endpoints for bot updates
  - POST endpoints for trade logging
  - JSON data models defined
- **Documentation**
  - README.md - Complete documentation
  - QUICKSTART.md - 5-minute setup guide
  - SUMMARY.md - Feature overview
  - STATUS.md - Deployment status

### Technical Details
- Python 3.11
- FastAPI web framework
- Static file serving
- CORS enabled for API access
- Cloud Run optimized (PORT env variable)

---

## Release Notes

### Version Numbering
- **Major.Minor.Patch** (Semantic Versioning)
- Major: Breaking changes or major features
- Minor: New features (backward compatible)
- Patch: Bug fixes only

### Deployment History
- v1.2.0: Revision trading-dashboard-00012-lbn (May 29, 2026)
- v1.1.0: Revision trading-dashboard-00011-xxx (May 29, 2026)
- v1.0.0: Initial deployment (May 29, 2026)

### Future Roadmap
Potential features for future releases:
- Export to CSV for P&L data
- Custom date range selector (start/end dates)
- Side-by-side period comparison
- Charts and visualizations
- Keyboard navigation for date selectors
- Preset buttons ("Yesterday", "Last Week", "Last Month")
- Email/Slack notifications for trades
- User authentication and access control
- Multiple dashboard views (admin vs viewer)
- Real-time WebSocket updates (vs polling)

---

**Current Version:** 1.2.0  
**Live URL:** https://trading-dashboard-w2n5czslna-uc.a.run.app  
**Last Updated:** May 29, 2026
