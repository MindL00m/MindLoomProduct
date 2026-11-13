import { useCallback, useEffect, useState } from "react";
import { Database, Download, Loader2, Unplug } from "lucide-react";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/StatusBadge";
import {
  connectNetSuite,
  disconnectNetSuite,
  getNetSuiteStatus,
  startNetSuiteSetup,
  type NetSuiteStatus,
} from "@/services/netsuite";

const inputClass = "mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm";

function downloadCertificate(pem: string) {
  const url = URL.createObjectURL(new Blob([pem], { type: "application/x-pem-file" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "mindloom-netsuite.pem";
  link.click();
  URL.revokeObjectURL(url);
}

/**
 * NetSuite for automations. Two steps: MindLoom makes a certificate, the person uploads it
 * in NetSuite and maps it to a role, then gives MindLoom the two IDs NetSuite shows. What
 * automations can read or change is whatever that NetSuite role allows.
 */
export default function NetSuiteConnector({ onChanged }: { onChanged: () => void }) {
  const [status, setStatus] = useState<NetSuiteStatus | null>(null);
  const [accountId, setAccountId] = useState("");
  const [clientId, setClientId] = useState("");
  const [certificateId, setCertificateId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => setStatus(await getNetSuiteStatus()), []);

  useEffect(() => {
    void load().catch((err) => setError(err instanceof Error ? err.message : "Could not load NetSuite."));
  }, [load]);

  const act = async (work: () => Promise<NetSuiteStatus>) => {
    setBusy(true);
    setError("");
    try {
      setStatus(await work());
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  const connected = Boolean(status?.connected);
  const pending = status?.status === "pending";
  const expires = status?.certificate_expires_at
    ? new Date(status.certificate_expires_at).toLocaleDateString()
    : "";

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="flex size-11 items-center justify-center rounded-lg border">
            <Database className="size-6" />
          </span>
          <div>
            <CardTitle className="text-lg">NetSuite</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              {connected
                ? "Automations can read NetSuite, and create or change records after you approve."
                : "Let automations read NetSuite and, with your approval, create or change records."}
            </p>
          </div>
        </div>
        <StatusBadge tone={connected ? "healthy" : "neutral"} dot>
          {connected ? "Active" : pending ? "Setup started" : "Not connected"}
        </StatusBadge>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {error && <p className="text-destructive">{error}</p>}

        {connected && (
          <p className="text-muted-foreground">
            Account {status?.account_id}. The certificate runs out on {expires}; connect again before then.
          </p>
        )}

        {!connected && !pending && (
          <>
            <label className="block">
              <span className="font-medium">NetSuite account ID</span>
              <input
                value={accountId}
                onChange={(e) => setAccountId(e.target.value)}
                placeholder="1234567 or 1234567_SB1"
                className={inputClass}
              />
              <span className="mt-1 block text-xs text-muted-foreground">
                In NetSuite: Setup &gt; Company &gt; Company Information.
              </span>
            </label>
            <PrimaryButton disabled={busy || !accountId.trim()} onClick={() => void act(() => startNetSuiteSetup(accountId))}>
              {busy && <Loader2 className="size-4 animate-spin" />}
              Start setup
            </PrimaryButton>
          </>
        )}

        {pending && (
          <>
            <p className="text-muted-foreground">
              Account {status?.account_id}. A NetSuite administrator does these steps once:
            </p>
            <ol className="list-decimal space-y-1 pl-5 text-muted-foreground">
              <li>
                Setup &gt; Company &gt; Enable Features &gt; SuiteCloud: turn on REST Web Services and OAuth 2.0.
              </li>
              <li>
                Setup &gt; Integration &gt; Manage Integrations &gt; New: tick Client Credentials (Machine to
                Machine) Grant and REST Web Services. Save, and copy the client ID.
              </li>
              <li>
                Give a role only what the automations need, plus REST Web Services and Log in using OAuth 2.0
                Access Tokens. Do not use the Administrator role.
              </li>
              <li>
                Setup &gt; Integration &gt; OAuth 2.0 Client Credentials (M2M) Setup &gt; Create New: choose the
                user, that role and the integration, and upload the certificate below. Copy the certificate ID.
              </li>
            </ol>
            <SecondaryButton onClick={() => status?.certificate_pem && downloadCertificate(status.certificate_pem)}>
              <Download className="size-4" />
              Download certificate
            </SecondaryButton>
            <p className="text-xs text-muted-foreground">
              This is the public half, safe to upload. It runs out on {expires}.
            </p>
            <label className="block">
              <span className="font-medium">Client ID</span>
              <input value={clientId} onChange={(e) => setClientId(e.target.value)} className={inputClass} />
            </label>
            <label className="block">
              <span className="font-medium">Certificate ID</span>
              <input value={certificateId} onChange={(e) => setCertificateId(e.target.value)} className={inputClass} />
            </label>
            <div className="flex flex-wrap gap-2">
              <PrimaryButton
                disabled={busy || !clientId.trim() || !certificateId.trim()}
                onClick={() => void act(() => connectNetSuite(clientId.trim(), certificateId.trim()))}
              >
                {busy && <Loader2 className="size-4 animate-spin" />}
                Connect
              </PrimaryButton>
              <SecondaryButton disabled={busy} onClick={() => void act(disconnectNetSuite)}>
                Cancel setup
              </SecondaryButton>
            </div>
          </>
        )}

        {connected && (
          <SecondaryButton disabled={busy} onClick={() => void act(disconnectNetSuite)}>
            <Unplug className="size-4" />
            Disconnect
          </SecondaryButton>
        )}
      </CardContent>
    </Card>
  );
}
