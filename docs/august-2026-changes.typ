#set document(
  title: "MindLoom — August 2026 technical summary",
  author: "MindLoom",
  description: "Technical changes landed in git during August 2026.",
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
  #text(size: 13pt)[Technical changes in August 2026]
  #v(0.35em)
  #text(size: 10pt, fill: luma(90))[From git history on `main` · 12--18 August]
]

#v(0.8em)

#block(
  width: 100%,
  fill: luma(246),
  inset: 14pt,
  radius: 6pt,
)[
  August is the month this deployable repository was created and then hardened for a real host. Nine commits landed on `main` between 12 and 18 August 2026, all by a single author. The first commit imported the existing product, including a GitHub connection so Ask can list and read repositories the configured token can access. The rest made the stack runnable on Railway, turned Loom Capture into a website download, opened workspace connections to every member, taught Ask to draft Gmail, and stopped the status board from treating ordinary mail as open work.
]

= Scope of this summary

This document follows the git history of `main` from `96536b7` (`initial`, 12 August) through `c8dc94e` (`amey fixes`, 18 August), which is `HEAD`. There are no other branches and no later August commits.

It is a change log, not a product primer. For what MindLoom is, see `docs/what-is-mindloom.typ`.

#figure(
  table(
    columns: (1.1fr, 1.15fr, 2.75fr),
    inset: 8pt,
    align: (left, left, left),
    stroke: 0.4pt + luma(180),
    fill: (x, y) => if y == 0 { luma(240) },
    [*Date*], [*Commit*], [*Subject*],
    [12 Aug], [`96536b7`], [initial --- full product snapshot],
    [13 Aug], [`501fb9d`], [railway --- API listen port fallback],
    [14 Aug], [`dff8d43`], [deployment stuff --- nginx on Railway],
    [14 Aug], [`2bddd73`], [test --- org-create error handling],
    [14 Aug], [`7a16dcb`], [Add Loom Capture Mac download from the website],
    [14 Aug], [`a845b3b`], [changed admin-only cards --- member connections],
    [18 Aug], [`f69883c`], [amey fixes --- email, status, desktop UX],
    [18 Aug], [`f7a636c`], [amey fixes --- follow-up],
    [18 Aug], [`c8dc94e`], [amey fixes --- follow-up],
  ),
  caption: [All August 2026 commits on `main`, in chronological order.],
)

= Repository landed

On 12 August the deployable tree arrived in a single commit: 223 files, about 43,000 insertions, no parent. That snapshot already contained the four applications --- web, API, macOS desktop agent, and Chrome extension --- plus Docker Compose, Railway metadata, and a staging smoke checklist.

Nothing in that commit is a *delta* for August. It is the baseline. Everything below is what changed after it --- except GitHub repository access, which arrived with the snapshot and is called out here because it is a first-class Ask capability.

= GitHub connection

MindLoom talks to GitHub through a server-side `GITHUB_TOKEN` (classic PAT or fine-grained token in the project `.env`). Recommended scopes are `repo` for private repositories or `public_repo` for public ones. This is not a per-user OAuth connection like Google or Microsoft; the API uses one token, and Ask tools run against whatever repositories that token can see.

When the token is set, Ask can:

- *List repositories* the token can access (`github_list_repos`), optionally filtered to a user or organization. The default list is the authenticated account's owned, collaborator, and organization-member repos, newest first.
- *Read repository metadata* (`github_get_repo`): description, visibility, default branch, language, topics, open issues, stars.
- *Read files and directories* (`github_get_file`): file contents, or a directory listing, at an optional branch, tag, or commit. Binary and oversized files are refused rather than inlined.
- *Propose a pull request* (`propose_github_pr`): fetch the current file, draft a single-file change, and show a diff in Ask. The model does not open the PR. After the user approves, `POST /github/pull-requests` creates a `loom/…` branch, commits the file, and opens the pull request.

Without `GITHUB_TOKEN`, the tools return a configuration error instead of calling GitHub. August's later Ask work left this path in place; the 18 August tests only adjusted GitHub fixtures around the new email proposal shape.

= Hosting on Railway

The first post-import work was making the API and web images start on Railway as well as Compose.

== API listen port

`apps/api/railway.json` had been starting uvicorn on `$PORT`. Railway supplies that variable; a missing or empty value would fail the process. The start command became:

`sh -c 'uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}'`

so a default of 8080 exists when `PORT` is unset.

== Frontend nginx that can boot without Compose DNS

The web image previously proxied `/api/` to the Compose hostname `api:8000` with a static `proxy_pass`. On Railway that hostname does not exist, and nginx refused to start.

The 14 August deployment commit did three things:

- Listen on `${PORT:-80}` at container start, so Railway's assigned port works while Compose can still use 80.
- Resolve `api` at request time (`resolver` plus a variable `proxy_pass`) so nginx can start even when that hostname is absent.
- Document that Railway should bake `VITE_API_BASE` to the public API URL, so the browser does not need the Compose proxy.

A small follow-up the same day fixed organization-creation error handling: `CreateOrg` now treats any `Error` as displayable instead of looking for a dedicated `AuthError` type.

= Loom Capture as a website download

The largest 14 August change (`7a16dcb`, 410 insertions) is how people get the macOS agent.

Before this, Loom Capture was a local Swift build. After it, a packaged app can be downloaded from the website.

== Packaging

`scripts/package-app.sh` now:

- Builds arm64 and x86_64 when possible and `lipo`s a universal binary.
- Names the bundle `Loom Capture.app` instead of `MindLoomAgent.app`.
- Bakes `LoomWebBase` and `LoomAPIBase` into `Info.plist` from `LOOM_WEB_BASE` / `LOOM_API_BASE` (defaulting a deployed site to same-origin `/api`).
- Zips the app with `ditto` and copies it to `apps/web/public/downloads/LoomCapture-macos.zip`.

