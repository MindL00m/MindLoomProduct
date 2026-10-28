# MindLoom user guide

This guide covers what MindLoom can do today and how to use it. It describes the code as of `main` @ `129932a` (28 Oct 2026). For what is missing, see [FEATURE_STATUS.md](FEATURE_STATUS.md). For how things are built, see [TECHNICAL_GUIDE.md](TECHNICAL_GUIDE.md).

---

## 1. What MindLoom is

MindLoom is your company's AI brain. It learns from your documents, email, calendars, chats, meetings and the way people work. It answers questions with citations, routes questions it can't answer to the right expert, tracks the status of work, and drafts actions (emails, meetings, files, pull requests) for you to approve.

Five agents do the work. Each one is a section of the sidebar.

| Agent | Job | Where you find it |
|---|---|---|
| **LoomBot** | Ask questions, work in team rooms, manage files, message experts | LoomBot → Ask, Workspaces, Files, Expert Messages |
| **Weaver** | Bring knowledge in: connectors, uploads, MindLoom Capture | Weaver → Import, MindLoom Capture |
| **Keeper** | Keep knowledge true: access control, reviews, conflicts, expiry | Import page (admin review queue) |
| **Lens** | Understand what's going on: the status board and trackers | Home |
| **Shuttle** | Act: recurring emails and runnable workflows | Shuttle → Workflows |

**MindLoom never sends, books or publishes anything without your click.** Emails, meetings, pull requests, new workspaces and recurring emails are always shown as a card first. The one exception is files: when you ask, MindLoom writes to your own Files straight away, and you can always undo.

---

## 2. Getting started

### 2.1 Set up your organization (first admin)
1. Open the web app. You'll see **Welcome to MindLoom**.
2. Click **Set up a new organization**. Enter the organization name and your email domain (for example `acme.com`).
3. Sign in with Google. Your Google email must be on that domain. You become the organization's **admin**.
4. Optionally upload a **directory CSV** (name, email, title, department, manager). This builds the org chart and tells MindLoom who the experts are.

### 2.2 Join as a colleague
1. Open the web app and click **Sign in to your organization**.
2. Sign in with Google. MindLoom matches your email domain to your organization and adds you as a **member**.

There are no invite emails yet. Anyone with a Google account on the company domain can join. A person only shows up in workspace member pickers after they have signed in at least once.

### 2.3 Your session
You stay signed in for 7 days by default. Admins see extra controls: the review queue, the directory, and the chat model settings.

---

## 3. Home

Home is your overview page. It shows:

- **Company counts**: people, departments and groups.
- **Status board (Lens).** Open projects, open issues and action items that MindLoom has learned about from your sources. Each card shows a summary of the latest status, never the raw email or message. Click **Finish** to close an item.
- **Status trackers.** Topics you asked MindLoom to follow, such as "track the Tiramisu launch". Each shows its current status and a timeline of updates, and you can pause, resume or stop it. Trackers update as new knowledge arrives, and the page refreshes every 60 s. *Trackers don't send notifications yet.*
- **Agent overview**: a card for each agent, with quick links.
- **Chat model** (admins only). Switch Ask between OpenAI and a self-hosted, OpenAI-compatible model.
- **Organization chart**: built from the directory.

---

## 4. Ask (LoomBot)

Ask is the main way to use MindLoom: type a question or a request.

### 4.1 Asking questions
- Answers come only from knowledge you are allowed to see. Every claim is cited; click a source card to see where it came from.
- When sources disagree, the answer says so and cites both. Newer, more authoritative sources (directory, SharePoint, Drive, approved Skill Files) count for more than chat messages.
- Each answer shows a confidence level. When confidence is low, MindLoom **routes the question to an expert**, and they answer in their Expert Messages inbox.
- Under the answer you can see the model used and the difficulty tier (simple, moderate or complex). MindLoom picks a model per question to balance quality and cost.
- Follow-up questions use the conversation so far (about the last 12 turns). Conversations are saved in your browser only.

### 4.2 Live lookups
Ask can also look up live data, not only stored knowledge:
- "How many emails did I get from Priya this week?" counts your Gmail or Outlook mail.
- "Find the email from the supplier about the March shipment" searches your mailbox live.
- "What's the status of project Tiramisu?" reads the status board and the knowledge graph.
- "What do we know about…?" gives an inventory of the knowledge on a topic.

