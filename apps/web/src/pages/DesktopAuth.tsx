/** Desktop agent sign-in bridge: Google → MindLoom JWT → localhost callback. */

import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { WizardCard } from "@/components/WizardCard";
import { PrimaryButton } from "@/components/PrimaryButton";
import { GoogleIcon } from "@/components/icons";
import { requestGoogleIdToken } from "@/lib/googleAuth";
import { AuthError, getFreshSession, toClientSession } from "@/services/auth";
import { useSession } from "@/store/session";
import { API_BASE } from "@/lib/api";

type BridgeSession = {
  accessToken: string;
  orgId: string;
  userId: string;
  email: string;
};

function readQuery() {
  const params = new URLSearchParams(window.location.search);
  const port = Number(params.get("port") || "");
  const api = (params.get("api") || API_BASE).trim();
  return { port, api };
}

async function sendToAgent(port: number, session: BridgeSession): Promise<void> {
  const payload = {
    access_token: session.accessToken,
    org_id: session.orgId,
    user_id: session.userId,
    email: session.email,
  };

  // Prefer POST JSON (avoids URL length / parsing issues). CORS is enabled on the agent.
  try {
    const res = await fetch(`http://127.0.0.1:${port}/callback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      mode: "cors",
    });
    if (res.ok) return;
  } catch {
    /* fall through to navigation */
  }

  const qs = new URLSearchParams(payload);
  window.location.href = `http://127.0.0.1:${port}/callback?${qs.toString()}`;
}

export default function DesktopAuth() {
  const setSession = useSession((s) => s.setSession);
  const accessToken = useSession((s) => s.accessToken);
  const email = useSession((s) => s.email);
  const orgId = useSession((s) => s.orgId);
  const userId = useSession((s) => s.userId);
  const isAuthenticated = useSession((s) => s.isAuthenticated);

  const { port, api } = useMemo(() => readQuery(), []);
  const [hydrated, setHydrated] = useState(() => useSession.persist.hasHydrated());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const autoTried = useRef(false);

  const portValid = Number.isInteger(port) && port > 0 && port < 65536;

  useEffect(() => {
    if (hydrated) return;
    const unsub = useSession.persist.onFinishHydration(() => setHydrated(true));
    setHydrated(useSession.persist.hasHydrated());
    return unsub;
  }, [hydrated]);

  async function completeWithSession(session: BridgeSession) {
    if (!portValid) {
      setError("Missing or invalid callback port. Start sign-in from the MindLoom Capture agent.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await sendToAgent(port, session);
      setDone(true);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Could not reach the MindLoom Capture agent. Click Sign in again in the agent window.",
      );
      setLoading(false);
    }
  }

  // Re-validate the browser JWT against this API (SESSION_SECRET / empty local DB
  // make a leftover Railway or pre-recreate token fail with 401 on upload).
  useEffect(() => {
    if (!hydrated || !portValid || done || autoTried.current) return;
    if (!isAuthenticated || !accessToken || !orgId || !userId) return;
    autoTried.current = true;
    void (async () => {
      try {
        const fresh = await getFreshSession();
        const mapped = toClientSession(fresh);
        setSession(mapped);
        await completeWithSession({
          accessToken: mapped.accessToken,
          orgId: mapped.orgId,
          userId: mapped.userId,
          email: mapped.email || "",
        });
      } catch {
        useSession.getState().clearSession();
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one-shot after hydration
  }, [hydrated, portValid, done, isAuthenticated, accessToken, orgId, userId, email]);

  async function handleUseCurrentSession() {
    setLoading(true);
    setError(null);
    try {
      const fresh = await getFreshSession();
      const mapped = toClientSession(fresh);
      setSession(mapped);
      await completeWithSession({
        accessToken: mapped.accessToken,
        orgId: mapped.orgId,
        userId: mapped.userId,
        email: mapped.email || "",
      });
    } catch {
      useSession.getState().clearSession();
      setError("No valid MindLoom session in this browser. Sign in with Google below.");
      setLoading(false);
    }
  }

  async function handleGoogleSignIn() {
    setLoading(true);
    setError(null);
    try {
      const idToken = await requestGoogleIdToken();
      const res = await fetch(`${api.replace(/\/$/, "")}/auth/google/signin`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id_token: idToken }),
      });
      if (!res.ok) {
        let detail = `Sign-in failed (${res.status})`;
        try {
          const body = await res.json();
          if (typeof body?.detail === "string") detail = body.detail;
        } catch {
          /* keep */
        }
        throw new AuthError(detail, res.status);
      }
      const session = toClientSession(await res.json());
      setSession(session);
      await completeWithSession({
        accessToken: session.accessToken,
        orgId: session.orgId,
        userId: session.userId,
        email: session.email,
      });
    } catch (err) {
      if (err instanceof AuthError && err.status === 404) {
        setError(
          "No organization exists for this email domain. Create an org in the web app first, then retry.",
        );
      } else {
        setError(err instanceof Error ? err.message : "Sign-in failed.");
      }
      setLoading(false);
    }
  }

  if (!portValid) {
    return (
      <WizardCard
        title="Desktop sign-in"
        subtitle="Open this page from the MindLoom Capture agent (Sign in with Google)."
      >
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            The agent starts a local callback and appends <code>?port=…</code> to this URL.
          </p>
          <div className="flex flex-col gap-2">
            <Link to="/download" className="text-sm underline underline-offset-2">
              Download MindLoom Capture
            </Link>
            <Link to="/setup" className="text-sm underline underline-offset-2">
              Back to setup
            </Link>
          </div>
        </div>
      </WizardCard>
    );
  }

  return (
    <WizardCard
      title="Connect MindLoom Capture"
      subtitle="Sign in so the macOS agent can upload activity summaries to your organization."
    >
      <div className="space-y-4">
        {error && <p className="text-sm text-destructive">{error}</p>}
        {!hydrated && (
          <p className="text-sm text-muted-foreground">Checking for an existing browser session…</p>
        )}
        {done && (
          <p className="text-sm text-muted-foreground">
            Sent to MindLoom Capture. Return to the agent — you should see a Signed in confirmation.
          </p>
        )}
        {loading && !done && (
          <p className="text-sm text-muted-foreground">Connecting to the capture agent…</p>
        )}

        {hydrated && isAuthenticated && accessToken && !done && (
          <PrimaryButton
            className="w-full"
            loading={loading}
            disabled={done}
            onClick={() => void handleUseCurrentSession()}
          >
            Use this browser session
            {email ? ` (${email})` : ""}
          </PrimaryButton>
        )}

        <PrimaryButton
          className="w-full"
          loading={loading}
          disabled={done}
          onClick={() => void handleGoogleSignIn()}
        >
          {!loading && <GoogleIcon className="size-5" />}
          {loading ? "Signing in…" : "Sign in with Google"}
        </PrimaryButton>

        <p className="text-xs text-muted-foreground">
          Callback port <span className="font-mono">{port}</span> · API{" "}
          <span className="font-mono">{api}</span>
        </p>
      </div>
    </WizardCard>
  );
}
