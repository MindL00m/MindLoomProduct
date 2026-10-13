#set document(
  title: "MindLoom Competitor Analysis",
  author: "MindLoom",
  description: "A basic competitive landscape for MindLoom against Soroco, Dust, Glean, CompanyBrain, and adjacent players.",
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
  #text(size: 13pt)[Competitor analysis: Soroco, Dust, Glean, CompanyBrain, and the rest of the field]
  #v(0.35em)
  #text(size: 10pt, fill: luma(90))[Draft --- September 2026]
]

#v(0.8em)

#block(
  width: 100%,
  fill: luma(246),
  inset: 14pt,
  radius: 6pt,
)[
  *Bottom line.* MindLoom sits between two markets that are converging. One is *enterprise knowledge and agents* (Glean, Dust, Microsoft Copilot, Atlassian Rovo, ChatGPT company knowledge, Guru, Onyx, CompanyBrain), which answers questions from what was *written down*. The other is *work observation* (Soroco, Skan.ai, UiPath, the task-mining vendors, Scribe), which records how work is *actually done*. MindLoom is one of very few products that puts both into the same cited knowledge graph and asks a human to approve observed workflows before they count as knowledge.

  That wedge is real, but it is closing. Glean announced *Transform* (mines activity to map workflows and draft skill blueprints) and mined *Skills*. Soroco now drafts SME-reviewed *agent blueprints* and has an insights agent. UiPath launched *Cartographer* with a correction-approval ledger. MindLoom is also missing table stakes every buyer will check: Slack, SSO/SCIM, SOC 2, breadth of connectors, and a notarized desktop app.
]

= Scope and method

This is a basic, first-pass analysis. It compares MindLoom as it exists in this repository (see `what-is-mindloom.typ` and `security-privacy.typ`) with four named competitors and a short list of other major players.

- *MindLoom* facts come from the code and docs in this repository.
- *CompanyBrain* facts come from its open-source repository (`supermemoryai/company-brain`, cloned to the Desktop) and Supermemory's public announcements.
- *Soroco, Dust, Glean,* and the rest come from vendor sites, product docs, and press coverage accessed on 28 September 2026.

Pricing and revenue for private companies are often third-party estimates. Those figures are marked _(est.)_ and should be treated as directional, not quotable.

= How the space is shaped

Two questions separate these products: *where the knowledge comes from*, and *who consumes it*.

#figure(
  table(
    columns: (1.1fr, 1.6fr, 1.6fr),
    inset: 8pt,
    align: (left, left, left),
    stroke: 0.4pt + luma(180),
    fill: (x, y) => if y == 0 or x == 0 { luma(240) },
    [], [*Knowledge from documents, chat, and SaaS records*], [*Knowledge from observing real work in apps*],
    [*Consumed by employees asking questions*],
    [Glean, Dust, Microsoft 365 Copilot, Atlassian Rovo, ChatGPT company knowledge, Guru, Onyx, CompanyBrain],
    [Scribe and Tango (step-by-step SOPs). *MindLoom* (Skill Files that Ask can answer from).],
    [*Consumed by ops leaders and agent builders*],
    [Dust and Glean agent builders. Glean Transform _(coming soon; infers workflows from SaaS activity, not UI-level capture)_.],
    [Soroco Scout, Skan.ai, UiPath Cartographer, Celonis, Mimica, KYP.ai],
  ),
  caption: [Positioning. MindLoom is the only product here that spans both columns of the top row.],
)

Two trends matter for MindLoom:

+ *Knowledge vendors are moving toward observed work.* Glean Transform and mined Skills, and Scribe Optimize, all try to learn how work happens instead of waiting for someone to document it.
+ *Observation vendors are moving toward agents and Q&A.* Soroco's Sia, Skan's Agentic Operating Procedures, and UiPath Cartographer all turn observed work into something agents or people can query.

MindLoom's pitch is that it already sits where both are heading. The risk is that each side arrives with far more distribution.

