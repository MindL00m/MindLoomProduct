// Browser task client: MindLoom operating web pages for an automation run ("Run with
// MindLoom" → hand_off_to_browser → a device task with surface "browser").
//
// It claims the signed-in person's pending browser task, then repeats: read the working
// tab as a numbered outline (page-driver.js), send it with the last action's result to
// the API, get one action back, check it again here (task-policy.js), perform it, wait
// for the page to settle. The model and the approval rules live on the server; this file
// only reads and acts.
//
// Limits it keeps whatever the server sends:
// - only https pages on the automation's allowed sites that this person also allowed in
//   the popup ("Sites MindLoom may click on"), never sign-in or password-manager sites,
//   never MindLoom's own pages;
// - a control whose label sends, submits, deletes, pays or confirms is only clicked when
//   the action arrives approved; the same for Enter in a form with such a button and for
//   OK on a confirm dialog;
// - password, one-time-code and card fields are never read or typed into;
// - cancelling Chrome's "started debugging" bar stops the task and its run.
//
// Loaded into the service worker by background.js after run-client.js and task-policy.js
// (uses getApiConfig, runApi, waitForTabLoad, RUN_SITES_KEY, RUN_SITE_REQUESTS_KEY,
// fromExtensionPage, updatePendingBadge from there).

const TASK_POLL_IDLE_MS = 10_000;
const TASK_WAIT_MS = 1_500;
const TASK_ALARM = "mindloom-browser-tasks";
const TASK_STATE_KEY = "browserTask";
const TASK_FINISHED = new Set(["completed", "failed", "stopped"]);
const TOP_FRAME_BUDGET = 10_000;
const SUB_FRAME_BUDGET = 2_500;
const OUTLINE_LIMIT = 15_500;
const MAX_TASK_FRAMES = 8;
const MAX_TASK_TABS = 8;
const SCRIPT_TIMEOUT_MS = 8_000;
const CDP_TIMEOUT_MS = 6_000;
const READ_LIMIT = 8_000;
const MAX_LOOP_FAILURES = 12;

let taskPollTimer = null;
/** The running drive loop, so one task runs at a time. */
let taskLoop = null;
/**
 * The task being driven: { taskId, tabId, allowed, ownOrigins, refs, tabRefs, dialog,
 * attachedTabId, status }. `refs` maps e12 → { frameId, n, generation } for the latest outline.
 */
let work = null;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const taskPath = (taskId) => `/captures/device-tasks/${encodeURIComponent(taskId)}`;
const sameText = (a, b) => String(a || "").replace(/\s+/g, " ").trim().toLowerCase() === String(b || "").replace(/\s+/g, " ").trim().toLowerCase();

