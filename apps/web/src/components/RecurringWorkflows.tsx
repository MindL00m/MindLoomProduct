import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { History, Loader2, Mail, Pause, Play, Repeat, Send, Sparkles, Trash2 } from "lucide-react";
import {
  deleteRecurringEmail,
  getRecurringEmail,
  listRecurringEmails,
  sendRecurringEmailNow,
  updateRecurringEmail,
  type RecurringEmail,
  type RecurringEmailRun,
} from "@/services/workflows";
import { cn } from "@/lib/utils";

const REFRESH_MS = 60_000;

function when(value?: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** Recurring email workflows created from Ask, marked as repeating. */
export function RecurringWorkflows() {
  const [searchParams] = useSearchParams();
  const highlight = searchParams.get("workflow");
  const [items, setItems] = useState<RecurringEmail[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setItems(await listRecurringEmails());
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load recurring workflows.");
      setItems((current) => current ?? []);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  function replace(updated: RecurringEmail | null, id: string) {
    setItems((current) =>
      (current ?? []).flatMap((item) => (item.workflow_id === id ? (updated ? [updated] : []) : [item])),
    );
  }

  return (
    <section aria-labelledby="recurring-heading" className="space-y-3">
      <div>
        <h3 id="recurring-heading" className="flex items-center gap-2 text-base font-semibold">
          <Repeat className="size-4 text-primary" /> Recurring workflows
        </h3>
        <p className="mt-0.5 text-sm text-muted-foreground">
          Emails that repeat at a fixed interval, created from Ask. They send from your Gmail.
        </p>
      </div>
      {error && (
        <p className="rounded-md border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </p>
      )}
      {items === null ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading…
        </p>
      ) : items.length === 0 ? (
        <p className="rounded-md border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
          No recurring workflows yet. In Ask, try “Email priya@acme.com the Tiramisu status every
          Monday at 9am.”
        </p>
      ) : (
        <ul className="space-y-2">
          {items.map((item) => (
            <RecurringRow
              key={item.workflow_id}
              item={item}
              highlighted={item.workflow_id === highlight}
              onChange={(updated) => replace(updated, item.workflow_id)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function RecurringRow({
  item,
  highlighted,
  onChange,
}: {
  item: RecurringEmail;
  highlighted: boolean;
  onChange: (updated: RecurringEmail | null) => void;
}) {
  const [busy, setBusy] = useState<"" | "toggle" | "send" | "delete">("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [runs, setRuns] = useState<RecurringEmailRun[] | null>(null);
  const [showRuns, setShowRuns] = useState(false);
  const ref = useRef<HTMLLIElement>(null);

  useEffect(() => {
    if (highlighted) ref.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [highlighted]);

  useEffect(() => {
    if (!showRuns) return;
    void getRecurringEmail(item.workflow_id)
      .then((detail) => setRuns(detail.runs ?? []))
      .catch(() => setRuns([]));
  }, [showRuns, item.workflow_id, item.run_count, item.last_run_at]);

  async function run(kind: typeof busy, action: () => Promise<void>) {
    setBusy(kind);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy("");
    }
  }

  const active = item.status === "active";
  const statusLabel = active ? "Active" : item.status === "error" ? "Paused after errors" : "Paused";

  return (
    <li
      ref={ref}
      className={cn(
        "rounded-lg border bg-card px-4 py-3 text-sm",
        highlighted ? "border-primary ring-2 ring-primary/30" : "border-border",
      )}
    >
      <div className="flex flex-wrap items-start gap-3">
        <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-brand-50 text-brand-700">
          <Mail className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-2 font-medium text-foreground">
            <span className="truncate">{item.subject}</span>
            <span className="inline-flex items-center gap-1 rounded-full border border-primary/30 bg-brand-50 px-2 py-0.5 text-[11px] font-semibold text-brand-700">
              <Repeat className="size-3" /> Repeats {item.repeats}
            </span>
            <span
              className={cn(
                "rounded-full border px-2 py-0.5 text-[11px] font-medium",
                active
                  ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                  : item.status === "error"
                    ? "border-red-200 bg-red-50 text-red-800"
                    : "border-border bg-muted text-muted-foreground",
              )}
            >
              {statusLabel}
            </span>
            {item.refresh_instructions && (
              <span className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">
                <Sparkles className="size-3" /> Fresh content each time
              </span>
            )}
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            To {item.recipient_name ? `${item.recipient_name} <${item.recipient_email}>` : item.recipient_email}
            {" · "}
            {active ? `Next: ${when(item.next_run_at)}` : "Not sending"}
            {" · "}
            {item.run_count === 1 ? "Sent once" : `Sent ${item.run_count} times`}
            {item.last_run_at ? ` · Last: ${when(item.last_run_at)}` : ""}
          </p>
          {item.status === "error" && item.last_error && (
            <p className="mt-1 text-xs text-destructive">Last error: {item.last_error}</p>
          )}
          {error && <p className="mt-1 text-xs text-destructive">{error}</p>}
          {notice && <p className="mt-1 text-xs text-emerald-800">{notice}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <button
            type="button"
            disabled={busy !== ""}
            onClick={() =>
              void run("toggle", async () =>
                onChange(await updateRecurringEmail(item.workflow_id, { status: active ? "paused" : "active" })),
              )
            }
            className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 font-medium hover:bg-muted/60 disabled:opacity-50"
          >
            {busy === "toggle" ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : active ? (
              <Pause className="size-3.5" />
            ) : (
              <Play className="size-3.5" />
            )}
            {active ? "Pause" : "Resume"}
          </button>
          <button
            type="button"
            disabled={busy !== ""}
            onClick={() =>
              void run("send", async () => {
                onChange(await sendRecurringEmailNow(item.workflow_id));
                setNotice("Sent. The schedule is unchanged.");
              })
            }
            className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 font-medium hover:bg-muted/60 disabled:opacity-50"
          >
            {busy === "send" ? <Loader2 className="size-3.5 animate-spin" /> : <Send className="size-3.5" />}
            Send now
          </button>
          <button
            type="button"
            onClick={() => setShowRuns((v) => !v)}
            className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 font-medium hover:bg-muted/60"
          >
            <History className="size-3.5" /> History
          </button>
          {confirmDelete ? (
            <>
              <button
                type="button"
                disabled={busy !== ""}
                onClick={() =>
                  void run("delete", async () => {
                    await deleteRecurringEmail(item.workflow_id);
                    onChange(null);
                  })
                }
                className="rounded-md bg-destructive px-2.5 py-1.5 font-medium text-destructive-foreground disabled:opacity-50"
              >
                {busy === "delete" ? "Deleting…" : "Delete"}
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
              aria-label="Delete workflow"
              className="rounded-md border border-border p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
            >
              <Trash2 className="size-3.5" />
            </button>
          )}
        </div>
      </div>
      {showRuns && (
        <div className="mt-3 border-t border-border pt-2 text-xs">
          {runs === null ? (
            <p className="text-muted-foreground">Loading…</p>
          ) : runs.length === 0 ? (
            <p className="text-muted-foreground">Nothing sent yet.</p>
          ) : (
            <ul className="space-y-1">
              {runs.map((r) => (
                <li key={r.run_id} className="flex flex-wrap gap-x-2">
                  <span className="font-medium text-foreground">{when(r.ran_at)}</span>
                  <span className={r.status === "sent" ? "text-emerald-800" : "text-destructive"}>
                    {r.status === "sent" ? "Sent" : `Failed: ${r.error ?? "unknown error"}`}
                  </span>
                  {r.status === "sent" && r.body_preview && (
                    <span className="truncate text-muted-foreground">“{r.body_preview.slice(0, 120)}”</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </li>
  );
}
