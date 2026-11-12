import { apiFetch } from "@/lib/api";

export interface WhatsAppLiveStatus {
  linked: boolean;
  linked_at: string | null;
  last_seen_at: string | null;
  messages: number;
  chats: number;
  pending: number;
  latest_message_at: string | null;
  /** Only present right after linking; it is never shown again. */
  token?: string;
}

async function call(path: string, method: string, fallback: string): Promise<WhatsAppLiveStatus> {
  const response = await apiFetch(`/integrations/whatsapp${path}`, { method });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new Error(body.detail || fallback);
  }
  return response.json() as Promise<WhatsAppLiveStatus>;
}

export const getWhatsAppLive = () => call("/live", "GET", "Could not load WhatsApp status.");
/** Create (or replace) the link token the MindLoom OpenClaw plugin sends messages with. */
export const linkWhatsAppLive = () => call("/live/link", "POST", "Could not create a link token.");
export const unlinkWhatsAppLive = () => call("/live", "DELETE", "Could not disconnect WhatsApp.");

export interface WhatsAppLiveChat {
  chat_id: string;
  name: string;
  named_by_you: boolean;
  is_group: boolean;
  messages: number;
  participants: number;
  latest_message_at: string | null;
}

export async function listWhatsAppLiveChats(): Promise<WhatsAppLiveChat[]> {
  const response = await apiFetch("/integrations/whatsapp/live/chats");
  if (!response.ok) throw new Error("Could not load WhatsApp chats.");
  return response.json() as Promise<WhatsAppLiveChat[]>;
}

/** Name a chat (WhatsApp group names are not forwarded). An empty name resets it. */
export async function renameWhatsAppLiveChat(chatId: string, name: string): Promise<WhatsAppLiveChat[]> {
  const response = await apiFetch("/integrations/whatsapp/live/chats/name", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, name }),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new Error(body.detail || "Could not rename the chat.");
  }
  return response.json() as Promise<WhatsAppLiveChat[]>;
}
