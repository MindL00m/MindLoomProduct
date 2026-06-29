import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, ArrowRight, FileSpreadsheet } from "lucide-react";
import { WizardCard } from "@/components/WizardCard";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { SourceCard } from "@/components/SourceCard";
import { GoogleIcon, MicrosoftIcon, OktaIcon } from "@/components/icons";
import { useOnboarding } from "@/store/onboarding";
import type { ProviderId } from "@/services/types";

interface SourceDef {
  id: ProviderId;
  title: string;
  description: string;
  icon: React.ReactNode;
  available: boolean;
}

const SOURCES: SourceDef[] = [
  {
    id: "google",
    title: "Google Workspace",
    description:
      "Import employees, teams, reporting hierarchy, job titles, and profile information.",
    icon: <GoogleIcon className="size-6" />,
    available: true,
  },
  {
    id: "microsoft",
    title: "Microsoft Entra ID",
    description: "Sync your directory, groups and org structure from Entra ID.",
    icon: <MicrosoftIcon className="size-6" />,
    available: false,
  },
  {
    id: "okta",
    title: "Okta",
    description: "Connect Okta Universal Directory to import people and groups.",
    icon: <OktaIcon className="size-6" />,
    available: false,
  },
  {
    id: "csv",
    title: "CSV Upload",
    description: "Upload a spreadsheet of employees to bootstrap your directory.",
    icon: <FileSpreadsheet className="size-6 text-brand" />,
    available: true,
  },
];

/** Where to go after picking a source. */
const NEXT_ROUTE: Partial<Record<ProviderId, string>> = {
  google: "/setup/google",
  csv: "/setup/csv",
};

export default function ChooseSource() {
  const navigate = useNavigate();
  const { selectedProvider, setProvider } = useOnboarding();
  const [selected, setSelected] = useState<ProviderId | null>(
    selectedProvider ?? "google",
  );

  function handleContinue() {
    if (!selected) return;
    setProvider(selected);
    navigate(NEXT_ROUTE[selected] ?? "/setup/google");
  }

  return (
    <WizardCard
      title="How would you like to import your people directory?"
      subtitle="Choose an identity source. You can connect more later."
    >
      <div className="space-y-3">
        {SOURCES.map((s) => (
          <SourceCard
            key={s.id}
            title={s.title}
            description={s.description}
            icon={s.icon}
            available={s.available}
            selected={selected === s.id}
            onSelect={() => setSelected(s.id)}
          />
        ))}
      </div>

      <div className="mt-8 flex items-center justify-between gap-3">
        <SecondaryButton onClick={() => navigate("/setup/org")}>
          <ArrowLeft />
          Back
        </SecondaryButton>
        <PrimaryButton onClick={handleContinue} disabled={!selected}>
          Continue
          <ArrowRight />
        </PrimaryButton>
      </div>
    </WizardCard>
  );
}
