import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  ArrowUp,
  Database,
  FileText,
  Loader2,
  MessageSquare,
  MessageSquarePlus,
  MessageSquareText,
  Paperclip,
  Sparkles,
  Trash2,
  UserRound,
  X,
} from "lucide-react";
import {
  askQuestion,
  citationText,
  extractFileForChat,
  type ChatMessage,
  type EphemeralDocument,
  type QueryResponse,
  type Source,
} from "@/services/ask";
import { ingestFileToGraph, isJson, isPdf } from "@/services/ingest";
import { useChat, type ChatAttachment, type Conversation, type Turn } from "@/store/chat";
import { cn } from "@/lib/utils";

const FILE_ACCEPT = ".pdf,.json,.txt,application/pdf,application/json,text/plain";

const EXAMPLES = [
  "What did we decide about the pricing model?",
  "Who owns the data pipeline?",
  "Summarize the latest status on the migration.",
];

const CITE_RE = /\[(?:SOURCE|EPHEMERAL):\s*([^\]]+?)\]/gi;

/** Render an answer, converting citation markers into numbered refs. */
function renderAnswer(answer: string, sources: Source[]): ReactNode[] {
  const index = new Map(sources.map((s, i) => [s.chunk_id, i + 1]));
  const nodes: ReactNode[] = [];
  let last = 0;
  let key = 0;
  let match: RegExpExecArray | null;
  CITE_RE.lastIndex = 0;
  while ((match = CITE_RE.exec(answer)) !== null) {
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
    last = CITE_RE.lastIndex;
  }
  if (last < answer.length) nodes.push(answer.slice(last));
  return nodes;
}

