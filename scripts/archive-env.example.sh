#!/bin/bash
# Copy to scripts/archive-env.sh and fill in values.
# Source before running archive-upload.py or archive-mcp.py:
#   source ~/.config/archive-env
# Or place this file at ~/.config/archive-env

export ARCHIVE_URL="https://claude.hangocthanh.io.vn/archive"
export ARCHIVE_AUTH_TOKEN="<paste-from-VPS-/memory-stack/.env>"
export USER_ID="thanh"

# Only needed if you're behind a corporate proxy:
# export HTTP_PROXY="http://10.121.127.204:3128"
# export HTTPS_PROXY="http://10.121.127.204:3128"
# export NO_PROXY="localhost,127.0.0.1"
