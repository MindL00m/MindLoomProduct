# MindLoom: feature status against the to-do list

Audited 28 Oct 2026 against `main` @ `129932a` ("workflow automation"). Every status comes from reading the code, not the READMEs. File references are relative to `apps/`.

**Legend:** ✅ Done · 🟡 Partial · ❌ Not done · ➖ Not a code task

## Summary: what is not done

### Not done at all ❌
1. **Shipment documents from templates.** No invoice, certificate of origin or certificate of analysis feature, no template registry, and no bill-of-lading or sales-contract field extraction.
2. **Supplier and buyer outreach.** No supplier or buyer contact model, and no batch "ask every supplier for offers" or "offer to every buyer" flow.
3. **Product-to-recipient targeting.** There is no way to say "email all parties that sell SOPs" and get the recipients automatically. The knowledge graph has no product, organization, supplier or customer entity types.
4. **Automatic master data (party × product).** No table, graph node or job builds it.
5. **BCC and private multi-recipient sends.** Every recipient goes in `To`, so a multi-recipient email exposes every address to everyone.
6. **Competitor price estimation / prediction model.** No ML or statistics libraries, and no training, model or staging pipeline.
7. **Anthropic (Claude) models.** Model mixing is OpenAI-only. The provider type is `Literal["openai", "local"]`.
8. **Rolling memory.** Ask history lives in browser localStorage and is truncated to the last 12 turns, not summarized. Workspace `CONTEXT.md` only changes on a manual Resync.
9. **ZIP files.** No upload path accepts `.zip`.
10. **GitHub status tracking.** The GitHub connector exists, but issues, PRs, commits and checks are never ingested, so status trackers can't follow GitHub.
11. **Notifications for status trackers and web search.** Trackers only update the Home page, which re-fetches every 60 s. Web search is labelled in the answer but sends no alerts.
12. **Group meetings visible to every attendee.** There is no calendar view, and synced Google events are visible only to whoever connected the calendar (plus a likely overwrite bug; see "Bugs found").

### Partially done 🟡
- **Excel on the Files page.** Read-only in-browser preview and a one-way "Open in Excel" link exist. **Auto-updating prices from new email works for CSV only.** XLSX can't be monitored or written to.
- **Adding people to a workspace.** Works only for colleagues who have already signed in. You can't type a raw email address, and there are no invites.
- **Sending email from workspaces.** Works through Gmail with an approval card. No Outlook sending, no CC or BCC, plain text only.
- **Creating and editing files in workspaces.** Loombot can create and edit files, but they land in the asker's private Files. There is no shared workspace file space.
- **Creating and reading all file types.** Many formats can be read. Only CSV, TXT and DOCX can be created from scratch; XLSX, PDF and PPTX can't.
- **Uploading pictures and PDFs.** PDFs work everywhere. Images and scanned PDFs are only OCR'd when they arrive as email attachments; manual uploads reject them.
- **Picking any file from the laptop.** Only the browser file picker, one file at a time, limited to supported types. No folder upload or local sync.
- **Workflow for file creation.** LLM-driven creation and "Update from memory" exist. There are no templates, mail-merge or scheduled generation.
- **Google Calendar scheduling.** Time-zone detection (from the browser), free/busy and meeting creation work. **The default length is 30 min, not 1 hour.**
- **Recurring emails ("every couple of days").** Work end to end, but only to a single recipient, Gmail only, with no UI to edit after creation.
- **Capture offers from replies into Excel.** Possible through CSV monitoring, but there is no offer/price schema and XLSX isn't supported.
- **MindLoom doing the task for you.** The Ask agent reads and writes Files directly; every outbound action is proposed and needs a click. Skill Runs drive the Mac app or extension, but the API never performs the steps itself.
- **Microsoft and Zoom connector variables.** The code is complete, but `docker-compose.yml` doesn't pass several required variables.
- **Tacit knowledge beyond screen recording.** Expert routing and answers, conflict detection, expiry reviews and Skill Files exist. Proactive capture of know-how is missing.

---

## Item-by-item

### General list

