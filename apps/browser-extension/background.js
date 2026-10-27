const STORAGE_KEY = "captureEnabled";
const SESSION_KEY = "captureSessionId";
const LAST_SESSION_KEY = "lastCaptureSessionId";
const PENDING_KEY = "pendingCaptures";
const APPROVED_KEY = "approvedCaptures";
const API_BASE_KEY = "apiBase";
const ACCESS_TOKEN_KEY = "accessToken";
const CAPTURE_INTERVAL_MS = 10_000;
const TAB_CAPTURE_DEBOUNCE_MS = 3_000;
const NAVIGATION_SETTLE_MS = 1_200;
const CAPTURE_RETRY_MS = 600;
const MAX_CAPTURES = 50;
const DEFAULT_API_BASE = "http://localhost:8000";

/** @type {{ tabId: number | null, url: string, title: string, windowId: number | null }} */
let activeTabState = {
  tabId: null,
  url: "",
  title: "",
  windowId: null,
};

/** @type {Array<{ id: string, timestamp: number, dataUrl: string, url: string, tabTitle: string, windowId: number }>} */
let pendingCaptures = [];

/** @type {Array<{ id: string, timestamp: number, dataUrl: string, url: string, tabTitle: string, windowId: number }>} */
let approvedCaptures = [];

let captureIntervalId = null;
let captureInProgress = false;
let lastTabChangeCaptureTime = 0;
/** An anchored capture that arrived mid-capture; anchors are not dropped. */
let deferredAnchorCapture = null;
/** Last origin+path seen per tab, so only real page changes count as navigation. */
const lastPageByTab = new Map();
let navigationTimer = null;

async function persistQueues() {
  await chrome.storage.local.set({
    [PENDING_KEY]: pendingCaptures,
    [APPROVED_KEY]: approvedCaptures,
  });
}

async function restoreQueues() {
  const stored = await chrome.storage.local.get([PENDING_KEY, APPROVED_KEY]);
  pendingCaptures = Array.isArray(stored[PENDING_KEY]) ? stored[PENDING_KEY] : [];
  approvedCaptures = Array.isArray(stored[APPROVED_KEY]) ? stored[APPROVED_KEY] : [];
  updatePendingBadge();
}

async function getSessionId() {
  const stored = await chrome.storage.local.get([SESSION_KEY, LAST_SESSION_KEY]);
  return stored[SESSION_KEY] || stored[LAST_SESSION_KEY] || "";
}

async function getCaptureEnabled() {
  const result = await chrome.storage.local.get(STORAGE_KEY);
  return Boolean(result[STORAGE_KEY]);
}

function nextCaptureId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function logTabEvent(event, state) {
  console.log(`[MindLoom Capture] ${event}:`, {
    tabId: state.tabId,
    url: state.url,
    title: state.title,
    windowId: state.windowId,
  });
}

/** Origin and path only: what a BrowserAnchor stores for a page. */
function originAndPath(url) {
  try {
    const { origin, pathname } = new URL(url);
    return `${origin}${pathname}`;
  } catch {
    return "";
  }
}

function isCapturableUrl(url) {
  if (!url) return false;
  try {
    const { protocol } = new URL(url);
    return protocol === "http:" || protocol === "https:";
  } catch {
    return false;
  }
}

function updatePendingBadge() {
  const count = pendingCaptures.length;
  const text = count > 0 ? String(count) : "";
  chrome.action.setBadgeText({ text });
  if (count > 0) {
    chrome.action.setBadgeBackgroundColor({ color: "#dc2626" });
  }
}

function trimPendingQueue() {
  if (pendingCaptures.length > MAX_CAPTURES) {
    pendingCaptures.length = MAX_CAPTURES;
  }
}

function addCapture(capture) {
  pendingCaptures.unshift(capture);
  trimPendingQueue();
  updatePendingBadge();
  void persistQueues();
}

function removeFromApprovedQueue(id) {
  const index = approvedCaptures.findIndex((c) => c.id === id);
  if (index === -1) return false;
  approvedCaptures.splice(index, 1);
  void persistQueues();
  return true;
}

