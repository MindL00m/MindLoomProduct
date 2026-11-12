// End-to-end check of the extension's browser-task loop (apps/browser-extension/task-client.js):
// real Chromium with the unpacked extension, a mock MindLoom API that plays the model, and a
// fixture "ERP" page. Each scripted step sends one action and checks the result and the
// next page outline the extension reports.
//
//   npm install && npx playwright-core install chromium && npm test
//
// Set CHROMIUM to a Chromium or Chrome for Testing binary to use one you already have.
// (Branded Google Chrome no longer loads unpacked extensions from the command line.)
const http = require("http");
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright-core");

const EXT = path.join(__dirname, "..", "browser-extension");
const FIXTURE = path.join(__dirname, "fixture");
const SITE = 8791, API = 8792;
const PAGE = `http://localhost:${SITE}/index.html`;
const checks = [];
const check = (name, ok, info) => { checks.push({ name, ok: Boolean(ok), info: ok ? undefined : info }); };
const el = (obs, role, label) => obs.elements.find((e) => (!role || e.role === role) && e.label === label);
const line = (obs, re) => obs.text.split("\n").find((l) => re.test(l)) || "";

// Each step: (obs, result) => action. `result` is the previous action's result.
const script = [
  (o) => { check("starts with no page", /No page is open/.test(o.text), o.text); return { tool: "open_url", args: { url: PAGE } }; },
  (o, r) => { check("open_url ok", r?.ok && /Fixture ERP/.test(o.app), r); check("heading read", /heading "New purchase order"/.test(o.text), o.text.slice(0, 300)); check("hidden text not read", !/IGNORE|hidden injected/.test(o.text));
    return { tool: "set_value", args: { ref: el(o, "textbox", "Vendor").ref, text: "Azure Interior" } }; },
  (o, r) => { check("vendor typed", r?.ok && /now shows “Azure Interior”/.test(r.detail), r); check("vendor in outline", /Vendor" value="Azure Interior"/.test(o.text), line(o, /Vendor"/));
    return { tool: "set_value", args: { ref: el(o, "date field", "Order date").ref, text: "2026-12-01" } }; },
  (o, r) => { check("date set", r?.ok && /2026-12-01/.test(r.detail), r);
    return { tool: "set_value", args: { ref: el(o, "textbox", "Quantity").ref, text: "40" } }; },
  (o, r) => { check("number set", r?.ok && /“40”/.test(r.detail), r);
    return { tool: "select_option", args: { ref: el(o, "select", "Currency").ref, option: "Euro" } }; },
  (o, r) => { check("select chosen", r?.ok && /Euro/.test(r.detail) && /Currency" value="Euro"/.test(o.text), r);
    return { tool: "press", args: { ref: el(o, "checkbox", "Urgent").ref }, expect_label: "Urgent" }; },
  (o, r) => { check("hidden checkbox ticked through its label", r?.ok && /checkbox "Urgent" \(checked\)/.test(o.text), [r, line(o, /checkbox/)]);
    return { tool: "press", args: { ref: el(o, "clickable", "Add line").ref } }; },
  (o, r) => { check("div button clicked with a real pointer event", /Add line \(1, trusted\)/.test(o.text), [r, line(o, /Add line/)]);
    return { tool: "press", args: { ref: el(o, "combobox", "Pick a warehouse").ref } }; },
  (o, r) => { check("custom dropdown opened", Boolean(el(o, "option", "Rotterdam")), [r, line(o, /combobox/)]);
    return { tool: "press", args: { ref: (el(o, "option", "Rotterdam") || {}).ref } }; },
  (o, r) => { check("custom dropdown choice made", /combobox "Rotterdam"/.test(o.text), [r, line(o, /combobox/)]);
    return { tool: "set_value", args: { ref: el(o, "textbox", "Notes").ref, text: "Deliver before noon" } }; },
  (o, r) => { check("rich text editor filled", r?.ok && /Notes" value="Deliver before noon"/.test(o.text), [r, line(o, /Notes/)]);
    return { tool: "press", args: { ref: el(o, "button", "Save").ref }, expect_label: "Save" }; },
  (o, r) => { check("toast after save is reported", /Messages that appeared: "Saved as PO-00042"/.test(o.text), [r, o.text.slice(-300)]);
    return { tool: "press", args: { ref: el(o, "button", "Submit order").ref }, expect_label: "Submit order", approved: false }; },
  (o, r) => { check("unapproved Submit is refused by the extension", r && !r.ok && r.blocked === "needs_approval", r);
    return { tool: "press", args: { ref: el(o, "textbox", "Vendor").ref } }; },
  (o, r) => { check("field focused", r?.ok && /Vendor" value="Azure Interior".*\(focused\)/.test(o.text), [r, line(o, /Vendor"/)]);
    return { tool: "key", args: { combo: "enter" }, approved: false }; },
  (o, r) => { check("Enter that would submit is refused", r && !r.ok && r.blocked === "needs_approval" && /Submit order/.test(r.detail), r);
    return { tool: "set_value", args: { ref: el(o, "textbox", "Password").ref, text: "hunter2" } }; },
  (o, r) => { check("password field is never typed into", r && !r.ok && r.blocked === "secure_field", r); check("password marked secure", el(o, "textbox", "Password").secure && el(o, "textbox", "Card").secure);
    check("same-site iframe is read", /frame "Terms frame":/.test(o.text), o.text.slice(-500)); check("srcdoc iframe is read", /Inline button/.test(o.text), o.text.slice(-500)); check("iframe from a site that is not allowed is not read", /frame from http:\/\/127\.0\.0\.1:8791: not an allowed site, not read/.test(o.text) && !/Payment in 30 days/.test(o.text), o.text.slice(-500));
    return { tool: "press", args: { ref: el(o, "button", "Agree terms").ref } }; },
  (o, r) => { check("button inside an iframe clicked with a real pointer event", /Terms agreed \(trusted\)/.test(o.text), [r, line(o, /agreed/i)]);
    return { tool: "set_value", args: { ref: el(o, "textbox", "Reference").ref, text: "REF-9" } }; },
  (o, r) => { check("field inside an iframe filled", r?.ok && /REF-9/.test(r.detail) && /Reference" value="REF-9"/.test(o.text), [r, line(o, /Reference/)]);
    return { tool: "read", args: { ref: el(o, "button", "Remove").ref } }; },
  (o, r) => { check("table read in full", r?.ok && /Item 40 \| 80 \| 60.00/.test(r.text || ""), r && { ...r, text: (r.text || "").slice(-120) });
    return { tool: "scroll", args: { direction: "bottom" } }; },
  (o, r) => { check("scrolled to the end", r?.ok && /end/.test(r.detail), r);
    return { tool: "press", args: { ref: el(o, "button", "Shadow action").ref } }; },
  (o, r) => { check("button inside a shadow root clicked", /Shadow clicked \(trusted\)/.test(o.text), [r, line(o, /Shadow clicked/)]);
    return { tool: "press", args: { ref: el(o, "textbox", "Shadow field").ref } }; },
  () => ({ tool: "type_text", args: { text: "abc" } }),
  (o, r) => { check("typed at the focus", r?.ok && /Shadow field" value="abc"/.test(o.text), [r, line(o, /Shadow field/)]);
    return { tool: "key", args: { combo: "shift+tab" } }; },
  (o, r) => { check("key pressed", r?.ok, r);
    return { tool: "press", args: { ref: el(o, "button", "Remove all lines").ref }, expect_label: "Remove all lines", approved: true }; },
  (o, r) => { check("confirm dialog is reported", o.dialog?.type === "confirm" && /Remove all 40 lines/.test(o.dialog.message), [r, o.dialog, o.text.slice(0, 200)]);
    return { tool: "answer_dialog", args: { accept: true }, approved: false }; },
  (o, r) => { check("unapproved OK on a confirm is refused", r && !r.ok && r.blocked === "needs_approval", r);
    return { tool: "answer_dialog", args: { accept: false } }; },
  (o, r) => { check("dialog cancelled, rows kept", r?.ok && !o.dialog && /Item 40/.test(o.text), [r, o.dialog]);
    return { tool: "open_url", args: { url: "https://example.com/" } }; },
  (o, r) => { check("a site that is not allowed is refused", r && !r.ok && r.blocked === "site_not_allowed", r); check("still on the fixture", /index\.html/.test(o.url), o.url);
    return { tool: "press", args: { ref: el(o, "link", "Open terms").ref } }; },
  (o, r) => { check("new tab noticed and listed", /new tab opened/i.test(r?.detail || "") && o.tabs.some((t) => /Second page/.test(t.title)), [r, o.tabs]);
    return { tool: "switch_tab", args: { tab: (o.tabs.find((t) => /Second page/.test(t.title)) || {}).ref } }; },
  (o, r) => { check("working in the new tab", r?.ok && o.app === "Second page" && /Payment in 30 days/.test(o.text), [r, o.app, o.text.slice(0, 200)]);
    return { tool: "go_back", args: {} }; },
  (o, r) => { check("stale ref is refused", true); return { tool: "press", args: { ref: "e999" } }; },
  (o, r) => { check("unknown ref fails cleanly", r && !r.ok && /No element e999/.test(r.detail), r); return null; },
];

let cursor = 0, status = "pending", lastAction = null;
const transcript = [];
const task = () => ({ task_id: "task-1", run_id: "run-1", skill_id: "s", status, surface: "browser", title: "E2E",
  allowed_apps: ["localhost"], own_origins: [], log: [], pending: null, summary: null, error: null, steps: cursor });

function serveSite(req, res) {
  const file = path.join(FIXTURE, decodeURIComponent(new URL(req.url, "http://x").pathname).replace(/^\/+/, "") || "index.html");
  if (!file.startsWith(FIXTURE) || !fs.existsSync(file)) { res.writeHead(404); return res.end(); }
  res.writeHead(200, { "Content-Type": file.endsWith(".js") ? "text/javascript" : "text/html; charset=utf-8" });
  res.end(fs.readFileSync(file));
}

function serveApi(req, res) {
  const cors = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "*" };
  if (req.method === "OPTIONS") { res.writeHead(204, cors); return res.end(); }
  let body = "";
  req.on("data", (c) => (body += c));
  req.on("end", () => {
    const url = new URL(req.url, "http://x");
    const send = (code, data) => { res.writeHead(code, { "Content-Type": "application/json", ...cors }); res.end(JSON.stringify(data)); };
    if (req.headers.authorization !== "Bearer test-token") return send(401, { detail: "no" });
    if (url.pathname === "/captures/skill-runs") return send(200, []);
    if (url.pathname === "/captures/device-tasks") {
      const wanted = url.searchParams.get("surface") === "browser" && url.searchParams.get("status") === "pending";
      return send(200, wanted && status === "pending" ? [task()] : []);
    }
    if (url.pathname === "/captures/device-tasks/task-1/claim") {
      if (status !== "pending" || url.searchParams.get("surface") !== "browser") return send(409, { detail: "taken" });
      status = "running";
      return send(200, task());
    }
    if (url.pathname === "/captures/device-tasks/task-1" && req.method === "GET") return send(200, task());
    if (url.pathname === "/captures/device-tasks/task-1/step") {
      const { observation, result } = JSON.parse(body);
      transcript.push({ step: cursor, last: lastAction && lastAction.tool, result, page: observation.app, url: observation.url });
      let next = null;
      try {
        next = cursor < script.length ? script[cursor](observation, result) : null;
      } catch (error) {
        check(`step ${cursor} could not be scripted`, false, [String(error), observation.text.slice(-700)]);
      }
      cursor += 1;
      if (!next) { status = "completed"; return send(200, { status, action: null, message: null, task: task() }); }
      lastAction = { id: `call-${cursor}`, tool: next.tool, args: next.args, description: next.tool,
        approved: Boolean(next.approved), expect_label: next.expect_label ?? null };
      return send(200, { status, action: lastAction, message: null, task: task() });
    }
    send(404, { detail: "not found" });
  });
}

