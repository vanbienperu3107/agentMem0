# Copy to scripts/archive-env.ps1 and fill in values.
# Dot-source before running scripts:
#   . "$env:USERPROFILE\scripts\archive-env.ps1"

$env:ARCHIVE_URL        = "https://claude.hangocthanh.io.vn/archive"
$env:ARCHIVE_AUTH_TOKEN = "<paste-from-VPS-/memory-stack/.env>"
$env:USER_ID            = "thanh"

# Only needed if you're behind a corporate proxy:
# $env:HTTP_PROXY  = "http://10.121.127.204:3128"
# $env:HTTPS_PROXY = "http://10.121.127.204:3128"
# $env:NO_PROXY    = "localhost,127.0.0.1"
