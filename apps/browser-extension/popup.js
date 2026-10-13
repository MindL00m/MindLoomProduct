const STORAGE_KEY = "captureEnabled";
const API_BASE_KEY = "apiBase";
const ACCESS_TOKEN_KEY = "accessToken";
const DEFAULT_API_BASE = "http://localhost:8000";

const tabIdEl = document.getElementById("tab-id");
const tabTitleEl = document.getElementById("tab-title");
const tabUrlEl = document.getElementById("tab-url");
const captureToggle = document.getElementById("capture-toggle");
const captureStatus = document.getElementById("capture-status");
const viewCapturesBtn = document.getElementById("view-captures");
const apiBaseInput = document.getElementById("api-base");
const accessTokenInput = document.getElementById("access-token");
const saveAuthBtn = document.getElementById("save-auth");
const authStatus = document.getElementById("auth-status");

function setCaptureUi(enabled) {
  captureToggle.checked = enabled;
  captureStatus.textContent = enabled ? "ON" : "OFF";
  captureStatus.classList.toggle("on", enabled);
}

function renderActiveTab(tab) {
  tabIdEl.textContent =
    tab.tabId !== null && tab.tabId !== undefined ? String(tab.tabId) : "—";
  tabTitleEl.textContent = tab.title || "—";
  tabUrlEl.textContent = tab.url || "—";
}

async function loadActiveTab() {
  try {
    const tab = await chrome.runtime.sendMessage({ type: "GET_ACTIVE_TAB" });
    if (tab) renderActiveTab(tab);
  } catch (err) {
    console.error("[MindLoom Capture] Failed to load active tab:", err);
  }
}

async function loadCaptureState() {
  const result = await chrome.storage.local.get(STORAGE_KEY);
  setCaptureUi(Boolean(result[STORAGE_KEY]));
}

async function loadAuthSettings() {
  const stored = await chrome.storage.local.get([API_BASE_KEY, ACCESS_TOKEN_KEY]);
  apiBaseInput.value = stored[API_BASE_KEY] || DEFAULT_API_BASE;
  accessTokenInput.value = stored[ACCESS_TOKEN_KEY] || "";
  authStatus.textContent = stored[ACCESS_TOKEN_KEY]
    ? "Token saved — uploads authenticated."
    : "Token required before uploads.";
  authStatus.classList.toggle("on", Boolean(stored[ACCESS_TOKEN_KEY]));
}

captureToggle.addEventListener("change", async () => {
  const enabled = captureToggle.checked;
  if (enabled) {
    const sessionId = `session-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    await chrome.storage.local.set({
      captureSessionId: sessionId,
      lastCaptureSessionId: sessionId,
    });
  } else {
    await chrome.storage.local.remove("captureSessionId");
  }
  await chrome.storage.local.set({ [STORAGE_KEY]: enabled });
  setCaptureUi(enabled);
});

saveAuthBtn.addEventListener("click", async () => {
  const apiBase = apiBaseInput.value.trim().replace(/\/$/, "") || DEFAULT_API_BASE;
  const accessToken = accessTokenInput.value.trim();
  await chrome.storage.local.set({
    [API_BASE_KEY]: apiBase,
    [ACCESS_TOKEN_KEY]: accessToken,
  });
  authStatus.textContent = accessToken
    ? "Saved. Captures will use Bearer auth."
    : "Saved API base, but token is still empty.";
  authStatus.classList.toggle("on", Boolean(accessToken));
});

viewCapturesBtn.addEventListener("click", () => {
  chrome.tabs.create({ url: chrome.runtime.getURL("captures.html") });
});

loadActiveTab();
loadCaptureState();
loadAuthSettings();
