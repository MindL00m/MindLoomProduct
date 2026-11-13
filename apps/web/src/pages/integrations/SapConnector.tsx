import { useCallback, useEffect, useState } from "react";
import { Boxes, Loader2, Unplug } from "lucide-react";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/StatusBadge";
import { connectSap, disconnectSap, getSapStatus, type SapStatus } from "@/services/sap";

const inputClass = "mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm";

/**
 * SAP S/4HANA for automations, through its OData APIs. The person gives the system's
 * address and a technical user; what automations can read or change is whatever that
 * SAP user is allowed.
 */
export default function SapConnector({ onChanged }: { onChanged: () => void }) {
  const [status, setStatus] = useState<SapStatus | null>(null);
  const [form, setForm] = useState({ base_url: "", username: "", password: "", test_service: "", sap_client: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => setStatus(await getSapStatus()), []);

  useEffect(() => {
    void load().catch((err) => setError(err instanceof Error ? err.message : "Could not load SAP."));
  }, [load]);

  const act = async (work: () => Promise<SapStatus>) => {
    setBusy(true);
    setError("");
    try {
      setStatus(await work());
      setForm((cur) => ({ ...cur, password: "" }));
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  const field = (key: keyof typeof form) => ({
    value: form[key],
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => setForm((cur) => ({ ...cur, [key]: e.target.value })),
    className: inputClass,
  });
  const connected = Boolean(status?.connected);
  const ready = form.base_url.trim() && form.username.trim() && form.password && form.test_service.trim();

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="flex size-11 items-center justify-center rounded-lg border">
            <Boxes className="size-6" />
          </span>
          <div>
            <CardTitle className="text-lg">SAP</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              {connected
                ? "Automations can read SAP S/4HANA, and create or change records after you approve."
                : "Let automations read SAP S/4HANA and, with your approval, create or change records."}
            </p>
          </div>
        </div>
        <StatusBadge tone={connected ? "healthy" : "neutral"} dot>
          {connected ? "Active" : "Not connected"}
        </StatusBadge>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {error && <p className="text-destructive">{error}</p>}

        {connected ? (
          <>
            <p className="break-all text-muted-foreground">
              {status?.base_url}
              {status?.sap_client ? `, client ${status.sap_client}` : ""}, as {status?.username}.
            </p>
            <SecondaryButton disabled={busy} onClick={() => void act(disconnectSap)}>
              <Unplug className="size-4" />
              Disconnect
            </SecondaryButton>
          </>
        ) : (
          <>
            <p className="text-muted-foreground">
              Use a technical user made for MindLoom, not a person&apos;s own sign-in. In S/4HANA Cloud: create
              a communication user and system, then a communication arrangement for each API automations
              should use. The system must be reachable from the internet over https.
            </p>
            <label className="block">
              <span className="font-medium">SAP address</span>
              <input {...field("base_url")} placeholder="https://my123456-api.s4hana.cloud.sap" />
            </label>
            <label className="block">
              <span className="font-medium">User name</span>
              <input {...field("username")} autoComplete="off" />
            </label>
            <label className="block">
              <span className="font-medium">Password</span>
              <input {...field("password")} type="password" autoComplete="new-password" />
            </label>
            <label className="block">
              <span className="font-medium">A service this user may use</span>
              <input {...field("test_service")} placeholder="API_BUSINESS_PARTNER" />
              <span className="mt-1 block text-xs text-muted-foreground">
                MindLoom reads it once to check the sign-in.
              </span>
            </label>
            <label className="block">
              <span className="font-medium">SAP client (on-premise only)</span>
              <input {...field("sap_client")} placeholder="100" maxLength={3} />
            </label>
            <PrimaryButton
              disabled={busy || !ready}
              onClick={() => void act(() => connectSap({ ...form, base_url: form.base_url.trim(), username: form.username.trim() }))}
            >
              {busy && <Loader2 className="size-4 animate-spin" />}
              Connect
            </PrimaryButton>
          </>
        )}
      </CardContent>
    </Card>
  );
}
