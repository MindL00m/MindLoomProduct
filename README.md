# Loom

Loom builds a searchable company knowledge base from organization directories,
documents, conversations, connected workspace apps, and user-approved browser
captures.

## Repository layout

```text
apps/
├── web/                 # Main React + TypeScript user interface
├── api/                 # Main Python API and all server-side processing
└── browser-extension/   # Chrome extension for approved work captures
docker-compose.yml       # Complete local stack
test_api.py              # Small manual API smoke test
```

There is intentionally one frontend and one backend:

- `apps/web` owns everything a user sees.
- `apps/api` owns authentication, organizations, ingestion, AI search, connected
  apps, databases, and screenshot capture processing.
- `apps/browser-extension` is a client of the main API; it is not a separate
  backend.

## How data flows

```text
React app / browser extension / connected apps
                       |
                       v
                Loom Python API
                       |
                    Redis queue
                       |
                 ingestion worker
                 /             \
                v               v
     PostgreSQL + pgvector     Neo4j
     jobs, text and search     relationships
```

Browser screenshots are captured locally, shown for approval, and uploaded only
after approval. The API saves them under its configured private capture storage
and creates vision summaries in the background.

Manual document ingestion supports PDF, Word (`.docx`), PowerPoint (`.pptx`),
Excel (`.xlsx`), CSV, text, Markdown, JSONL, and operational logs. Connector and
manual documents use the same provenance model for ownership, dates, location,
version, contributors, permissions, and source links.

## Run the full stack

1. Copy the API environment template:

   ```bash
   cp apps/api/.env.example .env
   ```

2. Set `OPENAI_API_KEY` and `NEO4J_PASSWORD` in `.env`. For live workspace
   connections, also configure the Google OAuth/webhook values and/or the
   Microsoft Entra OAuth/webhook values documented in `apps/api/.env.example`.

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

Tests:

```bash
cd apps/api
pytest
```

## Naming and generated data

The product and all user-facing text use the name **Loom**. Runtime screenshots,
summaries, uploaded blobs, build output, credentials, and local dependencies are
ignored by Git. Only anonymized fixtures should be committed.
