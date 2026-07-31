import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, Loader2, MessageCircle, Paperclip, Plus, Send } from "lucide-react";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import {
  answerExpertRequestWithMedia,
  listExpertThreads,
  listMessageContacts,
  listThreadMessages,
  sendExpertMessage,
  startExpertThread,
  type ExpertMessage,
  type ExpertThread,
  type MessageContact,
} from "@/services/reviews";
import {
  listSkillFiles,
  reviewSkillFile,
  type SkillFile,
} from "@/services/skillFiles";
import { useSession } from "@/store/session";
import { cn } from "@/lib/utils";

export default function ExpertMessages({
  onCountChange,
}: {
  onCountChange?: (count: number) => void;
}) {
  const userId = useSession((state) => state.userId);
  const [threads, setThreads] = useState<ExpertThread[]>([]);
  const [contacts, setContacts] = useState<MessageContact[]>([]);
  const [messages, setMessages] = useState<ExpertMessage[]>([]);
  const [skills, setSkills] = useState<SkillFile[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [showNew, setShowNew] = useState(false);
  const [newExpert, setNewExpert] = useState("");
  const [newMessage, setNewMessage] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  const active = threads.find((thread) => thread.review_id === activeId) ?? null;
  const activeSkill = skills.find(
    (skill) => skill.session_id === `expert-request:${activeId}`,
  );
  const isExpert = active?.owner_user_id === userId;

  const loadThreads = useCallback(async () => {
    const rows = await listExpertThreads();
    setThreads(rows);
    onCountChange?.(rows.reduce((total, row) => total + Number(row.unread_count), 0));
    setActiveId((current) => current ?? rows[0]?.review_id ?? null);
  }, [onCountChange]);

  useEffect(() => {
    Promise.all([
      loadThreads(),
      listMessageContacts().then(setContacts),
      listSkillFiles().then(setSkills),
    ]).finally(() => setLoading(false));
  }, [loadThreads]);

  useEffect(() => {
    if (!activeId) {
      setMessages([]);
      return;
    }
    void listThreadMessages(activeId).then((rows) => {
      setMessages(rows);
      void loadThreads();
    });
  }, [activeId, loadThreads]);

  const otherPerson = useMemo(() => {
    if (!active) return "";
    return isExpert
      ? active.requester_name || active.requester_email || "Employee"
      : active.expert_name || active.expert_email || "Expert";
  }, [active, isExpert]);

  async function send() {
    if (!activeId || !message.trim()) return;
    setBusy(true);
    try {
      await sendExpertMessage(activeId, message);
      setMessage("");
      setMessages(await listThreadMessages(activeId));
      await loadThreads();
    } finally {
      setBusy(false);
    }
  }

  async function createKnowledgeDraft() {
    if (!activeId || (!message.trim() && !file)) return;
    setBusy(true);
    setNotice(null);
    try {
      await answerExpertRequestWithMedia(activeId, message, file);
      setMessage("");
      setFile(null);
      setNotice("Knowledge draft created inside this conversation.");
      const [nextMessages, nextSkills] = await Promise.all([
        listThreadMessages(activeId),
        listSkillFiles(),
      ]);
      setMessages(nextMessages);
      setSkills(nextSkills);
      await loadThreads();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not create draft.");
    } finally {
      setBusy(false);
    }
  }

  async function startConversation() {
    if (!newExpert) return;
    const existing = threads.find(
      (thread) =>
        thread.owner_user_id === newExpert || thread.created_by === newExpert,
    );
    if (existing && !newMessage.trim()) {
      setShowNew(false);
      setNewExpert("");
      setActiveId(existing.review_id);
      return;
    }
    if (!newMessage.trim()) return;
    setBusy(true);
    try {
      const result = await startExpertThread(newExpert, newMessage);
      setShowNew(false);
      setNewExpert("");
      setNewMessage("");
      await loadThreads();
      setActiveId(result.review_id);
    } finally {
      setBusy(false);
    }
  }

  async function decideSkill(status: "approved" | "rejected") {
    if (!activeSkill) return;
    setBusy(true);
    try {
      const updated = await reviewSkillFile(activeSkill, status);
      setSkills((rows) =>
        rows.map((skill) => skill.skill_id === updated.skill_id ? updated : skill),
      );
      setNotice(
        status === "approved"
          ? "Knowledge approved and published."
          : "Knowledge draft rejected.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return <div className="flex justify-center py-20"><Loader2 className="animate-spin" /></div>;
  }

  return (
    <div className="flex h-full min-h-0 overflow-hidden rounded-xl border border-border bg-card">
      <aside className="w-80 shrink-0 border-r border-border">
        <div className="flex items-center justify-between border-b border-border p-3">
          <div>
            <h2 className="font-semibold">Expert messages</h2>
            <p className="text-xs text-muted-foreground">Questions and direct chats</p>
          </div>
          <button
            className="flex size-9 items-center justify-center rounded-full bg-primary text-primary-foreground"
            onClick={() => setShowNew(true)}
            aria-label="New expert conversation"
          >
            <Plus className="size-4" />
          </button>
        </div>
        <div className="h-[calc(100%-65px)] overflow-y-auto">
          {threads.length === 0 ? (
            <p className="p-6 text-center text-sm text-muted-foreground">No expert conversations yet.</p>
          ) : threads.map((thread) => {
            const selected = thread.review_id === activeId;
            const name =
              thread.owner_user_id === userId
                ? thread.requester_name || thread.requester_email
                : thread.expert_name || thread.expert_email;
            return (
              <button
                key={thread.review_id}
                className={cn(
                  "flex w-full gap-3 border-b border-border p-3 text-left",
                  selected ? "bg-primary/10" : "hover:bg-muted/60",
                )}
                onClick={() => setActiveId(thread.review_id)}
              >
                <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-brand-100 text-brand-700">
                  <MessageCircle className="size-5" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium">{name || "Expert conversation"}</span>
                    {thread.unread_count > 0 && (
                      <span className="rounded-full bg-primary px-2 text-xs text-primary-foreground">
                        {thread.unread_count}
                      </span>
                    )}
                  </span>
                  <span className="mt-1 block truncate text-xs text-muted-foreground">
                    {thread.last_message || thread.title}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        {active ? (
          <>
            <header className="border-b border-border px-5 py-3">
              <p className="font-medium">{otherPerson}</p>
              <p className="text-xs text-muted-foreground">
                {isExpert ? "Employee asking for your knowledge" : "Company expert"}
              </p>
            </header>
            <div className="min-h-0 flex-1 space-y-3 overflow-y-auto bg-muted/20 p-5">
              {messages.map((item) => {
                const mine = item.sender_user_id === userId;
                return (
                  <div key={item.message_id} className={cn("flex", mine ? "justify-end" : "justify-start")}>
                    <div className={cn(
                      "max-w-[75%] rounded-2xl px-4 py-2 text-sm shadow-sm",
                      mine
                        ? "rounded-br-sm bg-primary text-primary-foreground"
                        : "rounded-bl-sm border border-border bg-background",
                    )}>
                      <p className="whitespace-pre-wrap">{item.body}</p>
                      <p className={cn("mt-1 text-[10px]", mine ? "text-primary-foreground/70" : "text-muted-foreground")}>
                        {new Date(item.created_at).toLocaleString()}
                      </p>
                    </div>
                  </div>
                );
              })}

              {activeSkill && (
                <div className="mx-auto max-w-2xl rounded-xl border border-brand-200 bg-background p-4 shadow-sm">
                  <div className="flex items-center justify-between">
                    <p className="font-medium">Knowledge draft</p>
                    <span className="text-xs capitalize text-muted-foreground">{activeSkill.status}</span>
                  </div>
                  <p className="mt-1 text-sm font-medium">{activeSkill.title}</p>
                  <p className="mt-1 text-sm text-muted-foreground">{activeSkill.purpose}</p>
                  <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm">
                    {activeSkill.steps.map((step) => <li key={step}>{step}</li>)}
                  </ol>
                  {activeSkill.follow_up_questions.length > 0 && (
                    <div className="mt-3 rounded-md bg-muted p-3 text-sm">
                      <p className="font-medium">Follow-up needed</p>
                      {activeSkill.follow_up_questions.map((question) => <p key={question}>• {question}</p>)}
                    </div>
                  )}
                  {isExpert && activeSkill.status === "proposed" && (
                    <div className="mt-4 flex gap-2">
                      <SecondaryButton onClick={() => void decideSkill("rejected")}>Reject</SecondaryButton>
                      <PrimaryButton onClick={() => void decideSkill("approved")}>
                        <Check className="size-4" /> Approve knowledge
                      </PrimaryButton>
                    </div>
                  )}
                </div>
              )}
              {notice && <p className="text-center text-xs text-muted-foreground">{notice}</p>}
            </div>
            <div className="border-t border-border p-3">
              {file && <p className="mb-2 text-xs text-muted-foreground">Attached: {file.name}</p>}
              <div className="flex items-end gap-2">
                {isExpert && active.status === "open" && (
                  <label className="flex size-10 cursor-pointer items-center justify-center rounded-full hover:bg-muted">
                    <Paperclip className="size-4" />
                    <input
                      className="sr-only"
                      type="file"
                      accept="image/*,audio/*,video/*,text/plain"
                      onChange={(event) => setFile(event.target.files?.[0] || null)}
                    />
                  </label>
                )}
                <textarea
                  className="max-h-32 min-h-11 flex-1 resize-none rounded-xl border border-border bg-background px-3 py-2 text-sm"
                  placeholder="Write a message…"
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
              {isExpert && active.status === "open" && (
                <button
                  className="mt-2 text-xs font-medium text-primary hover:underline"
                  disabled={busy || (!message.trim() && !file)}
                  onClick={() => void createKnowledgeDraft()}
                >
                  Turn this response into reusable company knowledge
                </button>
              )}
            </div>
          </>
        ) : (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            Start a conversation with a company expert.
          </div>
        )}
      </section>

      {showNew && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/30 p-4">
          <div className="w-full max-w-md space-y-4 rounded-xl bg-background p-5 shadow-xl">
            <div>
              <h3 className="font-semibold">Message an expert</h3>
              <p className="text-sm text-muted-foreground">Choose a colleague and ask directly.</p>
            </div>
            <select
              className="w-full rounded-md border border-border bg-background px-3 py-2"
              value={newExpert}
              onChange={(event) => setNewExpert(event.target.value)}
            >
              <option value="">Choose a person</option>
              {contacts.map((contact) => (
                <option key={contact.user_id} value={contact.user_id}>
                  {contact.name} — {contact.email}
                </option>
              ))}
            </select>
            <textarea
              className="min-h-28 w-full rounded-md border border-border p-3"
              placeholder="What would you like to ask?"
              value={newMessage}
              onChange={(event) => setNewMessage(event.target.value)}
            />
            <div className="flex justify-end gap-2">
              <SecondaryButton onClick={() => setShowNew(false)}>Cancel</SecondaryButton>
              <PrimaryButton
                disabled={
                  busy
                  || !newExpert
                  || (
                    !newMessage.trim()
                    && !threads.some(
                      (thread) =>
                        thread.owner_user_id === newExpert
                        || thread.created_by === newExpert,
                    )
                  )
                }
                onClick={() => void startConversation()}
              >
                {threads.some(
                  (thread) =>
                    thread.owner_user_id === newExpert
                    || thread.created_by === newExpert,
                ) && !newMessage.trim()
                  ? "Open chat"
                  : "Send message"}
              </PrimaryButton>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
