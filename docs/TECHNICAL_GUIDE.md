# MindLoom technical guide

This guide covers architecture, data flow and the technical decisions behind each feature, as of `main` @ `129932a` (28 Oct 2026). File references are relative to `apps/`. For product behaviour, see [USER_GUIDE.md](USER_GUIDE.md). For gaps, see [FEATURE_STATUS.md](FEATURE_STATUS.md).

---

## 1. System overview

```text
React app (apps/web) ─┐
Mac capture agent ────┤
Chrome extension ─────┼──► FastAPI (apps/api, main.py) ──► Redis queue ──► worker.py
Connected apps ───────┘        │   (webhooks, OAuth)                            │
                               ▼                                                ▼
                     PostgreSQL + pgvector  ◄──────── ingestion pipeline ────► Neo4j
                     (rows, chunks, vectors,                                 (entities, people,
                      jobs, files, workflows)                                 provenance graph)
                               │
                               └── Blob storage (local volume or S3) for files and documents
```

**Decision: one frontend, one backend.** The capture clients and the browser extension are clients of the API, not separate servers. The backend is grouped by agent (`brain/`, `loombot/`, `weaver/`, `keeper/`, `lens/`, `shuttle/`, `core/`, `api/routes/`). `main.py` and `worker.py` stay at the top level so the deploy commands (`uvicorn main:app`, `python -m worker`) never change.

**Decision: two stores.**
- **pgvector** holds text, embeddings, jobs and application state.
- **Neo4j** holds relationships and provenance (who said what, which chunk supports which decision).

Retrieval also reads chunk payloads from Neo4j, so Ask still works if Postgres and Neo4j drift apart (`brain/retrieval.py:246`).

### 1.1 Stack

| Layer | Technology |
|---|---|
| API | Python 3.11, FastAPI, SQLAlchemy async (pool 10, pre-ping, recycle 1800 s), slowapi |
| Worker | asyncio, Redis `BRPOPLPUSH` |
| Data | PostgreSQL 16 + pgvector (ivfflat, cosine, `lists=100`), Neo4j 5.26 Community, Redis 7 (AOF) |
| LLM | OpenAI SDK (chat, vision, Whisper, embeddings `text-embedding-3-small` at 1536 dimensions), Tavily |
| Files | python-docx, openpyxl, PyMuPDF, Pillow, stdlib `zipfile`/`ElementTree`/`csv`/`email` |
| Frontend | React 18, react-router 6, zustand 5 (persisted), Tailwind 3, framer-motion, lucide, shadcn-style UI |
| Capture | Swift 5.9, macOS 13+ Accessibility APIs; Chrome Manifest V3 extension |
| Deploy | Docker Compose (local); Railway (api, worker and web services) |

---

## 2. Request lifecycle and platform concerns

### 2.1 App setup (`api/main.py`)
- On startup, `lifespan` calls `validate_production_secrets()` and then `ensure_schema()`, which runs the DDL on every boot.
- `/docs`, `/redoc` and `/openapi.json` are disabled in production.
- `LLMProviderError` becomes a 503.
- CORS allows `FRONTEND_URL` plus `CORS_ALLOWED_ORIGINS`, never `*`, with `allow_credentials=False`. Allowed headers are `Authorization`, `Content-Type` and `X-Requested-With`.

### 2.2 Rate limits (`core/rate_limit.py`)
- slowapi backed by Redis. The key is `user:{org}:{user}` from the JWT, falling back to the client IP.
- **Only decorated routes are limited** (`default_limits=[]`):
  - auth: 20/min
  - `/query`: 30/min
  - captures: 30/min
  - ingest and `/files/extract`: 20/min
  - GitHub inspect and connect
- In production, startup fails if Redis is unreachable. In development it falls back to in-memory limits.
- `_rebind_globals` works around a slowapi bug: with `from __future__ import annotations`, it breaks FastAPI body resolution.

### 2.3 Health (`core/health.py`)
- `/health` is a static liveness check.
- `/ready` pings Postgres, Redis and Neo4j, and checks that the worker heartbeat (`loom:worker:heartbeat`, TTL 60 s) is less than 90 s old.
- Railway healthchecks `/health`.

### 2.4 Configuration (`core/config.py`)
Settings come from pydantic-settings, read from `.env` (case-insensitive; unknown keys are ignored).

