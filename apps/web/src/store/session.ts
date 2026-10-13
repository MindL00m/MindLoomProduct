import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface Session {
  orgId: string;
  orgName: string;
  userId: string;
  email: string;
  name?: string | null;
  photoUrl?: string | null;
  role: "admin" | "member";
  accessToken: string;
}

interface SessionState extends Session {
  isAuthenticated: boolean;
  setSession: (session: Session) => void;
  clearSession: () => void;
}

const empty: Omit<SessionState, "setSession" | "clearSession"> = {
  isAuthenticated: false,
  orgId: "",
  orgName: "",
  userId: "",
  email: "",
  name: null,
  photoUrl: null,
  role: "member",
  accessToken: "",
};

export const useSession = create<SessionState>()(
  persist(
    (set) => ({
      ...empty,
      setSession: (session) =>
        set({ ...session, isAuthenticated: Boolean(session.accessToken) }),
      clearSession: () => set({ ...empty }),
    }),
    {
      name: "loom-session",
      merge: (persisted, current) => {
        const incoming = (persisted ?? {}) as Partial<SessionState>;
        const merged = { ...current, ...incoming };
        // Pre-JWT sessions lack accessToken — force re-auth.
        if (!merged.accessToken) {
          return { ...current, ...empty };
        }
        return {
          ...merged,
          isAuthenticated: true,
        };
      },
    },
  ),
);

/** Read org id synchronously for API calls (outside React). */
export function getOrgId(): string | null {
  const state = useSession.getState();
  return state.isAuthenticated ? state.orgId : null;
}

/** Read user id synchronously for API calls (outside React). */
export function getUserId(): string | null {
  const state = useSession.getState();
  return state.isAuthenticated ? state.userId : null;
}

/** Read MindLoom access JWT for Authorization headers. */
export function getAccessToken(): string | null {
  const state = useSession.getState();
  return state.isAuthenticated && state.accessToken ? state.accessToken : null;
}
