import { useCallback, useEffect, useRef, useState } from "react";
import { safeHref } from "@/lib/safeHref";
import { useSearchParams } from "react-router-dom";
import {
  Activity,
  ExternalLink,
  History,
  Loader2,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import {
  createTracker,
  deleteTracker,
  getTracker,
  listTrackers,
  setTrackerArchived,
  STATUS_LABELS,
  type StatusTracker,
  type StatusTrackerSummary,
  type StatusTrackerUpdate,
  type TrackerStatus,
} from "@/services/status";
import { cn } from "@/lib/utils";

const REFRESH_MS = 60_000;

const STATUS_STYLES: Record<TrackerStatus, string> = {
  on_track: "border-emerald-200 bg-emerald-50 text-emerald-800",
  at_risk: "border-amber-200 bg-amber-50 text-amber-900",
  blocked: "border-red-200 bg-red-50 text-red-800",
  done: "border-sky-200 bg-sky-50 text-sky-800",
  unknown: "border-border bg-muted text-muted-foreground",
};

export function StatusPill({ status, className }: { status: TrackerStatus; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-[11px] font-medium",
        STATUS_STYLES[status] ?? STATUS_STYLES.unknown,
        className,
      )}
    >
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

function formatDate(value?: string | null, withTime = false): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: date.getFullYear() === new Date().getFullYear() ? undefined : "numeric",
    ...(withTime ? { hour: "numeric", minute: "2-digit" } : {}),
  });
}

function timeAgo(value?: string | null): string {
  if (!value) return "no updates yet";
  const diff = Date.now() - new Date(value).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return formatDate(value);
}

