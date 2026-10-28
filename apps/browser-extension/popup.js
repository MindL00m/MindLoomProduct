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
  try {
    const url = new URL(apiBase);
    const local = ["localhost", "127.0.0.1"].includes(url.hostname);
    if (url.protocol !== "https:" && !(url.protocol === "http:" && local)) throw new Error();
  } catch {
    authStatus.textContent = "The API base must be an https:// address (or localhost).";
    authStatus.classList.remove("on");
    return;
  }
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

// --- Sites MindLoom may click on when running a Skill File -------------------------

const RUN_SITES_KEY = "runSites";
const RUN_SITE_REQUESTS_KEY = "runSiteRequests";
const sitesList = document.getElementById("run-sites");

async function renderSites() {
  const stored = await chrome.storage.local.get([RUN_SITES_KEY, RUN_SITE_REQUESTS_KEY]);
  const allowed = stored[RUN_SITES_KEY] || [];
  const requests = (stored[RUN_SITE_REQUESTS_KEY] || []).filter((o) => !allowed.includes(o));
  let current = "";
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const origin = new URL(tab?.url || "").origin;
    if (origin.startsWith("https://") && !allowed.includes(origin)) current = origin;
  } catch {
    /* no usable tab */
  }
  sitesList.replaceChildren();
  const row = (text, buttonText, onClick) => {
    const item = document.createElement("div");
    item.className = "site-row";
    const label = document.createElement("span");
    label.textContent = text;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = buttonText;
    button.addEventListener("click", onClick);
    item.append(label, button);
    sitesList.append(item);
  };
  for (const origin of allowed) {
    row(origin, "Remove", async () => {
      await chrome.storage.local.set({ [RUN_SITES_KEY]: allowed.filter((o) => o !== origin) });
      void renderSites();
    });
  }
  for (const origin of [...new Set([...requests, ...(current ? [current] : [])])]) {
    row(`${origin} (not allowed)`, "Allow", async () => {
      await chrome.storage.local.set({
        [RUN_SITES_KEY]: [...allowed, origin],
        [RUN_SITE_REQUESTS_KEY]: requests.filter((o) => o !== origin),
      });
      void renderSites();
    });
  }
  if (!sitesList.childElementCount) {
    const empty = document.createElement("p");
    empty.className = "site-empty";
    empty.textContent = "No sites yet. Open a site and allow it here before running a skill on it.";
    sitesList.append(empty);
  }
}

loadActiveTab();
loadCaptureState();
loadAuthSettings();
void renderSites();
