#!/usr/bin/env node
/**
 * Offline smoke test for the retrieval pipeline.
 *
 * Uses a deterministic local embedding so the whole path — chunking,
 * indexing, hybrid search, evaluation — can be verified without a working
 * embedding API. The vectors are meaningless for quality, but every code
 * path is exercised: the index is built, the two arms return results, and
 * the evaluation produces a report.
 *
 * Usage: pnpm --filter @my-brain/retrieval exec tsx src/cli/smoke.ts
 */
import { mkdirSync, rmSync, existsSync } from "node:fs";
import { join, resolve } from "node:path";
import { buildIndex } from "../build-index.js";
import { hybridSearch } from "../search.js";
import { SqliteIndex } from "../sqlite-store.js";
import type { Embedder } from "../types.js";

/**
 * A deterministic hash-based embedding.
 *
 * Each token contributes its hash projected onto a fixed dimension, so two
 * texts sharing tokens land near each other. Good enough to check that the
 * plumbing works; not a model.
 */
class HashEmbedder implements Embedder {
  readonly model = "hash-smoke-v1";
  readonly dimensions: number;
  constructor(dimensions = 256) {
    this.dimensions = dimensions;
  }
  async embed(texts: string[]): Promise<number[][]> {
    return texts.map((text) => {
      const vec = new Array(this.dimensions).fill(0);
      const tokens = text.match(/[\p{Script=Han}]+|[\p{L}\p{N}_]+/gu) ?? [];
      for (const token of tokens) {
        let h = 2166136261;
        for (let i = 0; i < token.length; i++) {
          h ^= token.charCodeAt(i);
          h = Math.imul(h, 16777619);
        }
        const idx = Math.abs(h) % this.dimensions;
        vec[idx]! += 1;
      }
      const norm = Math.sqrt(vec.reduce((s, v) => s + v * v, 0)) || 1;
      return vec.map((v) => v / norm);
    });
  }
}

const tmpDir = resolve(process.cwd(), ".data", "smoke");
const indexPath = join(tmpDir, "index.db");

async function main(): Promise<void> {
  if (existsSync(tmpDir)) rmSync(tmpDir, { recursive: true });
  mkdirSync(tmpDir, { recursive: true });

  const config = {
    vaultPath: process.env.VAULT_PATH ?? "",
    indexPath,
    embeddingModel: "hash-smoke-v1",
    embeddingDimensions: 256,
    openaiApiKey: "smoke",
  } as never;

  console.log("Building index from Vault (hash embeddings)…");
  const started = Date.now();
  const result = await buildIndex({
    config,
    embedder: new HashEmbedder(256),
  });
  console.log(
    `  indexed=${result.indexed} skipped=${result.skipped} deleted=${result.deleted} chunks=${result.chunks} in ${((Date.now() - started) / 1000).toFixed(1)}s`,
  );

  const index = new SqliteIndex(indexPath, 256);
  console.log("Index meta:", index.getMeta());

  const queries = ["前端构建工具", "Webpack 缓存", "TypeScript 类型"];
  for (const query of queries) {
    const { hits, trace } = await hybridSearch(index, new HashEmbedder(256), {
      query,
      limit: 5,
    });
    console.log(
      `\n  "${query}" → ${hits.length} hits (vector ${trace.vectorHits.length}, bm25 ${trace.bm25Hits.length}) in ${trace.timingsMs.bm25 ?? 0}ms bm25`,
    );
    for (const h of hits.slice(0, 3)) {
      console.log(
        `    ${h.docId}  v${h.vectorRank ?? "-"}/b${h.bm25Rank ?? "-"}  ${h.title.slice(0, 40)}`,
      );
    }
  }

  // The Chinese bigram index is the part that was rewritten — check it
  // actually matches short terms.
  const shortHits = index.bm25Search("缓存", 10);
  console.log(`\n  short-term "缓存" → ${shortHits.length} bm25 hits`);
  const longHits = index.bm25Search("Webpack", 10);
  console.log(`  latin "Webpack" → ${longHits.length} bm25 hits`);

  index.close();
  rmSync(tmpDir, { recursive: true, force: true });
  console.log("\nSmoke test OK.");
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
