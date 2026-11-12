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
 * Post one message to MindLoom. Never throws: a MindLoom outage must not break
 * the gateway. Returns true when MindLoom accepted it.
 */
export async function forward(message, config, { fetchImpl = fetch, log = console } = {}) {
  const apiUrl = String(config?.apiUrl ?? "").replace(/\/+$/, "");
  const token = String(config?.token ?? "");
  if (!apiUrl || !token) {
    log.warn?.("mindloom-whatsapp: apiUrl and token are not configured; message not forwarded");
    return false;
  }
  try {
    const response = await fetchImpl(`${apiUrl}/integrations/whatsapp/live/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ messages: [message] }),
      signal: AbortSignal.timeout(15_000),
    });
    if (!response.ok) {
      log.warn?.(`mindloom-whatsapp: MindLoom answered HTTP ${response.status}`);
      return false;
    }
    return true;
  } catch (error) {
    log.warn?.(`mindloom-whatsapp: could not reach MindLoom (${error?.name ?? "error"})`);
    return false;
  }
}

/** Wire the two hooks onto an OpenClaw plugin api. Exported for tests. */
export function register(api, deps = {}) {
  const config = () => api.pluginConfig ?? {};
  const log = api.logger ?? console;

  api.on("message_received", async (event, ctx) => {
    if (!isWhatsApp(event, ctx)) return;
    const message = toMindLoomMessage(event, ctx);
    if (message) await forward(message, config(), { log, ...deps });
  });

  // Read-only: claim WhatsApp turns so the agent never answers a contact.
  api.on("before_dispatch", (event, ctx) => {
    if (config().silent === false || !isWhatsApp(event, ctx)) return undefined;
    return { handled: true };
  });
}
