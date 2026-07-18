import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckSquare,
  FolderKanban,
  Loader2,
  RefreshCw,
} from "lucide-react";
import {
  getOpenStatus,
  type OpenStatus,
  type StatusEvidence,
} from "@/services/status";
import { cn } from "@/lib/utils";

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

function EvidenceList({ evidence }: { evidence: StatusEvidence[] }) {
  if (evidence.length === 0) return null;
  return (
    <ul className="mt-2 space-y-1 border-t border-border/60 pt-2">
      {evidence.map((item) => (
        <li key={item.chunk_id} className="text-xs text-muted-foreground">
          <span className="font-medium text-foreground/80">
            {item.source_label || item.source || "Source"}
          </span>
          {item.summary ? ` — ${item.summary}` : null}
        </li>
      ))}
    </ul>
  );
}

function EmptyState({ label }: { label: string }) {
  return (
    <p className="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
      {label}
    </p>
  );
}

export default function StatusView() {
  const [data, setData] = useState<OpenStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Status</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Open projects, reports, and action items inferred from ingested email
            and documents.
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
          <span className="text-xs text-muted-foreground">{projects.length}</span>
        </header>
        {projects.length === 0 ? (
          <EmptyState label="No open projects detected." />
        ) : (
          <ul className="divide-y divide-border border-y border-border">
            {projects.map((project) => (
              <li key={project.entity_id} className="py-3">
                <div className="flex items-baseline justify-between gap-3">
                  <p className="font-medium">{project.name}</p>
                  <p className="shrink-0 text-xs text-muted-foreground">
                    {formatWhen(project.last_signal_at)}
                  </p>
                </div>
                <EvidenceList evidence={project.evidence} />
              </li>
            ))}
          </ul>
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
          <ul className="divide-y divide-border border-y border-border">
            {issues.map((issue) => (
              <li key={issue.issue_id} className="py-3">
                <div className="flex items-baseline justify-between gap-3">
                  <div>
                    <p className="font-medium">{issue.title}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {issue.kind === "problem_report"
                        ? "Problem report"
                        : "Status update"}
                      {issue.project ? ` · ${issue.project}` : ""}
                    </p>
                  </div>
                  <p className="shrink-0 text-xs text-muted-foreground">
                    {formatWhen(issue.last_seen_at)}
                  </p>
                </div>
                <EvidenceList evidence={issue.evidence} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-3">
        <header className="flex items-center gap-2">
          <CheckSquare className="size-4 text-primary" />
          <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            Open action items
          </h3>
          <span className="text-xs text-muted-foreground">{actions.length}</span>
        </header>
        {actions.length === 0 ? (
          <EmptyState label="No open action items." />
        ) : (
          <ul className="divide-y divide-border border-y border-border">
            {actions.map((item) => (
              <li key={item.action_item_id} className="py-3">
                <div className="flex items-baseline justify-between gap-3">
                  <div>
                    <p className="font-medium">{item.text}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {[item.assignee, item.project].filter(Boolean).join(" · ") ||
                        "Unassigned"}
                    </p>
                  </div>
                  <p className="shrink-0 text-xs text-muted-foreground">
                    {formatWhen(item.last_signal_at ?? item.created_at)}
                  </p>
                </div>
                <EvidenceList evidence={item.evidence} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
