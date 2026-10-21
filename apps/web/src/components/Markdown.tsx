/**
 * Small, dependency-free Markdown renderer for chat answers.
 *
 * Supports headings, paragraphs (single newlines become line breaks), bold,
 * italic, strikethrough, inline code, fenced code blocks, links and bare URLs,
 * ordered/unordered/task lists with nesting, blockquotes, GFM tables, and
 * horizontal rules. Everything renders as React elements (never raw HTML), and
 * links are limited to http(s), mailto, and in-app paths.
 *
 * ``renderCitation`` lets callers turn ``[SOURCE: id]`` style markers into
 * inline references; it is never applied inside code.
 */

import { Fragment, type ReactNode } from "react";
import { cn } from "@/lib/utils";

export type CitationRenderer = (id: string, key: string) => ReactNode;

interface MarkdownProps {
  text: string;
  className?: string;
  renderCitation?: CitationRenderer;
}

export function Markdown({ text, className, renderCitation }: MarkdownProps) {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  return (
    <div className={cn("space-y-3 text-sm leading-relaxed text-foreground", className)}>
      {renderBlocks(lines, { renderCitation, keyPrefix: "md" })}
    </div>
  );
}

interface Ctx {
  renderCitation?: CitationRenderer;
  keyPrefix: string;
}

// ---------------------------------------------------------------------------
// Blocks

