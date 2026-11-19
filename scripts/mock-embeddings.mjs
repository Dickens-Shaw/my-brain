#!/usr/bin/env node
/**
 * A local mock of an OpenAI-compatible embeddings endpoint.
 *
 * It exists so the pipeline script can be exercised end to end without a real
 * API key: the vectors are deterministic hashes rather than a model, so the
 * retrieval quality is meaningless, but every code path — preflight,
 * migration, indexing, evaluation — runs exactly as it would in production.
 *
 * Usage: node scripts/mock-embeddings.mjs [port]
 */
import { createServer } from "node:http";

const port = Number(process.argv[2] ?? 8899);
const DIM = 1024;

function hashEmbed(text) {
  const vec = new Array(DIM).fill(0);
  const tokens = String(text).match(/[\p{Script=Han}]+|[\p{L}\p{N}_]+/gu) ?? [];
  for (const token of tokens) {
    let h = 2166136261;
    for (let i = 0; i < token.length; i++) {
      h ^= token.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    vec[Math.abs(h) % DIM] += 1;
  }
  const norm = Math.sqrt(vec.reduce((s, v) => s + v * v, 0)) || 1;
  return vec.map((v) => v / norm);
}

createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : {};

  const send = (status, payload) => {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify(payload));
  };

  if (req.url.endsWith("/embeddings")) {
    const input = Array.isArray(body.input) ? body.input : [body.input];
    const dim = body.dimensions ?? DIM;
    if (dim !== DIM) {
      return send(400, { error: { message: `this mock only produces ${DIM} dimensions` } });
    }
    return send(200, {
      object: "list",
      model: body.model ?? "mock",
      data: input.map((text, index) => ({
        object: "embedding",
        index,
        embedding: hashEmbed(text),
      })),
      usage: { prompt_tokens: 0, total_tokens: 0 },
    });
  }

  if (req.url.endsWith("/chat/completions")) {
    // The pipeline asks for JSON with {level1, level2, name_zh}. Return a
    // deterministic name derived from the sample titles so the taxonomy is
    // reviewable rather than a wall of placeholders.
    const messages = body.messages ?? [];
    let payload = {};
    for (const m of messages) {
      if (m.role === "user") {
        try {
          payload = JSON.parse(m.content);
        } catch {
          payload = {};
        }
      }
    }
    const titles = payload.sample_titles ?? [];
    const first = String(titles[0] ?? "未分类").slice(0, 6);
    const content = JSON.stringify({
      level1: first || "未命名",
      level2: `mock-${titles.length}`,
      name_zh: first || "未命名",
    });
    return send(200, {
      id: "mock",
      object: "chat.completion",
      model: body.model ?? "mock",
      choices: [
        {
          index: 0,
          message: { role: "assistant", content },
          finish_reason: "stop",
        },
      ],
      usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
    });
  }

  send(404, { error: { message: "not found" } });
}).listen(port, () => {
  console.log(`Mock embeddings server on http://127.0.0.1:${port} (${DIM}d)`);
});