| Group | Variables |
|---|---|
| Required | `OPENAI_API_KEY`, `POSTGRES_URL`, `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` |
| Runtime | `REDIS_URL`, `APP_ENV`, `SESSION_SECRET`, `SESSION_TTL_HOURS` (168), `ALLOW_DEV_INTEGRATIONS` |
| Ingestion | `CHUNK_GAP_MINUTES` (30), `CHUNK_MAX_TOKENS` (800), `SPEAKER_SIMILARITY_THRESHOLD` (85), `RETRIEVAL_SIMILARITY_THRESHOLD` (0.3, tuned for text-embedding-3-small), `RETRIEVAL_CHUNK_LIMIT` (5) |
| Models | `OPENAI_DEFAULT_MODEL` (gpt-6-luna), `MODEL_ROUTING_ENABLED`, `MODEL_ROUTER_MODEL`, `MODEL_{SIMPLE,MODERATE,COMPLEX}` (and `_REASONING`), `CAPTURE_VISION_MODEL` (gpt-4o-mini), `LOCAL_LLM_API_KEY`, `LOCAL_LLM_TIMEOUT_SECONDS` |
| Storage | `BLOB_STORAGE_BACKEND` (local or s3), `BLOB_STORAGE_ROOT`, `S3_*` |
| Connectors | `GOOGLE_*`, `MICROSOFT_*`, `ZOOM_*`, `GOOGLE_PUBSUB_*`, `GOOGLE_DRIVE_WEBHOOK_*`, `PUBLIC_API_URL`, `FRONTEND_URL` |
| Security | `TOKEN_ENCRYPTION_KEY` (Fernet), `CORS_ALLOWED_ORIGINS`, `RATE_LIMIT_*` |
| Features | `TAVILY_API_KEY`, `WEB_SEARCH_ENABLED`, `EMAIL_ATTACHMENT_OCR` |

**Decision: fail closed in production.** `validate_production_secrets` refuses to start in production when:
- `SESSION_SECRET` or the Neo4j password is weak,
- the Drive webhook secret is missing,
- the Graph `clientState` is the default value,
- the Zoom secret is missing,
- the Pub/Sub audience is missing,
- `TOKEN_ENCRYPTION_KEY` is missing, or
- the S3 backend is selected without a bucket.

These checks apply even to connectors you don't use.

---

## 3. Identity, tenancy and access control

### 3.1 Sign-in
- The browser uses Google Identity Services. The client id is served at runtime by `/auth/public-config`, with `VITE_GOOGLE_CLIENT_ID` as a fallback.
- `keeper/session_tokens.verify_google_id_token` checks the audience, requires `email_verified`, and lower-cases the email.
- **No passwords are stored.**

### 3.2 Sessions
- Sessions are HS256 JWTs with claims `sub`, `org_id`, `role`, `email`, `iat` and `exp`. They last `SESSION_TTL_HOURS`.
- There is no refresh token. `GET /auth/me` re-issues one.
- The frontend stores the session in localStorage under `loom-session`.
- **Decision: the JWT plus a database check on every request.** `require_user_context` re-checks that the user and the org still exist. `require_admin_context` reads the role from the database, never from the JWT.

### 3.3 Tenancy
- Tenancy is **domain-scoped**. `create_org` requires the admin's email domain to match the org domain, and allows one org per domain. `google_signin` maps the email domain to its org and upserts the user as a member.
- `users.email` is globally unique, so a person belongs to exactly one org.
- There is no invite flow.

### 3.4 Search ACL (`keeper/visibility_acl.py`, `keeper/auth.get_user_access_tokens`)
- Every chunk carries `visible_to TEXT[]`.
- A user's access tokens are:
  - `org:{id}`
  - `user:{id}`
  - their email
  - `domain:{d}`
  - `department:{dept}` (from their Neo4j `Person` node)
  - the email of every account they've connected
- The Postgres filter is `cardinality(visible_to)=0 OR visible_to && :tokens`. Neo4j queries use an equivalent case-insensitive filter. **An empty `visible_to` means the whole org can see the chunk.**
- **Decision: uploads are private by default.** `permissions_for_upload` checks explicit visibility first, then explicit permissions, and otherwise uses `user:{id}`.
- Connector visibility (`weaver/connection_setup.visibility_for_policy`):

| Mode | `visible_to` | Note |
|---|---|---|
| `respect_source_permissions` (default) | the connected account's email | Deliberately restrictive until per-source ACL import exists |
| `organization` | `org:{id}` | |
| `selected` | the chosen `user:` and `department:` tokens | |

### 3.5 Secret handling
- OAuth tokens, GitHub PATs and the Slack bot token and signing secret are Fernet-encrypted (`enc:v1:` prefix, `core/token_crypto.py`).
- Without a key, values are stored in plain text and a warning is logged.
- OAuth `state` is kept in Redis (`loom:oauth_state:*`, TTL 600 s).

---

## 4. Ingestion pipeline (Brain)

