# MindLoom WhatsApp plugin for OpenClaw

Lets MindLoom read the WhatsApp messages you receive. [OpenClaw](https://docs.openclaw.ai)
runs on your own computer and links to WhatsApp as a companion device; this plugin
forwards each inbound text message to MindLoom, where it becomes searchable in Ask.

- **Read-only.** While the plugin is enabled (`silent`, on by default) the OpenClaw
  agent does not reply to anyone on WhatsApp. MindLoom never sends on WhatsApp.
- **Private.** Chats you link are visible only to you in MindLoom.
- **Every chat.** Direct chats and groups, from numbers in any country. Numbers are
  kept in international form (`+<country code><number>`) exactly as WhatsApp gives
  them; no country is assumed. Contacts whose number WhatsApp hides are kept under
  their private id. Status updates and channels are skipped.
- **Text only, incoming only.** Media and reactions are not forwarded. Messages you
  send from your own phone are not forwarded either, because OpenClaw does not hand
  those to plugins, so a chat shows what others wrote to you. (Your own
  "Message yourself" chat does come through.)
- **Group names.** OpenClaw gives plugins a group's id but not its name. A group
  appears in MindLoom as "WhatsApp group 1234" until you name it once under
  Import > Live WhatsApp chats; MindLoom then uses that name everywhere.
- **From now on.** Only messages that arrive after linking are read. For history,
  upload a chat export in MindLoom (Import > Upload files).

## Set up

OpenClaw needs Node 24.16+ (plugins: 24.22.1+). OpenClaw's docs recommend a separate
WhatsApp number; linking adds a companion device to whichever account scans the QR code.

1. Install OpenClaw and its WhatsApp channel, then link by scanning the QR code:

   ```bash
   npm install -g openclaw@latest --allow-scripts=openclaw
   openclaw onboard --install-daemon
   openclaw plugins install @openclaw/whatsapp
   openclaw channels login --channel whatsapp
   ```

2. In MindLoom, open Import > Live WhatsApp chats and create a link token.

3. Install this plugin and give it your MindLoom API address and the token:

   ```bash
   openclaw plugins install --link /path/to/MindLoomProduct/apps/openclaw-plugin --force --accept-capabilities
   openclaw config set plugins.entries.mindloom-whatsapp.config '{"apiUrl":"https://your-mindloom-api.example","token":"mlwa_..."}'
   openclaw plugins enable mindloom-whatsapp
   ```

   OpenClaw will not enable the plugin until `apiUrl` and `token` are set. The rest
   goes in `~/.openclaw/openclaw.json` (or use `openclaw config set`):

   ```json5
   {
     plugins: { entries: { "mindloom-whatsapp": { config: {
       apiUrl: "https://your-mindloom-api.example",
       token: "mlwa_...",
     } } } },
     channels: { whatsapp: {
       pluginHooks: { messageReceived: true },   // lets the plugin see inbound messages
       dmPolicy: "open", allowFrom: ["*"],       // every direct chat
       groupPolicy: "open",
       groups: { "*": { requireMention: false } }, // every group
       sendReadReceipts: false,                  // do not mark chats as read
     } },
     messages: { ackReaction: "" },
   }
   ```

4. Restart the gateway (`openclaw gateway`) and check `openclaw plugins inspect mindloom-whatsapp --runtime --json`.

Messages appear in MindLoom a couple of minutes after a chat goes quiet.

## What has been verified

Checked on OpenClaw 2026.11.3 with a real linked WhatsApp account (12 Nov 2026):

- **Direct messages** arrive in MindLoom with the right sender name, number,
  WhatsApp message id and timestamp.
- **Staying silent** works: the gateway logs each turn as
  `cause=completed:before_dispatch_handled` with no reply sent.
- A gateway that has never been through `openclaw onboard` also needs
  `openclaw config set gateway.mode local` before it will start.

**Group messages** are handled from OpenClaw's source rather than from a live group
message: the event carries the group JID as `ctx.conversationId`, the speaker in
`metadata.senderE164` / `metadata.senderName`, and no group name. That shape is
covered by `forward.test.js`. If a real group ever shows up wrongly, compare the
event against `toMindLoomMessage` in `forward.js`.
