import { useNavigate } from "react-router-dom";
import { ArrowRight, Building2, LogIn } from "lucide-react";
import { WizardCard } from "@/components/WizardCard";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { OrgNetworkIllustration } from "@/components/illustrations";

export default function Welcome() {
  const navigate = useNavigate();
  return (
    <WizardCard
      centered
      media={<OrgNetworkIllustration className="h-32 w-full" />}
      title="Welcome to Loom"
      subtitle="Sign in to your organization's knowledge graph, or set up a new one."
      footer={
        <div className="flex w-full flex-col gap-2.5">
          <PrimaryButton
            size="lg"
            className="w-full"
            onClick={() => navigate("/setup/signin")}
          >
            <LogIn />
            Sign in to your organization
          </PrimaryButton>
          <SecondaryButton
            size="lg"
            className="w-full"
            onClick={() => navigate("/setup/org")}
          >
            <Building2 />
            Set up a new organization
            <ArrowRight />
          </SecondaryButton>
        </div>
      }
    />
  );
}