| Item | Status | What exists | What's missing |
|---|---|---|---|
| Add people to a workspace by email | 🟡 | Checkbox list of signed-in colleagues (`WorkspacesView.tsx` `MembersDialog`). `@Loombot add <name or email>` (`ask_agent.py` `add_workspace_members`). | People must have signed in already. No raw-email field, no invite emails, no pending members, no remove-member route. |
| Send email from workspaces | 🟡 | `@Loombot` drafts an email card. The asker edits it and clicks **Send email**, which sends through Gmail with Files attachments up to 20 MB (`keeper/review_workflows.py:send_proposed_email`). | Gmail only (no Outlook). No CC or BCC. Plain text only. |
| Create and edit files in workspaces | 🟡 | `write_csv_file`, `write_document` (TXT/DOCX), `create_updated_document`, `read_file` tools. Paperclip upload. | Files are private to the asker; there's no `workspace_id` on `loombot_files`. Editing happens on the Files page, not in the workspace. No live co-editing. |
| Web search | ✅ | Tavily web search as an explicit tool, plus automatic fallback for low-confidence answers (`loombot/web_search.py`). Labelled with a "Web search used" pill and verified contacts. | Needs `TAVILY_API_KEY`. |
| Create and read all file types | 🟡 | **Read:** pdf, docx, pptx, xlsx, csv, txt, md, log, json/jsonl. Email attachments also cover ics, vcf, xml, html, eml and images through OCR. **Create:** csv, txt, docx; edited copies of docx, xlsx, pdf. | New XLSX, PDF or PPTX from scratch. PPTX in Files. Legacy .doc, .xls, .ppt. ZIP. |
| Mixing models | 🟡 | A router sends each question to the simple, moderate or complex tier (`core/model_router.py`). Unknown models and rejected parameters fall back gracefully. | **No Anthropic.** OpenAI only. |
| Group meeting shows up in MindLoom for everyone | ❌ | Synced calendar events are searchable in Ask by the person who connected the calendar. | No calendar view. Attendees aren't added to the ACL. Google event ids collide across attendees (see "Bugs found"). |
| Google Calendar: time zone, 1 hour, availability, book it | 🟡 | Browser IANA time zone sent with every request. Google `freeBusy` check. Event created with a Meet link and `sendUpdates=all`, after approval. | **Default is 30 min** (`api/routes/calendar.py:16`, `ask_agent.py`, `AskView.tsx:1183`). The Google calendar's own time-zone setting isn't read. Slack-drafted meetings assume UTC. |
| Upload pictures and PDFs | 🟡 | PDFs upload, preview and ingest everywhere. Images are OCR'd from email attachments and in expert answers. | Images rejected in Files and Ask. Scanned PDFs rejected on manual ingest. |
| Pick any file from my laptop | 🟡 | Browser file pickers and drag-and-drop. | No arbitrary types, no folder upload, no desktop file sync. |
| Workflow for file creation | 🟡 | Chat-driven creation. "Update from memory" makes evidence-backed, format-preserving copies (`loombot/document_update.py`). | Templates, mail-merge, scheduled or batch generation. |
| GitHub status tracking | ❌ | PAT connector, repo browsing and PR proposals. | No GitHub ingestion or webhooks; trackers can't see GitHub. |
| Tacit knowledge beyond screen recording | 🟡 | Expert routing and inbox (text, image, audio and video answers become Skill Files). Conflict detection. Expiry and verification reviews. Desktop Accessibility activity summaries. Guided workflows. | Proactive interviews. "Why" and decision capture. Follow-up questions routed back to the expert. Meet and Teams transcripts. Expertise inference. |
| MindLoom does the task for you | 🟡 | Ask agent with 25+ tools. Files are written directly; email, meetings, PRs, workspaces and recurring emails need approval. Skill Runs drive the Mac app or Chrome extension. | No server-side execution. Skill Runs never type values. |
| Notifications on status tracker and web search | ❌ | Expert requests notify through Gmail, Outlook and Teams. | Nothing for trackers or web search: no email, Slack, push or sidebar badge. |
| Rolling memory | ❌ | Per-conversation history (12 turns) and a static workspace `CONTEXT.md`. | Server-side memory, summarization, cross-chat memory. |
| Price prediction model | ❌ | — | Everything. |

### Soham

| Item | Status | Notes |
|---|---|---|
| Automate sending emails every couple of days | 🟡 | Recurring emails (`shuttle/recurring_emails.py`, 60 s polling scheduler in the worker). Optional "rewrite with the latest information" before each send. Pause, resume, send now, history, and auto-pause after 3 failures. **Missing:** multiple recipients, an edit UI, Outlook, calendar-style schedules (the time zone is stored but unused), and failure alerts. |
| Excel link on the Files page, auto-updating prices from new email | 🟡 | XLSX preview in the browser (first 500 rows × 50 columns, read-only). "Open in Excel" through a 10-minute signed link; it's a one-way copy. Google Sheets readable live through the Drive tab. **Auto-update works for CSV only** (`loombot/files.py:887`). No XLSX writing, no write-back to Excel or Sheets. |
| Invoice, certificate of origin, any company document from a sample | ❌ | Nearest pieces: "Update from memory" copies a sample DOCX, XLSX or PDF but can't add XLSX rows or do arithmetic; `write_document` makes a plain DOCX. |
| Mix GPT and Anthropic models | ❌ | GPT-tier mixing only. |
| Figure out ZIP | ❌ | |

