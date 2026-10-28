import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  FileText,
  Loader2,
  Paperclip,
  Plus,
  RefreshCw,
  Send,
  UserPlus,
  Users,
  X,
} from "lucide-react";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { Markdown } from "@/components/Markdown";
import { FileKindIcon } from "@/components/FileKindIcon";
import { listMessageContacts, type MessageContact } from "@/services/reviews";
import {
  addWorkspaceMembers,
  addWorkspaceMembersByEmail,
  cancelWorkspaceInvite,
  createWorkspace,
  listWorkspaceInvites,
  listWorkspaceMembers,
  listWorkspaceMessages,
  listWorkspaces,
  removeWorkspaceMember,
  resyncWorkspaceContext,
  saveCardState,
  sendWorkspaceMessage,
  type LoombotPayload,
  type Workspace,
  type WorkspaceInvite,
  type WorkspaceMember,
  type WorkspaceMessage,
} from "@/services/workspaces";
import { FILE_UPLOAD_ACCEPT, kindFromName, uploadFile } from "@/services/files";
import { userTimezone } from "@/services/workflows";
import {
  AnswerView,
  SourceList,
  WebSearchPill,
  WebSources,
  citationRenderer,
  type AnswerState,
} from "@/pages/AskView";
import type { Turn } from "@/store/chat";
import { useSession } from "@/store/session";
import { cn } from "@/lib/utils";

