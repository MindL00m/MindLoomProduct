# MindLoom: feature status against the to-do list

The original audit was done on 28 Oct 2026 against `main` @ `129932a`. This page has been **updated for branch `feature/workspace-files-email-templates`**, which adds ten changes (listed under "What changed on this branch"). Every status comes from reading the code. File references are relative to `apps/`.

**Legend:** ✅ Done · 🟡 Partial · ❌ Not done · ➖ Not a code task

## What changed on this branch

| # | Commit | Change |
|---|---|---|
| 1 | Add workspace members by email with pending invites and removal | Type any email address to add someone. People on the company domain who haven't signed in yet get an invite (and an email from your Gmail) and join on first sign-in. Leave, remove (by the workspace creator), and cancel invites. |
| 2 | Email: Cc/Bcc, separate sends per recipient, and Outlook sending | Cc and Bcc. **Send separately** mails each recipient on their own, so nobody sees who else was contacted. Mail can go from Outlook when only Microsoft 365 is connected. File shares and recurring emails also work from Outlook. |
| 3 | Shared workspace Files that every member can open and edit | Each workspace has a Files panel. Files @Loombot creates and files you attach are shared there automatically. Every member opens and edits the same copy, and Loombot can read colleagues' shared files. |
| 4 | Create Excel, PDF and PowerPoint files; add Markdown, JSON and PPTX to Files | New XLSX (from Markdown tables or CSV, keeping numbers and formulas), A4 PDF, and 16:9 PPTX, plus .md and .json. PowerPoint preview. Ask can write all of these. |
| 5 | Read pictures and scanned PDFs with OCR across uploads, ingestion and Files | PNG, JPEG, GIF and WebP work in Files, Import and Ask. Scanned PDFs are OCR'd (up to 20 pages) instead of being rejected. |
| 6 | Upload any file type, several files at once, or a whole folder | Any file type is kept and can be downloaded. Upload several files or a whole folder from the laptop; duplicate names get "(2)". Import can take a whole folder. |
| 7 | Handle ZIP archives in Files, Import, Ask and email attachments | Archives are read safely, with guards against zip bombs. **Extract into Files**. Importing a ZIP queues each document inside it. Ask can read the documents in a ZIP, and ZIP email attachments are read. |
| 8 | Document templates: fill {{fields}} and line items, or turn a sample into a template | `{{field}}` placeholders and `{{items.x}}` line-item rows in Word, Excel, PowerPoint, CSV and text. **Make template** converts a company's sample document into a template. Templates are filled from typed values plus source files and knowledge. |
| 9 | Excel workbooks keep themselves updated from new email and knowledge | Automatic updates now work on **.xlsx**, not just CSV. New rows keep their styling, numbers and per-row formulas, and formula cells are never overwritten. |
| 10 | Shipment documents: invoices, packing lists, certificates of origin and analysis | Reads the bill of lading, contract and lab report. Amounts and totals are calculated exactly. Output uses the company's template or a standard layout. Missing values and mismatched amounts are flagged. |

Verification:
- **Automated:** 522 backend tests pass (447 before this work, 75 added). The frontend typechecks and builds.
- **Live stack:** rebuilt in Docker. New tables are migrated, `/ready` reports ok, and all new routes are served.
- **Not done:** the new flows have not been clicked through in a browser against real Gmail, Outlook or OpenAI accounts.

**Deploy notes:**
- Rebuild the API image, because `python-pptx` was added to `requirements.txt`.
- New tables and the new column are created automatically on startup: `workspace_invites`, `workspace_files`, and `loombot_files.is_template`. Migration files 015–017 document them.

---

## Summary: what is still not done

### Not done ❌
1. **Supplier and buyer outreach as a feature.** The pieces exist: drafts, Bcc and separate sends to many recipients, and approval before sending. There is still no supplier or buyer contact model, and no "ask every supplier for offers" campaign.
2. **Product-to-recipient targeting.** "Email all parties that sell SOPs" does not yet resolve recipients automatically. The graph has no product, supplier or customer entity types.
3. **Automatic master data (party × product).**
4. **Competitor price estimation / prediction model.**
5. **Anthropic (Claude) models.** Model mixing is still OpenAI-only.
6. **Rolling memory.** Ask history is still kept in the browser and truncated; nothing summarises it.
7. **GitHub status tracking.** GitHub activity is not ingested.
8. **Notifications for status trackers and web search.**
9. **Group meetings visible to every attendee.** There is still the Google event-id overwrite bug (see "Bugs found").

