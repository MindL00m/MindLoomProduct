// Turning OpenClaw's inbound-message event into MindLoom's message shape, and
// posting it. Kept free of OpenClaw imports so it can be tested with plain Node.

const first = (...values) => {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return undefined;
};

/**
 * A WhatsApp identity in one stable form, whatever the country:
 * "+<digits>" for a phone number, the JID unchanged for groups ("...@g.us") and
 * for contacts WhatsApp only identifies by a private id ("...@lid").
 * No country code is ever assumed or added.
 */
export function normalizeWhatsAppId(value) {
  if (value === undefined || value === null) return undefined;
  let id = String(value).trim().replace(/^whatsapp:/i, "");
  if (!id) return undefined;
  const at = id.indexOf("@");
  if (at > 0) {
    const domain = id.slice(at + 1).toLowerCase();
    if (domain !== "s.whatsapp.net" && domain !== "c.us") return id.toLowerCase();
    id = id.slice(0, at).split(":")[0]; // drop the device suffix (number:device@...)
  }
  const digits = id.replace(/[\s().-]/g, "");
  if (/^\+?\d{6,15}$/.test(digits)) return `+${digits.replace(/^\+/, "")}`;
  return id;
}

const isGroupId = (id) => typeof id === "string" && id.endsWith("@g.us");
// Status updates and channels are broadcasts, not chats.
const isBroadcast = (id) => typeof id === "string" && /@(broadcast|newsletter)$/.test(id);

/** True for a message that arrived on the WhatsApp channel. */
export function isWhatsApp(event = {}, ctx = {}) {
  const meta = event.metadata ?? {};
  const channel = first(ctx.channelId, event.channelId, meta.provider, meta.surface, ctx.channel);
  return String(channel ?? "").toLowerCase().includes("whatsapp");
}

/** The MindLoom message for an OpenClaw `message_received` event, or null if it has no text. */
export function toMindLoomMessage(event = {}, ctx = {}) {
  const meta = event.metadata ?? {};
  const text = first(event.content, event.text, event.body);
  if (typeof text !== "string") return null;
  // OpenClaw gives the phone number as metadata.senderE164 when WhatsApp reveals it,
  // and a private id (senderId) otherwise.
  const senderId = normalizeWhatsAppId(
    first(meta.senderE164, event.senderId, meta.senderId, ctx.senderId, event.from),
  );
  // ctx.conversationId is the group JID for a group and the other person for a direct chat.
  const conversationId = normalizeWhatsAppId(
    first(ctx.conversationId, event.conversationId, meta.originatingTo, event.from),
  );
  if (isBroadcast(conversationId)) return null;
  const isGroup = isGroupId(conversationId);
  const senderName = first(meta.senderName, event.senderName, meta.senderUsername);
  return {
    text,
    message_id: first(event.messageId, meta.messageId, ctx.messageId) !== undefined
      ? String(first(event.messageId, meta.messageId, ctx.messageId)) : undefined,
    chat_id: conversationId ?? senderId,
    // OpenClaw does not pass a group's name to plugins; MindLoom names it instead.
    chat_name: isGroup ? first(meta.channelName, meta.groupSubject, meta.groupName) : senderName,
    is_group: isGroup,
    sender_id: senderId,
    sender_name: senderName,
    timestamp: first(event.timestamp, meta.timestamp, ctx.timestamp) ?? Date.now(),
  };
}

/**
 * True when the address is safe to send the link token to: https, or this computer
 * itself during development. Plain http elsewhere would send the token in the clear.
 */
export function isSafeApiUrl(apiUrl) {
  try {
    const url = new URL(String(apiUrl ?? ""));
    if (url.username || url.password) return false;
    if (url.protocol === "https:") return true;
    return url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  } catch {
    return false;
  }
}

/**
 * Post messages to MindLoom (up to 100 in one call). Never throws: a MindLoom outage
 * must not break the gateway. Returns "sent", "retry" (worth trying again later: an
 * outage, a timeout, MindLoom busy) or "dropped" (never going to work: not configured,
 * an unsafe address, a token MindLoom refuses, a message it rejects).
 */
export async function post(messages, config, { fetchImpl = fetch, log = console } = {}) {
  const apiUrl = String(config?.apiUrl ?? "").replace(/\/+$/, "");
  const token = String(config?.token ?? "");
  if (!apiUrl || !token) {
    log.warn?.("mindloom-whatsapp: apiUrl and token are not configured; message not forwarded");
    return "dropped";
  }
  if (!isSafeApiUrl(apiUrl)) {
    log.warn?.("mindloom-whatsapp: apiUrl must be https; message not forwarded");
    return "dropped";
  }
  try {
    const response = await fetchImpl(`${apiUrl}/integrations/whatsapp/live/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ messages }),
      signal: AbortSignal.timeout(15_000),
    });
    if (response.ok) return "sent";
    log.warn?.(`mindloom-whatsapp: MindLoom answered HTTP ${response.status}`);
    return response.status === 408 || response.status === 429 || response.status >= 500 ? "retry" : "dropped";
  } catch (error) {
    log.warn?.(`mindloom-whatsapp: could not reach MindLoom (${error?.name ?? "error"})`);
    return "retry";
  }
}

/** Post one message. Returns true when MindLoom accepted it. */
export async function forward(message, config, deps = {}) {
  return (await post([message], config, deps)) === "sent";
}

export const BATCH_SIZE = 100;
export const OUTBOX_LIMIT = 500;

/**
 * Messages waiting to reach MindLoom. While MindLoom is unreachable they are kept in
 * memory (the newest 500; nothing is written to disk) and sent, oldest first, with the
 * next message that arrives. MindLoom ignores a message it already has, so sending one
 * twice is harmless.
 */
export class Outbox {
  constructor(config, deps = {}) {
    this.config = config;
    this.deps = deps;
    this.waiting = [];
    this.flushing = null;
  }

  /** Add a message and try to send everything waiting. Resolves when the attempt ends. */
  add(message) {
    this.waiting.push(message);
    if (this.waiting.length > OUTBOX_LIMIT) this.waiting.splice(0, this.waiting.length - OUTBOX_LIMIT);
    // One attempt at a time, so messages stay in order.
    this.flushing ??= this.flush().finally(() => { this.flushing = null; });
    return this.flushing;
  }

  async flush() {
    while (this.waiting.length > 0) {
      const batch = this.waiting.slice(0, BATCH_SIZE);
      const outcome = await post(batch, this.config(), this.deps);
      if (outcome === "retry") return; // keep them for the next message
      this.waiting.splice(0, batch.length); // sent, or never going to be accepted
    }
  }
}

/** Wire the two hooks onto an OpenClaw plugin api. Exported for tests. */
export function register(api, deps = {}) {
  const config = () => api.pluginConfig ?? {};
  const log = api.logger ?? console;
  const outbox = new Outbox(config, { log, ...deps });

  api.on("message_received", async (event, ctx) => {
    if (!isWhatsApp(event, ctx)) return;
    const message = toMindLoomMessage(event, ctx);
    if (message) await outbox.add(message);
  });

  // Read-only: claim WhatsApp turns so the agent never answers a contact.
  api.on("before_dispatch", (event, ctx) => {
    if (config().silent === false || !isWhatsApp(event, ctx)) return undefined;
    return { handled: true };
  });
  return outbox;
}