### 4.3 Web search
When a question is about public information, such as a market price, a company or a regulation, MindLoom can search the web. Ask explicitly ("search the web for…"), or let it fall back to the web automatically when your company knowledge can't answer.
- Web answers are marked with a **Web search used** pill and `[WEB: n]` citations.
- Internal names are removed from the query before it goes to the search engine.
- A **Who to contact** section lists only contact details found word for word in the search results.
- Your admin must configure `TAVILY_API_KEY` for web search to work.

### 4.4 Attaching files to a question
Click the paperclip and choose what to do with the file:
- **Use in chat only.** The file is read for this conversation and not stored.
- **Save to knowledge graph.** The file is ingested so everyone it's shared with can ask about it later.
- **Save to Files.** The file is stored in your personal Files.

Supported types: PDF, Word, Excel, CSV, TXT and JSON. PowerPoint and Markdown can also go to the knowledge graph from the Import page.

### 4.5 Things Ask can do for you
Ask can draft actions. Each one appears as a card, and **nothing happens until you click**.

| You say | You get | Clicking does |
|---|---|---|
| "Email the CFO the Q3 summary" | **Review and send** card: To, Subject and Message are editable; attach Files | **Send email** sends it from your Gmail |
| "Ask Priya how the export licence works" | **Expert message** card | Sends an in-app message, plus a notification email or Teams message |
| "Set up a call with Rahul next week" | **Meeting on Google Calendar** card | **Find times everyone is free** checks calendars; **Schedule** books it with a Meet link and emails the invites |
| "Email the supplier the stock report every 2 days" | **Recurring email** card | **Schedule every…** creates a recurring workflow (see §8) |
| "Create a workspace for the Dubai shipment with Amit and Neha" | **Workspace** card with members and a draft `CONTEXT.md` | **Approve & create** |
| "Fix the typo in README on our repo" | **Pull request** card with a diff | Opens a PR on GitHub |
| "Track the status of the Mumbai warehouse" | A status tracker on Home | Created immediately |
| "Make a CSV of all our suppliers and their products" | A new file in Files | Created immediately; can be undone |

### 4.6 Scheduling meetings
1. Ask: "Set up a meeting with Priya and Amit about pricing next week."
2. MindLoom finds each person, checks free/busy for the next 7 days during working hours (09:00–17:00 in your time zone, weekdays), and suggests slots.
3. Edit the title, guests, start time, duration (15 to 120 min), description, location and the Google Meet toggle.
4. Click **Schedule**. Google sends the invites, and the card shows **Open in Calendar** and the Meet link.

Notes:
- Your time zone is taken from your browser.
- The default length is **30 minutes**. Change it in the card if you need longer.
- People outside your organization, or with private calendars, show as "availability unknown".
- If you connected Google before calendar scheduling was added, click **Reconnect** on the Google card under Import.

---

## 5. Workspaces

Workspaces are team rooms where people and LoomBot talk together.

### 5.1 Rooms
- **Everyone** is the organization-wide room. Everyone is in it automatically.
- **Group rooms**: click **New workspace**, give it a name, and tick the colleagues to add.
- **Context rooms** are created from an Ask workspace card. They come with a `CONTEXT.md`: a briefing that MindLoom builds from company knowledge about the room's purpose. LoomBot in a context room answers only from that briefing. Click **Resync** to rebuild it from the latest knowledge, or **CONTEXT.md** to read it.

### 5.2 Members
- Click the member count to see members and add colleagues, or type "@Loombot add Priya to this workspace".
- Only people who have signed in to MindLoom at least once can be added. New members see the full history.

### 5.3 Talking to LoomBot
- Mention **@Loombot** (or click the @Loombot button). LoomBot can do everything Ask can: answer, draft emails and meetings, create files, and add members.
- LoomBot uses **your** mailbox, calendar and access when it answers you. Other members' messages are context only.
- **Privacy:** only you can act on the cards LoomBot makes for you. Other members see a short summary such as "a message draft" or "email sent", never the draft itself or your private sources.
- Paperclip: the file is saved to your Files, and LoomBot reads it with your next @Loombot message.

### 5.4 Emailing from a workspace
Ask "@Loombot email the shipping agent the updated packing list" and LoomBot drafts a card. Review it and click **Send email**; it's sent from your Gmail.
- *Today:* Gmail only. All recipients go in `To`, with no CC or BCC, so recipients can see each other. Send one email per recipient if that matters.