= MindLoom today (the baseline)

== What MindLoom does that matters competitively

- *Cited Ask over a typed graph.* Chunks in pgvector plus a Neo4j graph of people, entities, decisions, action items, claims, questions, and documents. Ranking blends similarity, graph overlap, freshness, extraction confidence, and source authority, and answers must surface conflicting evidence.
- *Tacit capture with employee control.* A Chrome extension (screenshots reviewed and redacted locally before upload) and a macOS agent (Accessibility events from an allowlist only, no screenshots, no field values). Both draft *Skill Files*.
- *Human review as the gate.* Skill Files are proposed, then an expert edits, answers follow-ups, approves, or rejects. Only approved Skill Files enter the graph.
- *Expert routing.* Low-confidence questions route to a named person from the employee directory. The expert's answer can itself become a versioned Skill File.
- *Operational views.* Status (open projects, issues, action items with evidence), Workspaces with Loombot (company brain or `CONTEXT.md`-only mode), and an admin Knowledge Graph explorer.
- *Propose-then-confirm actions.* Expert message, email (Gmail), GitHub pull request (per-user token and repo allowlist), and workspace creation.
- *Model flexibility.* OpenAI by default; an organization can switch to an OpenAI-compatible local endpoint.

== Where MindLoom is behind

- *No Slack.* The API only mentions Slack in comments. Dust, Glean, and CompanyBrain treat Slack as a primary surface and source.
- *Few connectors.* Google Workspace, Microsoft 365, Zoom, and GitHub, plus uploads and WhatsApp exports. Dust lists 70+ connectors, Glean 100+, and both speak MCP.
- *Enterprise identity and compliance.* Google sign-in plus MFA, with orgs scoped by email domain. There is no SAML/OIDC SSO, no SCIM, no Microsoft sign-in, no SOC 2, and no data-residency option.
- *Capture reach.* Chrome and macOS only. The Mac app is ad-hoc signed, not notarized, so Gatekeeper blocks browser downloads. Enterprise task mining assumes Windows desktops.
- *Skill Files are not executable.* They are knowledge, not automations. The OpenClaw bridge in `plan.md` is a plan, not shipped. Glean and Dust already run skills, and Soroco generates agent specs and eval sets.
- *No proactive or scheduled behavior.* No digests, triggers, or unprompted replies. Dust, Glean, and CompanyBrain all have them.
- *No MCP server or public API* for other agents (Claude, Cursor, Copilot) to query MindLoom.
- *No stated pricing, license, or go-to-market.* The repo has no LICENSE file, so it is self-hostable but not open source.

= The four named competitors

== Soroco (Scout)

*What it is.* Boston-based, founded 2014, operations in the US, UK, and India. Scout builds a "work graph" from anonymized human--computer interaction data across an enterprise, using Soroco's proprietary *TribeScope* model. The 2026 positioning is "human and agent workflow observability and redesign":

- *Context-aware process intelligence* --- every variation, exception, and workaround, across teams.
- *Agentic blueprints* --- observed workflows turned into agent-ready specs that SMEs review, refine, and validate.
- *Blueprint-to-agent build and agent eval sets* --- executable specs and test data drawn from real variations.
- *Autonomous workflow baseline* --- measuring where agents act alone versus hand off to humans.
- *Sia (Scout Insights Agent)*, launched September 2026 --- natural-language Q&A over Scout's workflow data, with evidence and source lineage.

*Scale and price.* More than 200 organizations, including Fortune 500 customers (Bayer, Unilever, and PepsiCo are cited). No disclosed venture funding. Pricing is quote-only and sold to large enterprises. Revenue estimates range from about \$11M to \$45M _(est., low confidence)_. Gartner Peer Insights rates Scout 4.9/5 across 50 reviews.

*Overlap with MindLoom.* The closest conceptual match: both observe real work and require a human to review what the AI inferred. Soroco's "SMEs review and refine blueprints" is essentially MindLoom's Skill File approval.

