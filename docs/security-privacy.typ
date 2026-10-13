#set document(
  title: "MindLoom — Security and privacy",
  author: "MindLoom",
  description: "Current security and privacy controls in the MindLoom product.",
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
  #text(size: 13pt)[Security and privacy controls in the current product]
  #v(0.35em)
  #text(size: 10pt, fill: luma(90))[Feature inventory · September 2026]
]

#v(0.8em)

#block(
  width: 100%,
  fill: luma(246),
  inset: 14pt,
  radius: 6pt,
)[
  MindLoom is built so company knowledge stays inside an organization, people only see what they are allowed to see, and nothing tacit (screenshots, desktop activity, outbound mail, GitHub changes) becomes shared knowledge or an external action until a human approves it.

  Sign-in never collects a password. Google verifies the account; MindLoom then requires *multifactor authentication* before it issues a session. GitHub is connected with each person's own fine-grained token and an explicit repo/action allowlist. Capture clients are allowlisted and redacting by default. Production refuses to start on weak secrets.
]

= How identity works

MindLoom does not run a password database. People sign in with *Google Identity Services*. The API verifies a Google ID token against `GOOGLE_CLIENT_ID`, checks that email and subject are present, and rejects tokens whose email is not verified. The UI copy is literal: Loom never sees the Google password.

After Google identity is accepted, membership is *domain-scoped*. Creating an organization requires the admin's Google email domain to match the org domain. Signing in loads the organization for that email domain, or fails if none exists. A user JWT is then bound to `org_id`, `user_id`, `role`, and `email`.

API calls require `Authorization: Bearer` with that JWT. The server re-checks that the user still exists in that organization. Admin-only routes (`require_admin_context`) additionally require `role = admin`. Directory import and the knowledge review queue stay admin-only; connecting Google / Microsoft / Zoom and uploading files is allowed for members.

Desktop Loom Capture does not ask people to paste tokens from DevTools. It opens `/desktop-auth` in the browser and receives the JWT only on a loopback listener bound to `127.0.0.1`. The token never travels to an extra public callback host.

= Multifactor authentication

MindLoom requires a *second factor after Google sign-in and before a Loom session JWT is issued*. Google proves who the account is. MFA proves that the person at the keyboard can complete a challenge that is not the Google password.

That MFA path exists in the deployed product. The enrollment and challenge code is not in this local repository snapshot; this section describes the product behaviour, not a file path in `apps/`.

In practice the sign-in sequence is:

+ The person chooses *Continue with Google*. Google may already enforce its own 2-Step Verification on that account.
+ MindLoom verifies the Google ID token (audience, email, verified email).
+ If MFA is enrolled for that user, MindLoom challenges them (authenticator / second factor) and does *not* issue `access_token` until the challenge succeeds.
+ Only then does the web app persist the Loom JWT and enter the dashboard. The desktop agent receives the same post-MFA token over localhost.

Google 2-Step Verification and MindLoom MFA stack: stealing a Google session cookie is not enough if MindLoom still requires its own factor, and a leaked Loom JWT still expires (`SESSION_TTL_HOURS`, default 168 hours) and is rejected if the user row disappears.

= Tenancy and who can see what

Every query and document is organization-scoped. Identical file bytes in two orgs become two documents. Citation lookup with the wrong `org_id` returns nothing.

Within an org, retrieval first applies source-permission filters, then ranks chunks. Access tokens include `org:…`, `user:…`, email, `domain:…`, and optionally `department:…` from the graph. Manual uploads default to *private to the uploader* (`user:{id}`). Choosing organization visibility stores `org:{id}` instead so Ask can find the file for everyone in the org.

Skill Files follow the same split: private or organisation. Only the creator can change visibility. Private skills are viewable and editable only by their creator.

Ask answers must cite sources they actually used. Conflicting evidence is supposed to be called out rather than averaged away. Knowledge review queues, claim checks, and scheduled owners exist so contradictions can be moderated instead of silently published.

= Human approval before knowledge or actions leave the device

Tacit knowledge and outbound actions are propose-then-confirm.

== Browser capture

The Chrome extension keeps screenshots in a *pending* queue on the device. Nothing is uploaded until the employee approves a capture. Before upload they can reject the image, add a note, or *Remove sensitive area* to black out rectangles. Approved captures are grouped into a session; a Skill File is only a *proposal* until an expert approves it in Workflows. Approval is what publishes it into the searchable graph.

== Desktop capture

The macOS agent is allowlist-first:

- an empty allowlist captures nothing
- apps that are not allowlisted never attach Accessibility observers
- password / secure roles (`AXSecureTextField`, `AXSecureTextArea`) are treated as sensitive at capture time
- `AXValue` (the actual text in fields) is never read
- titles and labels that look like passwords, SSNs, card numbers, emails, API keys, or tokens are sanitized to `[redacted]` in local event records
- only on-device *task summaries* are uploaded; raw events stay under `~/.mindloom/events/`

Ending a session drafts a Skill File. It still needs Workflows approval before Ask can use it.

== Ask follow-through

Ask can draft Expert Messages, Gmail, GitHub pull requests, and workspaces. The model does not send mail, open a PR, or create a workspace. The UI shows a compose or diff card. Email sends from the *caller's* connected Gmail, and only after they press Send. GitHub changes wait on an approved diff (`POST /github/pull-requests`) and use *that user's* GitHub connector, not a shared server token. Workspace creation waits on explicit approve.

