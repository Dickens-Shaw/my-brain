#!/usr/bin/env node
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { loadConfig } from "@my-brain/config";
import { OpenAIEmbedder } from "../embedder.js";
import { hybridSearch } from "../search.js";
import { SqliteIndex } from "../sqlite-store.js";

type GoldenItem = {
  id: string;
  query: string;
  expected_doc_ids: string[];
  notes?: string;
};

async function main(): Promise<void> {
  // findRepoRoot may not be exported — resolve relative to cwd
  let repoRoot = process.cwd();
  while (
    !existsSync(resolve(repoRoot, "pnpm-workspace.yaml")) &&
    repoRoot !== "/"
  ) {
    repoRoot = resolve(repoRoot, "..");
  }
  const goldenPath =
    process.argv[2] ?? resolve(repoRoot, "eval/golden-questions.json");
  if (!existsSync(goldenPath)) {
    throw new Error(`Golden questions not found: ${goldenPath}`);
  }
  const items = JSON.parse(readFileSync(goldenPath, "utf8")) as GoldenItem[];
  const filled = items.filter((i) => i.expected_doc_ids?.length);
  if (filled.length < 1) {
    console.warn(
      `Golden set has ${items.length} placeholders but none filled with expected_doc_ids.`,
    );
    console.warn(
      "Fill eval/golden-questions.json with real queries from your own use cases before trusting scores.",
    );
  }

  const config = loadConfig({ requireOpenAI: true });
  if (!existsSync(config.indexPath)) {
    throw new Error(`Index missing at ${config.indexPath}`);
  }
  const index = new SqliteIndex(config.indexPath, config.embeddingDimensions);
  const embedder = new OpenAIEmbedder({
    apiKey: config.openaiApiKey!,
    baseUrl: config.openaiBaseUrl,
    model: config.embeddingModel,
    dimensions: config.embeddingDimensions,
  });

  let top5 = 0;
  let top10 = 0;
  const misses: string[] = [];

  for (const item of filled) {
    const { hits } = await hybridSearch(index, embedder, {
      query: item.query,
      limit: 10,
    });
    const ids = hits.map((h) => h.docId);
    const hit5 = item.expected_doc_ids.some((id) => ids.slice(0, 5).includes(id));
    const hit10 = item.expected_doc_ids.some((id) => ids.includes(id));
    if (hit5) top5 += 1;
    if (hit10) top10 += 1;
    else misses.push(item.id);
    console.log(
      `${hit5 ? "✓" : hit10 ? "~" : "✗"} ${item.id}: ${item.query.slice(0, 60)}`,
    );
  }

  const n = filled.length || 1;
  console.log(`\nEvaluated ${filled.length}/${items.length} filled questions`);
  console.log(`top5:  ${((top5 / n) * 100).toFixed(1)}% (${top5}/${filled.length})`);
  console.log(`top10: ${((top10 / n) * 100).toFixed(1)}% (${top10}/${filled.length})`);
  if (misses.length) console.log(`misses: ${misses.join(", ")}`);
  index.close();
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
