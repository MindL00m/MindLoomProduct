# MindLoom Capture

Chrome Manifest V3 extension that tracks the active browser tab and optionally
captures visible-tab screenshots. Approved screenshots are sent to MindLoom's main
API (default `http://localhost:8000`) with an `Authorization: Bearer` MindLoom
access token. Configure the API base and token in the extension popup (copy
`access_token` from a signed-in MindLoom web session).

## Load unpacked in Chrome

1. Open Chrome and go to `chrome://extensions`.
2. Enable **Developer mode** (toggle in the top-right corner).
3. Click **Load unpacked**.
4. Select `apps/browser-extension/` (the directory containing `manifest.json`).
5. Pin the extension from the puzzle-piece menu if you want quick access to the popup.

## Test tab tracking

### Popup

1. Click the **MindLoom Capture** toolbar icon.
2. Confirm the popup shows the **Tab ID**, **Title**, and **URL** of your current tab.
3. Switch to another tab and reopen the popup — values should update.
4. Toggle **Capture** ON/OFF. State is saved in `chrome.storage.local` and persists across popup closes.
5. Click **View Captures** to open the captures gallery in a new tab.

### Screenshot capture

With **Capture ON**, the background service worker:

- Takes a screenshot every **10 seconds** of the focused window's visible tab
- Takes a screenshot when you **switch tabs** (debounced to at most once per **3 seconds**)
- Stores up to **50** pending captures in memory (oldest dropped)
- New captures land in a **pending** queue until explicitly approved
- Skips restricted pages (`chrome://`, extension pages, etc.) and logs rate-limit failures without crashing

### Captures gallery & approval (`captures.html`)

1. Turn **Capture ON** in the popup.
2. Browse normal `https://` pages for ~10–20 seconds (or switch tabs).
3. The extension icon shows a **red badge** with the pending (unreviewed) count.
4. Click **View Captures** in the popup.
5. **Pending review** — each capture has **Approve** / **Reject** buttons:
   - **Approve** → moves to the **Approved** section (still in memory)
   - **Reject** → deleted immediately
6. Use **Approve All** to approve every pending capture at once.
7. Badge count should drop as you approve/reject; it clears when pending reaches zero.

### Background service worker console

1. On `chrome://extensions`, find **MindLoom Capture** and click **Service worker** (under "Inspect views").
2. In the DevTools console, switch between tabs in Chrome.
   - You should see: `[MindLoom Capture] Active tab changed: { tabId, url, title }`
3. Stay on one tab and navigate to a different URL (e.g. click a link or change the address bar).
   - You should see: `[MindLoom Capture] Active tab URL changed: { tabId, url, title }`

## Page anchors

While capture is on, each click and each top-frame page change also records a page
anchor with the screenshot: the page's origin and path, the control's role and
accessible name (or link text), the link path, and a CSS selector built only from
`id`, `name`, `role`, and `aria-label`. It never reads a field's value. A click in a
text field, select, or file input records only its label and `needsValue: true`.
`anchor.js` runs on every page but does nothing until capture is on. When a Skill File
is drafted, steps the model ties to a recorded anchor keep it; the rest stay manual.

## Skill runs

When you run an approved skill (web app → Workflows → **Run**), Loom Capture on the Mac
walks it. For each browser step the extension:

1. Finds a tab on the step's origin and path, or opens it in the current window.
2. Finds the control by selector, then by role and accessible name. No match, or more
   than one, pauses the run.
3. Clicks it with `chrome.debugger` (Chrome shows a "started debugging" bar while this
   is attached) and reports back. It detaches when the run leaves browser steps.

It never types. A step that needs a value pauses with Next in the Loom Capture overlay.
The run client polls every 10 seconds while idle and every 1.5 seconds during a run,
using the access token from the popup.

Version 1.5.0 adds the `debugger` and `alarms` permissions. Chrome disables the
extension after an update that adds permissions until you re-enable it on
`chrome://extensions`.

## Files

| File            | Purpose                                      |
|-----------------|----------------------------------------------|
| `manifest.json` | Extension manifest (MV3)                     |
| `background.js` | Service worker — tracks active tab, captures  |
| `anchor.js`     | Content script — records and finds page anchors |
| `run-client.js` | Performs browser steps of skill runs          |
| `popup.html`    | Popup UI                                     |
| `popup.js`      | Popup logic — reads tab state, capture toggle |
| `captures.html` | Gallery page for queued screenshots           |
| `captures.js`   | Loads captures from the background worker     |
Captures are grouped into workflow sessions. The extension never uploads a
screenshot until the employee approves it. Before approval, the employee can
discard the image, add a decision note, or use **Remove sensitive area** to
black out confidential rectangles. **Create Skill File** analyzes the approved
sequence as one workflow; it does not ask the employee to explain every screen.
