import { useId, useRef, useState } from "react";
import { UploadCloud, FileText, X } from "lucide-react";
import { cn } from "@/lib/utils";

export interface FileDropzoneProps {
  /** Accept attribute, e.g. ".csv,text/csv". */
  accept?: string;
  /** Currently selected file name, if any (controlled display). */
  fileName?: string | null;
  onFile: (file: File) => void;
  onClear?: () => void;
  hint?: string;
  disabled?: boolean;
}

/** Accessible drag-and-drop / click-to-browse file picker. */
export function FileDropzone({
  accept = ".csv,text/csv",
  fileName,
  onFile,
  onClear,
  hint = "CSV up to 5 MB",
  disabled,
}: FileDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const [dragging, setDragging] = useState(false);

  function pick(files: FileList | null) {
    if (files && files[0]) onFile(files[0]);
  }

  return (
    <div>
      <input
        id={inputId}
        ref={inputRef}
        type="file"
        accept={accept}
        className="sr-only"
        disabled={disabled}
        onChange={(e) => pick(e.target.files)}
      />

      {fileName ? (
        <div className="flex items-center gap-3 rounded-md border border-border bg-card p-4">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-md bg-brand-50 text-brand-700">
            <FileText className="size-5" aria-hidden="true" />
          </span>
          <span className="min-w-0 flex-1 truncate text-sm font-medium">
            {fileName}
          </span>
          {onClear && (
            <button
              type="button"
              onClick={onClear}
              className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label="Remove file"
            >
              <X className="size-4" />
            </button>
          )}
        </div>
      ) : (
        <label
          htmlFor={inputId}
          onDragOver={(e) => {
            e.preventDefault();
            if (!disabled) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            if (!disabled) pick(e.dataTransfer.files);
          }}
          className={cn(
            "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed px-6 py-10 text-center transition-colors",
            dragging
              ? "border-primary bg-brand-50/50"
              : "border-border hover:border-mist-400 hover:bg-muted/50",
            disabled && "pointer-events-none opacity-50",
          )}
        >
          <span className="flex size-11 items-center justify-center rounded-full bg-muted text-mist-700">
            <UploadCloud className="size-5" aria-hidden="true" />
          </span>
          <span className="text-sm font-medium text-foreground">
            Drag &amp; drop your CSV, or{" "}
            <span className="text-primary underline-offset-2 hover:underline">
              browse
            </span>
          </span>
          <span className="text-xs text-muted-foreground">{hint}</span>
        </label>
      )}
    </div>
  );
}
