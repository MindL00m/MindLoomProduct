import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  ChevronRight,
  Download,
  ExternalLink,
  FileText,
  Folder,
  HardDrive,
  Image as ImageIcon,
  Loader2,
  Mail,
  Paperclip,
  Save,
  Search,
} from "lucide-react";
import {
  downloadDriveFile,
  downloadEmailAttachment,
  getEmailAttachment,
  importDriveFile,
  importEmailAttachment,
  listDrive,
  listEmailAttachments,
  previewDriveFile,
  saveBlob,
  type DriveItem,
  type EmailAttachmentDetail,
  type EmailAttachmentItem,
  type SourcePreview,
} from "@/services/sources";
import type { FileKind } from "@/services/files";
import { FileKindIcon } from "@/components/FileKindIcon";
import { BlobFrame, DocPreview, WorkbookPreview } from "@/pages/FileViewers";
import { formatBytes, timeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";

export type FileSource = "mine" | "drive" | "email";

/** Switch between your files, Google Drive, and files from email. */
export function SourceTabs({ source, onChange }: { source: FileSource; onChange: (s: FileSource) => void }) {
  const tabs: { id: FileSource; label: string; icon: ReactNode }[] = [
    { id: "mine", label: "My files", icon: <FileText className="size-3.5" /> },
    { id: "drive", label: "Drive", icon: <HardDrive className="size-3.5" /> },
    { id: "email", label: "Email", icon: <Paperclip className="size-3.5" /> },
  ];
  return (
    <div className="flex rounded-md border border-border p-0.5 text-xs" role="tablist" aria-label="File source">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={source === tab.id}
          onClick={() => onChange(tab.id)}
          className={cn(
            "flex flex-1 items-center justify-center gap-1 rounded px-2 py-1.5 font-medium",
            source === tab.id ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {tab.icon}
          {tab.label}
        </button>
      ))}
    </div>
  );
}

function iconKind(kind: string): FileKind {
  return kind === "docx" || kind === "xlsx" || kind === "pdf" || kind === "csv" ? kind : "txt";
}

function SearchBox({ value, onChange, onSubmit, placeholder }: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  placeholder: string;
}) {
  return (
    <form
      className="flex items-center gap-2 rounded-md border border-border px-2.5 py-1.5"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <Search className="size-4 shrink-0 text-muted-foreground" />
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        className="min-w-0 flex-1 bg-transparent text-sm outline-none"
      />
    </form>
  );
}

function Notice({ children }: { children: ReactNode }) {
  return <p className="m-3 rounded-md border border-border bg-muted/40 px-3 py-2 text-sm text-muted-foreground">{children}</p>;
}

function PreviewBody({ preview, load, loadKey, title }: {
  preview: SourcePreview;
  load: () => Promise<Blob>;
  loadKey: string;
  title: string;
}) {
  if (preview.kind === "docx") return <DocPreview blocks={preview.blocks} />;
  if (preview.kind === "xlsx") return <WorkbookPreview sheets={preview.sheets} />;
  if (preview.kind === "pdf") return <BlobFrame load={load} loadKey={loadKey} type="pdf" title={title} />;
  if (preview.kind === "image") return <BlobFrame load={load} loadKey={loadKey} type="image" title={title} />;
  if (preview.kind === "text" || preview.kind === "txt" || preview.kind === "csv") {
    return (
      <pre className="m-4 min-h-0 flex-1 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-background p-3 font-mono text-xs leading-relaxed">
        {preview.content || "This file has no readable text."}
      </pre>
    );
  }
  return <Notice>This file type cannot be previewed here. Download it or open it in Drive.</Notice>;
}

function SavedNote({ name, onOpen }: { name: string; onOpen: () => void }) {
  return (
    <span className="text-xs text-foreground">
      Saved as {name}.{" "}
      <button type="button" onClick={onOpen} className="font-medium text-primary hover:underline">
        Open in My files
      </button>
    </span>
  );
}

// --- Google Drive ------------------------------------------------------------------------

