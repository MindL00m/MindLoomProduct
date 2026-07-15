const STORAGE_KEY = "captureEnabled";
const CAPTURE_INTERVAL_MS = 10_000;
const TAB_CAPTURE_DEBOUNCE_MS = 3_000;
const MAX_CAPTURES = 50;
const BACKEND_CAPTURES_URL = "http://localhost:3000/captures";

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

async function getCaptureEnabled() {
  const result = await chrome.storage.local.get(STORAGE_KEY);
  return Boolean(result[STORAGE_KEY]);
}

function nextCaptureId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function logTabEvent(event, state) {
  console.log(`[workstyle-capture] ${event}:`, {
    tabId: state.tabId,
    url: state.url,
    title: state.title,
    windowId: state.windowId,
  });
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
}

function removeFromApprovedQueue(id) {
  const index = approvedCaptures.findIndex((c) => c.id === id);
  if (index === -1) return false;
  approvedCaptures.splice(index, 1);
  return true;
}

async function postCaptureToBackend(capture) {
  const response = await fetch(BACKEND_CAPTURES_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      id: capture.id,
      timestamp: capture.timestamp,
      dataUrl: capture.dataUrl,
      url: capture.url,
      tabTitle: capture.tabTitle,
      windowId: capture.windowId,
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
        "[workstyle-capture] Upload failed after retry, left in approved queue:",
        capture.id,
        message
      );
      return false;
    }
  }

  removeFromApprovedQueue(capture.id);
  console.log("[workstyle-capture] Uploaded to backend:", capture.id);
  return true;
}

function approveCapture(id) {
  const index = pendingCaptures.findIndex((c) => c.id === id);
  if (index === -1) return false;

  const [capture] = pendingCaptures.splice(index, 1);
  approvedCaptures.unshift(capture);
  updatePendingBadge();
  console.log("[workstyle-capture] Capture approved:", capture.id);
  uploadApprovedCapture(capture);
  return true;
}

function rejectCapture(id) {
  const index = pendingCaptures.findIndex((c) => c.id === id);
  if (index === -1) return false;

  pendingCaptures.splice(index, 1);
  updatePendingBadge();
  console.log("[workstyle-capture] Capture rejected:", id);
  return true;
}

function approveAllCaptures() {
  if (pendingCaptures.length === 0) return 0;

  const toUpload = [...pendingCaptures];
  approvedCaptures.unshift(...pendingCaptures);
  pendingCaptures.length = 0;
  updatePendingBadge();
  console.log("[workstyle-capture] Approved all pending captures:", toUpload.length);
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

  captureIntervalId = setInterval(() => {
    takeCapture("interval");
  }, CAPTURE_INTERVAL_MS);
  takeCapture("enabled");
}

async function takeCapture(reason) {
  if (!(await getCaptureEnabled())) return;

  if (captureInProgress) {
    console.log("[workstyle-capture] Capture skipped (in progress):", reason);
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
      console.warn("[workstyle-capture] Capture skipped (tab query failed):", reason, message);
      return;
    }

    if (!tab || tab.windowId === undefined) {
      console.log("[workstyle-capture] Capture skipped (no active tab):", reason);
      return;
    }

    if (!isCapturableUrl(tab.url)) {
      console.log(
        "[workstyle-capture] Capture skipped (restricted URL):",
        tab.url || "(empty)"
      );
      return;
    }

    if (!(await getCaptureEnabled())) return;

    let dataUrl;
    try {
      dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, {
        format: "png",
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.warn("[workstyle-capture] Capture failed (skipped):", reason, message);
      return;
    }

    if (!(await getCaptureEnabled())) return;

    const capture = {
      id: nextCaptureId(),
      timestamp: Date.now(),
      dataUrl,
      url: tab.url || "",
      tabTitle: tab.title || "",
      windowId: tab.windowId,
    };

    addCapture(capture);
    console.log("[workstyle-capture] Capture saved (pending):", {
      id: capture.id,
      reason,
      url: capture.url,
      pendingCount: pendingCaptures.length,
    });
  } finally {
    captureInProgress = false;
  }
}

async function maybeCaptureOnTabChange() {
  if (!(await getCaptureEnabled())) return;

  const now = Date.now();
  if (now - lastTabChangeCaptureTime < TAB_CAPTURE_DEBOUNCE_MS) {
    console.log("[workstyle-capture] Tab-change capture debounced");
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
    console.warn("[workstyle-capture] Failed to query active tab:", message);
  }
}

chrome.tabs.onActivated.addListener(async (activeInfo) => {
  try {
    const tab = await chrome.tabs.get(activeInfo.tabId);
    updateActiveTab(tab);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.warn("[workstyle-capture] Failed to get activated tab:", message);
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

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === "GET_ACTIVE_TAB") {
    sendResponse({ ...activeTabState });
    return false;
  }

  if (message.type === "GET_CAPTURES") {
    getCaptureEnabled().then((captureEnabled) => {
      sendResponse({
        pending: pendingCaptures.map((c) => ({ ...c })),
        approved: approvedCaptures.map((c) => ({ ...c })),
        captureEnabled,
      });
    });
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
updatePendingBadge();
