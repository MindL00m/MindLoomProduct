# MindLoom Desktop Capture Agent (macOS)

Privacy-focused menu-bar agent that captures **structured Accessibility interaction events** from an explicit app allowlist, aggregates them on-device into task summaries, and uploads only those aggregates to the Loom API. Raw events stay on the device; no screenshots or keystroke content are transmitted.

## Privacy defaults

- Empty allowlist → structurally nothing is captured
- Non-allowlisted apps never attach AX observers
- Password / secure fields are redacted at capture time
- Text field values (`AXValue`) are never read
- Only task summaries leave the device

## Requirements

- macOS 13+
- Swift 5.9+ / Xcode Command Line Tools (or Xcode)
- Loom API running (e.g. `docker compose up` → `http://localhost:8000`)
- Accessibility permission for the **packaged** `Loom Capture.app` (not a raw `swift run` binary)

### Accessibility (important)

macOS ties Accessibility to a specific app identity. After every rebuild:

1. `./scripts/package-app.sh` (installs `~/Applications/Loom Capture.app`)
2. `open "$HOME/Applications/Loom Capture.app"`
3. System Settings → Privacy & Security → Accessibility: remove old Loom Capture rows, add **this** app, toggle ON
4. **Quit the agent completely and reopen it** from `~/Applications` (permission does not apply to the already-running process)

Do not keep using `dist/Loom Capture.app` after a rebuild — packaging deletes `dist/`, so the Accessibility toggle is left pointing at a dead copy.

If it still says permission isn’t granted, remove old `MindLoomAgent` / `Loom Capture` rows, add `dist/Loom Capture.app` with +, enable, quit, reopen.

## Build & run

```bash
cd apps/desktop-agent
swift run MindLoomAgent
```

Or package and launch as a real macOS app (recommended — survives closing the terminal):

```bash
cd apps/desktop-agent
chmod +x scripts/package-app.sh
./scripts/package-app.sh
open "$HOME/Applications/Loom Capture.app"
```

A **Loom Capture** control window should open. Use that window for Start / Pause /
End. Open **Setup** (above Sign out) for allowlist and Accessibility. A menu-bar
item is also registered, but your menu bar may be too full for it to appear.

If you only ran `swift build`, that compiles and exits — it does not launch the agent.

### Publish a website download

The package script also writes `apps/web/public/downloads/LoomCapture-macos.zip`.
Rebuild/redeploy the frontend after packaging so nginx can serve it.

For a deployed site, bake the public web origin so downloaded copies sign in
against production instead of localhost:

```bash
cd apps/desktop-agent
LOOM_WEB_BASE=https://your-web-host ./scripts/package-app.sh
# Optional if the API is not at https://your-web-host/api:
# LOOM_API_BASE=https://your-api-host
```

Then rebuild the web image. People can:

- Open `/download` on the website
- Or use **Download Loom Capture for Mac** on the welcome page, Home, and Workflows

Website zip downloads are blocked by Gatekeeper until the app is **Developer ID
signed and notarized**. Ad-hoc signing is only for launching `dist/Loom Capture.app`
on the build Mac.

Until you notarize, install from Terminal (avoids Finder quarantine). After
redeploying the frontend, copy the command on `/download`, or run:

```bash
curl -fsSL https://your-web-host/downloads/install-loom-capture.sh | bash -s -- https://your-web-host
```

To ship a zip Apple will open from a browser download:

```bash
# One-time: Apple Developer ID Application cert in Keychain, plus a notary profile:
#   xcrun notarytool store-credentials loom-notary --apple-id … --team-id … --password <app-specific-password>
export MACOS_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export MACOS_NOTARY_PROFILE="loom-notary"
# Or App Store Connect API key: APPLE_API_KEY / APPLE_API_KEY_ID / APPLE_API_ISSUER
LOOM_WEB_BASE=https://your-web-host ./scripts/package-app.sh
```

The zip is gitignored by default. Either force-add it for Git-based deploys, or
host it separately and set `VITE_DESKTOP_AGENT_DOWNLOAD_URL`.

## Configuration

On first launch the agent writes `~/.mindloom/agent.json`. Packaged builds bake
`apiBase` / `webBase` from `LOOM_WEB_BASE` and `LOOM_API_BASE` (or `/api` on the
web host). A local unpackaged build still defaults to:

```json
{
  "apiBase": "http://localhost:8000",
  "webBase": "http://localhost:5500",
  "orgId": "default",
  "userId": "desktop-user",
  "accessToken": "",
  "email": "",
  "allowlist": [],
  "idleGapSeconds": 90
}
```

### Sign in (required for upload)

1. Start the Loom web app (Compose `:5500` or Vite `:5173` — set `webBase` to match).
2. In the **Loom Capture** window, click **Sign in with Google**.
3. Your browser opens `/desktop-auth`; finish Google sign-in (or reuse an existing web session).
4. The page sends the Loom JWT back to the agent over `127.0.0.1`; the token is saved in `agent.json`.

You should not need to copy tokens from DevTools. Use **Sign out** to clear the saved session.

`apiBase` is the Loom API the agent uploads to. `webBase` is only for the browser sign-in page.

- **Add Frontmost App to Allowlist** — grant capture for the active app (Setup screen or menu bar)
- **Remove** an allowlisted app
- **Open Config File…** / **Reload Config**
- **Grant Accessibility Permission…** if needed

Local raw events (debug / retention) are appended under `~/.mindloom/events/` and are **not** uploaded.

## Capture workflow

