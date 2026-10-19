import { useCallback, useId, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  FileJson,
  FileText,
  Loader2,
  MessageCircle,
  UploadCloud,
} from "lucide-react";
import {
  importWhatsAppExport,
  pollJob,
  uploadConversationJson,
  uploadDocument,
  type DocumentMetadata,
  type IngestionResult,
} from "@/services/ingest";
import { cn } from "@/lib/utils";

type ItemKind = "document" | "json" | "whatsapp";
type ItemStatus = "uploading" | "processing" | "complete" | "failed";

interface UploadItem {
  id: string;
  name: string;
  kind: ItemKind | null;
  status: ItemStatus;
  message?: string;
  result?: IngestionResult;
}

const ACCEPT = ".json,.jsonl,.pdf,.docx,.pptx,.xlsx,.csv,.txt,.md,.log";

function kindOf(file: File): ItemKind | null {
  const name = file.name.toLowerCase();
  if (name.endsWith(".json") || file.type === "application/json") return "json";
  if (/\.(pdf|docx|pptx|xlsx|csv|txt|md|log|jsonl)$/.test(name)) return "document";
  return null;
}

// Mirrors the bracket and dash message headers accepted by the API's WhatsApp parser.
const WHATSAPP_MESSAGE_LINE =
  /^\u200e?\[?\d{1,2}\/\d{1,2}\/\d{2,4},\s*\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?\]?\s*(?:-\s*)?[^:\n]+:\s/m;

async function looksLikeWhatsAppExport(file: File): Promise<boolean> {
  if (!file.name.toLowerCase().endsWith(".txt")) return false;
  return WHATSAPP_MESSAGE_LINE.test(await file.slice(0, 64 * 1024).text());
}

function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

export default function UploadData({ showHeading = true }: { showHeading?: boolean }) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const [metadata, setMetadata] = useState<DocumentMetadata>({});
  const [visibility, setVisibility] = useState<"private" | "organization">("private");
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
      let kind = kindOf(file);
      setItems((prev) => [
        { id, name: file.name, kind, status: "uploading" as ItemStatus },
        ...prev,
      ]);

      if (kind === null) {
        update(id, { status: "failed", message: "Unsupported document type." });
        return;
      }

      try {
        if (kind === "document" && (await looksLikeWhatsAppExport(file))) {
          kind = "whatsapp";
          update(id, { kind });
        }
        const { job_id } =
          kind === "json"
            ? await uploadConversationJson(file, { visibility })
            : kind === "whatsapp"
              ? await importWhatsAppExport(file, browserTimeZone(), {
                  ...metadata,
                  title: metadata.title || file.name.replace(/\.txt$/i, ""),
                  source_application: "WhatsApp",
                  source_location: metadata.source_location || "WhatsApp chat export",
                  visibility,
                })
              : await uploadDocument(file, {
                  ...metadata,
                  title: metadata.title || file.name,
                  visibility,
                });
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
    [metadata, update, visibility],
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
      {showHeading && (
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Upload data</h2>
          <p className="mt-1 text-muted-foreground">
            Add company documents and exported conversations. Their content and
            source context are retained together.
          </p>
        </div>
      )}

      <div className="grid gap-3 rounded-lg border border-border bg-card p-4 sm:grid-cols-2">
        <MetadataField label="Title (optional)" value={metadata.title} onChange={(title) => setMetadata({ ...metadata, title })} />
        <MetadataField label="Author or owner" value={metadata.author} onChange={(author) => setMetadata({ ...metadata, author })} />
        <MetadataField label="Department" value={metadata.department} onChange={(department) => setMetadata({ ...metadata, department })} />
        <MetadataField label="Project" value={metadata.project} onChange={(project) => setMetadata({ ...metadata, project })} />
        <MetadataField label="Version" value={metadata.version} onChange={(version) => setMetadata({ ...metadata, version })} />
        <MetadataField label="Original source link" value={metadata.source_url} onChange={(source_url) => setMetadata({ ...metadata, source_url })} />
        <div className="sm:col-span-2">
          <VisibilityToggle value={visibility} onChange={setVisibility} />
        </div>
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
          PDF, DOCX, PPTX, XLSX, CSV, text, logs, JSONL, or conversation JSON
        </span>
        <span className="text-xs text-muted-foreground">
          WhatsApp chat exports (.txt, without media) are detected automatically and
          read in your timezone ({browserTimeZone()}).
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
  const Icon =
    item.kind === "whatsapp" ? MessageCircle : item.kind === "document" ? FileText : FileJson;
  return (
    <li className="flex items-center gap-3 rounded-md border border-border bg-card p-3">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-brand-50 text-brand-700">
        <Icon className="size-4" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{item.name}</p>
        <p className="truncate text-xs text-muted-foreground">
          {item.status === "complete" && item.result && item.kind === "whatsapp"
            ? `WhatsApp chat · ${item.result.total_messages} message${item.result.total_messages === 1 ? "" : "s"} imported`
            : item.status === "complete" && item.result
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

function MetadataField({
  label,
  value,
  onChange,
}: {
  label: string;
  value?: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="text-sm">
      {label}
      <input
        className="mt-1 block w-full rounded-md border border-border bg-background px-3 py-2"
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function VisibilityToggle({
  value,
  onChange,
}: {
  value: "private" | "organization";
  onChange: (value: "private" | "organization") => void;
}) {
  return (
    <div>
      <p className="text-sm font-medium">Search visibility</p>
      <p className="mt-0.5 text-xs text-muted-foreground">
        Controls who can find this content in Ask. Defaults to only you.
      </p>
      <div className="mt-2 flex gap-1 rounded-md border border-border bg-background p-1">
        <button
          type="button"
          onClick={() => onChange("private")}
          className={cn(
            "flex-1 rounded px-3 py-1.5 text-sm font-medium transition-colors",
            value === "private"
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:bg-muted",
          )}
        >
          Only me
        </button>
        <button
          type="button"
          onClick={() => onChange("organization")}
          className={cn(
            "flex-1 rounded px-3 py-1.5 text-sm font-medium transition-colors",
            value === "organization"
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:bg-muted",
          )}
        >
          Organisation
        </button>
      </div>
    </div>
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
