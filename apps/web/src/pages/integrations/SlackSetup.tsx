import { useEffect, useState } from "react";
import { Hash, Loader2, Unplug } from "lucide-react";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/StatusBadge";
import {
  createSlackDraft,
  disconnectSlack,
  getSlackStatus,
  listSlackChannels,
  saveSlackCredentials,
  saveSlackSettings,
  sendSlackTest,
  startSlackBackfill,
  type SlackChannel,
  type SlackStatus,
} from "@/services/slack";

const STEPS = ["Create the app", "Install it", "Choose channels", "Behaviour", "Status"];

const inputClass = "mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm";

export function SlackIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <path fill="#E01E5A" d="M6 15a2 2 0 0 1-2 2 2 2 0 1 1 0-4h2v2zm1 0a2 2 0 0 1 2-2 2 2 0 0 1 2 2v5a2 2 0 1 1-4 0v-5z" />
      <path fill="#36C5F0" d="M9 6a2 2 0 0 1-2-2 2 2 0 1 1 4 0v2H9zm0 1a2 2 0 0 1 2 2 2 2 0 0 1-2 2H4a2 2 0 1 1 0-4h5z" />
      <path fill="#2EB67D" d="M18 9a2 2 0 0 1 2-2 2 2 0 1 1 0 4h-2V9zm-1 0a2 2 0 0 1-2 2 2 2 0 0 1-2-2V4a2 2 0 1 1 4 0v5z" />
      <path fill="#ECB22E" d="M15 18a2 2 0 0 1 2 2 2 2 0 1 1-4 0v-2h2zm0-1a2 2 0 0 1-2-2 2 2 0 0 1 2-2h5a2 2 0 1 1 0 4h-5z" />
    </svg>
  );
}

function defaultSettings(status: SlackStatus | null) {
  return {
    ingest_channel_ids: status?.settings?.ingest_channel_ids ?? [],
    ingest_mention_threads: status?.settings?.ingest_mention_threads ?? true,
    answer_mentions: status?.settings?.answer_mentions ?? true,
    unlinked_user_policy: status?.settings?.unlinked_user_policy ?? "ignore" as const,
  };
}

export function SlackConnectorCard({
  status,
  isAdmin,
  onSetup,
}: {
  status: SlackStatus | null;
  isAdmin: boolean;
  onSetup: () => void;
}) {
  const connected = Boolean(status?.connected);
  const errored = status?.status === "error";
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="flex size-11 items-center justify-center rounded-lg border">
            <SlackIcon className="size-6" />
          </span>
          <div>
            <CardTitle className="text-lg">Slack</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              {connected
                ? "Mention @Loombot in Slack to ask a question or add a thread to MindLoom."
                : "Add a Loombot that reads chosen channels and answers @mentions from MindLoom."}
            </p>
          </div>
        </div>
        <StatusBadge tone={connected ? "healthy" : errored ? "brand" : "neutral"} dot>
          {connected ? "Active" : errored ? "Needs attention" : "Not connected"}
        </StatusBadge>
      </CardHeader>
      <CardContent className="space-y-3">
        {connected && (
          <p className="text-sm text-muted-foreground">
            {status?.team_name ? `Workspace ${status.team_name}. ` : ""}
            {status?.last_event_at
              ? `Last event ${new Date(status.last_event_at).toLocaleString()}.`
              : "No events yet."}
          </p>
        )}
        {status?.last_error && <p className="text-sm text-destructive">{status.last_error}</p>}
        {isAdmin ? (
          <PrimaryButton onClick={onSetup}>
            <Hash className="size-4" />
            {connected || errored ? "Manage" : "Set up"}
          </PrimaryButton>
        ) : (
          connected && (
            <p className="text-sm text-muted-foreground">
              Mention @Loombot in Slack to ask or share knowledge.
            </p>
          )
        )}
      </CardContent>
    </Card>
  );
}

