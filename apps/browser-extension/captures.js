const summaryEl = document.getElementById("summary");
const pendingEmptyEl = document.getElementById("pending-empty");
const approvedEmptyEl = document.getElementById("approved-empty");
const pendingGridEl = document.getElementById("pending-grid");
const approvedGridEl = document.getElementById("approved-grid");
const refreshBtn = document.getElementById("refresh-btn");
const approveAllBtn = document.getElementById("approve-all-btn");
const analyzeBtn = document.getElementById("analyze-btn");
const analysisResultEl = document.getElementById("analysis-result");
let activeSessionId = "";

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
    approveBtn.addEventListener("click", () => approveCapture(capture, card));

    const rejectBtn = document.createElement("button");
    rejectBtn.type = "button";
    rejectBtn.className = "btn-reject";
    rejectBtn.textContent = "Reject";
    rejectBtn.addEventListener("click", () => rejectCapture(capture.id));

    actions.append(approveBtn, rejectBtn);
    const note = document.createElement("textarea");
    note.placeholder = "Optional note about the decision or what matters";
    note.style.width = "100%";
    note.style.marginBottom = "8px";
    note.addEventListener("input", () => {
      capture.note = note.value;
    });
    const redactBtn = document.createElement("button");
    redactBtn.type = "button";
    redactBtn.className = "btn-secondary";
    redactBtn.textContent = "Remove sensitive area";
    redactBtn.addEventListener("click", () => addRedaction(img, capture));
    body.append(note, redactBtn);
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

function renderCaptures(pending, approved, captureEnabled, sessionId) {
  activeSessionId = sessionId || "";
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
      Boolean(response?.captureEnabled),
      response?.sessionId || ""
    );
  } catch (err) {
    summaryEl.textContent = "Failed to load captures.";
    console.error("[Loom Capture] Failed to load captures:", err);
  }
}

async function applyRedactions(capture) {
  if (!capture.redactions?.length) return capture.dataUrl;
  const image = new Image();
  image.src = capture.dataUrl;
  await image.decode();
  const canvas = document.createElement("canvas");
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  const context = canvas.getContext("2d");
  context.drawImage(image, 0, 0);
  context.fillStyle = "#000";
  for (const area of capture.redactions) {
    context.fillRect(
      area.x * canvas.width,
      area.y * canvas.height,
      area.width * canvas.width,
      area.height * canvas.height
    );
  }
  return canvas.toDataURL("image/png");
}

function addRedaction(image, capture) {
  const value = prompt("Enter area as left,top,width,height percentages (example: 10,20,30,15)");
  if (!value) return;
  const numbers = value.split(",").map(Number);
  if (numbers.length !== 4 || numbers.some((item) => !Number.isFinite(item) || item < 0 || item > 100)) {
    alert("Use four numbers from 0 to 100.");
    return;
  }
  capture.redactions = capture.redactions || [];
  capture.redactions.push({
    x: numbers[0] / 100,
    y: numbers[1] / 100,
    width: numbers[2] / 100,
    height: numbers[3] / 100,
  });
  image.style.filter = "blur(5px)";
}

async function approveCapture(capture, card) {
  try {
    capture.note = card.querySelector("textarea")?.value || "";
    const dataUrl = await applyRedactions(capture);
    await chrome.runtime.sendMessage({
      type: "UPDATE_CAPTURE", id: capture.id, dataUrl,
      note: capture.note, redactions: capture.redactions || [],
    });
    await chrome.runtime.sendMessage({ type: "APPROVE_CAPTURE", id: capture.id });
    await loadCaptures();
  } catch (err) {
    console.error("[Loom Capture] Failed to approve capture:", err);
  }
}

async function analyzeSession() {
  analysisResultEl.hidden = false;
  analysisResultEl.textContent = "Analyzing the approved screenshot sequence…";
  const response = await chrome.runtime.sendMessage({
    type: "ANALYZE_SESSION",
    sessionId: activeSessionId,
  });
  if (!response?.ok) {
    analysisResultEl.textContent = response?.error || "Could not create the Skill File.";
    return;
  }
  const skill = response.skill;
  analysisResultEl.textContent = `Proposed Skill File: ${skill.title}. ${skill.steps.length} inferred steps. ${skill.follow_up_questions.length} follow-up question(s). Review it in Company Brain before approval.`;
}

async function rejectCapture(id) {
  try {
    await chrome.runtime.sendMessage({ type: "REJECT_CAPTURE", id });
    await loadCaptures();
  } catch (err) {
    console.error("[Loom Capture] Failed to reject capture:", err);
  }
}

async function approveAll() {
  try {
    await chrome.runtime.sendMessage({ type: "APPROVE_ALL_CAPTURES" });
    await loadCaptures();
  } catch (err) {
    console.error("[Loom Capture] Failed to approve all:", err);
  }
}

async function refreshCaptures() {
  try {
    await chrome.runtime.sendMessage({ type: "RETRY_APPROVED_UPLOADS" });
  } catch (err) {
    console.error("[Loom Capture] Failed to retry uploads:", err);
  }
  await loadCaptures();
}

refreshBtn.addEventListener("click", refreshCaptures);
approveAllBtn.addEventListener("click", approveAll);
analyzeBtn.addEventListener("click", analyzeSession);
loadCaptures();
