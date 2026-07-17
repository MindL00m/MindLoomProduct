import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { WizardCard } from "@/components/WizardCard";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { Input } from "@/components/ui/input";
import { useOnboarding, normalizeDomain } from "@/store/onboarding";
import { AuthError, createOrg } from "@/services/auth";
import { useSession } from "@/store/session";

export default function CreateOrg() {
  const navigate = useNavigate();
  const { organizationName, domain, setOrganization } = useOnboarding();
  const setSession = useSession((s) => s.setSession);

  const [name, setName] = useState(organizationName);
  const [domainInput, setDomainInput] = useState(domain);
  const [adminEmail, setAdminEmail] = useState("");
  const [touched, setTouched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);

  const nameError = name.trim() === "" ? "Organization name is required." : "";
  const normalized = normalizeDomain(domainInput);
  const domainError =
    normalized === ""
      ? "Company domain is required."
      : !/^[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(normalized)
        ? "Enter a valid domain, e.g. acme.com."
        : "";
  const emailNorm = adminEmail.trim().toLowerCase();
  const emailError =
    emailNorm === ""
      ? "Admin Google email is required."
      : !emailNorm.includes("@")
        ? "Enter a valid email address."
        : !emailNorm.endsWith(`@${normalized}`) && normalized
          ? `Email must use @${normalized}.`
          : "";

  const isValid = !nameError && !domainError && !emailError;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    setApiError(null);
    if (!isValid) return;

    setLoading(true);
    try {
      const session = await createOrg({
        name: name.trim(),
        domain: normalized,
        adminEmail: emailNorm,
      });
      setOrganization(name.trim(), normalized);
      setSession({
        orgId: session.org_id,
        orgName: session.org_name,
        userId: session.user_id,
        email: session.email,
        name: session.name,
        photoUrl: session.photo_url,
        role: session.role,
      });
      navigate("/dashboard");
    } catch (err) {
      setApiError(
        err instanceof AuthError ? err.message : "Could not create organization.",
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
          <label htmlFor="admin-email" className="text-sm font-medium">
            Your Google email (admin)
          </label>
          <Input
            id="admin-email"
            type="email"
            placeholder={normalized ? `you@${normalized}` : "you@company.com"}
            value={adminEmail}
            invalid={touched && !!emailError}
            onChange={(e) => setAdminEmail(e.target.value)}
          />
          {touched && emailError && (
            <p className="text-xs text-destructive">{emailError}</p>
          )}
        </div>

        {apiError && <p className="text-sm text-destructive">{apiError}</p>}

        <div className="flex items-center justify-between gap-3 pt-2">
          <SecondaryButton type="button" onClick={() => navigate("/setup")}>
            <ArrowLeft />
            Back
          </SecondaryButton>
          <PrimaryButton type="submit" loading={loading}>
            Create organization
            <ArrowRight />
          </PrimaryButton>
        </div>
      </form>
    </WizardCard>
  );
}