export function SlackSetup({
  onClose,
  onChanged,
}: {
  onClose: () => void;
  onChanged: () => void;
}) {
  const [step, setStep] = useState(0);
  const [status, setStatus] = useState<SlackStatus | null>(null);
  const [token, setToken] = useState("");
  const [secret, setSecret] = useState("");
  const [channels, setChannels] = useState<SlackChannel[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [ingestThreads, setIngestThreads] = useState(true);
  const [answerMentions, setAnswerMentions] = useState(true);
  const [policy, setPolicy] = useState<"ignore" | "org">("ignore");
  const [days, setDays] = useState<1 | 7 | 30>(7);
  const [testChannel, setTestChannel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (step !== 2 && step !== 4) return;
    if (status?.status !== "active" && status?.status !== "error") return;
    void listSlackChannels().then(setChannels).catch(() => undefined);
  }, [step, status?.connected]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setBusy(true);
      try {
        let current = await getSlackStatus();
        // Recreating the draft restarts Slack's unsigned URL check without changing the event URL.
        if (!current.install_id || current.status === "draft") current = await createSlackDraft();
        if (cancelled) return;
        apply(current);
        setStep(current.status === "active" || current.status === "error" ? 4 : 0);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not start Slack setup.");
      } finally {
        if (!cancelled) setBusy(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  function apply(next: SlackStatus) {
    const settings = defaultSettings(next);
    setStatus(next);
    setSelected(settings.ingest_channel_ids);
    setIngestThreads(settings.ingest_mention_threads);
    setAnswerMentions(settings.answer_mentions);
    setPolicy(settings.unlinked_user_policy);
    setTestChannel((current) => current || settings.ingest_channel_ids[0] || "");
  }

  async function copyManifest() {
    if (!status?.manifest) return;
    await navigator.clipboard.writeText(JSON.stringify(status.manifest, null, 2));
    setCopied(true);
  }

  async function saveCredentials() {
    setBusy(true);
    setError(null);
    try {
      const next = await saveSlackCredentials(token.trim(), secret.trim());
      apply(next);
      setToken("");
      setSecret("");
      onChanged();
      if ((next.missing_scopes ?? []).length === 0) setStep(2);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the credentials.");
    } finally {
      setBusy(false);
    }
  }

  async function loadChannels() {
    setBusy(true);
    setError(null);
    try {
      setChannels(await listSlackChannels());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not list channels.");
    } finally {
      setBusy(false);
    }
  }

  async function saveBehaviour(advance: boolean) {
    setBusy(true);
    setError(null);
    try {
      const next = await saveSlackSettings({
        ingest_channel_ids: selected,
        ingest_mention_threads: ingestThreads,
        answer_mentions: answerMentions,
        unlinked_user_policy: policy,
      });
      apply(next);
      onChanged();
      if (advance) setStep((current) => current + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save Slack settings.");
    } finally {
      setBusy(false);
    }
  }

  async function backfill() {
    setBusy(true);
    setError(null);
    try {
      await startSlackBackfill(days);
      setNotice(`Importing the last ${days === 1 ? "day" : `${days} days`} of selected channels.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the import.");
    } finally {
      setBusy(false);
    }
  }

  async function testMessage() {
    if (!testChannel) return;
    setBusy(true);
    setError(null);
    try {
      await sendSlackTest(testChannel);
      setNotice("Test message sent.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send the test message.");
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    if (!window.confirm("Disconnect Slack? Messages already saved in MindLoom stay.")) return;
    setBusy(true);
    setError(null);
    try {
      await disconnectSlack();
      onChanged();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not disconnect Slack.");
      setBusy(false);
    }
  }

  const missing = status?.missing_scopes ?? [];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="slack-setup-heading"
      onClick={onClose}
    >
      <div
        className="flex max-h-[88dvh] w-full max-w-2xl flex-col rounded-lg border border-border bg-background shadow-lg"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="border-b border-border px-5 py-4">
          <h3 id="slack-setup-heading" className="text-base font-semibold">Set up Loombot for Slack</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            Step {step + 1} of {STEPS.length}: {STEPS[step]}
          </p>
        </div>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-5 text-sm">
          {status?.public_api_warning && step === 0 && (
            <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-950">
              {status.public_api_warning}
            </p>
          )}
          {error && <p className="rounded-md border border-destructive/20 bg-destructive/10 px-3 py-2 text-destructive">{error}</p>}
          {notice && <p className="rounded-md border border-brand-200 bg-brand-50 px-3 py-2 text-brand-900">{notice}</p>}

          {step === 0 && (
            <div className="space-y-3">
              <p>Create a Slack app for this organization from the manifest MindLoom generated. It contains no secrets.</p>
              <ol className="list-decimal space-y-1 pl-5 text-muted-foreground">
                <li>Open Slack’s “Create an app” page and choose <span className="text-foreground">From a manifest</span>.</li>
                <li>Pick the workspace Loombot should join.</li>
                <li>Paste the manifest below and create the app. Slack will check the event URL.</li>
              </ol>
              <pre className="max-h-56 overflow-auto rounded-md border border-border bg-muted/40 p-3 text-xs">
                {status?.manifest ? JSON.stringify(status.manifest, null, 2) : "Preparing the manifest…"}
              </pre>
              <div className="flex flex-wrap gap-2">
                <SecondaryButton onClick={() => void copyManifest()} disabled={!status?.manifest}>
                  {copied ? "Copied" : "Copy manifest"}
                </SecondaryButton>
                <a
                  className="inline-flex items-center rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"
                  href="https://api.slack.com/apps?new_app=1"
                  target="_blank"
                  rel="noreferrer"
                >
                  Open Slack
                </a>
              </div>
              {status?.event_url && (
                <p className="text-xs text-muted-foreground">Event URL: {status.event_url}</p>
              )}
            </div>
          )}

          {step === 1 && (
            <div className="space-y-3">
              <p>Install the app to the workspace, then paste the two values Slack shows you.</p>
              <ol className="list-decimal space-y-1 pl-5 text-muted-foreground">
                <li>Install to Workspace and allow the permissions.</li>
                <li>On <span className="text-foreground">OAuth & Permissions</span>, copy the Bot User OAuth Token (it starts with xoxb-).</li>
                <li>On <span className="text-foreground">Basic Information</span>, copy the Signing Secret.</li>
              </ol>
              <label className="block">
                Bot User OAuth Token
                <input className={inputClass} type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} />
              </label>
              <label className="block">
                Signing secret
                <input className={inputClass} type="password" autoComplete="off" value={secret} onChange={(event) => setSecret(event.target.value)} />
              </label>
              {status?.team_name && <p>Connected workspace: <span className="font-medium">{status.team_name}</span></p>}
              {missing.length > 0 && (
                <p className="text-destructive">
                  The token is missing {missing.join(", ")}. Reinstall the app from the manifest, then paste the new token.
                </p>
              )}
              <PrimaryButton onClick={() => void saveCredentials()} disabled={busy || token.trim().length < 10 || secret.trim().length < 8}>
                {busy ? <Loader2 className="size-4 animate-spin" /> : null}
                Check and save
              </PrimaryButton>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-3">
              <p>
                Loombot only reads channels it has been invited to. In Slack, open a channel and run <span className="font-medium">/invite @Loombot</span>, then refresh.
              </p>
              <SecondaryButton onClick={() => void loadChannels()} disabled={busy}>
                {busy ? <Loader2 className="size-4 animate-spin" /> : null}
                {channels.length ? "Refresh channels" : "Load channels"}
              </SecondaryButton>
              {channels.length === 0 ? (
                <p className="text-muted-foreground">No joined channels yet.</p>
              ) : (
                <ul className="max-h-64 space-y-1 overflow-auto rounded-md border border-border p-2">
                  {channels.map((channel) => (
                    <li key={channel.id}>
                      <label className="flex items-center gap-2 rounded px-2 py-1 hover:bg-muted/60">
                        <input
                          type="checkbox"
                          checked={selected.includes(channel.id)}
                          onChange={() => setSelected((current) => (
                            current.includes(channel.id)
                              ? current.filter((id) => id !== channel.id)
                              : [...current, channel.id]
                          ))}
                        />
                        <span>{channel.is_private ? "Private #" : "#"}{channel.name}</span>
                      </label>
                    </li>
                  ))}
                </ul>
              )}
              <p className="text-muted-foreground">{selected.length} channel{selected.length === 1 ? "" : "s"} selected for continuous import.</p>
            </div>
          )}

          {step === 3 && (
            <div className="space-y-3">
              <label className="flex items-start gap-2">
                <input type="checkbox" className="mt-1" checked={answerMentions} onChange={(event) => setAnswerMentions(event.target.checked)} />
                <span>Reply when someone @mentions Loombot, using the same knowledge as Ask. The answer only includes what that person can see in MindLoom.</span>
              </label>
              <label className="flex items-start gap-2">
                <input type="checkbox" className="mt-1" checked={ingestThreads} onChange={(event) => setIngestThreads(event.target.checked)} />
                <span>Also save the thread a mention is in, even if that channel is not selected above.</span>
              </label>
              <fieldset className="space-y-2">
                <legend className="font-medium">People without a MindLoom account</legend>
                <label className="flex items-center gap-2">
                  <input type="radio" name="unlinked" checked={policy === "ignore"} onChange={() => setPolicy("ignore")} />
                  Don’t answer them
                </label>
                <label className="flex items-center gap-2">
                  <input type="radio" name="unlinked" checked={policy === "org"} onChange={() => setPolicy("org")} />
                  Answer from organization-wide knowledge only
                </label>
              </fieldset>
              <label className="block">
                When importing history, include the last
                <select
                  className={`${inputClass} max-w-40`}
                  value={days}
                  onChange={(event) => setDays(Number(event.target.value) as 1 | 7 | 30)}
                >
                  <option value={1}>1 day</option>
                  <option value={7}>7 days</option>
                  <option value={30}>30 days</option>
                </select>
              </label>
            </div>
          )}

          {step === 4 && (
            <div className="space-y-3">
              <p>
                {status?.team_name ? <span className="font-medium">{status.team_name}</span> : "Slack"} is connected.
                {status?.last_event_at
                  ? ` Last event ${new Date(status.last_event_at).toLocaleString()}.`
                  : " Waiting for the first event."}
              </p>
              {status?.last_error && <p className="text-destructive">{status.last_error}</p>}
              <label className="block">
                Send a test message to
                <select className={inputClass} value={testChannel} onChange={(event) => setTestChannel(event.target.value)}>
                  <option value="">Choose a channel</option>
                  {selected.map((id) => {
                    const channel = channels.find((item) => item.id === id);
                    return <option key={id} value={id}>{channel ? `#${channel.name}` : id}</option>;
                  })}
                </select>
              </label>
              <div className="flex flex-wrap gap-2">
                <SecondaryButton onClick={() => void testMessage()} disabled={busy || !testChannel}>Send test message</SecondaryButton>
                <SecondaryButton onClick={() => void backfill()} disabled={busy || selected.length === 0}>
                  Import last {days === 1 ? "day" : `${days} days`}
                </SecondaryButton>
                <SecondaryButton onClick={() => void disconnect()} disabled={busy}>
                  <Unplug className="size-4" />Disconnect
                </SecondaryButton>
              </div>
            </div>
          )}
        </div>
        <div className="flex items-center justify-between gap-2 border-t border-border px-5 py-4">
          <SecondaryButton onClick={onClose}>Close</SecondaryButton>
          <div className="flex gap-2">
            {step > 0 && (
              <SecondaryButton onClick={() => setStep((current) => current - 1)} disabled={busy}>Back</SecondaryButton>
            )}
            {step === 0 && (
              <PrimaryButton onClick={() => setStep(1)}>I created the app</PrimaryButton>
            )}
            {step === 2 && (
              <PrimaryButton onClick={() => void saveBehaviour(true)} disabled={busy}>Save channels</PrimaryButton>
            )}
            {step === 3 && (
              <PrimaryButton onClick={() => void saveBehaviour(true)} disabled={busy}>Save and finish</PrimaryButton>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
