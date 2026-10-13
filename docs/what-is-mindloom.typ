#set document(
  title: "What is MindLoom?",
  author: "MindLoom",
  description: "An explanation of MindLoom as a company knowledge system.",
)
#set page(
  paper: "us-letter",
  margin: (x: 1in, y: 0.95in),
  numbering: "1",
)
#set text(size: 11pt, lang: "en")
#set par(justify: true, leading: 0.72em)
#set heading(numbering: "1.1")
#show heading: set block(above: 1.35em, below: 0.65em)
#show heading.where(level: 1): set text(size: 18pt)
#show heading.where(level: 2): set text(size: 13.5pt)
#show heading.where(level: 3): set text(size: 12pt)

#align(center)[
  #block(below: 0.4em)[
    #text(size: 28pt, weight: "bold")[MindLoom]
  ]
  #text(size: 13pt)[What it is, and how it turns company work into searchable knowledge]
  #v(0.35em)
  #text(size: 10pt, fill: luma(90))[Product overview]
]

#v(0.8em)

#block(
  width: 100%,
  fill: luma(246),
  inset: 14pt,
  radius: 6pt,
)[
  *MindLoom* is a company knowledge system. It gathers what an organization already knows --- from documents, conversations, connected workplace apps, and the tacit workflows people actually perform --- then makes that knowledge searchable, cited, and reviewable.

  The in-app product currently brands as *Loom*. The project, capture agent, and this document use *MindLoom*.
]

= The problem

Organizations already produce a large amount of knowledge. Most of it never becomes reusable.

- Decisions live in email threads, meeting notes, and chat.
- Process knowledge lives in people's heads: how to file an expense, how to close a ticket, which fields matter, which warnings to heed.
- Documents exist, but they are scattered across Drive, SharePoint, local files, and exports.
- When someone asks a question, the answer depends on who they happen to ask, and that answer is rarely written down for the next person.

MindLoom's job is to close that gap. It does not replace the tools people already use. It connects them, extracts durable knowledge from them, and answers questions against that knowledge with sources attached.

= What MindLoom is

MindLoom is an *organization-scoped knowledge graph with a question-answering interface*. An organization signs in, connects its sources, and gradually builds a searchable company brain.

That brain has three layers:

+ *Sources* --- the raw material: files, mail, chat, meetings, directory data, browser captures, and desktop activity summaries.
+ *Knowledge* --- extracted entities, claims, decisions, action items, people, documents, and approved Skill Files, stored so they can be retrieved later.
+ *Use* --- people ask questions, review proposed knowledge, capture workflows, message experts, and see live status.

Every piece of knowledge stays tied to an organization. Search and answers are permission-aware: a person only sees what they are allowed to see.

= How knowledge gets in

MindLoom does not rely on a single ingestion path. Knowledge arrives from the places work already happens.

== Connected workplace apps

Members can connect Google Workspace, Microsoft 365, and Zoom. MindLoom imports only the locations the organization approves, then keeps those connections current.

- *Google* can cover Gmail, Calendar, shared drives, and folders.
- *Microsoft 365* can cover Outlook mail and calendar, SharePoint, Teams channels, and the connected user's chats.
- *Zoom* can cover cloud transcripts, summaries, and in-meeting chat.

Setup is controlled rather than all-or-nothing. An administrator authorizes the app, chooses which content MindLoom may see, reviews an import estimate, and activates the connection. Later updates arrive through webhooks and scheduled reconciliation.

== Manual uploads

Not everything lives in a connected workspace. People can upload documents directly. Supported formats include PDF, Word, PowerPoint, Excel, CSV, text, Markdown, JSONL, and operational logs. Conversation JSON and administrator-controlled WhatsApp text exports are also supported.

Uploads carry provenance: title, owners, dates, original application, location, department, project, version, contributors, permissions, and a source link. The same metadata contract is used for connector documents, so a Drive file and a manual PDF are not second-class citizens of each other.

Visibility can be private (only the uploader) or organization-wide. That choice controls who can find the content in Ask.

== The employee directory

