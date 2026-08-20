#!/bin/bash

# Trading Bot Dashboard - Google Cloud Deployment Script (Simplified)

set -e

# Configuration
PROJECT_ID="project-dfe26779-9849-4c97-9b6"
REGION="us-central1"
SERVICE_NAME="trading-dashboard"

echo "======================================"
echo "Trading Bot Dashboard Deployment"
echo "======================================"

# Get the directory where this script is located and go to parent (dashboard root)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
DASHBOARD_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
cd "$DASHBOARD_DIR"

echo "Deploying from: $DASHBOARD_DIR"

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo "Error: gcloud CLI is not installed"
    echo "Install from: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

# Verify required files exist
echo "Verifying deployment files..."
REQUIRED_FILES=("main.py" "requirements.txt" "Dockerfile" "static" "templates")
for file in "${REQUIRED_FILES[@]}"; do
    if [ ! -e "$file" ]; then
        echo "Error: Required file/directory '$file' not found in $SCRIPT_DIR"
        exit 1
    fi
done
echo "✓ All required files present"

# Set project
echo "Setting project to: $PROJECT_ID"
gcloud config set project $PROJECT_ID

# Enable required APIs
echo "Enabling required Google Cloud APIs..."
gcloud services enable run.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable artifactregistry.googleapis.com

# Deploy directly using gcloud run deploy with source
echo "Deploying to Cloud Run from source..."
echo "✓ Using IBKR data via Firestore (no external API keys required)"

# Deploy without API key - now uses IBKR data from Firestore
gcloud run deploy $SERVICE_NAME \
    --source . \
    --platform managed \
    --region $REGION \
    --allow-unauthenticated \
    --memory 512Mi \
    --cpu 1 \
    --max-instances 10 \
    --min-instances 0 \
    --timeout 300

# Get the service URL
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME \
    --platform managed \
    --region $REGION \
    --format 'value(status.url)')

echo ""
echo "======================================"
echo "Deployment Complete!"
echo "======================================"
echo "Service URL: $SERVICE_URL"
echo ""
echo "Access your dashboard at: $SERVICE_URL"
echo ""
echo "📊 Data Source: IBKR (15-min delayed) via Firestore"
echo "   - Historical bars: Last 100 from bot's recent_bars"
echo "   - SMA indicators: Calculated by bot, stored in bot_status"
echo "   - All data matches what the bot uses for trading"
echo ""
echo "📁 Deployed files:"
echo "   - main.py (FastAPI application)"
echo "   - templates/ (index.html, nvda_focus.html, learning_review.html)"
echo "   - static/css/ (dashboard.css, nvda_focus.css)"
echo "   - static/js/ (dashboard.js, nvda_focus.js, trade-charts.js)"
echo "   - requirements.txt (dependencies)"
echo ""
