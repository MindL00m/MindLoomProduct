import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  ArrowUp,
  FileText,
  Loader2,
  MessageSquarePlus,
  MessageSquareText,
  Sparkles,
  Trash2,
  UserRound,
} from "lucide-react";
import {
  askQuestion,
  citationText,
  type ChatMessage,
  type QueryResponse,
  type Source,
} from "@/services/ask";
import { useChat, type Conversation, type Turn } from "@/store/chat";
import { cn } from "@/lib/utils";

const EXAMPLES = [
  "What did we decide about the pricing model?",
  "Who owns the data pipeline?",
  "Summarize the latest status on the migration.",
];

const SOURCE_RE = /\[SOURCE:\s*([^\]]+?)\]/gi;

/** Render an answer, converting [SOURCE: chunk_id] markers into numbered refs. */
function renderAnswer(answer: string, sources: Source[]): ReactNode[] {
  const index = new Map(sources.map((s, i) => [s.chunk_id, i + 1]));
  const nodes: ReactNode[] = [];
  let last = 0;
  let key = 0;
  let match: RegExpExecArray | null;
  SOURCE_RE.lastIndex = 0;
  while ((match = SOURCE_RE.exec(answer)) !== null) {
    if (match.index > last) nodes.push(answer.slice(last, match.index));
    const n = index.get(match[1].trim());
    if (n) {
      nodes.push(
        <sup
          key={`ref-${key++}`}
          className="mx-0.5 inline-flex size-4 items-center justify-center rounded bg-brand-100 text-[10px] font-semibold text-brand-700 align-super"
        >
          {n}
        </sup>,
      );
    }
    last = SOURCE_RE.lastIndex;
  }
  if (last < answer.length) nodes.push(answer.slice(last));
  return nodes;
}

/** Flatten completed turns into the chat history sent to the backend. */
function historyFrom(conversation: Conversation | undefined): ChatMessage[] {
  if (!conversation) return [];
  const messages: ChatMessage[] = [];
  for (const turn of conversation.turns) {
    if (turn.status !== "done" || !turn.response) continue;
    messages.push({ role: "user", content: turn.question });
    messages.push({ role: "assistant", content: turn.response.answer });
  }
  return messages;
}

function timeAgo(ts: number): string {
  const diff = Date.now() - ts;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return new Date(ts).toLocaleDateString();
}

export default function AskView() {
  const conversations = useChat((s) => s.conversations);
  const activeId = useChat((s) => s.activeId);
  const ensureActive = useChat((s) => s.ensureActive);
  const setActive = useChat((s) => s.setActive);
  const newConversation = useChat((s) => s.newConversation);
  const deleteConversation = useChat((s) => s.deleteConversation);
  const addTurn = useChat((s) => s.addTurn);
  const updateTurn = useChat((s) => s.updateTurn);

  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    ensureActive();
  }, [ensureActive]);

  const active = conversations.find((c) => c.id === activeId);
  const turns = active?.turns ?? [];

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns.length, activeId]);

  async function submit(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    const convId = ensureActive();
    const history = historyFrom(useChat.getState().conversations.find((c) => c.id === convId));

    const turnId = crypto.randomUUID();
    setInput("");
    setBusy(true);
    addTurn(convId, { id: turnId, question: q, status: "pending" });
    try {
      const response = await askQuestion(q, history);
      updateTurn(convId, turnId, { status: "done", response });
    } catch (err) {
      updateTurn(convId, turnId, {
        status: "error",
        error: err instanceof Error ? err.message : "Something went wrong.",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full w-full gap-4">
      <ConversationList
        conversations={conversations}
        activeId={activeId}
        onSelect={setActive}
        onNew={() => newConversation()}
        onDelete={deleteConversation}
      />

      <section className="flex min-w-0 flex-1 flex-col">
        <MobileBar
          title={active?.title ?? "New conversation"}
          onNew={() => newConversation()}
        />

        <div className="min-h-0 flex-1 overflow-y-auto">
          {turns.length === 0 ? (
            <EmptyState onExample={submit} />
          ) : (
            <div className="mx-auto max-w-3xl space-y-6 py-4">
              {turns.map((turn) => (
                <TurnView key={turn.id} turn={turn} />
              ))}
              <div ref={bottomRef} />
            </div>
          )}
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submit(input);
          }}
          className="mx-auto w-full max-w-3xl pt-2"
        >
          <div className="flex items-end gap-2 rounded-xl border border-border bg-card p-2 shadow-sm focus-within:border-primary">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void submit(input);
                }
              }}
              rows={1}
              placeholder="Ask a question…"
              className="max-h-40 min-h-[2.25rem] flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-muted-foreground"
            />
            <button
              type="submit"
              disabled={busy || !input.trim()}
              className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
              aria-label="Send"
            >
              {busy ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <ArrowUp className="size-4" />
              )}
            </button>
          </div>
          <p className="mt-1.5 px-1 text-center text-xs text-muted-foreground">
            Answers are grounded in your knowledge graph and cite their sources.
          </p>
        </form>
      </section>
    </div>
  );
}

