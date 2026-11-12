// Turning OpenClaw's inbound-message event into MindLoom's message shape, and
// posting it. Kept free of OpenClaw imports so it can be tested with plain Node.

const first = (...values) => {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return undefined;
};

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
  const senderId = first(event.senderId, meta.senderE164, meta.senderId, event.from, ctx.from);
  return {
    text,
    message_id: first(event.messageId, meta.messageId) !== undefined
      ? String(first(event.messageId, meta.messageId)) : undefined,
    // Groups have their own conversation id; a direct chat is identified by the sender.
    chat_id: first(ctx.conversationId, event.conversationId, meta.threadId, event.threadId, senderId),
    chat_name: first(meta.channelName, meta.groupName, meta.senderName),
    sender_id: senderId !== undefined ? String(senderId) : undefined,
    sender_name: first(meta.senderName, event.senderName),
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
