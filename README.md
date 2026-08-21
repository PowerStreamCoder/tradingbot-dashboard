# Trading Dashboard

Real-time trading dashboard for monitoring NVDA positions and multi-bot performance.

## Overview

A FastAPI-based web dashboard that provides real-time monitoring of trading bot positions, P&L tracking, SMA indicators, and bot health metrics. Data is sourced from Google Firestore (IBKR trading bot data).

## Features

### 📊 NVDA Focus View
- Real-time position tracking (quantity, avg cost, current P&L)
- Live price chart with SMA 5 and SMA 20 indicators
- Bucket distribution visualization
- Trade history with performance metrics
- Bot health monitoring (heartbeat, error tracking)

### 🤖 Multi-Bot Overview
- Aggregate view across all active bots (NVDA, MSFT, SAP, etc.)
- Combined P&L and position summary
- Bot status indicators (online/offline, last update)
- Cross-bot performance comparison

### 📈 Real-Time Updates
- Position data refreshes every 2 seconds
- Price chart updates every 60 seconds (Alpha Vantage rate limits)
- Bot logs stream every 5 seconds
- Heartbeat indicator shows connection status

## Tech Stack

**Backend:**
- FastAPI (Python web framework)
- Google Firestore (data source)
- Alpha Vantage API (price data)
- Uvicorn (ASGI server)

**Frontend:**
- Vanilla JavaScript (no frameworks)
- Chart.js (price charts)
- Dark theme UI

## Directory Structure

```
dashboard/
├── main.py                 # FastAPI application entry point
├── static/                 # Frontend assets
│   ├── nvda_focus.html    # NVDA-focused dashboard
│   ├── overview.html      # Multi-bot overview
│   ├── nvda_focus.js      # NVDA dashboard logic
│   └── styles.css         # Dark theme styles
├── scripts/                # Utility scripts (pycache only)
└── requirements.txt       # Python dependencies
```

## API Endpoints

### Position & Status
- `GET /api/bot-overview` - Position data, buckets, P&L
- `GET /api/bot-status/{symbol}` - Health metrics, SMA values
- `GET /api/sma-indicators/{symbol}` - SMA 5/20 values

### Historical Data
- `GET /api/trade-history/all` - All historical trades
- `GET /api/historical-bars/{symbol}` - Price chart data (1min bars)

### Logs
- `GET /api/logs?bot={bot}&level={level}&limit={n}` - Bot logs with filtering

## Deployment

### Local Development
```bash
cd dashboard
pip install -r requirements.txt
python main.py
# Open http://localhost:8000
```

### Production (Google Cloud Run)

**Primary Method:** GitHub Actions CI/CD (automatic on push to main)
```bash
git push origin main
# GitHub Actions automatically builds, tests, and deploys
```

**Alternative:** Use `/deploy-dashboard` skill for manual deployment

**Production URL:** https://trading-dashboard-w2n5czslna-uc.a.run.app

## Configuration

The dashboard requires:
- Google Cloud credentials (Firestore access)
- Alpha Vantage API key (for price data)
- Configured via environment variables or service account

## Development Notes

### Pre-Deployment Testing (MANDATORY)
From the project CLAUDE.md:
1. Start dev server: `python main.py`
2. Open browser: http://localhost:8000
3. Test all modified features
4. Check browser console for errors (F12)
5. **Only deploy after manual verification**

### Code Patterns
- Event listeners must be added in `attachEventListeners()` function
- Use `CONFIG.apiEndpoints` for API URLs
- Chart updates: always call `priceChart.update()` after data changes
- Debouncing: check `dataset.lastContent` caching behavior

### Common Issues
- Real-time updates stop → Check API endpoint health
- Chart not updating → Verify Alpha Vantage API key and rate limits
- Position shows "--" → Firestore data may be stale or missing
- Logs not loading → Check bot log collection in Firestore

## Related Repositories

- [tradingbot-bots](https://github.com/PowerStreamCoder/tradingbot-bots) - Main bot runtime (data source)
- [tradingbot-tools](https://github.com/PowerStreamCoder/tradingbot-tools) - Utility scripts
- [tradingbot-documentation](https://github.com/PowerStreamCoder/tradingbot-documentation) - System documentation

## License

Private repository - All rights reserved
