import { useEffect, useRef, useState } from "react";
import {
  Bot,
  Download,
  ExternalLink,
  Loader2,
  Pencil,
  RefreshCw,
  Save,
  Sparkles,
  Trash2,
  Undo2,
  Upload,
} from "lucide-react";
import {
  createUpdatedVersion,
  deleteFile,
  downloadFileBlob,
  getFilePreview,
  openInExcel,
  replaceFileUpload,
  saveFileToDisk,
  undoFile,
  updateFile,
  type DocBlock,
  type FilePreview,
  type LoomFile,
  type LoomFileMeta,
  type ExtractZipResult,
  extractZip,
  type SheetPreview,
  type SlidePreview,
  IMAGE_KINDS,
  type UpdatedVersionResult,
} from "@/services/files";
import { fileDetail, FileKindIcon } from "@/components/FileKindIcon";
import { SharedByLine, ShareButton } from "@/pages/FileSharing";
import { formatBytes, timeAgo } from "@/lib/format";
import { TemplateControls } from "@/components/TemplatePanel";
import { cn } from "@/lib/utils";

const KIND_LABEL: Record<string, string> = {
  txt: "Text file",
  docx: "Word document",
  xlsx: "Excel workbook",
  pdf: "PDF",
  csv: "CSV",
  md: "Markdown",
  json: "JSON",
  pptx: "PowerPoint",
  png: "Picture",
  jpg: "Picture",
  jpeg: "Picture",
  gif: "Picture",
  webp: "Picture",
  file: "File",
};

/**
 * "Open in Excel": hands Excel a 10-minute signed link. Browsers cannot tell
 * whether Excel opened, so a short hint offers the download as a fallback.
 */
export function ExcelButton({ fileId, name, compact }: { fileId: string; name: string; compact?: boolean }) {
  const [state, setState] = useState<"" | "opening" | "hint" | "error">("");
  const [error, setError] = useState("");
  const timer = useRef<number>();

  useEffect(() => () => window.clearTimeout(timer.current), []);

  async function open() {
    setState("opening");
    setError("");
    try {
      await openInExcel(fileId);
      setState("hint");
      timer.current = window.setTimeout(() => setState(""), 12_000);
    } catch (err) {
      setState("error");
      setError(err instanceof Error ? err.message : "Could not open in Excel.");
    }
  }

  return (
    <span className="relative inline-flex">
      <button
        type="button"
        onClick={() => void open()}
        disabled={state === "opening"}
        className={cn(
          "flex items-center gap-1.5 rounded-md border border-emerald-200 bg-emerald-50 font-medium text-emerald-800 hover:bg-emerald-100 disabled:opacity-50",
          compact ? "px-2 py-1 text-xs" : "px-2.5 py-1.5 text-xs",
        )}
      >
        {state === "opening" ? <Loader2 className="size-3.5 animate-spin" /> : <ExternalLink className="size-3.5" />}
        Open in Excel
      </button>
      {(state === "hint" || state === "error") && (
        <span
          role="status"
          className="absolute right-0 top-full z-20 mt-1 w-72 rounded-md border border-border bg-card p-2.5 text-xs shadow-lg"
        >
          {state === "error" ? (
            <span className="text-destructive">{error}</span>
          ) : (
            <>
              Excel should open now. Nothing happened?{" "}
              <button
                type="button"
                className="font-medium text-primary hover:underline"
                onClick={() => void saveFileToDisk(fileId, name)}
              >
                Download the file
              </button>{" "}
              and open it in Excel. Changes made in Excel are not saved here; use “Upload new
              version” to bring them back.
            </>
          )}
        </span>
      )}
    </span>
  );
}

