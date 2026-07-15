const summaryEl = document.getElementById("summary");
const pendingEmptyEl = document.getElementById("pending-empty");
const approvedEmptyEl = document.getElementById("approved-empty");
const pendingGridEl = document.getElementById("pending-grid");
const approvedGridEl = document.getElementById("approved-grid");
const refreshBtn = document.getElementById("refresh-btn");
const approveAllBtn = document.getElementById("approve-all-btn");

function formatTimestamp(ts) {
  return new Date(ts).toLocaleString();
}

function createMetaBadges(capture, extraClass) {
  const meta = document.createElement("div");
  meta.className = "card-meta";
  meta.innerHTML = `
    <span class="badge${extraClass ? ` ${extraClass}` : ""}">ID ${capture.id}</span>
    <span class="badge">Window ${capture.windowId}</span>
    <span class="badge">${formatTimestamp(capture.timestamp)}</span>
  `;
  return meta;
}

function createCaptureCard(capture, { approved = false } = {}) {
  const card = document.createElement("article");
  card.className = approved ? "card approved" : "card";
  card.dataset.captureId = capture.id;

  const img = document.createElement("img");
  img.className = "thumb";
  img.src = capture.dataUrl;
  img.alt = capture.tabTitle || "Screenshot";

  const body = document.createElement("div");
  body.className = "card-body";

  const title = document.createElement("h3");
  title.className = "card-title";
  title.textContent = capture.tabTitle || "(no title)";

  const url = document.createElement("p");
  url.className = "card-url";
  url.textContent = capture.url || "(no url)";

  body.append(title, url);

  if (approved) {
    body.appendChild(createMetaBadges(capture, "badge-approved"));
  } else {
    body.appendChild(createMetaBadges(capture));

    const actions = document.createElement("div");
    actions.className = "card-actions";

    const approveBtn = document.createElement("button");
    approveBtn.type = "button";
    approveBtn.className = "btn-approve";
    approveBtn.textContent = "Approve";
    approveBtn.addEventListener("click", () => approveCapture(capture.id));

    const rejectBtn = document.createElement("button");
    rejectBtn.type = "button";
    rejectBtn.className = "btn-reject";
    rejectBtn.textContent = "Reject";
    rejectBtn.addEventListener("click", () => rejectCapture(capture.id));

    actions.append(approveBtn, rejectBtn);
    body.appendChild(actions);
  }

  card.append(img, body);
  return card;
}

function renderSection(gridEl, emptyEl, captures, options) {
  gridEl.innerHTML = "";

  if (captures.length === 0) {
    emptyEl.hidden = false;
    return;
  }

  emptyEl.hidden = true;
  for (const capture of captures) {
    gridEl.appendChild(createCaptureCard(capture, options));
  }
}

function renderCaptures(pending, approved, captureEnabled) {
  const status = captureEnabled ? "Capture ON" : "Capture OFF";
  summaryEl.textContent = `${pending.length} pending · ${approved.length} approved · ${status}`;

  approveAllBtn.disabled = pending.length === 0;

  renderSection(pendingGridEl, pendingEmptyEl, pending, { approved: false });
  renderSection(approvedGridEl, approvedEmptyEl, approved, { approved: true });
}

async function loadCaptures() {
  try {
    const response = await chrome.runtime.sendMessage({ type: "GET_CAPTURES" });
    renderCaptures(
      response?.pending || [],
      response?.approved || [],
      Boolean(response?.captureEnabled)
    );
  } catch (err) {
    summaryEl.textContent = "Failed to load captures.";
    console.error("[workstyle-capture] Failed to load captures:", err);
  }
}

async function approveCapture(id) {
  try {
    await chrome.runtime.sendMessage({ type: "APPROVE_CAPTURE", id });
    await loadCaptures();
  } catch (err) {
    console.error("[workstyle-capture] Failed to approve capture:", err);
  }
}

async function rejectCapture(id) {
  try {
    await chrome.runtime.sendMessage({ type: "REJECT_CAPTURE", id });
    await loadCaptures();
  } catch (err) {
    console.error("[workstyle-capture] Failed to reject capture:", err);
  }
}

async function approveAll() {
  try {
    await chrome.runtime.sendMessage({ type: "APPROVE_ALL_CAPTURES" });
    await loadCaptures();
  } catch (err) {
    console.error("[workstyle-capture] Failed to approve all:", err);
  }
}

async function refreshCaptures() {
  try {
    await chrome.runtime.sendMessage({ type: "RETRY_APPROVED_UPLOADS" });
  } catch (err) {
    console.error("[workstyle-capture] Failed to retry uploads:", err);
  }
  await loadCaptures();
}

refreshBtn.addEventListener("click", refreshCaptures);
approveAllBtn.addEventListener("click", approveAll);
loadCaptures();
