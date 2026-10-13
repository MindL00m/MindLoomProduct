import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Download,
  Info,
} from "lucide-react";
import { WizardCard } from "@/components/WizardCard";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { FileDropzone } from "@/components/FileDropzone";
import { ErrorState } from "@/components/ErrorState";
import {
  DIRECTORY_CSV_TEMPLATE,
  OPTIONAL_COLUMNS,
  REQUIRED_COLUMNS,
  parseDirectoryCsv,
  summarizeDirectory,
  type DirectoryParseResult,
} from "@/lib/directory";
import { uploadCsvDirectory } from "@/services/directory";
import { SetupError } from "@/services/types";
import { useOnboarding } from "@/store/onboarding";
import { cn } from "@/lib/utils";

const REQUIRED = REQUIRED_COLUMNS;
const OPTIONAL = OPTIONAL_COLUMNS;
const MAX_BYTES = 5 * 1024 * 1024;

export default function UploadCsv() {
  const navigate = useNavigate();
  const { setDirectory, setSummary, organizationName, csvFileName } =
    useOnboarding();

  const [fileName, setFileName] = useState<string | null>(csvFileName);
  const [result, setResult] = useState<DirectoryParseResult | null>(null);
  const [parseError, setParseError] = useState<SetupError | null>(null);
  const [uploadError, setUploadError] = useState<SetupError | null>(null);
  const [uploading, setUploading] = useState(false);

  const errors = useMemo(
    () => result?.issues.filter((i) => i.severity === "error") ?? [],
    [result],
  );
  const warnings = useMemo(
    () => result?.issues.filter((i) => i.severity === "warning") ?? [],
    [result],
  );

  const blockingHeaders = (result?.missingRequiredColumns.length ?? 0) > 0;
  const canImport =
    !!result && result.people.length > 0 && errors.length === 0 && !blockingHeaders;

  async function handleFile(file: File) {
    setParseError(null);
    setUploadError(null);
    setResult(null);
    setFileName(file.name);

    if (file.size > MAX_BYTES) {
      setParseError(
        new SetupError("csv_invalid", "That file is larger than 5 MB."),
      );
      return;
    }

    try {
      const text = await file.text();
      const parsed = parseDirectoryCsv(text);
      if (parsed.headers.length === 0) {
        setParseError(
          new SetupError("csv_invalid", "The file appears to be empty."),
        );
        return;
      }
      setResult(parsed);
    } catch {
      setParseError(
        new SetupError("csv_invalid", "We couldn't read that file as CSV."),
      );
    }
  }

  function clearFile() {
    setFileName(null);
    setResult(null);
    setParseError(null);
    setUploadError(null);
  }

  function downloadTemplate() {
    const blob = new Blob([DIRECTORY_CSV_TEMPLATE], {
      type: "text/csv;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "loom-directory-template.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  async function handleImport() {
    if (!result || !canImport) return;
    setUploadError(null);
    setUploading(true);
    try {
      const { summary } = await uploadCsvDirectory(result.people);
      setDirectory(result.people, fileName ?? "directory.csv");
      setSummary({
        ...summary,
        organization: summary.organization || organizationName || "Your organization",
      });
      navigate("/dashboard?tab=organization");
    } catch (err) {
      setUploadError(
        err instanceof SetupError
          ? err
          : new SetupError("network_timeout", "Upload failed."),
      );
    } finally {
      setUploading(false);
    }
  }

  const counts = result ? summarizeDirectory(result.people) : null;

  return (
    <WizardCard
      title="Upload your people directory"
      subtitle="Provide a CSV of your employees. We'll build profiles, teams, and your reporting hierarchy from it."
    >
      {uploadError ? (
        <ErrorState
          kind={uploadError.kind}
          message={uploadError.message}
          retrying={uploading}
          onRetry={handleImport}
          onBack={() => setUploadError(null)}
        />
      ) : (
        <div className="space-y-5">
          {/* Column reference */}
          <div className="rounded-md border border-border bg-muted/40 p-4">
            <div className="flex items-center justify-between gap-2">
              <p className="text-sm font-medium">Expected columns</p>
              <button
                type="button"
                onClick={downloadTemplate}
                className="inline-flex items-center gap-1.5 text-xs font-medium text-primary hover:underline"
              >
                <Download className="size-3.5" />
                Download template
              </button>
            </div>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {REQUIRED.map((c) => (
                <code
                  key={c}
                  className="rounded bg-brand-50 px-1.5 py-0.5 text-xs font-medium text-brand-700 ring-1 ring-inset ring-brand-200"
                >
                  {c}*
                </code>
              ))}
              {OPTIONAL.map((c) => (
                <code
                  key={c}
                  className="rounded bg-background px-1.5 py-0.5 text-xs text-muted-foreground ring-1 ring-inset ring-border"
                >
                  {c}
                </code>
              ))}
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              <span className="text-brand-700">*</span> required. Headers are
              case-insensitive; common aliases (e.g. “Job Title”, “Reports To”)
              are accepted.
            </p>
          </div>

          <FileDropzone
            fileName={fileName}
            onFile={handleFile}
            onClear={clearFile}
            disabled={uploading}
          />

          {parseError && (
            <p
              role="alert"
              className="flex items-center gap-2 text-sm text-destructive"
            >
              <AlertCircle className="size-4 shrink-0" />
              {parseError.message}
            </p>
          )}

          {/* Missing-column hard stop */}
          {result && blockingHeaders && (
            <div
              role="alert"
              className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm"
            >
              <p className="font-medium text-destructive">
                Missing required column
                {result.missingRequiredColumns.length > 1 ? "s" : ""}:{" "}
                {result.missingRequiredColumns.join(", ")}
              </p>
              <p className="mt-1 text-muted-foreground">
                Add the column{result.missingRequiredColumns.length > 1 ? "s" : ""}{" "}
                and re-upload, or start from the template.
              </p>
            </div>
          )}

          {/* Validation summary + preview */}
          {result && !blockingHeaders && (
            <ValidationReport
              counts={counts!}
              totalRows={result.totalRows}
              errorCount={errors.length}
              warningCount={warnings.length}
              issues={[...errors, ...warnings].slice(0, 6)}
              previewPeople={result.people.slice(0, 4)}
            />
          )}

          <div className="flex items-center justify-between gap-3 pt-1">
            <SecondaryButton
              onClick={() => navigate("/dashboard?tab=organization")}
              disabled={uploading}
            >
              <ArrowLeft />
              Back
            </SecondaryButton>
            <PrimaryButton
              onClick={handleImport}
              loading={uploading}
              disabled={!canImport}
            >
              {result && canImport
                ? `Import ${result.people.length} ${result.people.length === 1 ? "person" : "people"}`
                : "Import"}
              {!uploading && <ArrowRight />}
            </PrimaryButton>
          </div>
        </div>
      )}
    </WizardCard>
  );
}

interface ReportProps {
  counts: { people: number; departments: number; groups: number };
  totalRows: number;
  errorCount: number;
  warningCount: number;
  issues: { row: number; field: string; message: string; severity: string }[];
  previewPeople: { name: string; email: string; title?: string; department?: string }[];
}

function ValidationReport({
  counts,
  totalRows,
  errorCount,
  warningCount,
  issues,
  previewPeople,
}: ReportProps) {
  const ok = errorCount === 0 && counts.people > 0;
  return (
    <div className="space-y-3">
      <div
        className={cn(
          "flex items-start gap-2.5 rounded-md p-3.5 text-sm",
          ok
            ? "bg-success/10 text-foreground"
            : "bg-destructive/5 text-foreground",
        )}
      >
        {ok ? (
          <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" />
        ) : (
          <AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" />
        )}
        <div>
          <p className="font-medium">
            {ok
              ? `${counts.people} people ready to import`
              : `${errorCount} row${errorCount === 1 ? "" : "s"} need attention`}
          </p>
          <p className="text-muted-foreground">
            {totalRows} row{totalRows === 1 ? "" : "s"} · {counts.departments}{" "}
            department{counts.departments === 1 ? "" : "s"} · {counts.groups}{" "}
            team{counts.groups === 1 ? "" : "s"}
            {warningCount > 0 && ` · ${warningCount} warning${warningCount === 1 ? "" : "s"}`}
          </p>
        </div>
      </div>

      {issues.length > 0 && (
        <ul className="space-y-1.5 rounded-md border border-border p-3 text-xs">
          {issues.map((issue, i) => (
            <li key={i} className="flex items-start gap-2">
              <span
                className={cn(
                  "mt-0.5 shrink-0 font-medium",
                  issue.severity === "error"
                    ? "text-destructive"
                    : "text-brand-600",
                )}
              >
                Row {issue.row}
              </span>
              <span className="text-muted-foreground">
                <span className="font-medium text-foreground">{issue.field}</span>{" "}
                — {issue.message}
              </span>
            </li>
          ))}
        </ul>
      )}

      {previewPeople.length > 0 && (
        <div className="overflow-hidden rounded-md border border-border">
          <div className="flex items-center gap-1.5 border-b border-border bg-muted/40 px-3 py-2 text-xs font-medium text-muted-foreground">
            <Info className="size-3.5" />
            Preview
          </div>
          <table className="w-full text-left text-xs">
            <thead className="text-muted-foreground">
              <tr className="border-b border-border">
                <th className="px-3 py-2 font-medium">Name</th>
                <th className="px-3 py-2 font-medium">Email</th>
                <th className="hidden px-3 py-2 font-medium sm:table-cell">
                  Title
                </th>
                <th className="hidden px-3 py-2 font-medium sm:table-cell">
                  Dept
                </th>
              </tr>
            </thead>
            <tbody>
              {previewPeople.map((p) => (
                <tr key={p.email} className="border-b border-border last:border-0">
                  <td className="px-3 py-2 font-medium text-foreground">
                    {p.name}
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">{p.email}</td>
                  <td className="hidden px-3 py-2 text-muted-foreground sm:table-cell">
                    {p.title ?? "—"}
                  </td>
                  <td className="hidden px-3 py-2 text-muted-foreground sm:table-cell">
                    {p.department ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
