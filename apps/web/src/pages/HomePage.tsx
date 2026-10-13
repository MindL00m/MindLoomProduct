import type { OrgSummary } from "@/services/auth";
import OrganizationView from "@/pages/OrganizationView";
import StatusView from "@/pages/StatusView";
import { Card, CardContent } from "@/components/ui/card";
import { AgentOverview } from "@/components/AgentOverview";
import { LLMSettingsPanel } from "@/components/LLMSettingsPanel";

export default function HomePage({
  summary,
  isAdmin,
}: {
  summary: OrgSummary | null;
  isAdmin: boolean;
}) {
  return (
    <div className="mx-auto w-full max-w-6xl space-y-10">
      <section>
        <h2 className="text-2xl font-semibold tracking-tight">Company home</h2>
        <p className="mt-1 text-muted-foreground">
          Your organization, connected knowledge, and people in one place.
        </p>
        <div className="mt-5 grid grid-cols-3 gap-3">
          {[
            ["People", summary?.people ?? 0],
            ["Departments", summary?.departments ?? 0],
            ["Groups", summary?.groups ?? 0],
          ].map(([label, value]) => (
            <Card key={label}>
              <CardContent className="p-4">
                <p className="text-sm text-muted-foreground">{label}</p>
                <p className="mt-1 text-2xl font-semibold">{value}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      <section className="border-t border-border pt-8">
        <StatusView />
      </section>

      <AgentOverview isAdmin={isAdmin} />

      {isAdmin && (
        <div id="chat-model" className="scroll-mt-20">
          <LLMSettingsPanel />
        </div>
      )}

      <section className="min-h-[620px] border-t border-border pt-8">
        <OrganizationView />
      </section>
    </div>
  );
}
