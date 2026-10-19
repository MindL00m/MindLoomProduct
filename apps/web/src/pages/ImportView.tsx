import AppsView from "@/pages/AppsView";
import UploadData from "@/pages/UploadData";
import type { ConnectionProvider } from "@/services/integrations";

export default function ImportView({
  isAdmin,
  setupProvider,
  oauthStatus,
  oauthError,
  onOAuthHandled,
}: {
  isAdmin: boolean;
  setupProvider: ConnectionProvider | null;
  oauthStatus: "connected" | "error" | null;
  oauthError: string | null;
  onOAuthHandled: () => void;
}) {
  return (
    <div className="mx-auto w-full max-w-6xl space-y-10">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">Import</h2>
        <p className="mt-1 text-muted-foreground">
          Bring company knowledge into MindLoom through connected apps, documents,
          and exported conversations.
        </p>
      </div>

      <section aria-labelledby="connected-sources-heading">
        <h3 id="connected-sources-heading" className="text-lg font-semibold">
          Connected sources
        </h3>
        <p className="mt-1 mb-5 text-sm text-muted-foreground">
          Apps MindLoom keeps in sync. Use Add connector to connect Google
          Workspace, Microsoft 365, Zoom, or GitHub.
        </p>
        <AppsView
          isAdmin={isAdmin}
          showHeading={false}
          setupProvider={setupProvider}
          oauthStatus={oauthStatus}
          oauthError={oauthError}
          onOAuthHandled={onOAuthHandled}
        />
      </section>

      <section
        id="manual-import"
        aria-labelledby="manual-import-heading"
        className="scroll-mt-20 border-t border-border pt-8"
      >
        <h3 id="manual-import-heading" className="text-lg font-semibold">
          Upload files
        </h3>
        <p className="mt-1 mb-5 text-sm text-muted-foreground">
          Add documents and exported conversations, including WhatsApp chat
          exports. Their content and source context are retained together.
        </p>
        <UploadData showHeading={false} />
      </section>
    </div>
  );
}
