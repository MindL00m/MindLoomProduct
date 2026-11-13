import { useCallback, useEffect, useState } from "react";
import { Boxes, Loader2, Unplug } from "lucide-react";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/StatusBadge";
import { ErpChanges, ErpImports } from "@/pages/integrations/ErpImports";
import {
  connectSap,
  disconnectSap,
  getRelayStatus,
  getSapStatus,
  type RelayStatus,
  type SapAuth,
  type SapStatus,
  type SapSystem,
  type SapVia,
} from "@/services/sap";

const inputClass = "mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm";

const EMPTY = {
  base_url: "",
  username: "",
  password: "",
  test_service: "",
  sap_client: "",
  token_url: "",
  client_id: "",
  client_secret: "",
  certificate_pem: "",
  private_key_pem: "",
  company_db: "",
  relay_code: "",
};
// Never kept in the form after a try.
const SECRETS = { password: "", client_secret: "", private_key_pem: "" };

const SYSTEM_LABEL: Record<SapSystem, string> = { s4hana: "S/4HANA", business_one: "Business One" };
const AUTH_LABEL: Record<SapAuth, string> = {
  basic: "a user and password",
  oauth: "OAuth",
  certificate: "a certificate",
};

/**
 * SAP for Ask and automations. Two systems (S/4HANA through OData, Business One through
 * its Service Layer), three ways to sign in to S/4HANA, and two ways to reach it: from
 * MindLoom's servers, or through MindLoom for Windows on a PC inside the company network.
 */
