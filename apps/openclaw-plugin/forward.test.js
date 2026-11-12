import assert from "node:assert/strict";
import { test } from "node:test";

import { forward, isWhatsApp, normalizeWhatsAppId, register, toMindLoomMessage } from "./forward.js";

const dm = {
  content: "The shipment leaves Friday.",
  messageId: "3EB0ABC",
  senderId: "+6591234567",
  timestamp: 1794470400000,
  metadata: { senderName: "Priya Raman", senderE164: "+6591234567", provider: "whatsapp" },
};

test("a direct message maps to a chat named after the sender", () => {
  assert.deepEqual(toMindLoomMessage(dm, { channelId: "whatsapp" }), {
    text: "The shipment leaves Friday.",
    message_id: "3EB0ABC",
    chat_id: "+6591234567",
    chat_name: "Priya Raman",
    is_group: false,
    sender_id: "+6591234567",
    sender_name: "Priya Raman",
    timestamp: 1794470400000,
  });
});

test("a group message keeps the group as the chat", () => {
  // The shape OpenClaw 2026.11 really sends: the group JID as the conversation, no group name.
  const message = toMindLoomMessage(dm, { channelId: "whatsapp", conversationId: "120363001@g.us" });
  assert.equal(message.chat_id, "120363001@g.us");
  assert.equal(message.is_group, true);
  assert.equal(message.chat_name, undefined);
  assert.equal(message.sender_id, "+6591234567");
  assert.equal(message.sender_name, "Priya Raman");
  // If a later OpenClaw passes the group's name, it is used.
  const named = toMindLoomMessage(
    { ...dm, metadata: { ...dm.metadata, channelName: "Ops team" } },
    { channelId: "whatsapp", conversationId: "120363001@g.us" },
  );
  assert.equal(named.chat_name, "Ops team");
});

test("numbers from any country end up in one form, with no country assumed", () => {
  for (const [raw, expected] of [
    ["+919401264052", "+919401264052"],
    ["919401264052@s.whatsapp.net", "+919401264052"],
    ["919401264052:12@s.whatsapp.net", "+919401264052"],
    ["whatsapp:+44 7700 900123", "+447700900123"],
    ["+1 (415) 555-2671", "+14155552671"],
    ["4915112345678@c.us", "+4915112345678"],
    ["971501234567", "+971501234567"],
    ["120363001@G.US", "120363001@g.us"],
    ["98765432109876@lid", "98765432109876@lid"],
  ]) assert.equal(normalizeWhatsAppId(raw), expected, raw);
  assert.equal(normalizeWhatsAppId(""), undefined);
  assert.equal(normalizeWhatsAppId(undefined), undefined);
});

test("a contact with a hidden number is still one stable chat, and broadcasts are skipped", () => {
  const hidden = toMindLoomMessage(
    { content: "hello", senderId: "98765432109876@lid", metadata: { senderName: "Wei" } },
    { channelId: "whatsapp", conversationId: "98765432109876@lid" },
  );
  assert.equal(hidden.chat_id, "98765432109876@lid");
  assert.equal(hidden.is_group, false);
  assert.equal(toMindLoomMessage(dm, { channelId: "whatsapp", conversationId: "status@broadcast" }), null);
});

test("messages without text and other channels are ignored", () => {
  assert.equal(toMindLoomMessage({ media: [{}] }, {}), null);
  assert.equal(isWhatsApp(dm, { channelId: "telegram" }), false);
  assert.equal(isWhatsApp(dm, {}), true);
});

test("forward posts with the link token and survives an outage", async () => {
  const calls = [];
  const ok = await forward({ text: "hi" }, { apiUrl: "https://api.example/", token: "mlwa_x" }, {
    fetchImpl: async (url, init) => { calls.push([url, init]); return { ok: true, status: 200 }; },
  });
  assert.equal(ok, true);
  assert.equal(calls[0][0], "https://api.example/integrations/whatsapp/live/messages");
  assert.equal(calls[0][1].headers.Authorization, "Bearer mlwa_x");
  assert.deepEqual(JSON.parse(calls[0][1].body), { messages: [{ text: "hi" }] });

  const quiet = { warn() {} };
  const down = await forward({ text: "hi" }, { apiUrl: "https://api.example", token: "mlwa_x" }, {
    fetchImpl: async () => { throw new Error("ECONNREFUSED"); }, log: quiet,
  });
  assert.equal(down, false);
  assert.equal(await forward({ text: "hi" }, {}, { log: quiet }), false);
});

test("the plugin forwards WhatsApp messages and keeps the agent silent", async () => {
  const handlers = {};
  const sent = [];
  const api = {
    pluginConfig: { apiUrl: "https://api.example", token: "mlwa_x" },
    on: (name, handler) => { handlers[name] = handler; },
  };
  register(api, { fetchImpl: async (_url, init) => { sent.push(JSON.parse(init.body)); return { ok: true }; } });

  await handlers.message_received(dm, { channelId: "whatsapp" });
  await handlers.message_received(dm, { channelId: "slack" });
  assert.equal(sent.length, 1);
  assert.equal(sent[0].messages[0].text, "The shipment leaves Friday.");

  assert.deepEqual(handlers.before_dispatch(dm, { channelId: "whatsapp" }), { handled: true });
  assert.equal(handlers.before_dispatch(dm, { channelId: "slack" }), undefined);
  api.pluginConfig.silent = false;
  assert.equal(handlers.before_dispatch(dm, { channelId: "whatsapp" }), undefined);
});
