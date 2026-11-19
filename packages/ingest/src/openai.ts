import {
  assertEmbeddingDimensions,
  embeddingSupportsDimensions,
} from "@my-brain/core";

export type EmbedBatchResult = { embeddings: number[][]; model: string };

export async function embedTexts(options: {
  apiKey: string;
  baseUrl?: string;
  model: string;
  dimensions: number;
  texts: string[];
}): Promise<EmbedBatchResult> {
  const base = (options.baseUrl ?? "https://api.openai.com/v1").replace(/\/$/, "");
  const res = await fetch(`${base}/embeddings`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${options.apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: options.model,
      input: options.texts,
      ...(embeddingSupportsDimensions(options.model)
        ? { dimensions: options.dimensions }
        : {}),
    }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Embeddings request failed (${res.status}): ${text.slice(0, 500)}`);
  }
  const json = (await res.json()) as {
    data: { embedding: number[]; index: number }[];
    model: string;
  };
  const sorted = [...json.data].sort((a, b) => a.index - b.index);
  const embeddings = sorted.map((d) => d.embedding);
  assertEmbeddingDimensions(embeddings, options.dimensions, options.model);
  return {
    embeddings,
    model: json.model,
  };
}

/**
 * Extract a JSON object from a model response.
 *
 * Models asked for JSON sometimes wrap it in a markdown fence or put a
 * sentence in front of it. A bare `JSON.parse` then throws, and because the
 * failure happens mid-run it aborts the whole stage — for `induce` that means
 * losing the cluster naming work for every remaining cluster.
 */
export function extractJson(content: string): unknown {
  const trimmed = content.trim();

  // A fenced block: ```json ... ``` or ``` ... ```
  const fenced = trimmed.match(/```(?:json)?\s*([\s\S]*?)```/);
  const candidate = fenced ? fenced[1]!.trim() : trimmed;

  try {
    return JSON.parse(candidate);
  } catch {
    // Fall through to brace extraction.
  }

  // The first balanced {...} in the text.
  const start = candidate.indexOf("{");
  if (start < 0) {
    throw new Error(`No JSON object in model response: ${trimmed.slice(0, 200)}`);
  }
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let i = start; i < candidate.length; i++) {
    const ch = candidate[i]!;
    if (escaped) {
      escaped = false;
      continue;
    }
    if (ch === "\\") {
      escaped = true;
      continue;
    }
    if (ch === '"') {
      inString = !inString;
      continue;
    }
    if (inString) continue;
    if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) {
        return JSON.parse(candidate.slice(start, i + 1));
      }
    }
  }
  throw new Error(`Unbalanced JSON in model response: ${trimmed.slice(0, 200)}`);
}

export async function chatJson<T>(options: {
  apiKey: string;
  baseUrl?: string;
  model: string;
  system: string;
  user: string;
}): Promise<T> {
  const base = (options.baseUrl ?? "https://api.openai.com/v1").replace(/\/$/, "");
  const res = await fetch(`${base}/chat/completions`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${options.apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: options.model,
      temperature: 0.2,
      response_format: { type: "json_object" },
      messages: [
        { role: "system", content: options.system },
        { role: "user", content: options.user },
      ],
    }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`OpenAI chat failed (${res.status}): ${text.slice(0, 500)}`);
  }
  const json = (await res.json()) as {
    choices: { message: { content: string } }[];
  };
  const content = json.choices[0]?.message?.content;
  if (!content) throw new Error("OpenAI chat returned empty content");
  return extractJson(content) as T;
}
