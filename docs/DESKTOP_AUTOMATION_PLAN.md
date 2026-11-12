# Dev plan: autonomous desktop steps for Workflows (macOS)

Status: **approved 2026-11-01; host mode (M0–M3) implemented and merged to `main`.**
macOS only here. Windows has its own surface and client since November 2026
(`shuttle/windows_tasks.py`, `apps/windows-agent`; written, not yet run on Windows). Web pages
are covered separately by browser tasks in the Chrome extension (`shuttle/browser_tasks.py`).
See the Technical Guide for both.

Decisions:
- Host mode first, then the VM.
- macOS 15+.
- Automation runs may type and set values (guides still don't).
- Irreversible actions always need approval unless auto-approve is on for your own skill.
- Every safety item below ships in the first version.

## Where we are

- `shuttle/skill_automation.py` is a server-side LLM loop. `hand_off_to_device` creates a
  `skill_run` and parks the automation in `waiting_device` until the person clicks
  **Continue**.
- The Mac app (`SkillRunWalker` + `StepPerformer`) **replays recorded anchors**. It can
  press, focus or raise a window, and that's all: no LLM, it never types or sets a value,
  it never substitutes a control, and it runs in the foreground on the real desktop.

So today a "workflow" is a series of Ask steps plus a manual hand-off. The goal is to make
`hand_off_to_device` **do the work**: an agent that looks at the app through
Accessibility, acts, checks the result, and asks the person only when it truly can't.

## What the research changes

Full sourced notes are in the chat summary. The findings that shape the plan:

1. **The macOS VM is real, but it is a weak *first* product.**
   - Apple allows 2 macOS guests per Mac. The EULA limits their purpose to development,
     testing, or "personal, non-commercial use". **Legal review is needed** before we sell
     VM mode to businesses.
   - **Mac App Store apps don't run in a VM.** Apple Media Services are unavailable in
     guests, so paid App Store apps won't launch. That breaks "use your own apps" for many
     users.
   - iCloud sign-in works only with macOS 15+ on both host and guest, and only for a VM
     created fresh. Every app account must be signed into again inside the guest. Sign-in
     broke in 26.1 and was fixed later, so expect regressions.
   - Cost: about 18 GB for the restore image plus 50–80 GB per guest, several GB of RAM
     while running, and a long first setup. Fully unattended account setup only arrives
     with macOS 27 hosts (`VZMacGuestProvisioningOptions`).
2. **"Don't lose control of your computer" can mostly be had without a VM.**
   - AX actions (`AXPress`, setting `AXValue`) work on background windows for most native
     apps.
   - Keyboard input via `CGEvent.postToPid` reaches a background app without moving the
     cursor or stealing focus. Cua Driver and OpenAI's Codex computer use both ship this
     way today.
   - Exceptions: menu-bar menus, key-window-dependent shortcuts, canvas and Qt/Java apps,
     and Chromium clicks. Those need a brief foreground moment, or our Chrome extension for
     web.
3. **Every serious VM tool uses an in-guest agent** (vsock RPC), not host-side VNC
   clicking. So the same driver code serves host mode and VM mode. Building it once is the
   main work either way.
4. **AX lies sometimes.** Electron, Catalyst and web content can report success without
   applying the change. So every action is verified by re-reading the app.
5. **Credentials and permissions are hard limits.**
   - Password (secure) fields are opaque, and we will not type credentials: hand off to
     the person.
   - Accessibility must be granted by the user, on the host and again in each guest.
   - Screen Recording re-prompts monthly on Sequoia and later, so screenshots are an
     *optional* fallback, not a dependency.

## Recommended strategy

**Build one "Loom Driver" and ship it in two environments:**

- **Phase 1, Host mode (background-first).** Runs on the user's own Mac with their
  existing apps and logins. It works in background windows wherever macOS allows. When it
  needs the screen it asks first, takes it for a few seconds with a visible countdown,
  and hands it back. Any real mouse or keyboard input from the user pauses it at once.
- **Phase 2, Loom Workspace (VM).** An optional macOS guest managed by the Mac app, running
  the same driver inside it as the guest agent over vsock. For people who want full
  isolation, or long and foreground-heavy runs. It ships with the honest caveats above and
  after legal sign-off.

If you'd rather go VM-first, the driver milestone (M1–M3) is identical, and only M4/M5
swap order.

## Architecture

```
Web (Workflows)                API (FastAPI, worker)                      Mac app
 ───────────────               ─────────────────────                      ─────────
 Run automation ──▶ skill_automation loop
                    hand_off_to_device ──▶ device_tasks row (goal, steps,
                                           inputs, allowed apps, budget)
                                                  ▲   │
                    POST /device-tasks/{id}/step  │   │ next action(s)
                    (observation)                 │   ▼
                                           device_agent.py: LLM call with   ◀── DeviceTaskRunner
                                           driver tools, policy, approvals        └ LoomDriver (AX)
                                                                                    ├ observe
                    needs_approval / needs_input  ──▶ same Approve / Answer UI       ├ act + verify
                                                                                    └ ask person (overlay)
```

- **The LLM loop runs on the server and the Mac executes.** The device posts an
  observation and gets back the next action. This keeps keys, org model choice, logging,
  approval policy and the prompt-injection rules in one place (they already exist in
  `skill_automation.py`). It costs one HTTP round trip per step, and LLM latency dominates
  anyway.
- **Observation** is a compact, numbered AX outline of the target app's focused window:
  role, title or label, value (not for secure fields), and enabled/focused state. It also
  carries the window list, the frontmost app, and the last action's verification result.
  It is capped at a few thousand nodes, like `StepPerformer.maxNodes`. A screenshot plus
  Vision OCR is added only when the tree is empty or useless *and* the user granted Screen
  Recording.
- **Actions (driver tools):**
  - `open_app`, `open_url_or_file`
  - `press(ref)`, `set_value(ref, text)`, `type_text`, `key(combo)`
  - `select_menu(path)`, `scroll(ref)`, `wait_for(condition)`, `read(ref)`
  - `ask_person(instruction)`, `done(summary)`
- **Fallback ladder per action:**
  1. AX in the background, verified by read-back.
  2. `postToPid` key events.
  3. Web pages go to the Chrome extension, which already exists.
  4. Brief foreground, with consent.
  5. `ask_person`: the overlay shows exactly what to do, with **Done** and **Can't do it**
     buttons, and the run waits.
- **No private APIs in v1** (SkyLight, CGVirtualDisplay). They are fragile across OS
  updates, and real users would feel each break.

### Safety (real users)

- **Per-automation app allowlist.** It is shown on the plan screen ("This automation will
  use Mail, Excel"). The driver refuses any other app. This reuses the existing allowlist
  idea.
- **Approvals for irreversible clicks.**
  - The model must flag `irreversible: true` for send, submit, delete, pay or purchase
    actions.
  - The driver *also* flags them by control title and role heuristics ("Send", "Delete",
    "Pay", "Submit", "Buy"), so one missed flag doesn't skip approval.
  - Either flag pauses the run in `needs_approval`, using the existing Approve/Reject flow.
    Auto-approve keeps its current rule (own skills only).
- **Password and secure fields are never read or typed.** They always go to `ask_person`.
- **Screen and app text is data.** The existing `_UNTRUSTED` rule extends to observations.
- **Kill switches:** a global hotkey, **Stop** in the overlay, Stop on the web, and user
  input in host mode.
- **Budgets:** a step budget, wall-clock and per-action timeouts (AX messaging timeout
  about 1.5 s), and the existing `MAX_ROUNDS`.
- **Logging:** a full step log, so every action is visible on the run page.
- **Privacy:** observations are sent to the API only during a run, are redacted with the
  existing `Redaction` rules, and are stored truncated in the run log. The README's
  "nothing leaves the device" promise covers *capture*. Running needs a new, explicit
  consent screen.

## Milestones

| # | Milestone | Main changes | Done when |
|---|---|---|---|
| M0 | Branch + groundwork | `feature/desktop-automation` from main. A tiny **fixture Mac app** (buttons, text fields, table, menu, secure field, a "Send" button) for repeatable tests. | Fixture app builds; `swift run GuideCoreChecks` still passes |
| M1 | **Loom Driver core** | New SwiftPM targets: `DriverCore` (pure logic, i.e. outline serialization, element refs, action-ladder decisions, irreversible/secure policy, checked like `GuideCoreChecks`) and a `LoomDriver` AX layer (observe, press, set_value, type, key, menu, verify, Electron `AXManualAccessibility`) | Driver passes a scripted check suite against the fixture app *and* Notes, TextEdit, Finder, Mail, without stealing focus where macOS allows |
| M2 | **Server device-agent loop** | `device_tasks` table + migration 019; `shuttle/device_agent.py` (driver tool specs, prompt, policy, approvals); routes `POST /device-tasks/{id}/claim`, `/step`, `/stop`; `hand_off_to_device` now creates a device task carrying goal, steps, inputs, allowed apps | pytest covers loop, approvals, irreversible double-check, secure-field refusal, budgets, stop, and prompt-injection fixtures |
| M3 | **Mac runner + UX** | `DeviceTaskRunner` (replaces the walker for automations; old skill-run replay stays for guides); overlay showing live step, Stop, "Loom needs the screen for 5 s" consent, `ask_person` cards; global stop hotkey; user-input pause; consent screen; web run page shows device steps and screenshots-free log | End-to-end: a real Skill File (e.g. "copy invoice totals from Mail into Numbers and draft reply") runs from Workflows with only approvals/asks from the person |
| M4 | **Host-mode hardening + beta** | Test matrix (native: Mail, Notes, Calendar, Finder, Numbers/Excel; Electron: Slack; web via extension); optional Screen Recording + Vision OCR fallback; Developer ID signing + notarization (required to ship anyway); telemetry of failure reasons | Matrix success rate tracked; internal beta |
| M5 | **Loom Workspace (VM)**, gated on legal review | `com.apple.security.virtualization` entitlement; VM manager (download IPSW, create macOS 15+ guest, `VZMacOSVirtualMachineStartOptions`, `VZVirtualMachineView` window, shared folder, NAT); in-guest agent = the same driver over `VZVirtioSocketDevice`; guided setup (sign in to apps, grant AX in guest); macOS 27 hosts use guest provisioning | A run can target "Workspace" and complete with the user's real Mac untouched |

Rough size: M1 and M3 are the biggest pieces (about 1–2 weeks each). M2 is about a week.
M5 is about 2–3 weeks plus legal.

## Decisions I need from you

1. **Order:** host mode first, then VM (recommended), or VM first.
2. **Lift "never sets a value".** Autonomy needs typing. This applies to automation runs
   only; guide replay keeps the current rule.
3. **Irreversible clicks always need approval** unless auto-approve is on for your own
   skill, the same as email today. OK?
4. **Minimum macOS:** keep 13 for host mode? VM mode needs host and guest on 15+.