### 4.1 Durable jobs (`core/durable_jobs.py`, `worker.py`)
- **Enqueue:** the job is upserted into `ingestion_jobs` (queued), then `RPUSH`ed as JSON onto `loom:jobs`. File bytes travel base64-encoded in the payload.
- **Job kinds:** `conversation`, `pdf`, `document`, `whatsapp_export`, `zoom_sync`, `google_sync`, `microsoft_sync`, `initialize_connection`, `expert_notification`, `slack_backfill`, `expert_thread_ingest`.
- **Worker loop:**
  1. `BRPOPLPUSH` into `loom:jobs:processing` (30 s timeout).
  2. Write the heartbeat.
  3. `execute` the job.
  4. `LREM` it from the processing list.
- **Startup recovery:** the worker moves the processing list back to the queue. This assumes a **single worker**.
- **Failure handling:** a failed job is marked `failed` with the error text. **There is no retry.**
- **Background loops** run in the worker alongside the queue:
  - connection checks every 300 s (watch renewal, reconciliation, expiry reviews)
  - Slack thread flush every 60 s
  - the recurring-email scheduler every 60 s
- **Decision:** API and worker are separate processes so they scale independently. DDL is serialised by `pg_advisory_xact_lock(hashtext('mindloom_schema'))` in `ensure_schema`, so the two Railway services can't race.

### 4.2 Pipeline (`brain/pipeline.py`)
`validate → store Document (blob, content hash) → dedupe → normalise speakers → chunk → per chunk, concurrently: extract → embed → save to Postgres → MERGE into Neo4j → DERIVED_FROM link → record claims and detect conflicts → react (status trackers, monitored files)`

| Stage | Implementation | Decision / note |
|---|---|---|
| Validate | `brain/validator.py` | Hard-fails missing participants, messages or timestamps. Silently drops blank messages and sorts by time. |
| Dedupe | SHA-256 content hash; Document `MERGE (org_id, content_hash)`; blob key `{org}/{hash[:2]}/{hash}` | Idempotent re-ingest. Caveat: a document that already has chunks is skipped, so a partial failure is never repaired. |
| Normalise | thefuzz `max(token_sort, token_set) ≥ 85` | Merges "Priya S" and "Priya Sharma". Every merge is logged. |
| Chunk | `brain/chunker.py`: a time gap over 30 min, a new capitalised entity not seen in the last 5 messages, or 800 cl100k tokens | Splits only at message boundaries. `/ingest/document` sends non-tabular files as one message, which produces one large chunk (known risk). |
| PDF chunk | `brain/pdf_chunker.py` (PyMuPDF fonts): a heading is ≥1.15× body size or bold, ≤14 words. Section chunks, otherwise sentence packing with 2-sentence overlap. | Records page and character locators for citations. |
| Extract | `brain/extractor.py`, JSON mode, temperature 0, default model | Produces entities, typed entities, knowledge type, ownership signals, decisions, action items, claims, `valid_until`, and lifecycle updates. One retry with the error fed back, then a "noise" fallback. Routine email counts as noise. |
| Embed | `text-embedding-3-small` at 1536 dimensions on `summary + raw_text` | **Pinned to OpenAI even when chat runs on a local model**, so vector dimensions and rankings stay stable. |
| Store | `brain/storage.py`: Postgres upsert; Neo4j writes all via `MERGE` | Lifecycle nodes are created only from explicit `*_updates` fields. |
| External versions | `brain/source_registry.py`, keyed on `(org, provider, external_id)` | Skips unchanged content. On change it re-ingests and retires the old version. |

### 4.3 Graph schema (Neo4j)
- **Labels:** Person, Entity, Chunk, Question, Decision, Claim, ActionItem, OpenIssue, Document.
- **Relationships:**
  - `MENTIONED_IN`, `RELATES_TO`
  - `ASKED`, `ANSWERED`, `OWNS`
  - `SUPPORTED_BY`, `EVIDENCED_BY`
  - `PART_OF`, `ASSIGNED_TO`, `ABOUT`
  - `DERIVED_FROM` (Chunk → Document, with page and character locators)
  - `REPORTS_TO`
- **Constraints** live in `db/neo4j_constraints.cypher`. They are composite UNIQUE constraints, because Community Edition has no NODE KEY. **Only the Compose `neo4j-init` service applies them.**

### 4.4 Postgres schema
- The base schema is `db/init.sql`. `core/schema.py` adds idempotent DDL at startup.
- `db/migrations/*.sql` mirror that DDL for documentation; no code runs them.

