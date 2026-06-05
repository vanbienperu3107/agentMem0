# Test 3 mem0 actions BEFORE adding to ChatGPT Custom GPT.
# Run from PowerShell on your local Windows machine to verify endpoints work.
#
# Usage:
#   1. Set $TOKEN with the value of CHATGPT_AUTH_TOKEN (from VPS .env)
#   2. Run: .\test-actions.ps1

$BASE = "https://claude.hangocthanh.io.vn/memory"

# CHATGPT_AUTH_TOKEN — replace with the value from VPS .env
$TOKEN = Read-Host -Prompt "Paste CHATGPT_AUTH_TOKEN" -AsSecureString
$TOKEN_PLAIN = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($TOKEN)
)

Write-Host "`n=== Test 1: /health (no auth) ===" -ForegroundColor Cyan
curl.exe -s "$BASE/health"
Write-Host ""

Write-Host "`n=== Test 2: addMemory ===" -ForegroundColor Cyan
$addBody = '{"text":"Test memory: Thanh uses mem0 with Qdrant on Singapore VPS","user_id":"thanh"}'
$addResp = curl.exe -s -X POST "$BASE/memories" `
    -H "Authorization: Bearer $TOKEN_PLAIN" `
    -H "Content-Type: application/json" `
    -d $addBody
Write-Host ($addResp.Substring(0, [Math]::Min(300, $addResp.Length)))

Write-Host "`n`n=== Test 3: searchMemory ===" -ForegroundColor Cyan
$searchBody = '{"query":"Qdrant VPS","user_id":"thanh","limit":3}'
$searchResp = curl.exe -s -X POST "$BASE/memories/search" `
    -H "Authorization: Bearer $TOKEN_PLAIN" `
    -H "Content-Type: application/json" `
    -d $searchBody
Write-Host ($searchResp.Substring(0, [Math]::Min(500, $searchResp.Length)))

Write-Host "`n`n=== Test 4: listMemories ===" -ForegroundColor Cyan
$listResp = curl.exe -s "$BASE/memories?user_id=thanh&limit=5" -H "Authorization: Bearer $TOKEN_PLAIN"
Write-Host ($listResp.Substring(0, [Math]::Min(500, $listResp.Length)))

Write-Host "`n`n=== Test 5: openapi.json (spec for ChatGPT) ===" -ForegroundColor Cyan
$spec = curl.exe -s "$BASE/openapi.json" -H "Authorization: Bearer $TOKEN_PLAIN"
$specJson = $spec | ConvertFrom-Json
Write-Host "servers: $($specJson.servers | ConvertTo-Json -Compress)"
Write-Host "paths: $($specJson.paths.PSObject.Properties.Name -join ', ')"
Write-Host "operationIds:"
foreach ($path in $specJson.paths.PSObject.Properties) {
    foreach ($method in $path.Value.PSObject.Properties) {
        Write-Host "  - $($method.Value.operationId) ($($method.Name.ToUpper()) $($path.Name))"
    }
}

Write-Host "`n=== PASS CRITERIA ===" -ForegroundColor Yellow
Write-Host "Test 1: HTTP 200 + status:ok"
Write-Host "Test 2: ok:true with result array"
Write-Host "Test 3: results array with relevant facts"
Write-Host "Test 4: results array with all memories"
Write-Host "Test 5: servers field + 3 paths + operationIds addMemory/searchMemory/listMemories"

# Clean variable (don't leak token to session history)
Remove-Variable TOKEN_PLAIN
