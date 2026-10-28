import { useCallback, useEffect, useRef, useState } from "react";
import { Download, ExternalLink, FolderOpen, Loader2, Pencil, Plus, Trash2, Upload, X } from "lucide-react";
import { FileKindIcon } from "@/components/FileKindIcon";
import { FilePickerModal } from "@/components/FilePicker";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { formatBytes } from "@/lib/format";
import type { FileKind, FilePreview } from "@/services/files";
import { FILE_UPLOAD_ACCEPT } from "@/services/files";
import {
  downloadWorkspaceFile,
  editWorkspaceFile,
  getWorkspaceFilePreview,
  listWorkspaceFiles,
  shareFilesToWorkspace,
  unshareWorkspaceFile,
  uploadWorkspaceFile,
  type Workspace,
  type WorkspaceFile,
} from "@/services/workspaces";

const EDITABLE: FileKind[] = ["csv", "txt", "md", "json", "docx"];

/**
 * The workspace's shared Files. Each file stays in its owner's Files, but every
 * member can open, download, and edit the same copy here, so changes are shared.
 */
export function WorkspaceFilesDialog({
  workspace,
  onClose,
  onChanged,
}: {
  workspace: Workspace;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const [files, setFiles] = useState<WorkspaceFile[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState(false);
  const [open, setOpen] = useState<WorkspaceFile | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setFiles(await listWorkspaceFiles(workspace.workspace_id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load files.");
    }
  }, [workspace.workspace_id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await load();
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  if (open) {
    return (
      <WorkspaceFileEditor
        workspace={workspace}
        file={open}
        onBack={() => {
          setOpen(null);
          void load();
        }}
      />
    );
  }

  return (
    <Shell title={`${workspace.name} Files`} onClose={onClose}>
      <p className="text-sm text-muted-foreground">
        Everyone in the workspace can open and edit these files. Files @Loombot creates or
        updates here, and files you attach to a message, are added automatically.
      </p>
      <div className="min-h-[8rem] space-y-1 overflow-y-auto rounded-md border border-border p-2">
        {files === null ? (
          <p className="flex items-center gap-2 p-2 text-xs text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" /> Loading files…
          </p>
        ) : files.length === 0 ? (
          <p className="p-2 text-xs text-muted-foreground">No files shared here yet.</p>
        ) : (
          files.map((file) => (
            <div key={file.file_id} className="flex items-center gap-2 rounded px-2 py-1.5 hover:bg-muted">
              <FileKindIcon kind={file.kind} className="size-4 shrink-0" />
              <button
                type="button"
                className="min-w-0 flex-1 text-left"
                onClick={() => setOpen(file)}
              >
                <span className="block truncate text-sm font-medium">{file.name}</span>
                <span className="block truncate text-[11px] text-muted-foreground">
                  {file.is_mine ? "Yours" : `From ${file.owner_name ?? "a member"}`} ·{" "}
                  {formatBytes(file.size_bytes)}
                  {file.last_edited_by === "loombot" ? " · edited by Loombot" : ""}
                </span>
              </button>
              <IconButton
                label={`Download ${file.name}`}
                onClick={() => void downloadWorkspaceFile(workspace.workspace_id, file.file_id, file.name)}
              >
                <Download className="size-3.5" />
              </IconButton>
              <IconButton
                label={`Remove ${file.name} from the workspace`}
                disabled={busy}
                onClick={() => void run(() => unshareWorkspaceFile(workspace.workspace_id, file.file_id))}
              >
                <Trash2 className="size-3.5" />
              </IconButton>
            </div>
          ))
        )}
      </div>
      {error && <p className="text-xs text-destructive">{error}</p>}
      <input
        ref={input}
        type="file"
        accept={FILE_UPLOAD_ACCEPT}
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) void run(() => uploadWorkspaceFile(workspace.workspace_id, file));
        }}
      />
      <div className="flex flex-wrap justify-end gap-2">
        <SecondaryButton disabled={busy} onClick={() => setPicking(true)}>
          <Plus className="mr-1 size-3.5" /> Share from my Files
        </SecondaryButton>
        <PrimaryButton disabled={busy} onClick={() => input.current?.click()}>
          <Upload className="mr-1 size-3.5" /> Upload
        </PrimaryButton>
      </div>
      {picking && (
        <FilePickerModal
          selected={[]}
          onClose={() => setPicking(false)}
          onConfirm={(chosen) => {
            setPicking(false);
            if (chosen.length) {
              void run(() =>
                shareFilesToWorkspace(
                  workspace.workspace_id,
                  chosen.map((f) => f.file_id),
                ),
              );
            }
          }}
        />
      )}
    </Shell>
  );
}