| Area | Tables |
|---|---|
| Tenancy | `organizations`, `users`, `organization_llm_settings` |
| Connectors | `app_connections`, `connection_policies`, `sync_cursors`, `integration_subscriptions`, `sync_runs`, `slack_installations` |
| Knowledge | `external_sources`, `conversations`, `chunks`, `chunk_embeddings`, `ingestion_jobs`, `knowledge_claims` |
| Keeper | `knowledge_reviews`, `knowledge_review_schedules`, `expert_messages`, `notification_deliveries` |
| Workspaces | `workspaces`, `workspace_members`, `workspace_messages` |
| Files | `loombot_files`, `loombot_file_seen`, `loombot_file_events` |
| Lens | `status_trackers`, `status_tracker_updates`, `status_tracker_seen` |
| Shuttle | `recurring_emails`, `recurring_email_runs` |
| Capture | `captures`, `capture_summaries`, `activity_sessions`, `skill_files`, `skill_runs` |

---

## 5. Retrieval and answering

### 5.1 Retrieval (`brain/retrieval.retrieve`)
1. **Condense the question.** A follow-up is rewritten into a standalone query using the last 6 turns. This step never raises.
2. **Search in parallel:**
   - a pgvector search (limit × 3, similarity > 0.3), and
   - entity extraction (LLM plus heuristics), which feeds expert search, entity-linked chunks, lexical search in Postgres and Neo4j, and the directory.
3. **Score:** `0.78·similarity + 0.12·freshness + 0.10·authority + boost`, where:

| Term | Definition |
|---|---|
| Freshness | `exp(-age / 2 years)` |
| Authority | knowledge-type weight × confidence weight. Type: decision 1.0, Q&A 0.9, problem 0.75, status 0.6, other 0.15. Confidence: high 1.0, medium 0.7, low 0.4. |
| Boost | +0.08 for entity-linked or lexical hits; +0.05 for chunks loaded by id; directory people are fixed at 0.99 |

4. **Graph rerank:** add `+0.10 × (matching related entities ÷ question entities)`, de-duplicate, and keep the top 5.

### 5.2 Answer generation (`loombot/answerer.py`)
- The prompt requires the model to answer only from the context and to cite `[SOURCE: id]` (`[EPHEMERAL: id]` for chat-only files). It should prefer newer and more authoritative evidence, **state remaining conflicts and cite both sides**, and flag old evidence.
- **Source authority:**

| Weight | Sources |
|---|---|
| 1.0 | directory, SharePoint |
| 0.98 | skill_file |
| 0.95 | Drive |
| 0.9 | PDF, Word |
| 0.85 | spreadsheet |
| 0.8 | email attachment, presentation |
| 0.75 | default |
| 0.7 | Teams |
| 0.65 | Gmail |

- **Confidence:**
  - **Low, and routed to an expert:** a refusal phrase appears, the top score is below 0.45, or mean authority is below 0.35.
  - **Medium:** the top score is below 0.70, or there is only one source.
  - **High:** otherwise.
- Only sources that are actually cited are returned.

### 5.3 Ask agent (`loombot/ask_agent.py`)
- **Intent detection runs first.** Regex detectors (`_wants_messaging`, `_wants_files`, `_wants_meeting`, `_wants_recurring`, `_wants_web`, …) decide quickly. If none match, a JSON planner call (300 tokens) decides `use_tools` and a plan of up to 6 steps. Without tools, the turn is plain RAG.
- **Tool loop:** a base RAG answer first, then up to **8 rounds** of OpenAI tool calling:
  - round 0 uses `tool_choice="required"`
  - the loop exits early once the intended draft exists
  - a final call with `tool_choice="none"` writes the summary.
- **Tool classes:**

| Class | Tools |
|---|---|
| Propose only (card + click) | `propose_email`, `propose_expert_message`, `propose_meeting`, `propose_recurring_email`, `propose_workspace`, `propose_github_pr` |
| Read | `lookup_person`, `search_emails`, `count_emails`, `read_email_attachment`, `knowledge_inventory`, `project_status`, `list_files`, `read_file`, `find_meeting_times`, `github_*`, `web_search` |
| Execute immediately (undoable or low-risk) | `write_csv_file`, `write_document`, `create_updated_document`, `save_email_attachment`, `set_file_monitoring`, `track_status`, `add_workspace_members` |

- **Decision: propose-then-confirm for anything outward-facing.** Tool results say "User must approve in the UI", and the prompts forbid claiming that something was sent.
- **Decision: prompt-injection hygiene.** Email, web content, files, context excerpts and other people's workspace messages are labelled as untrusted data in every prompt.
- **Local models without tool support** skip tools. Messaging falls back to a heuristic draft.

### 5.4 Model mixing (`core/model_router.py`, `core/llm_provider.py`)
- **Router:** `MODEL_ROUTER_MODEL` (effort none, temperature 0, 60 tokens, JSON) rates each request simple, moderate or complex from the request, the last 4 turns, workspace flags and the attachment count. **When unsure, it picks the lower tier.** Small talk skips the router.
- **Tiers:**

