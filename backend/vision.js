require("dotenv").config({
  path: require("path").join(__dirname, ".env"),
  quiet: true,
});

const fs = require("fs");
const path = require("path");
const OpenAI = require("openai");
const sharp = require("sharp");

const MODEL = "gpt-4o-mini";
const REQUIRED_FIELDS = [
  "app_or_site",
  "action_summary",
  "content_excerpt",
  "inferred_task_type",
  "confidence",
];

// Rough gpt-4o-mini list prices (USD per 1M tokens) for sanity-check logging.
const PRICE_IN_PER_M = 0.15;
const PRICE_OUT_PER_M = 0.6;

const openai = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });

const SYSTEM_PROMPT = `You analyze screenshots of a user's browser/desktop activity.
Return ONLY a JSON object (no markdown fences, no commentary) with this exact shape:
{
  "app_or_site": string,        // e.g. "Gmail", "Jira", "Salesforce", "Wikipedia"
  "action_summary": string,     // one sentence: what the user appears to be doing
  "content_excerpt": string,    // short verbatim-ish text visible that is relevant to the task
  "inferred_task_type": string, // e.g. "writing_email", "editing_ticket", "reviewing_doc", "browsing"
  "confidence": number          // 0-1, how confident you are in this reading
}`;

const STRICT_RETRY_PROMPT = `Your previous reply was not valid JSON matching the required schema.
Reply with ONLY a single JSON object and nothing else — no markdown fences, no prose.
Required keys (all must be present):
app_or_site (string), action_summary (string), content_excerpt (string),
inferred_task_type (string), confidence (number 0-1).`;

function fallbackResult(rawText) {
  return {
    app_or_site: "unknown",
    action_summary: "Could not parse model response",
    content_excerpt: String(rawText || "").slice(0, 2000),
    inferred_task_type: "unknown",
    confidence: 0,
  };
}

function stripMarkdownFences(text) {
  const trimmed = String(text || "").trim();
  const fenced = /^```(?:json)?\s*([\s\S]*?)\s*```$/i.exec(trimmed);
  if (fenced) return fenced[1].trim();
  return trimmed;
}

function parseAndValidate(rawText) {
  const cleaned = stripMarkdownFences(rawText);
  let parsed;
  try {
    parsed = JSON.parse(cleaned);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return null;
  }
  for (const field of REQUIRED_FIELDS) {
    if (!(field in parsed)) return null;
  }
  if (typeof parsed.app_or_site !== "string") return null;
  if (typeof parsed.action_summary !== "string") return null;
  if (typeof parsed.content_excerpt !== "string") return null;
  if (typeof parsed.inferred_task_type !== "string") return null;
  if (typeof parsed.confidence !== "number" || Number.isNaN(parsed.confidence)) {
    return null;
  }
  parsed.confidence = Math.max(0, Math.min(1, parsed.confidence));
  return parsed;
}

/** Rough char-based estimate when the API omits usage. */
function estimateTextTokens(text) {
  return Math.ceil(String(text || "").length / 4);
}

function logCost({ label, usage, promptText, completionText, jpegBytes }) {
  let promptTokens;
  let completionTokens;

  if (usage && typeof usage.prompt_tokens === "number") {
    promptTokens = usage.prompt_tokens;
    completionTokens = usage.completion_tokens ?? 0;
  } else {
    // ~85 image tokens for a detail:low tile + text estimate
    const imageTokens = 85;
    promptTokens = estimateTextTokens(promptText) + imageTokens;
    completionTokens = estimateTextTokens(completionText);
  }

  const total = promptTokens + completionTokens;
  const estUsd =
    (promptTokens / 1e6) * PRICE_IN_PER_M +
    (completionTokens / 1e6) * PRICE_OUT_PER_M;

  console.log(
    `[vision] ${label} tokens≈ prompt=${promptTokens} completion=${completionTokens} total=${total}` +
      ` | jpeg≈${Math.round(jpegBytes / 1024)}KB | est≈$${estUsd.toFixed(6)}`
  );
}

