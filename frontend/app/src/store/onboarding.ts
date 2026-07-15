import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { ProviderId, SetupSummary } from "@/services/types";
import type { DirectoryPerson } from "@/lib/directory";

interface OnboardingState {
  organizationName: string;
  domain: string;
  selectedProvider: ProviderId | null;
  oauthConnected: boolean;
  oauthAccount: string | null;
  syncProgress: number;
  summary: SetupSummary | null;
  /** People parsed from an uploaded CSV (CSV flow only). */
  directory: DirectoryPerson[];
  /** Name of the uploaded CSV file, for display. */
  csvFileName: string | null;

  setOrganization: (name: string, domain: string) => void;
  setProvider: (provider: ProviderId) => void;
  setOAuthConnected: (account: string) => void;
  setSyncProgress: (progress: number) => void;
  setSummary: (summary: SetupSummary) => void;
  setDirectory: (people: DirectoryPerson[], fileName: string) => void;
  reset: () => void;
}

const initialState = {
  organizationName: "",
  domain: "",
  selectedProvider: null as ProviderId | null,
  oauthConnected: false,
  oauthAccount: null as string | null,
  syncProgress: 0,
  summary: null as SetupSummary | null,
  directory: [] as DirectoryPerson[],
  csvFileName: null as string | null,
};

export const useOnboarding = create<OnboardingState>()(
  persist(
    (set) => ({
      ...initialState,
      setOrganization: (organizationName, domain) =>
        set({ organizationName, domain }),
      setProvider: (selectedProvider) => set({ selectedProvider }),
      setOAuthConnected: (oauthAccount) =>
        set({ oauthConnected: true, oauthAccount }),
      setSyncProgress: (syncProgress) => set({ syncProgress }),
      setSummary: (summary) => set({ summary }),
      setDirectory: (directory, csvFileName) => set({ directory, csvFileName }),
      reset: () => set({ ...initialState }),
    }),
    {
      name: "loom-onboarding",
      // syncProgress is transient; don't persist it across reloads.
      partialize: ({ syncProgress: _omit, ...rest }) => rest,
    },
  ),
);

/** Domain normalization shared by the form and tests:
 *  strips protocol/path and lowercases. */
export function normalizeDomain(raw: string): string {
  return raw
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\//, "")
    .replace(/^www\./, "")
    .replace(/\/.*$/, "")
    .trim();
}