| Tier | Model | Reasoning effort |
|---|---|---|
| simple | gpt-6-luna | none |
| moderate | gpt-6-luna | low |
| complex | gpt-6-sol | medium |

- **Scope:** routing applies only when the org uses OpenAI with the default model. A pinned or local model is respected.
- The routed model is applied through a contextvar (`use_model_for_turn`), so every LLM call in the turn uses it.
- **Adapter resilience (`_CompletionsAdapter`):**
  - an unknown model is replaced by the fallback and remembered,
  - rejected parameters (reasoning_effort, temperature, top_p, max_tokens→max_completion_tokens, tool_choice, response_format, …) are dropped, remembered per model and retried,
  - messages are trimmed to the context window.
- **Constraint:** requests carrying tools are always sent with reasoning effort `none`, because Chat Completions rejects tools combined with other efforts on GPT-6. The complex tier's effort therefore applies only to tool-less calls.
- **Providers:** `openai` or `local` (any OpenAI-compatible `/v1`). Background jobs (extraction, CONTEXT.md, the conflict judge) always use the default OpenAI client.

### 5.5 Web search (`loombot/web_search.py`)
1. **Decide** (`_DECIDE_SYSTEM`): an LLM decides whether the question is about public information and writes a query of at most 12 words with internal names removed.
2. **Search:** Tavily basic search (5 results), plus a companion "contact email support" search (3 results). Results are merged to at most 8, snippets are capped at 1200 characters, and the timeout is 20 s.
3. **Answer** (`_ANSWER_SYSTEM`): answer only from the results, citing `[WEB: n]`, or return `NO_ANSWER`.
4. **Contacts:** extracted, then verified word for word against the result text, so no email address or phone number is invented.
5. **Fallback:** runs when the internal answer has low confidence, no actions were taken, and the room isn't context-only.

---

## 6. Keeper: keeping knowledge true

### 6.1 Reviews
- Table: `knowledge_reviews`.
- **Types:** `expert_request`, `conflict`, `verification`, `proposal`.
- **Statuses:** open, drafted, answered, resolved, approved, rejected, removed.

### 6.2 Expert flow
1. `create_expert_request` matches the expert by email, then name. It reuses an existing thread for the same pair, opens a review due in 7 days, and enqueues an `expert_notification`.
2. The expert's answer becomes a proposed Skill File. Media answers are processed first: images by vision, audio and video by `whisper-1`.
3. When approved, the answer is ingested with org visibility.
4. Admins can moderate: edit (publishes a new version) or remove (retires the source).

### 6.3 Conflict detection
- **Cheap first stage:** subject-word Jaccard ≥ 0.34, plus differing numbers or differing negation.
- **Second stage:** up to 3 router-model verdicts per claim: contradiction, update or compatible.
- Only a contradiction opens a review. **Decision:** this two-stage design avoids false conflicts such as "receipt total vs receipt number".

### 6.4 Expiry
- `schedule_expiry_reviews` runs every 5 minutes in the worker.
- It creates verification reviews from `knowledge_review_schedules` (default interval 180 days), and for any external source not updated in more than 180 days.

### 6.5 Notifications (`shuttle/notification_delivery.py`)
- Expert requests fan out to Gmail, Outlook `sendMail` and a Teams 1:1 chat. Each attempt is recorded in `notification_deliveries`.
- The sending mailbox is the most recently updated connection in the org. This is a known trade-off.

---

## 7. Lens: status

### 7.1 Status board (`lens/status_board.py`)
- Three ACL-filtered Cypher reads: open projects (with their latest 3 chunks and 3 open issues), open issues, and open action items.
- **Decision: summaries only.** Only `status_update` and `problem_report` chunks count as evidence. Raw text is stripped. Each card keeps at most 3 updates. Cards with no qualifying evidence are dropped.

### 7.2 Status trackers (`lens/status_trackers.py`)
- Trackers are per user and **driven by ingestion**.
- **Matching:** each new chunk visible to the tracker's owner is pre-filtered by cosine similarity ≥ 0.30 or a keyword hit. At most 4 candidates go to an LLM judge.
- Updates are written to `status_tracker_updates`. Nothing sends a notification.
- **Decision:** the cheap pre-filter keeps LLM cost proportional to relevant content.

---

## 8. LoomBot: workspaces and files

### 8.1 Workspaces (`loombot/workspaces.py`)
- **Kinds:**
  - `org_wide`: one per org (unique partial index), with members synced from `users`
  - `group`
- **`loombot_mode`:**
  - `org_knowledge`
  - `context_only`: answers only from `CONTEXT.md` (up to 8000 characters), synthesised by RAG plus an LLM, with manual Resync