export default function StatusView() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedId = searchParams.get("tracker");
  const [trackers, setTrackers] = useState<StatusTrackerSummary[]>([]);
  const [selected, setSelected] = useState<StatusTracker | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [subject, setSubject] = useState("");
  const [creating, setCreating] = useState(false);
  const sectionRef = useRef<HTMLDivElement>(null);

  // Links from Ask land on Home with ?tracker=…; bring the section into view.
  useEffect(() => {
    if (requestedId) sectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refresh = useCallback(async () => {
    try {
      const rows = await listTrackers();
      setTrackers(rows);
      setError("");
      return rows;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load status trackers.");
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  const open = useCallback(async (trackerId: string) => {
    try {
      setSelected(await getTracker(trackerId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load tracker.");
    }
  }, []);

  useEffect(() => {
    void refresh().then((rows) => {
      const first = requestedId ?? rows?.find((t) => !t.archived)?.tracker_id ?? rows?.[0]?.tracker_id;
      if (first) void open(first);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestedId]);

  // Trackers update in the background as knowledge arrives; keep the view fresh.
  useEffect(() => {
    const timer = window.setInterval(() => {
      void refresh();
      if (selected) void open(selected.tracker_id);
    }, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [refresh, open, selected]);

  function select(trackerId: string) {
    const next = new URLSearchParams(searchParams);
    next.set("tracker", trackerId);
    setSearchParams(next, { replace: true });
  }

  async function start() {
    const value = subject.trim();
    if (value.length < 2 || creating) return;
    setCreating(true);
    setError("");
    try {
      const tracker = await createTracker(value);
      setSubject("");
      setSelected(tracker);
      await refresh();
      const next = new URLSearchParams(searchParams);
      next.set("tracker", tracker.tracker_id);
      setSearchParams(next, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start tracking.");
    } finally {
      setCreating(false);
    }
  }

  async function onChanged(tracker: StatusTracker | null) {
    setSelected(tracker);
    const rows = await refresh();
    if (!tracker && rows?.length) void open(rows[0].tracker_id);
  }

  const active = trackers.filter((t) => !t.archived);
  const paused = trackers.filter((t) => t.archived);

  return (
    <div ref={sectionRef} id="status-tracking" className="scroll-mt-20">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
            <Activity className="size-5 text-primary" /> Status tracking
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Ask for a status report in Ask, or start one here. Each tracker updates itself
            whenever new information about it arrives, and keeps the full history.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted/60"
        >
          <RefreshCw className="size-3.5" /> Refresh
        </button>
      </div>

      <form
        className="mt-4 flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void start();
        }}
      >
        <input
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          maxLength={500}
          placeholder="What should MindLoom track? e.g. the website launch, the Acme renewal"
          aria-label="What to track"
          className="min-w-0 flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm"
        />
        <button
          type="submit"
          disabled={creating || subject.trim().length < 2}
          className="flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          {creating ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
          {creating ? "Building report…" : "Start tracking"}
        </button>
      </form>

      {error && (
        <p className="mt-3 rounded-md border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </p>
      )}

      {loading ? (
        <p className="mt-6 flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading trackers…
        </p>
      ) : trackers.length === 0 ? (
        <p className="mt-6 rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
          Nothing is being tracked yet. Ask “give me a status report on the website launch” in
          Ask, or type a subject above.
        </p>
      ) : (
        <div className="mt-5 grid gap-4 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
          <div className="space-y-4">
            <TrackerList trackers={active} selectedId={selected?.tracker_id} onSelect={select} />
            {paused.length > 0 && (
              <div>
                <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Paused
                </p>
                <TrackerList trackers={paused} selectedId={selected?.tracker_id} onSelect={select} />
              </div>
            )}
          </div>
          {selected ? (
            <TrackerDetail key={selected.tracker_id} tracker={selected} onChanged={onChanged} />
          ) : (
            <div className="rounded-lg border border-dashed border-border p-6 text-sm text-muted-foreground">
              Choose a tracker to see its history.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TrackerList({
  trackers,
  selectedId,
  onSelect,
}: {
  trackers: StatusTrackerSummary[];
  selectedId?: string;
  onSelect: (id: string) => void;
}) {
  if (trackers.length === 0) {
    return <p className="text-sm text-muted-foreground">No active trackers.</p>;
  }
  return (
    <ul className="space-y-2">
      {trackers.map((t) => (
        <li key={t.tracker_id}>
          <button
            type="button"
            onClick={() => onSelect(t.tracker_id)}
            aria-current={t.tracker_id === selectedId ? "true" : undefined}
            className={cn(
              "w-full rounded-lg border bg-card px-3 py-2.5 text-left transition-colors",
              t.tracker_id === selectedId
                ? "border-primary shadow-sm"
                : "border-border hover:border-mist-400",
              t.archived && "opacity-70",
            )}
          >
            <span className="flex items-start justify-between gap-2">
              <span className="min-w-0 truncate text-sm font-medium">{t.title}</span>
              <StatusPill status={t.status} />
            </span>
            <span className="mt-1 line-clamp-2 block text-xs text-muted-foreground">{t.summary}</span>
            <span className="mt-1.5 block text-[11px] text-muted-foreground">
              {t.update_count === 1 ? "1 update" : `${t.update_count} updates`} · last{" "}
              {timeAgo(t.last_update_at)}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

const KIND_LABELS: Record<StatusTrackerUpdate["kind"], string> = {
  update: "New update",
  history: "From earlier knowledge",
  created: "Tracking started",
};

function TrackerDetail({
  tracker,
  onChanged,
}: {
  tracker: StatusTracker;
  onChanged: (tracker: StatusTracker | null) => void;
}) {
  const [busy, setBusy] = useState<"" | "pause" | "delete">("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState("");

  async function toggle() {
    setBusy("pause");
    setError("");
    try {
      onChanged(await setTrackerArchived(tracker.tracker_id, !tracker.archived));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update tracker.");
    } finally {
      setBusy("");
    }
  }

  async function remove() {
    setBusy("delete");
    setError("");
    try {
      await deleteTracker(tracker.tracker_id);
      onChanged(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not stop tracking.");
      setBusy("");
    }
  }

  return (
    <section className="rounded-lg border border-border bg-card">
      <header className="border-b border-border px-4 py-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 className="font-semibold">{tracker.title}</h3>
            {tracker.topic !== tracker.title && (
              <p className="text-xs text-muted-foreground">Tracking: {tracker.topic}</p>
            )}
          </div>
          <StatusPill status={tracker.status} className="text-xs" />
        </div>
        <p className="mt-2 text-sm leading-relaxed">{tracker.summary}</p>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
          <span className="text-muted-foreground">
            {tracker.archived
              ? "Paused: new information is not being checked."
              : "Updates automatically as new information arrives."}
          </span>
          <span className="ml-auto flex gap-1.5">
            <button
              type="button"
              disabled={busy !== ""}
              onClick={() => void toggle()}
              className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 font-medium hover:bg-muted/60 disabled:opacity-50"
            >
              {tracker.archived ? <Play className="size-3.5" /> : <Pause className="size-3.5" />}
              {tracker.archived ? "Resume" : "Pause"}
            </button>
            {confirmDelete ? (
              <>
                <button
                  type="button"
                  disabled={busy !== ""}
                  onClick={() => void remove()}
                  className="rounded-md bg-destructive px-2.5 py-1.5 font-medium text-destructive-foreground disabled:opacity-50"
                >
                  {busy === "delete" ? "Stopping…" : "Stop and delete history"}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmDelete(false)}
                  className="rounded-md border border-border px-2.5 py-1.5 font-medium"
                >
                  Keep
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={() => setConfirmDelete(true)}
                aria-label="Stop tracking"
                className="rounded-md border border-border p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
              >
                <Trash2 className="size-3.5" />
              </button>
            )}
          </span>
        </div>
        {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
      </header>

      <div className="px-4 py-3">
        <p className="mb-3 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          <History className="size-3.5" /> History
        </p>
        <ol className="relative space-y-4 border-l border-border pl-4">
          {tracker.updates.map((update) => (
            <li key={update.update_id} className="relative">
              <span
                className={cn(
                  "absolute -left-[1.3rem] top-1.5 size-2.5 rounded-full border-2 border-card",
                  update.kind === "update" ? "bg-primary" : "bg-mist-400",
                )}
                aria-hidden="true"
              />
              <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span className="font-medium text-foreground">
                  {formatDate(update.occurred_at, update.kind !== "history")}
                </span>
                <span>{KIND_LABELS[update.kind]}</span>
                {update.status && <StatusPill status={update.status} />}
              </p>
              <p className="mt-0.5 text-sm">{update.summary}</p>
              {update.source_label && (
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Source:{" "}
                  {update.source_url ? (
                    <a
                      href={safeHref(update.source_url)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-0.5 font-medium text-primary hover:underline"
                    >
                      {update.source_label} <ExternalLink className="size-3" />
                    </a>
                  ) : (
                    update.source_label
                  )}
                </p>
              )}
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