(async () => {
  const site = http.createServer(serveSite).listen(SITE);
  const api = http.createServer(serveApi).listen(API);
  const profile = fs.mkdtempSync(path.join(require("os").tmpdir(), "ml-e2e-"));
  const context = await chromium.launchPersistentContext(profile, {
    headless: false,
    executablePath: process.env.CHROMIUM || undefined,
    args: ["--headless=new", `--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`],
    viewport: { width: 1280, height: 800 },
  });
  // Playwright dismisses page dialogs nobody listens for; leave them to the extension.
  const keepDialogs = (page) => page.on("dialog", () => {});
  context.pages().forEach(keepDialogs);
  context.on("page", keepDialogs);
  try {
    const worker = context.serviceWorkers()[0] || (await context.waitForEvent("serviceworker", { timeout: 15000 }));
    const errors = [];
    worker.on("console", (message) => { if (/MindLoom (Task|Run)/.test(message.text())) errors.push(message.text()); });
    await worker.evaluate(({ api, site }) => chrome.storage.local.set({
      apiBase: `http://localhost:${api}`, accessToken: "test-token", runSites: [`http://localhost:${site}`],
    }), { api: API, site: SITE });
    const deadline = Date.now() + 150000;
    while (status !== "completed" && Date.now() < deadline) await new Promise((r) => setTimeout(r, 500));
    check("the task ran to the end", status === "completed", { status, cursor, of: script.length, last: transcript.slice(-2) });
    const failedChecks = checks.filter((c) => !c.ok);
    console.log(`${checks.length - failedChecks.length}/${checks.length} checks passed, ${cursor} steps`);
    for (const c of failedChecks) console.log("FAIL", c.name, JSON.stringify(c.info, null, 1).slice(0, 900));
    if (errors.length) console.log("worker warnings:", [...new Set(errors)].slice(0, 8));
    process.exitCode = failedChecks.length ? 1 : 0;
  } finally {
    await context.close();
    site.close(); api.close();
  }
})().catch((error) => { console.error(error); process.exit(2); });
