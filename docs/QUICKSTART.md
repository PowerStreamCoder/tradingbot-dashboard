# Quick Start Guide - Trading Bot Dashboard

## 🎯 Overview
This guide will help you get your trading bot dashboard up and running in 5 minutes.

---

## Option 1: Test Locally (2 minutes)

### Step 1: Install Dependencies
```bash
cd /Users/i030983/Library/CloudStorage/OneDrive-Personal/IBKR/dashboard
pip install -r requirements.txt
```

### Step 2: Run the Server
```bash
python main.py
```

You should see:
```
INFO:     Started server process
INFO:     Uvicorn running on http://0.0.0.0:8080
```

### Step 3: Open Dashboard
Open your browser and go to: **http://localhost:8080**

✅ You should see your dashboard with mock data!

---

## Option 2: Deploy to Google Cloud (5 minutes)

### Prerequisites
- Google Cloud account
- gcloud CLI installed
- Project: `project-dfe26779-9849-4c97-9b6`

### Step 1: Authenticate
```bash
gcloud auth login
gcloud config set project project-dfe26779-9849-4c97-9b6
```

### Step 2: Deploy
```bash
cd /Users/i030983/Library/CloudStorage/OneDrive-Personal/IBKR/dashboard
./deploy.sh
```

The script will:
1. ✅ Enable required Google Cloud APIs
2. ✅ Build the container image
3. ✅ Deploy to Cloud Run
4. ✅ Output your dashboard URL

### Step 3: Access Your Dashboard
The deployment script will output a URL like:
```
https://trading-dashboard-xxxxx-uc.a.run.app
```

Open this URL in your browser!

---

## 🎨 What You'll See

### Tile 1: Bot and Bucket Overview
Shows current state of Bot 1 and Bot 2 with 5 buckets each:
- Reference price before entry
- Entry price  
- Reference price after entry

### Tile 2: Trade History
Lists recent trades with:
- Trade details (entry/exit prices)
- Timestamps
- Profit/Loss per trade

### Tile 3: Profit N Loss Statement
Three time periods (Day, Week, Month) showing:
- Number of trades per bot
- P&L per bot
- Totals

---

## 🔄 Using Mock Data vs Real Data

### Currently (Mock Data)
The dashboard displays randomly generated mock data that updates automatically.

### Connecting Real Trading Bots

To integrate with your actual trading bots, send HTTP requests to the API:

#### Update Bot Overview
```python
import requests

url = "http://localhost:8080/api/bot-overview/update"
# OR your Cloud Run URL: https://trading-dashboard-xxxxx-uc.a.run.app/api/bot-overview/update

data = {
    "bot1": {
        "bucket1": {
            "referencePriceBefore": 150.25,
            "entryPrice": 150.50,
            "referencePriceAfter": 151.00
        },
        "bucket2": {
            "referencePriceBefore": 151.00,
            "entryPrice": 151.25,
            "referencePriceAfter": 151.75
        },
        # ... buckets 3-5
    },
    "bot2": {
        # ... bot2 data
    }
}

response = requests.post(url, json=data)
print(response.json())
```

#### Add a Trade
```python
from datetime import datetime
import requests

url = "http://localhost:8080/api/trade-history/add"

trade = {
    "referencePriceBefore": 150.25,
    "entryPrice": 150.50,
    "referencePriceAfter": 151.00,
    "exitPrice": 151.50,
    "timestamp": datetime.now().isoformat(),
    "profitLoss": 100.00
}

response = requests.post(url, json=trade)
print(response.json())
```

#### Update P&L Statement
```python
import requests

url = "http://localhost:8080/api/pnl-statement/update"

pnl_data = {
    "day": {
        "bot1": {"trades": 12, "pnl": 450.75},
        "bot2": {"trades": 8, "pnl": -120.50},
        "total": {"trades": 20, "pnl": 330.25}
    },
    "week": {
        "bot1": {"trades": 65, "pnl": 2150.00},
        "bot2": {"trades": 42, "pnl": 980.25},
        "total": {"trades": 107, "pnl": 3130.25}
    },
    "month": {
        "bot1": {"trades": 285, "pnl": 8750.50},
        "bot2": {"trades": 198, "pnl": 4250.75},
        "total": {"trades": 483, "pnl": 13001.25}
    }
}

response = requests.post(url, json=pnl_data)
print(response.json())
```

---

## 🧪 Testing the API

### Check Health
```bash
curl http://localhost:8080/api/health
```

### Get Current Data
```bash
# Bot overview
curl http://localhost:8080/api/bot-overview

# Trade history
curl http://localhost:8080/api/trade-history

# P&L statement
curl http://localhost:8080/api/pnl-statement
```

---

## 🔧 Troubleshooting

### Port 8080 already in use
```bash
# Kill process on port 8080
lsof -ti:8080 | xargs kill -9

# Or run on different port
PORT=8081 python main.py
```

### Dashboard shows errors
1. Open browser developer console (F12)
2. Check for JavaScript errors
3. Verify API endpoints are responding:
   ```bash
   curl http://localhost:8080/api/health
   ```

### Deployment fails
```bash
# Check authentication
gcloud auth list

# Verify project
gcloud config get-value project

# Check if APIs are enabled
gcloud services list --enabled | grep -E "run|build|container"
```

### Dashboard not updating
- Check browser console for errors
- Verify JavaScript is not blocked
- Try hard refresh: Ctrl+Shift+R (Windows) or Cmd+Shift+R (Mac)

---

## 💰 Cost Information

### Local Development
**FREE** - runs on your computer

### Google Cloud Run
- **Free tier**: 2 million requests/month
- **Light usage** (< 100k requests): **FREE**
- **Medium usage** (100k-500k requests): **$1-5/month**
- **Auto-scales** from 0 to handle traffic spikes
- **Pay only for what you use**

---

## 📊 Next Steps

1. **Customize the UI:**
   - Edit `static/css/dashboard.css` for styling
   - Edit `templates/index.html` for layout

2. **Add More Features:**
   - Add authentication (see README.md)
   - Connect to database for persistence
   - Add charts and visualizations

3. **Integrate with Trading Bots:**
   - Use the API endpoints to send real data
   - Set up automated updates from your bots
   - Add webhooks for real-time updates

4. **Monitor in Production:**
   ```bash
   # View logs
   gcloud run logs read trading-dashboard --limit 50
   
   # Check metrics
   gcloud run services describe trading-dashboard --region us-central1
   ```

---

## 📞 Need Help?

- **Email:** sudhirmakam@gmail.com
- **Full Documentation:** See [README.md](README.md)
- **API Reference:** See API section in README.md

---

**🎉 Congratulations!** Your trading bot dashboard is ready to use!
