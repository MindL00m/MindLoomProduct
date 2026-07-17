import { AlertTriangle, FileWarning, RotateCw, WifiOff, ShieldX } from "lucide-react";
import type { SetupErrorKind } from "@/services/types";
import { PrimaryButton } from "./PrimaryButton";
import { SecondaryButton } from "./SecondaryButton";

const COPY: Record<
  SetupErrorKind,
  { title: string; icon: React.ReactNode }
> = {
  oauth_cancelled: {
    title: "Authorization cancelled",
    icon: <ShieldX className="size-6" />,
  },
  network_timeout: {
    title: "Connection timed out",
    icon: <WifiOff className="size-6" />,
  },
  sync_failed: {
    title: "Synchronization failed",
    icon: <AlertTriangle className="size-6" />,
  },
  csv_invalid: {
    title: "We couldn't read that file",
    icon: <FileWarning className="size-6" />,
  },
};

export interface ErrorStateProps {
  kind: SetupErrorKind;
  message: string;
  onRetry: () => void;
  onBack?: () => void;
  retrying?: boolean;
}

/** Inline, retryable error panel shared by every wizard step. */
export function ErrorState({
  kind,
  message,
  onRetry,
  onBack,
  retrying,
}: ErrorStateProps) {
  const { title, icon } = COPY[kind];
  return (
    <div
      role="alert"
      className="flex flex-col items-center gap-4 rounded-md border border-destructive/30 bg-destructive/5 p-6 text-center"
    >
      <span className="flex size-12 items-center justify-center rounded-full bg-destructive/10 text-destructive">
        {icon}
      </span>
      <div className="space-y-1">
        <h3 className="font-semibold text-foreground">{title}</h3>
        <p className="text-sm text-muted-foreground">{message}</p>
      </div>
      <div className="flex flex-wrap items-center justify-center gap-2">
        {onBack && (
          <SecondaryButton onClick={onBack} disabled={retrying}>
            Go back
          </SecondaryButton>
        )}
        <PrimaryButton onClick={onRetry} loading={retrying}>
          {!retrying && <RotateCw />}
          Try again
        </PrimaryButton>
      </div>
    </div>
  );
}