---

## 6. Files

Files is your personal file space. It has three tabs: **My files**, **Drive** and **Email attachments**.

### 6.1 My files
- **New file**: choose CSV, Text or Word, enter a name and content (Word accepts Markdown), and click **Create**. Or **Upload a file instead** (CSV, TXT, DOCX, XLSX or PDF, up to 15 MB).
- **CSV**: a table editor with Table and Raw modes. Add and remove rows and columns, then **Save**, **Undo**, **Download**, **Share** or **Delete**.
- **Word**: preview, and **Edit text** to edit as Markdown. *Editing as text keeps headings, lists, tables, bold, italic and links, but drops fonts, colours, images, headers and footers.*
- **Excel**: an in-browser preview of the first 500 rows and 50 columns, plus **Open in Excel**. *Changes made in Excel are not saved back; use **Upload new version**.*
- **PDF**: in-browser preview.
- **Undo** reverts the last change. Undo again to redo.
- Icons show files shared with you, files edited by LoomBot, and files that are kept updated automatically.

### 6.2 Update from memory
On a Word, Excel, PDF, CSV or TXT file, click **Update from memory** and say what to update, for example "update the prices and dates to the latest". MindLoom:
1. finds the relevant facts in company knowledge,
2. changes only what it has evidence for, citing a source for each change,
3. saves a **new file** named "*name* (updated YYYY-MM-DD)" in the same format and layout. The original is never changed.

You get a list of every change with the before and after values, the reason and the sources.

### 6.3 Files that keep themselves updated
For a **CSV** file you can switch on **Keep updated** and write instructions, for example "update item prices from new emails sent by supplier@acme.com". From then on, whenever new matching knowledge arrives (new email, documents, chats), MindLoom adds rows or fills in cells. It never deletes. Every change is logged with its source and can be undone.
- *Today:* CSV only, and only knowledge that arrives after you switch it on. For a price sheet that updates itself, use a CSV file.

### 6.4 Sharing
Click **Share** and enter up to 25 email addresses:
- **Share in MindLoom** gives a copy to colleagues, who see it in their Files. Later edits don't sync.
- **Email** sends the file as an attachment from your Gmail.

### 6.5 Drive and email attachments
- **Drive**: browse and search your Google Drive. Google Docs, Sheets and Slides open as previews read live from Drive. **Save to my Files** makes a copy.
- **Email attachments**: every attachment MindLoom has indexed from your mailbox. View the extracted text, **Download original** (fetched from your mailbox), or **Save to Files**.

---

## 7. Expert Messages

- When MindLoom can't answer confidently, it messages the most likely expert. You can also message an expert yourself from Ask.
- Experts see a badge and answer with text, or attach a screenshot, voice note or video. MindLoom transcribes audio and video and reads images.
- An expert's answer becomes a **Skill File** that admins can review and publish. After that, Ask uses it as trusted knowledge.
- Experts are also notified by Gmail, Outlook and Teams when those are connected.

---

## 8. Shuttle → Workflows

### 8.1 Recurring emails
Create one from Ask, for example "Send priya@acme.com the stock summary every 2 days". On the card, set the interval (from 15 minutes to 365 days, in minutes, hours, days or weeks), the first send time, and the message. Optionally tick **Rewrite each email with the latest information from MindLoom** so each email is regenerated from current knowledge.

On the Workflows page each recurring email shows its schedule, its next send, how many have been sent, and its status. Actions:
- **Pause** / **Resume**. Resuming never sends a backlog.
- **Send now**. The schedule is unchanged.
- **History**: the last 50 sends.
- **Delete**.

After 3 failures in a row a workflow pauses itself ("Paused after errors").

*Today:* one recipient per workflow, sent from Gmail. Subject, body and interval can't be edited after creation, and "monthly" means every 4 weeks.

### 8.2 Skill Files and guided workflows
Skill Files are step-by-step procedures, learned from MindLoom Capture sessions or expert answers.
- **Review**: proposed Skill Files appear here, and the Workflows badge counts them. Edit, approve or reject them. Approved skills become trusted knowledge.
- **Run / guide**: an approved skill can be run on your Mac (MindLoom Capture) or in Chrome (the extension). The app walks you through each step and detects when you've done it, for example "4/6 steps auto-detect". It never types values for you; it pauses and asks.

