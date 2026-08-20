# Trading Bot Dashboard - Documentation Index

**Version:** 1.7.0  
**Last Updated:** July 14, 2026

---

## 📚 Quick Navigation

| Document | Purpose | Audience |
|----------|---------|----------|
| [QUICKSTART.md](QUICKSTART.md) | Get running in 5 minutes | New users, DevOps |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design & data flow | Developers, Architects |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Cloud Run deployment guide | DevOps, Deployment |
| [CHANGELOG.md](CHANGELOG.md) | Version history | Everyone |
| [Features](features/) | Feature documentation | Product, Developers |

---

## 🚀 Getting Started

### For New Users
1. **[QUICKSTART.md](QUICKSTART.md)** - Run locally in 2 minutes
2. **[Main README](README.md)** - Understand features
3. **[NVDA Focus](features/NVDA_FOCUS.md)** - Explore single-bot view

### For Developers
1. **[ARCHITECTURE.md](ARCHITECTURE.md)** - Understand system design
2. **[Main README](README.md)** - Project structure
3. **[Features](features/)** - Individual feature docs

### For DevOps/Deployment
1. **[DEPLOYMENT.md](DEPLOYMENT.md)** - Comprehensive deploy guide
2. **[Deployment Checklist](DEPLOYMENT_CHECKLIST.md)** - Current manifest
3. **[Deploy Script](../scripts/deploy.sh)** - Automated deployment

---

## 📖 Core Documentation

### [QUICKSTART.md](QUICKSTART.md)
**Quick reference guide for getting started**

Topics:
- Local development (2 minutes)
- Cloud deployment (5 minutes)
- API testing
- Troubleshooting

### [ARCHITECTURE.md](ARCHITECTURE.md)
**Deep technical architecture documentation**

Topics:
- System architecture diagrams
- Technology stack (FastAPI, Firestore, Cloud Run)
- Data flow
- API endpoints
- Database schema
- Security considerations

### [DEPLOYMENT.md](DEPLOYMENT.md)
**Comprehensive deployment guide**

Topics:
- Local development setup
- Google Cloud Run deployment
- Environment variables
- Monitoring and troubleshooting
- Rollback procedures
- Cost estimation

### [CHANGELOG.md](CHANGELOG.md)
**Version history and release notes**

Recent versions:
- v1.7.0 (2026-07-14) - NVDA focus page enhancements
- v1.6.0 (2026-07-13) - NVDA focus page with real data
- v1.5.0 (2026-07-13) - SMA indicators integration

---

## ✨ Features Documentation

### [NVDA Focus Dashboard](features/NVDA_FOCUS.md)
Single-bot focused view for NVDA trading bot
- Real-time SMA indicators (20, 50, 200)
- Live price tracking from IBKR
- Trade history and P&L
- Performance metrics

### [Close Position Feature](features/CLOSE_POSITION.md)
Manual position closing capability
- Close position button
- Confirmation flow
- API integration

---

## 🗂️ Project Structure

```
dashboard/
├── main.py                      # FastAPI application (57KB)
├── requirements.txt             # Python dependencies
├── Dockerfile                   # Container configuration
├── .gcloudignore               # Deployment exclusions
│
├── templates/                   # HTML templates (3 files)
│   ├── index.html              # Main dashboard
│   ├── nvda_focus.html         # NVDA-focused view
│   └── learning_review.html    # Learning page
│
├── static/                      # Static assets
│   ├── css/                    # Stylesheets (2 files)
│   │   ├── dashboard.css
│   │   └── nvda_focus.css
│   └── js/                     # JavaScript (3 files)
│       ├── dashboard.js
│       ├── nvda_focus.js
│       └── trade-charts.js
│
├── scripts/                     # Deployment & utilities
│   ├── deploy.sh               # Cloud Run deployment
│   └── check_firestore.py      # Firestore test
│
├── docs/                        # Documentation (you are here)
│   ├── README.md               # Main project overview
│   ├── DEPLOYMENT_CHECKLIST.md # Current deployment manifest
│   ├── ORGANIZATION_SUMMARY.md # Reorganization summary
│   ├── INDEX.md                # This file (documentation hub)
│   ├── ARCHITECTURE.md         # System architecture
│   ├── DEPLOYMENT.md           # Deploy guide
│   ├── QUICKSTART.md           # Quick start
│   ├── CHANGELOG.md            # Version history
│   └── features/               # Feature docs
│       ├── NVDA_FOCUS.md
│       └── CLOSE_POSITION.md
│
└── archive/                     # Historical/obsolete files
```

---

## 🎯 Common Tasks

### Deploy the Dashboard
```bash
cd dashboard
./scripts/deploy.sh
```

### Run Locally
```bash
cd dashboard
pip install -r requirements.txt
python main.py
# Visit http://localhost:8080
```

### Check Firestore Connection
```bash
cd dashboard
python scripts/check_firestore.py
```

---

## 🔗 Key Resources

### Production URLs
- **Dashboard**: `https://trading-dashboard-879908494858.us-central1.run.app`
- **NVDA Focus**: `https://trading-dashboard-879908494858.us-central1.run.app/nvda`
- **API Health**: `https://trading-dashboard-879908494858.us-central1.run.app/api/health`

### Google Cloud
- [Cloud Run Console](https://console.cloud.google.com/run)
- [Firestore Console](https://console.cloud.google.com/firestore)
- [Cloud Build History](https://console.cloud.google.com/cloud-build)

### External Docs
- [FastAPI Documentation](https://fastapi.tiangolo.com)
- [Firestore Documentation](https://cloud.google.com/firestore/docs)
- [Cloud Run Documentation](https://cloud.google.com/run/docs)

---

## 📝 Documentation Standards

All documentation follows these conventions:
- **Markdown format** (GitHub-flavored)
- **Clear sections** with descriptive headers
- **Code blocks** with syntax highlighting
- **Last updated dates** at the top
- **Related links** at the bottom
- **Emojis** for visual scanning

---

## 🤝 Updating Documentation

When making changes:

1. **Code changes** → Update [DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md)
2. **New features** → Add to [features/](features/) + update [CHANGELOG.md](CHANGELOG.md)
3. **Architecture changes** → Update [ARCHITECTURE.md](ARCHITECTURE.md)
4. **Deployment changes** → Update [DEPLOYMENT.md](DEPLOYMENT.md)
5. **Breaking changes** → Highlight in [CHANGELOG.md](CHANGELOG.md)

---

## 📞 Need Help?

**Finding information:**
- Check this INDEX for document summaries
- Use GitHub/editor search for keywords
- Review [Main README](README.md) for overview

**Common questions:**
- **How to deploy?** → [DEPLOYMENT.md](DEPLOYMENT.md)
- **How it works?** → [ARCHITECTURE.md](ARCHITECTURE.md)
- **What changed?** → [CHANGELOG.md](CHANGELOG.md)
- **Quick start?** → [QUICKSTART.md](QUICKSTART.md)

---

**Last Updated**: July 14, 2026  
**Dashboard Version**: 1.7.0  
**Status**: Production Ready ✅
