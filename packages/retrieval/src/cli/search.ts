#!/usr/bin/env node
import { existsSync } from "node:fs";
import { loadConfig } from "@my-brain/config";
import { OpenAIEmbedder } from "../embedder.js";
import { hybridSearch } from "../search.js";
import { SqliteIndex } from "../sqlite-store.js";
import type { Scope } from "@my-brain/core";

function arg(name: string): string | undefined {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

async function main(): Promise<void> {
  const query = arg("--query") ?? process.argv[2];
  if (!query) {
    console.log(
      'Usage: pnpm search -- --query "..." [--scope library] [--topic "前端/构建工具"] [--limit 8]',
    );
    process.exit(1);
  }
  const config = loadConfig({ requireOpenAI: true });
  if (!existsSync(config.indexPath)) {
    throw new Error(`Index not found at ${config.indexPath}. Run: pnpm index`);
  }
  const index = new SqliteIndex(config.indexPath, config.embeddingDimensions);
  const embedder = new OpenAIEmbedder({
    apiKey: config.openaiApiKey!,
    baseUrl: config.openaiBaseUrl,
    model: config.embeddingModel,
    dimensions: config.embeddingDimensions,
  });
  const { hits, trace } = await hybridSearch(index, embedder, {
    query,
    scope: (arg("--scope") as Scope | "all" | undefined) ?? "all",
    topic: arg("--topic"),
    source: arg("--source"),
    limit: Number(arg("--limit") ?? 8),
  });
  for (const [i, h] of hits.entries()) {
    console.log(
      `${i + 1}. [${h.score.toFixed(4)}] ${h.title} (${h.docId}) scope=${h.scope}`,
    );
    console.log(`   ${h.headingPath}`);
    console.log(`   ${h.text.slice(0, 160).replace(/\s+/g, " ")}…`);
    console.log(`   path=${h.relPath} vec=#${h.vectorRank} bm25=#${h.bm25Rank}`);
  }
  console.log(
    `\nTimings(ms): ${JSON.stringify(trace.timingsMs)} | vector=${trace.vectorHits.length} bm25=${trace.bm25Hits.length}`,
  );
  index.close();
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