async function resizeCaptureToJpeg(filepath) {
  const abs = path.isAbsolute(filepath)
    ? filepath
    : path.join(__dirname, filepath);

  if (!fs.existsSync(abs)) {
    throw new Error(`Capture not found: ${abs}`);
  }

  return sharp(abs)
    .resize(768, 768, { fit: "inside", withoutEnlargement: true })
    .jpeg({ quality: 85 })
    .toBuffer();
}

async function callVision({
  jpegBase64,
  url,
  tabTitle,
  timestamp,
  strict,
  priorRaw,
}) {
  const contextBlock = [
    `url: ${url || ""}`,
    `tabTitle: ${tabTitle || ""}`,
    `timestamp: ${timestamp ?? ""}`,
  ].join("\n");

  const userText = strict
    ? `${STRICT_RETRY_PROMPT}\n\nContext:\n${contextBlock}\n\nPrevious invalid reply:\n${String(priorRaw || "").slice(0, 500)}`
    : `Context:\n${contextBlock}\n\nAnalyze the screenshot and return the JSON object.`;

  const messages = [
    { role: "system", content: SYSTEM_PROMPT },
    {
      role: "user",
      content: [
        { type: "text", text: userText },
        {
          type: "image_url",
          image_url: {
            url: `data:image/jpeg;base64,${jpegBase64}`,
            detail: "low",
          },
        },
      ],
    },
  ];

  const response = await openai.chat.completions.create({
    model: MODEL,
    messages,
    temperature: strict ? 0 : 0.2,
    max_tokens: 400,
  });

  const raw = response.choices?.[0]?.message?.content ?? "";
  return { raw, usage: response.usage, userText };
}

/**
 * Summarize a screen capture with gpt-4o-mini vision.
 * @param {{ filepath: string, url?: string, tabTitle?: string, timestamp?: number|string }} args
 */
async function summarizeCapture({ filepath, url, tabTitle, timestamp }) {
  if (!process.env.OPENAI_API_KEY) {
    throw new Error(
      "OPENAI_API_KEY is not set. Add it to backend/.env (see .env.example)."
    );
  }

  const jpegBuffer = await resizeCaptureToJpeg(filepath);
  const jpegBase64 = jpegBuffer.toString("base64");

  const first = await callVision({
    jpegBase64,
    url,
    tabTitle,
    timestamp,
    strict: false,
  });

  logCost({
    label: "attempt-1",
    usage: first.usage,
    promptText: SYSTEM_PROMPT + first.userText,
    completionText: first.raw,
    jpegBytes: jpegBuffer.length,
  });

  const parsed = parseAndValidate(first.raw);
  if (parsed) return parsed;

  console.warn("[vision] JSON parse/validate failed; retrying with stricter prompt");

  const second = await callVision({
    jpegBase64,
    url,
    tabTitle,
    timestamp,
    strict: true,
    priorRaw: first.raw,
  });

  logCost({
    label: "attempt-2",
    usage: second.usage,
    promptText: SYSTEM_PROMPT + second.userText,
    completionText: second.raw,
    jpegBytes: jpegBuffer.length,
  });

  const retried = parseAndValidate(second.raw);
  if (retried) return retried;

  console.error("[vision] parse failed after retry; returning fallback");
  return fallbackResult(second.raw || first.raw);
}

module.exports = { summarizeCapture };

// Standalone smoke test:
//   node vision.js captures/1783794934210_1783794934210-4kz6mh.png
if (require.main === module) {
  (async () => {
    const filepath = process.argv[2];
    if (!filepath) {
      console.error(
        "Usage: node vision.js <path-to-png> [url] [tabTitle] [timestamp]"
      );
      process.exit(1);
    }

    const url = process.argv[3] || "";
    const tabTitle = process.argv[4] || "";
    const timestamp = process.argv[5] || Date.now();

    const result = await summarizeCapture({
      filepath,
      url,
      tabTitle,
      timestamp,
    });
    console.log(JSON.stringify(result, null, 2));
  })().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
