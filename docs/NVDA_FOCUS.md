# NVDA Focus Dashboard

**Feature Status**: ✅ Active  
**Current Version**: 1.1.0  
**Last Updated**: July 14, 2026

---

## Overview

A dedicated single-bot dashboard for monitoring the NVDA SMA Crossover trading bot with real-time market data, indicators, and trade tracking.

**URL**: `/nvda`

---

## Features

### 1. Real-Time Market Data
- **Live Price**: NVDA current price from IBKR (15-min delayed)
- **SMA Indicators**: 20, 50, and 200-period moving averages
- **Signal Display**: LONG/SHORT based on SMA crossover
- **Spread Calculation**: SMA spread percentage

### 2. Market Session Clock
- Real-time ET clock display
- Heartbeat indicator (connection status)
- Session status (Pre-market, Regular, After-hours, Closed)

### 3. Trade History
- NVDA-specific trade log
- Entry/exit prices
- P&L tracking per trade
- Trade timestamps
- Expandable details view

### 4. Performance Metrics
- Daily P&L
- Weekly P&L
- Monthly P&L
- Win rate
- Trade count

### 5. Visual Design
- Single-bot focused layout
- Clean, professional interface
- Color-coded signals (green/red)
- Responsive design

---

## Technical Implementation

### Frontend
- **HTML**: `templates/nvda_focus.html`
- **CSS**: `static/css/nvda_focus.css`
- **JavaScript**: `static/js/nvda_focus.js`

### Backend Endpoints
- `GET /nvda` - Serve NVDA focus page
- `GET /api/bot-data/{symbol}` - Bot state and bucket data
- `GET /api/trades/{symbol}` - Trade history for NVDA
- `GET /api/bot-status/{symbol}` - Bot health status

### Data Sources
- **Price Data**: Firestore (`bot_status.nvda.recent_bars`)
- **SMA Indicators**: Firestore (`bot_status.nvda.sma_*`)
- **Trade Data**: Firestore (`trades` collection)
- **Bot State**: Firestore (`bot_data.nvda`)

---

## Data Flow

1. **Bot writes data** to Firestore:
   - `bot_status/nvda` - Recent bars, SMAs, heartbeat
   - `bot_data/nvda` - Bucket states, positions
   - `trades` - Trade history

2. **Dashboard fetches** from Firestore:
   - Real-time queries every 5 seconds
   - No external API dependencies
   - 15-minute delayed IBKR data

3. **Frontend updates**:
   - Live price display
   - SMA indicator updates
   - Trade log refreshes
   - P&L recalculation

---

## Implementation History

### Version 1.0.0 (July 13, 2026)
- Initial NVDA Focus page
- Basic layout and navigation
- Placeholder data and charts
- Static SMA values

### Version 1.1.0 (July 13, 2026)
- Real SMA indicators from Firestore
- Live IBKR price data integration
- Actual trade history display
- Dynamic P&L calculations
- Removed Alpha Vantage dependency

### Version 1.1.1 (July 14, 2026)
- Enhanced focus tracking
- Improved data refresh logic
- Bug fixes and optimizations

---

## Future Enhancements

### Potential Improvements
- [ ] Real-time WebSocket updates (eliminate polling)
- [ ] Advanced charting with TradingView
- [ ] Multi-timeframe SMA analysis
- [ ] Alert notifications
- [ ] Trade execution interface
- [ ] Position sizing calculator

---

## Related Documentation

- [Main Dashboard](../../README.md)
- [Architecture](../ARCHITECTURE.md)
- [Close Position Feature](CLOSE_POSITION_FEATURE.md)
- [Deployment Guide](../DEPLOYMENT.md)

---

## Notes

- Data is 15-minute delayed per IBKR regulations
- SMA calculations match bot's trading logic
- All timestamps in Eastern Time
- Authentication required (daily access code)
- Sessions persist across container restarts

---

**Maintained by**: Dashboard Development Team  
**Repository**: `/dashboard`
