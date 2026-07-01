import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowLeft, Lock } from "lucide-react";
import { WizardCard } from "@/components/WizardCard";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { Input } from "@/components/ui/input";
import { GoogleIcon } from "@/components/icons";
import { AuthError, googleSignIn } from "@/services/auth";
import { useSession } from "@/store/session";

export default function SignIn() {
  const navigate = useNavigate();
  const setSession = useSession((s) => s.setSession);
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  async function handleSignIn() {
    setError(null);
    setNotFound(false);
    const trimmed = email.trim().toLowerCase();
    if (!trimmed.includes("@")) {
      setError("Enter a valid Google email address.");
      return;
    }
    setLoading(true);
    try {
      const session = await googleSignIn(trimmed);
      setSession({
        orgId: session.org_id,
        orgName: session.org_name,
        userId: session.user_id,
        email: session.email,
        name: session.name,
        photoUrl: session.photo_url,
      });
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
      subtitle="Enter your work Google email. We'll load your organization's knowledge graph."
    >
      <div className="space-y-4">
        <div className="space-y-1.5">
          <label htmlFor="google-email" className="text-sm font-medium">
            Google email
          </label>
          <Input
            id="google-email"
            type="email"
            placeholder="you@company.com"
            value={email}
            autoFocus
            invalid={!!error}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void handleSignIn();
            }}
          />
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
        </div>

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
          Simulated sign-in for development — real Google OAuth can be wired later.
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