async function getApiConfig() {
  const stored = await chrome.storage.local.get([API_BASE_KEY, ACCESS_TOKEN_KEY]);
  const apiBase = String(stored[API_BASE_KEY] || DEFAULT_API_BASE).replace(/\/$/, "");
  const accessToken = String(stored[ACCESS_TOKEN_KEY] || "").trim();
  return { apiBase, accessToken };
}

async function authHeaders() {
  const { accessToken } = await getApiConfig();
  if (!accessToken) {
    throw new Error(
      "Missing MindLoom access token. Open the extension popup and paste your MindLoom access token.",
    );
  }
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${accessToken}`,
  };
}

async function postCaptureToBackend(capture) {
  const { apiBase } = await getApiConfig();
  const response = await fetch(`${apiBase}/captures`, {
    method: "POST",
    headers: await authHeaders(),
    body: JSON.stringify({
      id: capture.id,
      timestamp: capture.timestamp,
      dataUrl: capture.dataUrl,
      url: capture.url,
      tabTitle: capture.tabTitle,
      browserAnchor: capture.browserAnchor || null,
      windowId: capture.windowId,
      sessionId: capture.sessionId,
      note: capture.note || "",
      redactions: capture.redactions || [],
    }),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`POST ${response.status}: ${text}`);
  }

  return response.json();
}

async function uploadApprovedCapture(capture) {
  try {
    await postCaptureToBackend(capture);
  } catch (firstErr) {
    try {
      await postCaptureToBackend(capture);
    } catch (retryErr) {
      const message =
        retryErr instanceof Error ? retryErr.message : String(retryErr);
      console.warn(
        "[MindLoom Capture] Upload failed after retry, left in approved queue:",
        capture.id,
        message
      );
      return false;
    }
  }

  removeFromApprovedQueue(capture.id);
  console.log("[MindLoom Capture] Uploaded to backend:", capture.id);
  return true;
}

function approveCapture(id) {
  const index = pendingCaptures.findIndex((c) => c.id === id);
  if (index === -1) return false;

  const [capture] = pendingCaptures.splice(index, 1);
  approvedCaptures.unshift(capture);
  updatePendingBadge();
  void persistQueues();
  console.log("[MindLoom Capture] Capture approved:", capture.id);
  uploadApprovedCapture(capture);
  return true;
}

function rejectCapture(id) {
  const index = pendingCaptures.findIndex((c) => c.id === id);
  if (index === -1) return false;

  pendingCaptures.splice(index, 1);
  updatePendingBadge();
  void persistQueues();
  console.log("[MindLoom Capture] Capture rejected:", id);
  return true;
}

function approveAllCaptures() {
  if (pendingCaptures.length === 0) return 0;

  const toUpload = [...pendingCaptures];
  approvedCaptures.unshift(...pendingCaptures);
  pendingCaptures.length = 0;
  updatePendingBadge();
  void persistQueues();
  console.log("[MindLoom Capture] Approved all pending captures:", toUpload.length);
  for (const capture of toUpload) {
    uploadApprovedCapture(capture);
  }
  return toUpload.length;
}

async function retryApprovedUploads() {
  const toRetry = [...approvedCaptures];
  let uploaded = 0;
  for (const capture of toRetry) {
    if (await uploadApprovedCapture(capture)) {
      uploaded += 1;
    }
  }
  return uploaded;
}

async function syncCaptureSchedule() {
  if (captureIntervalId !== null) {
    clearInterval(captureIntervalId);
    captureIntervalId = null;
  }

  const enabled = await getCaptureEnabled();
  if (!enabled) return;
  const stored = await chrome.storage.local.get(SESSION_KEY);
  if (!stored[SESSION_KEY]) {
    const sessionId = `session-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    await chrome.storage.local.set({
      [SESSION_KEY]: sessionId,
      [LAST_SESSION_KEY]: sessionId,
    });
  }

  captureIntervalId = setInterval(() => {
    takeCapture("interval");
  }, CAPTURE_INTERVAL_MS);
  takeCapture("enabled");
}

/**
 * Screenshot the active tab. `browserAnchor` is the control a click activated, or the
 * page a navigation landed on; it travels with the screenshot to the API.
 */
