import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  AlertTriangle,
  Check,
  CheckSquare,
  FolderKanban,
  Loader2,
  RefreshCw,
  X,
} from "lucide-react";
import {
  finishStatusItem,
  getOpenStatus,
  type OpenStatus,
  type StatusActionItem,
  type StatusEvidence,
  type StatusIssue,
  type StatusItemKind,
  type StatusProject,
} from "@/services/status";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

type SelectedItem =
  | { kind: "project"; item: StatusProject }
  | { kind: "issue"; item: StatusIssue }
  | { kind: "action_item"; item: StatusActionItem };

function formatWhen(value?: string | null): string {
  if (!value) return "No recent signal";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "No recent signal";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatKnowledgeType(value?: string): string {
  if (!value) return "Source";
  return value
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function itemId(selected: SelectedItem): string {
  if (selected.kind === "project") return selected.item.entity_id;
  if (selected.kind === "issue") return selected.item.issue_id;
  return selected.item.action_item_id;
}

function itemTitle(selected: SelectedItem): string {
  if (selected.kind === "project") return selected.item.name;
  if (selected.kind === "issue") return selected.item.title;
  return selected.item.text;
}

function EmptyState({ label }: { label: string }) {
  return (
    <p className="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
      {label}
    </p>
  );
}

function EvidencePreview({ evidence }: { evidence: StatusEvidence[] }) {
  if (evidence.length === 0) return null;
  const first = evidence[0];
  return (
    <p className="mt-2 line-clamp-2 text-xs text-muted-foreground">
      <span className="font-medium text-foreground/80">
        {first.source_label || first.source || "Source"}
      </span>
      {first.summary ? ` — ${first.summary}` : null}
      {evidence.length > 1 ? ` · +${evidence.length - 1} more` : null}
    </p>
  );
}

function CurrentStatusBlock({
  status,
  when,
}: {
  status: string;
  when?: string | null;
}) {
  return (
    <div className="mt-3 rounded-md border border-border/70 bg-muted/40 px-3 py-2">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          Current status
        </p>
        <p className="text-[11px] text-muted-foreground">{formatWhen(when)}</p>
      </div>
      <p className="mt-1 line-clamp-3 text-sm leading-snug text-foreground">
        {status}
      </p>
    </div>
  );
}

function EvidenceDetail({
  evidence,
  emptyLabel = "No linked source excerpts yet.",
}: {
  evidence: StatusEvidence[];
  emptyLabel?: string;
}) {
  if (evidence.length === 0) {
    return <p className="text-sm text-muted-foreground">{emptyLabel}</p>;
  }
  return (
    <ul className="space-y-3">
      {evidence.map((item) => (
        <li
          key={item.chunk_id}
          className="rounded-md border border-border/70 bg-background/60 p-3"
        >
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="text-sm font-medium">
              {item.source_label || item.source || "Source"}
            </p>
            <p className="text-xs text-muted-foreground">
              {formatWhen(item.end_time)}
            </p>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {formatKnowledgeType(item.knowledge_type)}
            {item.source && item.source_label ? ` · ${item.source}` : null}
          </p>
          {item.summary ? (
            <p className="mt-2 text-sm text-foreground/90">{item.summary}</p>
          ) : null}
          {item.excerpt && item.excerpt !== item.summary ? (
            <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground">
              {item.excerpt}
              {item.excerpt.length >= 500 ? "…" : ""}
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function MarkFinishedButton({
  onClick,
  loading,
  compact = false,
}: {
  onClick: () => void;
  loading?: boolean;
  compact?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
      disabled={loading}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border border-border bg-background text-sm font-medium text-muted-foreground transition-colors hover:border-success/40 hover:bg-success/5 hover:text-success disabled:opacity-60",
        compact ? "px-2.5 py-1 text-xs" : "px-3 py-1.5",
      )}
    >
      {loading ? (
        <Loader2 className="size-3.5 animate-spin" />
      ) : (
        <Check className="size-3.5" />
      )}
      Mark finished
    </button>
  );
}

function StatusItemCard({
  title,
  subtitle,
  when,
  whenRaw,
  evidence,
  currentStatus,
  onOpen,
  onFinish,
  finishing,
}: {
  title: string;
  subtitle?: string;
  when: string;
  whenRaw?: string | null;
  evidence: StatusEvidence[];
  currentStatus?: string;
  onOpen: () => void;
  onFinish: () => void;
  finishing?: boolean;
}) {
  return (
    <Card
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
      className="cursor-pointer transition-colors hover:border-primary/40 hover:bg-secondary/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <CardHeader className="space-y-2 p-4 pb-2">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle className="text-base leading-snug">{title}</CardTitle>
            {subtitle ? (
              <p className="mt-1 text-xs text-muted-foreground">{subtitle}</p>
            ) : null}
          </div>
          <MarkFinishedButton
            compact
            loading={finishing}
            onClick={onFinish}
          />
        </div>
      </CardHeader>
      <CardContent className="p-4 pt-0">
        {currentStatus ? (
          <CurrentStatusBlock status={currentStatus} when={whenRaw} />
        ) : (
          <>
            <p className="text-xs text-muted-foreground">{when}</p>
            <EvidencePreview evidence={evidence} />
          </>
        )}
      </CardContent>
    </Card>
  );
}

function DetailPanel({
  selected,
  onClose,
  onFinish,
  finishing,
}: {
  selected: SelectedItem;
  onClose: () => void;
  onFinish: () => void;
  finishing?: boolean;
}) {
  const title = itemTitle(selected);
  let kindLabel = "Project";
  let whenLabel = "Last signal";
  let whenValue: string | null | undefined;
  let createdValue: string | null | undefined;
  let metaRows: Array<{ label: string; value: string }> = [];
  let evidence: StatusEvidence[] = [];

  if (selected.kind === "project") {
    kindLabel = "Open project";
    whenValue = selected.item.last_signal_at;
    evidence = selected.item.recent_updates?.length
      ? selected.item.recent_updates
      : selected.item.evidence;
    metaRows = [
      {
        label: "Current status",
        value:
          selected.item.current_status ||
          "No recent updates from connected sources yet.",
      },
    ];
  } else if (selected.kind === "issue") {
    kindLabel =
      selected.item.kind === "problem_report"
        ? "Problem report"
        : "Status update";
    whenLabel = "Last seen";
    whenValue = selected.item.last_seen_at;
    createdValue = selected.item.created_at;
    evidence = selected.item.evidence;
    metaRows = [
      { label: "Status", value: selected.item.status },
      ...(selected.item.project
        ? [{ label: "Project", value: selected.item.project }]
        : []),
    ];
  } else {
    kindLabel = "Action item";
    whenValue = selected.item.last_signal_at ?? selected.item.created_at;
    createdValue = selected.item.created_at;
    evidence = selected.item.evidence;
    metaRows = [
      { label: "Status", value: selected.item.status },
      {
        label: "Assignee",
        value: selected.item.assignee || "Unassigned",
      },
      ...(selected.item.project
        ? [{ label: "Project", value: selected.item.project }]
        : []),
    ];
  }

  return (
    <AnimatePresence>
      <motion.aside
        key={itemId(selected)}
        initial={{ x: 24, opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        exit={{ x: 24, opacity: 0 }}
        transition={{ duration: 0.2, ease: "easeOut" }}
        className="absolute inset-y-0 right-0 z-20 flex w-full max-w-md flex-col overflow-y-auto border-l border-border bg-card shadow-lg sm:static sm:z-auto sm:w-[22rem] sm:max-w-none sm:shrink-0 sm:shadow-none lg:w-[26rem]"
        aria-label={`Details: ${title}`}
      >
        <div className="flex items-center justify-between gap-3 border-b border-border p-4">
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {kindLabel}
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close details"
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="space-y-5 p-4">
          <div>
            <h3 className="text-lg font-semibold leading-snug tracking-tight">
              {title}
            </h3>
            <dl className="mt-3 space-y-2 text-sm">
              {metaRows.map((row) => (
                <div
                  key={row.label}
                  className={
                    row.label === "Current status"
                      ? "space-y-1"
                      : "flex justify-between gap-3"
                  }
                >
                  <dt className="text-muted-foreground">{row.label}</dt>
                  <dd
                    className={
                      row.label === "Current status"
                        ? "text-foreground"
                        : "text-right capitalize text-foreground"
                    }
                  >
                    {row.value}
                  </dd>
                </div>
              ))}
              {createdValue ? (
                <div className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">Created</dt>
                  <dd className="text-right text-foreground">
                    {formatWhen(createdValue)}
                  </dd>
                </div>
              ) : null}
              <div className="flex justify-between gap-3">
                <dt className="text-muted-foreground">{whenLabel}</dt>
                <dd className="text-right text-foreground">
                  {formatWhen(whenValue)}
                </dd>
              </div>
            </dl>
          </div>

          <MarkFinishedButton loading={finishing} onClick={onFinish} />

          <section className="space-y-2">
            <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {selected.kind === "project"
                ? `Recent updates (${evidence.length})`
                : `Evidence (${evidence.length})`}
            </h4>
            <EvidenceDetail
              evidence={evidence}
              emptyLabel={
                selected.kind === "project"
                  ? "No recent updates from connected sources yet."
                  : "No linked source excerpts yet."
              }
            />
          </section>
        </div>
      </motion.aside>
    </AnimatePresence>
  );
}

export default function StatusView() {
  const [data, setData] = useState<OpenStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<SelectedItem | null>(null);
  const [finishingId, setFinishingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await getOpenStatus());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load status.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const removeItem = useCallback((kind: StatusItemKind, id: string) => {
    setData((prev) => {
      if (!prev) return prev;
      if (kind === "project") {
        return {
          ...prev,
          projects: prev.projects.filter((item) => item.entity_id !== id),
        };
      }
      if (kind === "issue") {
        return {
          ...prev,
          issues: prev.issues.filter((item) => item.issue_id !== id),
        };
      }
      return {
        ...prev,
        action_items: prev.action_items.filter(
          (item) => item.action_item_id !== id,
        ),
      };
    });
    setSelected((prev) =>
      prev && prev.kind === kind && itemId(prev) === id ? null : prev,
    );
  }, []);

  const handleFinish = useCallback(
    async (kind: StatusItemKind, id: string) => {
      setFinishingId(id);
      setError(null);
      try {
        await finishStatusItem(kind, id);
        removeItem(kind, id);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Could not mark item finished.",
        );
      } finally {
        setFinishingId(null);
      }
    },
    [removeItem],
  );

  if (loading && !data) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Loading open work…
      </div>
    );
  }

  const projects = data?.projects ?? [];
  const issues = data?.issues ?? [];
  const actions = data?.action_items ?? [];
  const total = projects.length + issues.length + actions.length;

  return (
    <div className="relative flex min-h-0 flex-1 gap-0">
      <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-8 overflow-y-auto px-1 pb-8">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight">Status</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Open projects show the latest update from connected sources. Click
              a project for recent updates, or mark finished to clear it.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void load()}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
          >
            <RefreshCw className={cn("size-3.5", loading && "animate-spin")} />
            Refresh
          </button>
        </div>

        {error && (
          <p className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            {error}
          </p>
        )}

        {!error && total === 0 && (
          <EmptyState label="Nothing open yet. Sync Gmail or ingest documents that mention active projects, problems, or todos." />
        )}

        <section className="space-y-3">
          <header className="flex items-center gap-2">
            <FolderKanban className="size-4 text-primary" />
            <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              Open projects
            </h3>
            <span className="text-xs text-muted-foreground">
              {projects.length}
            </span>
          </header>
          {projects.length === 0 ? (
            <EmptyState label="No open projects detected." />
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              {projects.map((project) => (
                <StatusItemCard
                  key={project.entity_id}
                  title={project.name}
                  when={formatWhen(project.last_signal_at)}
                  whenRaw={project.last_signal_at}
                  currentStatus={
                    project.current_status ||
                    "No recent updates from connected sources yet."
                  }
                  evidence={project.recent_updates ?? project.evidence}
                  finishing={finishingId === project.entity_id}
                  onOpen={() => setSelected({ kind: "project", item: project })}
                  onFinish={() =>
                    void handleFinish("project", project.entity_id)
                  }
                />
              ))}
            </div>
          )}
        </section>

        <section className="space-y-3">
          <header className="flex items-center gap-2">
            <AlertTriangle className="size-4 text-primary" />
            <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              Open reports
            </h3>
            <span className="text-xs text-muted-foreground">{issues.length}</span>
          </header>
          {issues.length === 0 ? (
            <EmptyState label="No open problem reports or status updates." />
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              {issues.map((issue) => (
                <StatusItemCard
                  key={issue.issue_id}
                  title={issue.title}
                  subtitle={
                    issue.kind === "problem_report"
                      ? `Problem report${issue.project ? ` · ${issue.project}` : ""}`
                      : `Status update${issue.project ? ` · ${issue.project}` : ""}`
                  }
                  when={formatWhen(issue.last_seen_at)}
                  evidence={issue.evidence}
                  finishing={finishingId === issue.issue_id}
                  onOpen={() => setSelected({ kind: "issue", item: issue })}
                  onFinish={() => void handleFinish("issue", issue.issue_id)}
                />
              ))}
            </div>
          )}
        </section>

        <section className="space-y-3">
          <header className="flex items-center gap-2">
            <CheckSquare className="size-4 text-primary" />
            <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              Open action items
            </h3>
            <span className="text-xs text-muted-foreground">
              {actions.length}
            </span>
          </header>
          {actions.length === 0 ? (
            <EmptyState label="No open action items." />
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              {actions.map((item) => (
                <StatusItemCard
                  key={item.action_item_id}
                  title={item.text}
                  subtitle={
                    [item.assignee, item.project].filter(Boolean).join(" · ") ||
                    "Unassigned"
                  }
                  when={formatWhen(item.last_signal_at ?? item.created_at)}
                  evidence={item.evidence}
                  finishing={finishingId === item.action_item_id}
                  onOpen={() =>
                    setSelected({ kind: "action_item", item })
                  }
                  onFinish={() =>
                    void handleFinish("action_item", item.action_item_id)
                  }
                />
              ))}
            </div>
          )}
        </section>
      </div>

      {selected ? (
        <DetailPanel
          selected={selected}
          finishing={finishingId === itemId(selected)}
          onClose={() => setSelected(null)}
          onFinish={() => void handleFinish(selected.kind, itemId(selected))}
        />
      ) : null}
    </div>
  );
}