1. Grant Accessibility when prompted (System Settings → Privacy & Security → Accessibility), or open **Setup**.
2. Add the apps you want captured to the allowlist (**Setup**).
3. **Start Session** — status shows Capturing (or Paused / Needs Accessibility).
4. Work in allowlisted apps. Use **Pause / Resume** anytime (user-controlled, independent of admin).
5. **End** uploads the session and drafts a Skill File (`POST /captures/activity-sessions` plus analyze).
6. Review the proposed Skill File in the web app **Workflows** tab (source badge: **Desktop**).
7. Approve to ingest into the knowledge graph (same path as browser skills).

## Guided walkthroughs

Approved Skill Files play back in Loom Capture as floating, step-by-step guides.

- **Suggestions:** when you switch to an **allowlisted** app that has approved guides,
  a panel in the top-right offers them. **Not now** snoozes that app for 30 minutes.
- **Guides page:** open **Guides** from the capture page to start any guide you can see,
  with the frontmost app's guides listed first.
- **Auto-advance:** desktop-captured skills store a step anchor (bundle id, action,
  role, identifier, title, window title) for each step the drafter could tie to an
  observed control. When you perform that action, the guide moves on by itself. It
  also looks up to 2 steps ahead, so skipped steps don't stall it. Steps without an
  anchor, and every step of a guide for a non-allowlisted app, use **Next** / **Back**.
- **Privacy:** guide mode attaches AX observers only to allowlisted apps, and
  guide-mode events are never written to `~/.mindloom/events/` or uploaded.

Matching depends on what macOS Accessibility reports. Controls that don't take focus
when clicked (some buttons, custom-drawn UIs) won't auto-advance; use **Next** for those.

## Skill runs

**Run** on an approved skill in the web app's **Workflows** page creates a run. Loom
Capture polls for it, claims it, and walks the steps in order in the guide overlay:

- **Desktop click, menu, focus, navigate anchors:** Loom finds the control with the same
  matcher guides use, presses it (`AXPress`), sets focus, or raises the window, then
  waits up to 4 seconds to observe the result before moving on. It only acts in
  allowlisted apps and never sets a field's value.
- **Browser anchors:** the MindLoom Chrome extension clicks the recorded control. Loom
  shows the step and waits; after 30 seconds without the extension it pauses.
- **Fields, steps without an anchor, and anything that fails:** the run pauses on that
  step with **Next**. Loom never retries with a different control, and a control that
  matches more than once counts as a failure, not a guess.

**Stop run** in the overlay or on the web page stops it; nothing more is performed.
Loom Capture only starts runs created in the last 10 minutes.

If the app doesn't report a pressed button (some native buttons and closed menus
don't move focus), the step pauses as *action failed* even though the press may have
happened. The overlay says to check the app before clicking Next.

The matcher and the run decision (`RunDecision.decide`) live in `Sources/GuideCore`
with no AppKit dependency. Command Line Tools don't ship XCTest, so their checks run
as an executable:

```bash
swift run GuideCoreChecks
```

## Smoke test

1. Start the stack: `docker compose up --build` from the repo root.
2. Run the agent; click **Sign in with Google** and finish in the browser.
3. Add **Notes** (or another app) to the allowlist.
4. Start a session, create/edit a note for ~30s, then **End**.
5. Open <http://localhost:5500> → Workflows → confirm a **Desktop** skill draft.
6. Approve it; confirm it becomes searchable via Ask / appears as a `skill_file` document.

## API contract (agent → API)

`POST /captures/activity-sessions`

```json
{
  "sessionId": "session-…",
  "orgId": "default",
  "userId": "desktop-user",
  "source": "desktop_ax",
  "startedAt": "2026-08-02T12:00:00Z",
  "endedAt": "2026-08-02T12:05:00Z",
  "tasks": [
    {
      "taskId": "…",
      "startedAt": "…",
      "endedAt": "…",
      "primaryApp": "Notes",
      "apps": ["Notes"],
      "bundleIds": ["com.apple.Notes"],
      "stepHints": ["Focus Notes", "Click New Note"],
      "anchors": [
        { "bundleId": "com.apple.Notes", "action": "click", "role": "AXButton", "title": "New Note" }
      ],
      "fieldInteractions": [
        { "role": "AXTextArea", "label": "Note body", "durationMs": 1200 }
      ],
      "stats": { "eventCount": 4, "activeMs": 300000 }
    }
  ],
  "note": ""
}
```

`POST /captures/activity-sessions/{sessionId}/analyze` drafts a Skill File from those summaries (text-only LLM; no vision). The drafter links each step to an anchor where it can, which fills the skill's `bundle_ids` and `guide_steps`.

Skill runs (all scoped to the signed-in user):

| Route | Caller | Effect |
| --- | --- | --- |
| `POST /captures/skill-runs` `{skill_id}` | Web | `pending` at step 0; approved, viewable skills only |
| `GET /captures/skill-runs?status=pending` | Loom Capture | Runs to pick up |
| `POST /captures/skill-runs/{id}/claim` | Loom Capture | `pending` → `running`; a second claim gets 409 |
| `GET /captures/skill-runs/{id}` | All | Run plus its guide payload |
| `POST /captures/skill-runs/{id}/advance` `{step_index, outcome, wait_reason?, error?}` | Loom Capture, extension | `done`, `waiting`, or `failed` for the current step only (else 409) |
| `POST /captures/skill-runs/{id}/stop` | Web, Loom Capture | `stopped` |

`GET /captures/guides[?bundle_id=…]` returns approved Skill Files the caller can see, shaped for playback (`skill_id`, `title`, `bundle_ids`, `steps[{text, anchor}]`, `warnings`, `important_fields`). Skills without anchors, such as older or expert-answer skills, come back as manual-only steps. `GET /captures/guides/{skill_id}` returns a single guide.
