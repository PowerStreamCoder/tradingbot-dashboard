# Trading Bot Dashboard

A real-time web dashboard for monitoring NVDA and MSFT trading bots with comprehensive trade tracking, P&L analysis, and bot control features.

## 🎯 Quick Links

- [Getting Started](#getting-started)
- [Features](#features)
- [Deployment](#deployment)
- [Documentation](docs/)

## 🚀 Getting Started

### Prerequisites
- Python 3.11+
- Google Cloud account with Firestore enabled
- Trading bots running and writing to Firestore

### Local Development

```bash
# Install dependencies
cd dashboard
pip install -r requirements.txt

# Run locally
python main.py

# Access at http://localhost:8080
```

### Deploy to Cloud Run

```bash
cd dashboard
./scripts/deploy.sh
```

## ✨ Features

### 📊 Main Dashboard (`/`)
- **Bot Overview**: Monitor NVDA and MSFT bots with up to 10 buckets each
- **Trade History**: Detailed trade log with P&L reconciliation
- **P&L Statements**: Daily, weekly, and monthly performance metrics
- **Export**: Download trade history as Excel with custom date ranges

### 🎯 NVDA Focus Page (`/nvda`)
- Single-bot focused view for NVDA
- Real-time SMA indicators (20, 50, 200-period)
- Live price tracking with IBKR data
- Trade history and P&L specific to NVDA bot

### 📈 Key Features
- **Real-time Updates**: Live data from Firestore
- **Bot Control**: Stop/restart bots via systemctl (when available)
- **P&L Reconciliation**: Bot vs IBKR validation
- **Historical Analysis**: View any past date's performance
- **Persistent Storage**: All data stored in Firestore indefinitely

## 📁 Project Structure

```
dashboard/
├── main.py                      # FastAPI application
├── requirements.txt             # Python dependencies
├── Dockerfile                   # Container configuration
├── .gcloudignore               # Deployment exclusions
├── DEPLOYMENT_CHECKLIST.md      # Current deployment manifest
│
├── templates/                   # HTML templates
│   ├── index.html              # Main dashboard
│   ├── nvda_focus.html         # NVDA-focused view
│   └── learning_review.html    # Learning/review page
│
├── static/                      # Static assets
│   ├── css/
│   │   ├── dashboard.css       # Main styles
│   │   └── nvda_focus.css      # NVDA page styles
│   └── js/
│       ├── dashboard.js        # Main dashboard logic
│       ├── nvda_focus.js       # NVDA page logic
│       └── trade-charts.js     # Chart rendering
│
├── scripts/                     # Deployment & utility scripts
│   ├── deploy.sh               # Cloud Run deployment
│   └── check_firestore.py      # Firestore connection test
│
├── docs/                        # Documentation
│   ├── ARCHITECTURE.md         # System architecture
│   ├── DEPLOYMENT.md           # Deployment guide
│   ├── QUICKSTART.md           # Quick start guide
│   ├── CHANGELOG.md            # Version history
│   └── features/               # Feature documentation
│
└── archive/                     # Historical/obsolete files
```

## 🔧 Configuration

### Environment Variables
- `PORT`: Server port (default: 8080)
- `GOOGLE_CLOUD_PROJECT`: GCP project ID (auto-detected in Cloud Run)

### Firestore Collections Used
- `bot_data`: Bot state and bucket information
- `trades`: Trade history
- `bot_status`: Bot health and heartbeats
- `dashboard_sessions`: User authentication sessions

## 📚 Documentation

- **[Architecture](docs/ARCHITECTURE.md)**: System design and data flow
- **[Deployment Guide](docs/DEPLOYMENT.md)**: Detailed deployment instructions
- **[Features](docs/features/)**: Individual feature documentation
- **[Changelog](docs/CHANGELOG.md)**: Version history and updates

## 🔐 Authentication

Access requires daily code in `ddmmyy` format (e.g., `140726` for July 14, 2026). Sessions persist across container restarts via Firestore.

## 📊 Data Sources

- **Price Data**: IBKR (15-minute delayed) via Firestore
- **Trade Data**: Trading bots writing to Firestore
- **Bot Status**: Heartbeat system with 5-minute timeout

## 🚀 Deployment

Current deployment: **Cloud Run** (us-central1)
- Service: `trading-dashboard`
- Memory: 512Mi
- CPU: 1
- Auto-scaling: 0-10 instances

See [DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md) for latest deployment manifest.

## 📝 Version

**Current Version**: 1.7.0

See [CHANGELOG.md](docs/CHANGELOG.md) for version history.

## 🛠️ Technology Stack

- **Backend**: FastAPI + Uvicorn
- **Database**: Google Cloud Firestore
- **Frontend**: Vanilla JavaScript
- **Deployment**: Google Cloud Run
- **Container**: Docker (Python 3.11-slim)

---

**Last Updated**: 2026-07-14
