# MindLoom

MindLoom is an AI organisation brain that learns how your company really works,
and puts it to work privately. It learns from organization directories,
documents, conversations, connected workspace apps, desktop activity summaries,
and user-approved browser captures, then answers questions, routes work to
experts, and reports status.

People reach the brain through **LoomBot**. Four agents keep it running:

| Agent | Job | Backend package | Web app |
|---|---|---|---|
| LoomBot | Ask: cited answers, workspaces, expert routing | `apps/api/loombot/` | LoomBot → Ask, Workspaces, Expert Messages |
| Weaver | Capture: connectors, uploads, and MindLoom Capture | `apps/api/weaver/` | Weaver → Import, MindLoom Capture |
| Keeper | Keep true: access, reviews, expert answers, expiry | `apps/api/keeper/` | Home → Dashboard (directory), review queue |
| Lens | Understand: status board and analysis | `apps/api/lens/` | Home |
| Shuttle | Act: notifications, pull requests, workflow runs | `apps/api/shuttle/` | Shuttle |

All of them read from and write to the MindLoom Brain (`apps/api/brain/`).
Shared infrastructure lives in `apps/api/core/`.

## Repository layout

```text
apps/ 
├── web/                 # Main React + TypeScript user interface
├── api/                 # Main Python API, grouped by agent (see apps/api/README.md)
├── desktop-agent/       # MindLoom Capture for Mac: Accessibility capture agent (menu bar)
└── browser-extension/   # MindLoom Capture for Chrome: approved work captures
docs/                    # Product, security, and competitive documents (Typst)
scripts/                 # Staging smoke test
docker-compose.yml       # Complete local stack
test_api.py              # Small manual API smoke test
```

There is intentionally one frontend and one backend:

- `apps/web` owns everything a user sees.
- `apps/api` owns authentication, organizations, ingestion, AI search, connected
  apps, embeddings, screenshot capture, and desktop activity session processing.
- `apps/desktop-agent` is a macOS client that uploads on-device task summaries only.
- `apps/browser-extension` is a client of the main API; it is not a separate
  backend.

## How data flows

```text
React app / desktop agent / browser extension / connected apps
                       |
                       v
               MindLoom Python API
                       |
                    Redis queue
                       |
                 ingestion worker
                 /             \
                v               v
     PostgreSQL + pgvector     Neo4j
     jobs, text and search     relationships
```

The macOS desktop agent captures Accessibility interaction events from an
explicit app allowlist, aggregates them on-device into task summaries, and
uploads only those summaries (`POST /captures/activity-sessions`). Skill Files
are drafted from aggregates (no pixels). See [`apps/desktop-agent/README.md`](apps/desktop-agent/README.md).

Browser screenshots are still supported: captured locally, shown for approval,
and uploaded only after approval. The API stores image bytes in blob storage
(local volume by default; set `BLOB_STORAGE_BACKEND=s3` for multi-replica) and
capture metadata in Postgres, then creates vision summaries in the background.

Manual document ingestion supports PDF, Word (`.docx`), PowerPoint (`.pptx`),
Excel (`.xlsx`), CSV, text, Markdown, JSONL, and operational logs. Connector and
manual documents use the same provenance model for ownership, dates, location,
version, contributors, permissions, and source links.

## Run the full stack

1. Copy the API environment template:

   ```bash
   cp apps/api/.env.example .env
   ```

2. Set `OPENAI_API_KEY` and `NEO4J_PASSWORD` in `.env`. For Google sign-in and
   workspace connections, set `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` (and
   authorize your web origin for GIS). Also set `SESSION_SECRET` for production.
   Local docker defaults: `APP_ENV=development`, `ALLOW_DEV_INTEGRATIONS=true`.
   Production also requires strong webhook secrets, `TOKEN_ENCRYPTION_KEY`,
   `GOOGLE_PUBSUB_PUSH_AUDIENCE`, and a non-default `MICROSOFT_GRAPH_CLIENT_STATE`.
   CORS is limited to `FRONTEND_URL` plus `CORS_ALLOWED_ORIGINS`. Set
   `FRONTEND_URL` to the public web origin and register OAuth redirect URIs on
   the public API host. For the Compose frontend, bake `VITE_API_BASE` (default
   `/api` via nginx proxy) and `VITE_GOOGLE_CLIENT_ID` at image build — rebuild
   to change. See [`apps/web/README.md`](apps/web/README.md) for the HTTPS
   deploy checklist, and [`docs/STAGING_SMOKE.md`](docs/STAGING_SMOKE.md) before
   a beta cut.

3. Start everything:

   ```bash
   docker compose up --build
   ```

4. Open:

   - Web app: <http://localhost:5500>
   - API docs: <http://localhost:8000/docs>
   - Neo4j browser: <http://localhost:7474>

## Run applications directly

Web:

```bash
cd apps/web
cp .env.example .env.local   # set VITE_GOOGLE_CLIENT_ID to match GOOGLE_CLIENT_ID
npm install
npm run dev
```

API:

```bash
cd apps/api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn main:app --reload

# In a second terminal:
python -m worker
```

Desktop capture agent (macOS):

```bash
cd apps/desktop-agent
swift run MindLoomAgent
```

See [`apps/desktop-agent/README.md`](apps/desktop-agent/README.md) for Accessibility
permission, allowlist setup, the Skill File smoke path, and publishing a website
download (`/download` after `./scripts/package-app.sh` + frontend rebuild).

Tests:

```bash
cd apps/api
pytest
```

## Naming and generated data

The product and all user-facing text use the name **MindLoom** (MindLoom
Capture for the capture apps). Runtime screenshots,
summaries, uploaded blobs, build output, credentials, and local dependencies are
ignored by Git. Only anonymized fixtures should be committed.