The agent reads those plist values as defaults. If a user still has localhost URLs in `~/.mindloom/agent.json` but the running build was packaged for a remote host, the config is migrated off localhost automatically. Relative API paths such as `/api` resolve against the web origin.

== Website surface

A public `/download` route was added and taken out of the guest/session gates, so it works the same way as `/desktop-auth`. A `DesktopAgentDownload` card was placed on Welcome, Home, Workflows, and the download page. Nginx serves `/downloads/` as an attachment and returns 404 if the zip is missing, instead of falling back to `index.html`.

`VITE_DESKTOP_AGENT_DOWNLOAD_URL` lets a deploy host the zip elsewhere. Compose and the web Dockerfile pass that bake-time variable through.

= Members can connect their own apps

`a845b3b` changed who is allowed to attach workplace knowledge.

Previously, Google Workspace, Microsoft 365, Zoom, WhatsApp import, and the Home Apps panel were admin-only. Members could not connect their own mail, Drive, Teams, or Zoom, and they could not upload a WhatsApp export.

The API routes for connection setup, Google/Microsoft authorize and sync, Zoom, and WhatsApp switched from `require_admin_context` to `require_user_context`. The web app now shows Connected workspaces to every member. WhatsApp import is on the Upload page for everyone.

Two things stayed admin-only, on purpose:

- the knowledge review queue
- adding the employee directory

The staging smoke checklist was updated to match: members should be able to connect apps and upload files; moderation remains an administrator job.

= Ask can draft and send email

The 18 August work is three commits, all titled `amey fixes`. Together they are the largest product delta of the month (about 1,300 insertions after the initial import). The first and most visible part is email from Ask.

== Before

Ask could already list and read GitHub repositories, propose a single-file PR for approval, send an Expert Message to a *signed-in* org member, and propose workspaces. Lookup ignored directory people who had no Loom account. The compose card was approve-and-send for Expert Messages only.

== Agent tools

`ask_agent.py` gained a `propose_email` tool (recipient, subject, body). Lookup now returns directory emails as well as Loom users; `user_id` may be empty. Messaging intent detection covers "email" / "e-mail", not only "message" / "tell".

If the model forgets a tool, `_ensure_proposals` still builds drafts: an Expert Message when a unique signed-in user is found, and an email whenever the user asked to message someone --- including with a blank To field.

Email never sends from the model. The API returns `proposed_email` on `QueryResponse`. Sending is a separate user action.

== Send path

A new route, `POST /knowledge/reviews/messages/send-proposed-email`, sends from the *caller's* connected Gmail, not a shared org mailbox. It requires a valid address and an existing Google Workspace connection for that user. Gmail sending was refactored so expert-request notifications and user-authored mail share one raw send helper.

Expert request creation no longer assumes an email is always present; a name-only directory match still works.

== Compose card

The Ask UI replaced the Expert Message card with a *Review and send* compose card:

- editable To, Subject, and body
- candidate chips when lookup returns several people
- *Send Expert Message* when the address matches a signed-in Loom user
- *Send email* when Google Workspace is connected
- neither button implies the other; both can be used, and nothing goes out until a button is pressed

Tests cover email intent, directory-only lookup, typed unknown addresses, "propose does not send", missing Google, and send-from-caller Gmail.

= Status board stops treating mail as work

The same 18 August series tightened how projects, issues, and action items appear on Status.

== Extraction

The extractor prompt now treats routine email as noise: FYIs, newsletters, scheduling, acknowledgements, receipts, and passing project-name mentions. `summary` must be one sentence. Open project, issue, and action-item signals are only for clearly unfinished, assigned, or unresolved work.

== Graph writes

`storage.py` stopped synthesizing lifecycle nodes from weak cues:

- bare `action_items` strings no longer become open action items
- `problem_report` / `status_update` knowledge types no longer auto-open issues
- mentioning a project entity no longer marks that project open

Only explicit `*_updates` from the extractor are written.

== Status query and UI

The status board keeps evidence only when `knowledge_type` is `status_update` or `problem_report`, drops raw excerpts, and caps each card at three recent updates. Cards with no qualifying evidence are omitted. The UI shows summaries only --- source bodies are no longer pasted onto the board.

Tests were added for each coercion rule, excerpt stripping, the three-update cap, and "derive current status from summary, not excerpt".

= Desktop agent capture flow simplified

The Mac agent's control window went from a two-page sign-in/capture layout to three pages: Sign in, Capture, and Setup.

On Capture, session controls are Start, Pause / Resume, and a single *End*. End always uploads the activity session *and* drafts a Skill File. The previous pair --- "Upload Summary" versus "Upload & Create Skill" --- was removed from both the window and the menu bar.

Allowlist and Accessibility live on Setup, reached from a button above Sign out. Ending a session no longer dumps the user back through setup chrome.

= What August added up to

By 18 August the deployable repo could:

- access GitHub repositories the configured token can see --- list, read files, and open a PR after Ask approval
- boot on Railway without Compose DNS
- ship Loom Capture as a downloadable Mac app pointed at the deployed origin
- let every member connect Google, Microsoft, and Zoom
- draft Gmail from Ask to anyone, while Expert Messages still require a Loom user
- keep Status to explicit ongoing work instead of every email that named a project
- capture a desktop workflow with Start / Pause / End and a separate Setup page

The commit messages are short. The diffs are not: most of the month's product behaviour sits in `7a16dcb`, `a845b3b`, and the three `amey fixes` commits.