- **@Loombot replies:**
  - The reply runs **synchronously inside the POST request**, using `run_ask_agent` with the asker's tokens and mailbox.
  - The last 12 messages are used: only the asker's own turns count as chat, and other members' messages are context only.
  - The stored payload is redacted for other members by `_shared_payload`. They see summaries such as "a message draft" and outcomes such as "email sent", never sources or drafts.
  - Only `requested_by` can change a card's state.

### 8.2 Files (`loombot/files.py`)
- **Storage:**
  - Text kinds (csv, txt) are stored in `content`.
  - Binary kinds (docx, xlsx, pdf) are stored in the blob store at `loombot-files/{org}/{file}/{uuid}.{kind}`.
- **Limits:** 1 MB for text, 15 MB for binary.
- **Validation:** the OOXML zip structure for docx and xlsx, the `%PDF` header for PDF.
- **Undo:** `previous_content` / `previous_blob_key`. Undo is a swap, so undoing twice is a redo. Writing a new version deletes the blob from two versions back.
- **Owner-private:** every query filters on `owner_user_id`.
- **Sharing:**
  - "Share in MindLoom" copies the file to the recipient, with share metadata. Later edits don't sync.
  - "Email" sends it as a Gmail attachment.
- **Open in Excel:** a 10-minute HS256 signed public URL, opened through the `ms-excel:ofe|u|` scheme. It's one-way, with no write-back.
- **DOCX editing:** documents round-trip through Markdown (`docx_builder.py`). This is simple but loses formatting.
- **Previews:** XLSX via openpyxl `data_only` (500 × 50, read-only); DOCX blocks; PDF in an iframe.
- **Agent guards:** the agent must call `read_file` before overwriting a file, and can only edit files of 40,000 characters or fewer.

### 8.3 Update from memory (`loombot/document_update.py`)
1. Segment the document:
   - docx: paragraphs and table cells
   - xlsx: cells (formulas are read-only)
   - pdf: text blocks
   - csv: cells
   - txt: paragraphs
2. An LLM plans up to 8 search queries.
3. Permission-filtered retrieval returns up to 20 chunks.
4. An LLM proposes edits, each of which **must cite evidence or the user's instruction**. Uncited edits are dropped.
5. The edits are applied in the original format. In DOCX, runs are edited in place, keeping the first run's formatting.
6. The result is saved as **a new file**. The original is never modified.

Limitations:
- XLSX and PDF can't take inserts.
- PDF edits redact the old text and overlay Helvetica.
- Charts and images in XLSX are dropped.

### 8.4 Self-updating files (`loombot/file_monitor.py`)
- **Trigger:** every ingestion ends with `_react_to_new_knowledge`, which calls `process_new_knowledge`.
- **Matching:** keywords or embedding similarity against the file's instructions, restricted to chunks the owner can see. Already-processed chunks are skipped using `loombot_file_seen`.
- **LLM step:** `_MONITOR_SYSTEM` returns `append_rows` / `update_cells` and may never delete. The last 80 rows are shown to the model.
- **Apply:** under `SELECT … FOR UPDATE`, saving `previous_content` for undo and writing a `loombot_file_events` row.
- **CSV only today.**

### 8.5 Readable formats (four paths)
| Path | Formats |
|---|---|
| Files upload | csv, txt, docx, xlsx, pdf |
| `/ingest/document` | pdf, docx, pptx, xlsx, csv, txt, md, log, jsonl |
| `/files/extract` (chat only) | pdf, json/jsonl, csv, docx, pptx, xlsx; anything else is read as UTF-8 text |
| Email attachments | the above, plus ics, vcf, xml, html, eml, and images via vision OCR (over 30 KB, not inline). Scanned PDFs are OCR'd for the first 5 pages when a page averages under 40 characters. |

Legacy .doc, .xls and .ppt, ZIP, and images uploaded directly are not supported.

---

## 9. Weaver: connectors and capture

### 9.1 OAuth scopes
| Provider | Scopes |
|---|---|
| Google | openid, email, profile, `gmail.readonly`, `gmail.send`, `calendar.readonly`, `calendar.events`, `drive.readonly`, `drive.metadata.readonly` (`include_granted_scopes`) |
| Microsoft | `offline_access User.Read Team.ReadBasic.All Channel.ReadBasic.All ChannelMessage.Read.All TeamMember.Read.All ChannelMember.Read.All Chat.Read Chat.Create ChatMessage.Send Mail.Read Mail.Send Calendars.Read Sites.Read.All Files.Read.All`. Several need tenant admin consent. With only `Calendars.Read`, Outlook meetings can't be booked. |
| Zoom | Configured in the Marketplace app. The code stores a label only. |
| Slack | `app_mentions:read chat:write channels:history groups:history im:history channels:read groups:read users:read users:read.email` |
| GitHub | A fine-grained PAT, with MindLoom's own repo allowlist and capability ACL (metadata, contents, pull_requests) on top |