---

## 9. Import (Weaver)

### 9.1 Connecting apps
Click **Add connector** and choose a provider:

| Connector | What it brings in | Notes |
|---|---|---|
| **Google Workspace** | Gmail, Google Calendar, Drive (shared drives and folders) | Also enables sending mail and booking meetings. Gmail updates in near real time with Pub/Sub; otherwise every 5 min. |
| **Microsoft 365** | Outlook mail and calendar, Teams channels and chats, SharePoint | An Entra admin may need to grant consent. |
| **Zoom** | Cloud recording transcripts and chats | New recordings arrive by webhook. |
| **GitHub** | Repository browsing and pull request drafts | Paste a fine-grained personal access token and choose repos and allowed actions. *Issues and PR status are not imported.* |
| **Slack** (admin) | Chosen channels and threads; `@Loombot` answers in Slack | MindLoom generates the Slack app manifest. Needs a public HTTPS API address. |

After you authorize, a setup wizard asks you to choose:
1. **What to sync**: mailboxes, calendars, drives, folders, channels.
2. **How far back** to import history.
3. **How often**: real time, hourly or daily.
4. **Who can search it**:
   - **Respect source permissions** (default): only you.
   - **Organization**: everyone.
   - **Selected**: specific people or departments.
5. Preview, then **Confirm**. The first import runs in the background.

Each connector card also has **Manage**, **Pause/Resume** and **Disconnect**. Google also has **Reconnect**, for new permissions.

### 9.2 Uploading data
Drop files on the upload box: PDF, Word, PowerPoint, Excel, CSV, TXT, Markdown, logs, JSON/JSONL conversations, or WhatsApp `.txt` chat exports. Choose **Private** (only you, the default) or **Organization**.
- *Not supported yet:* images, scanned PDFs without text, audio, video, ZIP, and legacy `.doc`/`.xls`/`.ppt`.

### 9.3 Review queue (admins)
- **Conflicts**: two sources disagree. Pick which is right.
- **Verification**: knowledge that is due for a check, or older than 180 days.
- **Proposals**: Skill Files and expert answers waiting to be published.
- **Resolve all** handles conflicts and verifications in bulk. Proposals always need a decision each.
- Published expert answers can be edited or removed.

---

## 10. MindLoom Capture

Capture teaches MindLoom *how* work is done, not just what is written down.

### 10.1 Mac app
1. On MindLoom Capture, follow the Terminal install command. Sign in with Google.
2. Grant Accessibility permission, and choose which apps to observe (an allowlist).
3. Click **Start**, do the task, then **End & analyze**.
4. The app records only which controls you used in allowlisted apps. It never records what you type or takes screenshots. It summarizes tasks on your Mac and uploads only the summaries.
5. A draft Skill File appears under Shuttle → Workflows for review.

### 10.2 Chrome extension
1. Load the extension (unpacked). Paste the API address and your token into the popup.
2. Capture screenshots as you work. Each screenshot waits for your **approval** before it is uploaded, and you can redact areas.
3. Click **Analyze session** to draft a Skill File.

---

## 11. End-to-end example: a day in a trading company

1. **Connect Gmail and Calendar** (Import → Add connector → Google Workspace). Choose "Organization" if the whole team should be able to search supplier mail.
2. **Build a price sheet.** In Ask: "Create prices.csv with columns product, supplier, price, currency, date, and keep it updated from supplier emails." MindLoom creates the CSV with monitoring on.
3. **Ask for offers.** In Ask: "Email supplier@acme.com asking for their current offer on SOPs." Review the card and send. *Each supplier needs its own request today.*
4. **Replies arrive.** Gmail syncs, MindLoom reads each reply and fills in the prices in `prices.csv`. Every change is logged with a link to the email.
5. **Keep a buyer informed.** "Send buyer@xyz.com our available stock every 2 days, with the latest prices." Approve the recurring email card.
6. **Meet.** "Set up a call with the buyer on Thursday." Find a free slot and schedule it.
7. **Track.** "Track the status of the Dubai SOP shipment." Watch the tracker on Home.
8. **Paperwork.** Open last month's invoice in Files and click **Update from memory**: "Update quantities and rates for the new shipment." *Generating invoices and certificates from templates is planned, not built yet.*