function withTimeout(promise, ms, what) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${what} took too long`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

const failed = (detail, extra = {}) => ({ ok: false, detail: String(detail).slice(0, 1900), ...extra });
const done = (detail, extra = {}) => ({ ok: true, detail: String(detail).slice(0, 1900), ...extra });

// --- Polling and claiming ---------------------------------------------------------------

function scheduleTaskPoll(delay) {
  clearTimeout(taskPollTimer);
  taskPollTimer = setTimeout(pollBrowserTasks, delay);
}

async function pollBrowserTasks() {
  if (taskLoop) return;
  let delay = TASK_POLL_IDLE_MS;
  try {
    const { accessToken } = await getApiConfig();
    if (!accessToken) return;
    let taskId = null;
    let tabId = null;
    let resumed = false;

    // The service worker may have been restarted in the middle of a task.
    const saved = (await chrome.storage.session.get(TASK_STATE_KEY))[TASK_STATE_KEY];
    if (saved?.taskId) {
      const task = await runApi(taskPath(saved.taskId)).catch(() => null);
      if (task && task.surface === "browser" && !TASK_FINISHED.has(task.status) && task.status !== "pending") {
        ({ taskId, tabId } = saved);
        resumed = true;
      } else {
        await chrome.storage.session.remove(TASK_STATE_KEY);
      }
    }
    if (!taskId) {
      const pending = await runApi("/captures/device-tasks?status=pending&surface=browser");
      for (const task of [...pending].reverse()) {
        try {
          await runApi(`${taskPath(task.task_id)}/claim?surface=browser`, { method: "POST" });
          taskId = task.task_id;
          break;
        } catch (error) {
          if (error.status !== 409) throw error; // 409: taken already, or too old to start
        }
      }
    }
    if (!taskId) return;
    delay = 1_000;
    taskLoop = driveTask(taskId, tabId, resumed)
      .catch((error) => console.warn("[MindLoom Task]", error instanceof Error ? error.message : String(error)))
      .finally(async () => {
        taskLoop = null;
        await endWork();
        scheduleTaskPoll(1_000);
      });
  } catch (error) {
    console.warn("[MindLoom Task]", error instanceof Error ? error.message : String(error));
  } finally {
    if (!taskLoop) scheduleTaskPoll(delay);
  }
}

async function saveWork() {
  if (work) await chrome.storage.session.set({ [TASK_STATE_KEY]: { taskId: work.taskId, tabId: work.tabId } });
}

async function endWork() {
  await detachTaskDebugger();
  work = null;
  await chrome.storage.session.remove(TASK_STATE_KEY).catch(() => {});
  updatePendingBadge();
}

function showTaskBadge(waiting) {
  chrome.action.setBadgeText({ text: waiting ? "!" : "ML" });
  chrome.action.setBadgeBackgroundColor({ color: waiting ? "#d97706" : "#2563eb" });
}

// --- The drive loop -----------------------------------------------------------------------

async function driveTask(taskId, tabId, resumed) {
  work = {
    taskId, tabId: tabId ?? null, allowed: [], ownOrigins: [], refs: new Map(), tabRefs: new Map(),
    dialog: null, attachedTabId: null, status: "running",
  };
  await saveWork();
  let result = resumed
    ? failed("Chrome restarted the MindLoom extension during the last action. Check the page before continuing.")
    : null;
  let failures = 0;

  while (failures < MAX_LOOP_FAILURES) {
    let task;
    try {
      task = await runApi(taskPath(taskId));
    } catch (error) {
      if (error.status === 404 || error.status === 401) return;
      failures += 1;
      await sleep(2_000);
      continue;
    }
    work.status = task.status;
    if (TASK_FINISHED.has(task.status)) return;
    work.allowed = task.allowed_apps || [];
    work.ownOrigins = task.own_origins || [];
    if (task.status !== "running") {
      // Waiting for the person: an approval, or something only they can do.
      showTaskBadge(true);
      await sleep(TASK_WAIT_MS);
      continue;
    }
    showTaskBadge(false);

    let response;
    try {
      const observation = await observe();
      response = await runApi(`${taskPath(taskId)}/step`, {
        method: "POST",
        body: JSON.stringify({ observation, result }),
      });
    } catch (error) {
      if (error.status === 404 || error.status === 401) return;
      // 409: the person replied meanwhile; 429: too fast. The result is sent again.
      failures += error.status === 409 ? 0 : 1;
      await sleep(error.status === 429 ? 4_000 : 800);
      continue;
    }
    failures = 0;
    result = null;
    if (!response.action) continue; // finished or waiting: the top of the loop handles it

    try {
      result = await perform(response.action);
    } catch (error) {
      result = failed(`The action could not be performed: ${error instanceof Error ? error.message : String(error)}`);
    }
    result.action_id = String(response.action.id || "").slice(0, 100);
    await saveWork();
  }
}

// --- Sites ----------------------------------------------------------------------------------

/** Why this address can't be worked on, or "". The automation's sites, then the popup's. */
async function refusalFor(url, { request = true } = {}) {
  const refusal = pageRefusal(url, work.allowed, work.ownOrigins);
  if (refusal) return refusal;
  const { apiBase } = await getApiConfig();
  const origin = new URL(url).origin;
  if (origin === new URL(apiBase).origin) return "MindLoom never operates its own pages.";
  const stored = await chrome.storage.local.get([RUN_SITES_KEY, RUN_SITE_REQUESTS_KEY]);
  if ((stored[RUN_SITES_KEY] || []).includes(origin)) return "";
  if (request) {
    const requests = new Set(stored[RUN_SITE_REQUESTS_KEY] || []);
    requests.add(origin);
    await chrome.storage.local.set({ [RUN_SITE_REQUESTS_KEY]: [...requests].slice(-20) });
  }
  return (
    `MindLoom may not work on ${origin} in this Chrome yet. The person must open the MindLoom ` +
    "extension (toolbar icon) and allow this site under “Sites MindLoom may click on”. " +
    "Call ask_person with that instruction."
  );
}

/** Other open tabs on allowed sites, as [t1], [t2]: never tabs on any other site. */
async function allowedTabs() {
  work.tabRefs = new Map();
  const listed = [];
  for (const tab of await chrome.tabs.query({})) {
    if (tab.id === work.tabId || !tab.url || listed.length >= MAX_TASK_TABS) continue;
    if (await refusalFor(tab.url, { request: false })) continue;
    const ref = `t${listed.length + 1}`;
    work.tabRefs.set(ref, tab.id);
    listed.push({ ref, title: String(tab.title || "").slice(0, 300), url: String(tab.url).slice(0, 2000) });
  }
  return listed;
}

// --- Reading the page ---------------------------------------------------------------------

async function runInFrames(target, func, args = []) {
  return withTimeout(
    chrome.scripting.executeScript({ target: { tabId: work.tabId, ...target }, func, args }),
    SCRIPT_TIMEOUT_MS,
    "Reading the page",
  );
}

async function injectDriver() {
  try {
    await withTimeout(
      chrome.scripting.executeScript({ target: { tabId: work.tabId, allFrames: true }, files: ["page-driver.js"] }),
      SCRIPT_TIMEOUT_MS,
      "Preparing the page",
    );
  } catch {
    // A frame that can't take scripts must not stop the top frame from being read.
    await withTimeout(
      chrome.scripting.executeScript({ target: { tabId: work.tabId }, files: ["page-driver.js"] }),
      SCRIPT_TIMEOUT_MS,
      "Preparing the page",
    );
  }
}

/** Call a page-driver function in the frame that holds `ref` (or in `frameId`). */
async function driver(frameId, method, args = []) {
  const [entry] = await runInFrames(
    { frameIds: [frameId] },
    (name, params) => {
      const api = window.__mindloomDriver;
      if (!api) return { ok: false, error: "The page changed; use a ref from the latest page." };
      return api[name](...params);
    },
    [method, args],
  );
  return entry?.result ?? { ok: false, error: "The page did not answer; it may still be loading." };
}

async function observe() {
  const tabs = await allowedTabs();
  const base = { app: "(no page open)", url: "", text: "", elements: [], tabs, dialog: null };
  work.refs = new Map();
  const tab = work.tabId == null ? null : await chrome.tabs.get(work.tabId).catch(() => null);
  if (!tab) {
    work.tabId = null;
    return { ...base, text: "No page is open in the working tab. Use open_url to open one." };
  }
  base.app = String(tab.title || "").slice(0, 120);
  base.url = String(tab.url || "").slice(0, 2000);
  const refusal = await refusalFor(tab.url || "");
  if (refusal) return { ...base, text: `${refusal} Nothing on this page can be used.` };
  if (work.dialog) return { ...base, dialog: work.dialog, text: "(a dialog blocks the page)" };

  let frames;
  try {
    await ensureDebugger(tab.id);
    await injectDriver();
    frames = await runInFrames(
      { allFrames: true },
      (top, sub) => window.__mindloomDriver?.outline(window === window.top ? top : sub),
      [TOP_FRAME_BUDGET, SUB_FRAME_BUDGET],
    );
  } catch (error) {
    if (work.dialog) return { ...base, dialog: work.dialog, text: "(a dialog blocks the page)" };
    return { ...base, text: `The page could not be read (${error instanceof Error ? error.message : error}). Wait and look again.` };
  }

  const lines = [];
  const elements = [];
  const snapshots = frames.filter((frame) => frame.result).sort((a, b) => a.frameId - b.frameId);
  const top = snapshots.find((frame) => frame.frameId === 0)?.result;
  let counter = 0;
  let listedFrames = 0;
  for (const { frameId, result: snapshot } of snapshots) {
    if (frameId !== 0) {
      if (!snapshot.lines.length || listedFrames >= MAX_TASK_FRAMES) continue;
      // An embedded page from another site is only read when that site is allowed too.
      if (!top || snapshot.origin !== top.origin) {
        const embedded = snapshot.origin === "null" ? "about:blank" : `${snapshot.origin}/`;
        if (pageRefusal(embedded, work.allowed, work.ownOrigins)) {
          lines.push(`frame from ${snapshot.origin}: not an allowed site, not read`);
          continue;
        }
      }
      listedFrames += 1;
      lines.push(`frame "${String(snapshot.title || snapshot.url).slice(0, 80)}":`);
    }
    const refByNumber = new Map();
    for (const element of snapshot.elements) {
      counter += 1;
      const ref = `e${counter}`;
      refByNumber.set(element.n, ref);
      work.refs.set(ref, { frameId, n: element.n, generation: snapshot.generation });
      elements.push({
        ref, role: String(element.role).slice(0, 60), label: String(element.label).slice(0, 300),
        secure: Boolean(element.secure), focused: Boolean(element.focused && (snapshot.focused || frameId === 0)),
      });
    }
    if (frameId === 0 && snapshot.modal) lines.push("A dialog is in front of the page; only the dialog is listed.");
    if (snapshot.dropped.above) lines.push(`… ${snapshot.dropped.above} more lines above: scroll up to see them`);
    else if (frameId === 0 && snapshot.more.above) lines.push("(the page continues above: scroll up for more)");
    for (const line of snapshot.lines) {
      lines.push((frameId === 0 ? "" : " ") + line.replace(/\[#(\d+)\]/, (_, n) => `[${refByNumber.get(Number(n)) || "?"}]`));
    }
    if (snapshot.dropped.below) lines.push(`… ${snapshot.dropped.below} more lines below: scroll down to see them`);
    else if (frameId === 0 && snapshot.more.below) lines.push("(the page continues below: scroll down for more)");
    if (snapshot.dropped.shown || snapshot.truncated) lines.push("(this page is very large: part of it is left out; use read on a table or section)");
    if (snapshot.messages.length) lines.push(`Messages that appeared: ${snapshot.messages.map((m) => `"${m}"`).join(" | ")}`);
  }
  return { ...base, text: lines.join("\n").slice(0, OUTLINE_LIMIT), elements };
}

// --- Real input through the debugger ----------------------------------------------------------

async function ensureDebugger(tabId) {
  if (work.attachedTabId === tabId) return;
  await detachTaskDebugger();
  try {
    await chrome.debugger.attach({ tabId }, "1.3");
  } catch (error) {
    if (!/already attached/i.test(String(error?.message || error))) throw error;
  }
  work.attachedTabId = tabId;
  await cdp("Page.enable");
  // The tab behaves as focused even when the person is working in another window.
  await cdp("Emulation.setFocusEmulationEnabled", { enabled: true }).catch(() => {});
  await sleep(300); // the debugging bar resizes the page
}

async function detachTaskDebugger() {
  if (!work?.attachedTabId) return;
  const tabId = work.attachedTabId;
  work.attachedTabId = null;
  try {
    await chrome.debugger.detach({ tabId });
  } catch {
    /* already detached (tab closed, or the person cancelled debugging) */
  }
}

function cdp(method, params = {}) {
  return withTimeout(chrome.debugger.sendCommand({ tabId: work.attachedTabId }, method, params), CDP_TIMEOUT_MS, method);
}

/** Send input; a page dialog opened by it would otherwise hold the command open. */
async function cdpInput(method, params) {
  const sent = cdp(method, params).catch(() => {});
  const deadline = Date.now() + CDP_TIMEOUT_MS;
  let finished = false;
  void sent.then(() => {
    finished = true;
  });
  while (!finished && !work.dialog && Date.now() < deadline) await sleep(40);
}

chrome.debugger.onEvent.addListener((source, method, params) => {
  if (!work || source.tabId !== work.attachedTabId) return;
  if (method === "Page.javascriptDialogOpening") {
    work.dialog = { type: String(params.type || "alert").slice(0, 20), message: String(params.message || "").slice(0, 1500) };
  } else if (method === "Page.javascriptDialogClosed") {
    work.dialog = null;
  }
});

chrome.debugger.onDetach.addListener((source, reason) => {
  if (!work || source.tabId !== work.attachedTabId) return;
  work.attachedTabId = null;
  work.dialog = null;
  // Cancel on Chrome's "started debugging" bar is the person saying stop.
  if (reason === "canceled_by_user") void stopBrowserTask();
});

async function stopBrowserTask() {
  if (!work) return false;
  try {
    await runApi(`${taskPath(work.taskId)}/stop`, { method: "POST" });
    return true;
  } catch {
    return false;
  }
}

async function pointer(type, x, y, extra = {}) {
  const zoom = await chrome.tabs.getZoom(work.tabId).catch(() => 1);
  await cdpInput("Input.dispatchMouseEvent", { type, x: x * zoom, y: y * zoom, ...extra });
}

async function pressKey(parsed) {
  const { event, shift } = parsed;
  const common = {
    key: event.key, code: event.code, windowsVirtualKeyCode: event.keyCode, nativeVirtualKeyCode: event.keyCode,
    modifiers: shift ? 8 : 0,
  };
  await cdpInput("Input.dispatchKeyEvent", { type: event.text ? "keyDown" : "rawKeyDown", ...common, ...(event.text ? { text: event.text } : {}) });
  await cdpInput("Input.dispatchKeyEvent", { type: "keyUp", ...common });
}

/** Wait for a navigation to finish and for the page to stop changing. */
async function settlePage() {
  await sleep(150);
  if (work.dialog || work.tabId == null) return;
  try {
    await waitForTabLoad(work.tabId);
  } catch {
    return; // still loading after the limit: the next outline says what is there
  }
  if (work.dialog) return;
  try {
    await injectDriver();
    await driver(0, "settle", [4_000, 350]);
  } catch {
    /* a page that can't be scripted (it navigated away): the next outline reports it */
  }
}

// --- Performing one action --------------------------------------------------------------------

/** What has the keyboard focus, in whichever frame has it. */
async function focusedControl() {
  try {
    const frames = await runInFrames({ allFrames: true }, () => window.__mindloomDriver?.focusInfo());
    const focused = frames.map((frame) => frame.result).filter((info) => info?.focused);
    return focused.find((info) => info.hasFocus) || focused[0] || null;
  } catch {
    return null;
  }
}

/** Find the control for a ref and apply the checks every pointer action shares. */
async function locateForAction(action) {
  const ref = String(action.args?.ref || "");
  const entry = work.refs.get(ref);
  if (!entry) return { failure: failed(`No element ${ref || "(none)"} on the latest page. Use a ref from it.`) };
  const located = await driver(entry.frameId, "locate", [entry.n, entry.generation]);
  if (!located?.ok) return { failure: failed(located?.error || "That control could not be found again.") };
  return { entry, located };
}

async function clickControl(action) {
  const { entry, located, failure } = await locateForAction(action);
  if (failure) return failure;
  if (action.expect_label != null && !sameText(located.label, String(action.expect_label).slice(0, 100))) {
    return failed(`That control now says “${located.label}”, not “${action.expect_label}”. Read the page again.`);
  }
  if (looksIrreversible(located.label) && !action.approved) {
    return failed(`“${located.label}” looks like it can't be undone`, { blocked: "needs_approval" });
  }
  if (located.disabled) return failed(`“${located.label}” is disabled. Something else may need filling in first.`);
  if (located.covered) return failed(`Something is in front of “${located.label}”: ${located.covered}. Close it or scroll first.`);

  // A real pointer event when it lands on the control; the DOM click otherwise.
  let real = false;
  if (located.x != null) {
    await driver(entry.frameId, "armProbe", [entry.n, entry.generation]);
    await pointer("mouseMoved", located.x, located.y);
    await sleep(80);
    real = (await driver(entry.frameId, "probeHit")) === true;
    if (real) {
      await pointer("mousePressed", located.x, located.y, { button: "left", buttons: 1, clickCount: 1 });
      await pointer("mouseReleased", located.x, located.y, { button: "left", buttons: 0, clickCount: 1 });
    }
  }
  if (!real) {
    const clicked = await driver(entry.frameId, "domClick", [entry.n, entry.generation]);
    if (!clicked?.ok && !work.dialog) return failed(clicked?.error || "The click did not go through.");
  }
  return done(`Clicked “${located.label || located.role}”.`);
}

