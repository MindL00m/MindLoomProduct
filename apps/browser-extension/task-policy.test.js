// Run with: node --test apps/browser-extension
const test = require("node:test");
const assert = require("node:assert/strict");

const { looksIrreversible, isDeniedSite, hostAllowed, pageRefusal, parseCombo } = require("./task-policy.js");

test("labels that send, delete, pay or confirm need approval", () => {
  for (const label of ["Send", "Resend", "Sending…", "Confirm Order", "Place Order", "Post", "Don't Save", "Pay $40", "Submit for approval"]) {
    assert.ok(looksIrreversible(label), label);
  }
  for (const label of ["Save", "Sendai office", "Signature", "Cancel", "New", "Edit", ""]) {
    assert.ok(!looksIrreversible(label), label);
  }
});

test("only https pages on allowed sites can be worked on", () => {
  const sites = ["erp.example.com"];
  assert.equal(pageRefusal("https://erp.example.com/purchase?id=4", sites, []), "");
  assert.ok(pageRefusal("http://erp.example.com/", sites, []));
  assert.ok(pageRefusal("https://other.example.com/", sites, []));
  assert.ok(pageRefusal("https://erp.example.com.evil.net/", sites, []));
  assert.ok(pageRefusal("https://user:pw@erp.example.com/", sites, []));
  assert.ok(pageRefusal("chrome://settings", ["settings"], []));
  assert.ok(pageRefusal("not a url", sites, []));
});

test("wildcards cover subdomains and nothing else", () => {
  assert.ok(hostAllowed("a.b.example.org", ["*.example.org"]));
  assert.ok(hostAllowed("example.org", ["*.example.org"]));
  assert.ok(!hostAllowed("badexample.org", ["*.example.org"]));
});

test("sign-in sites and MindLoom's own pages are never worked on", () => {
  assert.ok(isDeniedSite("accounts.google.com"));
  assert.ok(isDeniedSite("my.1password.com"));
  assert.ok(!isDeniedSite("notaccounts.google.com.example.com"));
  assert.ok(pageRefusal("https://accounts.google.com/", ["*.google.com"], []));
  assert.ok(pageRefusal("https://app.mindloom.test/workflows", ["app.mindloom.test"], ["https://app.mindloom.test"]));
  // On localhost only the port tells MindLoom's web app from a site under test.
  assert.equal(pageRefusal("http://localhost:8069/odoo", ["localhost"], ["http://localhost:5500"]), "");
  assert.ok(pageRefusal("http://localhost:5500/workflows", ["localhost"], ["http://localhost:5500"]));
});

test("only navigation keys are pressed", () => {
  assert.deepEqual([parseCombo("Shift+Tab").shift, parseCombo("Shift+Tab").name], [true, "tab"]);
  assert.equal(parseCombo("Enter").event.key, "Enter");
  assert.equal(parseCombo("backspace").event.key, "Backspace");
  assert.equal(parseCombo("ctrl+w"), null);
  assert.equal(parseCombo("cmd+a"), null);
  assert.equal(parseCombo("a"), null);
  assert.equal(parseCombo(""), null);
});
