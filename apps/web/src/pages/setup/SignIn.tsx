import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowLeft, Lock } from "lucide-react";
import { WizardCard } from "@/components/WizardCard";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { GoogleIcon } from "@/components/icons";
import { requestGoogleIdToken } from "@/lib/googleAuth";
import { AuthError, googleSignIn, toClientSession } from "@/services/auth";
import { useSession } from "@/store/session";

export default function SignIn() {
  const navigate = useNavigate();
  const setSession = useSession((s) => s.setSession);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  async function handleSignIn() {
    setError(null);
    setNotFound(false);
    setLoading(true);
    try {
      const idToken = await requestGoogleIdToken();
      const session = await googleSignIn(idToken);
      setSession(toClientSession(session));
      navigate("/dashboard");
    } catch (err) {
      if (err instanceof AuthError && err.status === 404) {
        setNotFound(true);
        setError(
          "No organization exists for this email domain. Set up a new organization first.",
        );
      } else {
        setError(err instanceof Error ? err.message : "Sign-in failed.");
      }
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
      title="Sign in with Google"
      subtitle="Use your work Google account. We'll load your organization's knowledge graph."
    >
      <div className="space-y-4">
        {error && (
          <p className="text-xs text-destructive">
            {error}
            {notFound && (
              <>
                {" "}
                <Link to="/setup/org" className="underline underline-offset-2">
                  Set up now
                </Link>
              </>
            )}
          </p>
        )}

        <PrimaryButton
          size="lg"
          className="w-full"
          loading={loading}
          onClick={() => void handleSignIn()}
        >
          {!loading && <GoogleIcon className="size-5" />}
          {loading ? "Signing in…" : "Continue with Google"}
        </PrimaryButton>

        <p className="flex items-center justify-center gap-1.5 text-xs text-muted-foreground">
          <Lock className="size-3.5" aria-hidden="true" />
          Secured with Google Identity — MindLoom never sees your password.
        </p>

        <div className="flex justify-center pt-1">
          <SecondaryButton size="sm" disabled={loading} onClick={() => navigate("/setup")}>
            <ArrowLeft />
            Back
          </SecondaryButton>
        </div>
      </div>
    </WizardCard>
  );
}