### Partially done 🟡
- **Excel connected to the Files page.** Excel files now update themselves from new email. Open in Excel is still a one-way copy, and nothing writes back to Google Sheets or OneDrive.
- **Google Calendar.** The time zone comes from the browser. **The default meeting length is still 30 min, not 1 hour.**
- **Recurring emails.** Still one recipient per workflow, and there is no edit UI. They can now send from Outlook.
- **Doing the task for you.** Outbound actions are still proposed for approval, and Skill Runs don't type values.
- **Microsoft and Zoom connector variables.** `docker-compose.yml` still doesn't pass `TOKEN_ENCRYPTION_KEY`, `SESSION_SECRET`, `APP_ENV`, `ALLOW_DEV_INTEGRATIONS`, `GOOGLE_PUBSUB_PUSH_AUDIENCE` or `CORS_ALLOWED_ORIGINS`.
- **Tacit knowledge beyond screen recording.** Unchanged.
- **Workspace members.** People outside the company's email domain still can't join, because sign-in is limited to that domain. They're reported back instead.
- **Rich editing.** Word documents are still edited as Markdown, and Excel has no cell-level editor; changes come from rewriting, templates or monitoring. No live co-editing.

---

## Item-by-item

### General list

| Item | Status | What exists now | Still missing |
|---|---|---|---|
| Add people to a workspace by email | ✅ | Add any address in the Members dialog or ask @Loombot. Signed-in colleagues join now. Colleagues on the domain who haven't signed in get a pending invite, plus an invite email from your Gmail if it's connected, and join on first sign-in. Leave / Remove / Cancel invite. | Guests from other domains (blocked by domain tenancy). |
| Send email from workspaces | ✅ | Approval card with To (several addresses), Cc, Bcc, and **Send separately**. Sends from Gmail, or from Outlook through Graph `sendMail`. Attachments from Files. | HTML body, signature, reply threading. |
| Create files in workspaces and edit them there | ✅ | A Files panel per workspace (`workspace_files`). Loombot's created and updated files and message attachments are shared there automatically. Any member can open, edit (CSV, TXT, MD, JSON, Word as Markdown), upload and download. Loombot reads shared files. | Real-time co-editing; per-member edit history. |
| Web search | ✅ | Unchanged: Tavily, with automatic fallback. | Needs `TAVILY_API_KEY`. |
| Create and read all file types | ✅ | **Create:** CSV, TXT, MD, JSON, Word, Excel, PDF, PowerPoint. **Read:** those, plus pictures (OCR), scanned PDFs (OCR), ZIP contents, and any text-based file. Any other type is stored and can be downloaded. | Legacy .doc, .xls and .ppt are still not read. |
| Mixing models | 🟡 | OpenAI tier routing. | **Anthropic.** |
| Group meeting shows up for everyone | ❌ | — | Calendar view, attendee ACL, event-id fix. |
| Google Calendar: time zone, 1 hour, availability, book it | 🟡 | Browser time zone, free/busy, booking with a Meet link. | 1-hour default; reading the calendar's own time-zone setting. |
| Upload pictures and PDFs | ✅ | Pictures work in Files, Import and Ask, with OCR. Scanned PDFs are OCR'd instead of being rejected. | OCR uses the vision model, so it needs an OpenAI key and `EMAIL_ATTACHMENT_OCR` on. |
| Pick any file from my laptop | ✅ | Any type, several files at once, or a whole folder (Files and Import). Duplicate names are renamed automatically. | Desktop folder sync. |
| Workflow for file creation | ✅ | Templates with fields and line items. **Make template** from a sample. Fill from typed values plus source files and knowledge. Ask tools `list_templates`, `fill_template` and `make_template`. Plus Update from memory. | Scheduled or recurring document generation. |
| GitHub status tracking | ❌ | Connector only. | Ingest issues, PRs and checks. |
| Tacit knowledge beyond screen recording | 🟡 | Unchanged. | Proactive interviews; decision capture. |
| MindLoom does the task for you | 🟡 | More tools: shipment documents, templates, all file types. | Server-side execution. |
| Notifications on status tracker and web search | ❌ | — | Email, Slack and in-app alerts. |
| Rolling memory | ❌ | — | Server-side summarised memory. |
| Price prediction model | ❌ | — | Everything. |

