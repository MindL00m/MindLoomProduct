import { useEffect, useState } from "react";
import { MessageCircle } from "lucide-react";
import { PrimaryButton } from "@/components/PrimaryButton";
import {
  getWhatsAppLive,
  linkWhatsAppLive,
  listWhatsAppLiveChats,
  renameWhatsAppLiveChat,
  unlinkWhatsAppLive,
  type WhatsAppLiveChat,
  type WhatsAppLiveStatus,
} from "@/services/whatsappLive";

/** Live WhatsApp chats, forwarded by the user's own OpenClaw gateway. */
export function WhatsAppLivePanel() {
  const [status, setStatus] = useState<WhatsAppLiveStatus | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [chats, setChats] = useState<WhatsAppLiveChat[]>([]);

  useEffect(() => {
    getWhatsAppLive().then(setStatus).catch((error: Error) => setNote(error.message));
    listWhatsAppLiveChats().then(setChats).catch(() => setChats([]));
  }, []);

  async function rename(chat: WhatsAppLiveChat) {
    const name = window.prompt(
      chat.is_group
        ? "Name this group (WhatsApp does not share group names with MindLoom):"
        : "Name this chat:",
      chat.named_by_you ? chat.name : "",
    );
    if (name === null) return;
    try {
      setChats(await renameWhatsAppLiveChat(chat.chat_id, name));
      setNote("Renamed. The chat is being read in again under its new name.");
    } catch (error) {
      setNote(error instanceof Error ? error.message : "Could not rename the chat.");
    }
  }

  async function run(action: () => Promise<WhatsAppLiveStatus>, done: string) {
    setBusy(true);
    setNote("");
    try {
      const next = await action();
      setStatus(next);
      setChats(next.linked ? await listWhatsAppLiveChats().catch(() => []) : []);
      setToken(next.token ?? null);
      setNote(done);
    } catch (error) {
      setNote(error instanceof Error ? error.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  const linked = status?.linked ?? false;
  return (
    <section className="rounded-lg border border-border bg-card p-5">
      <h4 className="flex items-center gap-2 font-semibold">
        <MessageCircle className="size-5" />
        Live WhatsApp chats
      </h4>
      <p className="mt-1 text-sm text-muted-foreground">
        Reads the WhatsApp messages you receive, through an OpenClaw gateway running on your own
        computer with the MindLoom plugin. Chats are private to you, and MindLoom never sends
        anything on WhatsApp.
      </p>
      {linked && status && (
        <p className="mt-3 text-sm">
          Connected. {status.messages} message{status.messages === 1 ? "" : "s"} from {status.chats}{" "}
          chat{status.chats === 1 ? "" : "s"}
          {status.pending ? `, ${status.pending} waiting to be read in` : ""}.{" "}
          {status.last_seen_at
            ? `Last heard from your gateway ${new Date(status.last_seen_at).toLocaleString()}.`
            : "Your gateway has not sent anything yet."}
        </p>
      )}
      {token && (
        <div className="mt-3 rounded-md border border-border bg-background p-3 text-sm">
          <p className="font-medium">Your link token (shown only once)</p>
          <code className="mt-1 block break-all text-xs">{token}</code>
          <p className="mt-2 text-muted-foreground">
            Set it as the plugin's <code>token</code> in OpenClaw, as described in
            apps/openclaw-plugin/README.md.
          </p>
        </div>
      )}
      {chats.length > 0 && (
        <ul className="mt-3 divide-y divide-border rounded-md border border-border text-sm">
          {chats.map((chat) => (
            <li key={chat.chat_id} className="flex items-center gap-3 px-3 py-2">
              <span className="min-w-0 flex-1 truncate">
                <span className="font-medium">{chat.name}</span>
                <span className="text-muted-foreground">
                  {" "}
                  · {chat.is_group ? `group, ${chat.participants} people` : "direct"} · {chat.messages}{" "}
                  message{chat.messages === 1 ? "" : "s"}
                </span>
              </span>
              <button className="text-xs underline-offset-2 hover:underline" onClick={() => void rename(chat)}>
                {chat.is_group && !chat.named_by_you ? "Name this group" : "Rename"}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <PrimaryButton
          disabled={busy}
          onClick={() => void run(linkWhatsAppLive, linked ? "New token created. The old one no longer works." : "Link token created.")}
        >
          {linked ? "Replace link token" : "Create link token"}
        </PrimaryButton>
        {linked && (
          <button
            className="text-sm text-destructive underline-offset-2 hover:underline disabled:opacity-50"
            disabled={busy}
            onClick={() => void run(unlinkWhatsAppLive, "Disconnected. Chats already read in stay searchable.")}
          >
            Disconnect
          </button>
        )}
        <span className="text-sm text-muted-foreground">{note}</span>
      </div>
    </section>
  );
}
