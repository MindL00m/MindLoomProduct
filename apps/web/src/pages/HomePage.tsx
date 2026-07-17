import type { ConnectionProvider } from "@/services/integrations";
import type { OrgSummary } from "@/services/auth";
import AppsView from "@/pages/AppsView";
import OrganizationView from "@/pages/OrganizationView";
import { Card, CardContent } from "@/components/ui/card";

export default function HomePage({
  summary,
  isAdmin,
  setupProvider,
  oauthStatus,
  oauthError,
  onOAuthHandled,
}: {
  summary: OrgSummary | null;
  isAdmin: boolean;
  setupProvider: ConnectionProvider | null;
  oauthStatus: "connected" | "error" | null;
  oauthError: string | null;
  onOAuthHandled: () => void;
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

      {isAdmin && (
        <section className="border-t border-border pt-8">
          <AppsView
            setupProvider={setupProvider}
            oauthStatus={oauthStatus}
            oauthError={oauthError}
            onOAuthHandled={onOAuthHandled}
          />
        </section>
      )}

      <section className="min-h-[620px] border-t border-border pt-8">
        <OrganizationView />
      </section>
    </div>
  );
}