export default function SapConnector({ onChanged }: { onChanged: () => void }) {
  const [status, setStatus] = useState<SapStatus | null>(null);
  const [relay, setRelay] = useState<RelayStatus | null>(null);
  const [system, setSystem] = useState<SapSystem>("s4hana");
  const [auth, setAuth] = useState<SapAuth>("basic");
  const [via, setVia] = useState<SapVia>("direct");
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => setStatus(await getSapStatus()), []);

  useEffect(() => {
    void load().catch((err) => setError(err instanceof Error ? err.message : "Could not load SAP."));
  }, [load]);

  const usesRelay = via === "relay" || status?.via === "relay";
  useEffect(() => {
    if (!usesRelay) return;
    let live = true;
    const check = () => void getRelayStatus().then((value) => live && setRelay(value)).catch(() => undefined);
    check();
    const timer = window.setInterval(check, 15_000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [usesRelay]);

  const act = async (work: () => Promise<SapStatus>) => {
    setBusy(true);
    setError("");
    try {
      setStatus(await work());
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setForm((cur) => ({ ...cur, ...SECRETS }));
      setBusy(false);
    }
  };

  const field = (key: keyof typeof EMPTY) => ({
    value: form[key],
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setForm((cur) => ({ ...cur, [key]: e.target.value })),
    className: inputClass,
  });

  const b1 = system === "business_one";
  const mode: SapAuth = b1 ? "basic" : auth;
  const ready =
    form.base_url.trim() &&
    (via === "direct" || form.relay_code.trim()) &&
    (b1 ? form.company_db.trim() : form.test_service.trim()) &&
    (mode === "basic"
      ? form.username.trim() && form.password
      : mode === "oauth"
        ? form.token_url.trim() && form.client_id.trim() && form.client_secret
        : form.certificate_pem.trim() && form.private_key_pem.trim());

  const connect = () =>
    act(() =>
      connectSap({
        system,
        auth: mode,
        via,
        base_url: form.base_url.trim(),
        sap_client: b1 ? "" : form.sap_client.trim(),
        test_service: b1 ? "" : form.test_service.trim(),
        company_db: b1 ? form.company_db.trim() : "",
        relay_code: via === "relay" ? form.relay_code.trim() : "",
        ...(mode === "basic" ? { username: form.username.trim(), password: form.password } : {}),
        ...(mode === "oauth"
          ? { token_url: form.token_url.trim(), client_id: form.client_id.trim(), client_secret: form.client_secret }
          : {}),
        ...(mode === "certificate"
          ? { certificate_pem: form.certificate_pem, private_key_pem: form.private_key_pem }
          : {}),
      }),
    );

  const connected = Boolean(status?.connected);
  const relayLine = relay?.online
    ? "The relay is running."
    : "The relay is not running. Open MindLoom for Windows, signed in as you, on a PC inside your network that can reach SAP.";

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
                ? "Ask and automations can read SAP, and create or change records after you approve."
                : "Let Ask and automations read SAP (S/4HANA or Business One) and, with your approval, create or change records."}
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
              {SYSTEM_LABEL[status?.system ?? "s4hana"]} at {status?.base_url}
              {status?.sap_client ? `, client ${status.sap_client}` : ""}
              {status?.company_db ? `, company ${status.company_db}` : ""}, signed in with{" "}
              {AUTH_LABEL[status?.auth ?? "basic"]}
              {status?.auth === "certificate" ? "" : ` as ${status?.username}`}
              {status?.via === "relay" ? ", through the relay" : ""}.
            </p>
            {status?.via === "relay" && (
              <p className={relay?.online ? "text-muted-foreground" : "text-destructive"}>{relayLine}</p>
            )}
            <SecondaryButton disabled={busy} onClick={() => void act(disconnectSap)}>
              <Unplug className="size-4" />
              Disconnect
            </SecondaryButton>
            <ErpImports provider="sap" />
            <ErpChanges provider="sap" />
          </>
        ) : (
          <>
            <label className="block">
              <span className="font-medium">SAP system</span>
              <select value={system} onChange={(e) => setSystem(e.target.value as SapSystem)} className={inputClass}>
                <option value="s4hana">S/4HANA (cloud or on-premise)</option>
                <option value="business_one">Business One</option>
              </select>
            </label>
            <label className="block">
              <span className="font-medium">How MindLoom reaches it</span>
              <select value={via} onChange={(e) => setVia(e.target.value as SapVia)} className={inputClass}>
                <option value="direct">Over the internet (public https address)</option>
                <option value="relay">Through MindLoom for Windows inside our network</option>
              </select>
            </label>
            {via === "relay" && (
              <>
                <p className={relay?.online ? "text-muted-foreground" : "text-destructive"}>
                  {relayLine} The first time, that PC asks whether it may call this address: approve it there,
                  then press Connect again.
                </p>
                <label className="block">
                  <span className="font-medium">Relay code</span>
                  <input {...field("relay_code")} placeholder="AB12-CD34" autoComplete="off" />
                  <span className="mt-1 block text-xs text-muted-foreground">
                    Shown by MindLoom for Windows on that PC. It proves the sign-in goes to your relay.
                  </span>
                </label>
              </>
            )}
            <label className="block">
              <span className="font-medium">SAP address</span>
              <input
                {...field("base_url")}
                placeholder={b1 ? "https://b1.example.com:50000" : "https://my123456-api.s4hana.cloud.sap"}
              />
            </label>

            {!b1 && (
              <label className="block">
                <span className="font-medium">Sign in with</span>
                <select value={auth} onChange={(e) => setAuth(e.target.value as SapAuth)} className={inputClass}>
                  <option value="basic">A technical user and password</option>
                  <option value="oauth">OAuth (client ID and secret)</option>
                  {via === "direct" && <option value="certificate">A client certificate</option>}
                </select>
              </label>
            )}

            {mode === "basic" && (
              <>
                <p className="text-muted-foreground">
                  Use a user made for MindLoom, not a person&apos;s own sign-in.
                  {b1 ? "" : " In S/4HANA Cloud: a communication user, with a communication arrangement for each API."}
                </p>
                {b1 && (
                  <label className="block">
                    <span className="font-medium">Company database</span>
                    <input {...field("company_db")} placeholder="SBODemoUS" />
                  </label>
                )}
                <label className="block">
                  <span className="font-medium">User name</span>
                  <input {...field("username")} autoComplete="off" />
                </label>
                <label className="block">
                  <span className="font-medium">Password</span>
                  <input {...field("password")} type="password" autoComplete="new-password" />
                </label>
              </>
            )}
            {mode === "oauth" && (
              <>
                <label className="block">
                  <span className="font-medium">Token service address</span>
                  <input {...field("token_url")} placeholder="https://…/sap/bc/sec/oauth2/token" />
                  <span className="mt-1 block text-xs text-muted-foreground">
                    Copy it from the OAuth 2.0 details of the communication arrangement, or from your API gateway.
                  </span>
                </label>
                <label className="block">
                  <span className="font-medium">Client ID</span>
                  <input {...field("client_id")} autoComplete="off" />
                </label>
                <label className="block">
                  <span className="font-medium">Client secret</span>
                  <input {...field("client_secret")} type="password" autoComplete="new-password" />
                </label>
              </>
            )}
            {mode === "certificate" && (
              <>
                <p className="text-muted-foreground">
                  S/4HANA Cloud only accepts a certificate signed by an authority it trusts, uploaded to the
                  communication user.
                </p>
                <label className="block">
                  <span className="font-medium">Certificate (PEM)</span>
                  <textarea rows={3} {...field("certificate_pem")} placeholder="-----BEGIN CERTIFICATE-----" />
                </label>
                <label className="block">
                  <span className="font-medium">Private key (PEM, no password)</span>
                  <textarea rows={3} {...field("private_key_pem")} placeholder="-----BEGIN PRIVATE KEY-----" />
                </label>
              </>
            )}

            {!b1 && (
              <>
                <label className="block">
                  <span className="font-medium">A service this sign-in may use</span>
                  <input {...field("test_service")} placeholder="API_BUSINESS_PARTNER" />
                  <span className="mt-1 block text-xs text-muted-foreground">
                    MindLoom reads it once to check the sign-in.
                  </span>
                </label>
                <label className="block">
                  <span className="font-medium">SAP client (on-premise only)</span>
                  <input {...field("sap_client")} placeholder="100" maxLength={3} />
                </label>
              </>
            )}
            <PrimaryButton disabled={busy || !ready} onClick={() => void connect()}>
              {busy && <Loader2 className="size-4 animate-spin" />}
              Connect
            </PrimaryButton>
          </>
        )}
      </CardContent>
    </Card>
  );
}
