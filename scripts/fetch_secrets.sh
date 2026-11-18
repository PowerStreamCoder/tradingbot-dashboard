#!/usr/bin/env bash
# ==============================================================================
# Fetch secrets from Google Cloud Secret Manager into a local .env file
# Project: project-dfe26779-9849-4c97-9b6
# ==============================================================================

set -euo pipefail

PROJECT_ID="project-dfe26779-9849-4c97-9b6"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET_ENV="$TARGET_DIR/.env"

echo "🔐 Fetching secrets from Secret Manager for project: $PROJECT_ID..."

if ! command -v gcloud >/dev/null 2>&1; then
    echo "❌ Error: gcloud CLI is not installed or not in PATH."
    exit 1
fi

fetch_secret() {
    local secret_name="$1"
    gcloud secrets versions access latest --secret="$secret_name" --project="$PROJECT_ID" 2>/dev/null || echo ""
}

ALPHAVANTAGE_API_KEY=$(fetch_secret "ALPHAVANTAGE_API_KEY")
FINNHUB_API_KEY=$(fetch_secret "FINNHUB_API_KEY")
NEWSAPI_KEY=$(fetch_secret "NEWSAPI_KEY")
SEC_USER_AGENT=$(fetch_secret "SEC_USER_AGENT")
BOT_CONTROL_API_KEY=$(fetch_secret "BOT_CONTROL_API_KEY")
DASHBOARD_ACCESS_SECRET=$(fetch_secret "DASHBOARD_ACCESS_SECRET")

cat <<EOF > "$TARGET_ENV"
# Generated on $(date) via fetch_secrets.sh
# Production secrets are centralized in Google Cloud Secret Manager ($PROJECT_ID).
SEC_USER_AGENT=$SEC_USER_AGENT
NEWSAPI_KEY=$NEWSAPI_KEY
FINNHUB_API_KEY=$FINNHUB_API_KEY
ALPHAVANTAGE_API_KEY=$ALPHAVANTAGE_API_KEY
BOT_CONTROL_API_KEY=$BOT_CONTROL_API_KEY
DASHBOARD_ACCESS_SECRET=$DASHBOARD_ACCESS_SECRET
EOF

chmod 600 "$TARGET_ENV"
echo "✅ Successfully written local credentials to: $TARGET_ENV (mode 600)"
