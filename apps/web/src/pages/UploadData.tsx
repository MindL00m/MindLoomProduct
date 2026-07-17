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
  importWhatsAppExport,
  pollJob,
  previewWhatsAppExport,
  uploadConversationJson,
  uploadDocument,
  type DocumentMetadata,
  type IngestionResult,
  type WhatsAppPreview,
} from "@/services/ingest";
import { cn } from "@/lib/utils";
import { useSession } from "@/store/session";
import { PrimaryButton } from "@/components/PrimaryButton";

type ItemKind = "document" | "json";
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

export default function UploadData() {
  const [items, setItems] = useState<UploadItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const [metadata, setMetadata] = useState<DocumentMetadata>({});
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const isAdmin = useSession((state) => state.role === "admin");

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
        update(id, { status: "failed", message: "Unsupported document type." });
        return;
      }

      try {
        const { job_id } =
          kind === "json"
            ? await uploadConversationJson(file)
            : await uploadDocument(file, {
                ...metadata,
                title: metadata.title || file.name,
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
    [metadata, update],
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
          Add company documents and exported conversations. Their content and
          source context are retained together.
        </p>
      </div>

      <div className="grid gap-3 rounded-lg border border-border bg-card p-4 sm:grid-cols-2">
        <MetadataField label="Title (optional)" value={metadata.title} onChange={(title) => setMetadata({ ...metadata, title })} />
        <MetadataField label="Author or owner" value={metadata.author} onChange={(author) => setMetadata({ ...metadata, author })} />
        <MetadataField label="Department" value={metadata.department} onChange={(department) => setMetadata({ ...metadata, department })} />
        <MetadataField label="Project" value={metadata.project} onChange={(project) => setMetadata({ ...metadata, project })} />
        <MetadataField label="Version" value={metadata.version} onChange={(version) => setMetadata({ ...metadata, version })} />
        <MetadataField label="Original source link" value={metadata.source_url} onChange={(source_url) => setMetadata({ ...metadata, source_url })} />
      </div>

      {isAdmin && <WhatsAppImport metadata={metadata} />}

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

function WhatsAppImport({ metadata }: { metadata: DocumentMetadata }) {
  const [file, setFile] = useState<File | null>(null);
  const [timezoneName, setTimezoneName] = useState(
    Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  );
  const [preview, setPreview] = useState<WhatsAppPreview | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [accessTokens, setAccessTokens] = useState("");

  async function selectFile(selected: File | null) {
    setFile(selected);
    setPreview(null);
    setStatus(null);
    if (!selected) return;
    setBusy(true);
    try {
      setPreview(await previewWhatsAppExport(selected, timezoneName));
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Preview failed.");
    } finally {
      setBusy(false);
    }
  }

  async function startImport() {
    if (!file || !preview) return;
    setBusy(true);
    setStatus("Queuing WhatsApp export…");
    try {
      const { job_id } = await importWhatsAppExport(file, timezoneName, {
        ...metadata,
        title: metadata.title || file.name.replace(/\.txt$/i, ""),
        source_application: "WhatsApp",
        source_location: metadata.source_location || "WhatsApp chat export",
        permissions: accessTokens
          .split(",")
          .map((token) => token.trim())
          .filter(Boolean),
      });
      const result = await pollJob(job_id, (job) => setStatus(job.progress || job.status));
      if (result.status === "complete") {
        setStatus(
          `Imported ${result.result?.total_messages ?? preview.message_count} messages into searchable knowledge.`,
        );
        setFile(null);
        setPreview(null);
      } else {
        setStatus(result.error || "WhatsApp import failed.");
      }
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "WhatsApp import failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-lg border border-border bg-card p-4">
      <div>
        <h3 className="font-medium">WhatsApp export</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          Export a WhatsApp chat without media, then upload its .txt file. Previewing does not import anything.
        </p>
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-[1fr_220px]">
        <input
          type="file"
          accept=".txt,text/plain"
          className="block w-full rounded-md border border-border p-2 text-sm"
          onChange={(event) => void selectFile(event.target.files?.[0] || null)}
        />
        <label className="text-sm">
          Chat timezone
          <input
            className="mt-1 block w-full rounded-md border border-border bg-background px-3 py-2"
            value={timezoneName}
            onChange={(event) => setTimezoneName(event.target.value)}
            onBlur={() => file && void selectFile(file)}
            placeholder="Asia/Singapore"
          />
        </label>
      </div>
      <label className="mt-3 block text-sm">
        Restricted search access (optional)
        <input
          className="mt-1 block w-full rounded-md border border-border bg-background px-3 py-2"
          value={accessTokens}
          onChange={(event) => setAccessTokens(event.target.value)}
          placeholder="Comma-separated employee emails or access groups"
        />
        <span className="mt-1 block text-xs text-muted-foreground">
          Leave blank for everyone in the organization.
        </span>
      </label>
      {busy && !preview && <p className="mt-3 flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />Reading export…</p>}
      {preview && (
        <div className="mt-4 space-y-3 rounded-md bg-muted/50 p-3">
          <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            <span><strong>{preview.message_count}</strong><br />messages</span>
            <span><strong>{preview.participant_count}</strong><br />participants</span>
            <span><strong>{new Date(preview.first_message_at).toLocaleDateString()}</strong><br />first message</span>
            <span><strong>{new Date(preview.last_message_at).toLocaleDateString()}</strong><br />last message</span>
          </div>
          <p className="text-xs text-muted-foreground">Participants: {preview.participants.join(", ")}</p>
          <p className="text-xs text-muted-foreground">{preview.ignored_notice}</p>
          <PrimaryButton disabled={busy} onClick={() => void startImport()}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            Import WhatsApp knowledge
          </PrimaryButton>
        </div>
      )}
      {status && <p className="mt-3 text-sm text-muted-foreground">{status}</p>}
    </section>
  );
}

function UploadRow({ item }: { item: UploadItem }) {
  const Icon = item.kind === "document" ? FileText : FileJson;
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