const LOOMBOT_MENTION = /(?:^|[\s([{])@loombot\b/i;

interface Attachment {
  id: string;
  name: string;
  status: "uploading" | "ready" | "error";
  error?: string;
}

/** Card state worth keeping after a reload: in-flight states fall back to pending. */
function persistable(state: AnswerState): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(state)) {
    if (key.endsWith("Error")) continue;
    if (value === "sending") {
      out[key] = "pending";
    } else if (key === "fileChangeState" && value && typeof value === "object") {
      const entries: Record<string, unknown> = {};
      for (const [fileId, entry] of Object.entries(value as Record<string, Record<string, unknown>>)) {
        const { status, monitor, error: _error, ...rest } = entry;
        entries[fileId] = {
          ...rest,
          ...(status === "undone" ? { status } : {}),
          ...(monitor === "on" || monitor === "declined" ? { monitor } : {}),
        };
      }
      out[key] = entries;
    } else {
      out[key] = value;
    }
  }
  return out;
}

export default function WorkspacesView() {
  const userId = useSession((state) => state.userId);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [contacts, setContacts] = useState<MessageContact[]>([]);
  const [messages, setMessages] = useState<WorkspaceMessage[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  // The workspace where Loombot is working on a reply, if any.
  const [thinkingIn, setThinkingIn] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [resyncing, setResyncing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [showNew, setShowNew] = useState(false);
  const [showContext, setShowContext] = useState(false);
  const [showMembers, setShowMembers] = useState(false);
  const [newName, setNewName] = useState("");
  const [selectedMembers, setSelectedMembers] = useState<string[]>([]);
  const [resyncError, setResyncError] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [cardStates, setCardStates] = useState<Record<string, AnswerState>>({});
  const cardStatesRef = useRef(cardStates);
  // Card saves run one after another per message, so "sent" never loses to an
  // earlier "pending" that arrives late.
  const cardSaves = useRef(new Map<string, Promise<unknown>>());
  const activeIdRef = useRef(activeId);
  activeIdRef.current = activeId;
  const fileInput = useRef<HTMLInputElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

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

  const loadMessages = useCallback(async (workspaceId: string) => {
    const rows = await listWorkspaceMessages(workspaceId);
    // Ignore a slow response for a workspace the user has already left.
    if (activeIdRef.current === workspaceId) setMessages(rows);
  }, []);

  useEffect(() => {
    setAttachments([]);
    setSendError(null);
    if (!activeId) {
      setMessages([]);
      return;
    }
    void loadMessages(activeId);
  }, [activeId, loadMessages]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, thinkingIn, activeId]);

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

  const readyAttachments = attachments.filter((a) => a.status === "ready").map((a) => a.name);

  async function send() {
    if (!activeId || !message.trim() || busy) return;
    const workspaceId = activeId;
    const stillHere = () => activeIdRef.current === workspaceId;
    const body = message;
    const asksLoombot = LOOMBOT_MENTION.test(` ${body}`);
    const optimistic: WorkspaceMessage = {
      message_id: `pending-${Date.now()}`,
      sender_user_id: userId,
      sender_type: "user",
      sender_name: "You",
      body: body.trim(),
      created_at: new Date().toISOString(),
    };
    setBusy(true);
    setSendError(null);
    setThinkingIn(asksLoombot ? workspaceId : null);
    setMessage("");
    setMessages((rows) => [...rows, optimistic]);
    try {
      const result = await sendWorkspaceMessage(workspaceId, body, {
        timezone: userTimezone(),
        attachedFileNames: asksLoombot ? readyAttachments : [],
      });
      // If the user switched rooms while Loombot worked, the reply loads with that
      // room next time; it must not be added to the room now on screen.
      if (stillHere()) {
        setMessages((rows) => [
          ...rows.filter((row) => row.message_id !== optimistic.message_id),
          result.message,
          ...(result.bot_message ? [result.bot_message] : []),
        ]);
        if (asksLoombot) setAttachments([]);
      }
      await loadWorkspaces();
    } catch (err) {
      if (stillHere()) {
        setMessages((rows) => rows.filter((row) => row.message_id !== optimistic.message_id));
        setMessage(body);
        setSendError(err instanceof Error ? err.message : "Could not send the message.");
      }
    } finally {
      setBusy(false);
      setThinkingIn(null);
    }
  }

  async function attach(list: FileList | null) {
    const file = list?.[0];
    if (!file) return;
    if (!kindFromName(file.name)) {
      setSendError("Attach a .csv, .txt, .docx, .xlsx, or .pdf file.");
      return;
    }
    const id = crypto.randomUUID();
    setAttachments((rows) => [...rows, { id, name: file.name, status: "uploading" }]);
    try {
      const saved = await uploadFile(file);
      setAttachments((rows) =>
        rows.map((row) => (row.id === id ? { ...row, name: saved.name, status: "ready" } : row)),
      );
    } catch (err) {
      setAttachments((rows) =>
        rows.map((row) =>
          row.id === id
            ? { ...row, status: "error", error: err instanceof Error ? err.message : "Upload failed." }
            : row,
        ),
      );
    }
  }

  function patchCard(item: WorkspaceMessage, patch: Partial<Turn>) {
    const workspaceId = activeIdRef.current;
    const id = item.message_id;
    const base = cardStatesRef.current[id] ?? ((item.card_state ?? {}) as AnswerState);
    const next: AnswerState = { ...base, ...patch };
    cardStatesRef.current = { ...cardStatesRef.current, [id]: next };
    setCardStates(cardStatesRef.current);
    // In-flight states ("sending") are not worth a save; the final state follows.
    const inFlight = Object.values(patch).some((value) => value === "sending");
    if (!workspaceId || inFlight) return;
    const previous = cardSaves.current.get(id) ?? Promise.resolve();
    const saved = previous
      .catch(() => undefined)
      .then(() => saveCardState(workspaceId, id, persistable(cardStatesRef.current[id] ?? next)))
      .catch(() => {
        /* the card still works locally; it just won't show the result after a reload */
      });
    cardSaves.current.set(id, saved);
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
      <aside className="w-80 shrink-0 border-r border-border max-md:hidden">
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
        <div className="flex items-center gap-2 border-b border-border p-2 md:hidden">
          <select
            className="min-w-0 flex-1 rounded-md border border-border bg-background p-2 text-sm"
            value={activeId ?? ""}
            onChange={(e) => e.target.value && setActiveId(e.target.value)}
            aria-label="Workspace"
          >
            {workspaces.map((w) => (
              <option key={w.workspace_id} value={w.workspace_id}>
                {w.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => setShowNew(true)}
            className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-xs font-medium text-primary-foreground"
          >
            <Plus className="size-3.5" /> New
          </button>
        </div>
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
              <div className="flex shrink-0 flex-wrap items-center gap-2">
                <button
                  type="button"
                  className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted"
                  onClick={() => setShowMembers(true)}
                >
                  <Users className="size-3.5" />
                  Members
                  {active.kind !== "org_wide" && <UserPlus className="size-3.5 text-primary" />}
                </button>
                {isContextOnly && (
                  <>
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
                  </>
                )}
              </div>
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
                  anything you would ask in Ask: questions, emails, meetings, files, and status
                  tracking.
                </p>
              )}
              {messages.map((item) => (
                <MessageRow
                  key={item.message_id}
                  item={item}
                  userId={userId}
                  messages={messages}
                  cardState={cardStates[item.message_id] ?? ((item.card_state ?? {}) as AnswerState)}
                  onPatch={(patch) => patchCard(item, patch)}
                />
              ))}
              {thinkingIn === activeId && (
                <div className="flex justify-start">
                  <div className="flex items-center gap-2 rounded-2xl rounded-bl-sm border border-brand-200 bg-brand-50 px-4 py-2 text-sm text-muted-foreground">
                    <Loader2 className="size-4 animate-spin text-brand-700" /> Loombot is working on it…
                  </div>
                </div>
              )}
              <div ref={bottomRef} />
            </div>
            <div className="border-t border-border p-3">
              <div className="mb-2 flex flex-wrap items-center gap-2">
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
                    ? "Answers from CONTEXT.md, and can email, schedule meetings, write files, and track status for you"
                    : "Answers from company knowledge, and can email, schedule meetings, write files, and track status for you"}
                </span>
              </div>
              {attachments.length > 0 && (
                <div className="mb-2 flex flex-wrap gap-1.5">
                  {attachments.map((attachment) => (
                    <span
                      key={attachment.id}
                      title={attachment.error}
                      className={cn(
                        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs",
                        attachment.status === "error"
                          ? "border-destructive/30 bg-destructive/10 text-destructive"
                          : "border-border bg-background",
                      )}
                    >
                      {attachment.status === "uploading" ? (
                        <Loader2 className="size-3 animate-spin" />
                      ) : (
                        <FileKindIcon kind={kindFromName(attachment.name) ?? "txt"} className="size-3" />
                      )}
                      {attachment.name}
                      {attachment.status === "error" && ` · ${attachment.error}`}
                      <button
                        type="button"
                        aria-label={`Remove ${attachment.name}`}
                        onClick={() => setAttachments((rows) => rows.filter((r) => r.id !== attachment.id))}
                        className="text-muted-foreground hover:text-foreground"
                      >
                        <X className="size-3" />
                      </button>
                    </span>
                  ))}
                  <span className="self-center text-[11px] text-muted-foreground">
                    Saved to your Files. Loombot reads them with your next @Loombot message.
                  </span>
                </div>
              )}
              {sendError && <p className="mb-2 text-xs text-destructive">{sendError}</p>}
              <input
                ref={fileInput}
                type="file"
                accept={FILE_UPLOAD_ACCEPT}
                className="sr-only"
                onChange={(event) => {
                  void attach(event.target.files);
                  event.target.value = "";
                }}
              />
              <div className="flex items-end gap-2">
                <button
                  type="button"
                  onClick={() => fileInput.current?.click()}
                  aria-label="Attach a file for Loombot"
                  className="flex size-10 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <Paperclip className="size-4" />
                </button>
                <textarea
                  className="max-h-32 min-h-11 flex-1 resize-none rounded-xl border border-border bg-background px-3 py-2 text-sm"
                  placeholder={
                    isContextOnly
                      ? "Write a message… use @Loombot to ask from CONTEXT.md or get things done"
                      : "Write a message… use @Loombot to ask the company brain or get things done"
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
                  aria-label="Send"
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
                A group chat for a team, project, or topic. You can add more people later.
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

      {showMembers && active && (
        <MembersDialog
          workspace={active}
          contacts={contacts}
          userId={userId}
          onClose={() => setShowMembers(false)}
          onAdded={async () => {
            await Promise.all([loadWorkspaces(), loadMessages(active.workspace_id)]);
          }}
          onLeft={async () => {
            setShowMembers(false);
            await loadWorkspaces();
          }}
        />
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

function MessageRow({
  item,
  userId,
  messages,
  cardState,
  onPatch,
}: {
  item: WorkspaceMessage;
  userId: string | null;
  messages: WorkspaceMessage[];
  cardState: AnswerState;
  onPatch: (patch: Partial<Turn>) => void;
}) {
  const mine = item.sender_user_id === userId;
  const isBot = item.sender_type === "bot";

  if (item.sender_type === "system") {
    return (
      <p className="text-center text-xs text-muted-foreground" role="status">
        {item.body} · {new Date(item.created_at).toLocaleString()}
      </p>
    );
  }

  const payload = isBot ? item.payload ?? null : null;
  const askedByMe = Boolean(
    payload && !payload.shared_view && item.requested_by && item.requested_by === userId,
  );
  const asker =
    messages.find(
      (m) => m.sender_type === "user" && m.sender_user_id === item.requested_by,
    )?.sender_name ?? "the person who asked";

  return (
    <div className={cn("flex", mine ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "rounded-2xl px-4 py-2 text-sm shadow-sm",
          isBot
            ? "w-full max-w-[min(100%,48rem)] rounded-bl-sm border border-brand-200 bg-brand-50 text-foreground"
            : mine
              ? "max-w-[75%] rounded-br-sm bg-primary text-primary-foreground"
              : "max-w-[75%] rounded-bl-sm border border-border bg-background",
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
            {isBot && payload && item.requested_by && (
              <span className="font-normal text-muted-foreground">
                {" "}· replying to {askedByMe ? "you" : asker}
              </span>
            )}
          </p>
        )}
        {payload && askedByMe ? (
          <AnswerView response={payload} state={cardState} onProposalPatch={onPatch} />
        ) : payload ? (
          <SharedAnswer payload={payload} asker={asker} />
        ) : isBot ? (
          <Markdown text={item.body} />
        ) : (
          <p className="whitespace-pre-wrap">{item.body}</p>
        )}
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
}

/**
 * A Loombot reply as other members see it: the answer and any web results. The
 * asker's sources and drafts stay private to them; members see only what was prepared.
 */
function SharedAnswer({ payload, asker }: { payload: LoombotPayload; asker: string }) {
  const summaries = payload.card_summaries ?? [];
  const outcomes = payload.card_outcomes ?? [];
  const webResults = payload.web_results ?? [];
  const sources = payload.sources ?? [];
  return (
    <div className="space-y-3">
      {payload.web_search_used && <WebSearchPill query={payload.web_search_query} />}
      <Markdown text={payload.answer} renderCitation={citationRenderer(sources, webResults)} />
      {summaries.length > 0 && (
        <p className="rounded-md border border-border bg-card px-3 py-2 text-xs text-muted-foreground">
          Loombot prepared {summaries.join("; ")} for {asker}. Only {asker} can see the details
          and approve them.
          {outcomes.length > 0 && (
            <span className="mt-1 block font-medium text-foreground">
              {asker}: {outcomes.join(", ")}.
            </span>
          )}
        </p>
      )}
      {webResults.length > 0 && <WebSources results={webResults} />}
      {sources.length > 0 && <SourceList sources={sources} cited={new Set()} />}
    </div>
  );
}

function MembersDialog({
  workspace,
  contacts,
  userId,
  onClose,
  onAdded,
  onLeft,
}: {
  workspace: Workspace;
  contacts: MessageContact[];
  userId: string | null;
  onClose: () => void;
  onAdded: () => Promise<void>;
  onLeft: () => Promise<void>;
}) {
  const [members, setMembers] = useState<WorkspaceMember[] | null>(null);
  const [invites, setInvites] = useState<WorkspaceInvite[]>([]);
  const [emailInput, setEmailInput] = useState("");
  const [sendInviteEmail, setSendInviteEmail] = useState(true);
  const isCreator = Boolean(userId && workspace.created_by === userId);
  const [selected, setSelected] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const canAdd = workspace.kind !== "org_wide";

  useEffect(() => {
    let cancelled = false;
    void listWorkspaceMembers(workspace.workspace_id)
      .then((rows) => !cancelled && setMembers(rows))
      .catch((err: Error) => !cancelled && setError(err.message));
    if (workspace.kind !== "org_wide") {
      void listWorkspaceInvites(workspace.workspace_id)
        .then((rows) => !cancelled && setInvites(rows))
        .catch(() => undefined);
    }
    return () => {
      cancelled = true;
    };
  }, [workspace.workspace_id]);

  const memberIds = new Set((members ?? []).map((m) => m.user_id));
  const query = filter.trim().toLowerCase();
  const available = contacts.filter(
    (c) =>
      !memberIds.has(c.user_id) &&
      (!query || c.name.toLowerCase().includes(query) || c.email.toLowerCase().includes(query)),
  );

  async function add() {
    if (selected.length === 0) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const result = await addWorkspaceMembers(workspace.workspace_id, selected);
      setMembers(result.members);
      setSelected([]);
      setNotice(
        result.added.length
          ? `Added ${result.added.map((m) => m.name).join(", ")}.`
          : "Everyone you picked is already a member.",
      );
      await onAdded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add members.");
    } finally {
      setSaving(false);
    }
  }

  async function addByEmail() {
    const emails = emailInput
      .split(/[\s,;]+/)
      .map((e) => e.trim())
      .filter(Boolean);
    if (emails.length === 0) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const result = await addWorkspaceMembersByEmail(
        workspace.workspace_id,
        emails,
        sendInviteEmail,
      );
      setMembers(result.members);
      setInvites(result.invites);
      setEmailInput("");
      const parts: string[] = [];
      if (result.added.length) parts.push(`Added ${result.added.map((m) => m.name).join(", ")}.`);
      if (result.invited.length) {
        parts.push(
          `Invited ${result.invited.join(", ")}` +
            (result.invite_emails_sent
              ? " and emailed them."
              : ". They join when they first sign in."),
        );
      }
      if (result.already_members.length) parts.push(`Already members: ${result.already_members.join(", ")}.`);
      if (result.already_invited.length) parts.push(`Already invited: ${result.already_invited.join(", ")}.`);
      if (result.outside_organization.length) {
        parts.push(
          `Not added (outside your organization's email domain): ${result.outside_organization.join(", ")}.`,
        );
      }
      if (result.invalid.length) parts.push(`Not an email address: ${result.invalid.join(", ")}.`);
      setNotice(parts.join(" ") || "Nothing to add.");
      await onAdded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add people.");
    } finally {
      setSaving(false);
    }
  }

  async function remove(member: WorkspaceMember) {
    const leaving = member.user_id === userId;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const result = await removeWorkspaceMember(workspace.workspace_id, member.user_id);
      if (leaving || result.left) {
        await onLeft();
        return;
      }
      setMembers(result.members);
      setNotice(`Removed ${member.name}.`);
      await onAdded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove the member.");
    } finally {
      setSaving(false);
    }
  }

  async function cancelInvite(invite: WorkspaceInvite) {
    setError(null);
    try {
      const result = await cancelWorkspaceInvite(workspace.workspace_id, invite.invite_id);
      setInvites(result.invites);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not cancel the invite.");
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/30 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={`Members of ${workspace.name}`}
      onClick={onClose}
    >
      <div
        className="flex max-h-[85dvh] w-full max-w-md flex-col gap-4 rounded-xl bg-background p-5 shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-2">
          <div>
            <h3 className="font-semibold">Members of {workspace.name}</h3>
            <p className="text-sm text-muted-foreground">
              {canAdd
                ? "Anyone in the workspace can add colleagues. New members see the whole chat history."
                : "Everyone in your organization is in this workspace automatically."}
            </p>
          </div>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="min-h-0 space-y-1 overflow-y-auto rounded-md border border-border p-2">
          {members === null ? (
            <p className="flex items-center gap-2 p-2 text-xs text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" /> Loading members…
            </p>
          ) : (
            members.map((member) => (
              <div key={member.user_id} className="flex items-center gap-2 px-2 py-1">
                <p className="min-w-0 flex-1 truncate text-sm">
                  {member.name}
                  <span className="text-muted-foreground"> — {member.email}</span>
                </p>
                {canAdd && (member.user_id === userId || isCreator) && (
                  <button
                    type="button"
                    disabled={saving}
                    onClick={() => void remove(member)}
                    className="shrink-0 rounded px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-muted hover:text-destructive"
                  >
                    {member.user_id === userId ? "Leave" : "Remove"}
                  </button>
                )}
              </div>
            ))
          )}
          {invites.map((invite) => (
            <div key={invite.invite_id} className="flex items-center gap-2 px-2 py-1">
              <p className="min-w-0 flex-1 truncate text-sm text-muted-foreground">
                {invite.email}
                <span className="ml-1 rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase">
                  Invited
                </span>
              </p>
              <button
                type="button"
                onClick={() => void cancelInvite(invite)}
                className="shrink-0 rounded px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-muted hover:text-destructive"
              >
                Cancel
              </button>
            </div>
          ))}
        </div>

        {canAdd && (
          <div className="space-y-2">
            <p className="text-sm font-medium">Add by email</p>
            <div className="flex gap-2">
              <input
                value={emailInput}
                onChange={(e) => setEmailInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    void addByEmail();
                  }
                }}
                placeholder="priya@company.com, sam@company.com"
                aria-label="Email addresses to add"
                className="min-w-0 flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm"
              />
              <SecondaryButton
                disabled={saving || !emailInput.trim()}
                onClick={() => void addByEmail()}
              >
                Add
              </SecondaryButton>
            </div>
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={sendInviteEmail}
                onChange={(e) => setSendInviteEmail(e.target.checked)}
              />
              Email an invite to people who have not signed in yet (from your Gmail)
            </label>
          </div>
        )}

        {canAdd && (
          <div className="space-y-2">
            <p className="text-sm font-medium">Add people</p>
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Search colleagues"
              aria-label="Search colleagues"
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            />
            <div className="max-h-40 space-y-1 overflow-y-auto rounded-md border border-border p-2">
              {available.length === 0 ? (
                <p className="p-2 text-xs text-muted-foreground">
                  {contacts.length === 0
                    ? "No other signed-in colleagues yet."
                    : "Everyone who matches is already a member."}
                </p>
              ) : (
                available.map((contact) => (
                  <label
                    key={contact.user_id}
                    className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-muted"
                  >
                    <input
                      type="checkbox"
                      checked={selected.includes(contact.user_id)}
                      onChange={() =>
                        setSelected((current) =>
                          current.includes(contact.user_id)
                            ? current.filter((id) => id !== contact.user_id)
                            : [...current, contact.user_id],
                        )
                      }
                    />
                    <span className="truncate">
                      {contact.name}
                      <span className="text-muted-foreground"> — {contact.email}</span>
                    </span>
                  </label>
                ))
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              Colleagues who have not signed in yet can be added by email above; they join
              on their first sign-in. You can also ask “@Loombot add Priya to this workspace”.
            </p>
          </div>
        )}

        {error && <p className="text-xs text-destructive">{error}</p>}
        {notice && <p className="text-xs text-foreground">{notice}</p>}

        <div className="flex justify-end gap-2">
          <SecondaryButton onClick={onClose}>Close</SecondaryButton>
          {canAdd && (
            <PrimaryButton disabled={saving || selected.length === 0} onClick={() => void add()}>
              {saving
                ? "Adding…"
                : selected.length > 1
                  ? `Add ${selected.length} people`
                  : "Add to workspace"}
            </PrimaryButton>
          )}
        </div>
      </div>
    </div>
  );
}