### Isha

| Item | Status | Notes |
|---|---|---|
| Variables for Microsoft and Zoom connectors | 🟡 | Settings exist in `core/config.py`. **Compose doesn't pass** `TOKEN_ENCRYPTION_KEY`, `SESSION_SECRET`, `APP_ENV`, `ALLOW_DEV_INTEGRATIONS`, `GOOGLE_PUBSUB_PUSH_AUDIENCE` or `CORS_ALLOWED_ORIGINS` to the api and worker containers. As a result the dev Authorize buttons fail, tokens are stored unencrypted, and the dev JWT secret is used. `MICROSOFT_GRAPH_WEBHOOK_URL` must be public HTTPS. Zoom scopes must be set in the Zoom Marketplace. Microsoft and Zoom cards have no Reconnect button. |
| MindLoom Capture check | 🟡 | Both capture paths are wired end to end. Issues are listed under "Bugs found". |
| A16z fill-out | ➖ | Not a code task. |

### Testing items

| Item | Status |
|---|---|
| Create master data automatically | ❌ |
| Mention a product → automatically pick the right recipients | ❌ |

### Customer use cases (trading workflows)

| Use case | Status |
|---|---|
| Purchase-side outreach to suppliers | ❌ Only one-off `propose_email` drafts. |
| Sales-side outreach to buyers | ❌ |
| Capture offers and update the pricing sheet | 🟡 CSV monitoring only, no offer schema. |
| Product-specific, private outreach (no irrelevant parties, recipients hidden from each other) | ❌ No targeting, no BCC. |
| Approval before sending | ✅ Every outbound email is a card that needs a click. Exception: once a recurring email is approved, later sends happen automatically. |
| Shipment documents (invoice, CoO, CoA) | ❌ |
| Competitor price estimates | ❌ |

---

## Bugs and risks found during the audit

These aren't on the to-do list, but they came up while reading the code.

1. **Google Calendar events overwrite each other between attendees.** `external_id = "primary:<event_id>"` and `external_sources` is unique on `(org, provider, external_id)`. When two org members sync the same meeting, the second replaces the first's copy and ACL. A cancellation on one person's calendar deletes the event for everyone (`weaver/google_calendar_sync.py:133-138`).
2. **Multi-recipient emails leak addresses**: all recipients go in `To` (`shuttle/notification_delivery.py:build_email`).
3. **"Save to knowledge graph" for PDFs in Ask is visible to the whole org.** `/ingest/pdf` enqueues with `visible_to: []`, which is visible to everyone. This contradicts the private-by-default rule (`main.py:333-343`).
4. **Large non-tabular documents become a single chunk.** `/ingest/document` turns a whole PDF or DOCX into one message of up to 120k characters. That can exceed the embedding input limit.
5. **No job retries, and partial ingestions are never repaired.** The `existing_chunks > 0` guard skips the re-run (`brain/pipeline.py:296-310`).
6. **Worker recovery assumes a single worker.** It re-queues everything in the processing list at startup.
7. **Desktop sign-in handoff** pushes the JWT to any local port given in the URL, with no state or nonce and no click (`web/src/pages/DesktopAuth.tsx`).
8. **Capture lists are org-wide**, so any member sees other members' capture metadata (`routes/captures.py:90-134`).
9. **A failed desktop upload loses the session.** There is no retry queue.
10. **Expert notifications are sent from an arbitrary member's mailbox**: the most recently updated connection in the org.
11. **Conflict reviews quote claim text from private sources to every admin.**
12. **Web deployment drift.** `docker-entrypoint.sh` is never used, nginx has no `/api` proxy, and the frontend ignores `$PORT` on Railway. The READMEs and `.env.example` describe a proxy that doesn't exist.
13. **Neo4j constraints are only applied by Compose**, not on Railway.
14. **Recurring emails are at-most-once.** `next_run_at` is committed before the send, so a crash loses that run.
15. **No MFA code** in the repo, although `security-privacy.typ` describes MFA.
