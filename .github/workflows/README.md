# GitHub Actions

## Workflows

### `test.yml` — CI tests on every push/PR

2 jobs:

#### Job 1: `syntax-check` (always runs)
- Python `ast.parse()` validation on all `.py` files
- YAML validation on all `.yaml`/`.yml`
- OpenAPI 3.x spec validation for `memory-rest-api/openapi-for-chatgpt.yaml`
- `docker compose config --quiet` validation
- Dockerfile basic syntax check
- `.env.example` contains all required env vars

#### Job 2: `smoke-test-prod` (only on `main` or manual trigger)
- Tests live production endpoints at `https://claude.hangocthanh.io.vn`
- Skips tests if corresponding secret not set
- Tests:
  - `/health` (no auth)
  - `/archive/sessions` with `ARCHIVE_AUTH_TOKEN`
  - `/memory/openapi.json` with `CHATGPT_AUTH_TOKEN`
  - `/mcp tools/list` with `MCP_BEARER_TOKEN`

## Setup secrets (for smoke-test-prod)

Repo Settings → Secrets and variables → Actions → New repository secret:

| Secret name | Value source |
|---|---|
| `ARCHIVE_AUTH_TOKEN` | `~/memory-stack/.env` on VPS |
| `CHATGPT_AUTH_TOKEN` | `~/memory-stack/.env` on VPS |
| `MCP_BEARER_TOKEN` | `~/memory-stack/.env` on VPS |

⚠️ **Security:** secrets are exposed only to workflow runtime, never logged. But anyone with repo admin access can read them. Use scoped tokens (read-only for production tests).

## Triggers

- **Push to `main` or `new-features`** → syntax-check only
- **PR to either branch** → syntax-check only
- **Push to `main`** → syntax-check + smoke-test-prod
- **Manual via Actions tab → Run workflow** → both jobs

## Run locally

```bash
# Install act (https://github.com/nektos/act)
brew install act  # or: choco install act

# Run syntax-check job locally
act -j syntax-check

# Run smoke-test (requires secrets file)
echo "ARCHIVE_AUTH_TOKEN=..." > .secrets
act -j smoke-test-prod --secret-file .secrets
```
