# StockPicker - On-Demand Execution Guide

## ✅ Better Approach: Run On-Demand (No Cron Needed!)

Instead of running on a schedule, you can now trigger StockPicker **whenever you want** directly from the dashboard.

---

## How It Works

### **In the Dashboard**
1. Visit: https://trading-dashboard-w2n5czslna-uc.a.run.app
2. Click **"📊 Stock Picker"** tab
3. Click **"▶️ Run Now"** button
4. Wait 30-90 seconds (it shows "⏳ Running...")
5. You'll get an alert with results
6. Table automatically refreshes with new picks!

### **What Happens Behind the Scenes**
```
Click "Run Now" Button
  ↓
POST /api/stock-picks/run
  ↓
Fetch news (last 24h)
  ↓
Rank by explosiveness
  ↓
Score fundamentals
  ↓
Select top 5
  ↓
Save to Firestore
  ↓
Show results in dashboard
```

---

## Benefits Over Cron

✅ **No VM setup needed** - Everything runs in Cloud Run
✅ **Run whenever you want** - Not tied to schedule
✅ **See progress** - Shows "Running..." status
✅ **Immediate feedback** - Alert with pick count & duration
✅ **No failed cron jobs** - Only runs when you trigger it
✅ **Lower costs** - Only uses resources when you run it

---

## Expected Behavior

### **First Run (No API Keys)**
Click "Run Now" → After 10-20 seconds:
```
✅ Success!

Generated 0 picks in 12.3s

No picks generated (no news available)
```

**Why?** No API keys = No news sources = No picks

**Solution**: Add API keys (see below) OR wait until market hours when more free data available

### **With API Keys Configured**
Click "Run Now" → After 30-90 seconds:
```
✅ Success!

Generated 5 picks in 67.8s

Generated 5 picks successfully
```

Then the table shows your picks!

---

## Adding API Keys (Optional)

### **For Cloud Run**
Since StockPicker now runs **inside Cloud Run**, add environment variables:

1. **Go to Cloud Console**:
   - https://console.cloud.google.com/run
   - Select `trading-dashboard` service
   - Click "Edit & Deploy New Revision"

2. **Add Environment Variables** (all optional):
   ```
   NEWSAPI_KEY=your_key_here
   X_BEARER_TOKEN=your_token_here
   POLYGON_API_KEY=your_key_here
   OPENAI_API_KEY=your_key_here
   OPENAI_MODEL=gpt-4o-mini
   ALPHAVANTAGE_API_KEY=your_key_here
   SEC_USER_AGENT=stockpicker/1.0 your_email@example.com
   ```

3. **Deploy** the new revision

### **Or Edit via gcloud**
```bash
gcloud run services update trading-dashboard \
  --region us-central1 \
  --set-env-vars "OPENAI_API_KEY=sk-...,SEC_USER_AGENT=stockpicker/1.0 your@email.com"
```

---

## Alternative: Schedule It (If You Want)

You can still set up a cron job to auto-run it, but now it's **optional**:

### **Option 1: Cloud Scheduler (GCP)**
Create a Cloud Scheduler job that calls your endpoint:

```bash
gcloud scheduler jobs create http stockpicker-daily \
  --schedule="45 13 * * 1-5" \
  --uri="https://trading-dashboard-w2n5czslna-uc.a.run.app/api/stock-picks/run" \
  --http-method=POST \
  --oidc-service-account-email=YOUR_SERVICE_ACCOUNT@project.iam.gserviceaccount.com \
  --time-zone="America/New_York"
```

### **Option 2: VM Cron** (if you prefer)
Still works! Just call the API instead:

```bash
# On VM crontab
45 13 * * 1-5 curl -X POST https://trading-dashboard-w2n5czslna-uc.a.run.app/api/stock-picks/run
```

---

## Resource Usage

### **Memory & CPU**
- Cloud Run instance: 512MB RAM
- StockPicker run: Uses ~200MB during execution
- Duration: 30-90 seconds per run
- **Cost**: ~$0.01 per run (within free tier for occasional use)

### **Recommended Usage**
- **Manual runs**: 1-3 times per week when markets are active
- **Scheduled**: Daily at market time (9:45 AM ET) if you want automation

---

## Troubleshooting

### **"Run Now" Button Not Working**
- Check browser console (F12) for errors
- Verify you're logged in to dashboard
- Check Network tab - should see POST to `/api/stock-picks/run`

### **"StockPicker is already running"**
- Wait for current run to complete (max 2 minutes)
- Refresh page if stuck

### **"Generated 0 picks"**
**Causes**:
1. No API keys configured → Add at least OpenAI + one news source
2. No explosive news (< 7.5 threshold) → Normal, try again later
3. Market closed → Less news available on weekends

**Fix**: Add API keys or run during market hours

### **"StockPicker run failed"**
- Check Cloud Run logs: Console → Cloud Run → trading-dashboard → Logs
- Look for Python errors
- Verify Firestore permissions

---

## Monitoring

### **Check Cloud Run Logs**
```bash
gcloud run services logs read trading-dashboard \
  --limit=50 \
  --region=us-central1
```

Or visit: https://console.cloud.google.com/run/detail/us-central1/trading-dashboard/logs

### **Check Firestore**
Console → Firestore → `stock_picks` collection → `current` document

Should see:
```json
{
  "picks": [...],
  "run_timestamp": "2026-08-12T13:45:00Z",
  "pick_count": 5,
  "avg_explosiveness": 8.2
}
```

---

## Cost Comparison

### **On-Demand (Recommended)**
- **Cost**: ~$0.01 per run × 12 runs/month = **$0.12/month**
- **When**: Only when you click "Run Now"
- **Best for**: Manual analysis, testing, occasional use

### **Daily Scheduled (Cloud Scheduler)**
- **Cost**: ~$0.10/month (scheduler) + ~$0.20/month (runs) = **$0.30/month**
- **When**: Automatically at 9:45 AM ET, Mon-Fri
- **Best for**: Daily routine, automation

### **VM Cron**
- **Cost**: $0 (VM already running)
- **Complexity**: Higher (need VM access, dependencies)
- **Best for**: If you already manage VMs

---

## What This Replaces

❌ ~~No VM cron setup needed~~
❌ ~~No SSH to VM~~
❌ ~~No pip install on VM~~
❌ ~~No crontab -e~~
❌ ~~No log file monitoring~~

✅ **Just click "Run Now" in the dashboard!**

---

## Summary

**Old Way**: Setup cron on VM → Runs on schedule → Hope it works → Check logs

**New Way**: Click "Run Now" → See it run → Get immediate results → Done!

**Recommended**: Use on-demand for now, add scheduling later if needed.

🎯 **Try it now**: https://trading-dashboard-w2n5czslna-uc.a.run.app → Stock Picker tab → Run Now!
