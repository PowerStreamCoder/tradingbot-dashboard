#!/bin/bash

# ⚠️  DEPRECATED: This script is no longer used for regular deployments
#
# Primary deployment method: GitHub Actions CI/CD
# Push to 'main' branch triggers automatic deployment
#
# This script is kept only for emergency manual deployments
# when GitHub Actions is unavailable

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "⚠️  DEPRECATED DEPLOYMENT SCRIPT"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "This script is deprecated. Use GitHub Actions instead:"
echo ""
echo "  git push origin main"
echo ""
echo "GitHub Actions will automatically:"
echo "  1. Run tests"
echo "  2. Build container"
echo "  3. Deploy to Cloud Run"
echo "  4. Run smoke tests"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
read -p "Continue with manual deployment anyway? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "Deployment cancelled"
    exit 0
fi

# Configuration
PROJECT_ID="project-dfe26779-9849-4c97-9b6"
REGION="us-central1"
SERVICE_NAME="trading-dashboard"

echo ""
echo "Starting manual deployment..."
echo "Project: $PROJECT_ID"
echo "Service: $SERVICE_NAME"
echo ""

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo "❌ Error: gcloud CLI is not installed"
    exit 1
fi

# Set project
gcloud config set project $PROJECT_ID

# Deploy
echo "Deploying to Cloud Run..."
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

echo ""
echo "✅ Manual deployment complete"
echo ""
echo "⚠️  Remember: Future deployments should use GitHub Actions"
echo "    Just push to main branch: git push origin main"
