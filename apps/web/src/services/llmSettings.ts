import { apiFetch } from "@/lib/api";

export interface LLMSettings {
  provider: "openai" | "anthropic" | "local";
  base_url: string | null;
  model: string;
  context_window: number;
  supports_tools: boolean;
  /** Whether the organization has saved its own provider key. The key itself is never returned. */
  has_api_key?: boolean;
  /** Write-only: a new key to save. Omit to keep the saved one; "" removes it. */
  api_key?: string | null;
}

export async function getLLMSettings(): Promise<LLMSettings> {
  const response = await apiFetch("/llm-settings");
  if (!response.ok) throw new Error("Could not load LLM settings.");
  return response.json() as Promise<LLMSettings>;
}

export async function saveLLMSettings(settings: LLMSettings): Promise<LLMSettings> {
  const response = await apiFetch("/llm-settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new Error(body.detail || "Could not save LLM settings.");
  }
  return response.json() as Promise<LLMSettings>;
}