export function DriveBrowser({
  sourceTabs,
  onOpenInFiles,
}: {
  sourceTabs: ReactNode;
  onOpenInFiles: (fileId: string) => void;
}) {
  const [mode, setMode] = useState<"recent" | "browse">("recent");
  const [path, setPath] = useState<{ id: string; name: string }[]>([{ id: "root", name: "My Drive" }]);
  const [search, setSearch] = useState("");
  const [activeSearch, setActiveSearch] = useState("");
  const [items, setItems] = useState<DriveItem[]>([]);
  const [nextPage, setNextPage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<DriveItem | null>(null);
  const folderId = mode === "browse" && !activeSearch ? path[path.length - 1].id : undefined;

  const load = useCallback(
    async (pageToken?: string) => {
      setLoading(true);
      setError("");
      try {
        const page = await listDrive({ folderId, search: activeSearch, pageToken });
        setItems((rows) => (pageToken ? [...rows, ...page.files] : page.files));
        setNextPage(page.next_page_token ?? null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not load your Google Drive.");
        if (!pageToken) setItems([]);
      } finally {
        setLoading(false);
      }
    },
    [folderId, activeSearch],
  );

  useEffect(() => {
    void load();
  }, [load]);

  function open(item: DriveItem) {
    if (item.kind === "folder") {
      setMode("browse");
      setActiveSearch("");
      setSearch("");
      setPath((rows) => [...(mode === "browse" && !activeSearch ? rows : [{ id: "root", name: "My Drive" }]), { id: item.id, name: item.name }]);
      return;
    }
    setSelected(item);
  }

  return (
    <div className="flex h-full w-full gap-4">
      <aside className="flex w-72 shrink-0 flex-col gap-2 rounded-lg border border-border bg-card p-2 max-md:hidden">
        {sourceTabs}
        <SearchBox value={search} onChange={setSearch} onSubmit={() => setActiveSearch(search.trim())} placeholder="Search Drive" />
        <div className="flex gap-1 text-xs">
          {(["recent", "browse"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => {
                setMode(m);
                setActiveSearch("");
                setSearch("");
              }}
              className={cn(
                "rounded px-2 py-1 font-medium",
                mode === m && !activeSearch ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {m === "recent" ? "Recent" : "My Drive"}
            </button>
          ))}
          {activeSearch && <span className="self-center text-muted-foreground">Results for “{activeSearch}”</span>}
        </div>
        {mode === "browse" && !activeSearch && path.length > 1 && (
          <nav className="flex flex-wrap items-center gap-0.5 px-1 text-xs text-muted-foreground" aria-label="Folder path">
            {path.map((crumb, i) => (
              <span key={crumb.id} className="flex items-center gap-0.5">
                {i > 0 && <ChevronRight className="size-3" />}
                <button
                  type="button"
                  onClick={() => setPath((rows) => rows.slice(0, i + 1))}
                  className={cn("hover:underline", i === path.length - 1 && "font-medium text-foreground")}
                >
                  {crumb.name}
                </button>
              </span>
            ))}
          </nav>
        )}
        <ul className="min-h-0 flex-1 space-y-0.5 overflow-y-auto">
          {items.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => open(item)}
                aria-current={selected?.id === item.id ? "true" : undefined}
                className={cn(
                  "flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-sm",
                  selected?.id === item.id ? "bg-secondary" : "hover:bg-muted/60",
                )}
              >
                {item.kind === "folder" ? (
                  <Folder className="mt-0.5 size-4 shrink-0 text-amber-600" />
                ) : item.kind === "image" ? (
                  <ImageIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                ) : (
                  <FileKindIcon kind={iconKind(item.kind)} className="mt-0.5" />
                )}
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{item.name}</span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {item.type_label}
                    {item.modified_at ? ` · ${timeAgo(item.modified_at)}` : ""}
                  </span>
                </span>
              </button>
            </li>
          ))}
          {loading && (
            <li className="flex items-center justify-center gap-2 py-3 text-xs text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" /> Loading…
            </li>
          )}
          {!loading && !error && items.length === 0 && (
            <li className="px-2 py-3 text-center text-xs text-muted-foreground">Nothing here.</li>
          )}
          {nextPage && !loading && (
            <li>
              <button type="button" onClick={() => void load(nextPage)} className="w-full py-2 text-xs font-medium text-primary hover:underline">
                Load more
              </button>
            </li>
          )}
        </ul>
        <p className="px-1 text-[11px] text-muted-foreground">
          A live view of your Google Drive. Only folders approved in Apps become company knowledge.
        </p>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col rounded-lg border border-border bg-card">
        <div className="space-y-2 border-b border-border p-2 md:hidden">
          {sourceTabs}
          <select
            className="w-full rounded-md border border-border bg-background p-2 text-sm"
            value={selected?.id ?? ""}
            onChange={(e) => {
              const item = items.find((row) => row.id === e.target.value);
              if (item) open(item);
            }}
            aria-label="Drive file"
          >
            <option value="">{items.length ? "Choose a Drive file…" : "No files"}</option>
            {items.map((item) => (
              <option key={item.id} value={item.id}>{item.kind === "folder" ? `Folder: ${item.name}` : item.name}</option>
            ))}
          </select>
        </div>
        {error ? (
          <Notice>
            {error}{" "}
            <a href="/dashboard?tab=apps" className="font-medium text-primary hover:underline">Open Apps</a>
          </Notice>
        ) : selected ? (
          <DriveFileView key={selected.id} item={selected} onOpenInFiles={onOpenInFiles} />
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center p-6 text-center text-sm text-muted-foreground">
            <HardDrive className="mb-2 size-8 text-brand-700" />
            Pick a Google Drive file to preview it, download it, or save a copy to your Files.
          </div>
        )}
      </section>
    </div>
  );
}