const FENCE_RE = /^ {0,3}(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/;
const HEADING_RE = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const HR_RE = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/;
const QUOTE_RE = /^ {0,3}>\s?(.*)$/;
const LIST_RE = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const TABLE_SEP_RE = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/;

function indentOf(line: string): number {
  const match = /^[ \t]*/.exec(line)?.[0] ?? "";
  return match.replace(/\t/g, "    ").length;
}

function dedent(line: string, amount: number): string {
  let removed = 0;
  let i = 0;
  while (i < line.length && removed < amount && (line[i] === " " || line[i] === "\t")) {
    removed += line[i] === "\t" ? 4 : 1;
    i++;
  }
  return line.slice(i);
}

function isTableStart(lines: string[], i: number): boolean {
  return (
    i + 1 < lines.length &&
    lines[i].includes("|") &&
    TABLE_SEP_RE.test(lines[i + 1]) &&
    lines[i + 1].includes("-") &&
    splitRow(lines[i + 1]).length === splitRow(lines[i]).length
  );
}

function splitRow(line: string): string[] {
  let row = line.trim();
  if (row.startsWith("|")) row = row.slice(1);
  if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
  const cells: string[] = [];
  let cell = "";
  let inCode = false;
  for (let i = 0; i < row.length; i++) {
    const ch = row[i];
    if (ch === "\\" && row[i + 1] === "|") {
      cell += "|";
      i++;
    } else if (ch === "`") {
      inCode = !inCode;
      cell += ch;
    } else if (ch === "|" && !inCode) {
      cells.push(cell.trim());
      cell = "";
    } else {
      cell += ch;
    }
  }
  cells.push(cell.trim());
  return cells;
}

function startsBlock(lines: string[], i: number): boolean {
  const line = lines[i];
  return (
    FENCE_RE.test(line) ||
    HEADING_RE.test(line) ||
    HR_RE.test(line) ||
    QUOTE_RE.test(line) ||
    LIST_RE.test(line) ||
    isTableStart(lines, i)
  );
}

function renderBlocks(lines: string[], ctx: Ctx): ReactNode[] {
  const out: ReactNode[] = [];
  let i = 0;
  let n = 0;
  const key = () => `${ctx.keyPrefix}-${n++}`;

  while (i < lines.length) {
    const line = lines[i];

    if (!line.trim()) {
      i++;
      continue;
    }

    const fence = FENCE_RE.exec(line);
    if (fence) {
      const marker = fence[1];
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(marker)) {
        body.push(lines[i]);
        i++;
      }
      i++; // closing fence (or end of input)
      out.push(
        <pre
          key={key()}
          className="overflow-x-auto rounded-md border border-border bg-muted/60 px-3 py-2 font-mono text-xs leading-relaxed"
          data-language={fence[2] || undefined}
        >
          <code>{body.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    const heading = HEADING_RE.exec(line);
    if (heading) {
      const level = heading[1].length;
      const k = key();
      const content = renderInline(heading[2], { ...ctx, keyPrefix: k });
      const styles = [
        "text-lg font-semibold",
        "text-base font-semibold",
        "text-sm font-semibold",
        "text-sm font-semibold",
        "text-sm font-medium",
        "text-sm font-medium text-muted-foreground",
      ];
      const Tag = `h${Math.min(level + 1, 6)}` as "h2";
      out.push(
        <Tag key={k} className={cn("pt-1 tracking-tight text-foreground", styles[level - 1])}>
          {content}
        </Tag>,
      );
      i++;
      continue;
    }

    if (HR_RE.test(line)) {
      out.push(<hr key={key()} className="border-border" />);
      i++;
      continue;
    }

    if (QUOTE_RE.test(line)) {
      const body: string[] = [];
      while (i < lines.length && lines[i].trim() && QUOTE_RE.test(lines[i])) {
        body.push(QUOTE_RE.exec(lines[i])![1]);
        i++;
      }
      const k = key();
      out.push(
        <blockquote key={k} className="space-y-2 border-l-2 border-mist-400 pl-3 text-muted-foreground">
          {renderBlocks(body, { ...ctx, keyPrefix: k })}
        </blockquote>,
      );
      continue;
    }

    if (isTableStart(lines, i)) {
      const header = splitRow(lines[i]);
      const aligns = splitRow(lines[i + 1]).map((c) =>
        c.startsWith(":") && c.endsWith(":")
          ? "center"
          : c.endsWith(":")
            ? "right"
            : "left",
      );
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
        rows.push(splitRow(lines[i]));
        i++;
      }
      const k = key();
      out.push(
        <div key={k} className="overflow-x-auto rounded-md border border-border">
          <table className="min-w-full border-collapse text-xs">
            <thead className="bg-muted/60">
              <tr>
                {header.map((cell, c) => (
                  <th
                    key={c}
                    style={{ textAlign: aligns[c] as "left" }}
                    className="border-b border-border px-3 py-1.5 font-semibold"
                  >
                    {renderInline(cell, { ...ctx, keyPrefix: `${k}-h${c}` })}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, r) => (
                <tr key={r} className="even:bg-muted/30">
                  {header.map((_, c) => (
                    <td
                      key={c}
                      style={{ textAlign: aligns[c] as "left" }}
                      className="border-t border-border px-3 py-1.5 align-top"
                    >
                      {renderInline(row[c] ?? "", { ...ctx, keyPrefix: `${k}-${r}-${c}` })}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    if (LIST_RE.test(line)) {
      const [node, next] = renderList(lines, i, { ...ctx, keyPrefix: key() });
      out.push(node);
      i = next;
      continue;
    }

    // Paragraph: consecutive lines until a blank line or another block.
    const para: string[] = [line.trim()];
    i++;
    while (i < lines.length && lines[i].trim() && !startsBlock(lines, i)) {
      para.push(lines[i].trim());
      i++;
    }
    const k = key();
    out.push(
      <p key={k} className="break-words">
        {para.map((text, j) => (
          <Fragment key={j}>
            {j > 0 && <br />}
            {renderInline(text, { ...ctx, keyPrefix: `${k}-${j}` })}
          </Fragment>
        ))}
      </p>,
    );
  }
  return out;
}

function renderList(lines: string[], start: number, ctx: Ctx): [ReactNode, number] {
  const first = LIST_RE.exec(lines[start])!;
  const baseIndent = indentOf(first[1]);
  const ordered = /\d/.test(first[2]);
  const items: { text: string; children: string[] }[] = [];
  let i = start;

  while (i < lines.length) {
    const line = lines[i];
    const match = LIST_RE.exec(line);
    if (match && indentOf(match[1]) === baseIndent) {
      if (/\d/.test(match[2]) !== ordered) break; // marker type changed: new list
      items.push({ text: match[3], children: [] });
      i++;
      continue;
    }
    if (!line.trim()) {
      // A blank line continues the list only if more list content follows.
      const nextLine = lines.slice(i + 1).find((l) => l.trim());
      if (nextLine === undefined) break;
      const nextMatch = LIST_RE.exec(nextLine);
      const continues =
        indentOf(nextLine) > baseIndent ||
        (nextMatch !== null && indentOf(nextMatch[1]) === baseIndent);
      if (!continues) break;
      items[items.length - 1]?.children.push("");
      i++;
      continue;
    }
    if (indentOf(line) > baseIndent && items.length) {
      items[items.length - 1].children.push(dedent(line, baseIndent + 2));
      i++;
      continue;
    }
    if (match && indentOf(match[1]) < baseIndent) break;
    // Lazy continuation of the item's text.
    if (items.length && !startsBlock(lines, i)) {
      items[items.length - 1].children.push(line.trim());
      i++;
      continue;
    }
    break;
  }

  const startNumber = ordered ? parseInt(first[2], 10) : undefined;
  const ListTag = ordered ? "ol" : "ul";
  const node = (
    <ListTag
      key={ctx.keyPrefix}
      start={startNumber !== undefined && startNumber !== 1 ? startNumber : undefined}
      className={cn(
        "space-y-1 pl-5 marker:text-muted-foreground",
        ordered ? "list-decimal" : "list-disc",
      )}
    >
      {items.map((item, idx) => {
        const k = `${ctx.keyPrefix}-${idx}`;
        const task = /^\[( |x|X)\]\s+(.*)$/.exec(item.text);
        const text = task ? task[2] : item.text;
        // Lines that are not themselves blocks continue the item's first paragraph.
        const inlineTail: string[] = [];
        let rest = item.children;
        while (rest.length && rest[0].trim() && !startsBlock(rest, 0)) {
          inlineTail.push(rest[0].trim());
          rest = rest.slice(1);
        }
        return (
          <li key={k} className={cn("pl-0.5", task && "list-none -ml-5")}>
            {task && (
              <input
                type="checkbox"
                checked={task[1].toLowerCase() === "x"}
                readOnly
                aria-label={task[1].toLowerCase() === "x" ? "Done" : "Not done"}
                className="mr-2 align-middle"
              />
            )}
            {[text, ...inlineTail].map((line, j) => (
              <Fragment key={j}>
                {j > 0 && <br />}
                {renderInline(line, { ...ctx, keyPrefix: `${k}-t${j}` })}
              </Fragment>
            ))}
            {rest.some((l) => l.trim()) && (
              <div className="mt-1 space-y-2">
                {renderBlocks(rest, { ...ctx, keyPrefix: `${k}-c` })}
              </div>
            )}
          </li>
        );
      })}
    </ListTag>
  );
  return [node, i];
}

// ---------------------------------------------------------------------------
// Inline

const CITE_INLINE = /^\[(?:SOURCE|EPHEMERAL):\s*([^\]]+?)\]/i;
const LINK_INLINE =
  /^\[((?:[^\[\]]|\[[^\]]*\])*)\]\(\s*<?((?:[^\s()<>]|\([^\s()<>]*\))+)>?(?:\s+"[^"]*")?\s*\)/;
const AUTOLINK = /^<(https?:\/\/[^\s>]+|mailto:[^\s>]+)>/;
const BARE_URL = /^https?:\/\/[^\s<]*[^\s<.,:;"')\]!?]/;

function safeHref(url: string): string | null {
  const trimmed = url.trim();
  if (/^(https?:|mailto:)/i.test(trimmed)) return trimmed;
  if (trimmed.startsWith("/") && !trimmed.startsWith("//")) return trimmed;
  if (trimmed.startsWith("#")) return trimmed;
  return null;
}

function link(href: string, children: ReactNode, key: string): ReactNode {
  const external = /^(https?:)?\/\//i.test(href);
  return (
    <a
      key={key}
      href={href}
      className="font-medium text-primary underline-offset-2 hover:underline"
      {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
    >
      {children}
    </a>
  );
}

/** Find the closing delimiter for emphasis, ignoring ones inside code spans. */
function findClose(text: string, from: number, delim: string): number {
  let i = from;
  while (i < text.length) {
    if (text[i] === "\\") {
      i += 2;
      continue;
    }
    if (text[i] === "`") {
      const end = text.indexOf("`", i + 1);
      if (end === -1) return -1;
      i = end + 1;
      continue;
    }
    if (text.startsWith(delim, i) && i > from && text[i - 1] !== " ") {
      // For single-char delimiters, don't treat the first char of a double as a close.
      if (delim.length === 1 && text[i + 1] === delim) {
        i += 2;
        continue;
      }
      return i;
    }
    i++;
  }
  return -1;
}

function renderInline(text: string, ctx: Ctx): ReactNode[] {
  const out: ReactNode[] = [];
  let buffer = "";
  let n = 0;
  const key = () => `${ctx.keyPrefix}-i${n++}`;
  const flush = () => {
    if (buffer) {
      out.push(buffer);
      buffer = "";
    }
  };

  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    const rest = text.slice(i);

    // Escaped punctuation renders literally.
    if (ch === "\\" && /[\\`*_{}[\]()#+\-.!|~>]/.test(text[i + 1] ?? "")) {
      buffer += text[i + 1];
      i += 2;
      continue;
    }

    if (ch === "`") {
      const ticks = /^`+/.exec(rest)![0];
      const end = text.indexOf(ticks, i + ticks.length);
      if (end !== -1) {
        flush();
        out.push(
          <code
            key={key()}
            className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em] text-foreground"
          >
            {text.slice(i + ticks.length, end).replace(/^ (.*) $/, "$1")}
          </code>,
        );
        i = end + ticks.length;
        continue;
      }
    }

    if (ch === "[") {
      const cite = CITE_INLINE.exec(rest);
      if (cite) {
        flush();
        const node = ctx.renderCitation?.(cite[1].trim(), key());
        if (node) out.push(node);
        i += cite[0].length;
        continue;
      }
      const linkMatch = LINK_INLINE.exec(rest);
      if (linkMatch) {
        const href = safeHref(linkMatch[2]);
        flush();
        const k = key();
        const label = renderInline(linkMatch[1], { ...ctx, keyPrefix: k });
        out.push(href ? link(href, label, k) : <Fragment key={k}>{label}</Fragment>);
        i += linkMatch[0].length;
        continue;
      }
    }

    if (ch === "<") {
      const auto = AUTOLINK.exec(rest);
      if (auto) {
        flush();
        out.push(link(auto[1], auto[1].replace(/^mailto:/i, ""), key()));
        i += auto[0].length;
        continue;
      }
    }

    if ((ch === "h" || ch === "H") && (i === 0 || /[\s(]/.test(text[i - 1]))) {
      const url = BARE_URL.exec(rest);
      if (url) {
        flush();
        out.push(link(url[0], url[0], key()));
        i += url[0].length;
        continue;
      }
    }

    if (ch === "*" || ch === "_" || ch === "~") {
      const prevWord = i > 0 && /\w/.test(text[i - 1]);
      const double = text[i + 1] === ch;
      // Intra-word underscores (snake_case) are literal.
      if (!(ch === "_" && prevWord)) {
        if (ch === "~" && double) {
          const close = findClose(text, i + 2, "~~");
          if (close !== -1) {
            flush();
            const k = key();
            out.push(
              <del key={k} className="text-muted-foreground">
                {renderInline(text.slice(i + 2, close), { ...ctx, keyPrefix: k })}
              </del>,
            );
            i = close + 2;
            continue;
          }
        } else if (ch !== "~" && double && text[i + 2] && text[i + 2] !== " ") {
          const close = findClose(text, i + 2, ch + ch);
          if (close !== -1) {
            flush();
            const k = key();
            out.push(
              <strong key={k} className="font-semibold">
                {renderInline(text.slice(i + 2, close), { ...ctx, keyPrefix: k })}
              </strong>,
            );
            i = close + 2;
            continue;
          }
        } else if (ch !== "~" && !double && text[i + 1] && text[i + 1] !== " ") {
          const close = findClose(text, i + 1, ch);
          const nextIsWord = close !== -1 && /\w/.test(text[close + 1] ?? "");
          if (close !== -1 && !(ch === "_" && nextIsWord)) {
            flush();
            const k = key();
            out.push(
              <em key={k}>{renderInline(text.slice(i + 1, close), { ...ctx, keyPrefix: k })}</em>,
            );
            i = close + 1;
            continue;
          }
        }
      }
    }

    buffer += ch;
    i++;
  }
  flush();
  return out;
}