*Where Soroco wins.* Enterprise scale and trust, continuous team-wide capture, workflow analytics and ROI for operations leaders, a direct path from observation to agent specs and eval sets, and a mature enterprise sales motion.

*Where MindLoom wins.* Soroco answers questions about *workflow data*. It does not answer from Drive, mail, meetings, or chat, and it is not a knowledge base for the individual employee. MindLoom captures per-session with the employee's consent and redaction, which reads as enablement rather than monitoring. It also has expert routing, and it can be used self-serve without a transformation program.

*How to position.* Do not sell against Soroco on process intelligence. Sell to the team that needs *answers and how-tos*, not an operations dashboard, and to mid-market companies that will never buy a Soroco engagement.

== Dust

*What it is.* Paris-based "multiplayer AI" platform (founded 2022, ex-Stripe and ex-OpenAI founders). Teams build and share agents over company knowledge and tools, inside collaborative workspaces. It offers 70+ connectors plus any MCP server, 20+ models (OpenAI, Anthropic, Google, Mistral, DeepSeek), skills, memory, scheduled "wake-ups," and multi-agent workflows on schedules and triggers.

*Scale and price.* About \$20M ARR, 3,000+ organizations, 300,000+ deployed agents, and 240% net revenue retention. It raised a \$40M Series B in May 2026 (Abstract and Sequoia, with Snowflake Ventures and Datadog), bringing total funding past \$60M. Credit-based seats: Free (500 lifetime credits), Pro \$30/month or \$24/month annually (8,000 credits), Max \$150/month or \$120/month annually (40,000 credits), and custom Enterprise. SOC 2 Type II, US or EU data residency, single-tenant option, and SCIM and audit logs on Enterprise.

