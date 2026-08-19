# Current work

## 2026-08-19 GitHub REST stability

- Branch: `fix/vertex-cost-guardrails`
- Goal: separate dashboard authentication with a GitHub App and prevent webhook/SSE operation from exhausting personal REST quota.
- Status: implementation and local validation complete; GitHub App production credentials and Mac mini deployment remain operational follow-up.
- Files: `backend/app/github.py`, `backend/app/main.py`, `frontend/src/App.tsx`, environment/config documentation, and GitHub tests.
- Validation:
  - `PYTHONPATH=. .venv/bin/pytest -q backend/tests`
  - `cd frontend && npm run lint && npm test && npm run build`
- Next steps:
  1. Create/install a read-only GitHub App on `Biz-CollabCraft/ontology_dashboard` and `Biz-CollabCraft/gen_data`.
  2. Configure App ID, installation ID, and private-key path on the Mac mini.
  3. Deploy and confirm `/dev_dashboard/api/health` reports `githubAuthentication: github-app` and a closed REST circuit.