### 9.2 Sync mechanisms
| Source | Mechanism |
|---|---|
| Gmail | Pub/Sub watch with OIDC-verified push. Without it, history is polled every 300 s for real-time policies. |
| Drive | `changes.watch` channel with a secret token |
| Google Calendar | `events.list` with `syncToken`, stored in `sync_cursors`; 410 triggers a full resync. **No push**: it runs at reconciliation (every 6 h for real-time policies). |
| Outlook mail and calendar | Graph delta links; no subscription |
| Teams channels and SharePoint | Graph subscriptions with `clientState`. Renewal: Teams within 15 min of expiry, SharePoint within 12 h. |
| Teams chats | Delta, plus reconciliation |
| Zoom | `recording.completed` and `transcript_completed` webhooks, plus list syncs |
| Slack | Signed events (300 s maximum age, 1 h de-duplication); threads marked dirty in Redis and flushed every 60 s; visible to channel members |

- **Decision: webhooks for latency, plus a reconciliation safety net.** Reconciliation intervals: real-time 6 h, hourly 1 h, daily 24 h.
- **Outbound HTTP** uses `request_with_backoff`: retries 408, 429 and 5xx up to 6 times, honouring `Retry-After` up to 120 s.

### 9.3 Calendar scheduling (`shuttle/calendar_meetings.py`)
- **Time zone:** the browser's IANA zone, falling back to UTC.
- **Free/busy:** Google `freeBusy` on `primary` plus the attendees. Unreadable calendars are reported as `unknown_availability`.
- **Slot generation:**
  - working hours 09:00–17:00
  - 30-minute step
  - at most 2 slots per day
  - weekdays only by default
  - a 7-day window, at most 21
- **Booking:** a POST to `calendars/primary/events` with `sendUpdates=all` and a Meet link through `conferenceData`.
- **Guards:** end after start, at most 12 h, not in the past, at most 50 attendees.
- **Scope check:** the `calendar.events` scope is detected from the stored scopes, without calling Google. If it's missing, the user is told to reconnect.
- **Known issue:** synced Google events use `external_id = primary:<eventId>`, so attendees overwrite each other's copy.

### 9.4 Recurring emails (`shuttle/recurring_emails.py`)
- **Polling scheduler:** every 60 s, `SELECT … FOR UPDATE SKIP LOCKED LIMIT 20` claims due rows, advances `next_run_at` in the same transaction, and sends after commit.
- **Decision: at-most-once, with no burst after downtime.** `next_after` skips missed slots, and Resume never sends a backlog.
- **Fixed intervals** of 15 min to 365 days. The time zone is stored but not used.
- **Auto-pause** after 3 consecutive failures.
- **Optional refresh:** before each send the body is regenerated from the owner's permission-filtered retrieval (top 12 chunks) at temperature 0.2.

### 9.5 Capture
- **Chrome extension (MV3):**
  - Screenshots are taken on an interval or on click.
  - **The user approves each one before upload.**
  - `POST /captures`, then a background summary in the API process, then session analysis drafts a Skill File.
  - Auth is a pasted JWT.
- **Mac agent:**
  - Captures Accessibility events from allowlisted apps only. **It never reads text-field values.**
  - Tasks are aggregated on the device, and `POST /captures/activity-sessions` drafts a Skill File.
  - Sign-in goes through a `/desktop-auth` handoff to a localhost port.
- **Skill Runs (HEAD):**
  - The API only records state. The Mac app or extension performs the steps and reports each one.
  - A run waits on `needs_value` rather than typing values.

---

## 10. Frontend

### 10.1 Routing (`App.tsx`)
| Route | Guard |
|---|---|
| `/setup` (welcome, sign-in, create org) | guest |
| `/setup/csv` | admin |
| `/dashboard?tab=<view>` | session |
| `/desktop-auth`, `/download` | public |

### 10.2 Views (`lib/navigation.ts`)
- home, ask, workspaces, files, messages, import, capture, shuttle.
- Legacy aliases: `graph`, `status` and others map to `home`; `apps`, `upload` and `connectors` map to `import`; `workflows` maps to `shuttle`.
- **Badges** poll every 30 s: expert inbox, unseen shares, proposed skills.

### 10.3 State and API access
- **Stores (zustand, persisted):**
  - `session`: JWT, org, role
  - `onboarding`
  - `chat`: Ask conversations, kept in localStorage
