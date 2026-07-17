import { useCallback, useEffect, useState } from "react";
import {
  LayoutGrid,
  Loader2,
  MessageCircle,
  MessageSquare,
  Settings2,
  Unplug,
  Upload,
} from "lucide-react";
import { GoogleIcon } from "@/components/icons";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/StatusBadge";
import { ConnectionWizard } from "@/pages/integrations/ConnectionWizard";
import {
  connectGoogleWorkspaceDev,
  connectMicrosoftTeamsDev,
  connectZoomDev,
  disconnectConnection,
  listIntegrations,
  startGoogleWorkspaceOAuth,
  startMicrosoftTeamsOAuth,
  startZoomOAuth,
  setConnectionPaused,
  type ConnectionProvider,
  type IntegrationInfo,
} from "@/services/integrations";
import {
  decideKnowledgeReview,
  listKnowledgeReviews,
  moderateExpertAnswer,
  type KnowledgeReview,
} from "@/services/reviews";

interface AppsViewProps {
  oauthStatus?: "connected" | "error" | null;
  oauthError?: string | null;
  setupProvider?: ConnectionProvider | null;
  onOAuthHandled?: () => void;
}

export default function AppsView({
  oauthStatus,
  oauthError,
  setupProvider,
  onOAuthHandled,
}: AppsViewProps) {
  const [integrations, setIntegrations] = useState<IntegrationInfo[]>([]);
  const [oauthEnabled, setOauthEnabled] = useState(false);
  const [microsoftOauthEnabled, setMicrosoftOauthEnabled] = useState(false);
  const [zoomOauthEnabled, setZoomOauthEnabled] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busyProvider, setBusyProvider] = useState<string | null>(null);
  const [wizardProvider, setWizardProvider] = useState<ConnectionProvider | null>(
    setupProvider ?? null,
  );
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [reviews, setReviews] = useState<KnowledgeReview[]>([]);
  const [answerEdits, setAnswerEdits] = useState<Record<string, string>>({});

  const workspace = integrations.find((item) => item.provider === "google_workspace");
  const teams = integrations.find((item) => item.provider === "microsoft_teams");
  const zoom = integrations.find((item) => item.provider === "zoom");

  const loadIntegrations = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listIntegrations();
      setIntegrations(data.integrations);
      setOauthEnabled(data.oauth_enabled);
      setMicrosoftOauthEnabled(data.microsoft_oauth_enabled);
      setZoomOauthEnabled(data.zoom_oauth_enabled);
      setReviews(await listKnowledgeReviews().catch(() => []));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load apps.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadIntegrations();
  }, [loadIntegrations]);

  useEffect(() => {
    if (!integrations.some((item) => item.setup_status === "importing")) return;
    const timer = window.setInterval(() => void loadIntegrations(), 3000);
    return () => window.clearInterval(timer);
  }, [integrations, loadIntegrations]);

  useEffect(() => {
    if (setupProvider) {
      setWizardProvider(setupProvider);
      setBanner("Authorization complete. Choose the company content Loom may connect.");
      onOAuthHandled?.();
    } else if (oauthStatus === "connected") {
      setBanner("Workspace authorization completed successfully.");
      onOAuthHandled?.();
      void loadIntegrations();
    } else if (oauthStatus === "error") {
      setError(oauthError ?? "Sign-in was cancelled or failed.");
      onOAuthHandled?.();
    }
  }, [setupProvider, oauthStatus, oauthError, onOAuthHandled, loadIntegrations]);

  async function connectWorkspace(provider: ConnectionProvider) {
    setBusyProvider(provider);
    setError(null);
    try {
      if (provider === "google_workspace") {
        if (oauthEnabled) {
          window.location.href = await startGoogleWorkspaceOAuth();
          return;
        }
        await connectGoogleWorkspaceDev();
      } else if (provider === "microsoft_teams") {
        if (microsoftOauthEnabled) {
          window.location.href = await startMicrosoftTeamsOAuth();
          return;
        }
        await connectMicrosoftTeamsDev();
      } else {
        if (zoomOauthEnabled) {
          window.location.href = await startZoomOAuth();
          return;
        }
        await connectZoomDev();
      }
      await loadIntegrations();
      setWizardProvider(provider);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Workspace connection failed.");
    } finally {
      setBusyProvider(null);
    }
  }

  if (loading && integrations.length === 0) {
    return <div className="flex justify-center py-16"><Loader2 className="animate-spin" /></div>;
  }

  return (
    <div className="mx-auto w-full max-w-4xl space-y-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">Connected workspaces</h2>
        <p className="mt-1 text-muted-foreground">
          Choose approved company locations once. Loom imports their knowledge and keeps it current.
        </p>
      </div>

      {banner && <div className="rounded-md border border-brand-200 bg-brand-50 p-3 text-sm text-brand-900">{banner}</div>}
      {error && <div className="rounded-md border border-destructive/20 bg-destructive/10 p-3 text-sm text-destructive">{error}</div>}

      {wizardProvider && (
        <ConnectionWizard
          provider={wizardProvider}
          onCancel={() => setWizardProvider(null)}
          onComplete={(jobId) => {
            setWizardProvider(null);
            setBanner(`Initial import started${jobId ? ` (${jobId})` : ""}. Loom will keep this connection updated.`);
            void loadIntegrations();
          }}
        />
      )}

      <WorkspaceCard
        title="Google Workspace"
        description="Continuously connect approved Gmail, Google Calendars, shared drives, and folders."
        icon={<GoogleIcon className="size-6" />}
        integration={workspace}
        busy={busyProvider === "google_workspace"}
        connectLabel={oauthEnabled ? "Authorize Google" : "Authorize Google (dev)"}
        onConnect={() => void connectWorkspace("google_workspace")}
        onManage={() => setWizardProvider("google_workspace")}
        onPause={(paused) => void setConnectionPaused("google_workspace", paused).then(loadIntegrations)}
        onDisconnect={() => void disconnectConnection("google_workspace").then(loadIntegrations)}
      />

      <WorkspaceCard
        title="Zoom"
        description="Continuously import approved cloud-recording transcripts, summaries, and meeting chats."
        icon={<MessageSquare className="size-6" />}
        integration={zoom}
        busy={busyProvider === "zoom"}
        connectLabel={zoomOauthEnabled ? "Authorize Zoom" : "Authorize Zoom (dev)"}
        onConnect={() => void connectWorkspace("zoom")}
        onManage={() => setWizardProvider("zoom")}
        onPause={(paused) => void setConnectionPaused("zoom", paused).then(loadIntegrations)}
        onDisconnect={() => void disconnectConnection("zoom").then(loadIntegrations)}
      />

      <WorkspaceCard
        title="Microsoft 365"
        description="Continuously connect approved Outlook mail and calendars, SharePoint, Teams channels, and private chats."
        icon={<MessageSquare className="size-6" />}
        integration={teams}
        busy={busyProvider === "microsoft_teams"}
        connectLabel={microsoftOauthEnabled ? "Authorize Microsoft" : "Authorize Microsoft (dev)"}
        onConnect={() => void connectWorkspace("microsoft_teams")}
        onManage={() => setWizardProvider("microsoft_teams")}
        onPause={(paused) => void setConnectionPaused("microsoft_teams", paused).then(loadIntegrations)}
        onDisconnect={() => void disconnectConnection("microsoft_teams").then(loadIntegrations)}
      />

      <Card>
        <CardHeader className="flex-row items-start gap-3">
          <span className="flex size-11 items-center justify-center rounded-lg border">
            <MessageCircle className="size-6" />
          </span>
          <div>
            <CardTitle className="text-lg">WhatsApp exports</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              Preview and import approved WhatsApp chat exports into searchable knowledge.
            </p>
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Use the Upload section. WhatsApp exports are manual snapshots, not a live connection.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex-row items-start gap-3">
          <span className="flex size-11 items-center justify-center rounded-lg border"><Upload className="size-6" /></span>
          <div>
            <CardTitle className="text-lg">Manual uploads</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              Add files that are not stored in a connected workspace. Uploaded files use the same Loom ingestion pipeline.
            </p>
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">Use the Upload section in the main navigation.</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Knowledge review queue</CardTitle>
          <p className="text-sm text-muted-foreground">
            Resolve detected conflicts, verify ageing sources, and approve expert-proposed answers.
          </p>
        </CardHeader>
        <CardContent>
          {reviews.filter((review) => review.status === "open" && review.review_type !== "expert_request").length === 0 ? (
            <p className="text-sm text-muted-foreground">No open reviews.</p>
          ) : (
            <div className="space-y-3">
              {reviews.filter((review) => review.status === "open" && review.review_type !== "expert_request").map((review) => (
                <div key={review.review_id} className="rounded-md border border-border p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-xs uppercase tracking-wide text-muted-foreground">{review.review_type}</p>
                      <p className="font-medium">{review.title}</p>
                      <p className="mt-1 whitespace-pre-line text-sm text-muted-foreground">{review.description}</p>
                      {review.proposed_content && <p className="mt-2 rounded bg-muted p-2 text-sm">{review.proposed_content}</p>}
                    </div>
                    <div className="flex gap-2">
                      <SecondaryButton onClick={() => void decideKnowledgeReview(review.review_id, "rejected").then(loadIntegrations)}>
                        Reject
                      </SecondaryButton>
                      <PrimaryButton onClick={() => void decideKnowledgeReview(
                        review.review_id,
                        review.review_type === "proposal" ? "approved" : "resolved",
                      ).then(loadIntegrations)}>
                        {review.review_type === "proposal" ? "Approve" : "Resolve"}
                      </PrimaryButton>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
          {reviews.some((review) => review.review_type === "expert_request" && review.status === "answered") && (
            <div className="mt-6 space-y-3 border-t border-border pt-5">
              <h3 className="font-medium">Published expert answers</h3>
              <p className="text-sm text-muted-foreground">
                These answers are already searchable. Administrators can correct or remove them.
              </p>
              {reviews.filter((review) => review.review_type === "expert_request" && review.status === "answered").map((review) => (
                <div key={review.review_id} className="rounded-md border border-border p-3">
                  <p className="font-medium">{review.title.replace("Expert question: ", "")}</p>
                  <textarea
                    className="mt-2 min-h-24 w-full rounded-md border border-border bg-background p-2 text-sm"
                    value={answerEdits[review.review_id] ?? review.proposed_content ?? ""}
                    onChange={(event) => setAnswerEdits((current) => ({
                      ...current, [review.review_id]: event.target.value,
                    }))}
                  />
                  <div className="mt-2 flex gap-2">
                    <PrimaryButton onClick={() => void moderateExpertAnswer(
                      review.review_id,
                      "edit",
                      answerEdits[review.review_id] ?? review.proposed_content ?? "",
                    ).then(loadIntegrations)}>
                      Save correction
                    </PrimaryButton>
                    <SecondaryButton onClick={() => void moderateExpertAnswer(
                      review.review_id,
                      "remove",
                    ).then(loadIntegrations)}>
                      Remove from knowledge
                    </SecondaryButton>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

    </div>
  );
}

function WorkspaceCard({
  title,
  description,
  icon,
  integration,
  busy,
  connectLabel,
  onConnect,
  onManage,
  onPause,
  onDisconnect,
}: {
  title: string;
  description: string;
  icon: React.ReactNode;
  integration?: IntegrationInfo;
  busy: boolean;
  connectLabel: string;
  onConnect: () => void;
  onManage: () => void;
  onPause: (paused: boolean) => void;
  onDisconnect: () => void;
}) {
  const status = integration?.setup_status ?? "not_connected";
  const active = status === "active";
  const statusLabel = {
    not_connected: "Not connected",
    setup_required: "Setup required",
    importing: "Initial import",
    active: "Active",
    paused: "Paused",
    warning: "Needs attention",
    error: "Error",
  }[status] ?? status;
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="flex size-11 items-center justify-center rounded-lg border">{icon}</span>
          <div><CardTitle className="text-lg">{title}</CardTitle><p className="mt-1 text-sm text-muted-foreground">{description}</p></div>
        </div>
        <StatusBadge tone={active ? "healthy" : status === "not_connected" ? "neutral" : "brand"} dot>{statusLabel}</StatusBadge>
      </CardHeader>
      <CardContent className="space-y-4">
        {integration?.connected && (
          <div className="grid gap-2 text-sm text-muted-foreground sm:grid-cols-3">
            <span>{integration.account_email}</span>
            <span>{integration.selected_resource_count} approved locations</span>
            <span>{integration.last_synced_at ? `Last updated ${new Date(integration.last_synced_at).toLocaleString()}` : "Not imported yet"}</span>
          </div>
        )}
        {!integration?.connected ? (
          <PrimaryButton onClick={onConnect} disabled={busy}>
            {busy ? <Loader2 className="size-4 animate-spin" /> : <LayoutGrid className="size-4" />}{connectLabel}
          </PrimaryButton>
        ) : (
          <div className="flex flex-wrap gap-2">
            <PrimaryButton onClick={onManage}><Settings2 className="size-4" />{status === "setup_required" ? "Continue setup" : "Manage connection"}</PrimaryButton>
            {status !== "setup_required" && (
              <SecondaryButton onClick={() => onPause(status !== "paused")}>
                {status === "paused" ? "Resume" : "Pause"}
              </SecondaryButton>
            )}
            <SecondaryButton onClick={onDisconnect}><Unplug className="size-4" />Disconnect</SecondaryButton>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