function ephemeralFrom(conversation: Conversation | undefined): EphemeralDocument[] {
  if (!conversation?.attachments) return [];
  return conversation.attachments
    .filter((a) => a.scope === "chat" && a.status === "ready" && a.text)
    .map((a) => ({
      document_id: a.id,
      filename: a.filename,
      text: a.text!,
    }));
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
  const addAttachment = useChat((s) => s.addAttachment);
  const updateAttachment = useChat((s) => s.updateAttachment);
  const removeAttachment = useChat((s) => s.removeAttachment);

  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    ensureActive();
  }, [ensureActive]);

  const active = conversations.find((c) => c.id === activeId);
  const turns = active?.turns ?? [];
  const attachments = active?.attachments ?? [];
  const hasChatFiles = attachments.some((a) => a.scope === "chat" && a.status === "ready");

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns.length, activeId]);

  async function submit(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    const convId = ensureActive();
    const conv = useChat.getState().conversations.find((c) => c.id === convId);
    const history = historyFrom(conv);
    const ephemeral = ephemeralFrom(conv);

    const turnId = crypto.randomUUID();
    setInput("");
    setBusy(true);
    addTurn(convId, { id: turnId, question: q, status: "pending" });
    try {
      const response = await askQuestion(q, history, ephemeral);
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

  function handleFilePick(files: FileList | null) {
    const file = files?.[0];
    if (!file) return;
    if (!isPdf(file) && !isJson(file) && !file.name.toLowerCase().endsWith(".txt")) {
      alert("Supported files: PDF, JSON conversation exports, or plain text.");
      return;
    }
    setPendingFile(file);
  }

  async function saveToKnowledgeGraph(file: File) {
    const convId = ensureActive();
    const attId = crypto.randomUUID();
    setPendingFile(null);
    addAttachment(convId, {
      id: attId,
      filename: file.name,
      scope: "graph",
      status: "processing",
    });
    try {
      const result = await ingestFileToGraph(file, (status) => {
        updateAttachment(convId, attId, {
          status: status.status === "failed" ? "error" : "processing",
          error: status.error ?? undefined,
        });
      });
      if (result.status === "failed") {
        updateAttachment(convId, attId, {
          status: "error",
          error: result.error ?? "Ingestion failed.",
        });
      } else {
        updateAttachment(convId, attId, {
          status: "ready",
          ingestedChunks: result.result?.total_chunks ?? 0,
        });
      }
    } catch (err) {
      updateAttachment(convId, attId, {
        status: "error",
        error: err instanceof Error ? err.message : "Upload failed.",
      });
    }
  }

  async function useInChatOnly(file: File) {
    const convId = ensureActive();
    const attId = crypto.randomUUID();
    setPendingFile(null);
    addAttachment(convId, {
      id: attId,
      filename: file.name,
      scope: "chat",
      status: "processing",
    });
    try {
      const extracted = await extractFileForChat(file);
      updateAttachment(convId, attId, {
        id: extracted.document_id,
        status: "ready",
        text: extracted.text,
      });
    } catch (err) {
      updateAttachment(convId, attId, {
        status: "error",
        error: err instanceof Error ? err.message : "Could not read file.",
      });
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
          {attachments.length > 0 && activeId && (
            <AttachmentBar
              attachments={attachments}
              onRemove={(id) => removeAttachment(activeId, id)}
            />
          )}

          <input
            ref={fileInputRef}
            type="file"
            accept={FILE_ACCEPT}
            className="sr-only"
            onChange={(e) => {
              handleFilePick(e.target.files);
              e.target.value = "";
            }}
          />

          <div className="flex items-end gap-2 rounded-xl border border-border bg-card p-2 shadow-sm focus-within:border-primary">
            <button
              type="button"
              disabled={busy}
              onClick={() => fileInputRef.current?.click()}
              className="flex size-9 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
              aria-label="Upload file"
            >
              <Paperclip className="size-4" />
            </button>
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
            {hasChatFiles
              ? "This chat includes attached files visible only here."
              : "Answers cite the knowledge graph and any chat-only attachments."}
          </p>
        </form>

        {pendingFile && (
          <UploadChoiceModal
            file={pendingFile}
            onClose={() => setPendingFile(null)}
            onGraph={() => void saveToKnowledgeGraph(pendingFile)}
            onChat={() => void useInChatOnly(pendingFile)}
            graphSupported={isPdf(pendingFile) || isJson(pendingFile)}
          />
        )}
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
  const cited = new Set<string>();
  let match: RegExpExecArray | null;
  CITE_RE.lastIndex = 0;
  while ((match = CITE_RE.exec(response.answer)) !== null) {
    cited.add(match[1].trim());
  }
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

function AttachmentBar({
  attachments,
  onRemove,
}: {
  attachments: ChatAttachment[];
  onRemove: (id: string) => void;
}) {
  return (
    <div className="mb-2 flex flex-wrap gap-2">
      {attachments.map((a) => (
        <span
          key={a.id}
          className={cn(
            "inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs",
            a.status === "error"
              ? "border-destructive/30 bg-destructive/10 text-destructive"
              : a.scope === "graph"
                ? "border-brand-200 bg-brand-50 text-brand-800"
                : "border-border bg-muted text-foreground",
          )}
        >
          {a.scope === "graph" ? (
            <Database className="size-3 shrink-0" />
          ) : (
            <MessageSquare className="size-3 shrink-0" />
          )}
          <span className="truncate">{a.filename}</span>
          {a.status === "processing" && (
            <Loader2 className="size-3 shrink-0 animate-spin" />
          )}
          {a.status === "ready" && a.scope === "graph" && a.ingestedChunks != null && (
            <span className="text-muted-foreground">· {a.ingestedChunks} chunks</span>
          )}
          {a.status === "ready" && a.scope === "chat" && (
            <span className="text-muted-foreground">· this chat</span>
          )}
          {a.status === "error" && (
            <span className="truncate text-destructive" title={a.error}>
              · failed
            </span>
          )}
          <button
            type="button"
            onClick={() => onRemove(a.id)}
            className="rounded p-0.5 hover:bg-black/5"
            aria-label={`Remove ${a.filename}`}
          >
            <X className="size-3" />
          </button>
        </span>
      ))}
    </div>
  );
}

function UploadChoiceModal({
  file,
  onClose,
  onGraph,
  onChat,
  graphSupported,
}: {
  file: File;
  onClose: () => void;
  onGraph: () => void;
  onChat: () => void;
  graphSupported: boolean;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg border border-border bg-card p-5 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold">Add {file.name}</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          Should this file be saved to your organization&apos;s knowledge graph, or kept
          private to this chat?
        </p>
        <div className="mt-5 flex flex-col gap-2">
          <button
            type="button"
            disabled={!graphSupported}
            onClick={onGraph}
            className="flex items-start gap-3 rounded-md border border-border p-3 text-left transition-colors hover:border-primary hover:bg-brand-50/50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Database className="mt-0.5 size-5 shrink-0 text-brand-700" />
            <span>
              <span className="block text-sm font-medium">Save to knowledge graph</span>
              <span className="mt-0.5 block text-xs text-muted-foreground">
                {graphSupported
                  ? "Chunk and index the file so everyone in your org can query it."
                  : "Only PDF and JSON conversation exports can be saved to the graph."}
              </span>
            </span>
          </button>
          <button
            type="button"
            onClick={onChat}
            className="flex items-start gap-3 rounded-md border border-border p-3 text-left transition-colors hover:border-primary hover:bg-muted/50"
          >
            <MessageSquare className="mt-0.5 size-5 shrink-0 text-mist-700" />
            <span>
              <span className="block text-sm font-medium">Use in this chat only</span>
              <span className="mt-0.5 block text-xs text-muted-foreground">
                The file stays in this conversation and is not added to the graph.
              </span>
            </span>
          </button>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="mt-4 w-full text-center text-sm text-muted-foreground hover:text-foreground"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
