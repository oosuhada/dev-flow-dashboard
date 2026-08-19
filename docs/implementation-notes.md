# Implementation notes

## 2026-08-19 GitHub REST quota isolation

- Complete GitHub App configuration takes precedence over `GITHUB_TOKEN`; partial App configuration intentionally refuses PAT fallback.
- Installation tokens are generated from a short-lived app JWT and cached conservatively for 55 minutes.
- REST GET responses retain their ETag and payload in process memory. A 304 reuses the saved payload.
- A rate-limit 403/429 opens one aggregator-wide circuit until the greatest applicable `Retry-After`, `X-RateLimit-Reset`, or 60-second minimum backoff.
- Webhooks patch cached PR, review, check, comment-count, and default-branch push state before SSE notification.
- PR open/reopen/synchronize schedules a four-request targeted PR refresh; routine AI PM reads the patched snapshot instead of forcing a full snapshot and PR inspector fetch.
- Browser fallback polling runs every five minutes only while SSE is disconnected. The server recovery watcher runs every ten minutes with conditional requests.
