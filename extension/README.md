# workstyle-capture

Chrome Manifest V3 extension that tracks the active browser tab and optionally captures visible-tab screenshots on an interval and when you switch tabs.

## Load unpacked in Chrome

1. Open Chrome and go to `chrome://extensions`.
2. Enable **Developer mode** (toggle in the top-right corner).
3. Click **Load unpacked**.
4. Select this folder: `extension/` (the directory containing `manifest.json`).
5. Pin the extension from the puzzle-piece menu if you want quick access to the popup.

## Test tab tracking

### Popup

1. Click the **workstyle-capture** toolbar icon.
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

1. On `chrome://extensions`, find **workstyle-capture** and click **Service worker** (under "Inspect views").
2. In the DevTools console, switch between tabs in Chrome.
   - You should see: `[workstyle-capture] Active tab changed: { tabId, url, title }`
3. Stay on one tab and navigate to a different URL (e.g. click a link or change the address bar).
   - You should see: `[workstyle-capture] Active tab URL changed: { tabId, url, title }`

## Files

| File            | Purpose                                      |
|-----------------|----------------------------------------------|
| `manifest.json` | Extension manifest (MV3)                     |
| `background.js` | Service worker — tracks active tab           |
| `popup.html`    | Popup UI                                     |
| `popup.js`      | Popup logic — reads tab state, capture toggle |
| `captures.html` | Gallery page for queued screenshots           |
| `captures.js`   | Loads captures from the background worker     |
