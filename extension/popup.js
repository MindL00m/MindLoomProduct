const STORAGE_KEY = "captureEnabled";

const tabIdEl = document.getElementById("tab-id");
const tabTitleEl = document.getElementById("tab-title");
const tabUrlEl = document.getElementById("tab-url");
const captureToggle = document.getElementById("capture-toggle");
const captureStatus = document.getElementById("capture-status");
const viewCapturesBtn = document.getElementById("view-captures");

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
    console.error("[workstyle-capture] Failed to load active tab:", err);
  }
}

async function loadCaptureState() {
  const result = await chrome.storage.local.get(STORAGE_KEY);
  setCaptureUi(Boolean(result[STORAGE_KEY]));
}

captureToggle.addEventListener("change", async () => {
  const enabled = captureToggle.checked;
  await chrome.storage.local.set({ [STORAGE_KEY]: enabled });
  setCaptureUi(enabled);
});

viewCapturesBtn.addEventListener("click", () => {
  chrome.tabs.create({ url: chrome.runtime.getURL("captures.html") });
});

loadActiveTab();
loadCaptureState();