### Soham

| Item | Status | Notes |
|---|---|---|
| Automate sending emails every couple of days | 🟡 | Recurring emails now also send from Outlook. Still missing: multiple recipients, an edit UI, calendar-style schedules, and failure alerts. |
| Excel link on the Files page, auto-updating prices from new email | 🟡→mostly ✅ | Excel files have **Keep updated**: new emails add rows and update prices in the workbook, keeping its styling and formulas, and each change can be undone and is logged. The in-browser preview and Open in Excel are unchanged; Open in Excel downloads a copy. Missing: writing back to an Excel or Sheets file that lives in OneDrive or Drive. |
| Invoice, certificate of origin, any company document from a sample | ✅ | **Shipment documents** (invoice, packing list, CoO, CoA) are made from the BL, contract and lab report, with exact totals. **Make template** turns any company sample into a template, and **Fill template** makes new documents from it. |
| Mix GPT and Anthropic models | ❌ | |
| Figure out ZIP | ✅ | Extract into Files, import every document in a ZIP, and read ZIPs in Ask and in email attachments, with zip-bomb protection. |

### Isha

| Item | Status | Notes |
|---|---|---|
| Variables for Microsoft and Zoom connectors | 🟡 | Unchanged; see the Compose gap above. |
| MindLoom Capture check | 🟡 | Unchanged; see "Bugs found". |
| A16z fill-out | ➖ | |

### Testing items

| Item | Status |
|---|---|
| Create master data automatically | ❌ |
| Mention a product → automatically pick the right recipients | ❌ The sending side is ready (Bcc and separate sends); recipient resolution is not. |

### Customer use cases (trading workflows)

| Use case | Status |
|---|---|
| Purchase-side outreach to suppliers | 🟡 One draft to many suppliers, sent separately, after approval. No supplier list or targeting. |
| Sales-side outreach to buyers | 🟡 Same as purchase-side. |
| Capture offers and update the pricing sheet | 🟡→mostly ✅ CSV **or Excel** price sheets update from incoming email. No dedicated offer schema or price history. |
| Product-specific, private outreach | 🟡 Recipients are hidden (separate sends or Bcc). Product targeting is not done. |
| Approval before sending | ✅ |
| Shipment documents (invoice, CoO, CoA) | ✅ |
| Competitor price estimates | ❌ |

---

## Bugs and risks found during the audit

These are still open unless marked otherwise.

1. **Google Calendar events overwrite each other between attendees.** `external_id = primary:<eventId>`. See `weaver/google_calendar_sync.py:133-138`.
2. ~~Multi-recipient emails leak addresses.~~ **Mitigated:** Bcc and Send separately are available. A plain multi-address To still shows everyone, which is how email normally works.
3. **"Save to knowledge graph" for PDFs in Ask is visible to the whole org.** `/ingest/pdf` stores `visible_to: []`. See `main.py`.
4. **Large non-tabular documents become a single chunk** on `/ingest/document`.
5. **No job retries, and partial ingestions are never repaired.** See `brain/pipeline.py` (the `existing_chunks > 0` guard).
6. **Worker recovery assumes a single worker.**
7. **The desktop sign-in handoff** pushes the JWT to any local port, with no state or nonce. See `web/src/pages/DesktopAuth.tsx`.
8. **Capture lists are org-wide.** See `routes/captures.py`.
9. **A failed desktop upload loses the session.**
10. **Expert notifications are sent from an arbitrary member's mailbox.**
11. **Conflict reviews quote claim text from private sources to every admin.**
12. **Web deployment drift.** The nginx `/api` proxy and `docker-entrypoint.sh` are unused.
13. **Neo4j constraints are only applied by Compose.**
14. **Recurring emails are at-most-once.**
15. **No MFA code** in the repo, although `security-privacy.typ` describes MFA.

Notes on the new code:
- **Long calls inside the request.** Template filling, Make template and shipment documents each run one or two LLM calls inside the HTTP request, as Update from memory already did.
- **OCR in Ask.** Pictures and scanned PDFs are OCR'd on demand when Ask reads them. Results are cached per file version, in memory, per process.