async function setValue(action) {
  const text = String(action.args?.text ?? "");
  const { entry, located, failure } = await locateForAction(action);
  if (failure) return failure;
  if (located.secure) return failed("That is a password, code or card field. The person fills it in.", { blocked: "secure_field" });
  const at = [entry.n, entry.generation];
  const focus = await driver(entry.frameId, "focusForTyping", at);
  if (focus?.secure) return failed(focus.error, { blocked: "secure_field" });
  if (focus?.error) return failed(focus.error);

  const shows = (value) => sameText(value, text) || (text && String(value || "").toLowerCase().includes(text.toLowerCase()));
  let read = { ok: false };
  if (focus?.ok) {
    // Typed as real input, which replaces the selection: frameworks see every change.
    if (text) await cdpInput("Input.insertText", { text });
    else if (!focus.empty) await pressKey(parseCombo("delete"));
    read = await driver(entry.frameId, "readValue", at);
  }
  if (!read?.ok || !shows(read.value)) {
    const filled = await driver(entry.frameId, "fillValue", [...at, text]);
    if (filled?.secure) return failed(filled.error, { blocked: "secure_field" });
    if (!filled?.ok) return failed(filled?.error || "The field did not take the text.");
    read = filled;
  }
  if (text && !String(read.value || "").trim()) {
    return failed(`“${located.label}” is still empty: it did not accept “${text.slice(0, 80)}” (check the format it expects).`);
  }
  return done(
    shows(read.value)
      ? `“${located.label}” now shows “${String(read.value).slice(0, 200)}”.`
      : `“${located.label}” shows “${String(read.value).slice(0, 200)}” after entering “${text.slice(0, 80)}”: the page reformatted or changed it, check it is right.`,
  );
}