/** Viewer and actions for TXT, DOCX, XLSX, and PDF files (TXT and DOCX are editable). */
export function DocumentFileView({
  file,
  onSaved,
  onDeleted,
  onDirtyChange,
  onCreated,
}: {
  file: LoomFile;
  onSaved: (file: LoomFile) => void;
  onDeleted: (fileId: string) => void;
  onDirtyChange: (dirty: boolean) => void;
  onCreated: (file: LoomFileMeta) => void;
}) {
  const [name, setName] = useState(file.name);
  const [text, setText] = useState(file.content ?? "");
  const [busy, setBusy] = useState<"" | "save" | "undo" | "delete" | "rename" | "upload" | "edit">("");
  const [error, setError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [showUpdate, setShowUpdate] = useState(false);
  const replaceInput = useRef<HTMLInputElement>(null);
  const isText = file.kind === "txt" || file.kind === "md" || file.kind === "json";
  // Word documents are edited as Markdown and rebuilt on save (undoable).
  const [docText, setDocText] = useState<string | null>(null);
  const [docOriginal, setDocOriginal] = useState("");
  const editingDoc = file.kind === "docx" && docText !== null;
  const dirty = isText ? text !== (file.content ?? "") : editingDoc && docText !== docOriginal;

  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);

  async function run(kind: typeof busy, action: () => Promise<void>) {
    setBusy(kind);
    setError("");
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy("");
    }
  }

  const rename = () => {
    const next = name.trim();
    if (!next || next === file.name) {
      setName(file.name);
      return;
    }
    void run("rename", async () => {
      try {
        onSaved(await updateFile(file.file_id, { name: next }));
      } catch (err) {
        setName(file.name);
        throw err;
      }
    });
  };

  const undo = () => {
    if (dirty && !window.confirm("Undo the last saved change and discard your unsaved edits?")) return;
    void run("undo", async () => onSaved(await undoFile(file.file_id)));
  };

  function startDocEdit() {
    void run("edit", async () => {
      const preview = await getFilePreview(file.file_id);
      const markdown = preview.kind === "docx" ? preview.markdown ?? "" : "";
      setDocOriginal(markdown);
      setDocText(markdown);
    });
  }

  function saveText() {
    void run("save", async () => {
      const content = isText ? text : docText ?? "";
      const saved = await updateFile(file.file_id, { content });
      setDocText(null);
      onSaved(saved);
    });
  }

  function discard() {
    if (isText) setText(file.content ?? "");
    else setDocText(null);
  }

  function replace(list: FileList | null) {
    const picked = list?.[0];
    if (!picked) return;
    void run("upload", async () => onSaved(await replaceFileUpload(file.file_id, picked)));
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
        <FileKindIcon kind={file.kind} />
        <div className="min-w-0 flex-1">
          <input
            aria-label="File name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={rename}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
              if (e.key === "Escape") setName(file.name);
            }}
            className="w-full truncate rounded bg-transparent px-1 py-0.5 font-medium outline-none hover:bg-muted/60 focus:bg-muted/60"
          />
          <p className="flex flex-wrap items-center gap-1 px-1 text-xs text-muted-foreground">
            {KIND_LABEL[file.kind]} · {fileDetail(file)} · {formatBytes(file.size_bytes)} ·{" "}
            {file.last_edited_by === "loombot" ? "Made by LoomBot" : "Updated"} {timeAgo(file.updated_at)}
            {file.based_on_file_id && (
              <span className="inline-flex items-center gap-0.5 rounded-full bg-brand-50 px-1.5 text-[10px] font-medium text-brand-700">
                <Bot className="size-3" /> Updated version
              </span>
            )}
          </p>
          <SharedByLine file={file} />
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {["docx", "xlsx", "pdf", "txt"].includes(file.kind) && (
            <button
              type="button"
              onClick={() => setShowUpdate((v) => !v)}
              className="flex items-center gap-1.5 rounded-md border border-primary/40 bg-brand-50 px-2.5 py-1.5 text-xs font-medium text-brand-700 hover:bg-brand-100"
            >
              <Sparkles className="size-3.5" /> Update from memory
            </button>
          )}
          {file.kind === "xlsx" && <ExcelButton fileId={file.file_id} name={file.name} />}
          <TemplateControls
            file={file}
            onChanged={(changed) => onSaved({ ...file, ...changed })}
            onCreated={onCreated}
          />
          {file.kind === "docx" && !editingDoc && (
            <button
              type="button"
              disabled={busy !== ""}
              onClick={startDocEdit}
              className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted/60 disabled:opacity-50"
            >
              {busy === "edit" ? <Loader2 className="size-3.5 animate-spin" /> : <Pencil className="size-3.5" />}
              Edit text
            </button>
          )}
          {file.can_undo && (
            <button
              type="button"
              disabled={busy !== ""}
              onClick={undo}
              className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted/60 disabled:opacity-50"
            >
              {busy === "undo" ? <Loader2 className="size-3.5 animate-spin" /> : <Undo2 className="size-3.5" />}
              Undo
            </button>
          )}
          {!isText && (
            <button
              type="button"
              disabled={busy !== ""}
              onClick={() => replaceInput.current?.click()}
              className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted/60 disabled:opacity-50"
            >
              {busy === "upload" ? <Loader2 className="size-3.5 animate-spin" /> : <Upload className="size-3.5" />}
              Upload new version
            </button>
          )}
          <input
            ref={replaceInput}
            type="file"
            accept={`.${file.kind}`}
            className="sr-only"
            onChange={(e) => {
              replace(e.target.files);
              e.target.value = "";
            }}
          />
          <ShareButton file={file} />
          <button
            type="button"
            onClick={() => void run("", () => saveFileToDisk(file.file_id, file.name))}
            className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted/60"
          >
            <Download className="size-3.5" /> Download
          </button>
          {confirmDelete ? (
            <span className="flex items-center gap-1.5 text-xs">
              Delete {file.name}?
              <button
                type="button"
                disabled={busy !== ""}
                onClick={() =>
                  void run("delete", async () => {
                    await deleteFile(file.file_id);
                    onDeleted(file.file_id);
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
            </span>
          ) : (
            <button
              type="button"
              onClick={() => setConfirmDelete(true)}
              aria-label="Delete file"
              className="rounded-md border border-border p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
            >
              <Trash2 className="size-3.5" />
            </button>
          )}
        </div>
      </header>

      {error && (
        <p className="mx-4 mt-3 rounded-md border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </p>
      )}

      {showUpdate && <UpdateFromMemoryPanel file={file} onCreated={onCreated} />}

      {isText ? (
        <textarea
          aria-label="File text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="m-4 min-h-0 flex-1 resize-none rounded-md border border-border bg-background p-3 font-mono text-sm leading-relaxed outline-none focus:border-primary"
        />
      ) : editingDoc ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <p className="mx-4 mt-3 rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
            Editing as text: # headings, - bullets, 1. numbered lists, **bold**, *italic*, and |
            tables. Saving rebuilds the Word file with standard styles, so custom fonts and layout
            are not kept. Undo restores the previous version.
          </p>
          <textarea
            aria-label="Document text"
            value={docText ?? ""}
            onChange={(e) => setDocText(e.target.value)}
            className="m-4 min-h-0 flex-1 resize-none rounded-md border border-border bg-background p-3 font-mono text-sm leading-relaxed outline-none focus:border-primary"
          />
        </div>
      ) : file.kind === "pdf" ? (
        <PdfFrame fileId={file.file_id} updatedAt={file.updated_at} />
      ) : IMAGE_KINDS.includes(file.kind) ? (
        <div className="m-4 flex min-h-0 flex-1 flex-col">
          <BlobFrame
            load={() => downloadFileBlob(file.file_id)}
            loadKey={`${file.file_id}:${file.updated_at}`}
            type="image"
            title={file.name}
          />
          <p className="mt-2 text-xs text-muted-foreground">
            LoomBot reads the text in pictures with OCR when you ask about this file.
          </p>
        </div>
      ) : (
        <StructuredPreview
          fileId={file.file_id}
          updatedAt={file.updated_at}
          onExtracted={onCreated}
        />
      )}

      {(dirty || editingDoc) && (
        <footer className="flex items-center justify-end gap-2 border-t border-border bg-muted/40 px-4 py-2">
          <span className="mr-auto text-xs text-muted-foreground">
            {dirty ? "Unsaved changes" : "No changes yet"}
          </span>
          <button
            type="button"
            disabled={busy !== ""}
            onClick={discard}
            className="rounded-md border border-border bg-card px-3 py-1.5 text-xs font-medium disabled:opacity-50"
          >
            {editingDoc && !dirty ? "Done" : "Discard"}
          </button>
          <button
            type="button"
            disabled={busy !== "" || !dirty}
            onClick={saveText}
            className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:opacity-50"
          >
            {busy === "save" ? <Loader2 className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}
            Save
          </button>
        </footer>
      )}
    </div>
  );
}