Administrators can import an employee directory. That directory is not decoration. It gives MindLoom a map of people, departments, and groups, and it is what lets a low-confidence question be routed to a named expert instead of dying as an unanswered search.

== Tacit capture

The distinctive source is work that was never written down. MindLoom captures that through two clients:

- a Chrome extension for approved browser workflows
- a macOS menu-bar agent for approved desktop apps

Both produce *Skill Files* --- structured descriptions of how a task is done --- which experts review before they enter the knowledge graph. Capture is covered in more detail below.

= How knowledge is stored

Incoming content is not dumped into a keyword index and left there.

Text is parsed, chunked, and embedded. Those chunks live in PostgreSQL with pgvector for semantic search. At the same time, MindLoom writes a graph in Neo4j. The graph holds typed nodes such as:

- Person
- Entity
- Decision
- ActionItem
- Claim
- Question
- Document
- Chunk

Relationships keep a path back to the supporting chunk and document. That is why an answer can cite a source, surface a conflict, or show that two claims disagree: the graph retains provenance instead of collapsing everything into a summary.

Long imports are queued and processed in the background. The web app tracks jobs; the API and a worker share the work.

= Asking questions

The primary interface is *Ask*. A person types a question in natural language --- for example, who owns a pipeline, what was decided about pricing, or the latest status on a migration --- and MindLoom answers from company knowledge.

Retrieval is not a single similarity lookup. It first applies organization and source-permission filters, then ranks candidates using semantic similarity, graph entity overlap, freshness, extraction confidence, and source authority. The generated answer must identify conflicting evidence and return only the sources it actually cites.

Ask can also propose follow-through work instead of stopping at a paragraph:

- send an expert a question
- draft an email
- open a GitHub pull request
- create a project workspace

Those actions are propose-then-confirm. Nothing is sent or created until a person approves it.

Files can be attached to a conversation for that chat only, or ingested into the graph so they become lasting knowledge.

= Capturing how work is actually done

Documents and chat explain *what was said*. They rarely explain *how a tool is used*. MindLoom treats that how-to knowledge as a first-class source.

== Skill Files

A Skill File is a reviewed workflow artifact. It typically includes:

- a title and purpose
- the application it belongs to
- context
- ordered steps
- important fields
- warnings
- decision guidance
- follow-up questions

A Skill File starts as *proposed*. An expert can edit it, answer follow-ups, reject it, or approve it. Approval publishes it into the knowledge graph, after which Ask can answer questions about that workflow. Visibility can be private or organisation-wide.

Skill Files are knowledge today: they describe a process so people and Ask can use it. They are not an automation runtime. Approved skills can later be exported as executable agent skills, but that is a bridge to an external runner, not the core product.

== Browser capture

The Chrome extension, Loom Capture, watches the active tab and can take screenshots of work in ordinary web apps. Screenshots stay local until the employee reviews them.

Before anything is uploaded, the employee can:

- approve or reject each capture
- discard confidential screens
- black out sensitive rectangles
- add a decision note

Approved captures are grouped into a session. MindLoom then analyzes the ordered sequence as one workflow and drafts a Skill File. The employee is not asked to narrate every screen. The AI infers steps, fields, warnings, and decision points; a human still has to publish them.

== Desktop capture

The macOS agent captures structured Accessibility events from an explicit app allowlist, aggregates them on-device into task summaries, and uploads only those summaries.

Privacy is the default, not an afterthought:

- an empty allowlist captures nothing
- apps that are not allowlisted never attach observers
- password and secure fields are redacted at capture time
- text field values are never read
- raw events stay on the device; no screenshots or keystrokes are transmitted

The employee starts, pauses, and ends a session. Ending a session drafts a Skill File from the task summaries. Those drafts appear in Workflows with a Desktop source badge, then follow the same review and publish path as browser skills.

= Working with other people

MindLoom is not only a search box. Several surfaces exist so knowledge can be corrected, scoped, and acted on.

== Expert Messages

When Ask is not confident and a matching directory expert exists, MindLoom can open an expert request. The employee sees it in Expert Messages; the expert can answer in the thread.

