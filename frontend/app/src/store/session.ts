import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface Session {
  orgId: string;
  orgName: string;
  userId: string;
  email: string;
  name?: string | null;
  photoUrl?: string | null;
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
};

export const useSession = create<SessionState>()(
  persist(
    (set) => ({
      ...empty,
      setSession: (session) =>
        set({ ...session, isAuthenticated: true }),
      clearSession: () => set({ ...empty }),
    }),
    { name: "loom-session" },
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
