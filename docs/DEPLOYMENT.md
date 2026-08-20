# Dashboard Deployment Guide

**Version:** 1.8.0  
**Last Updated:** June 2, 2026  
**Status:** Production

---

## 📋 Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Local Development Deployment](#local-development-deployment)
4. [Google Cloud Run Deployment](#google-cloud-run-deployment)
5. [Configuration](#configuration)
6. [Deployment Verification](#deployment-verification)
7. [Troubleshooting](#troubleshooting)
8. [Rollback Procedures](#rollback-procedures)
9. [Monitoring & Maintenance](#monitoring--maintenance)
10. [CI/CD Integration](#cicd-integration)

---

## Overview

The Trading Bot Dashboard is deployed as a **serverless container** on Google Cloud Run. This guide covers:

- **Local development** - Testing on your machine
- **Cloud deployment** - Production deployment to Google Cloud Run
- **Configuration** - Environment variables and settings
- **Monitoring** - Health checks and logs
- **Troubleshooting** - Common issues and solutions

### Deployment Architecture

```
Local Machine                    Google Cloud
┌─────────────┐                 ┌──────────────────────┐
│             │   deploy.sh     │  Cloud Build         │
│  dashboard/ │  ────────────>  │  • Builds container  │
│             │                 │  • Pushes to GCR     │
└─────────────┘                 └──────────────────────┘
                                          │
                                          ↓
                                ┌──────────────────────┐
                                │  Cloud Run           │
                                │  • Deploys container │
                                │  • Auto-scales       │
                                │  • Serves traffic    │
                                └──────────────────────┘
                                          │
                                          ↓
                                ┌──────────────────────┐
                                │  Public URL          │
                                │  trading-dashboard-* │
                                │  .run.app            │
                                └──────────────────────┘
```

---

## Prerequisites

### Required Software

**For Local Development:**
- Python 3.11+ ([Download](https://www.python.org/downloads/))
- pip (comes with Python)
- Git (optional, for version control)

**For Cloud Deployment:**
- Google Cloud SDK ([Install](https://cloud.google.com/sdk/docs/install))
- Docker (optional, for local container testing)
- Access to GCP project `project-dfe26779-9849-4c97-9b6`

### Required Permissions

**Google Cloud IAM Roles:**
- `roles/run.admin` - Deploy to Cloud Run
- `roles/iam.serviceAccountUser` - Use service account
- `roles/cloudbuild.builds.editor` - Build containers
- `roles/artifactregistry.writer` - Push container images

### GCP Project Setup

**Project ID**: `project-dfe26779-9849-4c97-9b6`  
**Region**: `us-central1`  
**Service Name**: `trading-dashboard`

**Required APIs** (enabled automatically by deploy.sh):
- Cloud Run API (`run.googleapis.com`)
- Cloud Build API (`cloudbuild.googleapis.com`)
- Artifact Registry API (`artifactregistry.googleapis.com`)

---

## Local Development Deployment

### Step 1: Clone/Navigate to Dashboard Directory

```bash
cd /Users/i030983/Library/CloudStorage/OneDrive-Personal/IBKR/dashboard
```

### Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

**Expected output:**
```
Successfully installed fastapi-0.115.0 uvicorn-0.32.0 ...
```

### Step 3: Run the Server

```bash
python main.py
```

**Expected output:**
```
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)
```

### Step 4: Access Dashboard

Open browser: **http://localhost:8080**

You should see the dashboard interface with:
- Bot Control Panel (top)
- Trade Overview
- Bot Overview Tile
- Trade History Tile
- P&L Statement Tile

### Step 5: Test API Endpoints

```bash
# Health check
curl http://localhost:8080/api/health

# Expected: {"status": "healthy"}
```

### Local Development Notes

**Data Source:**
- Local development connects to production Firestore
- All data shown is **real production data**
- Changes affect production (use caution!)

**Hot Reload:**
- Kill server (Ctrl+C)
- Restart: `python main.py`
- No hot reload by default

**Port Conflicts:**
```bash
# If port 8080 is in use
lsof -ti:8080 | xargs kill -9

# Or use different port
PORT=8081 python main.py
```

---

## Google Cloud Run Deployment

### Method 1: Using deploy.sh Script (Recommended)

#### Step 1: Authenticate

```bash
gcloud auth login
```

Follow browser prompts to authenticate.

#### Step 2: Set Project

```bash
gcloud config set project project-dfe26779-9849-4c97-9b6
```

#### Step 3: Navigate to Dashboard Directory

```bash
cd /Users/i030983/Library/CloudStorage/OneDrive-Personal/IBKR/dashboard
```

#### Step 4: Run Deployment Script

```bash
./deploy.sh
```

**Deployment Process:**
1. ✓ Validates gcloud CLI installation
2. ✓ Sets GCP project
3. ✓ Enables required APIs (if not already enabled)
4. ✓ Builds container from source
5. ✓ Pushes to Artifact Registry
6. ✓ Deploys to Cloud Run
7. ✓ Outputs service URL

**Expected output:**
```
======================================
Trading Bot Dashboard Deployment
======================================
Setting project to: project-dfe26779-9849-4c97-9b6
Enabling required Google Cloud APIs...
Operation "operations/..." finished successfully.
Deploying to Cloud Run from source...
Building using Dockerfile and deploying container to Cloud Run service [trading-dashboard]...
✓ Building and deploying new service... Done.
  ✓ Uploading sources...
  ✓ Building Container... Logs are available at [https://...]
  ✓ Creating Revision...
  ✓ Routing traffic...
Done.
Service [trading-dashboard] revision [trading-dashboard-00123] has been deployed.
Service URL: https://trading-dashboard-879908494858.us-central1.run.app

======================================
Deployment Complete!
======================================
Service URL: https://trading-dashboard-879908494858.us-central1.run.app

Access your dashboard at: https://trading-dashboard-879908494858.us-central1.run.app
```

#### Step 5: Verify Deployment

Open the service URL in your browser.

**Deployment Time:**
- First deployment: ~3-5 minutes (build + deploy)
- Subsequent deployments: ~2-3 minutes (incremental build)

---

### Method 2: Manual gcloud Command

```bash
gcloud run deploy trading-dashboard \
  --source . \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --max-instances 10 \
  --min-instances 0 \
  --timeout 300 \
  --project project-dfe26779-9849-4c97-9b6
```

---

### Method 3: Using Dockerfile Directly

#### Build Container

```bash
docker build -t gcr.io/project-dfe26779-9849-4c97-9b6/trading-dashboard:latest .
```

#### Push to Google Container Registry

```bash
docker push gcr.io/project-dfe26779-9849-4c97-9b6/trading-dashboard:latest
```

#### Deploy to Cloud Run

```bash
gcloud run deploy trading-dashboard \
  --image gcr.io/project-dfe26779-9849-4c97-9b6/trading-dashboard:latest \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated
```

---

## Configuration

### Environment Variables

Cloud Run automatically sets:
- `PORT=8080` - Server port
- `GOOGLE_CLOUD_PROJECT=project-dfe26779-9849-4c97-9b6` - GCP project

**Set custom environment variables:**

```bash
gcloud run services update trading-dashboard \
  --region us-central1 \
  --set-env-vars "CUSTOM_VAR=value"
```

### Cloud Run Configuration

**Current Settings:**
```yaml
Service: trading-dashboard
Region: us-central1
Memory: 512Mi
CPU: 1 vCPU
Min Instances: 0 (scales to zero)
Max Instances: 10
Timeout: 300 seconds
Concurrency: 80 (default)
Authentication: Allow unauthenticated
```

**Modify Settings:**

```bash
# Increase memory
gcloud run services update trading-dashboard \
  --region us-central1 \
  --memory 1Gi

# Set min instances (prevent cold starts)
gcloud run services update trading-dashboard \
  --region us-central1 \
  --min-instances 1

# Increase max instances
gcloud run services update trading-dashboard \
  --region us-central1 \
  --max-instances 20
```

### Firestore Configuration

**Database**: Default Firestore database  
**Location**: `us-central` (must match Cloud Run region for best performance)

**Collections Used:**
- `bot_overview` - Bot status and bucket data
- `trade_history` - Completed trades
- `logs` - Bot logs
- `heartbeats` - Bot heartbeat status
- `regime_data` - Market regime information

**No configuration needed** - Service account has automatic access.

### VM Access Configuration (for Bot Control)

Dashboard needs SSH access to VM for bot control (stop/restart).

**Requirements:**
1. Cloud Run service account must have `roles/compute.instanceAdmin.v1`
2. VM must have IAP tunnel enabled
3. gcloud CLI must be available in container (already included)

**Grant Permissions:**

```bash
# Get service account email
SERVICE_ACCOUNT=$(gcloud run services describe trading-dashboard \
  --region us-central1 \
  --format 'value(spec.template.spec.serviceAccountName)')

# Grant compute instance admin role
gcloud projects add-iam-policy-binding project-dfe26779-9849-4c97-9b6 \
  --member="serviceAccount:$SERVICE_ACCOUNT" \
  --role="roles/compute.instanceAdmin.v1"
```

---

## Deployment Verification

### 1. Check Service Status

```bash
gcloud run services describe trading-dashboard \
  --region us-central1 \
  --format yaml
```

**Look for:**
- `status.conditions[0].status: "True"` - Service ready
- `status.url` - Service URL
- `status.latestCreatedRevisionName` - Latest revision

### 2. Check Service URL

```bash
SERVICE_URL=$(gcloud run services describe trading-dashboard \
  --region us-central1 \
  --format 'value(status.url)')

echo $SERVICE_URL
```

### 3. Test Health Endpoint

```bash
curl $SERVICE_URL/api/health
```

**Expected:**
```json
{"status": "healthy"}
```

### 4. Test Dashboard Access

Open browser: `$SERVICE_URL`

**Verify:**
- ✓ Page loads successfully
- ✓ Bot control buttons visible
- ✓ Trade history table loads
- ✓ P&L statements display
- ✓ No JavaScript errors (check browser console)

### 5. Check Cloud Run Logs

```bash
gcloud run services logs read trading-dashboard \
  --region us-central1 \
  --limit 50
```

**Look for:**
- `INFO:     Uvicorn running on http://0.0.0.0:8080`
- `INFO:     Started server process`
- No ERROR or CRITICAL logs

### 6. Check Revision Traffic

```bash
gcloud run services describe trading-dashboard \
  --region us-central1 \
  --format 'value(status.traffic)'
```

**Should show:**
```
100,trading-dashboard-00123-xyx
```
(100% traffic to latest revision)

---

## Troubleshooting

### Issue 1: Deployment Fails

**Error:**
```
ERROR: (gcloud.run.deploy) PERMISSION_DENIED: Permission denied
```

**Solution:**
```bash
# Check authentication
gcloud auth list

# Re-authenticate if needed
gcloud auth login

# Verify project
gcloud config get-value project

# Should output: project-dfe26779-9849-4c97-9b6
```

---

### Issue 2: Container Build Fails

**Error:**
```
ERROR: failed to solve: failed to fetch required artifacts
```

**Solution:**

1. Check Dockerfile syntax:
```bash
docker build -t test .
```

2. Verify requirements.txt exists:
```bash
ls -la requirements.txt
```

3. Check Python version in Dockerfile matches local:
```bash
python --version  # Should be 3.11+
```

---

### Issue 3: Service Not Responding (503 Error)

**Possible Causes:**
- Container crashes on startup
- Port mismatch (container must listen on $PORT)
- Out of memory

**Solution:**

1. Check logs:
```bash
gcloud run services logs read trading-dashboard \
  --region us-central1 \
  --limit 100
```

2. Check container is listening on correct port:
```python
# main.py should have:
import os
port = int(os.environ.get("PORT", 8080))
uvicorn.run(app, host="0.0.0.0", port=port)
```

3. Increase memory if OOM:
```bash
gcloud run services update trading-dashboard \
  --region us-central1 \
  --memory 1Gi
```

---

### Issue 4: Cold Start Timeouts

**Symptom:** First request after idle period takes 5+ seconds

**Solution:**

1. Set min instances to 1:
```bash
gcloud run services update trading-dashboard \
  --region us-central1 \
  --min-instances 1
```

**Note:** Increases cost (~$5-10/month for 1 min instance)

2. Optimize startup time:
- Reduce container size
- Minimize imports in main.py
- Use lazy loading for heavy dependencies

---

### Issue 5: Bot Control Buttons Don't Work

**Error:** "Failed to stop/restart bots"

**Possible Causes:**
- Service account lacks VM access permissions
- VM name/zone incorrect
- IAP tunnel not working

**Solution:**

1. Check service account permissions:
```bash
# Get service account
SA=$(gcloud run services describe trading-dashboard \
  --region us-central1 \
  --format 'value(spec.template.spec.serviceAccountName)')

# Grant compute admin role
gcloud projects add-iam-policy-binding project-dfe26779-9849-4c97-9b6 \
  --member="serviceAccount:$SA" \
  --role="roles/compute.instanceAdmin.v1"
```

2. Verify VM details in main.py:
```python
VM_NAME = "trading-bot-vm"
VM_ZONE = "us-central1-a"
```

3. Test IAP tunnel manually:
```bash
gcloud compute ssh trading-bot-vm \
  --zone us-central1-a \
  --tunnel-through-iap \
  --command "echo test"
```

---

### Issue 6: Firestore Connection Fails

**Error:** "Failed to connect to Firestore"

**Solution:**

1. Verify Firestore API is enabled:
```bash
gcloud services enable firestore.googleapis.com
```

2. Check service account has Firestore access:
```bash
# Grant datastore user role
gcloud projects add-iam-policy-binding project-dfe26779-9849-4c97-9b6 \
  --member="serviceAccount:$SA" \
  --role="roles/datastore.user"
```

3. Verify Firestore database exists:
```bash
gcloud firestore databases list
```

---

## Rollback Procedures

### Rollback to Previous Revision

#### Step 1: List Revisions

```bash
gcloud run revisions list \
  --service trading-dashboard \
  --region us-central1
```

**Output:**
```
REVISION                         ACTIVE  SERVICE             DEPLOYED
trading-dashboard-00005-abc      yes     trading-dashboard   2026-06-02 14:30:00
trading-dashboard-00004-xyz              trading-dashboard   2026-06-01 10:15:00
trading-dashboard-00003-def              trading-dashboard   2026-05-31 09:00:00
```

#### Step 2: Route Traffic to Previous Revision

```bash
gcloud run services update-traffic trading-dashboard \
  --region us-central1 \
  --to-revisions trading-dashboard-00004-xyz=100
```

#### Step 3: Verify Rollback

```bash
curl $SERVICE_URL/api/health
```

**Check logs:**
```bash
gcloud run services logs read trading-dashboard \
  --region us-central1 \
  --limit 20
```

### Gradual Rollout (Canary Deployment)

Deploy new version but route only 10% traffic:

```bash
# Deploy new version
./deploy.sh

# Get new revision name
NEW_REVISION=$(gcloud run services describe trading-dashboard \
  --region us-central1 \
  --format 'value(status.latestCreatedRevisionName)')

# Route 10% to new, 90% to old
gcloud run services update-traffic trading-dashboard \
  --region us-central1 \
  --to-revisions $NEW_REVISION=10,$OLD_REVISION=90

# Monitor metrics, then increase traffic
gcloud run services update-traffic trading-dashboard \
  --region us-central1 \
  --to-revisions $NEW_REVISION=50,$OLD_REVISION=50

# Finally route 100% if all good
gcloud run services update-traffic trading-dashboard \
  --region us-central1 \
  --to-latest
```

---

## Monitoring & Maintenance

### View Logs

**Real-time logs:**
```bash
gcloud run services logs tail trading-dashboard \
  --region us-central1
```

**Recent logs:**
```bash
gcloud run services logs read trading-dashboard \
  --region us-central1 \
  --limit 100
```

**Filter by severity:**
```bash
gcloud run services logs read trading-dashboard \
  --region us-central1 \
  --log-filter "severity>=ERROR"
```

### Monitor Metrics

**Via Cloud Console:**
1. Go to: https://console.cloud.google.com/run
2. Select `trading-dashboard`
3. Click "METRICS" tab

**Metrics available:**
- Request count
- Request latency (p50, p95, p99)
- Container instance count
- CPU utilization
- Memory utilization
- Container startup latency

**Via gcloud:**
```bash
gcloud monitoring dashboards list --project project-dfe26779-9849-4c97-9b6
```

### Set Up Alerts

**Example: Alert on high error rate**

```bash
# Create alert policy via Cloud Console
# Metrics Explorer > Cloud Run > trading-dashboard > Request Count
# Filter: response_code_class="5xx"
# Condition: Rate > 10 errors/minute
# Notification: Email
```

### Health Checks

**Endpoint:** `/api/health`

**Manual check:**
```bash
curl https://trading-dashboard-879908494858.us-central1.run.app/api/health
```

**Automated monitoring:**
```bash
# Add to cron
*/5 * * * * curl -s https://trading-dashboard-879908494858.us-central1.run.app/api/health || echo "Dashboard down!"
```

### Regular Maintenance Tasks

**Weekly:**
- Review error logs
- Check request latency metrics
- Verify bot control functionality

**Monthly:**
- Review and clean old revisions:
```bash
# Delete old revisions (keeps last 3)
gcloud run revisions list \
  --service trading-dashboard \
  --region us-central1 \
  --sort-by ~deployed \
  --limit 100 \
  --format "value(name)" | tail -n +4 | xargs -I {} \
  gcloud run revisions delete {} --region us-central1 --quiet
```

- Review Cloud Run costs
- Update dependencies if needed

---

## CI/CD Integration

### GitHub Actions Example

**File:** `.github/workflows/deploy-dashboard.yml`

```yaml
name: Deploy Dashboard to Cloud Run

on:
  push:
    branches:
      - main
    paths:
      - 'dashboard/**'

jobs:
  deploy:
    runs-on: ubuntu-latest
    
    steps:
      - name: Checkout code
        uses: actions/checkout@v3
      
      - name: Authenticate to Google Cloud
        uses: google-github-actions/auth@v1
        with:
          credentials_json: ${{ secrets.GCP_SA_KEY }}
      
      - name: Set up Cloud SDK
        uses: google-github-actions/setup-gcloud@v1
      
      - name: Deploy to Cloud Run
        run: |
          cd dashboard
          gcloud run deploy trading-dashboard \
            --source . \
            --platform managed \
            --region us-central1 \
            --allow-unauthenticated \
            --project project-dfe26779-9849-4c97-9b6
```

### Manual Deployment Workflow

**Recommended Steps:**

1. **Test locally**
```bash
cd dashboard
python main.py
# Test all features
```

2. **Review changes**
```bash
git diff
```

3. **Deploy to Cloud Run**
```bash
./deploy.sh
```

4. **Verify deployment**
```bash
curl $SERVICE_URL/api/health
```

5. **Monitor for 10 minutes**
```bash
gcloud run services logs tail trading-dashboard --region us-central1
```

6. **Rollback if issues**
```bash
# See Rollback Procedures section
```

---

## Cost Estimation

### Cloud Run Pricing (us-central1)

**Free Tier (per month):**
- 2 million requests
- 360,000 GiB-seconds memory
- 180,000 vCPU-seconds

**Current Configuration:**
- Memory: 512Mi
- CPU: 1 vCPU
- Min instances: 0

**Estimated Monthly Cost:**

**Scenario 1: Light Usage (< 10k requests/month)**
- Requests: 10,000
- Avg response time: 100ms
- Cost: **FREE** (within free tier)

**Scenario 2: Medium Usage (100k requests/month)**
- Requests: 100,000
- Avg response time: 100ms
- Memory: ~5 GiB-seconds
- CPU: ~10 vCPU-seconds
- Cost: **$0.50 - $2/month**

**Scenario 3: High Usage (1M requests/month)**
- Requests: 1,000,000
- Cost: **$8-15/month**

**Scenario 4: With Min Instance = 1 (No cold starts)**
- Always-on: 1 instance × 512Mi × 730 hours
- Additional cost: **$6-8/month**

**Total typical cost: $1-10/month**

---

## Security Best Practices

### Current Security

✅ **Implemented:**
- HTTPS enforced (automatic via Cloud Run)
- Service runs with minimal service account
- No hardcoded credentials
- Environment variables for config
- IAP tunnel for VM access (no public SSH)

❌ **TODO:**
- Authentication (currently allow-unauthenticated)
- Rate limiting
- Input validation/sanitization
- CORS restrictions (currently allows all origins)

### Enable Authentication (Future)

**Step 1: Require authentication**
```bash
gcloud run services update trading-dashboard \
  --region us-central1 \
  --no-allow-unauthenticated
```

**Step 2: Add Firebase Auth to frontend**
```javascript
// Add Firebase Auth UI
// Require login before showing dashboard
```

**Step 3: Verify tokens in backend**
```python
# Add Firebase token verification middleware
```

---

## Summary

**Dashboard deployment is:**
- ✅ **Simple** - Single command (`./deploy.sh`)
- ✅ **Fast** - 2-3 minutes per deployment
- ✅ **Reliable** - Cloud Run 99.95% SLA
- ✅ **Scalable** - Auto-scales from 0 to N
- ✅ **Cost-effective** - $1-10/month typical

**Production URL:**  
https://trading-dashboard-879908494858.us-central1.run.app

**Key Commands:**
```bash
# Deploy
./deploy.sh

# Check status
gcloud run services describe trading-dashboard --region us-central1

# View logs
gcloud run services logs read trading-dashboard --region us-central1

# Rollback
gcloud run services update-traffic trading-dashboard \
  --region us-central1 --to-revisions REVISION_NAME=100
```

For more details, see:
- [QUICKSTART.md](QUICKSTART.md) - Quick start guide
- [ARCHITECTURE.md](ARCHITECTURE.md) - Technical architecture
- [README.md](README.md) - Feature documentation
