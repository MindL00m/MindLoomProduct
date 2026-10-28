import { useRef, useState } from "react";
import { FolderUp, Loader2, Upload } from "lucide-react";
import { cn } from "@/lib/utils";

export interface UploadManyResult {
  uploaded: number;
  failed: { name: string; error: string }[];
}

const MAX_BYTES = 15_000_000;
// Operating-system clutter that a folder upload should skip.
const SKIPPED = /^(\.ds_store|thumbs\.db|desktop\.ini)$|^\._/i;

/**
 * Upload several files, or a whole folder, from the computer. Each file is sent
 * one at a time through ``upload``; hidden system files and files over 15 MB are
 * skipped and reported back.
 */
export function UploadManyButton({
  upload,
  onDone,
  accept,
  className,
  compact = false,
}: {
  upload: (file: File) => Promise<unknown>;
  onDone: (result: UploadManyResult) => void;
  accept?: string;
  className?: string;
  compact?: boolean;
}) {
  const files = useRef<HTMLInputElement>(null);
  const folder = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);

  async function run(list: FileList | null) {
    const picked = Array.from(list ?? []).filter((f) => !SKIPPED.test(f.name));
    if (picked.length === 0) return;
    const failed: UploadManyResult["failed"] = [];
    let uploaded = 0;
    setProgress({ done: 0, total: picked.length });
    for (const [index, file] of picked.entries()) {
      const label = (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
      if (file.size > MAX_BYTES) {
        failed.push({ name: label, error: "Larger than 15 MB." });
      } else {
        try {
          await upload(file);
          uploaded += 1;
        } catch (err) {
          failed.push({ name: label, error: err instanceof Error ? err.message : "Upload failed." });
        }
      }
      setProgress({ done: index + 1, total: picked.length });
    }
    setProgress(null);
    onDone({ uploaded, failed });
  }

  const button =
    "flex flex-1 items-center justify-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs font-medium hover:bg-muted disabled:opacity-50";
  return (
    <div className={cn("flex gap-1.5", className)}>
      <input
        ref={files}
        type="file"
        multiple
        accept={accept}
        className="sr-only"
        onChange={(e) => {
          void run(e.target.files);
          e.target.value = "";
        }}
      />
      <input
        ref={folder}
        type="file"
        multiple
        className="sr-only"
        // Folder picking is non-standard but supported by every current browser.
        {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
        onChange={(e) => {
          void run(e.target.files);
          e.target.value = "";
        }}
      />
      {progress ? (
        <span className={cn(button, "cursor-default")}>
          <Loader2 className="size-3.5 animate-spin" /> Uploading {progress.done}/{progress.total}
        </span>
      ) : (
        <>
          <button type="button" className={button} onClick={() => files.current?.click()}>
            <Upload className="size-3.5" /> {compact ? "Files" : "Upload files"}
          </button>
          <button type="button" className={button} onClick={() => folder.current?.click()}>
            <FolderUp className="size-3.5" /> {compact ? "Folder" : "Upload folder"}
          </button>
        </>
      )}
    </div>
  );
}