function ConversationList({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
}: {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}) {
  return (
    <aside className="hidden w-64 shrink-0 flex-col rounded-lg border border-border bg-card md:flex">
      <div className="p-2">
        <button
          type="button"
          onClick={onNew}
          className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
        >
          <MessageSquarePlus className="size-4" /> New chat
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {conversations.length === 0 ? (
          <p className="px-2 py-4 text-center text-xs text-muted-foreground">
            No conversations yet.
          </p>
        ) : (
          <ul className="space-y-1">
            {conversations.map((c) => (
              <li key={c.id}>
                <div
                  className={cn(
                    "group flex items-center gap-2 rounded-md px-2 py-2 text-sm transition-colors",
                    c.id === activeId
                      ? "bg-secondary text-foreground"
                      : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                  )}
                >
                  <button
                    type="button"
                    onClick={() => onSelect(c.id)}
                    className="min-w-0 flex-1 text-left"
                  >
                    <p className="truncate font-medium">{c.title}</p>
                    <p className="truncate text-xs text-muted-foreground">
                      {timeAgo(c.updatedAt)}
                    </p>
                  </button>
                  <button
                    type="button"
                    onClick={() => onDelete(c.id)}
                    className="shrink-0 rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-destructive/10 hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100"
                    aria-label="Delete conversation"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}

function MobileBar({ title, onNew }: { title: string; onNew: () => void }) {
  return (
    <div className="mb-2 flex items-center justify-between gap-2 md:hidden">
      <p className="min-w-0 flex-1 truncate text-sm font-medium">{title}</p>
      <button
        type="button"
        onClick={onNew}
        className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted/60"
      >
        <MessageSquarePlus className="size-3.5" /> New
      </button>
    </div>
  );
}

function EmptyState({ onExample }: { onExample: (q: string) => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center text-center">
      <span className="flex size-12 items-center justify-center rounded-full bg-brand-50 text-brand-700">
        <Sparkles className="size-6" />
      </span>
      <h2 className="mt-4 text-2xl font-semibold tracking-tight">
        Ask your Company Brain
      </h2>
      <p className="mt-1 max-w-md text-muted-foreground">
        Questions are answered strictly from your ingested knowledge, with a
        citation for every source. Memory persists within a conversation.
      </p>
      <div className="mt-6 flex flex-col gap-2">
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            type="button"
            onClick={() => onExample(ex)}
            className="rounded-full border border-border bg-card px-4 py-2 text-sm text-foreground transition-colors hover:border-mist-400 hover:bg-muted/50"
          >
            {ex}
          </button>
        ))}
      </div>
    </div>
  );
}

function TurnView({ turn }: { turn: Turn }) {
  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <div className="flex max-w-[85%] items-start gap-2">
          <div className="rounded-2xl rounded-tr-sm bg-primary px-4 py-2 text-sm text-primary-foreground">
            {turn.question}
          </div>
          <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-secondary text-mist-700">
            <UserRound className="size-4" />
          </span>
        </div>
      </div>

      <div className="flex items-start gap-2">
        <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-brand-50 text-brand-700">
          <Sparkles className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          {turn.status === "pending" && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> Searching your knowledge…
            </div>
          )}
          {turn.status === "error" && (
            <div className="rounded-md border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {turn.error}
            </div>
          )}
          {turn.status === "done" && turn.response && (
            <AnswerView response={turn.response} />
          )}
        </div>
      </div>
    </div>
  );
}

function AnswerView({ response }: { response: QueryResponse }) {
  const cited = new Set(
    (response.answer.match(SOURCE_RE) ?? [])
      .map((m) => m.replace(SOURCE_RE, "$1").trim())
      .filter(Boolean),
  );
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <ConfidencePill confidence={response.confidence} />
      </div>

      <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
        {renderAnswer(response.answer, response.sources)}
      </p>

      {response.routed && response.expert && (
        <div className="rounded-md border border-border bg-muted/50 px-3 py-2 text-sm">
          <span className="font-medium">Suggested expert: {response.expert.name}</span>
          <span className="text-muted-foreground"> — {response.expert.reason}</span>
        </div>
      )}

      {response.sources.length > 0 && (
        <div className="rounded-lg border border-border bg-card">
          <p className="border-b border-border px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Sources
          </p>
          <ol className="divide-y divide-border">
            {response.sources.map((source, i) => (
              <SourceRow
                key={source.chunk_id}
                source={source}
                index={i + 1}
                cited={cited.has(source.chunk_id)}
              />
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}

function SourceRow({
  source,
  index,
  cited,
}: {
  source: Source;
  index: number;
  cited: boolean;
}) {
  return (
    <li className="flex gap-3 px-3 py-2.5">
      <span
        className={cn(
          "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded text-[11px] font-semibold",
          cited ? "bg-brand-100 text-brand-700" : "bg-muted text-muted-foreground",
        )}
      >
        {index}
      </span>
      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-1.5 text-sm font-medium">
          <FileText className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate">{citationText(source)}</span>
        </p>
        <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
          {source.summary || source.raw_text}
        </p>
        <p className="mt-1 text-[11px] text-muted-foreground/80">
          {source.knowledge_type.replace(/_/g, " ")} ·{" "}
          {Math.round(source.similarity_score * 100)}% match
        </p>
      </div>
    </li>
  );
}

function ConfidencePill({
  confidence,
}: {
  confidence: "high" | "medium" | "low";
}) {
  const styles: Record<"high" | "medium" | "low", string> = {
    high: "bg-success/10 text-success ring-success/20",
    medium: "bg-amber-100 text-amber-800 ring-amber-200",
    low: "bg-muted text-muted-foreground ring-border",
  };
  const label = {
    high: "High confidence",
    medium: "Medium confidence",
    low: "Low confidence",
  }[confidence];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset",
        styles[confidence],
      )}
    >
      <MessageSquareText className="size-3.5" />
      {label}
    </span>
  );
}
