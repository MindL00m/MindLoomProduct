import { FileSpreadsheet, FileText, FileType2 } from "lucide-react";
import type { FileKind, LoomFileMeta } from "@/services/files";
import { plural } from "@/lib/format";
import { cn } from "@/lib/utils";

export function FileKindIcon({ kind, className }: { kind: FileKind; className?: string }) {
  const Icon =
    kind === "csv" || kind === "xlsx" ? FileSpreadsheet : kind === "pdf" ? FileType2 : FileText;
  const color =
    kind === "xlsx" || kind === "csv"
      ? "text-emerald-700"
      : kind === "pdf"
        ? "text-red-700"
        : kind === "docx"
          ? "text-sky-700"
          : "text-muted-foreground";
  return <Icon className={cn("size-4 shrink-0", color, className)} aria-hidden="true" />;
}

export function fileDetail(f: Pick<LoomFileMeta, "kind" | "row_count" | "column_count">): string {
  switch (f.kind) {
    case "csv":
      return plural(f.row_count, "row");
    case "txt":
      return plural(f.row_count, "line");
    case "pdf":
      return plural(f.row_count, "page");
    case "docx":
      return "Word document";
    default:
      return plural(f.column_count, "sheet");
  }
}
