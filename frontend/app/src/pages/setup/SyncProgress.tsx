import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Check, Loader2, Circle } from "lucide-react";
import { WizardCard } from "@/components/WizardCard";
import { ProgressBar } from "@/components/ProgressBar";
import { ErrorState } from "@/components/ErrorState";
import { getSyncProgress, initialStages } from "@/services/mockApi";
import { SetupError, type SetupSummary, type SyncStage } from "@/services/types";
import { useOnboarding } from "@/store/onboarding";
import { summarizeDirectory } from "@/lib/directory";
import { cn } from "@/lib/utils";

export default function SyncProgress() {
  const navigate = useNavigate();
  const {
    setSyncProgress,
    setSummary,
    selectedProvider,
    organizationName,
    directory,
    summary,
  } = useOnboarding();

  const isCsv = selectedProvider === "csv";
  const mode = isCsv ? "directory" : "google";

  const [progress, setProgress] = useState(0);
  const [stages, setStages] = useState<SyncStage[]>(() => initialStages(mode));
  const [error, setError] = useState<SetupError | null>(null);
  // Guard against React 18 StrictMode double-invocation in dev.
  const startedRef = useRef(false);

  async function runSync() {
    setError(null);
    setProgress(0);
    setStages(initialStages(mode));

    // CSV flow uses the counts captured at upload time (server-authoritative);
    // fall back to a client-side recount. Google flow uses mock numbers.
    const csvSummary: SetupSummary | undefined = isCsv
      ? (summary ?? {
          organization: organizationName || "Your organization",
          ...summarizeDirectory(directory),
        })
      : undefined;

    try {
      const summary = await getSyncProgress(
        (snap) => {
          setProgress(snap.progress);
          setStages(snap.stages);
          setSyncProgress(snap.progress);
        },
        { mode, summary: csvSummary },
      );
      setSummary(summary);
      // Brief beat at 100% before advancing.
      setTimeout(() => navigate("/setup/complete"), 600);
    } catch (err) {
      setError(
        err instanceof SetupError
          ? err
          : new SetupError("sync_failed", "Synchronization failed."),
      );
    }
  }

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    void runSync();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <WizardCard
      title="Setting up your organization"
      subtitle="This usually takes a few moments. You can keep this tab open."
    >
      {error ? (
        <ErrorState
          kind={error.kind}
          message={error.message}
          onRetry={() => {
            startedRef.current = true;
            void runSync();
          }}
          onBack={() => navigate(isCsv ? "/setup/csv" : "/setup/permissions")}
        />
      ) : (
        <div className="space-y-6">
          <ProgressBar value={progress} showValue label="Sync progress" />

          <ul className="space-y-1">
            {stages.map((stage) => (
              <li
                key={stage.id}
                className={cn(
                  "flex items-center gap-3 rounded-md px-3 py-2.5 text-sm transition-colors",
                  stage.status === "active" && "bg-accent/50",
                )}
              >
                <StageIcon status={stage.status} />
                <span
                  className={cn(
                    stage.status === "queued" && "text-muted-foreground",
                    stage.status === "active" && "font-medium text-foreground",
                    stage.status === "done" && "text-foreground",
                  )}
                >
                  {stage.label}
                  {stage.status === "active" && "…"}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </WizardCard>
  );
}

function StageIcon({ status }: { status: SyncStage["status"] }) {
  if (status === "done") {
    return (
      <span className="flex size-5 items-center justify-center rounded-full bg-success text-success-foreground">
        <Check className="size-3" strokeWidth={3} aria-hidden="true" />
      </span>
    );
  }
  if (status === "active") {
    return <Loader2 className="size-5 animate-spin text-primary" aria-hidden="true" />;
  }
  return <Circle className="size-5 text-mist-400" aria-hidden="true" />;
}