async function pressKeyAction(action) {
  const parsed = parseCombo(action.args?.combo);
  if (!parsed) return failed("That key is not one MindLoom presses.");
  const focus = await focusedControl();
  if ((parsed.name === "return" || parsed.name === "space") && focus && !action.approved) {
    const onButton = ["button", "link", "menuitem"].includes(focus.role) && looksIrreversible(focus.label);
    const submits = parsed.name === "return" && focus.editable && looksIrreversible(focus.submitLabel);
    if (onButton || submits) {
      return failed(`${parsed.name === "return" ? "Enter" : "Space"} would press “${onButton ? focus.label : focus.submitLabel}”`, { blocked: "needs_approval" });
    }
  }
  if (focus?.secure && parsed.name !== "tab" && parsed.name !== "escape") {
    return failed("The focus is in a password, code or card field. The person fills it in.", { blocked: "secure_field" });
  }
  await pressKey(parsed);
  return done(`Pressed ${parsed.shift ? "shift+" : ""}${parsed.name}.`);
}

async function openUrl(action) {
  const url = String(action.args?.url || "").trim();
  const refusal = await refusalFor(url);
  if (refusal) return failed(refusal, { blocked: "site_not_allowed" });
  if (work.tabId != null && !(await chrome.tabs.get(work.tabId).catch(() => null))) work.tabId = null;
  if (work.tabId == null) {
    let windowId;
    try {
      windowId = (await chrome.windows.getLastFocused({ windowTypes: ["normal"] })).id;
    } catch {
      windowId = (await chrome.windows.create({})).id;
    }
    const tab = await chrome.tabs.create({ windowId, url, active: true });
    work.tabId = tab.id;
    await saveWork();
  } else {
    await chrome.tabs.update(work.tabId, { url, active: true });
  }
  await sleep(300);
  if (work.dialog) return done("The page asks before leaving: answer the dialog.");
  const tab = await waitForTabLoad(work.tabId).catch(() => null);
  return tab ? done(`Opened “${String(tab.title || "").slice(0, 120)}” (${String(tab.url || "").slice(0, 300)}).`) : done("The page is still loading; wait and look again.");
}

