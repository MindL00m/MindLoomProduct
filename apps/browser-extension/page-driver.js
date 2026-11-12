// Page driver for browser tasks. task-client.js injects this into every frame of the tab
// MindLoom is working in (the extension's isolated world), then calls
// window.__mindloomDriver.* through chrome.scripting.
//
// It reads the page as a numbered outline (controls, with the visible text in between),
// finds a numbered control again, and does the parts of an action that need the DOM:
// scrolling into view, focusing and selecting, choosing a select's option, reading text.
// Clicks and typing themselves are sent by the service worker as real input events; the
// DOM versions here are the fallback when those can't reach the control.
//
// It never reads the value of a password, one-time-code or card field, and it leaves out
// text a person can't see.

(() => {
  if (window.__mindloomDriver) return;

  const MAX_NODES = 20_000;
  const MAX_LABEL = 100;
  const MAX_TEXT = 220;
  const MAX_OPTIONS = 25;
  const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "HEAD", "META", "LINK", "BR", "HR", "WBR", "CAPTION"]);
  const INTERACTIVE_ROLES = new Set([
    "button", "link", "checkbox", "radio", "switch", "tab", "menuitem", "menuitemcheckbox",
    "menuitemradio", "option", "combobox", "listbox", "textbox", "searchbox", "spinbutton", "slider",
    "treeitem", "gridcell", "row", "columnheader",
  ]);
  const SECURE_HINT = /passw|otp|one.?time|cvv|cvc|card.?num|security.?code/i;
  const BUSY_SELECTOR = '[aria-busy="true"]';
  const TOAST_SELECTOR =
    '[role="alert"], [role="status"], [aria-live="assertive"], [aria-live="polite"], ' +
    '[class*="toast" i], [class*="snackbar" i], [class*="notification" i], [class*="MessageStrip" i], ' +
    '[class*="MessageToast" i], [class*="alert" i]';

  /** n → element for the latest outline. A new outline replaces it (refs go stale). */
  let registry = new Map();
  let generation = 0;
  /** Messages that appeared and may vanish before the next outline: [{ text, at }]. */
  const toasts = [];
  let probe = null;

  // --- Small helpers --------------------------------------------------------------------

  const clean = (text, limit) => {
    const value = String(text || "").replace(/\s+/g, " ").trim();
    return limit && value.length > limit ? `${value.slice(0, limit - 1)}…` : value;
  };
  const quote = (text) => `"${String(text).replace(/"/g, "'")}"`;

  function composedParent(node) {
    if (!node) return null;
    if (node.assignedSlot) return node.assignedSlot;
    if (node.parentNode instanceof ShadowRoot) return node.parentNode.host;
    return node.parentNode;
  }

  function composedContains(ancestor, node) {
    for (let current = node; current; current = composedParent(current)) {
      if (current === ancestor) return true;
    }
    return false;
  }

  function deepActiveElement() {
    let active = document.activeElement;
    while (active?.shadowRoot?.activeElement) active = active.shadowRoot.activeElement;
    return active;
  }

  function deepElementFromPoint(x, y) {
    let element = document.elementFromPoint(x, y);
    while (element?.shadowRoot) {
      const inner = element.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === element) break;
      element = inner;
    }
    return element;
  }

  function rendered(element) {
    if (typeof element.checkVisibility === "function") {
      if (!element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
    } else {
      const style = getComputedStyle(element);
      if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
    }
    return true;
  }

  /** Something a person can see: rendered, with a real size, not parked off screen. */
  function visible(element) {
    if (!rendered(element)) return false;
    const rect = element.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return false;
    return rect.right > -2000 && rect.bottom > -20_000;
  }

  /** Text a person can read here: not hidden, not parked off screen, not sized to nothing. */
  function readable(element) {
    if (!rendered(element) || element.closest('[aria-hidden="true"]')) return false;
    const rect = element.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1 || rect.right <= -2000 || rect.bottom <= -20_000) return false;
    return parseFloat(getComputedStyle(element).fontSize) >= 2;
  }

  /** The text inside an element that a person can see, in one line. */
  function visibleText(element, limit) {
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    const checked = new Map();
    let text = "";
    let lastInline = false;
    for (let node = walker.nextNode(); node && text.length < limit * 2; node = walker.nextNode()) {
      const parent = node.parentElement;
      if (!parent || !node.nodeValue.trim()) {
        if (node.nodeValue && text && !text.endsWith(" ")) text += " ";
        continue;
      }
      let entry = checked.get(parent);
      if (!entry) {
        entry = { ok: readable(parent), inline: getComputedStyle(parent).display.startsWith("inline") };
        checked.set(parent, entry);
      }
      if (!entry.ok) continue;
      text += (text && !(lastInline && entry.inline) ? " " : "") + node.nodeValue;
      lastInline = entry.inline;
    }
    return clean(text, limit);
  }

  /** -1 above the viewport, 0 in it, 1 below. */
  function position(rect) {
    if (rect.bottom <= 0) return -1;
    if (rect.top >= window.innerHeight) return 1;
    return 0;
  }

  // --- What a control is ------------------------------------------------------------------

  function isSecure(element) {
    if (!(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement)) return false;
    if (element.type === "password") return true;
    const autocomplete = (element.getAttribute("autocomplete") || "").toLowerCase();
    if (/password|one-time-code|^cc-|\scc-/.test(autocomplete)) return true;
    return SECURE_HINT.test(`${element.name} ${element.id} ${element.getAttribute("aria-label") || ""}`);
  }

  function isEditable(element) {
    if (element instanceof HTMLTextAreaElement) return true;
    if (element instanceof HTMLInputElement) {
      return !["checkbox", "radio", "button", "submit", "reset", "file", "image", "range", "color", "hidden"].includes(element.type);
    }
    return element.isContentEditable;
  }

  function roleOf(element) {
    const explicit = (element.getAttribute("role") || "").trim().split(/\s+/)[0];
    if (explicit && explicit !== "presentation" && explicit !== "none") return explicit;
    const tag = element.tagName;
    if (tag === "A") return "link";
    if (tag === "BUTTON" || tag === "SUMMARY") return "button";
    if (tag === "SELECT") return element.multiple ? "listbox" : "select";
    if (tag === "TEXTAREA") return "textbox";
    if (tag === "INPUT") {
      const type = element.type;
      if (["button", "submit", "reset", "image"].includes(type)) return "button";
      if (type === "checkbox" || type === "radio") return type;
      if (type === "file") return "file chooser";
      if (type === "range") return "slider";
      if (["date", "datetime-local", "month", "time", "week"].includes(type)) return "date field";
      return "textbox";
    }
    if (element.isContentEditable) return "textbox";
    return "clickable";
  }

  function isInteractive(element) {
    const tag = element.tagName;
    if (tag === "A") return element.hasAttribute("href") || element.hasAttribute("onclick");
    if (tag === "BUTTON" || tag === "SELECT" || tag === "TEXTAREA" || tag === "SUMMARY") return true;
    if (tag === "INPUT") return element.type !== "hidden";
    const role = (element.getAttribute("role") || "").trim().split(/\s+/)[0];
    if (INTERACTIVE_ROLES.has(role)) return true;
    if (element.isContentEditable) return !element.parentElement?.isContentEditable;
    if (element.hasAttribute("onclick")) return true;
    const tabindex = element.getAttribute("tabindex");
    if (tabindex !== null && Number(tabindex) >= 0) return true;
    // A div or span with a click handler: the pointer cursor starts here.
    if (tag === "LABEL" || tag === "TR" || tag === "LI" || tag === "DIV" || tag === "SPAN" || tag === "TD" || tag === "IMG" || tag === "SVG" || tag === "svg") {
      const parent = composedParent(element);
      // A label (and what is in it) is clicked through its field, which is listed itself.
      const label = tag === "LABEL" ? element : element.closest("label");
      if (label?.control) return false;
      return (
        getComputedStyle(element).cursor === "pointer" &&
        !(parent instanceof Element && getComputedStyle(parent).cursor === "pointer")
      );
    }
    return false;
  }

  /** What to click for this control: a hidden checkbox or radio is clicked through its label. */
  function clickTarget(element) {
    if (element instanceof HTMLInputElement && (element.type === "checkbox" || element.type === "radio") && !visible(element)) {
      const label = element.labels?.[0] || element.closest("label");
      if (label && visible(label)) return label;
      const parent = element.parentElement;
      if (parent && visible(parent)) return parent;
    }
    return element;
  }

  function nearbyText(element) {
    let node = element;
    for (let level = 0; level < 3 && node; level += 1) {
      for (let previous = node.previousSibling; previous; previous = previous.previousSibling) {
        if (previous instanceof Element && !rendered(previous)) continue;
        const text = clean(previous.textContent);
        if (text) return text.slice(-60);
      }
      node = node.parentElement;
    }
    return "";
  }

  function nameOf(element) {
    const aria = element.getAttribute("aria-label");
    if (aria?.trim()) return clean(aria, MAX_LABEL);
    const labelledby = element.getAttribute("aria-labelledby");
    if (labelledby) {
      const root = element.getRootNode();
      const text = labelledby.split(/\s+/).map((id) => root.getElementById?.(id)?.textContent || "").join(" ");
      if (text.trim()) return clean(text, MAX_LABEL);
    }
    const isField = element instanceof HTMLInputElement || element instanceof HTMLSelectElement || element instanceof HTMLTextAreaElement;
    if (isField) {
      const label = [...(element.labels || [])].map((item) => item.textContent).join(" ");
      if (label.trim()) return clean(label, MAX_LABEL);
      if (element instanceof HTMLInputElement && ["button", "submit", "reset"].includes(element.type)) {
        return clean(element.value || element.type, MAX_LABEL);
      }
      const hint = element.getAttribute("placeholder") || element.getAttribute("title");
      if (hint?.trim()) return clean(hint, MAX_LABEL);
      return clean(nearbyText(element) || element.getAttribute("name") || "", MAX_LABEL);
    }
    if (element.isContentEditable) {
      return clean(element.getAttribute("title") || element.getAttribute("data-placeholder") || nearbyText(element), MAX_LABEL);
    }
    const text = visibleText(element, MAX_LABEL);
    if (text) return text;
    const image = element.matches("img, svg") ? element : element.querySelector("img[alt], svg[aria-label], [title]");
    return clean(
      element.getAttribute("title") || element.getAttribute("alt") || image?.getAttribute("alt") ||
        image?.getAttribute("aria-label") || image?.getAttribute("title") || element.getAttribute("name") || "",
      MAX_LABEL,
    );
  }

  function selectedLabel(select) {
    return clean([...select.selectedOptions].map((option) => option.label || option.textContent).join(", "), 80);
  }

  function stateOf(element, secure) {
    const parts = [];
    const role = roleOf(element);
    if (element instanceof HTMLSelectElement) {
      parts.push(`value=${quote(selectedLabel(element))}`);
      const options = [...element.options].filter((option) => !option.disabled);
      const shown = options.slice(0, MAX_OPTIONS).map((option) => clean(option.label || option.textContent, 40));
      parts.push(`choices: ${shown.join(" | ")}${options.length > MAX_OPTIONS ? ` | … ${options.length - MAX_OPTIONS} more` : ""}`);
    } else if (isEditable(element)) {
      const format = { date: "YYYY-MM-DD", "datetime-local": "YYYY-MM-DDTHH:MM", month: "YYYY-MM", time: "HH:MM", week: "YYYY-Www" }[element.type];
      if (format) parts.push(`(enter as ${format})`);
      if (secure) parts.push("(secure)");
      else {
        const value = element.isContentEditable ? element.innerText : element.value;
        parts.push(`value=${quote(clean(value, 80))}`);
      }
    }
    const checked = element instanceof HTMLInputElement && (element.type === "checkbox" || element.type === "radio")
      ? String(element.checked)
      : element.getAttribute("aria-checked");
    if (checked === "true") parts.push("(checked)");
    else if (checked === "false" && ["checkbox", "radio", "switch", "menuitemcheckbox", "menuitemradio"].includes(role)) parts.push("(not checked)");
    else if (checked === "mixed") parts.push("(partly checked)");
    const expanded = element.getAttribute("aria-expanded");
    if (expanded === "true") parts.push("(open)");
    else if (expanded === "false") parts.push("(closed)");
    if (element.getAttribute("aria-selected") === "true" || element.getAttribute("aria-current")) parts.push("(selected)");
    if (element.getAttribute("aria-pressed") === "true") parts.push("(pressed)");
    if (element.disabled || element.getAttribute("aria-disabled") === "true") parts.push("(disabled)");
    if (element.readOnly || element.getAttribute("aria-readonly") === "true") parts.push("(read-only)");
    if (element.required || element.getAttribute("aria-required") === "true") parts.push("(required)");
    if (element.getAttribute("aria-invalid") === "true") parts.push("(invalid)");
    if (element.getAttribute("aria-haspopup") && expanded === null) parts.push("(opens a menu)");
    if (element === deepActiveElement()) parts.push("(focused)");
    return parts.join(" ");
  }

  // --- The outline ------------------------------------------------------------------------

  function hasInteractiveInside(element) {
    if (element.shadowRoot) return true;
    return Boolean(
      element.querySelector(
        'a[href], button, input:not([type="hidden"]), select, textarea, summary, [role], [onclick], [tabindex], [contenteditable], iframe',
      ),
    ) || [...element.querySelectorAll("*")].some((child) => child.shadowRoot);
  }

  /** The dialog in front, when the page shows one: the rest of the page is behind it. */
  function frontDialog() {
    const candidates = [...document.querySelectorAll('dialog[open], [aria-modal="true"], [role="alertdialog"]')].filter(visible);
    return candidates.length ? candidates[candidates.length - 1] : null;
  }

  function buildOutline(budget) {
    generation += 1;
    registry = new Map();
    const items = []; // { line, pos, element?: {n, role, label, secure, focused} }
    let visited = 0;
    let truncated = false;
    let nextNumber = 0;

    const push = (depth, line, rect, element) => {
      items.push({ line: `${" ".repeat(Math.min(depth, 6))}${line}`, pos: position(rect), element });
    };

    const emitControl = (element, depth) => {
      const target = clickTarget(element);
      if (!visible(target)) return false;
      nextNumber += 1;
      registry.set(nextNumber, element);
      const secure = isSecure(element);
      const role = roleOf(element);
      const label = nameOf(element);
      const state = stateOf(element, secure);
      push(depth, `[#${nextNumber}] ${role}${label ? ` ${quote(label)}` : ""}${state ? ` ${state}` : ""}`,
        target.getBoundingClientRect(),
        { n: nextNumber, role, label, secure, focused: element === deepActiveElement() });
      return true;
    };

    const walk = (node, depth, quiet) => {
      if (visited > MAX_NODES) {
        truncated = true;
        return;
      }
      visited += 1;
      if (node.nodeType === Node.TEXT_NODE) {
        if (quiet) return;
        const text = clean(node.nodeValue, MAX_TEXT);
        const parent = node.parentElement;
        if (text && parent && visible(parent)) push(depth, `text ${quote(text)}`, parent.getBoundingClientRect());
        return;
      }
      if (!(node instanceof Element) || SKIP_TAGS.has(node.tagName)) return;
      if (node.getAttribute("aria-hidden") === "true" || node.hasAttribute("inert")) return;
      if (!rendered(node)) {
        // A hidden checkbox or radio still counts: its label is what people click.
        if (node instanceof HTMLInputElement && (node.type === "checkbox" || node.type === "radio")) emitControl(node, depth);
        return;
      }
      const tag = node.tagName;
      if (tag === "IFRAME" || tag === "FRAME") {
        if (visible(node)) push(depth, `frame ${quote(clean(node.title || node.name || "embedded page", 60))} (its content is listed below)`, node.getBoundingClientRect());
        return;
      }
      if (tag === "OPTION" || tag === "OPTGROUP") return;

      let childDepth = depth;
      let childQuiet = quiet;
      const interactive = isInteractive(node);
      if (interactive) {
        const listed = emitControl(node, depth);
        // The control's own text is its label; controls inside it (a row's buttons) still count.
        if (listed) childQuiet = true;
        if (listed && !hasInteractiveInside(node)) return;
        childDepth = depth + (listed ? 1 : 0);
      } else if (/^H[1-6]$/.test(tag) || node.getAttribute("role") === "heading") {
        const text = visibleText(node, MAX_TEXT);
        if (text && visible(node)) push(depth, `heading ${quote(text)}`, node.getBoundingClientRect());
        if (!hasInteractiveInside(node)) return;
        childQuiet = true;
      } else if (tag === "TR" || node.getAttribute("role") === "row") {
        const cells = [...node.children].filter((cell) => rendered(cell)).map((cell) => visibleText(cell, 60));
        if (cells.some(Boolean) && visible(node)) push(depth, `row ${quote(cells.join(" | ").slice(0, 400))}`, node.getBoundingClientRect());
        if (!hasInteractiveInside(node)) return;
        childQuiet = true;
        childDepth = depth + 1;
      } else if (tag === "DIALOG" || ["dialog", "alertdialog"].includes(node.getAttribute("role"))) {
        if (visible(node)) push(depth, `dialog ${quote(nameOf(node).slice(0, 80) || "dialog")}:`, node.getBoundingClientRect());
        childDepth = depth + 1;
      } else if (tag === "TABLE" || ["table", "grid", "treegrid"].includes(node.getAttribute("role"))) {
        const rows = node.getAttribute("aria-rowcount") || (node.rows ? node.rows.length : "");
        const caption = clean(node.getAttribute("aria-label") || (node.caption ? visibleText(node.caption, 60) : ""), 60);
        if (visible(node)) push(depth, `table${caption ? ` ${quote(caption)}` : ""}${rows ? ` (${rows} rows)` : ""}:`, node.getBoundingClientRect());
        childDepth = depth + 1;
      } else if (!quiet && !hasInteractiveInside(node)) {
        // A block of plain text: one line, not one per fragment.
        const text = visibleText(node, MAX_TEXT * 2);
        if (text && visible(node)) {
          if (text.length <= MAX_TEXT || !node.children.length) {
            push(depth, `text ${quote(clean(text, MAX_TEXT))}`, node.getBoundingClientRect());
            return;
          }
        } else if (!text) {
          return;
        }
      }

      if (node.shadowRoot) {
        for (const child of node.shadowRoot.childNodes) walk(child, childDepth, childQuiet);
        return;
      }
      if (tag === "SLOT") {
        const assigned = node.assignedNodes({ flatten: true });
        for (const child of assigned.length ? assigned : node.childNodes) walk(child, childDepth, childQuiet);
        return;
      }
      for (const child of node.childNodes) walk(child, childDepth, childQuiet);
    };

    const dialog = frontDialog();
    const root = dialog || document.body || document.documentElement;
    if (root) walk(root, 0, false);

    // Keep what is on screen; add what is nearest below, then above, while it fits.
    let used = 0;
    const keep = new Array(items.length).fill(false);
    const take = (index) => {
      const cost = items[index].line.length + 1;
      if (used + cost > budget) return false;
      used += cost;
      keep[index] = true;
      return true;
    };
    items.forEach((item, index) => {
      if (item.pos === 0) take(index);
    });
    let full = false;
    for (let index = 0; index < items.length && !full; index += 1) {
      if (items[index].pos === 1 && !take(index)) full = true;
    }
    for (let index = items.length - 1; index >= 0 && !full; index -= 1) {
      if (items[index].pos === -1 && !take(index)) full = true;
    }
    const dropped = { above: 0, below: 0, shown: 0 };
    const lines = [];
    const elements = [];
    items.forEach((item, index) => {
      if (keep[index]) {
        lines.push(item.line);
        if (item.element) elements.push(item.element);
      } else if (item.pos === -1) dropped.above += 1;
      else if (item.pos === 1) dropped.below += 1;
      else dropped.shown += 1;
    });
    for (const [n] of registry) {
      if (!elements.some((element) => element.n === n)) registry.delete(n);
    }

    const scroller = document.scrollingElement || document.documentElement;
    const recent = toasts.filter((toast) => Date.now() - toast.at < 20_000).map((toast) => toast.text);
    toasts.length = 0;
    return {
      generation,
      title: clean(document.title, 160),
      url: location.href,
      origin: self.origin,
      focused: document.hasFocus(),
      lines,
      elements,
      dropped,
      truncated,
      modal: Boolean(dialog),
      more: {
        above: scroller.scrollTop > 4,
        below: scroller.scrollTop + window.innerHeight < scroller.scrollHeight - 4,
      },
      messages: [...new Set(recent)].slice(-5),
    };
  }

  // --- Finding a numbered control again ---------------------------------------------------

  function lookup(n, gen) {
    if (gen !== generation) return { error: "The page was read again since; use a ref from the latest page." };
    const element = registry.get(n);
    if (!element || !element.isConnected) return { error: "That control is no longer on the page; use a ref from the latest page." };
    return { element };
  }

  function describeElement(element) {
    return `${roleOf(element)} ${quote(nameOf(element).slice(0, 60))}`;
  }

  /** The label of the button Enter would press from this field, or "". */
  function submitLabelFor(element) {
    if (!(element instanceof HTMLInputElement) || !element.form) return "";
    const form = element.form;
    const button = form.querySelector('button[type="submit"], input[type="submit"], button:not([type])');
    return button ? nameOf(button) : "form";
  }

  async function frameOffset() {
    if (window === window.top) return { x: 0, y: 0 };
    return new Promise((resolve) => {
      const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const timer = setTimeout(() => {
        window.removeEventListener("message", onAnswer);
        resolve(null);
      }, 600);
      function onAnswer(event) {
        const data = event.data;
        if (event.source !== window.parent || !data || data.__mindloomFrame !== "answer" || data.id !== id) return;
        clearTimeout(timer);
        window.removeEventListener("message", onAnswer);
        resolve(Number.isFinite(data.x) && Number.isFinite(data.y) ? { x: data.x, y: data.y } : null);
      }
      window.addEventListener("message", onAnswer);
      window.parent.postMessage({ __mindloomFrame: "ask", id }, "*");
    });
  }

  // A child frame asks where it sits; answer with this frame's own position added.
  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || data.__mindloomFrame !== "ask" || typeof data.id !== "string") return;
    const frame = [...document.querySelectorAll("iframe, frame")].find((item) => item.contentWindow === event.source);
    if (!frame) return;
    void frameOffset().then((own) => {
      if (!own) return;
      const rect = frame.getBoundingClientRect();
      const style = getComputedStyle(frame);
      event.source.postMessage({
        __mindloomFrame: "answer",
        id: data.id,
        x: own.x + rect.left + frame.clientLeft + (parseFloat(style.paddingLeft) || 0),
        y: own.y + rect.top + frame.clientTop + (parseFloat(style.paddingTop) || 0),
      }, "*");
    });
  });

  /** Scroll the control into view and say where its middle is, in the tab's viewport. */
  async function locate(n, gen) {
    const found = lookup(n, gen);
    if (found.error) return { ok: false, error: found.error };
    const element = found.element;
    const target = clickTarget(element);
    target.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
    await new Promise((resolve) => requestAnimationFrame(() => resolve()));
    if (!visible(target)) return { ok: false, error: "That control is not visible any more." };
    const rect = target.getBoundingClientRect();
    const x = Math.min(Math.max(rect.left + rect.width / 2, 1), window.innerWidth - 1);
    const y = Math.min(Math.max(rect.top + rect.height / 2, 1), window.innerHeight - 1);
    const top = deepElementFromPoint(x, y);
    let covered = "";
    if (top && !composedContains(target, top) && !composedContains(top, target)) {
      const related = top.closest?.("label")?.control === element || target.parentElement?.contains(top);
      if (!related) covered = describeElement(top);
    }
    const offset = await frameOffset();
    return {
      ok: true,
      x: offset ? offset.x + x : null,
      y: offset ? offset.y + y : null,
      label: nameOf(element),
      role: roleOf(element),
      secure: isSecure(element),
      editable: isEditable(element),
      disabled: Boolean(element.disabled || element.getAttribute("aria-disabled") === "true"),
      covered,
      submitLabel: submitLabelFor(element),
    };
  }

  // --- Checking that a real pointer event reaches the control -----------------------------

  function onProbe(event) {
    if (!probe || !event.isTrusted) return;
    // The pointer is on the control or on something inside it.
    if (event.composedPath().includes(probe.target)) probe.hit = true;
  }

  function armProbe(n, gen) {
    const found = lookup(n, gen);
    if (found.error) return { ok: false, error: found.error };
    probe = { target: clickTarget(found.element), hit: false };
    window.addEventListener("mousemove", onProbe, true);
    window.addEventListener("pointermove", onProbe, true);
    return { ok: true };
  }

  function probeHit() {
    const hit = Boolean(probe?.hit);
    probe = null;
    window.removeEventListener("mousemove", onProbe, true);
    window.removeEventListener("pointermove", onProbe, true);
    return hit;
  }

  // --- DOM fallbacks ------------------------------------------------------------------------

  /** The pointer and mouse events of a click, for when real input can't reach the control. */
  function domClick(n, gen) {
    const found = lookup(n, gen);
    if (found.error) return { ok: false, error: found.error };
    const target = clickTarget(found.element);
    const rect = target.getBoundingClientRect();
    const init = {
      bubbles: true, cancelable: true, composed: true, view: window, button: 0,
      clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2,
    };
    target.dispatchEvent(new PointerEvent("pointerover", init));
    target.dispatchEvent(new MouseEvent("mouseover", init));
    target.dispatchEvent(new PointerEvent("pointerdown", { ...init, buttons: 1, pointerId: 1, isPrimary: true }));
    target.dispatchEvent(new MouseEvent("mousedown", { ...init, buttons: 1 }));
    if (typeof target.focus === "function") target.focus({ preventScroll: true });
    target.dispatchEvent(new PointerEvent("pointerup", { ...init, pointerId: 1, isPrimary: true }));
    target.dispatchEvent(new MouseEvent("mouseup", init));
    target.click();
    return { ok: true };
  }

  /** Focus a field and select what is in it, so typed text replaces it. */
  function focusForTyping(n, gen) {
    const found = lookup(n, gen);
    if (found.error) return { ok: false, error: found.error };
    const element = found.element;
    if (isSecure(element)) return { ok: false, secure: true, error: "That is a password, code or card field." };
    if (!isEditable(element)) return { ok: false, error: `${describeElement(element)} is not a text field.` };
    if (element.disabled || element.readOnly) return { ok: false, error: "That field can't be edited (disabled or read-only)." };
    element.focus({ preventScroll: true });
    if (element.isContentEditable) {
      const range = document.createRange();
      range.selectNodeContents(element);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    } else {
      try {
        element.select();
      } catch {
        /* number, date and email inputs have no text selection; the caller falls back */
      }
    }
    return { ok: deepActiveElement() === element, empty: !currentValue(element) };
  }

  function currentValue(element) {
    if (element instanceof HTMLSelectElement) return selectedLabel(element);
    if (element.isContentEditable) return clean(element.innerText);
    return String(element.value ?? "");
  }

  function readValue(n, gen) {
    const found = lookup(n, gen);
    if (found.error) return { ok: false, error: found.error };
    if (isSecure(found.element)) return { ok: false, secure: true, error: "That is a password, code or card field." };
    return { ok: true, value: currentValue(found.element).slice(0, 2000) };
  }

  /** Set a field's value the way frameworks notice: the native setter, then input and change. */
  function fillValue(n, gen, text) {
    const found = lookup(n, gen);
    if (found.error) return { ok: false, error: found.error };
    const element = found.element;
    if (isSecure(element)) return { ok: false, secure: true, error: "That is a password, code or card field." };
    element.focus({ preventScroll: true });
    if (element.isContentEditable) {
      const range = document.createRange();
      range.selectNodeContents(element);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      if (!document.execCommand("insertText", false, text)) element.textContent = text;
    } else {
      const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
      if (setter) setter.call(element, text);
      else element.value = text;
    }
    element.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "insertText", data: text }));
    element.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    return { ok: true, value: currentValue(element).slice(0, 2000) };
  }

  function chooseOption(n, gen, wanted) {
    const found = lookup(n, gen);
    if (found.error) return { ok: false, error: found.error };
    const select = found.element;
    if (!(select instanceof HTMLSelectElement)) {
      return { ok: false, error: `${describeElement(select)} is not a select: press it, then press the choice on the next page.` };
    }
    if (select.disabled) return { ok: false, error: "That select is disabled." };
    const target = clean(wanted).toLowerCase();
    const options = [...select.options].filter((option) => !option.disabled);
    const text = (option) => clean(option.label || option.textContent).toLowerCase();
    const match =
      options.find((option) => text(option) === target) ||
      options.find((option) => option.value.toLowerCase() === target) ||
      options.filter((option) => text(option).includes(target)).find((_, __, all) => all.length === 1);
    if (!match) {
      return { ok: false, error: `No single choice matches “${wanted}”. Choices: ${options.slice(0, 40).map((o) => clean(o.label || o.textContent, 40)).join(" | ")}` };
    }
    select.focus({ preventScroll: true });
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
    if (setter) setter.call(select, match.value);
    else select.value = match.value;
    match.selected = true;
    select.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    select.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
    return { ok: true, value: selectedLabel(select) };
  }

  function readText(n, gen, limit) {
    const found = lookup(n, gen);
    if (found.error) return { ok: false, error: found.error };
    const element = found.element;
    if (isSecure(element)) return { ok: false, secure: true, error: "That is a password, code or card field." };
    // A control inside a table or list: read the whole table or list.
    const container = element.closest("table, [role='grid'], [role='table'], ul, ol, [role='list'], [role='listbox']") || element;
    const rows = container.matches("table, [role='grid'], [role='table']")
      ? [...container.querySelectorAll("tr, [role='row']")].filter(rendered).map((row) =>
          [...row.children].filter(rendered).map((cell) => visibleText(cell, 200)).join(" | "))
      : container.matches("ul, ol, [role='list'], [role='listbox']")
        ? [...container.querySelectorAll("li, [role='listitem'], [role='option']")].filter(rendered).map((item) => visibleText(item, 400))
        : null;
    const text = rows ? rows.filter(Boolean).join("\n") : visibleText(container, limit) || currentValue(element);
    return { ok: true, text: text.slice(0, limit), cut: text.length > limit, lines: text.split("\n").length };
  }

  function scrollableAncestor(element) {
    for (let node = element; node && node !== document.body; node = composedParent(node)) {
      if (!(node instanceof Element)) continue;
      const style = getComputedStyle(node);
      if (/(auto|scroll|overlay)/.test(style.overflowY) && node.scrollHeight > node.clientHeight + 4) return node;
    }
    return document.scrollingElement || document.documentElement;
  }

  async function scrollBy(n, gen, direction) {
    let scroller = document.scrollingElement || document.documentElement;
    if (n) {
      const found = lookup(n, gen);
      if (found.error) return { ok: false, error: found.error };
      scroller = scrollableAncestor(found.element);
    }
    const before = scroller.scrollTop;
    const page = (scroller === document.scrollingElement ? window.innerHeight : scroller.clientHeight) * 0.85;
    const top =
      direction === "top" ? 0 : direction === "bottom" ? scroller.scrollHeight : before + (direction === "up" ? -page : page);
    scroller.scrollTo({ top, behavior: "instant" });
    await new Promise((resolve) => setTimeout(resolve, 150));
    return {
      ok: true,
      moved: Math.abs(scroller.scrollTop - before) > 2,
      atTop: scroller.scrollTop <= 2,
      atBottom: scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 2,
    };
  }

  /** What has the keyboard focus in this frame, for the checks before typing or Enter. */
  function focusInfo() {
    const element = deepActiveElement();
    if (!element || element === document.body || element === document.documentElement || element.tagName === "IFRAME" || element.tagName === "FRAME") {
      return { focused: false };
    }
    return {
      focused: true,
      hasFocus: document.hasFocus(),
      secure: isSecure(element),
      editable: isEditable(element),
      role: roleOf(element),
      label: nameOf(element),
      submitLabel: submitLabelFor(element),
    };
  }

  /** Wait until the page stops changing (loaded, nothing marked busy), up to maxMs. */
  function settle(maxMs, quietMs) {
    return new Promise((resolve) => {
      const started = Date.now();
      let last = Date.now();
      const observer = new MutationObserver(() => {
        last = Date.now();
      });
      observer.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
      const timer = setInterval(() => {
        const now = Date.now();
        const busy = [...document.querySelectorAll(BUSY_SELECTOR)].some(visible);
        const quiet = now - last >= quietMs && document.readyState === "complete" && !busy;
        if (quiet || now - started >= maxMs) {
          clearInterval(timer);
          observer.disconnect();
          resolve({ settled: quiet, waited: now - started });
        }
      }, 80);
    });
  }

  // Messages that flash up after a save and are gone before the next outline.
  function noteToasts(node) {
    if (!(node instanceof Element)) return;
    const candidates = node.matches(TOAST_SELECTOR) ? [node] : [...node.querySelectorAll(TOAST_SELECTOR)].slice(0, 5);
    for (const candidate of candidates) {
      const text = visible(candidate) ? visibleText(candidate, 300) : "";
      if (text && !toasts.some((toast) => toast.text === text)) {
        toasts.push({ text, at: Date.now() });
        if (toasts.length > 10) toasts.shift();
      }
    }
  }
  new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === "childList") mutation.addedNodes.forEach(noteToasts);
      else if (mutation.target instanceof Element) noteToasts(mutation.target);
      else if (mutation.target.parentElement) noteToasts(mutation.target.parentElement);
    }
  }).observe(document.documentElement, {
    subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ["class", "style", "hidden", "aria-hidden"],
  });

  window.__mindloomDriver = {
    outline: buildOutline, locate, armProbe, probeHit, domClick, focusForTyping, readValue, fillValue,
    chooseOption, readText, scrollBy, focusInfo, settle,
  };
})();