async function takeCapture(reason, browserAnchor = null) {
  if (!(await getCaptureEnabled())) return;

  if (captureInProgress) {
    if (browserAnchor) {
      deferredAnchorCapture = { reason, browserAnchor };
      return;
    }
    console.log("[MindLoom Capture] Capture skipped (in progress):", reason);
    return;
  }

  captureInProgress = true;

  try {
    let tab;
    try {
      [tab] = await chrome.tabs.query({
        active: true,
        lastFocusedWindow: true,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.warn("[MindLoom Capture] Capture skipped (tab query failed):", reason, message);
      return;
    }

    if (!tab || tab.windowId === undefined) {
      console.log("[MindLoom Capture] Capture skipped (no active tab):", reason);
      return;
    }

    if (!isCapturableUrl(tab.url)) {
      console.log(
        "[MindLoom Capture] Capture skipped (restricted URL):",
        tab.url || "(empty)"
      );
      return;
    }

    if (!(await getCaptureEnabled())) return;

    let dataUrl;
    for (let attempt = 0; !dataUrl; attempt += 1) {
      try {
        dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, {
          format: "png",
        });
      } catch (err) {
        // Chrome allows ~2 captures a second; an anchored capture waits once and retries.
        if (browserAnchor && attempt === 0) {
          await new Promise((resolve) => setTimeout(resolve, CAPTURE_RETRY_MS));
          continue;
        }
        const message = err instanceof Error ? err.message : String(err);
        console.warn("[MindLoom Capture] Capture failed (skipped):", reason, message);
        return;
      }
    }

    if (!(await getCaptureEnabled())) return;

    const capture = {
      id: nextCaptureId(),
      timestamp: Date.now(),
      dataUrl,
      url: tab.url || "",
      tabTitle: tab.title || "",
      browserAnchor,
      windowId: tab.windowId,
      sessionId: await getSessionId(),
      note: "",
      redactions: [],
    };

    addCapture(capture);
    console.log("[MindLoom Capture] Capture saved (pending):", {
      id: capture.id,
      reason,
      url: capture.url,
      pendingCount: pendingCaptures.length,
    });
  } finally {
    captureInProgress = false;
    if (deferredAnchorCapture) {
      const next = deferredAnchorCapture;
      deferredAnchorCapture = null;
      void takeCapture(next.reason, next.browserAnchor);
    }
  }
}

/** A top-frame page change in the active tab; captured once the page settles. */
function noteNavigation(tab) {
  if (!tab.active || !isCapturableUrl(tab.url)) return;
  const page = originAndPath(tab.url);
  if (!page || lastPageByTab.get(tab.id) === page) return;
  lastPageByTab.set(tab.id, page);
  clearTimeout(navigationTimer);
  navigationTimer = setTimeout(() => {
    void takeCapture("navigation", { url: page, role: "document", name: "", selector: "", needsValue: false });
  }, NAVIGATION_SETTLE_MS);
}

async function maybeCaptureOnTabChange() {
  if (!(await getCaptureEnabled())) return;

  const now = Date.now();
  if (now - lastTabChangeCaptureTime < TAB_CAPTURE_DEBOUNCE_MS) {
    console.log("[MindLoom Capture] Tab-change capture debounced");
    return;
  }

  lastTabChangeCaptureTime = now;
  takeCapture("tab-change");
}

function updateActiveTab(tab) {
  if (!tab || tab.id === undefined) return;

  const prev = { ...activeTabState };
  const next = {
    tabId: tab.id,
    url: tab.url || "",
    title: tab.title || "",
    windowId: tab.windowId ?? null,
  };

  const tabChanged = prev.tabId !== next.tabId;
  const urlChanged = prev.tabId === next.tabId && prev.url !== next.url;
  const titleOnlyChange =
    prev.tabId === next.tabId &&
    prev.url === next.url &&
    prev.title !== next.title;

  if (!tabChanged && !urlChanged && !titleOnlyChange) return;

  activeTabState = next;

  if (tabChanged) {
    logTabEvent("Active tab changed", activeTabState);
    maybeCaptureOnTabChange();
  } else if (urlChanged) {
    logTabEvent("Active tab URL changed", activeTabState);
  } else {
    logTabEvent("Active tab title updated", activeTabState);
  }
}

async function refreshActiveTab() {
  try {
    const [tab] = await chrome.tabs.query({
      active: true,
      lastFocusedWindow: true,
    });
    if (tab) updateActiveTab(tab);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.warn("[MindLoom Capture] Failed to query active tab:", message);
  }
}

chrome.tabs.onActivated.addListener(async (activeInfo) => {
  try {
    const tab = await chrome.tabs.get(activeInfo.tabId);
    updateActiveTab(tab);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.warn("[MindLoom Capture] Failed to get activated tab:", message);
  }
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (!tab.active) return;

  const isTrackedTab =
    activeTabState.tabId === null || tabId === activeTabState.tabId;
  if (!isTrackedTab) return;

  if (changeInfo.url || changeInfo.title || changeInfo.status === "complete") {
    updateActiveTab(tab);
  }
  if (changeInfo.url || changeInfo.status === "complete") {
    noteNavigation(tab);
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  lastPageByTab.delete(tabId);
});

chrome.windows.onFocusChanged.addListener((windowId) => {
  if (windowId !== chrome.windows.WINDOW_ID_NONE) {
    refreshActiveTab();
  }
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local" || !changes[STORAGE_KEY]) return;
  syncCaptureSchedule();
});

chrome.runtime.onStartup.addListener(() => {
  syncCaptureSchedule();
  refreshActiveTab();
  updatePendingBadge();
});

chrome.runtime.onInstalled.addListener(() => {
  syncCaptureSchedule();
  refreshActiveTab();
  updatePendingBadge();
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "BROWSER_ANCHOR") {
    // Only the top frame of the tab being captured records anchors.
    if (sender.tab?.active && sender.frameId === 0 && message.anchor) {
      void takeCapture("click", message.anchor);
    }
    return false;
  }

  if (message.type === "GET_ACTIVE_TAB") {
    sendResponse({ ...activeTabState });
    return false;
  }

  if (message.type === "GET_CAPTURES") {
    getCaptureEnabled().then(async (captureEnabled) => {
      sendResponse({
        pending: pendingCaptures.map((c) => ({ ...c })),
        approved: approvedCaptures.map((c) => ({ ...c })),
        captureEnabled,
        sessionId: await getSessionId(),
      });
    });
    return true;
  }

  if (message.type === "UPDATE_CAPTURE") {
    const capture = pendingCaptures.find((item) => item.id === message.id);
    if (!capture) {
      sendResponse({ ok: false });
      return false;
    }
    capture.dataUrl = message.dataUrl || capture.dataUrl;
    capture.note = message.note || "";
    capture.redactions = message.redactions || [];
    void persistQueues();
    sendResponse({ ok: true });
    return false;
  }

  if (message.type === "ANALYZE_SESSION") {
    getApiConfig()
      .then(async ({ apiBase }) => {
        const response = await fetch(
          `${apiBase}/captures/sessions/${encodeURIComponent(message.sessionId)}/analyze`,
          {
            method: "POST",
            headers: await authHeaders(),
          },
        );
        const body = await response.json().catch(() => ({}));
        sendResponse(
          response.ok
            ? { ok: true, skill: body }
            : { ok: false, error: body.detail || `HTTP ${response.status}` },
        );
      })
      .catch((error) => sendResponse({ ok: false, error: String(error) }));
    return true;
  }

  if (message.type === "APPROVE_CAPTURE") {
    sendResponse({ ok: approveCapture(message.id) });
    return false;
  }

  if (message.type === "REJECT_CAPTURE") {
    sendResponse({ ok: rejectCapture(message.id) });
    return false;
  }

  if (message.type === "APPROVE_ALL_CAPTURES") {
    sendResponse({ count: approveAllCaptures() });
    return false;
  }

  if (message.type === "RETRY_APPROVED_UPLOADS") {
    retryApprovedUploads().then((uploaded) => {
      sendResponse({ uploaded });
    });
    return true;
  }

  return false;
});

syncCaptureSchedule();
refreshActiveTab();
restoreQueues();

importScripts("run-client.js");
