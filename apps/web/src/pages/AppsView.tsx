import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  LayoutGrid,
  Loader2,
  MessageSquare,
  Plus,
  RefreshCw,
  Settings2,
  Unplug,
  X,
} from "lucide-react";
import { GoogleIcon } from "@/components/icons";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/StatusBadge";
import { ConnectionWizard } from "@/pages/integrations/ConnectionWizard";
import GitHubConnector from "@/pages/integrations/GitHubConnector";
import { SlackConnectorCard, SlackSetup } from "@/pages/integrations/SlackSetup";
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
import { getSlackStatus, type SlackStatus } from "@/services/slack";

interface AppsViewProps {
  isAdmin: boolean;
  showHeading?: boolean;
  oauthStatus?: "connected" | "error" | null;
  oauthError?: string | null;
  setupProvider?: ConnectionProvider | null;
  onOAuthHandled?: () => void;
}

export default function AppsView({
  isAdmin,
  showHeading = true,
  oauthStatus,
  oauthError,
  setupProvider,
  onOAuthHandled,
}: AppsViewProps) {
  const [integrations, setIntegrations] = useState<IntegrationInfo[]>([]);
  const [oauthEnabled, setOauthEnabled] = useState(false);
  const [microsoftOauthEnabled, setMicrosoftOauthEnabled] = useState(false);
  const [zoomOauthEnabled, setZoomOauthEnabled] = useState(false);
  const [devIntegrationsAllowed, setDevIntegrationsAllowed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busyProvider, setBusyProvider] = useState<string | null>(null);
  const [wizardProvider, setWizardProvider] = useState<ConnectionProvider | null>(
    setupProvider ?? null,
  );
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [reviews, setReviews] = useState<KnowledgeReview[]>([]);
  const [answerEdits, setAnswerEdits] = useState<Record<string, string>>({});
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [slack, setSlack] = useState<SlackStatus | null>(null);
  const [slackOpen, setSlackOpen] = useState(false);

  const workspace = integrations.find((item) => item.provider === "google_workspace");
  const teams = integrations.find((item) => item.provider === "microsoft_teams");
  const zoom = integrations.find((item) => item.provider === "zoom");
  const github = integrations.find((item) => item.provider === "github");

  const loadIntegrations = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listIntegrations();
      setIntegrations(data.integrations);
      setOauthEnabled(data.oauth_enabled);
      setMicrosoftOauthEnabled(data.microsoft_oauth_enabled);
      setZoomOauthEnabled(data.zoom_oauth_enabled);
      setDevIntegrationsAllowed(Boolean(data.dev_integrations_allowed));
      setReviews(isAdmin ? await listKnowledgeReviews().catch(() => []) : []);
      setSlack(await getSlackStatus().catch(() => null));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load apps.");
    } finally {
      setLoading(false);
    }
  }, [isAdmin]);

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
      setBanner("Authorization complete. Choose the company content MindLoom may connect.");
      onOAuthHandled?.();
    } else if (oauthStatus === "connected") {
      setBanner("Workspace authorization completed successfully.");
      onOAuthHandled?.();
      void loadIntegrations();
    } else if (oauthStatus === "error") {
      setError(oauthError ?? "Sign-in was cancelled or failed.");
      onOAuthHandled?.();
    }
  }, [setupProvider, oauthStatus, oauthError, onOAuthHandled, loadIntegrations, isAdmin]);

  async function connectWorkspace(provider: ConnectionProvider) {
    setBusyProvider(provider);
    setError(null);
    try {
      if (provider === "google_workspace") {
        if (oauthEnabled) {
          window.location.href = await startGoogleWorkspaceOAuth();
          return;
        }
        if (!devIntegrationsAllowed) {
          throw new Error("Google OAuth is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.");
        }
        await connectGoogleWorkspaceDev();
      } else if (provider === "microsoft_teams") {
        if (microsoftOauthEnabled) {
          window.location.href = await startMicrosoftTeamsOAuth();
          return;
        }
        if (!devIntegrationsAllowed) {
          throw new Error("Microsoft OAuth is not configured. Set MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET.");
        }
        await connectMicrosoftTeamsDev();
      } else {
        if (zoomOauthEnabled) {
          window.location.href = await startZoomOAuth();
          return;
        }
        if (!devIntegrationsAllowed) {
          throw new Error("Zoom OAuth is not configured. Set ZOOM_CLIENT_ID and ZOOM_CLIENT_SECRET.");
        }
        await connectZoomDev();
      }
      await loadIntegrations();
      setCatalogOpen(false);
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

  const connectors: { id: string; connected: boolean; card: ReactNode }[] = [
    {
      id: "google_workspace",
      connected: Boolean(workspace?.connected),
      card: (
        <WorkspaceCard
          title="Google Workspace"
          description="Continuously connect approved Gmail, Google Calendars, shared drives, and folders."
          icon={<GoogleIcon className="size-6" />}
          integration={workspace}
          busy={busyProvider === "google_workspace"}
          connectLabel={oauthEnabled ? "Authorize Google" : "Authorize Google (dev)"}
          onConnect={() => void connectWorkspace("google_workspace")}
          onReconnect={oauthEnabled ? () => void connectWorkspace("google_workspace") : undefined}
          reconnectHint="Sign in to Google again to grant new permissions, such as scheduling meetings on Google Calendar. Your approved locations are kept."
          onManage={() => setWizardProvider("google_workspace")}
          onPause={(paused) => void setConnectionPaused("google_workspace", paused).then(loadIntegrations)}
          onDisconnect={() => void disconnectConnection("google_workspace").then(loadIntegrations)}
        />
      ),
    },
    {
      id: "zoom",
      connected: Boolean(zoom?.connected),
      card: (
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
      ),
    },
    {
      id: "microsoft_teams",
      connected: Boolean(teams?.connected),
      card: (
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
      ),
    },
    {
      id: "github",
      connected: Boolean(github?.connected),
      card: (
        <GitHubConnector
          connected={github?.connected}
          account={github?.account_email}
          selectedCount={github?.selected_resource_count}
          onChanged={() => {
            setCatalogOpen(false);
            void loadIntegrations();
          }}
        />
      ),
    },
    {
      id: "slack",
      connected: Boolean(slack?.connected) || slack?.status === "error",
      card: (
        <SlackConnectorCard
          status={slack}
          isAdmin={isAdmin}
          onSetup={() => {
            setCatalogOpen(false);
            setSlackOpen(true);
          }}
        />
      ),
    },
  ];
  const activeConnectors = connectors.filter((connector) => connector.connected);
  const availableConnectors = connectors.filter(
    (connector) => !connector.connected && (connector.id !== "slack" || isAdmin),
  );

  return (
    <div className="mx-auto w-full max-w-4xl space-y-6">
      {showHeading && (
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Connected workspaces</h2>
          <p className="mt-1 text-muted-foreground">
            Connect Google, Microsoft 365, Zoom, GitHub, or Slack. MindLoom only uses the locations and
            permissions you approve.
          </p>
        </div>
      )}

      {banner && <div className="rounded-md border border-brand-200 bg-brand-50 p-3 text-sm text-brand-900">{banner}</div>}
      {error && <div className="rounded-md border border-destructive/20 bg-destructive/10 p-3 text-sm text-destructive">{error}</div>}

      {slackOpen && (
        <SlackSetup
          onClose={() => setSlackOpen(false)}
          onChanged={() => void loadIntegrations()}
        />
      )}

      {wizardProvider && (
        <ConnectionWizard
          provider={wizardProvider}
          onCancel={() => setWizardProvider(null)}
          onComplete={(jobId) => {
            setWizardProvider(null);
            setBanner(`Initial import started${jobId ? ` (${jobId})` : ""}. MindLoom will keep this connection updated.`);
            void loadIntegrations();
          }}
        />
      )}

      {activeConnectors.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-lg border-2 border-dashed border-border px-6 py-10 text-center">
          <p className="text-sm text-muted-foreground">
            No connectors yet. Connect a workspace app to keep MindLoom in sync with it.
          </p>
          <PrimaryButton onClick={() => setCatalogOpen(true)}>
            <Plus className="size-4" />
            Add connector
          </PrimaryButton>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-muted-foreground">
              {activeConnectors.length} connected
            </p>
            {availableConnectors.length > 0 && (
              <SecondaryButton onClick={() => setCatalogOpen(true)}>
                <Plus className="size-4" />
                Add connector
              </SecondaryButton>
            )}
          </div>
          {activeConnectors.map((connector) => (
            <div key={connector.id}>{connector.card}</div>
          ))}
        </>
      )}

      {catalogOpen && (
        <ConnectorCatalog onClose={() => setCatalogOpen(false)} error={error}>
          {availableConnectors.length === 0 ? (
            <p className="text-sm text-muted-foreground">Every available connector is already connected.</p>
          ) : (
            availableConnectors.map((connector) => (
              <div key={connector.id}>{connector.card}</div>
            ))
          )}
        </ConnectorCatalog>
      )}

      {isAdmin && (
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
      )}

    </div>
  );
}

