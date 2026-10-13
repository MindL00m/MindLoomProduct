import { useCallback, useEffect, useMemo, useState } from "react";
import { Github, Loader2, Unplug } from "lucide-react";
import { PrimaryButton } from "@/components/PrimaryButton";
import { SecondaryButton } from "@/components/SecondaryButton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/StatusBadge";
import {
  connectGitHub,
  disconnectGitHub,
  getGitHubConnection,
  inspectGitHubToken,
  updateGitHubConnection,
  type GitHubCapability,
  type GitHubConnection,
  type GitHubRepoPreview,
} from "@/services/github";
import { cn } from "@/lib/utils";

const CAPABILITY_OPTIONS: { id: GitHubCapability; label: string; detail: string }[] = [
  {
    id: "metadata",
    label: "Repository metadata",
    detail: "List granted repos and read names, visibility, and default branch.",
  },
  {
    id: "contents",
    label: "File contents",
    detail: "Read files, and write them when opening an approved pull request.",
  },
  {
    id: "pull_requests",
    label: "Open pull requests",
    detail: "Propose a one-file change in Ask. Nothing is pushed until you approve the diff.",
  },
];

export default function GitHubConnector({
  connected,
  account,
  selectedCount,
  onChanged,
}: {
  connected?: boolean;
  account?: string | null;
  selectedCount?: number;
  onChanged: () => void;
}) {
  const [status, setStatus] = useState<GitHubConnection | null>(null);
  const [token, setToken] = useState("");
  const [repos, setRepos] = useState<GitHubRepoPreview[]>([]);
  const [selectedRepos, setSelectedRepos] = useState<string[]>([]);
  const [capabilities, setCapabilities] = useState<GitHubCapability[]>(["metadata", "contents"]);
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [login, setLogin] = useState<string | null>(account ?? null);

  const load = useCallback(async () => {
    const connection = await getGitHubConnection();
    setStatus(connection);
    if (connection.connected) {
      setLogin(connection.account_login ?? null);
      setSelectedRepos(connection.repositories ?? []);
      setCapabilities(
        (connection.capabilities as GitHubCapability[] | undefined) ?? ["metadata"],
      );
    }
  }, []);

  useEffect(() => {
    void load().catch((err) => {
      setError(err instanceof Error ? err.message : "Could not load GitHub.");
    });
  }, [load]);

  const visibleRepos = useMemo(() => {
    const query = filter.trim().toLowerCase();
    const names = new Map(repos.map((repo) => [repo.full_name, repo]));
    for (const name of selectedRepos) {
      if (!names.has(name)) names.set(name, { full_name: name });
    }
    return [...names.values()].filter((repo) =>
      query ? repo.full_name.toLowerCase().includes(query) : true,
    );
  }, [filter, repos, selectedRepos]);

  async function inspect() {
    setBusy(true);
    setError(null);
    try {
      const result = await inspectGitHubToken(token.trim());
      setLogin(result.login);
      setRepos(result.repositories);
      if (selectedRepos.length === 0) {
        setSelectedRepos(result.repositories.slice(0, 3).map((repo) => repo.full_name));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not inspect this token.");
    } finally {
      setBusy(false);
    }
  }

  function toggleRepo(name: string) {
    setSelectedRepos((current) =>
      current.includes(name) ? current.filter((item) => item !== name) : [...current, name],
    );
  }

  function toggleCapability(id: GitHubCapability) {
    setCapabilities((current) => {
      if (current.includes(id)) return current.filter((item) => item !== id);
      if (id === "pull_requests") {
        return Array.from(new Set([...current, "metadata", "contents", id]));
      }
      if (id === "contents") {
        return Array.from(new Set([...current, "metadata", id]));
      }
      return [...current, id];
    });
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      if (status?.connected && !token.trim()) {
        await updateGitHubConnection({ capabilities, repositories: selectedRepos });
      } else {
        await connectGitHub({
          token: token.trim(),
          capabilities,
          repositories: selectedRepos,
        });
        setToken("");
      }
      await load();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save GitHub.");
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    setError(null);
    try {
      await disconnectGitHub();
      setStatus({ connected: false });
      setRepos([]);
      setSelectedRepos([]);
      setToken("");
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not disconnect GitHub.");
    } finally {
      setBusy(false);
    }
  }

  const isConnected = Boolean(status?.connected ?? connected);

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="flex size-11 items-center justify-center rounded-lg border">
            <Github className="size-6" />
          </span>
          <div>
            <CardTitle className="text-lg">GitHub</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              Paste your own fine-grained personal access token. MindLoom only uses the
              repositories and actions you select here.
            </p>
          </div>
        </div>
        <StatusBadge tone={isConnected ? "healthy" : "neutral"} dot>
          {isConnected ? "Connected" : "Not connected"}
        </StatusBadge>
      </CardHeader>
      <CardContent className="space-y-4">
        {isConnected && (
          <p className="text-sm text-muted-foreground">
            {login || account || "GitHub"} · {selectedCount ?? selectedRepos.length} granted
            repositories
          </p>
        )}
        {error && (
          <div className="rounded-md border border-destructive/20 bg-destructive/10 p-3 text-sm text-destructive">
            {error}
          </div>
        )}

        <label className="block text-sm">
          <span className="font-medium">Fine-grained personal access token</span>
          <input
            type="password"
            autoComplete="off"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder={isConnected ? "Paste a new token to replace the stored one" : "github_pat_…"}
            className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          />
        </label>
        <p className="text-xs text-muted-foreground">
          Create the token at GitHub → Settings → Developer settings → Fine-grained tokens.
          Grant only the repositories MindLoom should see. Contents: Read is enough for Ask
          to list files. Opening a pull request also needs Contents: Read and write and
          Pull requests: Read and write — Contents: Read alone returns 403 when creating
          a branch. The token is stored encrypted and is never shown again.
        </p>
        <SecondaryButton onClick={() => void inspect()} disabled={busy || !token.trim()}>
          {busy ? <Loader2 className="size-4 animate-spin" /> : null}
          List repositories this token can see
        </SecondaryButton>

        <div className="space-y-2">
          <p className="text-sm font-medium">What MindLoom may do</p>
          {CAPABILITY_OPTIONS.map((option) => (
            <label key={option.id} className="flex gap-3 rounded-md border border-border p-3 text-sm">
              <input
                type="checkbox"
                className="mt-1"
                checked={capabilities.includes(option.id)}
                onChange={() => toggleCapability(option.id)}
              />
              <span>
                <span className="font-medium">{option.label}</span>
                <span className="mt-0.5 block text-muted-foreground">{option.detail}</span>
              </span>
            </label>
          ))}
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm font-medium">Repositories MindLoom may access</p>
            <button
              type="button"
              className="text-xs text-muted-foreground underline-offset-2 hover:underline"
              onClick={() => setSelectedRepos(repos.map((repo) => repo.full_name))}
              disabled={repos.length === 0}
            >
              Select all listed
            </button>
          </div>
          <input
            type="search"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Filter repositories"
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
          />
          <div className="max-h-56 overflow-y-auto rounded-md border border-border">
            {visibleRepos.length === 0 ? (
              <p className="p-3 text-sm text-muted-foreground">
                Inspect a token to choose from repositories it can see.
              </p>
            ) : (
              visibleRepos.map((repo) => {
                const checked = selectedRepos.includes(repo.full_name);
                return (
                  <label
                    key={repo.full_name}
                    className={cn(
                      "flex cursor-pointer items-start gap-3 border-b border-border px-3 py-2 text-sm last:border-b-0",
                      checked ? "bg-muted/50" : "",
                    )}
                  >
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={checked}
                      onChange={() => toggleRepo(repo.full_name)}
                    />
                    <span>
                      <span className="font-medium">{repo.full_name}</span>
                      {repo.private ? (
                        <span className="ml-2 text-xs text-muted-foreground">Private</span>
                      ) : null}
                      {repo.description ? (
                        <span className="mt-0.5 block text-muted-foreground">{repo.description}</span>
                      ) : null}
                    </span>
                  </label>
                );
              })
            )}
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          <PrimaryButton
            onClick={() => void save()}
            disabled={busy || selectedRepos.length === 0 || capabilities.length === 0 || (!isConnected && !token.trim())}
          >
            {busy ? <Loader2 className="size-4 animate-spin" /> : <Github className="size-4" />}
            {isConnected ? "Save permissions" : "Connect GitHub"}
          </PrimaryButton>
          {isConnected && (
            <SecondaryButton onClick={() => void disconnect()} disabled={busy}>
              <Unplug className="size-4" />
              Disconnect
            </SecondaryButton>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
