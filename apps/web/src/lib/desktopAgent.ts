/** Download URL for the packaged macOS capture agent. */

const DEFAULT_DOWNLOAD_PATH = "/downloads/LoomCapture-macos.zip";
const INSTALL_SCRIPT_PATH = "/downloads/install-loom-capture.sh";

export const DESKTOP_AGENT_DOWNLOAD_URL: string = (() => {
  const baked = import.meta.env.VITE_DESKTOP_AGENT_DOWNLOAD_URL?.trim();
  return baked || DEFAULT_DOWNLOAD_PATH;
})();

export function isAbsoluteUrl(value: string): boolean {
  return /^https?:\/\//i.test(value);
}

export function absoluteAssetUrl(pathOrUrl: string, origin: string): string {
  if (isAbsoluteUrl(pathOrUrl)) return pathOrUrl;
  const path = pathOrUrl.startsWith("/") ? pathOrUrl : `/${pathOrUrl}`;
  return `${origin.replace(/\/$/, "")}${path}`;
}

/**
 * Terminal install avoids Finder quarantine on the unsigned .app zip.
 *
 * The site origin and the API origin this site talks to are passed to the
 * installer, which saves them for the app. Without them the app falls back to
 * the hosts baked into the zip (localhost for local builds).
 */
export function loomCaptureInstallCommand(
  origin: string,
  zipUrl = DESKTOP_AGENT_DOWNLOAD_URL,
  apiBase = "/api",
): string {
  const host = origin.replace(/\/$/, "");
  const zip = absoluteAssetUrl(zipUrl, host);
  const script = absoluteAssetUrl(INSTALL_SCRIPT_PATH, host);
  const api = absoluteAssetUrl(apiBase, host).replace(/\/$/, "");
  return `curl -fsSL "${script}" | bash -s -- "${host}" "${zip}" "${api}"`;
}