function UpdateFromMemoryPanel({
  file,
  onCreated,
}: {
  file: LoomFileMeta;
  onCreated: (file: LoomFileMeta) => void;
}) {
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<UpdatedVersionResult | null>(null);

  async function go() {
    setBusy(true);
    setError("");
    setResult(null);
    try {
      setResult(await createUpdatedVersion(file.file_id, instructions));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create an updated version.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-4 mt-3 space-y-2 rounded-md border border-primary/30 bg-brand-50/60 px-3 py-2.5 text-sm">
      <p className="text-xs text-muted-foreground">
        LoomBot searches MindLoom’s memory for newer facts, edits only what it can cite, and saves
        an updated copy next to this file. This file is not changed.
      </p>
      <div className="flex flex-wrap gap-2">
        <input
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          maxLength={1000}
          placeholder="Optional: what to update, e.g. revenue figures and project statuses"
          aria-label="What to update"
          className="min-w-0 flex-1 rounded-md border border-border bg-background px-2.5 py-1.5 text-sm"
        />
        <button
          type="button"
          disabled={busy}
          onClick={() => void go()}
          className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:opacity-50"
        >
          {busy ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
          {busy ? "Researching…" : "Create updated version"}
        </button>
      </div>
      {error && <p className="text-xs text-destructive">{error}</p>}
      {result && (
        <div className="space-y-1.5 text-xs">
          <p className="font-medium text-foreground">{result.summary}</p>
          {result.changes.length > 0 && <ChangeList changes={result.changes} />}
          {result.warnings.map((warning) => (
            <p key={warning} className="text-amber-900">{warning}</p>
          ))}
          {result.changed && result.file && (
            <button
              type="button"
              onClick={() => onCreated(result.file!)}
              className="rounded-md border border-border bg-card px-2.5 py-1 font-medium hover:bg-muted/60"
            >
              Open {result.file.name}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export function ChangeList({
  changes,
}: {
  changes: { before: string; after: string; reason?: string; sources: string[] }[];
}) {
  return (
    <ul className="max-h-64 space-y-1.5 overflow-y-auto">
      {changes.map((change, i) => (
        <li key={i} className="rounded border border-border bg-card px-2 py-1.5">
          {change.before ? (
            <p className="text-muted-foreground line-through">{change.before}</p>
          ) : (
            <p className="text-[10px] font-semibold uppercase tracking-wide text-emerald-700">Added</p>
          )}
          <p className="text-foreground">{change.after}</p>
          <p className="mt-0.5 text-[11px] text-muted-foreground">Source: {change.sources.join("; ")}</p>
        </li>
      ))}
    </ul>
  );
}

/** A PDF or image fetched by ``load`` (e.g. from Google Drive or a mailbox). */
export function BlobFrame({
  load,
  loadKey,
  type,
  title,
}: {
  load: () => Promise<Blob>;
  loadKey: string;
  type: "pdf" | "image";
  title: string;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    setUrl(null);
    setError("");
    void load()
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(type === "pdf" ? new Blob([blob], { type: "application/pdf" }) : blob);
        setUrl(objectUrl);
      })
      .catch((err: Error) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadKey, type]);

  if (error) return <p className="m-4 text-sm text-destructive">{error}</p>;
  if (!url) {
    return (
      <p className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> Loading…
      </p>
    );
  }
  return type === "pdf" ? (
    <iframe title={title} src={url} className="m-4 min-h-0 flex-1 rounded-md border border-border" />
  ) : (
    <div className="min-h-0 flex-1 overflow-auto p-4">
      <img src={url} alt={title} className="mx-auto max-w-full rounded-md border border-border" />
    </div>
  );
}

function PdfFrame({ fileId, updatedAt }: { fileId: string; updatedAt: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    void downloadFileBlob(fileId)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(new Blob([blob], { type: "application/pdf" }));
        setUrl(objectUrl);
      })
      .catch((err: Error) => setError(err.message));
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [fileId, updatedAt]);

  if (error) return <p className="m-4 text-sm text-destructive">{error}</p>;
  if (!url) {
    return (
      <p className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> Loading PDF…
      </p>
    );
  }
  return <iframe title="PDF preview" src={url} className="m-4 min-h-0 flex-1 rounded-md border border-border" />;
}

function StructuredPreview({
  fileId,
  updatedAt,
  onExtracted,
}: {
  fileId: string;
  updatedAt: string;
  onExtracted?: (file: LoomFileMeta) => void;
}) {
  const [preview, setPreview] = useState<FilePreview | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setPreview(null);
    void getFilePreview(fileId)
      .then((value) => !cancelled && setPreview(value))
      .catch((err: Error) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [fileId, updatedAt]);

  if (error) return <p className="m-4 text-sm text-destructive">{error}</p>;
  if (!preview) {
    return (
      <p className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> Loading preview…
      </p>
    );
  }
  if (preview.kind === "docx") return <DocPreview blocks={preview.blocks} />;
  if (preview.kind === "xlsx") return <WorkbookPreview sheets={preview.sheets} />;
  if (preview.kind === "pptx") return <SlidesPreview slides={preview.slides} />;
  if (preview.kind === "zip") {
    return <ZipPreview fileId={fileId} entries={preview.entries} onExtracted={onExtracted} />;
  }
  if (preview.kind === "file") {
    return preview.text !== null ? (
      <pre className="m-4 min-h-0 flex-1 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-background p-3 font-mono text-xs">
        {preview.text}
      </pre>
    ) : (
      <p className="m-4 text-sm text-muted-foreground">
        This {preview.mime_type || "file"} can't be previewed here. Use Download to open it, or
        attach it to an email.
      </p>
    );
  }
  return null;
}

function ZipPreview({
  fileId,
  entries,
  onExtracted,
}: {
  fileId: string;
  entries: { path: string; size: number }[];
  onExtracted?: (file: LoomFileMeta) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ExtractZipResult | null>(null);
  const [error, setError] = useState("");

  async function extract() {
    setBusy(true);
    setError("");
    try {
      const done = await extractZip(fileId);
      setResult(done);
      if (done.created[0]) onExtracted?.(done.created[0]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not unpack the ZIP.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-medium">
          ZIP archive with {entries.length} file{entries.length === 1 ? "" : "s"}
        </p>
        <button
          type="button"
          disabled={busy || entries.length === 0}
          onClick={() => void extract()}
          className="flex items-center gap-1.5 rounded-md bg-primary px-2.5 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50"
        >
          {busy ? <Loader2 className="size-3.5 animate-spin" /> : null}
          Extract into Files
        </button>
      </div>
      {result && (
        <p className="text-xs text-foreground">
          Added {result.created.length} file{result.created.length === 1 ? "" : "s"} to your Files.
          {result.skipped.length
            ? ` Skipped ${result.skipped.length}: ${result.skipped
                .slice(0, 4)
                .map((s) => `${s.path} (${s.reason})`)
                .join(", ")}.`
            : ""}
        </p>
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
      <ul className="divide-y divide-border rounded-md border border-border text-sm">
        {entries.slice(0, 300).map((entry) => (
          <li key={entry.path} className="flex justify-between gap-3 px-3 py-1.5">
            <span className="truncate">{entry.path}</span>
            <span className="shrink-0 text-xs text-muted-foreground">{formatBytes(entry.size)}</span>
          </li>
        ))}
      </ul>
      <p className="text-xs text-muted-foreground">
        LoomBot can also read the documents inside this ZIP when you ask about it.
      </p>
    </div>
  );
}

function SlidesPreview({ slides }: { slides: SlidePreview[] }) {
  if (slides.length === 0) {
    return <p className="m-4 text-sm text-muted-foreground">This presentation has no slides.</p>;
  }
  return (
    <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
      {slides.map((slide) => (
        <section
          key={slide.number}
          className="aspect-video w-full max-w-2xl overflow-hidden rounded-md border border-border bg-card p-5 shadow-sm"
        >
          <p className="text-[11px] text-muted-foreground">Slide {slide.number}</p>
          <h3 className="mt-1 text-lg font-semibold">{slide.title}</h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
            {slide.lines.slice(0, 12).map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
          {slide.notes && (
            <p className="mt-2 border-t border-border pt-2 text-xs text-muted-foreground">
              Notes: {slide.notes}
            </p>
          )}
        </section>
      ))}
    </div>
  );
}

export function DocPreview({ blocks }: { blocks: DocBlock[] }) {
  if (blocks.length === 0) {
    return <p className="m-4 text-sm text-muted-foreground">This document has no text.</p>;
  }
  return (
    <article className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
      <div className="mx-auto max-w-3xl space-y-3 text-sm leading-relaxed">
        {blocks.map((block, i) => {
          if (block.type === "heading") {
            const size = block.level <= 1 ? "text-xl" : block.level === 2 ? "text-lg" : "text-base";
            return <h3 key={i} className={cn("pt-2 font-semibold tracking-tight", size)}>{block.text}</h3>;
          }
          if (block.type === "list_item") {
            return <p key={i} className="pl-4 before:mr-2 before:content-['•']">{block.text}</p>;
          }
          if (block.type === "table") {
            return (
              <div key={i} className="overflow-x-auto rounded-md border border-border">
                <table className="min-w-full border-collapse text-xs">
                  <tbody>
                    {block.rows.map((row, r) => (
                      <tr key={r} className={r === 0 ? "bg-muted/60 font-semibold" : "even:bg-muted/30"}>
                        {row.map((cell, c) => (
                          <td key={c} className="border-t border-border px-3 py-1.5 align-top">{cell}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          }
          return <p key={i}>{block.text}</p>;
        })}
      </div>
    </article>
  );
}

export function WorkbookPreview({ sheets }: { sheets: SheetPreview[] }) {
  const [active, setActive] = useState(0);
  const sheet = sheets[active];
  if (!sheet) return <p className="m-4 text-sm text-muted-foreground">This workbook has no sheets.</p>;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="min-w-full border-separate border-spacing-0 text-sm">
          <tbody>
            {sheet.rows.map((row, r) => (
              <tr key={r}>
                <td className="sticky left-0 w-10 border-b border-r border-border bg-muted px-2 text-right text-xs text-muted-foreground">
                  {r + 1}
                </td>
                {row.map((cell, c) => (
                  <td key={c} className="min-w-[7rem] border-b border-r border-border px-2 py-1.5 align-top">
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {sheet.truncated && (
          <p className="px-3 py-2 text-xs text-muted-foreground">
            Showing the first 500 rows and 50 columns. Open in Excel to see everything.
          </p>
        )}
      </div>
      <div className="flex gap-1 overflow-x-auto border-t border-border bg-muted/40 px-2 py-1.5" role="tablist">
        {sheets.map((s, i) => (
          <button
            key={s.name}
            type="button"
            role="tab"
            aria-selected={i === active}
            onClick={() => setActive(i)}
            className={cn(
              "shrink-0 rounded px-2.5 py-1 text-xs font-medium",
              i === active ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:bg-card/60",
            )}
          >
            {s.name}
          </button>
        ))}
      </div>
    </div>
  );
}
