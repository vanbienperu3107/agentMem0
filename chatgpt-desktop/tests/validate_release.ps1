# Validate ChatGPT Desktop release sâu hơn smoke-test
# Yêu cầu: app đã được install + launched + tạo data
# Usage: pwsh -File validate_release.ps1 [-DataDir <path>] [-ExeDir <path>]

param(
    [string]$DataDir = "",
    [string]$ExeDir = ""
)

$ErrorActionPreference = "Continue"
$global:Passed = 0
$global:Failed = 0
$global:Warnings = 0

function Assert-True($cond, $msg) {
    if ($cond) {
        Write-Host "PASS  $msg" -ForegroundColor Green
        $global:Passed++
    } else {
        Write-Host "FAIL  $msg" -ForegroundColor Red
        $global:Failed++
    }
}

function Assert-Warn($cond, $msg) {
    if ($cond) {
        Write-Host "PASS  $msg" -ForegroundColor Green
        $global:Passed++
    } else {
        Write-Host "WARN  $msg" -ForegroundColor Yellow
        $global:Warnings++
    }
}

# Auto-detect DataDir nếu không truyền
if (-not $DataDir) {
    $candidates = @()
    if ($ExeDir) {
        $candidates += Join-Path $ExeDir "data\com.nofwl.chatgpt"
    }
    $candidates += "$env:APPDATA\com.nofwl.chatgpt"
    foreach ($p in $candidates) {
        if (Test-Path $p) { $DataDir = $p; break }
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " ChatGPT Desktop Release Validation" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "DataDir: $DataDir"
Write-Host "ExeDir:  $ExeDir"
Write-Host ""

# ===== R1: Data dir exists =====
Assert-True (Test-Path $DataDir) "[R1] App data dir tồn tại: $DataDir"

# ===== R2: Log file =====
$logFile = Join-Path $DataDir "logs\app.log"
Assert-True (Test-Path $logFile) "[R2] Log file: logs\app.log"

if (Test-Path $logFile) {
    $logContent = Get-Content $logFile -Raw
    Assert-True ($logContent -match "ChatGPT Desktop starting") `
        "[R3] Log chứa 'ChatGPT Desktop starting'"
    Assert-True ($logContent -match "\[app\] version") `
        "[R4] Log chứa app version"
    Assert-True ($logContent -match "init_session OK") `
        "[R5] Log chứa init_session OK"
    Assert-True ($logContent -match "\[portable\]") `
        "[R6] Log chứa portable detect logic"
}

# ===== R7: Session metadata =====
$meta = Join-Path $DataDir "current.session"
Assert-True (Test-Path $meta) "[R7] current.session file tồn tại"

if (Test-Path $meta) {
    try {
        $session = Get-Content $meta -Raw | ConvertFrom-Json
        Assert-True ($session.session_id -match '^s[0-9a-f]+$') `
            "[R8] session_id format đúng: $($session.session_id)"
        Assert-True ($session.started_at -gt 0) `
            "[R9] started_at timestamp > 0"
        Assert-True ($null -ne $session.started_at_iso) `
            "[R10] started_at_iso có giá trị"
    } catch {
        Assert-True $false "[R8-R10] current.session parse JSON: $_"
    }
}

# ===== R11: WAL file =====
$wal = Join-Path $DataDir "current.wal"
Assert-True (Test-Path $wal) "[R11] current.wal tồn tại"

# ===== R12: Sessions folder =====
$sessions = Join-Path $DataDir "sessions"
Assert-Warn (Test-Path $sessions) "[R12] sessions/ folder tồn tại (chưa compact = warn)"

# ===== R13: Portable detect (nếu ExeDir đặt) =====
if ($ExeDir) {
    $expectedPortable = Join-Path $ExeDir "data\com.nofwl.chatgpt"
    if ($DataDir -eq $expectedPortable) {
        Write-Host "PASS  [R13] Portable mode auto-detected (data cạnh exe)" -ForegroundColor Green
        $global:Passed++
    } else {
        Write-Host "INFO  [R13] AppData mode (exe có thể nằm trong Program Files)" -ForegroundColor Cyan
    }
}

# ===== R14: NO sqlite =====
$sqlite = Get-ChildItem -Path $DataDir -Recurse -Include *.db, *.sqlite -ErrorAction SilentlyContinue
Assert-True ($null -eq $sqlite) "[R14] KHÔNG có SQLite file (yêu cầu chỉ JSON)"

# ===== R15: Run Python acceptance test nếu có =====
$pyTest = "tests/acceptance_test.py"
if (Test-Path $pyTest) {
    Write-Host ""
    Write-Host "Running Python acceptance test..." -ForegroundColor Cyan
    $pyOut = python $pyTest "$DataDir" 2>&1
    $pyOut | ForEach-Object { Write-Host "  $_" }
    if ($LASTEXITCODE -eq 0) {
        Write-Host "PASS  [R15] Python acceptance test (10 case) tất cả PASS" -ForegroundColor Green
        $global:Passed++
    } else {
        Write-Host "FAIL  [R15] Python acceptance test fail" -ForegroundColor Red
        $global:Failed++
    }
}

# ===== Summary =====
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " RESULT" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Passed:   $global:Passed" -ForegroundColor Green
Write-Host "Warnings: $global:Warnings" -ForegroundColor Yellow
Write-Host "Failed:   $global:Failed" -ForegroundColor Red

if ($global:Failed -gt 0) {
    Write-Host ""
    Write-Host "RELEASE VALIDATION FAILED" -ForegroundColor Red
    exit 1
}
Write-Host ""
Write-Host "RELEASE VALIDATION PASSED" -ForegroundColor Green
exit 0