function ConnectorCatalog({
  onClose,
  error,
  children,
}: {
  onClose: () => void;
  error: string | null;
  children: ReactNode;
}) {
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-foreground/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="connector-catalog-heading"
      onClick={onClose}
    >
      <div
        className="flex max-h-[85dvh] w-full max-w-3xl flex-col rounded-lg border border-border bg-background shadow-lg"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
          <div>
            <h3 id="connector-catalog-heading" className="text-base font-semibold text-foreground">
              Add a connector
            </h3>
            <p className="mt-0.5 text-sm text-muted-foreground">
              MindLoom only uses the locations and permissions you approve.
            </p>
          </div>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-5">
          {error && (
            <div className="rounded-md border border-destructive/20 bg-destructive/10 p-3 text-sm text-destructive">
              {error}
            </div>
          )}
          {children}
        </div>
      </div>
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
  onReconnect,
  reconnectHint,
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
  /** Re-run authorization on a connected account, e.g. to grant newly added scopes. */
  onReconnect?: () => void;
  reconnectHint?: string;
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
            {onReconnect && (
              <SecondaryButton onClick={onReconnect} disabled={busy} title={reconnectHint}>
                {busy ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}Reconnect
              </SecondaryButton>
            )}
            <SecondaryButton onClick={onDisconnect}><Unplug className="size-4" />Disconnect</SecondaryButton>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
