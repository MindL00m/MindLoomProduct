// Page anchors: which control was activated, never what is typed in it. One definition
// of a control's role, name, and selector, used both to record a click while capture is
// on and to find that control again during a skill run.
(() => {
  if (globalThis.__mindloomAnchor) return; // injected by the manifest and on demand
  globalThis.__mindloomAnchor = true;

  const CAPTURE_KEY = "captureEnabled";
  const MAX_NAME = 80;
  const INTERACTIVE_ROLES = [
    "button", "link", "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "checkbox",
    "radio", "switch", "option", "treeitem", "combobox", "textbox", "searchbox", "slider",
    "spinbutton", "listbox",
  ];
  const INTERACTIVE = [
    "a[href]", "area[href]", "button", "input:not([type=hidden])", "select", "textarea",
    "summary", "[contenteditable='']", "[contenteditable='true']",
    ...INTERACTIVE_ROLES.map((role) => `[role~='${role}']`),
  ].join(",");
  // Roles whose step is typing or choosing a value; a run never does either.
  const VALUE_ROLES = new Set(["textbox", "searchbox", "combobox", "spinbutton", "slider", "listbox"]);
  const NO_TEXT = new Set(["input", "textarea", "select", "script", "style", "noscript", "svg"]);
  const SENSITIVE = [
    /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i,
    /\b\d{3}-\d{2}-\d{4}\b/,
    /\b(?:\d[ -]*?){13,19}\b/,
  ];

  let captureEnabled = false;
  chrome.storage.local.get(CAPTURE_KEY).then((stored) => {
    captureEnabled = Boolean(stored[CAPTURE_KEY]);
  });
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes[CAPTURE_KEY]) {
      captureEnabled = Boolean(changes[CAPTURE_KEY].newValue);
    }
  });

  // --- Identity ------------------------------------------------------------------

  function inputRole(type) {
    switch (type) {
      case "button": case "submit": case "reset": case "image": case "file": return "button";
      case "checkbox": return "checkbox";
      case "radio": return "radio";
      case "range": return "slider";
      case "number": return "spinbutton";
      case "search": return "searchbox";
      default: return "textbox";
    }
  }

  function roleOf(el) {
    const explicit = (el.getAttribute("role") || "").trim().split(/\s+/)[0];
    if (explicit) return explicit;
    const tag = el.localName;
    if ((tag === "a" || tag === "area") && el.hasAttribute("href")) return "link";
    if (tag === "button" || tag === "summary") return "button";
    if (tag === "textarea") return "textbox";
    if (tag === "select") return el.multiple || el.size > 1 ? "listbox" : "combobox";
    if (tag === "input") return inputRole(el.type);
    if (el.isContentEditable) return "textbox";
    return "";
  }

  function needsValue(el, role) {
    // A file input's step is picking a file: a value.
    return VALUE_ROLES.has(role) || (el.localName === "input" && el.type === "file");
  }

  function isField(el, role) {
    return needsValue(el, role) || ["input", "select", "textarea"].includes(el.localName) || el.isContentEditable;
  }

  /** Visible label text, skipping form fields and editable regions so no value leaks in. */
  function textOf(node, depth = 0) {
    let out = "";
    for (const child of node.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) {
        out += ` ${child.nodeValue}`;
      } else if (child.nodeType === Node.ELEMENT_NODE) {
        if (NO_TEXT.has(child.localName) || child.isContentEditable) continue;
        if (child.getAttribute("aria-hidden") === "true") continue;
        if (child.localName === "img") out += ` ${child.getAttribute("alt") || ""}`;
        else if (depth < 8) out += ` ${textOf(child, depth + 1)}`;
      }
      if (out.length > 400) break;
    }
    return out;
  }

  function clean(text) {
    const value = String(text || "").replace(/\s+/g, " ").trim().slice(0, MAX_NAME);
    return SENSITIVE.some((pattern) => pattern.test(value)) ? "" : value;
  }

  /** Mirrors GuideMatcher.normalize on the Mac. */
  function normalize(text) {
    return String(text || "").toLowerCase().replace(/\s+/g, " ").trim().replace(/^[…:.]+|[…:.]+$/g, "");
  }

  function attr(el, name) {
    return clean(el.getAttribute(name));
  }

  /** Accessible name, or link text. Never `.value`, never a field's contents. */
  function nameOf(el, role) {
    const ids = (el.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    const labelledBy = clean(ids.map((id) => textOf(document.getElementById(id) || document.createElement("i"))).join(" "));
    if (labelledBy) return labelledBy;
    const aria = attr(el, "aria-label");
    if (aria) return aria;
    if (isField(el, role)) {
      const labels = clean([...(el.labels || [])].map((label) => textOf(label)).join(" "));
      return labels || attr(el, "title") || attr(el, "placeholder")
        || (el.localName === "input" && el.type === "image" ? attr(el, "alt") : "");
    }
    return clean(textOf(el)) || attr(el, "title");
  }

  function hrefPath(el) {
    const href = el.getAttribute("href");
    if (!href || /^\s*javascript:/i.test(href)) return null;
    try {
      return new URL(href, location.href).pathname || null;
    } catch {
      return null;
    }
  }

  /** Generated ids (React useId, numeric suffixes) change between loads. */
  function stableId(id) {
    return Boolean(id) && id.length <= 64 && !/\d{3,}|:|^\d/.test(id);
  }

  function count(selector) {
    try {
      return document.querySelectorAll(selector).length;
    } catch {
      return 0;
    }
  }

  /** CSS path from id, name, role, and aria-label only. No positions, no values. */
  function selectorFor(el) {
    if (stableId(el.id)) return `#${CSS.escape(el.id)}`;
    let own = el.localName;
    for (const name of ["name", "role", "aria-label"]) {
      const raw = el.getAttribute(name);
      if (raw && (name !== "aria-label" || clean(raw))) own += `[${name}="${CSS.escape(raw)}"]`;
    }
    if (count(own) === 1) return own;
    const scope = el.parentElement?.closest("[id]");
    return scope && stableId(scope.id) ? `#${CSS.escape(scope.id)} ${own}` : own;
  }

  function activatedControl(target) {
    if (!(target instanceof Element)) return null;
    const control = target.closest(INTERACTIVE);
    if (control) return control;
    return target.closest("label")?.control || null;
  }

  function anchorFor(el) {
    const role = roleOf(el);
    if (!role) return null;
    const anchor = {
      url: location.origin + location.pathname,
      role,
      name: nameOf(el, role),
      selector: selectorFor(el),
      needsValue: needsValue(el, role),
    };
    if (role === "link") anchor.href = hrefPath(el);
    return anchor;
  }

  // --- Recording -------------------------------------------------------------------

  let lastKey = "";
  let lastAt = 0;

  document.addEventListener(
    "click",
    (event) => {
      if (!captureEnabled || !event.isTrusted) return;
      const control = activatedControl(event.target);
      const anchor = control && anchorFor(control);
      if (!anchor) return;
      // A label click also clicks its control; record it once.
      const key = JSON.stringify(anchor);
      if (key === lastKey && Date.now() - lastAt < 800) return;
      lastKey = key;
      lastAt = Date.now();
      chrome.runtime.sendMessage({ type: "BROWSER_ANCHOR", anchor }).catch(() => {});
    },
    true,
  );

  // --- Finding again during a run --------------------------------------------------

  function isVisible(el) {
    return el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden";
  }

  function sameControl(el, anchor, { checkName }) {
    const role = roleOf(el);
    if (anchor.role && role !== anchor.role) return false;
    if (checkName && anchor.name && normalize(nameOf(el, role)) !== normalize(anchor.name)) return false;
    if (anchor.href && role === "link" && hrefPath(el) !== anchor.href) return false;
    return true;
  }

  /** Selector first, then role plus accessible name. More than one match is a failure. */
  function findControl(anchor) {
    let candidates = [];
    if (anchor.selector) {
      try {
        candidates = [...document.querySelectorAll(anchor.selector)];
      } catch {
        candidates = [];
      }
      // An id is identity enough; anything weaker must also carry the recorded name.
      const byId = /^#[^\s]+$/.test(anchor.selector);
      candidates = candidates.filter((el) => isVisible(el) && sameControl(el, anchor, { checkName: !byId }));
    }
    if (candidates.length === 0 && anchor.role && anchor.name) {
      candidates = [...document.querySelectorAll(INTERACTIVE)].filter(
        (el) => isVisible(el) && sameControl(el, anchor, { checkName: true }),
      );
    }
    const label = anchor.name || anchor.selector || anchor.role || "control";
    if (candidates.length === 1) return { element: candidates[0] };
    if (candidates.length === 0) return { error: `Couldn't find ${anchor.role || "the control"} “${label}” on this page.` };
    return { error: `${candidates.length} controls match “${label}”, so MindLoom didn't pick one.` };
  }

  function locate(anchor) {
    const found = findControl(anchor);
    if (found.error) return { ok: false, error: found.error };
    const el = found.element;
    const role = roleOf(el);
    const label = nameOf(el, role) || anchor.name || role;
    if (el.disabled || el.getAttribute("aria-disabled") === "true") {
      return { ok: false, error: `“${label}” is disabled.` };
    }
    el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
    const rect = el.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    const hit = document.elementFromPoint(x, y);
    const reachable = hit && (el === hit || el.contains(hit) || [...(el.labels || [])].some((l) => l.contains(hit)));
    if (!reachable) return { ok: false, error: `Something on the page is covering “${label}”.` };
    return { ok: true, x, y, needsValue: needsValue(el, role) };
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "LOCATE_ANCHOR") return false;
    sendResponse(locate(message.anchor || {}));
    return false;
  });
})();
