#!/bin/bash
# Copy to ~/scripts/r2-budget-env.sh on VPS and fill in values.
# Sourced by cron via wrapper, or by you for manual runs:
#   source ~/scripts/r2-budget-env.sh && python3 ~/scripts/r2-budget-check.py

export R2_ENDPOINT_URL="https://<account-id>.r2.cloudflarestorage.com"
export R2_ACCESS_KEY_ID="<paste from Cloudflare API Token>"
export R2_SECRET_ACCESS_KEY="<paste from Cloudflare API Token>"
export R2_BUCKET="mem0-transcripts"

export TELEGRAM_BOT_TOKEN="<from @BotFather>"
export TELEGRAM_CHAT_ID="<your numeric chat ID>"

# Optional overrides
# export R2_WARN_GB="7.0"
# export R2_CRITICAL_GB="9.0"
# export R2_WEEKLY_SUMMARY_DAYS="7"
