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
  return JSON.parse(content) as T;
}
