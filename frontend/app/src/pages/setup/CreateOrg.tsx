import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { WizardCard } from "@/components/WizardCard";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { Input } from "@/components/ui/input";
import { useOnboarding, normalizeDomain } from "@/store/onboarding";

export default function CreateOrg() {
  const navigate = useNavigate();
  const { organizationName, domain, setOrganization } = useOnboarding();

  const [name, setName] = useState(organizationName);
  const [domainInput, setDomainInput] = useState(domain);
  const [touched, setTouched] = useState(false);

  const nameError = name.trim() === "" ? "Organization name is required." : "";
  const normalized = normalizeDomain(domainInput);
  const domainError =
    normalized === ""
      ? "Company domain is required."
      : !/^[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(normalized)
        ? "Enter a valid domain, e.g. acme.com."
        : "";

  const isValid = !nameError && !domainError;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (!isValid) return;
    setOrganization(name.trim(), normalized);
    navigate("/setup/source");
  }

  return (
    <WizardCard
      title="Create your organization"
      subtitle="Tell us who you are. We'll use your domain to match employees during import."
    >
      <form onSubmit={handleSubmit} noValidate className="space-y-5">
        <div className="space-y-1.5">
          <label htmlFor="org-name" className="text-sm font-medium">
            Organization name
          </label>
          <Input
            id="org-name"
            placeholder="OpenAI"
            value={name}
            autoFocus
            invalid={touched && !!nameError}
            aria-describedby={nameError ? "org-name-error" : undefined}
            onChange={(e) => setName(e.target.value)}
          />
          {touched && nameError && (
            <p id="org-name-error" className="text-xs text-destructive">
              {nameError}
            </p>
          )}
        </div>

        <div className="space-y-1.5">
          <label htmlFor="org-domain" className="text-sm font-medium">
            Company domain
          </label>
          <Input
            id="org-domain"
            placeholder="openai.com"
            value={domainInput}
            inputMode="url"
            autoComplete="off"
            invalid={touched && !!domainError}
            aria-describedby="org-domain-help org-domain-error"
            // Normalize (strip protocol, lowercase) as soon as the field blurs.
            onBlur={() => setDomainInput(normalizeDomain(domainInput))}
            onChange={(e) => setDomainInput(e.target.value)}
          />
          {touched && domainError ? (
            <p id="org-domain-error" className="text-xs text-destructive">
              {domainError}
            </p>
          ) : (
            <p id="org-domain-help" className="text-xs text-muted-foreground">
              Protocols are stripped and the domain is lowercased automatically.
            </p>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 pt-2">
          <SecondaryButton type="button" onClick={() => navigate("/setup")}>
            <ArrowLeft />
            Back
          </SecondaryButton>
          <PrimaryButton type="submit">
            Continue
            <ArrowRight />
          </PrimaryButton>
        </div>
      </form>
    </WizardCard>
  );
}
