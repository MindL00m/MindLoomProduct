import { useEffect, useState } from "react";
import { Bot, Save } from "lucide-react";
import { PrimaryButton } from "@/components/PrimaryButton";
import { getLLMSettings, saveLLMSettings, type LLMSettings } from "@/services/llmSettings";

const defaults: LLMSettings = {
  provider: "openai", base_url: null, model: "gpt-4o-mini",
  context_window: 128000, supports_tools: true,
};

export function LLMSettingsPanel() {
  const [settings, setSettings] = useState(defaults);
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getLLMSettings().then(setSettings).catch((error: Error) => setStatus(error.message));
  }, []);

  async function save() {
    setSaving(true); setStatus("Checking provider…");
    try {
      setSettings(await saveLLMSettings(settings));
      setStatus("LLM settings saved.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not save settings.");
    } finally { setSaving(false); }
  }

  return (
    <section className="rounded-lg border border-border bg-card p-5">
      <h3 className="flex items-center gap-2 text-lg font-semibold"><Bot className="size-5" />Chat model</h3>
      <p className="mt-1 text-sm text-muted-foreground">Organization-wide provider used by Ask. Local endpoints must expose an OpenAI-compatible API.</p>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <label className="text-sm font-medium">Provider
          <select className="mt-1 w-full rounded-md border border-border bg-background p-2" value={settings.provider}
            onChange={(e) => {
              const provider = e.target.value as LLMSettings["provider"];
              setSettings({ ...settings, provider, model: provider === "openai" ? "gpt-4o-mini" : settings.model });
            }}>
            <option value="openai">OpenAI</option><option value="local">Local / self-hosted</option>
          </select>
        </label>
        <label className="text-sm font-medium">Model
          <input className="mt-1 w-full rounded-md border border-border bg-background p-2" value={settings.model}
            onChange={(e) => setSettings({ ...settings, model: e.target.value })} />
        </label>
        {settings.provider === "local" && <>
          <label className="text-sm font-medium md:col-span-2">Base URL
            <input className="mt-1 w-full rounded-md border border-border bg-background p-2" placeholder="http://llm-host:8000/v1"
              value={settings.base_url ?? ""} onChange={(e) => setSettings({ ...settings, base_url: e.target.value })} />
          </label>
          <label className="text-sm font-medium">Context window (tokens)
            <input type="number" min={1024} className="mt-1 w-full rounded-md border border-border bg-background p-2"
              value={settings.context_window} onChange={(e) => setSettings({ ...settings, context_window: Number(e.target.value) })} />
          </label>
          <label className="flex items-end gap-2 pb-2 text-sm font-medium">
            <input type="checkbox" checked={settings.supports_tools}
              onChange={(e) => setSettings({ ...settings, supports_tools: e.target.checked })} /> Supports structured tool calls
          </label>
        </>}
      </div>
      <div className="mt-4 flex items-center gap-3"><PrimaryButton onClick={save} disabled={saving}><Save className="size-4" />{saving ? "Saving…" : "Save"}</PrimaryButton><span className="text-sm text-muted-foreground">{status}</span></div>
    </section>
  );
}
