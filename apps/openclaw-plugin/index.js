import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

import { register } from "./forward.js";

export default definePluginEntry({
  id: "mindloom-whatsapp",
  name: "MindLoom WhatsApp",
  description: "Forwards inbound WhatsApp messages to MindLoom, read-only.",
  register(api) {
    register(api);
  },
});