function WorkspaceFileEditor({
  workspace,
  file,
  onBack,
}: {
  workspace: Workspace;
  file: WorkspaceFile;
  onBack: () => void;
}) {
  const [preview, setPreview] = useState<FilePreview | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const editable = EDITABLE.includes(file.kind);

  useEffect(() => {
    void getWorkspaceFilePreview(workspace.workspace_id, file.file_id)
      .then(setPreview)
      .catch((err: Error) => setError(err.message));
  }, [workspace.workspace_id, file.file_id]);

  const text =
    preview && "content" in preview
      ? preview.content
      : preview && preview.kind === "docx"
        ? preview.markdown ?? ""
        : "";

  async function save() {
    if (draft === null) return;
    setSaving(true);
    setError(null);
    try {
      await editWorkspaceFile(workspace.workspace_id, file.file_id, draft);
      setPreview(await getWorkspaceFilePreview(workspace.workspace_id, file.file_id));
      setDraft(null);
      setNotice("Saved. Everyone in the workspace sees this version.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Shell title={file.name} onClose={onBack} wide>
      <div className="flex flex-wrap items-center gap-2">
        <SecondaryButton onClick={onBack}>
          <FolderOpen className="mr-1 size-3.5" /> All files
        </SecondaryButton>
        {editable && draft === null && preview && (
          <SecondaryButton onClick={() => setDraft(text)}>
            <Pencil className="mr-1 size-3.5" /> Edit
          </SecondaryButton>
        )}
        {file.kind === "pdf" && (
          <SecondaryButton
            onClick={() => void downloadWorkspaceFile(workspace.workspace_id, file.file_id, file.name, true)}
          >
            <ExternalLink className="mr-1 size-3.5" /> Open
          </SecondaryButton>
        )}
        <SecondaryButton
          onClick={() => void downloadWorkspaceFile(workspace.workspace_id, file.file_id, file.name)}
        >
          <Download className="mr-1 size-3.5" /> Download
        </SecondaryButton>
      </div>
      {file.kind === "docx" && draft !== null && (
        <p className="text-xs text-muted-foreground">
          Word documents are edited as Markdown: headings, lists, tables, bold, italic and links
          are kept.
        </p>
      )}
      <div className="min-h-0 flex-1 overflow-auto rounded-md border border-border">
        {error ? (
          <p className="p-3 text-xs text-destructive">{error}</p>
        ) : preview === null ? (
          <p className="flex items-center gap-2 p-3 text-xs text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" /> Loading…
          </p>
        ) : draft !== null ? (
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            aria-label={`Edit ${file.name}`}
            className="h-[50dvh] w-full resize-none bg-background p-3 font-mono text-xs"
          />
        ) : preview.kind === "xlsx" ? (
          preview.sheets.map((sheet) => (
            <div key={sheet.name} className="p-2">
              <p className="mb-1 text-xs font-medium">{sheet.name}</p>
              <table className="text-xs">
                <tbody>
                  {sheet.rows.slice(0, 200).map((row, r) => (
                    <tr key={r}>
                      {row.map((cell, c) => (
                        <td key={c} className="border border-border px-2 py-0.5">
                          {cell}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))
        ) : preview.kind === "pdf" ? (
          <p className="p-3 text-xs text-muted-foreground">
            PDF, {preview.pages} page{preview.pages === 1 ? "" : "s"}. Use Open to view it.
          </p>
        ) : (
          <pre className="whitespace-pre-wrap p-3 text-xs">{text}</pre>
        )}
      </div>
      {notice && <p className="text-xs text-foreground">{notice}</p>}
      {draft !== null && (
        <div className="flex justify-end gap-2">
          <SecondaryButton disabled={saving} onClick={() => setDraft(null)}>
            Cancel
          </SecondaryButton>
          <PrimaryButton disabled={saving} onClick={() => void save()}>
            {saving ? "Saving…" : "Save for everyone"}
          </PrimaryButton>
        </div>
      )}
    </Shell>
  );
}

function Shell({
  title,
  onClose,
  wide = false,
  children,
}: {
  title: string;
  onClose: () => void;
  wide?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/30 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={onClose}
    >
      <div
        className={`flex max-h-[85dvh] w-full flex-col gap-3 rounded-xl bg-background p-5 shadow-xl ${wide ? "max-w-3xl" : "max-w-lg"}`}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-2">
          <h3 className="truncate font-semibold">{title}</h3>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function IconButton({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className="shrink-0 rounded p-1 text-muted-foreground hover:bg-background hover:text-foreground disabled:opacity-50"
    >
      {children}
    </button>
  );
}
