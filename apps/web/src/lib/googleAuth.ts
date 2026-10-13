/** Google Identity Services helpers for obtaining ID tokens. */

import { apiFetch } from "@/lib/api";

const GIS_SCRIPT_SRC = "https://accounts.google.com/gsi/client";

export function getGoogleClientId(): string {
  const clientId = (import.meta.env.VITE_GOOGLE_CLIENT_ID as string | undefined)?.trim();
  if (!clientId) {
    throw new Error(
      "VITE_GOOGLE_CLIENT_ID is not set. Add it to the web env (same value as GOOGLE_CLIENT_ID).",
    );
  }
  return clientId;
}

async function resolveGoogleClientId(): Promise<string> {
  try {
    const res = await apiFetch("/auth/public-config", {}, { skipAuth: true });
    if (res.ok) {
      const body = (await res.json()) as { google_client_id?: string };
      const live = body.google_client_id?.trim();
      if (live) return live;
    }
  } catch {
    /* fall through to the Vite-baked id */
  }
  return getGoogleClientId();
}

function loadGisScript(): Promise<void> {
  if (typeof window === "undefined") {
    return Promise.reject(new Error("Google sign-in is only available in the browser."));
  }
  if (window.google?.accounts?.id) {
    return Promise.resolve();
  }
  const existing = document.querySelector<HTMLScriptElement>(`script[src="${GIS_SCRIPT_SRC}"]`);
  if (existing) {
    return new Promise((resolve, reject) => {
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener(
        "error",
        () => reject(new Error("Failed to load Google Identity Services.")),
        { once: true },
      );
    });
  }
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = GIS_SCRIPT_SRC;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Failed to load Google Identity Services."));
    document.head.appendChild(script);
  });
}

/**
 * Prompt the user for a Google account and return a verified ID token (JWT).
 * Shows a lightweight overlay with the official Google button.
 */
export async function requestGoogleIdToken(): Promise<string> {
  const clientId = await resolveGoogleClientId();
  await loadGisScript();
  const google = window.google;
  if (!google?.accounts?.id) {
    throw new Error("Google Identity Services failed to initialize.");
  }

  return new Promise((resolve, reject) => {
    let settled = false;
    const overlay = document.createElement("div");
    overlay.setAttribute("role", "dialog");
    overlay.style.cssText =
      "position:fixed;inset:0;z-index:99999;display:flex;align-items:center;justify-content:center;" +
      "background:rgba(15,23,42,0.45);padding:16px;";

    const panel = document.createElement("div");
    panel.style.cssText =
      "background:#fff;border-radius:12px;padding:24px;max-width:360px;width:100%;" +
      "box-shadow:0 20px 40px rgba(0,0,0,0.2);font-family:system-ui,sans-serif;text-align:center;";
    panel.innerHTML =
      "<p style='margin:0 0 8px;font-size:16px;font-weight:600;color:#0f172a'>Continue with Google</p>" +
      "<p style='margin:0 0 16px;font-size:13px;color:#64748b'>Choose the Google account for MindLoom.</p>";

    const buttonHost = document.createElement("div");
    buttonHost.style.cssText = "display:flex;justify-content:center;min-height:44px;";
    panel.appendChild(buttonHost);

    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "Cancel";
    cancel.style.cssText =
      "margin-top:16px;border:none;background:transparent;color:#64748b;cursor:pointer;font-size:13px;";
    panel.appendChild(cancel);
    overlay.appendChild(panel);
    document.body.appendChild(overlay);

    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      overlay.remove();
      fn();
    };

    cancel.addEventListener("click", () => {
      finish(() => reject(new Error("Google sign-in was cancelled.")));
    });
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        finish(() => reject(new Error("Google sign-in was cancelled.")));
      }
    });

    google.accounts.id.initialize({
      client_id: clientId,
      callback: (response) => {
        const credential = response.credential?.trim();
        if (!credential) {
          finish(() => reject(new Error("Google did not return an ID token.")));
          return;
        }
        finish(() => resolve(credential));
      },
      auto_select: false,
      cancel_on_tap_outside: true,
    });

    google.accounts.id.renderButton(buttonHost, {
      type: "standard",
      theme: "outline",
      size: "large",
      text: "continue_with",
      width: 280,
    });

    // Best-effort One Tap in parallel; credential callback is shared.
    try {
      google.accounts.id.prompt();
    } catch {
      /* ignore */
    }
  });
}

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: {
            client_id: string;
            callback: (response: { credential?: string }) => void;
            auto_select?: boolean;
            cancel_on_tap_outside?: boolean;
          }) => void;
          prompt: () => void;
          renderButton: (
            parent: HTMLElement,
            options: {
              type?: string;
              theme?: string;
              size?: string;
              text?: string;
              width?: number;
            },
          ) => void;
        };
      };
    };
  }
}