An expert answer can become a proposed Skill File. Once the expert approves it, that answer is versioned and ingested as knowledge. Administrators can later correct or remove it. Requests also attempt delivery through Gmail, Outlook, and Teams, but the in-app thread remains the system of record.

== Workspaces

Workspaces are team rooms inside MindLoom. Members can talk to each other and mention `@Loombot`.

There are two bot modes:

- *Company brain* --- Loombot answers from organization knowledge.
- *Context only* --- Loombot answers only from that workspace's `CONTEXT.md`.

An organization-wide workspace can include everyone. Smaller workspaces are for a named group.

== Status

Status reads the knowledge graph for open projects, issues, and action items, with evidence attached. It is a live operational view of what the company already recorded, not a separate project-management product.

== Knowledge Graph

Administrators can inspect the graph itself: people, entities, documents, chunks, and questions, and the links between them. This is the audit and exploration surface for the same data Ask uses.

== Home and organization

Home is the company landing view: people, departments, groups, connected apps, the organization chart, and the Mac capture download. It is where the organization is assembled before it is queried.

= Privacy, permissions, and review

MindLoom is designed around the idea that company knowledge is sensitive and often wrong until a person says otherwise.

- *Organization isolation* --- queries and documents do not cross organizations.
- *Source permissions* --- connected apps and uploads carry access rules into retrieval.
- *Human approval for tacit knowledge* --- browser pixels and desktop summaries do not become searchable workflow knowledge until a Skill File is approved.
- *Redaction before upload* --- browser captures can black out regions; desktop capture never sends field values or screenshots.
- *Conflict and review queues* --- knowledge reviews, scheduled owners, and conservative claim checks exist so contradictions can be handled instead of silently averaged away.
- *Versioning* --- corrected expert answers create a new source version rather than silently rewriting history.

= What you see in the product

Once an organization exists, the main application is a dashboard with these areas:

#figure(
  table(
    columns: (1.35fr, 2.65fr),
    inset: 8pt,
    align: (left, left),
    stroke: 0.4pt + luma(180),
    fill: (x, y) => if y == 0 { luma(240) },
    [*Area*], [*Purpose*],
    [Home], [Organization overview, people, connected apps, and capture download],
    [Status], [Open projects, issues, and action items with evidence],
    [Ask], [Cited question answering and proposed follow-through actions],
    [Expert Messages], [Routed questions and answers that can become knowledge],
    [Workspaces], [Team rooms with Loombot, optionally scoped to CONTEXT.md],
    [Workflows], [Review and publish Skill Files from browser, desktop, or experts],
    [Upload], [Manual documents, conversation JSON, and WhatsApp exports],
    [Knowledge Graph], [Administrator view of extracted nodes and relationships],
  ),
  caption: [Main surfaces in the MindLoom web application.],
)

Surrounding that are setup (sign in, create an organization, optional directory import), a public Mac download page, and a desktop sign-in bridge so the capture agent can use the same Google session as the web app.

= How the system is put together

MindLoom is one product with four applications, not four products.

#block(
  width: 100%,
  fill: luma(246),
  inset: 12pt,
  radius: 6pt,
  raw(
    block: true,
    "Web app / Mac agent / Chrome extension / connected apps
                 |
                 v
          MindLoom API
                 |
              Redis queue
                 |
          ingestion worker
           /            \
          v              v
 PostgreSQL + pgvector   Neo4j
 jobs, text, search      relationships",
  ),
)

- The *web app* is everything a user sees: setup, dashboard, Ask, workflows, and graphs.
- The *API* owns authentication, organizations, ingestion, search, connected apps, embeddings, screenshot storage, and desktop session processing.
- The *macOS agent* captures on-device activity and uploads only task summaries.
- The *browser extension* captures approved screenshots and is a client of the same API.

There is intentionally one frontend and one backend. Capture clients do not have their own servers.

= In one sentence

MindLoom is the system that turns an organization's documents, conversations, connected apps, and approved real-world workflows into a permission-aware knowledge graph that people can ask, review, and keep current.
