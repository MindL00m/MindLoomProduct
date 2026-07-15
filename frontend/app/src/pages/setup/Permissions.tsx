import { useNavigate } from "react-router-dom";
import { ArrowLeft, ArrowRight, ShieldCheck } from "lucide-react";
import { WizardCard } from "@/components/WizardCard";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { PermissionItem } from "@/components/PermissionItem";

const PERMISSIONS = [
  {
    title: "Read user profiles",
    description: "Used to create employee profiles.",
  },
  {
    title: "Read organizational units",
    description: "Used to understand departments.",
  },
  {
    title: "Read reporting relationships",
    description: "Used to construct your org chart.",
  },
  {
    title: "Read profile photos",
    description: "Adds faces to employee profiles.",
    optional: true,
  },
  {
    title: "Read groups",
    description: "Used to understand teams.",
  },
];

export default function Permissions() {
  const navigate = useNavigate();
  return (
    <WizardCard
      title="Review requested permissions"
      subtitle="Loom requests only the read scopes it needs to build your graph."
    >
      <ul className="divide-y divide-border">
        {PERMISSIONS.map((p) => (
          <PermissionItem key={p.title} {...p} />
        ))}
      </ul>

      <div className="mt-5 flex items-start gap-2.5 rounded-md bg-accent/60 p-3.5 text-sm text-accent-foreground">
        <ShieldCheck className="mt-0.5 size-4 shrink-0 text-success" aria-hidden="true" />
        <p>Loom never modifies your Google Workspace.</p>
      </div>

      <div className="mt-8 flex items-center justify-between gap-3">
        <SecondaryButton onClick={() => navigate("/setup/google")}>
          <ArrowLeft />
          Back
        </SecondaryButton>
        <PrimaryButton onClick={() => navigate("/setup/sync")}>
          Continue
          <ArrowRight />
        </PrimaryButton>
      </div>
    </WizardCard>
  );
}
