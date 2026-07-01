import { useCallback, useId, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  FileJson,
  FileText,
  Loader2,
  UploadCloud,
} from "lucide-react";
import {
  pollJob,
  uploadConversationJson,
  uploadPdf,
  type IngestionResult,
} from "@/services/ingest";
import { cn } from "@/lib/utils";

type ItemKind = "pdf" | "json";
type ItemStatus = "uploading" | "processing" | "complete" | "failed";

interface UploadItem {
  id: string;
  name: string;
  kind: ItemKind | null;
  status: ItemStatus;
  message?: string;
  result?: IngestionResult;
}

const ACCEPT = ".json,.pdf,application/json,application/pdf";

function kindOf(file: File): ItemKind | null {
  const name = file.name.toLowerCase();
  if (name.endsWith(".pdf") || file.type === "application/pdf") return "pdf";
  if (name.endsWith(".json") || file.type === "application/json") return "json";
  return null;
}

export default function UploadData() {
  const [items, setItems] = useState<UploadItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();

  const update = useCallback((id: string, patch: Partial<UploadItem>) => {
    setItems((prev) =>
      prev.map((item) => (item.id === id ? { ...item, ...patch } : item)),
    );
  }, []);

  const start = useCallback(
    async (file: File) => {
      const id = crypto.randomUUID();
      const kind = kindOf(file);
      setItems((prev) => [
        { id, name: file.name, kind, status: "uploading" as ItemStatus },
        ...prev,
      ]);

      if (kind === null) {
        update(id, { status: "failed", message: "Unsupported file type. Use JSON or PDF." });
        return;
      }

      try {
        const { job_id } =
          kind === "pdf"
            ? await uploadPdf(file)
            : await uploadConversationJson(file);
        update(id, { status: "processing", message: "Chunking & classifying…" });

        const final = await pollJob(job_id, (status) => {
          if (status.status === "processing" || status.status === "queued") {
            update(id, {
              status: "processing",
              message: status.progress ?? "Processing…",
            });
          }
        });

        if (final.status === "complete") {
          update(id, {
            status: "complete",
            message: undefined,
            result: final.result ?? undefined,
          });
        } else {
          update(id, { status: "failed", message: final.error ?? "Ingestion failed." });
        }
      } catch (err) {
        update(id, {
          status: "failed",
          message: err instanceof Error ? err.message : "Upload failed.",
        });
      }
    },
    [update],
  );

  const handleFiles = useCallback(
    (files: FileList | null) => {
      if (!files) return;
      Array.from(files).forEach((file) => void start(file));
    },
    [start],
  );

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">Upload data</h2>
        <p className="mt-1 text-muted-foreground">
          Add knowledge to your graph. Conversation exports (<code>.json</code>)
          and documents (<code>.pdf</code>) are chunked, classified, and made
          queryable.
        </p>
      </div>

      <input
        id={inputId}
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        multiple
        className="sr-only"
        onChange={(e) => {
          handleFiles(e.target.files);
          e.target.value = "";
        }}
      />

      <label
        htmlFor={inputId}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          handleFiles(e.dataTransfer.files);
        }}
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-6 py-12 text-center transition-colors",
          dragging
            ? "border-primary bg-brand-50/50"
            : "border-border hover:border-mist-400 hover:bg-muted/50",
        )}
      >
        <span className="flex size-12 items-center justify-center rounded-full bg-muted text-mist-700">
          <UploadCloud className="size-6" aria-hidden="true" />
        </span>
        <span className="text-sm font-medium text-foreground">
          Drag &amp; drop files, or{" "}
          <span className="text-primary underline-offset-2 hover:underline">
            browse
          </span>
        </span>
        <span className="text-xs text-muted-foreground">
          JSON conversation exports or PDF documents
        </span>
      </label>

      {items.length > 0 && (
        <ul className="space-y-2">
          {items.map((item) => (
            <UploadRow key={item.id} item={item} />
          ))}
        </ul>
      )}
    </div>
  );
}

function UploadRow({ item }: { item: UploadItem }) {
  const Icon = item.kind === "pdf" ? FileText : FileJson;
  return (
    <li className="flex items-center gap-3 rounded-md border border-border bg-card p-3">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-brand-50 text-brand-700">
        <Icon className="size-4" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{item.name}</p>
        <p className="truncate text-xs text-muted-foreground">
          {item.status === "complete" && item.result
            ? `${item.result.total_chunks} chunk${item.result.total_chunks === 1 ? "" : "s"} added${
                item.result.failed_chunks
                  ? ` · ${item.result.failed_chunks} failed`
                  : ""
              }`
            : item.status === "failed"
              ? item.message
              : (item.message ?? "Uploading…")}
        </p>
      </div>
      <StatusPill status={item.status} />
    </li>
  );
}

function StatusPill({ status }: { status: ItemStatus }) {
  if (status === "complete") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-success/10 px-2.5 py-0.5 text-xs font-semibold text-success ring-1 ring-inset ring-success/20">
        <CheckCircle2 className="size-3.5" /> Done
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-destructive/10 px-2.5 py-0.5 text-xs font-semibold text-destructive ring-1 ring-inset ring-destructive/20">
        <AlertCircle className="size-3.5" /> Failed
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-0.5 text-xs font-semibold text-muted-foreground ring-1 ring-inset ring-border">
      <Loader2 className="size-3.5 animate-spin" />
      {status === "uploading" ? "Uploading" : "Processing"}
    </span>
  );
}
