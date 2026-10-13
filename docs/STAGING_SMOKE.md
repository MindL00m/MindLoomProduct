# Staging smoke checklist (P1)

Use this before a public beta cut. Mark each item pass/fail. Automated curls
(unauthenticated + optional JWT) live in [`scripts/staging_smoke.sh`](../scripts/staging_smoke.sh).

```bash
# Against a running stack (Compose API on :8000 by default):
./scripts/staging_smoke.sh
API_BASE=https://api.staging.example.com ./scripts/staging_smoke.sh
SMOKE_TOKEN='eyJ…' ./scripts/staging_smoke.sh
```

Pre-deploy gate: `cd apps/api && pytest`.

---

## Automated (script)

- [ ] `GET /health` → 200 `{"status":"ok"}`
- [ ] `GET /ready` → 200 when Postgres, Redis, Neo4j, and worker heartbeat are healthy
- [ ] `GET /captures` without auth → 401
- [ ] With `SMOKE_TOKEN`: `GET /auth/me`, `GET /org/summary`, `GET /integrations` → 200

---

## Manual — auth and onboarding

- [ ] Google GIS sign-in on staging web (baked `VITE_GOOGLE_CLIENT_ID`, public origin authorized)
- [ ] Create organization as admin
- [ ] Second user with the same email domain signs in and joins the org
- [ ] Session survives browser refresh (Loom JWT)

## Manual — directory and documents

- [ ] Admin CSV directory import → people / org chart / `GET /org/summary` counts match
- [ ] Upload a PDF or DOCX → ingestion job reaches `complete` via the worker
- [ ] Ask a question whose answer is in that doc → response includes citations

## Manual — integrations

- [ ] Connect one of Google Workspace / Microsoft Teams / Zoom with **public** redirect URIs
- [ ] Restart or bounce the API process → reconnect / token refresh still works (Redis OAuth state + encrypted tokens)

## Manual — captures

- [ ] Browser extension: set staging API base + Bearer access token → approve a screenshot → appears in org-scoped capture list
- [ ] (Optional) Desktop agent activity session → Skill File draft → expert approve
- [ ] `/download` serves Loom Capture (zip present after packaging + frontend rebuild)

## Manual — durability and hardening

- [ ] With `BLOB_STORAGE_BACKEND=s3` (or shared `blobdata` volume): upload a capture/doc, replace the API container, content still readable
- [ ] `APP_ENV=production`: `/docs` and `/openapi.json` absent; `connect-dev` blocked; `/graph/debug` → 404
- [ ] CORS rejects a foreign browser origin
- [ ] Production web build does **not** call `http://localhost:8000` (use `/api` or `https://…` bake)
- [ ] Members can connect Google / Microsoft / Zoom and upload files; knowledge-review stays admin-only

---

## Notes

- “Invite flow” is not a first-class product path yet — same-domain second user is the stand-in.
- Extension still defaults to localhost in its own config; point it at staging manually for this checklist.
- Package the desktop agent with `LOOM_WEB_BASE` set to the staging web origin so `/download` copies sign in against staging.
- Wire `scripts/staging_smoke.sh` into CI later; this slice is checklist + curl only.
