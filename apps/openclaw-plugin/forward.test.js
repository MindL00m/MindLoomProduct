import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BATCH_SIZE, OUTBOX_LIMIT, Outbox, forward, isSafeApiUrl, isWhatsApp, normalizeWhatsAppId, post, register,
  toMindLoomMessage,
} from "./forward.js";

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

test("the link token is only sent over https, or to this computer", async () => {
  for (const safe of ["https://api.example", "https://api.example:8443/base", "http://localhost:8000", "http://127.0.0.1:8000"]) {
    assert.equal(isSafeApiUrl(safe), true, safe);
  }
  for (const unsafe of ["http://api.example", "ftp://api.example", "api.example", "", undefined, "https://user:pw@api.example", "http://localhost.evil.example"]) {
    assert.equal(isSafeApiUrl(unsafe), false, String(unsafe));
  }
  const calls = [];
  const quiet = { warn() {} };
  const sent = await forward({ text: "hi" }, { apiUrl: "http://api.example", token: "mlwa_x" }, {
    fetchImpl: async (...args) => { calls.push(args); return { ok: true }; }, log: quiet,
  });
  assert.equal(sent, false);
  assert.equal(calls.length, 0);
});

test("post says whether trying again can help", async () => {
  const config = { apiUrl: "https://api.example", token: "mlwa_x" };
  const quiet = { warn() {} };
  const answer = (status) => post([{ text: "hi" }], config, { fetchImpl: async () => ({ ok: status < 300, status }), log: quiet });
  assert.equal(await answer(200), "sent");
  for (const status of [408, 429, 500, 503]) assert.equal(await answer(status), "retry");
  for (const status of [400, 401, 403, 422]) assert.equal(await answer(status), "dropped");
  assert.equal(await post([{ text: "hi" }], config, { fetchImpl: async () => { throw new Error("down"); }, log: quiet }), "retry");
});

test("messages wait out an outage and arrive in order, without duplicates", async () => {
  const quiet = { warn() {} };
  const batches = [];
  let up = false;
  const outbox = new Outbox(() => ({ apiUrl: "https://api.example", token: "mlwa_x" }), {
    log: quiet,
    fetchImpl: async (_url, init) => {
      if (!up) throw new Error("ECONNREFUSED");
      batches.push(JSON.parse(init.body).messages.map((m) => m.text));
      return { ok: true, status: 200 };
    },
  });
  await outbox.add({ text: "1" });
  await outbox.add({ text: "2" });
  assert.deepEqual(batches, []);
  assert.equal(outbox.waiting.length, 2);
  up = true;
  await outbox.add({ text: "3" });
  assert.deepEqual(batches, [["1", "2", "3"]]);
  assert.equal(outbox.waiting.length, 0);
  await outbox.add({ text: "4" });
  assert.deepEqual(batches, [["1", "2", "3"], ["4"]]);
});

test("the outbox sends in batches, keeps only the newest, and drops what MindLoom refuses", async () => {
  const quiet = { warn() {} };
  const sizes = [];
  let status = 503;
  const outbox = new Outbox(() => ({ apiUrl: "https://api.example", token: "mlwa_x" }), {
    log: quiet,
    fetchImpl: async (_url, init) => { sizes.push(JSON.parse(init.body).messages.length); return { ok: status < 300, status }; },
  });
  for (let i = 0; i < OUTBOX_LIMIT + 50; i += 1) outbox.waiting.push({ text: String(i) });
  await outbox.add({ text: "last" });
  assert.equal(outbox.waiting.length, OUTBOX_LIMIT);
  assert.equal(outbox.waiting[0].text, "51");
  status = 200;
  sizes.length = 0;
  await outbox.add({ text: "one more" });
  assert.deepEqual(sizes, [BATCH_SIZE, BATCH_SIZE, BATCH_SIZE, BATCH_SIZE, BATCH_SIZE]);
  assert.equal(outbox.waiting.length, 0);
  status = 401; // a token MindLoom refuses: nothing piles up
  await outbox.add({ text: "x" });
  assert.equal(outbox.waiting.length, 0);
});

test("two messages at once share one attempt", async () => {
  let calls = 0;
  const outbox = new Outbox(() => ({ apiUrl: "https://api.example", token: "mlwa_x" }), {
    fetchImpl: async () => { calls += 1; await new Promise((r) => setTimeout(r, 5)); return { ok: true, status: 200 }; },
  });
  await Promise.all([outbox.add({ text: "a" }), outbox.add({ text: "b" })]);
  assert.equal(outbox.waiting.length, 0);
  assert.ok(calls <= 2);
});
