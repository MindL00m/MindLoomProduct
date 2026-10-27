// Skill run client. The Mac app owns the walk; this only performs browser steps. While
// the signed-in user has a running run whose current step has a browser_anchor, find
// that control in the matching tab and click it through chrome.debugger, then report
// through the advance route. It never types and never picks a different control.
// Loaded into the service worker by background.js (uses getApiConfig, authHeaders,
// originAndPath from there).

const RUN_POLL_ACTIVE_MS = 1_500;
const RUN_POLL_IDLE_MS = 10_000;
const RUN_ALARM = "mindloom-skill-runs";
const PAGE_LOAD_TIMEOUT_MS = 15_000;
const DEBUGGER_SETTLE_MS = 300;
const ACTIVE_RUN_STATUSES = new Set(["pending", "running", "waiting"]);
const MAX_REMEMBERED_STEPS = 50;

let runPollTimer = null;
let runPolling = false;
/** Tab the debugger is attached to, and the run step it was attached for. */
let debuggerTarget = null;
/** `${runId}:${stepIndex}` → report. A step is clicked at most once; reports are retried. */
const stepReports = new Map();

class StaleStepError extends Error {}

async function runApi(path, init = {}) {
  const { apiBase } = await getApiConfig();
  const response = await fetch(`${apiBase}${path}`, { ...init, headers: await authHeaders() });
  if (!response.ok) {
    const error = new Error(`${init.method || "GET"} ${path}: HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function scheduleRunPoll(delay) {
  clearTimeout(runPollTimer);
  runPollTimer = setTimeout(pollSkillRuns, delay);
}

async function pollSkillRuns() {
  if (runPolling) return;
  runPolling = true;
  let delay = RUN_POLL_IDLE_MS;
  try {
    const { accessToken } = await getApiConfig();
    if (!accessToken) {
      await detachRunDebugger();
      return;
    }
    const runs = await runApi("/captures/skill-runs");
    const active = runs.filter((run) => ACTIVE_RUN_STATUSES.has(run.status));
    if (active.length) delay = RUN_POLL_ACTIVE_MS;

    let browserStep = null;
    for (const summary of active.filter((run) => run.status === "running")) {
      const run = await runApi(`/captures/skill-runs/${encodeURIComponent(summary.run_id)}`);
      const anchor = run.status === "running" ? run.guide?.steps?.[run.step_index]?.browser_anchor : null;
      if (!anchor) continue;
      browserStep = `${run.run_id}:${run.step_index}`;
      await performBrowserStep(run, anchor, browserStep);
      break;
    }
    if (debuggerTarget && debuggerTarget.step !== browserStep) {
      await detachRunDebugger();
    }
  } catch (error) {
    console.warn("[MindLoom Run]", error instanceof Error ? error.message : String(error));
  } finally {
    runPolling = false;
    scheduleRunPoll(delay);
  }
}

async function performBrowserStep(run, anchor, step) {
  if (!stepReports.has(step)) {
    let report;
    try {
      report = await browserStepReport(run, anchor, step);
    } catch (error) {
      if (error instanceof StaleStepError) return; // stopped or moved on: do nothing
      throw error;
    }
    stepReports.set(step, report);
    if (stepReports.size > MAX_REMEMBERED_STEPS) {
      stepReports.delete(stepReports.keys().next().value);
    }
  }
  try {
    await runApi(`/captures/skill-runs/${encodeURIComponent(run.run_id)}/advance`, {
      method: "POST",
      body: JSON.stringify({ step_index: run.step_index, ...stepReports.get(step) }),
    });
  } catch (error) {
    // 409: the Mac app or the person already moved the run; nothing to retry.
    if (error.status !== 409) throw error;
  }
}

function actionFailed(detail) {
  return { outcome: "waiting", wait_reason: "action_failed", error: String(detail).slice(0, 500) };
}

async function browserStepReport(run, anchor, step) {
  if (anchor.needsValue) return { outcome: "waiting", wait_reason: "needs_value" };
  let tab;
  try {
    tab = await tabForPage(anchor.url);
  } catch (error) {
    return actionFailed(error instanceof Error ? error.message : String(error));
  }
  // A page anchor has no control: being on the page is the step.
  if (!anchor.selector && !anchor.name) return { outcome: "done" };

  try {
    // Attach first: the debugging bar resizes the page, which can move the control.
    await attachRunDebugger(tab.id, step);
    const located = await locateInTab(tab.id, anchor);
    if (!located?.ok) return actionFailed(located?.error || "Couldn't find the control on this page.");
    if (located.needsValue) return { outcome: "waiting", wait_reason: "needs_value" };

    // Locating can take a moment; make sure the run is still on this step before clicking.
    const current = await runApi(`/captures/skill-runs/${encodeURIComponent(run.run_id)}`);
    if (current.status !== "running" || current.step_index !== run.step_index) {
      throw new StaleStepError();
    }
    await clickAt(tab.id, located.x, located.y);
    return { outcome: "done" };
  } catch (error) {
    if (error instanceof StaleStepError) throw error;
    return actionFailed(error instanceof Error ? error.message : String(error));
  }
}

/** A tab on this origin+path (activated), or that path opened in the current window. */
async function tabForPage(url) {
  const tabs = await chrome.tabs.query({});
  const matches = tabs.filter((tab) => originAndPath(tab.url || "") === url);
  let tab = matches.find((item) => item.active) || matches[0];
  if (tab) {
    await chrome.tabs.update(tab.id, { active: true });
    await chrome.windows.update(tab.windowId, { focused: true });
  } else {
    let windowId;
    try {
      windowId = (await chrome.windows.getLastFocused({ windowTypes: ["normal"] })).id;
    } catch {
      windowId = (await chrome.windows.create({ focused: true })).id;
    }
    tab = await chrome.tabs.create({ windowId, url, active: true });
  }
  const loaded = await waitForTabLoad(tab.id);
  if (originAndPath(loaded.url || "") !== url) {
    throw new Error(`Expected ${url}, but the tab is on ${originAndPath(loaded.url || "") || "another page"}.`);
  }
  return loaded;
}

async function waitForTabLoad(tabId) {
  const deadline = Date.now() + PAGE_LOAD_TIMEOUT_MS;
  for (;;) {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === "complete") return tab;
    if (Date.now() > deadline) throw new Error("The page did not finish loading.");
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
}

async function locateInTab(tabId, anchor) {
  const message = { type: "LOCATE_ANCHOR", anchor };
  try {
    return await chrome.tabs.sendMessage(tabId, message, { frameId: 0 });
  } catch {
    // Tabs opened before the extension loaded have no content script yet.
    await chrome.scripting.executeScript({ target: { tabId }, files: ["anchor.js"] });
    return chrome.tabs.sendMessage(tabId, message, { frameId: 0 });
  }
}

async function attachRunDebugger(tabId, step) {
  if (debuggerTarget?.tabId === tabId) {
    debuggerTarget.step = step;
    return;
  }
  await detachRunDebugger();
  await chrome.debugger.attach({ tabId }, "1.3");
  debuggerTarget = { tabId, step };
  await new Promise((resolve) => setTimeout(resolve, DEBUGGER_SETTLE_MS));
}

async function detachRunDebugger() {
  if (!debuggerTarget) return;
  const { tabId } = debuggerTarget;
  debuggerTarget = null;
  try {
    await chrome.debugger.detach({ tabId });
  } catch {
    /* already detached (tab closed or the person cancelled debugging) */
  }
}

async function clickAt(tabId, x, y) {
  const target = { tabId };
  const send = (params) => chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", params);
  await send({ type: "mouseMoved", x, y });
  await send({ type: "mousePressed", x, y, button: "left", buttons: 1, clickCount: 1 });
  await send({ type: "mouseReleased", x, y, button: "left", buttons: 0, clickCount: 1 });
}

chrome.debugger.onDetach.addListener((source) => {
  if (debuggerTarget?.tabId === source.tabId) debuggerTarget = null;
});

// setTimeout chains end when the service worker sleeps; the alarm restarts polling.
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === RUN_ALARM) void pollSkillRuns();
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && (changes.accessToken || changes.apiBase)) scheduleRunPoll(0);
});

chrome.alarms.create(RUN_ALARM, { periodInMinutes: 0.5 });
scheduleRunPoll(0);
