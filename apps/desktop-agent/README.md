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
- Accessibility permission for `MindLoomAgent`

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
open dist/MindLoomAgent.app
```

A **Loom Capture** control window should open. Use that window for Start / Pause /
Allowlist / Upload. A menu-bar item is also registered, but your menu bar may be
too full for it to appear.

If you only ran `swift build`, that compiles and exits — it does not launch the agent.

## Configuration

On first launch the agent writes `~/.mindloom/agent.json`:

```json
{
  "apiBase": "http://localhost:8000",
  "orgId": "default",
  "userId": "desktop-user",
  "allowlist": [],
  "idleGapSeconds": 90
}
```

Use the menu to:

- **Add Frontmost App to Allowlist** — grant capture for the active app
- **Remove** an allowlisted app
- **Open Config File…** / **Reload Config**
- **Grant Accessibility Permission…** if needed

Local raw events (debug / retention) are appended under `~/.mindloom/events/` and are **not** uploaded.

## Capture workflow

1. Grant Accessibility when prompted (System Settings → Privacy & Security → Accessibility).
2. Add the apps you want captured to the allowlist.
3. **Start Session** — status shows Capturing (or Paused / Needs Accessibility).
4. Work in allowlisted apps. Use **Pause Capture** anytime (user-controlled, independent of admin).
5. When done, choose either:
   - **End & Upload Summary** → `POST /captures/activity-sessions`
   - **End, Upload & Create Skill File** → upload + `POST /captures/activity-sessions/{id}/analyze`
6. Review the proposed Skill File in the web app **Workflows** tab (source badge: **Desktop**).
7. Approve to ingest into the knowledge graph (same path as browser skills).

## Smoke test

1. Start the stack: `docker compose up --build` from the repo root.
2. Run the agent; add **Notes** (or another app) to the allowlist.
3. Start a session, create/edit a note for ~30s, then **End, Upload & Create Skill File**.
4. Open <http://localhost:5500> → Workflows → confirm a **Desktop** skill draft.
5. Approve it; confirm it becomes searchable via Ask / appears as a `skill_file` document.

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
      "stepHints": ["Focus Notes", "Click New Note"],
      "fieldInteractions": [
        { "role": "AXTextArea", "label": "Note body", "durationMs": 1200 }
      ],
      "stats": { "eventCount": 4, "activeMs": 300000 }
    }
  ],
  "note": ""
}
```

`POST /captures/activity-sessions/{sessionId}/analyze` drafts a Skill File from those summaries (text-only LLM; no vision).
