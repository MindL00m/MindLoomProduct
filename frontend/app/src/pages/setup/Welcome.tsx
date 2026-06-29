import { useNavigate } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { WizardCard } from "@/components/WizardCard";
import { PrimaryButton } from "@/components/PrimaryButton";
import { OrgNetworkIllustration } from "@/components/illustrations";

export default function Welcome() {
  const navigate = useNavigate();
  return (
    <WizardCard
      centered
      media={<OrgNetworkIllustration className="h-32 w-full" />}
      title="Welcome to Company Brain"
      subtitle="Connect your organization so Company Brain can build your company knowledge graph."
      footer={
        <PrimaryButton
          size="lg"
          className="w-full"
          onClick={() => navigate("/setup/org")}
        >
          Get Started
          <ArrowRight />
        </PrimaryButton>
      }
    />
  );
}