*Overlap with MindLoom.* Ask plus actions, and shared team spaces (Dust's collaborative workspaces versus MindLoom Workspaces with Loombot).

*Where Dust wins.* Connector and model breadth, a real agent builder, automation (schedules, triggers, chaining), compliance, self-serve pricing, and momentum.

*Where MindLoom wins.* Dust retrieves and acts on what exists in connected tools. It has no UI-level capture of how work is done, no reviewed-workflow artifact, no typed graph of decisions and claims with conflict surfacing, no expert routing, and no Status view derived from the graph.

*How to position.* "Dust gives your team agents. MindLoom gives those agents, and your people, the knowledge nobody wrote down." Longer term, feeding approved Skill Files into Dust (via MCP or the Agent Skills format) is more realistic than out-building Dust's agent platform.

== Glean

*What it is.* The category leader in enterprise AI search and assistants (founded 2019, Arvind Jain). It is built on a permissions-aware *Enterprise Graph* over 100+ connectors, with Glean Assistant, Glean Agents, MCP servers, model choice, and APIs.

*Scale and price.* \$300M ARR as of May 2026, triple its \$100M mark of 15 months earlier, with Fortune 500 customer count nearly doubling year over year. Valued at \$7.2B in its June 2025 Series F. *Enterprise Flex* pricing combines per-user seats with a pooled allowance of FlexCredits, which agent runs and advanced features consume. Third-party estimates put seats around \$35--50/user/month with minimums of roughly 100 seats _(est.)_.

*The two announcements that matter most for MindLoom:*
- *Glean Skills* (private beta). Supports the open Agent Skills standard. Glean says its personal graph will *mine* skills from how people actually work, with humans reviewing and refining them.
- *Glean Transform* (coming soon, announced at Glean:GO in August 2026). It mines activity across calendar, Slack, docs, CRM, and code to map workflows by role, ranks automation opportunities, drafts agent and skill blueprints for admin review, and tracks ROI in a ledger.

*Overlap with MindLoom.* Cited Q&A over a graph (direct overlap), plus a fast-approaching overlap with Skill Files through mined Skills and Transform.

*Where Glean wins.* Almost everything about scale: connectors, ranking quality, permissions enforcement, agents, governance, security, and brand.

*Where MindLoom wins.* Glean infers workflows from *SaaS exhaust* (who edited which doc, which meeting, which CRM update). MindLoom captures the *actual steps inside the application*, including fields, warnings, and decision points, and has the employee and an expert approve them. That matters for work that leaves little SaaS trace: legacy web apps, admin consoles, finance and operations tools, and desktop apps. Glean's price floor also excludes most organizations under a few hundred seats.

*How to position.* Assume Glean will own "enterprise search" wherever it is deployed. Compete below its seat floor, and pitch MindLoom's capture layer as the *source* of reviewed skills that Glean (and others) can import through the Agent Skills standard or MCP.

== CompanyBrain (Supermemory, open source)

*What it is.* Supermemory's Slack-native "AI teammate." It launched around August 2026, was *discontinued on 9 September 2026* (customers refunded), and was open-sourced later that month under Apache 2.0. Supermemory's stated reasons: focus on its memory API, and avoid competing with customers who build company-brain products on that API.

*Architecture.* TypeScript on Cloudflare Workers, Durable Objects (one agent per org), D1, and KV. Memory is stored in Supermemory's hosted API (the deployment needs a `SUPERMEMORY_API_KEY`). Models are bring-your-own-key: Anthropic, OpenAI, Google, xAI, or OpenRouter.

*Notable design ideas:*
- A *permissions graph* of memory: one shared org memory from public channels, one memory per private channel, and one per employee from DMs. Each message writes to exactly one; reads widen with how private the room is.
- *Proactive chime-in.* A triage step (answer, acknowledge, investigate, or pass) lets it reply in threads without being mentioned, with rate limits and per-channel settings.
- *Tool actions over MCP* (GitHub, Linear, Notion, Sentry, Gmail, plus a marketplace and custom servers). Writes run only under the asker's own connection and pause for an Approve/Deny card. *Leasing* asks a teammate for one-time access when only they have a tool connected.
- *Automations* (scheduled digests that fail closed), a *code sandbox* (Cloudflare container or Daytona), *skills*, and a workspace prompt.

*Limits of the open-source build.* No background document sync (no Drive or OneDrive), no MCP server or API for asking from other tools, Slack as the only chat surface, and memory tied to a Supermemory account.

*Competitive meaning.* It is not a vendor MindLoom will meet in a sales cycle anymore. It matters in three other ways:
+ *A free floor.* A Slack-heavy startup can deploy it in minutes for roughly the cost of model tokens plus Cloudflare's \$0--5/month.
+ *A reference design.* Its proactive triage, room-scoped memory, write approvals under personal connections, leasing, and fail-closed digests are well-documented patterns. MindLoom lacks most of them, and the Apache 2.0 license allows reuse with attribution.
+ *A market signal.* A funded memory company left the "company brain" app layer after about a month. The app layer is crowded, and differentiation has to come from something other than chat over memory. For MindLoom, that is observed, reviewed workflow knowledge.

= Other major players

#figure(
  table(
    columns: (1.25fr, 2.45fr, 1.9fr),
    inset: 7pt,
    align: (left, left, left),
    stroke: 0.4pt + luma(180),
    fill: (x, y) => if y == 0 { luma(240) },
    [*Player*], [*What it is*], [*Relevance to MindLoom*],
    [Microsoft 365 Copilot], [Assistant and agents grounded in Microsoft Graph (SharePoint, Outlook, Teams), sold as a per-seat add-on to Microsoft 365.], [Default choice in Microsoft shops. MindLoom's Microsoft connector competes with a bundle.],
    [Atlassian Rovo], [Search, chat, and agents on the Teamwork Graph (Jira, Confluence, 100 connectors). Now available inside Microsoft 365 Copilot and Teams.], [Owns engineering and product knowledge wherever Atlassian is standard.],
    [ChatGPT Business / Enterprise], [Company knowledge through connectors ("apps") to Microsoft 365, Google Workspace, and others, with citations and deep research.], [Many buyers already pay for it, so "good enough" knowledge Q&A comes at no extra cost.],
    [Guru], [Governed knowledge layer: SMEs get verification reminders, and answers are cited and delivered through MCP. Moved to custom enterprise pricing bundled with services.], [Closest to MindLoom's human-review ethos, but for written cards, not observed work.],
    [Onyx (formerly Danswer)], [Open-source (MIT) enterprise search and agents with 50+ connectors, self-hostable or air-gapped. Onyx Cloud is about \$20/user/month. \$10M seed; about 31,000 GitHub stars.], [The credible free alternative to Glean. It sets price expectations for self-hosting buyers.],
    [UiPath Cartographer], [A "Map of Work" of rules and judgment calls, built on UiPath's process mining, task mining, and task capture. Its *Decision Ledger* routes human corrections of agent work to the map owner for approval.], [Nearly the same loop as Skill File review, backed by UiPath's installed base.],
    [Skan.ai], [Desktop sensor that builds a "Context Graph of Work" (25B+ events claimed) and versioned *Agentic Operating Procedures* for agents.], [Enterprise task mining with a strong agent story. Competes for the same "observed work" budget as Soroco.],
    [Celonis, Mimica, KYP.ai], [Process mining (event logs) and task mining (desktop capture) that score automation opportunities.], [Operations-transformation budgets. Rarely a head-to-head deal, but they shape how buyers frame capture.],
    [Scribe], [Auto-generated step-by-step SOPs from browser and desktop capture. About 6M users and a reported \$1.3B valuation (Nov 2025). *Scribe Optimize* adds workflow intelligence. Pro Team is \$13/seat/month.], [Direct overlap with browser Skill Files. It wins on polish and distribution; MindLoom wins on Ask, the graph, and expert review.],
    [Tango], [Started as SOP capture with in-app guidance; pivoted in 2026 to AI browser automation ("Hybrid Automation").], [Shows where Skill Files lead: execution. That is what the OpenClaw plan targets.],
  ),
  caption: [Adjacent players. Pricing and scale figures are vendor-published or press-reported as of September 2026.],
)

= Feature comparison

#[
#set text(size: 8.6pt)
#figure(
  table(
    columns: (1.55fr, 1.2fr, 1.1fr, 1.1fr, 1.1fr, 1.1fr),
    inset: 5.5pt,
    align: (left, left, left, left, left, left),
    stroke: 0.4pt + luma(180),
    fill: (x, y) => if y == 0 { luma(240) } else if x == 1 { luma(250) },
    [*Capability*], [*MindLoom*], [*Soroco*], [*Dust*], [*Glean*], [*CompanyBrain*],
    [Cited Q&A over docs, mail, meetings, chat], [Yes], [No (Sia answers over workflow data only)], [Yes], [Yes], [Partial (Slack plus live tool reads; no doc sync)],
    [Connector breadth], [Google, Microsoft 365, Zoom, GitHub, uploads], [N/A], [70+ plus MCP], [100+ plus MCP], [MCP tools; no data connectors],
    [Slack], [No], [N/A], [Yes], [Yes], [Yes (primary surface)],
    [Typed knowledge graph], [Yes (people, decisions, claims, action items)], [Work graph of interactions], [Not a stated feature], [Yes (Enterprise Graph)], [Memory graph (Supermemory)],
    [UI-level workflow capture], [Yes (Chrome, macOS; per session, opt-in)], [Yes (continuous, team-wide)], [No], [No (Transform infers from SaaS activity; coming soon)], [No],
    [Human review before capture becomes knowledge], [Yes (Skill File approval)], [Yes (SME blueprint review)], [N/A], [Partial (mined Skills reviewed; beta)], [N/A (writes memory from chat)],
    [Expert routing from directory], [Yes], [No], [No], [Not verified], [No],
    [Actions with approval], [Partial (email, expert message, GitHub PR, workspace)], [No], [Yes (agents, MCP)], [Yes (agents)], [Yes (MCP writes, Approve/Deny)],
    [Scheduled or proactive work], [No], [N/A], [Yes], [Yes], [Yes (chime-in, digests)],
    [Workflows become executable], [Planned (OpenClaw bridge)], [Agent specs and eval sets], [Skills], [Skills; agent blueprints (coming)], [Skills],
    [Model choice], [OpenAI or local OpenAI-compatible], [Proprietary TribeScope], [20+ models], [Model Hub], [Bring your own key (4 providers plus OpenRouter)],
    [SSO / SCIM / SOC 2], [No (Google sign-in plus MFA)], [Enterprise (not verified here)], [Yes (SOC 2 Type II; SCIM on Enterprise)], [Yes], [N/A (self-hosted on your Cloudflare)],
    [Self-host / license], [Docker Compose; no license file], [SaaS], [Hosted; MIT repo; single-tenant option], [Hosted; customer-hosted option _(est.)_], [Yes, Apache 2.0],
    [Pricing], [Not set], [Quote-only, enterprise], [Free; \$24--30 and \$120--150 per seat/month plus credits], [Seats plus FlexCredits; about \$35--50/user, 100-seat minimum _(est.)_], [Free; you pay model and Supermemory usage],
  ),
  caption: [Capability comparison. "N/A" means the capability does not apply to that product's category.],
)
]

= Where MindLoom can win

+ *Mid-market, process-heavy teams.* Operations, finance, support, and IT teams whose work happens in web apps and admin consoles. Glean's seat floor and Soroco's enterprise motion leave them unserved, and Scribe gives them documents but not answers.
+ *"Answers plus how-tos" in one place.* An employee can ask "what did we decide about refunds?" and "how do I issue a partial refund in the billing console?" in the same Ask, with citations for both. No named competitor does both today.
+ *Consent-based capture as a trust story.* Soroco and Skan deploy continuous, team-wide sensors for operations leaders. MindLoom captures only when the employee starts a session, lets them redact before upload, and asks an expert to approve the result. In works-council, EU, or privacy-sensitive settings, that is a selling point, not a limitation.
+ *Knowledge that corrects itself.* Expert routing, versioned expert answers, conflict surfacing, and review queues create a feedback loop that pure retrieval products lack. Guru and UiPath's Decision Ledger show buyers value this loop.

= Threats

- *Glean Transform and mined Skills* are aimed squarely at MindLoom's wedge, with Glean's distribution. If Glean ships UI-level capture, the wedge narrows to price and consent.
- *UiPath Cartographer and Soroco blueprints* show incumbents already have the observe, review, and publish loop. They are adding Q&A (Sia) faster than MindLoom is adding enterprise readiness.
- *Bundling.* Microsoft Copilot, Rovo, and ChatGPT company knowledge come with licenses buyers already pay for. "Good enough" Q&A at no extra cost is the most common reason a deal stalls.
- *Free open-source floors.* Onyx (MIT) and CompanyBrain (Apache 2.0) set price expectations near zero for self-hosted knowledge Q&A.
- *Execution.* Tango, Glean, Dust, and Soroco are all moving from "document the workflow" to "run the workflow." Skill Files that stay read-only will look dated within a year.

= Recommendations

+ *Lead with the wedge.* Position MindLoom as the system that captures and verifies the knowledge nobody wrote down, then makes it answerable. Treat knowledge Q&A as the price of entry, not the headline.
+ *Close the table-stakes gaps before enterprise pilots:* a Slack connector and Slack surface for Ask, SAML/OIDC SSO and SCIM, Microsoft sign-in, a notarized Mac app, and a SOC 2 readiness plan. Windows capture comes next if operations buyers are the target.
+ *Export, do not compete, on agents.* Publish approved Skill Files in the open Agent Skills format and expose Ask and Skill Files through an MCP server. Glean imports Agent Skills, and Dust, Claude, and Cursor all speak MCP. That turns larger platforms into distribution. Ship the OpenClaw execution bridge as the in-product demo.
+ *Borrow CompanyBrain's best patterns* (Apache 2.0): proactive triage for Loombot in Workspaces, scheduled digests from Status, and room-scoped memory for Workspaces.
+ *Price below Glean's floor.* A per-seat plan that small teams can start without sales, with capture seats priced separately from Ask-only seats, puts MindLoom where Glean, Soroco, and Guru's custom packages are not.
+ *Watch list, reviewed quarterly:* Glean Transform general availability, whether Glean or Scribe adds UI-level capture, Soroco Sia moving beyond workflow data, and UiPath Cartographer adoption.

= Sources

Accessed 28 September 2026. Third-party revenue and pricing estimates are marked _(est.)_ in the text.

- Soroco: #link("https://soroco.com/")[soroco.com]; #link("https://www.gartner.com/reviews/product/scout-31005636")[Gartner Peer Insights, Scout]; #link("https://www.linkedin.com/posts/soroco-pvt-ltd_ask-sia-scout-insights-agent-activity-7501594941173735424-DSsn")[Sia launch post]; #link("https://toolradar.com/tools/soroco-scout")[Toolradar pricing check]; #link("https://getlatka.com/companies/soroco.com")[GetLatka estimate].
- Dust: #link("https://dust.tt/home/product")[product]; #link("https://dust.tt/home/pricing")[pricing]; #link("https://dust.tt/blog/economics-of-multiplayer-ai")[credit pricing blog]; #link("https://cfotech.news/story/dust-raises-usd-40-million-for-workplace-ai-platform")[Series B coverage]; #link("https://rywalker.com/research/dust")[Ry Walker research].
- Glean: #link("https://techcrunch.com/2026/05/28/gleans-top-line-crosses-300m-as-ai-budget-cutting-becomes-its-major-selling-point/")[TechCrunch, \$300M ARR]; #link("https://docs.glean.com/glean-enterprise-flex-pricing")[Enterprise Flex]; #link("https://www.glean.com/blog/glean-skills-launch-2026")[Skills launch]; #link("https://www.glean.com/transform")[Transform]; #link("https://www.theravitshow.com/releases/glean-go-2026-keynote-announcements-context-not-models")[Glean:GO 2026 recap].
- CompanyBrain: `~/Desktop/company-brain` (README, `docs/architecture.md`, `docs/guide/*`); #link("https://github.com/supermemoryai/company-brain")[GitHub repository]; #link("https://www.plushcap.com/content/supermemory/blog/supermemory-an-update-to-supermemory")[Supermemory update (discontinuation)].
- Others: #link("https://www.atlassian.com/platform/teamwork-graph")[Atlassian Teamwork Graph]; #link("https://www.atlassian.com/blog/rovo/rovo-jira-microsoft-365")[Rovo in Microsoft 365]; #link("https://superkind.ai/blog/copilot-vs-chatgpt-enterprise")[Copilot vs ChatGPT Enterprise]; #link("https://www.getguru.com/pricing")[Guru pricing]; #link("https://onyx.app/")[Onyx]; #link("https://www.teamazing.com/blog/onyx-ai-enterprise-review-2026/")[Onyx review]; #link("https://www.reworked.co/digital-workplace/uipath-cartographer-maps-how-enterprise-work-actually-runs/")[UiPath Cartographer]; #link("https://www.skan.ai/context-graph-of-work")[Skan Context Graph of Work]; #link("https://www.mimica.ai/articles/what-is-task-mining")[Mimica on task mining]; #link("https://www.supered.io/blog/tango-vs-scribe/")[Tango vs Scribe]; #link("https://www.happysupport.ai/en/blog/scribe-vs-tango")[Scribe and Tango pricing].
