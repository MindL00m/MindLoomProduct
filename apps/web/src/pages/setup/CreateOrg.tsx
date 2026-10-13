import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { WizardCard } from "@/components/WizardCard";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { Input } from "@/components/ui/input";
import { GoogleIcon } from "@/components/icons";
import { requestGoogleIdToken } from "@/lib/googleAuth";
import { useOnboarding, normalizeDomain } from "@/store/onboarding";
import { createOrg, toClientSession } from "@/services/auth";
import { useSession } from "@/store/session";

export default function CreateOrg() {
  const navigate = useNavigate();
  const { organizationName, domain, setOrganization } = useOnboarding();
  const setSession = useSession((s) => s.setSession);

  const [name, setName] = useState(organizationName);
  const [domainInput, setDomainInput] = useState(domain);
  const [idToken, setIdToken] = useState<string | null>(null);
  const [googleEmail, setGoogleEmail] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);

  const nameError = name.trim() === "" ? "Organization name is required." : "";
  const normalized = normalizeDomain(domainInput);
  const googleEmailDomain =
    googleEmail?.includes("@") ? normalizeDomain(googleEmail.split("@")[1] ?? "") : "";
  const domainError =
    normalized === ""
      ? "Company domain is required."
      : !/^[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(normalized)
        ? "Enter a valid domain, e.g. acme.com."
        : googleEmailDomain && normalized !== googleEmailDomain
          ? `Domain must match your Google account (${googleEmailDomain}).`
          : "";
  const googleError = !idToken ? "Verify your Google account before creating the organization." : "";

  const isValid = !nameError && !domainError && !googleError;

  async function handleGoogleVerify() {
    setApiError(null);
    setGoogleLoading(true);
    try {
      const token = await requestGoogleIdToken();
      setIdToken(token);
      // Decode email from JWT payload for display only (server re-verifies).
      try {
        const payload = JSON.parse(atob(token.split(".")[1] ?? "")) as { email?: string };
        const email = payload.email ?? null;
        setGoogleEmail(email);
        // Prefill company domain from the verified Google email.
        if (email?.includes("@")) {
          const emailDomain = normalizeDomain(email.split("@")[1] ?? "");
          if (emailDomain) setDomainInput(emailDomain);
        }
      } catch {
        setGoogleEmail(null);
      }
    } catch (err) {
      setApiError(err instanceof Error ? err.message : "Google verification failed.");
      setIdToken(null);
      setGoogleEmail(null);
    } finally {
      setGoogleLoading(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    setApiError(null);
    if (!isValid || !idToken) return;

    setLoading(true);
    try {
      const session = await createOrg({
        name: name.trim(),
        domain: normalized,
        idToken,
      });
      setOrganization(name.trim(), normalized);
      setSession(toClientSession(session));
      navigate("/dashboard");
    } catch (err) {
      setApiError(
        err instanceof Error ? err.message : "Could not create organization.",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <WizardCard
      title="Create your organization"
      subtitle="Tell us who you are. Your Google email domain becomes your organization's identity."
    >
      <form onSubmit={(e) => void handleSubmit(e)} noValidate className="space-y-5">
        <div className="space-y-1.5">
          <label htmlFor="org-name" className="text-sm font-medium">
            Organization name
          </label>
          <Input
            id="org-name"
            placeholder="Acme Inc"
            value={name}
            autoFocus
            invalid={touched && !!nameError}
            onChange={(e) => setName(e.target.value)}
          />
          {touched && nameError && (
            <p className="text-xs text-destructive">{nameError}</p>
          )}
        </div>

        <div className="space-y-1.5">
          <label htmlFor="org-domain" className="text-sm font-medium">
            Company domain
          </label>
          <Input
            id="org-domain"
            placeholder="acme.com"
            value={domainInput}
            inputMode="url"
            autoComplete="off"
            invalid={touched && !!domainError}
            onBlur={() => setDomainInput(normalizeDomain(domainInput))}
            onChange={(e) => setDomainInput(e.target.value)}
          />
          {touched && domainError ? (
            <p className="text-xs text-destructive">{domainError}</p>
          ) : (
            <p className="text-xs text-muted-foreground">
              Employees sign in with an email on this domain.
            </p>
          )}
        </div>

        <div className="space-y-1.5">
          <span className="text-sm font-medium">Admin Google account</span>
          {googleEmail ? (
            <p className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm">
              Verified as <span className="font-medium">{googleEmail}</span>
            </p>
          ) : (
            <PrimaryButton
              type="button"
              className="w-full"
              loading={googleLoading}
              onClick={() => void handleGoogleVerify()}
            >
              {!googleLoading && <GoogleIcon className="size-5" />}
              {googleLoading ? "Verifying…" : "Verify with Google"}
            </PrimaryButton>
          )}
          {touched && googleError && (
            <p className="text-xs text-destructive">{googleError}</p>
          )}
        </div>

        {apiError && <p className="text-sm text-destructive">{apiError}</p>}

        <div className="flex items-center justify-between gap-3 pt-2">
          <SecondaryButton type="button" onClick={() => navigate("/setup")}>
            <ArrowLeft />
            Back
          </SecondaryButton>
          <PrimaryButton type="submit" loading={loading} disabled={!idToken}>
            Create organization
            <ArrowRight />
          </PrimaryButton>
        </div>
      </form>
    </WizardCard>
  );
}
