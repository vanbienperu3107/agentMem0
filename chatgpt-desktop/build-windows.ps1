# Build script cho Windows — chạy trong PowerShell (Admin không bắt buộc).
# Usage: .\build-windows.ps1
#
# Sẽ tự cài Rust nếu chưa có, install npm deps, chạy cargo test, rồi build .exe + installer.
# Kết quả nằm trong src-tauri\target\release\bundle\

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

function Write-Step($msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Test-Command($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

# ---------- 1. Kiểm tra Rust ----------
Write-Step "Kiểm tra Rust toolchain"
if (-not (Test-Command "cargo")) {
    Write-Host "Rust chưa được cài. Đang tải rustup-init..." -ForegroundColor Yellow
    $rustup = "$env:TEMP\rustup-init.exe"
    Invoke-WebRequest -Uri "https://win.rustup.rs/x86_64" -OutFile $rustup
    & $rustup -y --default-toolchain stable --profile minimal
    $env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
}
cargo --version

# ---------- 2. Kiểm tra Node + pnpm ----------
Write-Step "Kiểm tra Node + pnpm"
if (-not (Test-Command "node")) {
    Write-Host "Node.js chưa được cài. Vui lòng cài Node 18+ từ https://nodejs.org" -ForegroundColor Red
    exit 1
}
node --version

if (-not (Test-Command "pnpm")) {
    Write-Host "pnpm chưa có, đang cài qua npm..." -ForegroundColor Yellow
    npm install -g pnpm
}
pnpm --version

# ---------- 3. Kiểm tra WebView2 ----------
Write-Step "Kiểm tra Microsoft Edge WebView2 Runtime"
$wv2 = Get-ItemProperty -Path "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" -ErrorAction SilentlyContinue
if (-not $wv2) {
    Write-Host "Cài WebView2 Evergreen Bootstrapper trước khi build..." -ForegroundColor Yellow
    $wv2Url = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
    $wv2Setup = "$env:TEMP\MicrosoftEdgeWebview2Setup.exe"
    Invoke-WebRequest -Uri $wv2Url -OutFile $wv2Setup
    & $wv2Setup /silent /install
}

# ---------- 4. Install frontend deps ----------
Write-Step "Cài npm dependencies"
Set-Location $ProjectRoot
pnpm install

# ---------- 5. Chạy Rust unit tests ----------
Write-Step "Chạy Rust unit tests (history.rs) — lần đầu sẽ download ~300 crate, có thể mất 10-20 phút"
Set-Location "$ProjectRoot\src-tauri"
# --bin chatgpt: project là binary, không có lib target.
# --color always + verbose: hiển thị tiến độ real-time, không bị buffer khi pipe.
$env:CARGO_TERM_COLOR = "always"
$env:CARGO_TERM_PROGRESS_WHEN = "always"
cargo test --bin chatgpt core::history -- --nocapture
if ($LASTEXITCODE -ne 0) {
    Write-Host "Unit test fail — KHÔNG build .exe" -ForegroundColor Red
    exit 1
}
Write-Host "Tất cả Rust tests PASS" -ForegroundColor Green

# ---------- 6. Build production .exe + installer ----------
Write-Step "Build .exe + installer (MSI / NSIS)"
Set-Location $ProjectRoot
pnpm tauri build

# ---------- 7. Báo kết quả ----------
Write-Step "Build xong"
$bundleDir = "$ProjectRoot\src-tauri\target\release\bundle"
Write-Host "Output:" -ForegroundColor Green
Get-ChildItem -Path $bundleDir -Recurse -Include *.exe, *.msi | ForEach-Object {
    Write-Host "  $($_.FullName)"
}
Write-Host ""
Write-Host "Cài đặt: chạy file .msi hoặc .exe trong bundle/" -ForegroundColor Green