- **API client** (`lib/api.ts`):
  - `VITE_API_BASE` (`/api` in development via the Vite proxy; a production build fails if it's unset)
  - adds the Bearer token
  - a 401 with a stale-session message clears the session

### 10.4 Styling
Tailwind with CSS-variable tokens and the deep-water palette:

| Name | Hex |
|---|---|
| abyss | `#003135` |
| deep | `#024950` |
| teal | `#2a9d8f` |
| sky | `#afdde5` |
| rust | `#964734` |

---

## 11. Deployment

### 11.1 Docker Compose
| Service | Details |
|---|---|
| redis | |
| postgres | host port **5433**; `init.sql` runs on first init only |
| neo4j | |
| neo4j-init | one-shot; applies the constraints |
| api | port 8000 |
| worker | |
| frontend | nginx, port 5500 |

- Volumes: pgdata, neo4jdata, redisdata, capturedata, blobdata.
- **Caveat:** Compose doesn't pass `TOKEN_ENCRYPTION_KEY`, `SESSION_SECRET`, `APP_ENV`, `ALLOW_DEV_INTEGRATIONS`, `GOOGLE_PUBSUB_PUSH_AUDIENCE` or `CORS_ALLOWED_ORIGINS` to the containers.

### 11.2 Railway
- **api:** `uvicorn` on `$PORT`, healthcheck `/health`, restart on failure ×10.
- **worker:** `python -m worker`.
- **web:** Dockerfile build.

### 11.3 Images
- **API image:** `python:3.11-slim-bookworm`, pinned because of an arm64 exec-format error. tiktoken's `cl100k_base` is vendored because Railway builds can't reach the blob host.
- **Web image:** a node 22 build that bakes in `VITE_API_BASE` and `VITE_GOOGLE_CLIENT_ID`, served by nginx 1.27.
- **Known drift:** `docker-entrypoint.sh` is never used, and nginx has no `/api` proxy.

### 11.4 Blob storage
Local volume by default. Set `BLOB_STORAGE_BACKEND=s3` for multiple replicas.

### 11.5 Smoke tests
`scripts/staging_smoke.sh` checks `/health`, `/ready`, and that an unauthenticated request gets a 401. With a token it also checks `/auth/me`, `/org/summary` and `/integrations`. The manual checklist is in `docs/STAGING_SMOKE.md`.

---

## 12. Tests
- `apps/api/tests`: 51 pytest files (`asyncio_mode=auto`). They cover:
  - auth and hardening
  - ACLs and org isolation
  - the pipeline and chunkers
  - retrieval and the planner
  - Ask tools
  - the LLM provider
  - web search
  - reviews and conflicts
  - status
  - files, documents and monitoring
  - workspaces
  - every connector
  - calendar
  - recurring email
  - capture, guides and Skill Runs
- **Frontend:** no tests. `npm run lint` is just `tsc --noEmit`.

Run the backend tests:

```bash
cd apps/api && pytest
```

---

## 13. Decision log (summary)

| # | Decision | Why | Trade-off |
|---|---|---|---|
| 1 | One API, one worker, agent-grouped packages | Simple deploys, clear ownership | Long LLM calls run inside HTTP requests (@Loombot, Update from memory) |
| 2 | pgvector + Neo4j | Semantic search plus provenance and relationships | Two stores to keep in sync |
| 3 | Embeddings pinned to OpenAI | Stable vector dimensions and rankings across chat providers | Needs an OpenAI key even with local chat |
| 4 | Domain-scoped tenancy, Google-only sign-in | No passwords; automatic org join | No invites, no external members |
| 5 | Chunk-level `visible_to` ACL, private by default | Answers never leak beyond what the asker can read | An empty ACL means org-wide; restrictive connector defaults hide shared meetings |
| 6 | Propose-then-confirm for outbound actions | Safety and trust | One click per action; no bulk send |
| 7 | Per-question model routing, ties to the cheaper tier | Cost and latency | OpenAI only; tools force effort none |
| 8 | Cheap-filter-then-LLM for conflicts, trackers and file monitors | LLM cost proportional to relevant content | Can miss paraphrased matches |
| 9 | Append/update-only file monitoring, with row lock, undo and event log | No silent data loss | CSV only |
| 10 | Format-preserving, evidence-gated document updates saved as new files | Auditability; originals untouched | No inserts in XLSX or PDF; no arithmetic |
| 11 | Polling scheduler with SKIP LOCKED for recurring email | No extra infrastructure; safe with several workers | At-most-once; fixed intervals |
| 12 | Webhooks plus a reconciliation safety net | Low latency with reliability | Calendar can lag up to 6 h |
| 13 | Capture privacy: Accessibility metadata only, or screenshots approved one by one | Employee trust | Less detail than full screen recording |
| 14 | Fail-closed production config | Prevent insecure deploys | Unused connectors still need secrets |
