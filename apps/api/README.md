# Loom API

This is Loom's only backend. It owns the HTTP API, organization isolation,
document and conversation ingestion, retrieval and AI answers, PostgreSQL,
Neo4j, Google and Microsoft integrations, and approved browser captures.

The source is grouped by responsibility through clearly named modules:

- `main.py` — HTTP routes and application startup
- `pipeline.py`, parsers, chunkers, and extractors — ingestion
- `retrieval.py` and `answerer.py` — question answering
- `auth.py` and `integrations.py` — identity and connections
- `storage.py`, `database.py`, and `db/` — persistence
- `capture_service.py` — screenshot persistence and vision summaries
- `connection_setup.py` — controlled workspace discovery, policies, previews,
  initial imports, watches, and periodic safety checks
- `tests/` — automated backend tests

`main.py` remains the composition point so existing imports and deployment
commands stay stable. New feature areas should use a dedicated module instead
of adding their business logic directly to `main.py`.

Long imports are queued in Redis, tracked in PostgreSQL, and processed by
`python -m worker`. Run the API and worker together; Docker Compose does this
automatically.

The Microsoft connection covers Teams and SharePoint. Its Entra delegated
permissions are `User.Read`, `Team.ReadBasic.All`, `Channel.ReadBasic.All`,
`ChannelMessage.Read.All`, `Chat.Read`, `Chat.Create`, `ChatMessage.Send`,
`Mail.Read`, `Mail.Send`, `Calendars.Read`, `Sites.Read.All`, `Files.Read.All`,
`TeamMember.Read.All`, `ChannelMember.Read.All`, and `offline_access`. An Entra
administrator may need to grant tenant consent.

## Document ingestion

`POST /ingest/document` accepts PDF, DOCX, PPTX, XLSX, CSV, TXT, Markdown,
JSONL, and operational log files. Its optional `metadata_json` form field stores
title, author/owners, source dates, original application and location,
department, project, folder path, version, contributors, permissions, and a
direct source URL. Google Drive, SharePoint, Teams, and Gmail populate the same
metadata contract automatically where their APIs expose those values.

Image-only scans, images, audio, and video are deliberately rejected when no
readable text is present; they belong to the later OCR and media pipeline.

## Knowledge and answer pipeline

Chunks are embedded in pgvector and written to Neo4j with typed `Entity`,
`Person`, `Decision`, `ActionItem`, `Claim`, `Question`, `Document`, and `Chunk`
nodes. Relationships always retain a path back to the supporting chunk and
document.

Retrieval first applies organization and source-permission filters, then ranks
candidate chunks using semantic similarity, graph entity overlap, freshness,
knowledge/extraction confidence, and source authority. Answer generation sees
document version, update date, and source URL, must identify conflicting
evidence, and returns only the sources it actually cites.

## Microsoft 365 and Google Workspace permissions

The controlled Google Workspace setup can select Gmail, calendars, shared
drives, and folders. The Google OAuth client must have the Calendar API enabled
and grant `calendar.readonly` and `gmail.send` in addition to the existing Gmail
and Drive scopes. Existing connections must reconnect once to grant the new
scopes.

The Microsoft 365 setup can select Outlook Inbox, Outlook Calendar, SharePoint,
Teams channels, and the connected user's private/group chats. The Entra app
requires delegated `Mail.Read`, `Mail.Send`, `Calendars.Read`, `Chat.Read`,
`Chat.Create`, and `ChatMessage.Send` in addition to the existing Teams,
SharePoint, and offline-access permissions. An administrator may need to grant
tenant consent, and existing connections must reconnect.

Outlook mail and calendar use Microsoft Graph delta links. Google Calendar uses
Calendar sync tokens. Teams private chats use source versions plus scheduled
reconciliation because Graph does not expose the same delta contract for every
delegated chat collection.

## WhatsApp exports

WhatsApp is implemented as an administrator-controlled export connector. An
administrator exports a chat without media, uploads the `.txt` file, confirms
its timezone and access list, reviews an exact preview, and starts a durable
ingestion job. Messages retain their speakers, timestamps, chat provenance,
source metadata, and permissions.

This is intentionally a snapshot workflow: a later WhatsApp export must be
uploaded again to bring in later messages. It does not claim to be a live
WhatsApp Business API connection, and media is not ingested.

## Zoom

Zoom uses the same controlled setup as Google and Microsoft. Administrators
authorize Zoom, choose cloud transcripts/summaries and/or in-meeting chat,
choose history and search access, review an import estimate, and activate the
connection. The initial import runs on the durable worker.

For automatic updates, configure the public `/webhooks/zoom` endpoint for
`recording.completed` and `recording.transcript_completed`. The worker also
performs scheduled reconciliation according to the selected real-time, hourly,
or daily policy, so missed webhook deliveries are recovered. Updated meeting
artifacts replace their previous indexed source version.

## Tacit browser knowledge

The Chrome extension groups approved screenshots into capture sessions and
analyzes the ordered sequence as a workflow. Employees can remove confidential
rectangles or discard screenshots before upload. The AI infers steps, important
fields, warnings, decision guidance, context, and only material follow-up
questions. It produces a proposed Skill File; an expert can edit, answer
follow-ups, approve, and publish it through the normal permission-aware
knowledge pipeline.

Knowledge governance is stored in Postgres:

- `sync_runs` records source-by-source synchronization outcomes.
- `knowledge_reviews` stores conflict, verification, and expert-proposal work.
- `knowledge_review_schedules` assigns owners, review intervals, and expiry
  dates.
- `knowledge_claims` provides the conservative numeric/negation conflict signal.

## Expert routing

When a low-confidence question has a matching directory expert, the query route
creates an assigned `expert_request`. The employee sees it in the in-app Expert
Messages page and notification badge. An expert answer creates a proposed Skill
File inside the conversation; once the expert approves it, the answer is
versioned and ingested through the normal knowledge pipeline; no administrator approval is
required. Administrators can subsequently correct the answer (creating a new
source version) or remove it from searchable knowledge.

Every request is placed in the in-app Messages page. A durable worker also attempts
Gmail, Outlook, and Teams delivery independently:

- Gmail sends an RFC 2822 message through `users.messages.send`.
- Outlook sends through Microsoft Graph `POST /me/sendMail`.
- Teams creates or reuses a sender/expert one-to-one chat and posts a message.

`notification_deliveries` records each result and provider message id. A
provider failure never removes the in-app request or prevents the other channels
from being attempted.
