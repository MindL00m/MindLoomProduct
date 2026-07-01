import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  CalendarDays,
  ExternalLink,
  FileText,
  Inbox,
  Loader2,
  MapPin,
  RefreshCw,
  MessageSquare,
  Unplug,
} from "lucide-react";
import { GoogleIcon } from "@/components/icons";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/StatusBadge";
import {
  connectGoogleCalendarDev,
  connectGoogleWorkspaceDev,
  connectMicrosoftTeamsDev,
  disconnectGoogleCalendar,
  fetchCalendarEvents,
  formatEventTime,
  listIntegrations,
  startGoogleWorkspaceOAuth,
  startGoogleCalendarOAuth,
  startMicrosoftTeamsOAuth,
  syncDriveNow,
  syncGmailNow,
  syncMicrosoftTeamsNow,
  type CalendarEvent,
  type IntegrationInfo,
  watchMicrosoftTeams,
  watchDrive,
  watchGmail,
} from "@/services/integrations";
import { cn } from "@/lib/utils";

interface AppsViewProps {
  /** Set when returning from OAuth callback. */
  oauthStatus?: "connected" | "error" | null;
  oauthError?: string | null;
  onOAuthHandled?: () => void;
}

export default function AppsView({
  oauthStatus,
  oauthError,
  onOAuthHandled,
}: AppsViewProps) {
  const [integrations, setIntegrations] = useState<IntegrationInfo[]>([]);
  const [oauthEnabled, setOauthEnabled] = useState(false);
  const [microsoftOauthEnabled, setMicrosoftOauthEnabled] = useState(false);
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [accountEmail, setAccountEmail] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [workspaceBusy, setWorkspaceBusy] = useState<string | null>(null);
  const [teamsBusy, setTeamsBusy] = useState<string | null>(null);
  const [teamId, setTeamId] = useState("");
  const [channelId, setChannelId] = useState("");
  const [eventsLoading, setEventsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const calendar = integrations.find((i) => i.provider === "google_calendar");
  const workspace = integrations.find((i) => i.provider === "google_workspace");
  const teams = integrations.find((i) => i.provider === "microsoft_teams");
  const isConnected = calendar?.connected ?? false;
  const workspaceConnected = workspace?.connected ?? false;
  const teamsConnected = teams?.connected ?? false;

  const loadIntegrations = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listIntegrations();
      setIntegrations(data.integrations);
      setOauthEnabled(data.oauth_enabled);
      setMicrosoftOauthEnabled(data.microsoft_oauth_enabled);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load apps.");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadEvents = useCallback(async () => {
    setEventsLoading(true);
    try {
      const data = await fetchCalendarEvents();
      setEvents(data.events);
      setAccountEmail(data.account_email ?? null);
    } catch {
      setEvents([]);
    } finally {
      setEventsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadIntegrations();
  }, [loadIntegrations]);

  useEffect(() => {
    if (isConnected) void loadEvents();
    else {
      setEvents([]);
      setAccountEmail(null);
    }
  }, [isConnected, loadEvents]);

  useEffect(() => {
    if (oauthStatus === "connected") {
      setBanner("Google Calendar connected successfully.");
      onOAuthHandled?.();
      void loadIntegrations();
    } else if (oauthStatus === "error") {
      setBanner(oauthError ?? "Google sign-in was cancelled or failed.");
      onOAuthHandled?.();
    }
  }, [oauthStatus, oauthError, onOAuthHandled, loadIntegrations]);

  async function handleConnect() {
    setConnecting(true);
    setError(null);
    setBanner(null);
    try {
      if (oauthEnabled) {
        const url = await startGoogleCalendarOAuth();
        window.location.href = url;
        return;
      }
      await connectGoogleCalendarDev();
      await loadIntegrations();
      setBanner("Google Calendar connected (development mode).");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Connection failed.");
    } finally {
      setConnecting(false);
    }
  }

  async function handleDisconnect() {
    setConnecting(true);
    setError(null);
    setBanner(null);
    try {
      await disconnectGoogleCalendar();
      await loadIntegrations();
      setBanner("Google Calendar disconnected.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Disconnect failed.");
    } finally {
      setConnecting(false);
    }
  }

  async function handleWorkspaceConnect() {
    setWorkspaceBusy("connect");
    setError(null);
    setBanner(null);
    try {
      if (oauthEnabled) {
        const url = await startGoogleWorkspaceOAuth();
        window.location.href = url;
        return;
      }
      await connectGoogleWorkspaceDev();
      await loadIntegrations();
      setBanner("Google Workspace connected (development mode).");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Workspace connection failed.");
    } finally {
      setWorkspaceBusy(null);
    }
  }

  async function handleWorkspaceAction(action: "gmail-watch" | "drive-watch" | "gmail-sync" | "drive-sync") {
    setWorkspaceBusy(action);
    setError(null);
    setBanner(null);
    try {
      if (action === "gmail-watch") {
        await watchGmail();
        setBanner("Gmail inbox watch started.");
      } else if (action === "drive-watch") {
        await watchDrive();
        setBanner("Drive changes watch started.");
      } else if (action === "gmail-sync") {
        const job = await syncGmailNow();
        setBanner(`Gmail sync queued (${job.job_id}).`);
      } else {
        const job = await syncDriveNow();
        setBanner(`Drive sync queued (${job.job_id}).`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Workspace action failed.");
    } finally {
      setWorkspaceBusy(null);
    }
  }

  async function handleTeamsConnect() {
    setTeamsBusy("connect");
    setError(null);
    setBanner(null);
    try {
      if (microsoftOauthEnabled) {
        const url = await startMicrosoftTeamsOAuth();
        window.location.href = url;
        return;
      }
      await connectMicrosoftTeamsDev();
      await loadIntegrations();
      setBanner("Microsoft Teams connected (development mode).");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Teams connection failed.");
    } finally {
      setTeamsBusy(null);
    }
  }

  async function handleTeamsWatch() {
    if (!teamId.trim() || !channelId.trim()) {
      setError("Enter both a Team ID and Channel ID first.");
      return;
    }
    setTeamsBusy("watch");
    setError(null);
    setBanner(null);
    try {
      const result = await watchMicrosoftTeams({
        team_id: teamId.trim(),
        channel_id: channelId.trim(),
      });
      setBanner(`Teams watch started for ${result.resource}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Teams watch failed.");
    } finally {
      setTeamsBusy(null);
    }
  }

  async function handleTeamsSync() {
    setTeamsBusy("sync");
    setError(null);
    setBanner(null);
    try {
      const job = await syncMicrosoftTeamsNow();
      setBanner(`Teams sync queued (${job.job_id}).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Teams sync failed.");
    } finally {
      setTeamsBusy(null);
    }
  }

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center py-16">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight">Apps</h2>
        <p className="mt-1 text-muted-foreground">
          Connect third-party apps to your workspace. Calendar data stays scoped
          to your account.
        </p>
      </div>

      {banner && (
        <div className="rounded-md border border-brand-200 bg-brand-50 px-4 py-3 text-sm text-brand-900">
          {banner}
        </div>
      )}

      {error && (
        <div className="rounded-md border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      <Card>
        <CardHeader className="flex-row items-start justify-between gap-4 space-y-0">
          <div className="flex items-start gap-3">
            <span className="flex size-11 shrink-0 items-center justify-center rounded-lg border border-border bg-background">
              <GoogleIcon className="size-6" />
            </span>
            <div>
              <CardTitle className="text-lg">Google Calendar</CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                View upcoming events from your primary calendar. Read-only access
                via Google sign-in.
              </p>
            </div>
          </div>
          <StatusBadge tone={isConnected ? "healthy" : "neutral"} dot>
            {isConnected ? "Connected" : "Not connected"}
          </StatusBadge>
        </CardHeader>

        <CardContent className="space-y-4">
          {isConnected && (
            <p className="text-sm text-muted-foreground">
              Signed in as{" "}
              <span className="font-medium text-foreground">
                {calendar?.account_email ?? accountEmail ?? "Google account"}
              </span>
            </p>
          )}

          <div className="flex flex-wrap gap-2">
            {!isConnected ? (
              <PrimaryButton onClick={() => void handleConnect()} disabled={connecting}>
                {connecting ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <GoogleIcon className="size-4" />
                )}
                {oauthEnabled ? "Connect with Google" : "Connect (dev mode)"}
              </PrimaryButton>
            ) : (
              <SecondaryButton onClick={() => void handleDisconnect()} disabled={connecting}>
                {connecting ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Unplug className="size-4" />
                )}
                Disconnect
              </SecondaryButton>
            )}
          </div>

          {!oauthEnabled && !isConnected && (
            <p className="text-xs text-muted-foreground">
              Google OAuth credentials are not configured on the server. Dev mode
              uses your session email and shows sample events.
            </p>
          )}

          {isConnected && (
            <div className="border-t border-border pt-4">
              <div className="mb-3 flex items-center gap-2">
                <CalendarDays className="size-4 text-muted-foreground" />
                <h3 className="text-sm font-semibold">Upcoming events</h3>
                {eventsLoading && <Loader2 className="size-3.5 animate-spin text-muted-foreground" />}
              </div>

              {eventsLoading && events.length === 0 ? (
                <p className="text-sm text-muted-foreground">Loading events…</p>
              ) : events.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No upcoming events in the next two weeks.
                </p>
              ) : (
                <ul className="divide-y divide-border rounded-md border border-border">
                  {events.map((event) => (
                    <EventRow key={event.id} event={event} />
                  ))}
                </ul>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex-row items-start justify-between gap-4 space-y-0">
          <div className="flex items-start gap-3">
            <span className="flex size-11 shrink-0 items-center justify-center rounded-lg border border-border bg-background">
              <MessageSquare className="size-6" />
            </span>
            <div>
              <CardTitle className="text-lg">Microsoft Teams</CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                Connect Teams, sync recent channel messages, and subscribe to one
                channel at a time for push updates.
              </p>
            </div>
          </div>
          <StatusBadge tone={teamsConnected ? "healthy" : "neutral"} dot>
            {teamsConnected ? "Connected" : "Not connected"}
          </StatusBadge>
        </CardHeader>

        <CardContent className="space-y-4">
          {teamsConnected && (
            <p className="text-sm text-muted-foreground">
              Signed in as{" "}
              <span className="font-medium text-foreground">
                {teams?.account_email ?? "Microsoft account"}
              </span>
            </p>
          )}

          {!teamsConnected ? (
            <PrimaryButton onClick={() => void handleTeamsConnect()} disabled={teamsBusy === "connect"}>
              {teamsBusy === "connect" ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <MessageSquare className="size-4" />
              )}
              {microsoftOauthEnabled ? "Connect Teams" : "Connect Teams (dev)"}
            </PrimaryButton>
          ) : (
            <div className="space-y-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <Input
                  value={teamId}
                  onChange={(e) => setTeamId(e.target.value)}
                  placeholder="Team ID"
                />
                <Input
                  value={channelId}
                  onChange={(e) => setChannelId(e.target.value)}
                  placeholder="Channel ID"
                />
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <WorkspaceAction
                  icon={<RefreshCw className="size-4" />}
                  label="Sync Teams Now"
                  busy={teamsBusy === "sync"}
                  onClick={() => void handleTeamsSync()}
                />
                <WorkspaceAction
                  icon={<MessageSquare className="size-4" />}
                  label="Watch Channel"
                  busy={teamsBusy === "watch"}
                  onClick={() => void handleTeamsWatch()}
                />
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex-row items-start justify-between gap-4 space-y-0">
          <div className="flex items-start gap-3">
            <span className="flex size-11 shrink-0 items-center justify-center rounded-lg border border-border bg-background">
              <GoogleIcon className="size-6" />
            </span>
            <div>
              <CardTitle className="text-lg">Google Workspace</CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                Sync Gmail inbox updates and Drive Docs, Sheets, and Slides into
                the knowledge graph.
              </p>
            </div>
          </div>
          <StatusBadge tone={workspaceConnected ? "healthy" : "neutral"} dot>
            {workspaceConnected ? "Connected" : "Not connected"}
          </StatusBadge>
        </CardHeader>

        <CardContent className="space-y-4">
          {workspaceConnected && (
            <p className="text-sm text-muted-foreground">
              Signed in as{" "}
              <span className="font-medium text-foreground">
                {workspace?.account_email ?? "Google account"}
              </span>
            </p>
          )}

          {!workspaceConnected ? (
            <PrimaryButton
              onClick={() => void handleWorkspaceConnect()}
              disabled={workspaceBusy === "connect"}
            >
              {workspaceBusy === "connect" ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <GoogleIcon className="size-4" />
              )}
              {oauthEnabled ? "Connect Workspace" : "Connect Workspace (dev)"}
            </PrimaryButton>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              <WorkspaceAction
                icon={<Inbox className="size-4" />}
                label="Start Gmail Watch"
                busy={workspaceBusy === "gmail-watch"}
                onClick={() => void handleWorkspaceAction("gmail-watch")}
              />
              <WorkspaceAction
                icon={<FileText className="size-4" />}
                label="Start Drive Watch"
                busy={workspaceBusy === "drive-watch"}
                onClick={() => void handleWorkspaceAction("drive-watch")}
              />
              <WorkspaceAction
                icon={<RefreshCw className="size-4" />}
                label="Sync Gmail Now"
                busy={workspaceBusy === "gmail-sync"}
                onClick={() => void handleWorkspaceAction("gmail-sync")}
              />
              <WorkspaceAction
                icon={<RefreshCw className="size-4" />}
                label="Sync Drive Now"
                busy={workspaceBusy === "drive-sync"}
                onClick={() => void handleWorkspaceAction("drive-sync")}
              />
            </div>
          )}

          {!oauthEnabled && !workspaceConnected && (
            <p className="text-xs text-muted-foreground">
              Dev mode records the connection but does not call Gmail or Drive.
            </p>
          )}
          {!microsoftOauthEnabled && !teamsConnected && (
            <p className="text-xs text-muted-foreground">
              Dev mode records the connection but does not call Microsoft Graph.
            </p>
          )}
        </CardContent>
      </Card>

      <p className="text-center text-xs text-muted-foreground">
        More integrations coming soon — Slack, Notion, and others.
      </p>
    </div>
  );
}

function WorkspaceAction({
  icon,
  label,
  busy,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  busy: boolean;
  onClick: () => void;
}) {
  return (
    <SecondaryButton onClick={onClick} disabled={busy}>
      {busy ? <Loader2 className="size-4 animate-spin" /> : icon}
      {label}
    </SecondaryButton>
  );
}

function EventRow({ event }: { event: CalendarEvent }) {
  return (
    <li className="flex items-start gap-3 px-4 py-3">
      <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-brand-50 text-brand-700">
        <CalendarDays className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="font-medium">{event.title}</p>
        <p className="mt-0.5 text-sm text-muted-foreground">{formatEventTime(event)}</p>
        {event.location && (
          <p className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
            <MapPin className="size-3 shrink-0" />
            <span className="truncate">{event.location}</span>
          </p>
        )}
      </div>
      {event.html_link && (
        <a
          href={event.html_link}
          target="_blank"
          rel="noopener noreferrer"
          className={cn(
            "shrink-0 rounded p-1.5 text-muted-foreground transition-colors",
            "hover:bg-muted hover:text-foreground",
          )}
          aria-label="Open in Google Calendar"
        >
          <ExternalLink className="size-4" />
        </a>
      )}
    </li>
  );
}
