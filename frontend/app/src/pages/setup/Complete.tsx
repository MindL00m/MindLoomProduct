import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import {
  Building2,
  Check,
  Layers,
  Plus,
  Users,
  UsersRound,
} from "lucide-react";
import { WizardCard } from "@/components/WizardCard";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { SummaryCard } from "@/components/SummaryCard";
import { useOnboarding } from "@/store/onboarding";
import { useSession } from "@/store/session";
import { getOrgSummary, type OrgSummary } from "@/services/auth";

export default function Complete() {
  const navigate = useNavigate();
  const { summary: localSummary, organizationName } = useOnboarding();
  const orgName = useSession((s) => s.orgName);
  const [remote, setRemote] = useState<OrgSummary | null>(null);

  useEffect(() => {
    getOrgSummary()
      .then(setRemote)
      .catch(() => setRemote(null));
  }, []);

  const summary = remote ?? localSummary;

  const rows = [
    {
      label: "Organization",
      value: summary?.organization ?? orgName ?? organizationName ?? "—",
      icon: <Building2 className="size-4 text-mist-700" />,
    },
    {
      label: "People",
      value: summary?.people ?? 0,
      icon: <Users className="size-4 text-mist-700" />,
    },
    {
      label: "Departments",
      value: summary?.departments ?? 0,
      icon: <Layers className="size-4 text-mist-700" />,
    },
    {
      label: "Groups",
      value: summary?.groups ?? 0,
      icon: <UsersRound className="size-4 text-mist-700" />,
    },
  ];

  return (
    <WizardCard
      centered
      media={
        <motion.span
          initial={{ scale: 0, rotate: -20 }}
          animate={{ scale: 1, rotate: 0 }}
          transition={{ type: "spring", stiffness: 260, damping: 16 }}
          className="flex size-16 items-center justify-center rounded-full bg-success text-success-foreground shadow-sm"
        >
          <Check className="size-8" strokeWidth={3} aria-hidden="true" />
        </motion.span>
      }
      title="Your organization is ready"
      subtitle="We've imported your directory and built your organization graph."
    >
      <SummaryCard rows={rows} />

      <div className="mt-8 flex flex-col gap-2.5 sm:flex-row">
        <PrimaryButton className="flex-1" onClick={() => navigate("/dashboard")}>
          Go to Dashboard
        </PrimaryButton>
        <SecondaryButton
          className="flex-1"
          onClick={() => navigate("/setup/source")}
        >
          <Plus />
          Import More Sources
        </SecondaryButton>
      </div>
    </WizardCard>
  );
}
