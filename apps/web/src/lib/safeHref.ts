/**
 * A link target from data (documents, email, web results, providers) made safe to
 * put in an href: only http(s), mailto, same-site paths and anchors. Anything else
 * (javascript:, data:, vbscript:) becomes undefined, so the link does nothing.
 */
export function safeHref(url: string | null | undefined): string | undefined {
  const trimmed = (url ?? "").trim();
  if (!trimmed) return undefined;
  if (/^(https?:|mailto:)/i.test(trimmed)) return trimmed;
  if (trimmed.startsWith("/") && !trimmed.startsWith("//")) return trimmed;
  if (trimmed.startsWith("#")) return trimmed;
  return undefined;
}
