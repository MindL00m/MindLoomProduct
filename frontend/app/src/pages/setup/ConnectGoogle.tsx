import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, Lock } from "lucide-react";
import { WizardCard } from "@/components/WizardCard";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { ErrorState } from "@/components/ErrorState";
import { GoogleIcon } from "@/components/icons";
import { connectGoogleWorkspace } from "@/services/mockApi";
import { SetupError } from "@/services/types";
import { useOnboarding } from "@/store/onboarding";

export default function ConnectGoogle() {
  const navigate = useNavigate();
  const { domain, setOAuthConnected } = useOnboarding();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<SetupError | null>(null);

  async function handleConnect() {
    setError(null);
    setLoading(true);
    try {
      // TODO(backend): this kicks off the mock OAuth flow; swap for a real
      // redirect to the Google consent screen + callback exchange.
      const result = await connectGoogleWorkspace(domain);
      setOAuthConnected(result.account);
      navigate("/setup/permissions");
    } catch (err) {
      setError(
        err instanceof SetupError
          ? err
          : new SetupError("network_timeout", "Something went wrong."),
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <WizardCard
      centered
      media={
        <span className="flex size-16 items-center justify-center rounded-2xl border border-border bg-background shadow-sm">
          <GoogleIcon className="size-9" />
        </span>
      }
      title="Connect your Google Workspace directory"
      subtitle="Loom will request read-only access to your organization's directory."
    >
      {error ? (
        <ErrorState
          kind={error.kind}
          message={error.message}
          retrying={loading}
          onRetry={handleConnect}
          onBack={() => navigate("/setup/source")}
        />
      ) : (
        <div className="space-y-4">
          <PrimaryButton
            size="lg"
            className="w-full"
            loading={loading}
            onClick={handleConnect}
          >
            {!loading && <GoogleIcon className="size-5" />}
            {loading ? "Connecting…" : "Continue with Google"}
          </PrimaryButton>

          <p className="flex items-center justify-center gap-1.5 text-xs text-muted-foreground">
            <Lock className="size-3.5" aria-hidden="true" />
            Read-only access. We never modify your Google Workspace.
          </p>

          <div className="flex justify-center pt-1">
            <SecondaryButton
              size="sm"
              disabled={loading}
              onClick={() => navigate("/setup/source")}
            >
              <ArrowLeft />
              Back
            </SecondaryButton>
          </div>
        </div>
      )}
    </WizardCard>
  );
}
