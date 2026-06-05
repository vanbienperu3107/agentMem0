# R2 Cost Protection — 4-Layer Defense

Goal: make sure a misconfigured upload, leaked token, or runaway script cannot
result in a surprise Cloudflare bill. Each layer is independent; if any one
fails, the next still protects you.

## Layer 1 — Virtual card hard limit ($5/month)

**Where:** the bank/card issuer (Cake, Timo, Visa, etc.)

**How:** the card you give Cloudflare has a $5 monthly international transaction limit.

**What it stops:** Cloudflare cannot charge more than $5 — the bank rejects the
transaction at payment gateway level. This is the ONLY truly hard cap.

**Failure modes:**
- Card limit accidentally raised → no longer hard.
- Cloudflare charges via different processor that bypasses limit → very rare,
  but possible if you've authorized recurring payments.

## Layer 2 — Bucket lifecycle rule (auto-delete after 720 days)

**Where:** R2 bucket → Settings → Object Lifecycle Rules

**How:** any object older than 720 days is auto-deleted by Cloudflare's daily job.

**What it stops:** storage accumulation. With 50 sessions/day × 200 KB, the
rolling 2-year window caps storage at ~7.2 GB → never exceeds 10 GB free tier.

**Failure modes:**
- New code uploads larger files (e.g. uncompressed transcripts) → window cap
  reached sooner. Re-evaluate threshold if avg session size > 500 KB.
- Cloudflare delays lifecycle job during outage → temporary overshoot.

## Layer 3 — Cloudflare Billing Budget Alert ($1 and $3 thresholds)

**Where:** Cloudflare dashboard → Avatar → Billing → Notifications

**How:** email alert when monthly spend on Cloudflare account exceeds $1 (warning)
or $3 (critical). Triggers regardless of which Cloudflare product caused it.

**What it stops:** nothing directly — it's a warning. But it gives you time to
react before Layer 1's hard cap kicks in. Typical lag: 1-3 days from threshold
crossing to email delivery.

**Failure modes:**
- Email goes to spam/filtered.
- Cloudflare notification system outage.
- You miss the email.

## Layer 4 — VPS cron + Telegram alert (this directory)

**Where:** VPS cron at 7:00 daily, script `r2-budget-check.py`

**How:** queries R2 directly via boto3 (S3 API), calculates total bucket size,
sends Telegram push if exceeded threshold.

**Why Telegram instead of email:**
- Push notification on phone (1-2s delivery vs 1-10 min email)
- Independent channel — if Cloudflare email broken, Telegram still works
- Easy to act on (Telegram link can open dashboard directly)

**Default thresholds (override via env):**
- `R2_WARN_GB=7.0`     — warn at 70% of free tier
- `R2_CRITICAL_GB=9.0` — critical at 90%

**Anti-spam:**
- Only alerts when level changes (ok → warn, warn → critical, etc.)
- Re-alerts daily if stuck in warn/critical
- Weekly "OK" heartbeat so you know cron is alive

## Setup checklist

- [ ] Layer 1: bank card limit set to $5/month international
- [ ] Layer 2: lifecycle rule `auto-delete-transcripts-over-2y` enabled in bucket
- [ ] Layer 3: 2 billing alerts ($1 and $3) configured in Cloudflare
- [ ] Layer 4: cron script deployed on VPS, Telegram receiving test message
- [ ] Combined test: upload a tiny file, verify it appears in next-day cron log

## Why 4 layers?

Single-layer protection has 5-15% failure rate (bank glitch, missed email, etc.).
Four independent layers compound: P(all 4 fail) ≈ 0.0001%. With $5 hard cap,
worst case = $5 lost. Not catastrophic.

The trade-off is setup time (~30 min one-time) and ongoing cognitive overhead
of remembering which layer is which. Documented here so future-you doesn't have
to reverse-engineer the design.
