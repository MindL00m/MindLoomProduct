import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2, Search, Upload, X } from "lucide-react";
import { FILE_UPLOAD_ACCEPT, kindFromName, listFiles, uploadFile, type LoomFileMeta } from "@/services/files";
import { FileKindIcon } from "@/components/FileKindIcon";
import { formatBytes } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Pick files from the user's Files tab (or upload new ones) to attach to an email. */
export function FilePickerModal({
  selected,
  onClose,
  onConfirm,
}: {
  selected: string[];
  onClose: () => void;
  onConfirm: (files: LoomFileMeta[]) => void;
}) {
  const [files, setFiles] = useState<LoomFileMeta[] | null>(null);
  const [chosen, setChosen] = useState<string[]>(selected);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    void listFiles()
      .then(setFiles)
      .catch((err: Error) => setError(err.message));
  }, []);

  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return (files ?? []).filter((f) => !needle || f.name.toLowerCase().includes(needle));
  }, [files, filter]);

  async function upload(list: FileList | null) {
    const file = list?.[0];
    if (!file) return;
    if (!kindFromName(file.name)) {
      setError("Attach a .csv, .txt, .md, .json, .docx, .xlsx, .pdf, or .pptx file.");
      return;
    }
    setUploading(true);
    setError("");
    try {
      const saved = await uploadFile(file);
      setFiles((rows) => [saved, ...(rows ?? [])]);
      setChosen((ids) => [...ids, saved.file_id]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/30 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Attach files"
      onClick={onClose}
    >
      <div
        className="flex max-h-[85dvh] w-full max-w-md flex-col gap-3 rounded-xl bg-background p-5 shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-2">
          <div>
            <h3 className="font-semibold">Attach files</h3>
            <p className="text-sm text-muted-foreground">Choose from your Files, or upload a new one.</p>
          </div>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        </div>
        <label className="flex items-center gap-2 rounded-md border border-border px-2.5 py-1.5">
          <Search className="size-4 text-muted-foreground" />
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Search your files"
            aria-label="Search your files"
            className="min-w-0 flex-1 bg-transparent text-sm outline-none"
          />
        </label>
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto rounded-md border border-border p-1">
          {files === null && !error ? (
            <p className="flex items-center gap-2 p-2 text-xs text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" /> Loading files…
            </p>
          ) : visible.length === 0 ? (
            <p className="p-2 text-xs text-muted-foreground">No files match.</p>
          ) : (
            visible.map((file) => {
              const checked = chosen.includes(file.file_id);
              return (
                <label
                  key={file.file_id}
                  className={cn(
                    "flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-muted",
                    checked && "bg-secondary",
                  )}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() =>
                      setChosen((ids) =>
                        checked ? ids.filter((id) => id !== file.file_id) : [...ids, file.file_id],
                      )
                    }
                  />
                  <FileKindIcon kind={file.kind} />
                  <span className="min-w-0 flex-1 truncate">{file.name}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">{formatBytes(file.size_bytes)}</span>
                </label>
              );
            })
          )}
        </div>
        {error && <p className="text-xs text-destructive">{error}</p>}
        <input
          ref={input}
          type="file"
          accept={FILE_UPLOAD_ACCEPT}
          className="sr-only"
          onChange={(event) => {
            void upload(event.target.files);
            event.target.value = "";
          }}
        />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <button
            type="button"
            disabled={uploading}
            onClick={() => input.current?.click()}
            className="flex items-center gap-1.5 rounded-md border border-border px-3 py-2 text-xs font-medium hover:bg-muted/60 disabled:opacity-50"
          >
            {uploading ? <Loader2 className="size-3.5 animate-spin" /> : <Upload className="size-3.5" />}
            Upload from computer
          </button>
          <button
            type="button"
            onClick={() => onConfirm((files ?? []).filter((f) => chosen.includes(f.file_id)))}
            className="rounded-md bg-primary px-3 py-2 text-xs font-semibold text-primary-foreground"
          >
            {chosen.length ? `Attach ${chosen.length} file${chosen.length === 1 ? "" : "s"}` : "Attach none"}
          </button>
        </div>
      </div>
    </div>
  );
}
