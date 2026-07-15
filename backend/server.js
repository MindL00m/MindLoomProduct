const express = require("express");
const cors = require("cors");
const fs = require("fs");
const path = require("path");
const { randomUUID } = require("crypto");
const { summarizeCapture } = require("./vision");

const app = express();
const PORT = 3000;
const CAPTURES_DIR = path.join(__dirname, "captures");
const JSONL_PATH = path.join(__dirname, "captures.jsonl");
const SUMMARIES_PATH = path.join(__dirname, "summaries.jsonl");

const VISION_MAX_ATTEMPTS = 3;
const VISION_BASE_DELAY_MS = 1000;

fs.mkdirSync(CAPTURES_DIR, { recursive: true });

app.use(
  cors({
    origin(origin, callback) {
      if (
        !origin ||
        origin.startsWith("chrome-extension://") ||
        origin.startsWith("http://localhost")
      ) {
        callback(null, true);
        return;
      }
      callback(new Error("Not allowed by CORS"));
    },
  })
);
app.use(express.json({ limit: "50mb" }));

function parseDataUrl(dataUrl) {
  const match = /^data:([^;]+);base64,(.+)$/.exec(dataUrl);
  if (!match) {
    throw new Error("Invalid dataUrl");
  }
  return Buffer.from(match[2], "base64");
}

function readJsonl(filePath) {
  if (!fs.existsSync(filePath)) {
    return [];
  }
  return fs
    .readFileSync(filePath, "utf8")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isRetryableOpenAIError(err) {
  if (!err) return false;

  const status = err.status ?? err.statusCode;
  if ([408, 429, 500, 502, 503, 504].includes(status)) {
    return true;
  }

  const code = err.code || err.cause?.code;
  if (
    ["ETIMEDOUT", "ECONNRESET", "ECONNREFUSED", "ENOTFOUND", "UND_ERR_CONNECT_TIMEOUT"].includes(
      code
    )
  ) {
    return true;
  }

  const name = err.name || err.constructor?.name || "";
  if (
    name === "APIConnectionError" ||
    name === "RateLimitError" ||
    name === "InternalServerError" ||
    name === "APIUserAbortError"
  ) {
    return true;
  }

  const message = String(err.message || "").toLowerCase();
  if (
    message.includes("timeout") ||
    message.includes("timed out") ||
    message.includes("rate limit") ||
    message.includes("connection error")
  ) {
    return true;
  }

  return false;
}

async function summarizeWithRetry(args, captureId) {
  let lastErr;
  for (let attempt = 1; attempt <= VISION_MAX_ATTEMPTS; attempt++) {
    try {
      return await summarizeCapture(args);
    } catch (err) {
      lastErr = err;
      const retryable = isRetryableOpenAIError(err);
      if (!retryable || attempt === VISION_MAX_ATTEMPTS) {
        throw err;
      }
      const delayMs = VISION_BASE_DELAY_MS * 2 ** (attempt - 1);
      const message = err instanceof Error ? err.message : String(err);
      console.warn(
        `[backend] summary retry ${attempt}/${VISION_MAX_ATTEMPTS} id=${captureId} waiting ${delayMs}ms: ${message}`
      );
      await sleep(delayMs);
    }
  }
  throw lastErr;
}

async function processVisionSummary(metadata) {
  const { id, filepath, url, tabTitle, timestamp } = metadata;
  try {
    const summary = await summarizeWithRetry(
      { filepath, url, tabTitle, timestamp },
      id
    );

    const record = {
      id,
      timestamp,
      url,
      tabTitle,
      filepath,
      summarizedAt: Date.now(),
      ...summary,
    };

    fs.appendFileSync(SUMMARIES_PATH, `${JSON.stringify(record)}\n`);
    console.log(
      `[backend] summary ok id=${id} app=${summary.app_or_site} task=${summary.inferred_task_type} confidence=${summary.confidence}`
    );
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`[backend] summary failed id=${id}: ${message}`);
  }
}

app.post("/captures", (req, res) => {
  try {
    const { timestamp, dataUrl, url, tabTitle, windowId, id } = req.body;

    if (!timestamp || !dataUrl) {
      res.status(400).json({ error: "timestamp and dataUrl are required" });
      return;
    }

    const captureId = id ? String(id) : randomUUID();
    const filename = `${timestamp}_${captureId}.png`;
    const filepath = path.join("captures", filename);
    const fullPath = path.join(__dirname, filepath);

    const imageBuffer = parseDataUrl(dataUrl);
    fs.writeFileSync(fullPath, imageBuffer);

    const metadata = {
      id: captureId,
      timestamp,
      url: url || "",
      tabTitle: tabTitle || "",
      windowId: windowId ?? null,
      filepath,
    };

    fs.appendFileSync(JSONL_PATH, `${JSON.stringify(metadata)}\n`);

    // Respond immediately; vision runs in the background.
    res.status(201).json({ ok: true, ...metadata });
    void processVisionSummary(metadata);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error("[backend] POST /captures failed:", message);
    res.status(500).json({ error: message });
  }
});

app.get("/captures", (_req, res) => {
  try {
    res.json(readJsonl(JSONL_PATH));
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error("[backend] GET /captures failed:", message);
    res.status(500).json({ error: message });
  }
});

app.get("/summaries", (_req, res) => {
  try {
    res.json(readJsonl(SUMMARIES_PATH));
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error("[backend] GET /summaries failed:", message);
    res.status(500).json({ error: message });
  }
});

app.listen(PORT, () => {
  console.log(`workstyle-capture backend listening on http://localhost:${PORT}`);
});
