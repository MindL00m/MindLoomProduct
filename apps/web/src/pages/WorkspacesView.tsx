import { useCallback, useEffect, useMemo, useState } from "react";
import { Bot, FileText, Loader2, Plus, RefreshCw, Send, Users, X } from "lucide-react";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { listMessageContacts, type MessageContact } from "@/services/reviews";
import {
  createWorkspace,
  listWorkspaceMessages,
  listWorkspaces,
  resyncWorkspaceContext,
  sendWorkspaceMessage,
  type Workspace,
  type WorkspaceMessage,
} from "@/services/workspaces";
import { useSession } from "@/store/session";
import { cn } from "@/lib/utils";

export default function WorkspacesView() {
  const userId = useSession((state) => state.userId);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [contacts, setContacts] = useState<MessageContact[]>([]);
  const [messages, setMessages] = useState<WorkspaceMessage[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [resyncing, setResyncing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [showNew, setShowNew] = useState(false);
  const [showContext, setShowContext] = useState(false);
  const [newName, setNewName] = useState("");
  const [selectedMembers, setSelectedMembers] = useState<string[]>([]);
  const [resyncError, setResyncError] = useState<string | null>(null);

  const active = workspaces.find((row) => row.workspace_id === activeId) ?? null;
  const isContextOnly = active?.loombot_mode === "context_only";

  const loadWorkspaces = useCallback(async () => {
    const rows = await listWorkspaces();
    setWorkspaces(rows);
    setActiveId((current) => current ?? rows[0]?.workspace_id ?? null);
  }, []);

  useEffect(() => {
    Promise.all([
      loadWorkspaces(),
      listMessageContacts().then(setContacts),
    ]).finally(() => setLoading(false));
  }, [loadWorkspaces]);

  useEffect(() => {
    if (!activeId) {
      setMessages([]);
      return;
    }
    void listWorkspaceMessages(activeId).then(setMessages);
  }, [activeId]);

  const subtitle = useMemo(() => {
    if (!active) return "";
    if (active.kind === "org_wide") {
      return `Everyone in the organization · ${active.member_count} members`;
    }
    if (active.loombot_mode === "context_only") {
      const synced = active.context_synced_at
        ? ` · Synced ${new Date(active.context_synced_at).toLocaleString()}`
        : "";
      return `${active.member_count} members · Loombot answers from this workspace’s CONTEXT.md${synced}`;
    }
    return `${active.member_count} members · Mention @Loombot to ask the company brain`;
  }, [active]);

  async function send() {
    if (!activeId || !message.trim()) return;
    setBusy(true);
    try {
      const result = await sendWorkspaceMessage(activeId, message);
      setMessage("");
      setMessages((rows) => [
        ...rows,
        result.message,
        ...(result.bot_message ? [result.bot_message] : []),
      ]);
      await loadWorkspaces();
    } finally {
      setBusy(false);
    }
  }

  async function create() {
    if (!newName.trim()) return;
    setBusy(true);
    try {
      const created = await createWorkspace(newName, selectedMembers);
      setShowNew(false);
      setNewName("");
      setSelectedMembers([]);
      await loadWorkspaces();
      setActiveId(created.workspace_id);
    } finally {
      setBusy(false);
    }
  }

  async function resync() {
    if (!activeId || !isContextOnly) return;
    setResyncing(true);
    setResyncError(null);
    try {
      const updated = await resyncWorkspaceContext(activeId);
      setWorkspaces((rows) =>
        rows.map((row) =>
          row.workspace_id === updated.workspace_id ? { ...row, ...updated } : row,
        ),
      );
    } catch (err) {
      setResyncError(err instanceof Error ? err.message : "Resync failed.");
    } finally {
      setResyncing(false);
    }
  }

  function toggleMember(userIdToToggle: string) {
    setSelectedMembers((current) =>
      current.includes(userIdToToggle)
        ? current.filter((id) => id !== userIdToToggle)
        : [...current, userIdToToggle],
    );
  }

  function insertLoombotMention() {
    setMessage((current) => {
      if (/\b@loombot\b/i.test(current)) return current;
      const trimmed = current.trim();
      return trimmed ? `${trimmed} @Loombot ` : "@Loombot ";
    });
  }

  if (loading) {
    return (
      <div className="flex justify-center py-20">
        <Loader2 className="animate-spin" />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 overflow-hidden rounded-xl border border-border bg-card">
      <aside className="w-80 shrink-0 border-r border-border">
        <div className="flex items-center justify-between border-b border-border p-3">
          <div>
            <h2 className="font-semibold">Workspaces</h2>
            <p className="text-xs text-muted-foreground">Group chats for your team</p>
          </div>
          <button
            className="flex size-9 items-center justify-center rounded-full bg-primary text-primary-foreground"
            onClick={() => setShowNew(true)}
            aria-label="New workspace"
          >
            <Plus className="size-4" />
          </button>
        </div>
        <div className="h-[calc(100%-65px)] overflow-y-auto">
          {workspaces.length === 0 ? (
            <p className="p-6 text-center text-sm text-muted-foreground">
              No workspaces yet.
            </p>
          ) : (
            workspaces.map((workspace) => {
              const selected = workspace.workspace_id === activeId;
              return (
                <button
                  key={workspace.workspace_id}
                  className={cn(
                    "flex w-full gap-3 border-b border-border p-3 text-left",
                    selected ? "bg-primary/10" : "hover:bg-muted/60",
                  )}
                  onClick={() => setActiveId(workspace.workspace_id)}
                >
                  <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-brand-100 text-brand-700">
                    <Users className="size-5" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium">{workspace.name}</span>
                      {workspace.kind === "org_wide" && (
                        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                          Default
                        </span>
                      )}
                    </span>
                    <span className="mt-1 block truncate text-xs text-muted-foreground">
                      {workspace.last_message
                        ? `${workspace.last_sender_name || "Someone"}: ${workspace.last_message}`
                        : `${workspace.member_count} members`}
                    </span>
                  </span>
                </button>
              );
            })
          )}
        </div>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        {active ? (
          <>
            <header className="flex items-start justify-between gap-3 border-b border-border px-5 py-3">
              <div className="min-w-0">
                <p className="font-medium">{active.name}</p>
                <p className="text-xs text-muted-foreground">{subtitle}</p>
                {resyncError && (
                  <p className="mt-1 text-xs text-destructive">{resyncError}</p>
                )}
              </div>
              {isContextOnly && (
                <div className="flex shrink-0 flex-wrap items-center gap-2">
                  <button
                    type="button"
                    className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted"
                    onClick={() => setShowContext(true)}
                  >
                    <FileText className="size-3.5" />
                    CONTEXT.md
                  </button>
                  <button
                    type="button"
                    disabled={resyncing}
                    className="inline-flex items-center gap-1.5 rounded-md bg-primary px-2.5 py-1.5 text-xs font-semibold text-primary-foreground disabled:opacity-50"
                    onClick={() => void resync()}
                  >
                    {resyncing ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <RefreshCw className="size-3.5" />
                    )}
                    Resync
                  </button>
                </div>
              )}
            </header>
            <div className="min-h-0 flex-1 space-y-3 overflow-y-auto bg-muted/20 p-5">
              {messages.length === 0 && (
                <p className="py-10 text-center text-sm text-muted-foreground">
                  Say hello to the team, or ask{" "}
                  <button
                    type="button"
                    className="font-medium text-primary hover:underline"
                    onClick={insertLoombotMention}
                  >
                    @Loombot
                  </button>{" "}
                  {isContextOnly
                    ? "a question from this workspace’s CONTEXT.md."
                    : "a question from company knowledge."}
                </p>
              )}
              {messages.map((item) => {
                const mine = item.sender_user_id === userId;
                const isBot = item.sender_type === "bot";
                return (
                  <div
                    key={item.message_id}
                    className={cn("flex", mine ? "justify-end" : "justify-start")}
                  >
                    <div
                      className={cn(
                        "max-w-[75%] rounded-2xl px-4 py-2 text-sm shadow-sm",
                        isBot
                          ? "rounded-bl-sm border border-brand-200 bg-brand-50 text-foreground"
                          : mine
                            ? "rounded-br-sm bg-primary text-primary-foreground"
                            : "rounded-bl-sm border border-border bg-background",
                      )}
                    >
                      {!mine && (
                        <p
                          className={cn(
                            "mb-1 flex items-center gap-1 text-[11px] font-medium",
                            isBot ? "text-brand-700" : "text-muted-foreground",
                          )}
                        >
                          {isBot && <Bot className="size-3" />}
                          {item.sender_name}
                        </p>
                      )}
                      <p className="whitespace-pre-wrap">{item.body}</p>
                      <p
                        className={cn(
                          "mt-1 text-[10px]",
                          mine ? "text-primary-foreground/70" : "text-muted-foreground",
                        )}
                      >
                        {new Date(item.created_at).toLocaleString()}
                      </p>
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="border-t border-border p-3">
              <div className="mb-2 flex items-center gap-2">
                <button
                  type="button"
                  className="inline-flex items-center gap-1 rounded-full border border-border px-2.5 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
                  onClick={insertLoombotMention}
                >
                  <Bot className="size-3.5" />
                  @Loombot
                </button>
                <span className="text-xs text-muted-foreground">
                  {isContextOnly
                    ? "Mention the bot to answer from CONTEXT.md"
                    : "Mention the bot to answer from company knowledge"}
                </span>
              </div>
              <div className="flex items-end gap-2">
                <textarea
                  className="max-h-32 min-h-11 flex-1 resize-none rounded-xl border border-border bg-background px-3 py-2 text-sm"
                  placeholder={
                    isContextOnly
                      ? "Write a message… use @Loombot to ask from CONTEXT.md"
                      : "Write a message… use @Loombot to ask the company brain"
                  }
                  value={message}
                  onChange={(event) => setMessage(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      void send();
                    }
                  }}
                />
                <button
                  className="flex size-10 items-center justify-center rounded-full bg-primary text-primary-foreground disabled:opacity-40"
                  disabled={busy || !message.trim()}
                  onClick={() => void send()}
                >
                  {busy ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
                </button>
              </div>
            </div>
          </>
        ) : (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            Pick a workspace to start chatting.
          </div>
        )}
      </section>

      {showNew && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/30 p-4">
          <div className="w-full max-w-md space-y-4 rounded-xl bg-background p-5 shadow-xl">
            <div>
              <h3 className="font-semibold">Create a workspace</h3>
              <p className="text-sm text-muted-foreground">
                A group chat for a team, project, or topic.
              </p>
            </div>
            <input
              className="w-full rounded-md border border-border bg-background px-3 py-2"
              placeholder="Workspace name"
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
            />
            <div>
              <p className="mb-2 text-sm font-medium">Members</p>
              <div className="max-h-48 space-y-1 overflow-y-auto rounded-md border border-border p-2">
                {contacts.length === 0 ? (
                  <p className="p-2 text-xs text-muted-foreground">
                    No other signed-in colleagues yet. You can still create this room.
                  </p>
                ) : (
                  contacts.map((contact) => {
                    const checked = selectedMembers.includes(contact.user_id);
                    return (
                      <label
                        key={contact.user_id}
                        className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-muted"
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleMember(contact.user_id)}
                        />
                        <span className="truncate">
                          {contact.name}
                          <span className="text-muted-foreground"> — {contact.email}</span>
                        </span>
                      </label>
                    );
                  })
                )}
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <SecondaryButton onClick={() => setShowNew(false)}>Cancel</SecondaryButton>
              <PrimaryButton
                disabled={busy || !newName.trim()}
                onClick={() => void create()}
              >
                Create workspace
              </PrimaryButton>
            </div>
          </div>
        </div>
      )}

      {showContext && active && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Workspace CONTEXT.md"
          onClick={() => setShowContext(false)}
        >
          <div
            className="flex max-h-[85dvh] w-full max-w-3xl flex-col rounded-lg border border-border bg-background shadow-lg"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <div>
                <h3 className="text-sm font-semibold text-foreground">CONTEXT.md</h3>
                <p className="text-xs text-muted-foreground">
                  {active.name}
                  {active.context_synced_at
                    ? ` · Synced ${new Date(active.context_synced_at).toLocaleString()}`
                    : ""}
                </p>
              </div>
              <button
                type="button"
                aria-label="Close"
                onClick={() => setShowContext(false)}
                className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <X className="size-4" />
              </button>
            </div>
            <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap px-4 py-4 font-mono text-xs leading-relaxed text-foreground">
              {active.context_md?.trim() || "(CONTEXT.md is empty. Use Resync to rebuild it.)"}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}
