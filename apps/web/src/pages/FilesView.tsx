import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Bot,
  Download,
  FilePlus2,
  FileSpreadsheet,
  Loader2,
  Plus,
  Radar,
  Save,
  Trash2,
  Undo2,
  Upload,
  X,
} from "lucide-react";
import { parseCsvMatrix, toCsv } from "@/lib/csv";
import {
  FILE_UPLOAD_ACCEPT,
  createFile,
  deleteFile,
  getFile,
  kindFromName,
  listFileEvents,
  listFiles,
  setFileMonitoring,
  undoFile,
  updateFile,
  uploadFile,
  type LoomFile,
  type LoomFileEvent,
  type LoomFileMeta,
} from "@/services/files";
import { DocumentFileView, ExcelButton } from "@/pages/FileViewers";
import { cn } from "@/lib/utils";
import { plural, timeAgo } from "@/lib/format";
import { FileKindIcon, fileDetail } from "@/components/FileKindIcon";

const PAGE_SIZE = 200;



export default function FilesView() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedId = searchParams.get("file");
  const [files, setFiles] = useState<LoomFileMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [active, setActive] = useState<LoomFile | null>(null);
  const [opening, setOpening] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [dirty, setDirty] = useState(false);

  const refreshList = useCallback(async () => {
    try {
      setFiles(await listFiles());
      setListError("");
    } catch (err) {
      setListError(err instanceof Error ? err.message : "Could not load files.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  const open = useCallback(
    async (fileId: string) => {
      setOpening(true);
      try {
        setActive(await getFile(fileId));
        setListError("");
      } catch (err) {
        setActive(null);
        setListError(err instanceof Error ? err.message : "Could not open file.");
      } finally {
        setOpening(false);
      }
    },
    [],
  );

  // Open the file named in the URL (Ask links here), else the most recent one.
  useEffect(() => {
    if (loading) return;
    const target = requestedId ?? (active ? null : files[0]?.file_id);
    if (target && target !== active?.file_id) void open(target);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, requestedId, files]);

  function select(fileId: string, dirty: boolean) {
    if (dirty && !window.confirm("Discard unsaved changes to this file?")) return;
    setShowNew(false);
    const next = new URLSearchParams(searchParams);
    next.set("file", fileId);
    setSearchParams(next, { replace: true });
  }

  function onSaved(file: LoomFile) {
    setActive(file);
    void refreshList();
  }

  function onDeleted(fileId: string) {
    setFiles((current) => current.filter((f) => f.file_id !== fileId));
    setActive(null);
    const next = new URLSearchParams(searchParams);
    next.delete("file");
    setSearchParams(next, { replace: true });
    void refreshList();
  }

  return (
    <div className="flex h-full w-full gap-4">
      <aside className="flex w-64 shrink-0 flex-col rounded-lg border border-border bg-card max-md:hidden">
        <div className="p-2">
          <button
            type="button"
            onClick={() => setShowNew(true)}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
          >
            <FilePlus2 className="size-4" /> New CSV file
          </button>
        </div>
        <FileList
          files={files}
          loading={loading}
          activeId={active?.file_id ?? null}
          onSelect={(id) => select(id, dirty)}
        />
      </aside>

      <section className="flex min-w-0 flex-1 flex-col rounded-lg border border-border bg-card">
        <div className="flex items-center gap-2 border-b border-border p-2 md:hidden">
          <select
            className="min-w-0 flex-1 rounded-md border border-border bg-background p-2 text-sm"
            value={active?.file_id ?? ""}
            onChange={(e) => e.target.value && select(e.target.value, dirty)}
          >
            <option value="">{files.length ? "Choose a file…" : "No files yet"}</option>
            {files.map((f) => (
              <option key={f.file_id} value={f.file_id}>
                {f.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => setShowNew(true)}
            className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-xs font-medium text-primary-foreground"
          >
            <Plus className="size-3.5" /> New
          </button>
        </div>

        {listError && (
          <p className="m-3 rounded-md border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {listError}
          </p>
        )}

        {showNew ? (
          <NewFileForm
            existingNames={files.map((f) => f.name.toLowerCase())}
            onCancel={() => setShowNew(false)}
            onCreated={(file) => {
              setShowNew(false);
              const next = new URLSearchParams(searchParams);
              next.set("file", file.file_id);
              setSearchParams(next, { replace: true });
              onSaved(file);
            }}
          />
        ) : opening && !active ? (
          <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
            <Loader2 className="mr-2 size-4 animate-spin" /> Opening file…
          </div>
        ) : active && active.kind !== "csv" ? (
          <DocumentFileView
            key={`${active.file_id}:${active.updated_at}`}
            file={active}
            onSaved={onSaved}
            onDeleted={onDeleted}
            onDirtyChange={setDirty}
            onCreated={(file) => {
              const next = new URLSearchParams(searchParams);
              next.set("file", file.file_id);
              setSearchParams(next, { replace: true });
              void refreshList();
            }}
          />
        ) : active ? (
          <FileEditor
            key={`${active.file_id}:${active.updated_at}`}
            file={active}
            onSaved={onSaved}
            onDeleted={onDeleted}
            onDirtyChange={setDirty}
          />
        ) : (
          <EmptyState loading={loading} onNew={() => setShowNew(true)} />
        )}
      </section>
    </div>
  );
}

function FileList({
  files,
  loading,
  activeId,
  onSelect,
}: {
  files: LoomFileMeta[];
  loading: boolean;
  activeId: string | null;
  onSelect: (id: string) => void;
}) {
  if (loading) {
    return (
      <p className="flex items-center justify-center gap-2 px-2 py-4 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" /> Loading…
      </p>
    );
  }
  if (files.length === 0) {
    return <p className="px-2 py-4 text-center text-xs text-muted-foreground">No files yet.</p>;
  }
  return (
    <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto px-2 pb-2">
      {files.map((f) => (
        <li key={f.file_id}>
          <button
            type="button"
            onClick={() => onSelect(f.file_id)}
            aria-current={f.file_id === activeId ? "true" : undefined}
            className={cn(
              "flex w-full items-start gap-2 rounded-md px-2 py-2 text-left text-sm transition-colors",
              f.file_id === activeId
                ? "bg-secondary text-foreground"
                : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
            )}
          >
            <FileKindIcon kind={f.kind} className="mt-0.5" />
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium">{f.name}</span>
              <span className="flex items-center gap-1 truncate text-xs text-muted-foreground">
                {fileDetail(f)} · {timeAgo(f.updated_at)}
                {f.last_edited_by === "loombot" && (
                  <Bot className="size-3 shrink-0" aria-label="Last edited by LoomBot" />
                )}
                {f.monitor_enabled && (
                  <Radar className="size-3 shrink-0 text-primary" aria-label="Updates automatically" />
                )}
              </span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function EmptyState({ loading, onNew }: { loading: boolean; onNew: () => void }) {
  if (loading) return <div className="flex-1" />;
  return (
    <div className="flex flex-1 flex-col items-center justify-center p-6 text-center">
      <span className="flex size-12 items-center justify-center rounded-full bg-brand-50 text-brand-700">
        <FileSpreadsheet className="size-6" />
      </span>
      <h2 className="mt-4 text-xl font-semibold tracking-tight">Your CSV files</h2>
      <p className="mt-1 max-w-md text-sm text-muted-foreground">
        Create a CSV here or import one from your computer. You can also ask LoomBot in
        Ask to create a file or change one, for example “add a Q3 column to budget.csv”.
      </p>
      <button
        type="button"
        onClick={onNew}
        className="mt-5 flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
      >
        <FilePlus2 className="size-4" /> New CSV file
      </button>
    </div>
  );
}

function NewFileForm({
  existingNames,
  onCancel,
  onCreated,
}: {
  existingNames: string[];
  onCancel: () => void;
  onCreated: (file: LoomFile) => void;
}) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"csv" | "txt">("csv");
  const [headers, setHeaders] = useState("Name, Value");
  const [imported, setImported] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [monitor, setMonitor] = useState(false);
  const [instructions, setInstructions] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  const importedKind = imported ? kindFromName(imported.name) : null;
  const finalKind = importedKind ?? kind;
  const finalName = (() => {
    const trimmed = name.trim();
    if (!trimmed) return "";
    return trimmed.toLowerCase().endsWith(`.${finalKind}`) ? trimmed : `${trimmed}.${finalKind}`;
  })();
  const duplicate = finalName !== "" && existingNames.includes(finalName.toLowerCase());
  const canMonitor = finalKind === "csv";

  function pick(list: FileList | null) {
    const file = list?.[0];
    if (!file) return;
    const picked = kindFromName(file.name);
    if (!picked) {
      setError("Choose a .csv, .txt, .docx, .xlsx, or .pdf file.");
      return;
    }
    const limit = picked === "csv" || picked === "txt" ? 1_000_000 : 15_000_000;
    if (file.size > limit) {
      setError(picked === "csv" || picked === "txt" ? "CSV and text files are limited to 1 MB." : "Files are limited to 15 MB.");
      return;
    }
    setImported(file);
    if (!name.trim()) setName(file.name);
    setError("");
  }

  async function submit() {
    if (!finalName || duplicate) return;
    setBusy(true);
    setError("");
    try {
      let created: LoomFile;
      if (imported) {
        created = await uploadFile(imported, finalName);
      } else if (kind === "txt") {
        created = await createFile(finalName, "", "txt");
      } else {
        const columns = headers
          .split(",")
          .map((h) => h.trim())
          .filter(Boolean);
        created = await createFile(finalName, columns.length ? toCsv([columns]) : "");
      }
      if (canMonitor && monitor && instructions.trim()) {
        created = await setFileMonitoring(created.file_id, true, instructions.trim());
      }
      onCreated(created);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create file.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="mx-auto w-full max-w-lg space-y-4 overflow-y-auto p-6"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">New file</h2>
        <button
          type="button"
          onClick={onCancel}
          aria-label="Cancel"
          className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <X className="size-4" />
        </button>
      </div>

      {imported ? (
        <div className="flex items-center justify-between rounded-md border border-border bg-muted/40 px-3 py-2 text-sm">
          <span className="flex min-w-0 items-center gap-2 truncate">
            <FileKindIcon kind={importedKind ?? "txt"} />
            Uploading <span className="truncate font-medium">{imported.name}</span>
          </span>
          <button
            type="button"
            className="ml-2 shrink-0 text-xs font-medium text-primary hover:underline"
            onClick={() => setImported(null)}
          >
            Remove
          </button>
        </div>
      ) : (
        <div className="flex rounded-md border border-border p-0.5 text-sm" role="radiogroup" aria-label="File type">
          {(["csv", "txt"] as const).map((option) => (
            <button
              key={option}
              type="button"
              role="radio"
              aria-checked={kind === option}
              onClick={() => setKind(option)}
              className={cn(
                "flex-1 rounded px-3 py-1.5 font-medium",
                kind === option ? "bg-secondary text-foreground" : "text-muted-foreground",
              )}
            >
              {option === "csv" ? "Spreadsheet (CSV)" : "Text (TXT)"}
            </button>
          ))}
        </div>
      )}

      <label className="block text-sm font-medium">
        File name
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={`notes.${finalKind}`}
          maxLength={120}
          className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
        />
        {duplicate && (
          <span className="mt-1 block text-xs text-destructive">
            You already have a file named {finalName}.
          </span>
        )}
      </label>

      {!imported && kind === "csv" && (
        <label className="block text-sm font-medium">
          Column headers
          <input
            value={headers}
            onChange={(e) => setHeaders(e.target.value)}
            placeholder="Name, Owner, Due date"
            className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          />
          <span className="mt-1 block text-xs font-normal text-muted-foreground">
            Separate columns with commas. You can add more later.
          </span>
        </label>
      )}

      <input
        ref={fileInput}
        type="file"
        accept={FILE_UPLOAD_ACCEPT}
        className="sr-only"
        onChange={(e) => {
          pick(e.target.files);
          e.target.value = "";
        }}
      />

      {canMonitor && (
        <div className="space-y-2 rounded-md border border-border p-3">
          <label className="flex items-start gap-2 text-sm font-medium">
            <input
              type="checkbox"
              checked={monitor}
              onChange={(e) => setMonitor(e.target.checked)}
              className="mt-0.5"
            />
            <span>
              Keep this file updated automatically
              <span className="block text-xs font-normal text-muted-foreground">
                LoomBot adds rows when new information that matches arrives. It never deletes.
              </span>
            </span>
          </label>
          {monitor && (
            <textarea
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              rows={2}
              maxLength={1000}
              placeholder="What should be added? e.g. Add a row for each new invoice email from Stripe with date, amount, and invoice number."
              aria-label="What should be added automatically"
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            />
          )}
        </div>
      )}

      {error && <p className="text-sm text-destructive">{error}</p>}

      <div className="flex flex-wrap gap-2">
        <button
          type="submit"
          disabled={busy || !finalName || duplicate || (canMonitor && monitor && !instructions.trim())}
          className="flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          {busy ? <Loader2 className="size-4 animate-spin" /> : <FilePlus2 className="size-4" />}
          {imported ? "Upload file" : "Create file"}
        </button>
        {!imported && (
          <button
            type="button"
            onClick={() => fileInput.current?.click()}
            className="flex items-center gap-2 rounded-md border border-border px-4 py-2 text-sm font-medium hover:bg-muted/60"
          >
            <Upload className="size-4" /> Upload a file instead
          </button>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        You can upload CSV, TXT, Word (.docx), Excel (.xlsx), and PDF files.
      </p>
    </form>
  );
}


function FileEditor({
  file,
  onSaved,
  onDeleted,
  onDirtyChange,
}: {
  file: LoomFile;
  onSaved: (file: LoomFile) => void;
  onDeleted: (fileId: string) => void;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const original = useMemo(() => {
    const matrix = parseCsvMatrix(file.content);
    return matrix.length ? matrix : [["Column 1"]];
  }, [file.content]);
  const [matrix, setMatrix] = useState<string[][]>(original);
  const [mode, setMode] = useState<"table" | "raw">("table");
  const [raw, setRaw] = useState(file.content);
  const [page, setPage] = useState(0);
  const [name, setName] = useState(file.name);
  const [busy, setBusy] = useState<"" | "save" | "undo" | "delete" | "rename">("");
  const [error, setError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  const originalCsv = useMemo(() => toCsv(original), [original]);
  const currentCsv = mode === "raw" ? raw : toCsv(matrix);
  const dirty =
    mode === "raw"
      ? raw !== file.content && raw !== originalCsv
      : currentCsv !== originalCsv;

  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);

  const header = matrix[0] ?? [];
  const body = matrix.slice(1);
  const pages = Math.max(1, Math.ceil(body.length / PAGE_SIZE));
  const pageRows = body.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  function setCell(row: number, col: number, value: string) {
    setMatrix((m) => m.map((r, i) => (i === row ? r.map((c, j) => (j === col ? value : c)) : r)));
  }

  function addRow() {
    setMatrix((m) => [...m, Array<string>(header.length || 1).fill("")]);
    setPage(Math.floor(body.length / PAGE_SIZE));
  }

  function addColumn() {
    setMatrix((m) =>
      m.map((r, i) => [...r, i === 0 ? `Column ${(m[0]?.length ?? 0) + 1}` : ""]),
    );
  }

  function removeRow(row: number) {
    setMatrix((m) => m.filter((_, i) => i !== row));
  }

  function removeColumn(col: number) {
    if (header.length <= 1) return;
    setMatrix((m) => m.map((r) => r.filter((_, j) => j !== col)));
  }

  function switchMode(next: "table" | "raw") {
    if (next === mode) return;
    if (next === "raw") setRaw(toCsv(matrix));
    else {
      const parsed = parseCsvMatrix(raw);
      setMatrix(parsed.length ? parsed : [["Column 1"]]);
      setPage(0);
    }
    setMode(next);
  }

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

  const save = () =>
    run("save", async () => onSaved(await updateFile(file.file_id, { content: currentCsv })));

  const undo = () => {
    if (dirty && !window.confirm("Undo the last saved change and discard your unsaved edits?")) {
      return;
    }
    void run("undo", async () => onSaved(await undoFile(file.file_id)));
  };

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

  function download() {
    const blob = new Blob([currentCsv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = file.name;
    link.click();
    URL.revokeObjectURL(url);
  }

  const lastEdit =
    file.last_edited_by === "loombot"
      ? `Last changed by LoomBot ${timeAgo(file.updated_at)}`
      : `Updated ${timeAgo(file.updated_at)}`;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
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
          <p className="px-1 text-xs text-muted-foreground">
            {plural(Math.max(0, matrix.length - 1), "row")} · {plural(header.length, "column")} ·{" "}
            {lastEdit}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <div className="flex rounded-md border border-border p-0.5 text-xs" role="tablist">
            {(["table", "raw"] as const).map((m) => (
              <button
                key={m}
                type="button"
                role="tab"
                aria-selected={mode === m}
                onClick={() => switchMode(m)}
                className={cn(
                  "rounded px-2 py-1 font-medium",
                  mode === m ? "bg-secondary text-foreground" : "text-muted-foreground",
                )}
              >
                {m === "table" ? "Table" : "Raw CSV"}
              </button>
            ))}
          </div>
          {file.can_undo && (
            <button
              type="button"
              disabled={busy !== ""}
              onClick={undo}
              title={
                file.last_edited_by === "loombot"
                  ? "Undo LoomBot's last change"
                  : "Undo the last saved change"
              }
              className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted/60 disabled:opacity-50"
            >
              {busy === "undo" ? <Loader2 className="size-3.5 animate-spin" /> : <Undo2 className="size-3.5" />}
              Undo
            </button>
          )}
          <ExcelButton fileId={file.file_id} name={file.name} />
          <button
            type="button"
            onClick={download}
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

      <MonitoringPanel file={file} dirty={dirty} onSaved={onSaved} />

      {mode === "raw" ? (
        <textarea
          aria-label="Raw CSV"
          value={raw}
          onChange={(e) => setRaw(e.target.value)}
          spellCheck={false}
          className="m-4 min-h-0 flex-1 resize-none rounded-md border border-border bg-background p-3 font-mono text-xs leading-relaxed outline-none focus:border-primary"
        />
      ) : (
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="min-w-full border-separate border-spacing-0 text-sm">
            <thead className="sticky top-0 z-10 bg-muted">
              <tr>
                <th className="w-10 border-b border-r border-border px-2 text-right text-xs font-normal text-muted-foreground">
                  #
                </th>
                {header.map((cell, col) => (
                  <th key={col} className="group min-w-[9rem] border-b border-r border-border p-0">
                    <div className="flex items-center">
                      <input
                        aria-label={`Column ${col + 1} header`}
                        value={cell}
                        onChange={(e) => setCell(0, col, e.target.value)}
                        className="w-full bg-transparent px-2 py-1.5 font-semibold outline-none focus:bg-background"
                      />
                      {header.length > 1 && (
                        <button
                          type="button"
                          onClick={() => removeColumn(col)}
                          aria-label={`Remove column ${cell || col + 1}`}
                          className="mr-1 rounded p-0.5 text-muted-foreground opacity-0 hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100"
                        >
                          <X className="size-3" />
                        </button>
                      )}
                    </div>
                  </th>
                ))}
                <th className="border-b border-border px-1">
                  <button
                    type="button"
                    onClick={addColumn}
                    aria-label="Add column"
                    className="rounded p-1 text-muted-foreground hover:bg-background hover:text-foreground"
                  >
                    <Plus className="size-3.5" />
                  </button>
                </th>
              </tr>
            </thead>
            <tbody>
              {pageRows.map((row, i) => {
                const rowIndex = page * PAGE_SIZE + i + 1;
                return (
                  <tr key={rowIndex} className="group">
                    <td className="border-b border-r border-border px-2 text-right text-xs text-muted-foreground">
                      {rowIndex}
                    </td>
                    {row.map((cell, col) => (
                      <td key={col} className="border-b border-r border-border p-0">
                        <input
                          aria-label={`Row ${rowIndex}, ${header[col] || `column ${col + 1}`}`}
                          value={cell}
                          onChange={(e) => setCell(rowIndex, col, e.target.value)}
                          className="w-full bg-transparent px-2 py-1.5 outline-none focus:bg-brand-50"
                        />
                      </td>
                    ))}
                    <td className="border-b border-border px-1">
                      <button
                        type="button"
                        onClick={() => removeRow(rowIndex)}
                        aria-label={`Remove row ${rowIndex}`}
                        className="rounded p-1 text-muted-foreground opacity-0 hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100"
                      >
                        <X className="size-3" />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="flex flex-wrap items-center gap-3 px-3 py-2">
            <button
              type="button"
              onClick={addRow}
              className="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-primary hover:bg-muted/60"
            >
              <Plus className="size-3.5" /> Add row
            </button>
            {pages > 1 && (
              <span className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
                <button
                  type="button"
                  disabled={page === 0}
                  onClick={() => setPage((p) => p - 1)}
                  className="rounded border border-border px-2 py-0.5 disabled:opacity-40"
                >
                  Previous
                </button>
                Rows {(page * PAGE_SIZE + 1).toLocaleString()}–
                {Math.min(body.length, (page + 1) * PAGE_SIZE).toLocaleString()} of{" "}
                {body.length.toLocaleString()}
                <button
                  type="button"
                  disabled={page >= pages - 1}
                  onClick={() => setPage((p) => p + 1)}
                  className="rounded border border-border px-2 py-0.5 disabled:opacity-40"
                >
                  Next
                </button>
              </span>
            )}
          </div>
        </div>
      )}

      {dirty && (
        <footer className="flex items-center justify-end gap-2 border-t border-border bg-muted/40 px-4 py-2">
          <span className="mr-auto text-xs text-muted-foreground">Unsaved changes</span>
          <button
            type="button"
            disabled={busy !== ""}
            onClick={() => {
              setMatrix(original);
              setRaw(file.content);
            }}
            className="rounded-md border border-border bg-card px-3 py-1.5 text-xs font-medium disabled:opacity-50"
          >
            Discard
          </button>
          <button
            type="button"
            disabled={busy !== ""}
            onClick={() => void save()}
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


function MonitoringPanel({
  file,
  dirty,
  onSaved,
}: {
  file: LoomFile;
  dirty: boolean;
  onSaved: (file: LoomFile) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [instructions, setInstructions] = useState(file.monitor_instructions ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [events, setEvents] = useState<LoomFileEvent[] | null>(null);
  const [showEvents, setShowEvents] = useState(false);
  const suggested = !file.monitor_enabled && Boolean(file.monitor_instructions);

  useEffect(() => {
    if (!file.monitor_enabled && !showEvents) return;
    void listFileEvents(file.file_id)
      .then(setEvents)
      .catch(() => setEvents([]));
  }, [file.file_id, file.monitor_enabled, file.updated_at, showEvents]);

  async function apply(enabled: boolean, text?: string) {
    setBusy(true);
    setError("");
    try {
      onSaved(await setFileMonitoring(file.file_id, enabled, text));
      setEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change automatic updates.");
    } finally {
      setBusy(false);
    }
  }

  const blocked = dirty || busy;

  return (
    <div className="mx-4 mt-3 rounded-md border border-border bg-muted/30 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Radar className={cn("size-3.5", file.monitor_enabled ? "text-primary" : "text-muted-foreground")} />
        {file.monitor_enabled ? (
          <span className="min-w-0 flex-1">
            <span className="font-medium text-foreground">Updates automatically.</span>{" "}
            <span className="text-muted-foreground">{file.monitor_instructions}</span>
          </span>
        ) : suggested ? (
          <span className="min-w-0 flex-1">
            <span className="font-medium text-foreground">LoomBot suggests keeping this file updated:</span>{" "}
            <span className="text-muted-foreground">{file.monitor_instructions}</span>
          </span>
        ) : (
          <span className="min-w-0 flex-1 text-muted-foreground">
            Not updated automatically.
          </span>
        )}
        <span className="flex flex-wrap gap-1.5">
          {suggested && !editing && (
            <button
              type="button"
              disabled={blocked}
              onClick={() => void apply(true)}
              className="rounded-md bg-primary px-2.5 py-1 font-semibold text-primary-foreground disabled:opacity-50"
            >
              Turn on
            </button>
          )}
          {!editing && (
            <button
              type="button"
              disabled={blocked}
              onClick={() => {
                setInstructions(file.monitor_instructions ?? "");
                setEditing(true);
              }}
              className="rounded-md border border-border bg-card px-2.5 py-1 font-medium disabled:opacity-50"
            >
              {file.monitor_enabled ? "Edit" : suggested ? "Change" : "Keep updated…"}
            </button>
          )}
          {file.monitor_enabled && !editing && (
            <button
              type="button"
              disabled={blocked}
              onClick={() => void apply(false)}
              className="rounded-md border border-border bg-card px-2.5 py-1 font-medium disabled:opacity-50"
            >
              Turn off
            </button>
          )}
          <button
            type="button"
            onClick={() => setShowEvents((v) => !v)}
            className="rounded-md px-2 py-1 font-medium text-primary hover:underline"
          >
            {showEvents ? "Hide changes" : "Automatic changes"}
          </button>
        </span>
      </div>
      {dirty && (
        <p className="mt-1 text-muted-foreground">Save or discard your edits to change automatic updates.</p>
      )}
      {editing && (
        <div className="mt-2 space-y-2">
          <textarea
            autoFocus
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            rows={2}
            maxLength={1000}
            placeholder="What should be added? e.g. Add a row for each new lead email with name, company, and email."
            aria-label="What should be added automatically"
            className="w-full rounded-md border border-border bg-background px-2.5 py-1.5 text-sm"
          />
          <div className="flex gap-1.5">
            <button
              type="button"
              disabled={blocked || !instructions.trim()}
              onClick={() => void apply(true, instructions.trim())}
              className="rounded-md bg-primary px-2.5 py-1 font-semibold text-primary-foreground disabled:opacity-50"
            >
              {busy ? "Saving…" : "Save and keep updated"}
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="rounded-md border border-border bg-card px-2.5 py-1 font-medium"
            >
              Cancel
            </button>
          </div>
          <p className="text-muted-foreground">
            Only information that arrives from now on is applied. Rows are added or filled in,
            never deleted, and every change can be undone.
          </p>
        </div>
      )}
      {error && <p className="mt-1 text-destructive">{error}</p>}
      {showEvents && (
        <ul className="mt-2 space-y-1.5 border-t border-border pt-2">
          {events === null ? (
            <li className="text-muted-foreground">Loading…</li>
          ) : events.length === 0 ? (
            <li className="text-muted-foreground">No automatic changes yet.</li>
          ) : (
            events.map((event) => (
              <li key={event.event_id}>
                <span className="font-medium text-foreground">{timeAgo(event.created_at)}</span>
                {" · "}
                {event.summary}
                {event.source_label && (
                  <span className="text-muted-foreground">
                    {" "}
                    (from{" "}
                    {event.source_url ? (
                      <a
                        href={event.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-primary hover:underline"
                      >
                        {event.source_label}
                      </a>
                    ) : (
                      event.source_label
                    )}
                    )
                  </span>
                )}
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}
