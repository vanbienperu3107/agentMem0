<#
.SYNOPSIS Test API key (openai/anthropic) hoặc auth token (-Provider archive) mà không cần mở app.
.EXAMPLE .\test-provider-key.ps1 -Provider openai_4o
.EXAMPLE .\test-provider-key.ps1 -Provider archive
#>
param([Parameter(Mandatory=$true)][string]$Provider, [string]$ConfigPath = "")
$ErrorActionPreference = "Stop"

function Resolve-EnvVar([string]$value) {
    if ($value -match '^\$\{env:([^}]+)\}$') {
        return [Environment]::GetEnvironmentVariable($matches[1])
    }
    return $value
}

if ($Provider -eq "archive") {
    $syncPath = if ($ConfigPath) { $ConfigPath } else {
        $p = Join-Path $env:APPDATA "com.nofwl.chatgpt\sync.json"
        if (Test-Path $p) { $p } else { throw "Không tìm thấy sync.json" }
    }
    Write-Host "Đọc: $syncPath" -ForegroundColor Cyan
    $sc = Get-Content $syncPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $token = Resolve-EnvVar $sc.auth_token
    if (-not $token -or $token -match '^\$\{env:') {
        Write-Error "auth_token chưa cấu hình"; exit 1
    }
    $url = "$($sc.archive_url)/sessions?user_id=$($sc.user_id)&limit=1"
    try {
        $resp = Invoke-RestMethod -Method GET -Uri $url -Headers @{ "Authorization" = "Bearer $token" } -TimeoutSec 30
        Write-Host "OK ✓ archive-api work" -ForegroundColor Green
        exit 0
    } catch {
        Write-Host "LỖI ✗ $($_.Exception.Message)" -ForegroundColor Red
        exit 2
    }
}

# Summarize provider mode
$cfgPath = if ($ConfigPath) { $ConfigPath } else { Join-Path $env:APPDATA "com.nofwl.chatgpt\summarize.json" }
$cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
$p = $cfg.providers.$Provider
$apiKey = Resolve-EnvVar $p.api_key
if (-not $apiKey -or $apiKey -match '^\$\{env:') {
    Write-Error "api_key chưa cấu hình"; exit 1
}
$body = @{ model = $p.model; messages = @(@{ role = "user"; content = "ping" }); max_tokens = 10 } | ConvertTo-Json -Depth 4
$headers = if ($p.type -eq "anthropic") {
    @{ "x-api-key" = $apiKey; "anthropic-version" = "2023-06-01"; "Content-Type" = "application/json" }
} else {
    @{ "Authorization" = "Bearer $apiKey"; "Content-Type" = "application/json" }
}
try {
    $resp = Invoke-RestMethod -Method POST -Uri $p.endpoint -Headers $headers -Body $body -TimeoutSec 30
    Write-Host "OK ✓ Provider '$Provider' work · model=$($p.model)" -ForegroundColor Green
    exit 0
} catch {
    Write-Host "LỖI ✗ $($_.Exception.Message)" -ForegroundColor Red
    exit 2
}