function DriveFileView({ item, onOpenInFiles }: { item: DriveItem; onOpenInFiles: (fileId: string) => void }) {
  const [preview, setPreview] = useState<SourcePreview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<"" | "save" | "download">("");
  const [saved, setSaved] = useState<{ id: string; name: string } | null>(null);

  useEffect(() => {
    if (!item.can_preview) return;
    let cancelled = false;
    void previewDriveFile(item.id)
      .then((value) => !cancelled && setPreview(value))
      .catch((err: Error) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [item.id, item.can_preview]);

  async function run(kind: "save" | "download", action: () => Promise<void>) {
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

  const downloadName = preview && "item" in preview && preview.item?.download_name ? preview.item.download_name : item.name;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
        <FileKindIcon kind={iconKind(item.kind)} />
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">{item.name}</p>
          <p className="text-xs text-muted-foreground">
            {item.type_label} in Google Drive
            {item.owner ? ` · ${item.owner}` : ""}
            {item.size_bytes ? ` · ${formatBytes(item.size_bytes)}` : ""}
            {item.modified_at ? ` · Updated ${timeAgo(item.modified_at)}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {saved && <SavedNote name={saved.name} onOpen={() => onOpenInFiles(saved.id)} />}
          {item.can_import && !saved && (
            <button
              type="button"
              disabled={busy !== ""}
              onClick={() => void run("save", async () => {
                const file = await importDriveFile(item.id);
                setSaved({ id: file.file_id, name: file.name });
              })}
              className="flex items-center gap-1.5 rounded-md border border-primary/40 bg-brand-50 px-2.5 py-1.5 text-xs font-medium text-brand-700 hover:bg-brand-100 disabled:opacity-50"
            >
              {busy === "save" ? <Loader2 className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}
              Save to my Files
            </button>
          )}
          <button
            type="button"
            disabled={busy !== ""}
            onClick={() => void run("download", async () => saveBlob(await downloadDriveFile(item.id), downloadName))}
            className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted/60 disabled:opacity-50"
          >
            {busy === "download" ? <Loader2 className="size-3.5 animate-spin" /> : <Download className="size-3.5" />}
            Download
          </button>
          {item.web_view_link && (
            <a
              href={item.web_view_link}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted/60"
            >
              <ExternalLink className="size-3.5" /> Open in Drive
            </a>
          )}
        </div>
      </header>
      {error && <p className="mx-4 mt-3 rounded-md border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>}
      {!item.can_preview ? (
        <Notice>This file type cannot be previewed here. Download it or open it in Drive.</Notice>
      ) : preview ? (
        <PreviewBody preview={preview} load={() => downloadDriveFile(item.id)} loadKey={item.id} title={item.name} />
      ) : !error ? (
        <p className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading preview…
        </p>
      ) : null}
    </div>
  );
}

// --- Files from email -----------------------------------------------------------------------

export function EmailFilesBrowser({
  sourceTabs,
  onOpenInFiles,
}: {
  sourceTabs: ReactNode;
  onOpenInFiles: (fileId: string) => void;
}) {
  const [search, setSearch] = useState("");
  const [activeSearch, setActiveSearch] = useState("");
  const [items, setItems] = useState<EmailAttachmentItem[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void listEmailAttachments(activeSearch)
      .then((result) => {
        if (cancelled) return;
        setItems(result.attachments);
        setNote(result.note ?? null);
        setError("");
      })
      .catch((err: Error) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [activeSearch]);

  return (
    <div className="flex h-full w-full gap-4">
      <aside className="flex w-72 shrink-0 flex-col gap-2 rounded-lg border border-border bg-card p-2 max-md:hidden">
        {sourceTabs}
        <SearchBox value={search} onChange={setSearch} onSubmit={() => setActiveSearch(search.trim())} placeholder="Search file, sender, or subject" />
        <ul className="min-h-0 flex-1 space-y-0.5 overflow-y-auto">
          {items.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => setSelected(item.id)}
                aria-current={selected === item.id ? "true" : undefined}
                className={cn(
                  "flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-sm",
                  selected === item.id ? "bg-secondary" : "hover:bg-muted/60",
                )}
              >
                <FileKindIcon kind={iconKind(item.file_kind ?? item.kind)} className="mt-0.5" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{item.filename}</span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {item.from || "Unknown sender"}
                    {item.sent_at ? ` · ${timeAgo(item.sent_at)}` : ""}
                  </span>
                  {item.subject && <span className="block truncate text-[11px] text-muted-foreground">{item.subject}</span>}
                </span>
              </button>
            </li>
          ))}
          {loading && (
            <li className="flex items-center justify-center gap-2 py-3 text-xs text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" /> Loading…
            </li>
          )}
          {!loading && !error && items.length === 0 && (
            <li className="px-2 py-3 text-center text-xs text-muted-foreground">
              {note ?? "No attachments read from your email yet. They appear here after the next mail sync."}
            </li>
          )}
        </ul>
        <p className="px-1 text-[11px] text-muted-foreground">
          Files MindLoom read from your own Gmail and Outlook. Only you see your mailbox’s files.
        </p>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col rounded-lg border border-border bg-card">
        <div className="space-y-2 border-b border-border p-2 md:hidden">
          {sourceTabs}
          <select
            className="w-full rounded-md border border-border bg-background p-2 text-sm"
            value={selected ?? ""}
            onChange={(e) => setSelected(e.target.value || null)}
            aria-label="Email attachment"
          >
            <option value="">{items.length ? "Choose an attachment…" : "No attachments"}</option>
            {items.map((item) => (
              <option key={item.id} value={item.id}>{item.filename}</option>
            ))}
          </select>
        </div>
        {error ? (
          <Notice>{error}</Notice>
        ) : selected ? (
          <EmailAttachmentView key={selected} id={selected} onOpenInFiles={onOpenInFiles} />
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center p-6 text-center text-sm text-muted-foreground">
            <Mail className="mb-2 size-8 text-brand-700" />
            Pick a file from your email to read it, download the original, or save it to your Files.
          </div>
        )}
      </section>
    </div>
  );
}

function EmailAttachmentView({ id, onOpenInFiles }: { id: string; onOpenInFiles: (fileId: string) => void }) {
  const [detail, setDetail] = useState<EmailAttachmentDetail | null>(null);
  const [view, setView] = useState<"text" | "original">("text");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<"" | "save" | "download">("");
  const [saved, setSaved] = useState<{ id: string; name: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    void getEmailAttachment(id)
      .then((value) => !cancelled && setDetail(value))
      .catch((err: Error) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [id]);

  async function run(kind: "save" | "download", action: () => Promise<void>) {
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

  if (!detail) {
    return error ? (
      <Notice>{error}</Notice>
    ) : (
      <p className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> Opening…
      </p>
    );
  }
  const originalViewable = detail.kind === "pdf" || detail.kind === "image";
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
        <FileKindIcon kind={iconKind(detail.file_kind ?? detail.kind)} />
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">{detail.filename}</p>
          <p className="text-xs text-muted-foreground">
            From {detail.from || "unknown sender"}
            {detail.subject ? ` · “${detail.subject}”` : ""}
            {detail.sent_at ? ` · ${new Date(detail.sent_at).toLocaleDateString()}` : ""}
            {` · ${detail.provider === "outlook" ? "Outlook" : "Gmail"}`}
            {detail.read_with === "ocr" ? " · read from a scan" : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {saved && <SavedNote name={saved.name} onOpen={() => onOpenInFiles(saved.id)} />}
          {!saved && (
            <button
              type="button"
              disabled={busy !== ""}
              title={detail.file_kind ? "Save the original file" : "Files cannot store this type, so its text is saved"}
              onClick={() => void run("save", async () => {
                const file = await importEmailAttachment(detail.id);
                setSaved({ id: file.file_id, name: file.name });
              })}
              className="flex items-center gap-1.5 rounded-md border border-primary/40 bg-brand-50 px-2.5 py-1.5 text-xs font-medium text-brand-700 hover:bg-brand-100 disabled:opacity-50"
            >
              {busy === "save" ? <Loader2 className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}
              {detail.file_kind ? "Save to my Files" : "Save text to my Files"}
            </button>
          )}
          <button
            type="button"
            disabled={busy !== ""}
            onClick={() => void run("download", async () => saveBlob(await downloadEmailAttachment(detail.id), detail.filename))}
            className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted/60 disabled:opacity-50"
          >
            {busy === "download" ? <Loader2 className="size-3.5 animate-spin" /> : <Download className="size-3.5" />}
            Download original
          </button>
          {detail.email_url && (
            <a
              href={detail.email_url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted/60"
            >
              <ExternalLink className="size-3.5" /> Open email
            </a>
          )}
        </div>
      </header>
      {error && <p className="mx-4 mt-3 rounded-md border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>}
      {originalViewable && (
        <div className="mx-4 mt-3 flex w-fit rounded-md border border-border p-0.5 text-xs" role="tablist">
          {(["text", "original"] as const).map((v) => (
            <button
              key={v}
              type="button"
              role="tab"
              aria-selected={view === v}
              onClick={() => setView(v)}
              className={cn("rounded px-2 py-1 font-medium", view === v ? "bg-secondary" : "text-muted-foreground")}
            >
              {v === "text" ? "Text MindLoom read" : "Original"}
            </button>
          ))}
        </div>
      )}
      {view === "original" && originalViewable ? (
        <BlobFrame
          load={() => downloadEmailAttachment(detail.id)}
          loadKey={detail.id}
          type={detail.kind === "pdf" ? "pdf" : "image"}
          title={detail.filename}
        />
      ) : (
        <pre className="m-4 min-h-0 flex-1 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-background p-3 font-mono text-xs leading-relaxed">
          {detail.text || "MindLoom found no readable text in this file."}
          {detail.text_truncated ? "\n\n[… truncated …]" : ""}
        </pre>
      )}
    </div>
  );
}