async function switchTab(action) {
  const tabId = work.tabRefs.get(String(action.args?.tab || ""));
  const tab = tabId == null ? null : await chrome.tabs.get(tabId).catch(() => null);
  if (!tab) return failed("That tab is not open any more.");
  const refusal = await refusalFor(tab.url || "");
  if (refusal) return failed(refusal, { blocked: "site_not_allowed" });
  await detachTaskDebugger();
  work.tabId = tab.id;
  work.dialog = null;
  await saveWork();
  await chrome.tabs.update(tab.id, { active: true });
  return done(`Now working in the tab “${String(tab.title || "").slice(0, 120)}”.`);
}

async function perform(action) {
  const tool = action.tool;
  const args = action.args || {};
  if (tool === "open_url") return openUrl(action);
  if (tool === "switch_tab") return switchTab(action);
  if (tool === "wait") {
    await sleep(Math.min(Math.max(Number(args.seconds) || 1, 0.2), 15) * 1000);
    return done("Waited.");
  }

  const tab = work.tabId == null ? null : await chrome.tabs.get(work.tabId).catch(() => null);
  if (!tab) return failed("No page is open in the working tab. Use open_url.");
  if (tool === "go_back") {
    await chrome.tabs.goBack(tab.id).catch(() => {});
    await settlePage();
    return done("Went back one page.");
  }
  // Everything below touches the page: it must still be on an allowed site.
  const refusal = await refusalFor(tab.url || "");
  if (refusal) return failed(refusal, { blocked: "site_not_allowed" });
  await ensureDebugger(tab.id);

  if (tool === "answer_dialog") {
    if (!work.dialog) return failed("No dialog is open.");
    const accept = args.accept === true;
    if (accept && work.dialog.type !== "alert" && !action.approved) {
      return failed(`The page asks “${work.dialog.message.slice(0, 200)}” and OK may not be undoable`, { blocked: "needs_approval" });
    }
    await cdp("Page.handleJavaScriptDialog", { accept }).catch(() => {});
    work.dialog = null;
    await settlePage();
    return done(`Answered the dialog with ${accept ? "OK" : "Cancel"}.`);
  }
  if (work.dialog) return failed("A dialog is open. Answer it with answer_dialog first.");

  const tabsBefore = new Set((await chrome.tabs.query({})).map((item) => item.id));
  let result;
  if (tool === "press") result = await clickControl(action);
  else if (tool === "set_value") result = await setValue(action);
  else if (tool === "key") result = await pressKeyAction(action);
  else if (tool === "hover") {
    const { located, failure } = await locateForAction(action);
    if (failure) return failure;
    if (located.x == null) return failed("That control is inside an embedded page MindLoom can't point at. Press it instead.");
    await pointer("mouseMoved", located.x, located.y);
    result = done(`Pointing at “${located.label || located.role}”.`);
  } else if (tool === "type_text") {
    const focus = await focusedControl();
    if (focus?.secure) return failed("The focus is in a password, code or card field. The person fills it in.", { blocked: "secure_field" });
    if (!focus?.editable) return failed("No text field has the focus. Press the field first, or use set_value with its ref.");
    await cdpInput("Input.insertText", { text: String(args.text ?? "") });
    result = done(`Typed “${String(args.text ?? "").slice(0, 120)}”.`);
  } else if (tool === "select_option" || tool === "read" || tool === "scroll") {
    const entry = args.ref ? work.refs.get(String(args.ref)) : null;
    if (args.ref && !entry) return failed(`No element ${args.ref} on the latest page. Use a ref from it.`);
    if (tool === "select_option") {
      const chosen = await driver(entry.frameId, "chooseOption", [entry.n, entry.generation, String(args.option || "")]);
      result = chosen?.ok ? done(`It now shows “${chosen.value}”.`) : failed(chosen?.error || "The choice could not be made.");
    } else if (tool === "read") {
      const read = await driver(entry.frameId, "readText", [entry.n, entry.generation, READ_LIMIT]);
      if (read?.secure) return failed(read.error, { blocked: "secure_field" });
      return read?.ok
        ? done(`Read ${read.lines} lines${read.cut ? " (cut short: scroll and read again for the rest)" : ""}.`, { text: read.text })
        : failed(read?.error || "That could not be read.");
    } else {
      const moved = await driver(entry?.frameId ?? 0, "scrollBy", [entry?.n ?? null, entry?.generation ?? 0, String(args.direction)]);
      if (!moved?.ok) return failed(moved?.error || "Could not scroll.");
      result = done(moved.moved ? `Scrolled ${args.direction}${moved.atBottom ? " to the end" : moved.atTop ? " to the start" : ""}.` : `Already at the ${args.direction === "up" || args.direction === "top" ? "start" : "end"}: nothing more that way.`);
    }
  } else {
    return failed(`MindLoom's extension does not know the action ${tool}. Update the extension.`);
  }

  if (result.ok) {
    await settlePage();
    const opened = (await chrome.tabs.query({})).filter((item) => !tabsBefore.has(item.id) && item.openerTabId === work.tabId);
    if (opened.length) result.detail += " A new tab opened: it is listed under open tabs if its site is allowed.";
    if (work.dialog) result.detail += " A dialog opened.";
  }
  return result;
}

// --- The popup: what is running, and Stop ------------------------------------------------------

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!fromExtensionPage(sender)) return false;
  if (message.type === "GET_BROWSER_TASK") {
    sendResponse(work ? { active: true, status: work.status, sites: work.allowed } : { active: false });
    return false;
  }
  if (message.type === "STOP_BROWSER_TASK") {
    stopBrowserTask().then((ok) => sendResponse({ ok }));
    return true;
  }
  return false;
});

// setTimeout chains end when the service worker sleeps; the alarm restarts polling.
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === TASK_ALARM) void pollBrowserTasks();
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && (changes.accessToken || changes.apiBase)) scheduleTaskPoll(0);
});

chrome.alarms.create(TASK_ALARM, { periodInMinutes: 0.5 });
scheduleTaskPoll(0);
