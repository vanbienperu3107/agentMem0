#!/bin/bash
# Test 3 mem0 actions BEFORE adding to ChatGPT Custom GPT.
# Run on VPS to verify endpoints work correctly.
#
# Usage:
#   chmod +x test-actions.sh
#   ./test-actions.sh

set -e

BASE="https://claude.hangocthanh.io.vn/memory"
TOKEN=$(grep ^CHATGPT_AUTH_TOKEN ~/memory-stack/.env | cut -d= -f2- | tr -d '"')

if [ -z "$TOKEN" ]; then
    echo "ERROR: CHATGPT_AUTH_TOKEN not found in ~/memory-stack/.env"
    exit 1
fi

echo "=== Test 1: /health (no auth) ==="
curl -s -w "\nHTTP %{http_code}\n" "$BASE/health"
echo ""

echo "=== Test 2: addMemory ==="
ADD_RESP=$(curl -s -X POST "$BASE/memories" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text": "Test memory: Thanh uses mem0 with Qdrant on Singapore VPS", "user_id": "thanh"}')
echo "$ADD_RESP" | head -c 300
echo ""
echo ""

echo "=== Test 3: searchMemory ==="
SEARCH_RESP=$(curl -s -X POST "$BASE/memories/search" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "Qdrant VPS", "user_id": "thanh", "limit": 3}')
echo "$SEARCH_RESP" | head -c 500
echo ""
echo ""

echo "=== Test 4: listMemories ==="
LIST_RESP=$(curl -s "$BASE/memories?user_id=thanh&limit=5" \
  -H "Authorization: Bearer $TOKEN")
echo "$LIST_RESP" | head -c 500
echo ""
echo ""

echo "=== Test 5: openapi.json (spec for ChatGPT) ==="
SPEC=$(curl -s "$BASE/openapi.json" -H "Authorization: Bearer $TOKEN")
echo "$SPEC" | python3 -c "
import sys, json
s = json.load(sys.stdin)
print(f\"servers: {s.get('servers')}\")
print(f\"paths: {list(s.get('paths', {}).keys())}\")
print(f\"operationIds:\")
for p, methods in s.get('paths', {}).items():
    for m, op in methods.items():
        print(f\"  - {op.get('operationId')} ({m.upper()} {p})\")
"

echo ""
echo "=== PASS CRITERIA ==="
echo "Test 1: HTTP 200 + {status: ok}"
echo "Test 2: {ok: true, result: [...]}"
echo "Test 3: {results: [...]} with relevant facts"
echo "Test 4: {results: [...]} with all memories"
echo "Test 5: servers + paths /memories /memories/search; operationIds addMemory/searchMemory/listMemories"