`OPENAI_API_KEY` stays on the API host. It is not shipped to the browser or the extension.

= Connected apps and inbound webhooks

Google, Microsoft, and Zoom connections are *controlled setup*: the member (or admin) chooses which locations MindLoom may import, previews the policy, then activates it. The product does not silently ingest an entire tenant.

== GitHub connector

GitHub is a *per-user* connector. There is no server-wide `GITHUB_TOKEN` in the live path.

Each person pastes their own fine-grained personal access token in Apps. Inspecting the token lists only the repositories GitHub already allows that token to see; the token is not stored until they confirm. They then choose:

- *which repositories* MindLoom may touch (deny by default --- at least one must be selected)
- *which actions* MindLoom may perform: repository metadata, file contents, and/or opening pull requests

Ask tools and PR approval both enforce that allowlist. A repo the token can see but the user did not grant is rejected. File reads require the contents permission; opening a PR requires pull-request permission (which also implies contents and metadata). The stored token is Fernet-encrypted like other connection secrets and is never returned by the API. Disconnecting deletes it.

Create the PAT on GitHub as a *fine-grained* token scoped to specific repositories and permissions (Contents: Read, or Read and write if PRs are needed). MindLoom's allowlist is a second gate on top of GitHub's.

OAuth access and refresh tokens (and GitHub PATs) are encrypted at rest with Fernet (`TOKEN_ENCRYPTION_KEY`, stored as `enc:v1:…`). Production will not start without that key. Development may store plaintext and logs a warning.

Inbound provider traffic is fail-closed in production:

- Google Drive webhooks require the configured secret
- Gmail Pub/Sub push requires a Google-signed OIDC bearer token whose audience is `GOOGLE_PUBSUB_PUSH_AUDIENCE`
- Microsoft Graph notifications must present a non-default `MICROSOFT_GRAPH_CLIENT_STATE`
- Zoom webhooks require `ZOOM_WEBHOOK_SECRET_TOKEN`

Development may skip Pub/Sub OIDC if the audience is unset; production always requires it.

WhatsApp is a snapshot export, not a live Business API. The uploader confirms timezone and access list and reviews an exact preview before ingestion. Media is not ingested.

= Production hardening

When `APP_ENV=production` the process refuses known-weak defaults: session secret, Neo4j password, Drive webhook secret, Microsoft client state, Zoom webhook secret, Pub/Sub audience, and token encryption key. Simulated `connect-dev` integrations are development-only.

CORS is an explicit origin list (`FRONTEND_URL` plus `CORS_ALLOWED_ORIGINS`). It is never `*`, and credentials are not allowed on CORS. `/docs`, `/redoc`, and `/openapi.json` are disabled in production. `/graph/debug` is not a member-facing surface.

Inbound rate limits (Redis-backed in production) apply per authenticated `org:user`, falling back to client IP: auth, Ask/query, captures, and ingest each have their own ceiling (defaults 20--30/minute).

The web JWT is persisted in the browser under `loom-session` so returning users stay signed in until expiry or sign-out. Pre-JWT leftover sessions without `access_token` are discarded and must sign in again.

= What this does not claim

MindLoom is not end-to-end encrypted: the API, Postgres, Neo4j, and (when configured) S3 can read org content in order to embed and answer questions. GitHub uses each person's own token and the repositories and actions they granted in Apps --- not a shared server token. MFA enrollment UX lives in the deployed identity flow, not in this git tree. Deleting a Railway volume still destroys data; encryption at rest of the volume is a host concern, not an application feature.

= Controls at a glance

#figure(
  table(
    columns: (1.35fr, 2.65fr),
    inset: 8pt,
    align: (left, left),
    stroke: 0.4pt + luma(180),
    fill: (x, y) => if y == 0 { luma(240) },
    [*Control*], [*What it does*],
    [Google sign-in], [No Loom password; verified Google ID token only],
    [Multifactor authentication], [Second factor after Google, before a Loom JWT (deployed; not in this tree)],
    [Domain tenancy], [Org membership follows verified email domain],
    [JWT + admin roles], [Bearer session; admin vs member on privileged routes],
    [Org isolation], [Queries, documents, and citations cannot cross orgs],
    [Visibility ACL], [Private vs organisation on uploads and Skill Files],
    [Desktop allowlist + redaction], [No AXValue; secure fields and identifiers redacted; summaries only leave the Mac],
    [Browser approve / blackout], [Screenshots stay local until approved; sensitive rectangles can be removed],
    [Skill File review], [Proposed workflows are not searchable until approved],
    [Propose-then-send], [Mail, Expert Messages, PRs, and workspaces wait on a human],
    [GitHub connector], [Per-user fine-grained PAT; repo allowlist and action ACL; encrypted at rest],
    [Token encryption], [OAuth tokens and GitHub PATs Fernet-encrypted at rest in production],
    [Webhook verification], [Drive, Pub/Sub OIDC, Graph client state, Zoom secret],
    [CORS and docs gating], [Explicit origins; OpenAPI hidden in production],
    [Rate limits], [Per-user (or IP) caps on auth, Ask, captures, ingest],
  ),
  caption: [Security and privacy controls in the current MindLoom product.],
)
