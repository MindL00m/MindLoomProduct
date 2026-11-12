// Safety rules for browser tasks, checked here again before every action whatever the
// server sent. Pure functions with no chrome.* calls, so they run in the service worker
// and in Node (task-policy.test.js). The word list and the denied sites are the server's
// (apps/api/shuttle/device_tasks.py and browser_tasks.py); a backend test keeps them in step.

const IRREVERSIBLE_WORDS = [
  "send", "submit", "post", "publish", "share", "delete", "remove", "erase", "trash",
  "discard", "don't save", "dont save", "replace", "overwrite", "pay", "purchase", "buy",
  "order", "checkout", "check out", "transfer", "book", "reserve", "confirm", "approve",
  "accept", "decline", "sign", "unsubscribe", "cancel subscription", "empty",
];

// Sign-in and account pages, password managers, the extension store: never operated.
const DENIED_SITES = [
  "accounts.google.com", "myaccount.google.com", "passwords.google.com",
  "chromewebstore.google.com", "login.microsoftonline.com", "login.live.com",
  "account.microsoft.com", "appleid.apple.com", "1password.com", "bitwarden.com",
  "lastpass.com", "dashlane.com", "keepersecurity.com", "nordpass.com", "okta.com",
  "auth0.com", "duosecurity.com",
];

const escapeRegExp = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// A word on its own or with a common prefix/suffix ("Resend", "Deleting"), not inside
// another word ("Sendai", "Signature").
const IRREVERSIBLE_RE = new RegExp(
  `(?<![a-z])(?:re)?(${IRREVERSIBLE_WORDS.map(escapeRegExp).join("|")})(?:s|es|d|ed|ing|al)?(?![a-z])`,
  "i",
);

function looksIrreversible(label) {
  return IRREVERSIBLE_RE.test(String(label || ""));
}

function isDeniedSite(host) {
  const name = String(host || "").toLowerCase().replace(/\.$/, "");
  if (!name) return true;
  return DENIED_SITES.some((denied) => name === denied || name.endsWith(`.${denied}`));
}

/** `host` is one of the automation's allowed sites ("erp.example.com" or "*.example.com"). */
function hostAllowed(host, allowedSites) {
  const name = String(host || "").toLowerCase().replace(/\.$/, "");
  if (!name || isDeniedSite(name)) return false;
  return (allowedSites || []).some((site) =>
    site.startsWith("*.") ? name === site.slice(2) || name.endsWith(site.slice(1)) : name === site,
  );
}

/**
 * Why MindLoom may not work on this address, or "" when it may: https only (http on
 * localhost), on a site the automation allows, never a denied site, and never the
 * MindLoom pages themselves (`ownOrigins`), where the Approve button lives.
 */
function pageRefusal(url, allowedSites, ownOrigins) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return "This is not a web page MindLoom can work on.";
  }
  const local = parsed.hostname === "localhost";
  if (parsed.protocol !== "https:" && !(parsed.protocol === "http:" && local)) {
    return "MindLoom only works on https:// pages.";
  }
  if (parsed.username || parsed.password) return "MindLoom does not open addresses that carry a password.";
  if ((ownOrigins || []).includes(parsed.origin)) return "MindLoom never operates its own pages.";
  if (isDeniedSite(parsed.hostname)) {
    return `MindLoom never operates ${parsed.hostname}: it holds sign-ins or passwords.`;
  }
  if (!hostAllowed(parsed.hostname, allowedSites)) {
    return `${parsed.hostname} is not one of the sites allowed for this automation.`;
  }
  return "";
}

/** Keys the driver may press, as Chrome DevTools key events. */
const KEY_EVENTS = {
  return: { key: "Enter", code: "Enter", keyCode: 13, text: "\r" },
  tab: { key: "Tab", code: "Tab", keyCode: 9 },
  escape: { key: "Escape", code: "Escape", keyCode: 27 },
  esc: { key: "Escape", code: "Escape", keyCode: 27 },
  delete: { key: "Backspace", code: "Backspace", keyCode: 8 },
  forwarddelete: { key: "Delete", code: "Delete", keyCode: 46 },
  space: { key: " ", code: "Space", keyCode: 32, text: " " },
  up: { key: "ArrowUp", code: "ArrowUp", keyCode: 38 },
  down: { key: "ArrowDown", code: "ArrowDown", keyCode: 40 },
  left: { key: "ArrowLeft", code: "ArrowLeft", keyCode: 37 },
  right: { key: "ArrowRight", code: "ArrowRight", keyCode: 39 },
  home: { key: "Home", code: "Home", keyCode: 36 },
  end: { key: "End", code: "End", keyCode: 35 },
  pageup: { key: "PageUp", code: "PageUp", keyCode: 33 },
  pagedown: { key: "PageDown", code: "PageDown", keyCode: 34 },
};

const KEY_ALIASES = { enter: "return", backspace: "delete", del: "forwarddelete", spacebar: "space",
  arrowup: "up", arrowdown: "down", arrowleft: "left", arrowright: "right" };

/** "Shift+Tab" → { shift: true, name: "tab", event }, or null for a key the driver won't press. */
function parseCombo(combo) {
  const parts = String(combo || "").trim().toLowerCase().split(/[+\s-]+/).filter(Boolean);
  if (!parts.length) return null;
  const name = KEY_ALIASES[parts[parts.length - 1]] || parts[parts.length - 1];
  const modifiers = parts.slice(0, -1);
  if (!KEY_EVENTS[name] || modifiers.some((m) => m !== "shift")) return null;
  return { shift: modifiers.includes("shift"), name, event: KEY_EVENTS[name] };
}

if (typeof module !== "undefined") {
  module.exports = { IRREVERSIBLE_WORDS, DENIED_SITES, looksIrreversible, isDeniedSite, hostAllowed, pageRefusal, parseCombo };
}
