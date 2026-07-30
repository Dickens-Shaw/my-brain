import {
  assertEmbeddingDimensions,
  embeddingSupportsDimensions,
} from "@my-brain/core";
import type { Embedder } from "./types.js";

export class OpenAIEmbedder implements Embedder {
  readonly model: string;
  readonly dimensions: number;
  private apiKey: string;
  private baseUrl: string;

  constructor(options: {
    apiKey: string;
    baseUrl?: string;
    model: string;
    dimensions: number;
  }) {
    this.apiKey = options.apiKey;
    this.baseUrl = (options.baseUrl ?? "https://api.openai.com/v1").replace(
      /\/$/,
      "",
    );
    this.model = options.model;
    this.dimensions = options.dimensions;
  }

  async embed(texts: string[]): Promise<number[][]> {
    if (!texts.length) return [];
    const res = await fetch(`${this.baseUrl}/embeddings`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${this.apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: this.model,
        input: texts,
        ...(embeddingSupportsDimensions(this.model)
          ? { dimensions: this.dimensions }
          : {}),
      }),
    });
    if (!res.ok) {
      throw new Error(
        `embeddings failed (${res.status}): ${(await res.text()).slice(0, 400)}`,
      );
    }
    const json = (await res.json()) as {
      data: { embedding: number[]; index: number }[];
    };
    const vectors = [...json.data]
      .sort((a, b) => a.index - b.index)
      .map((d) => d.embedding);
    assertEmbeddingDimensions(vectors, this.dimensions, this.model);
    return vectors;
  }
}
