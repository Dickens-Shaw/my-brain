#!/usr/bin/env node
import { existsSync, readFileSync, writeFileSync } from "node:fs";
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
  /**
   * Questions whose answer is not in the Vault. They are not scored as hits;
   * they are reported separately so the retrieval can be checked for whether
   * it surfaces something confidently wrong.
   */
  unanswerable?: boolean;
  /** Category label, used to break the summary down by question type. */
  category?: string;
};

type Mode = "hybrid" | "bm25" | "vector";
const MODES: Mode[] = ["hybrid", "bm25", "vector"];

type ItemResult = {
  id: string;
  query: string;
  category: string;
  unanswerable: boolean;
  /** Rank of the first expected doc, 0 when not found in the returned set. */
  bestRank: number;
  /** Best rank within each arm's own top-50, 0 when not recalled at all. */
  vectorRank: number;
  bm25Rank: number;
  /** How many expected docs made it into the top 10. */
  hitsInTop10: number;
  expectedCount: number;
};

type ModeSummary = {
  mode: Mode;
  top5: number;
  top10: number;
  mrr: number;
  total: number;
  avgMs: number;
};

function findRepoRoot(start = process.cwd()): string {
  let dir = resolve(start);
  while (
    !existsSync(resolve(dir, "pnpm-workspace.yaml")) &&
    dir !== "/"
  ) {
    dir = resolve(dir, "..");
  }
  return dir;
}

/**
 * Why a question missed. The two retrieval arms each return their own top 50,
 * so the evaluation can tell "never recalled" apart from "recalled but
 * ranked below the cutoff" — the two have completely different fixes.
 */
function missReason(
  bestRank: number,
  vectorRank: number,
  bm25Rank: number,
): string {
  if (bestRank > 0) return "hit";
  if (vectorRank > 0 && bm25Rank > 0) return "recalled-but-low";
  if (vectorRank > 0) return "vector-only";
  if (bm25Rank > 0) return "bm25-only";
  return "not-recalled";
}

async function main(): Promise<void> {
  const repoRoot = findRepoRoot();
  // `pnpm eval -- <path>` forwards the separator as an argument, so it is
  // filtered out before the first positional argument is read.
  const positional = process.argv.slice(2).filter((a) => a !== "--");
  const goldenPath =
    positional[0] ?? resolve(repoRoot, "eval/golden-questions.json");
  if (!existsSync(goldenPath)) {
    throw new Error(`Golden questions not found: ${goldenPath}`);
  }
  const items = JSON.parse(readFileSync(goldenPath, "utf8")) as GoldenItem[];
  const filled = items.filter(
    (i) => i.expected_doc_ids?.length || i.unanswerable,
  );
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

  const results: Record<Mode, ItemResult[]> = {
    hybrid: [],
    bm25: [],
    vector: [],
  };
  const summaries: ModeSummary[] = [];

  for (const mode of MODES) {
    let top5 = 0;
    let top10 = 0;
    let mrrSum = 0;
    let scored = 0;
    let totalMs = 0;

    for (const item of filled) {
      const started = Date.now();
      const { hits, trace } = await hybridSearch(index, embedder, {
        query: item.query,
        limit: 10,
        mode,
      });
      totalMs += Date.now() - started;

      const docIds = hits.map((h) => h.docId);
      const expected = item.expected_doc_ids ?? [];

      // Rank of the first expected document in the returned list.
      let bestRank = 0;
      for (const id of expected) {
        const pos = docIds.indexOf(id);
        if (pos >= 0 && (bestRank === 0 || pos + 1 < bestRank)) {
          bestRank = pos + 1;
        }
      }
      // Ranks inside each arm's own top 50, which is where the trace keeps
      // the full candidate list. The trace stores chunk ids, so the document
      // id is resolved from the chunks table.
      const armChunkIds = [
        ...trace.vectorHits.map((h) => h.chunkId),
        ...trace.bm25Hits.map((h) => h.chunkId),
      ];
      const chunkDocIds = index.getDocIdsByChunkIds(armChunkIds);
      const armRank = (arm: { chunkId: string; rank: number }[]): number => {
        for (const hit of arm) {
          const docId = chunkDocIds.get(hit.chunkId);
          if (docId && expected.includes(docId)) return hit.rank;
        }
        return 0;
      };
      const vectorRank = armRank(trace.vectorHits);
      const bm25Rank = armRank(trace.bm25Hits);

      const hitsInTop10 = expected.filter((id) =>
        docIds.slice(0, 10).includes(id),
      ).length;

      const result: ItemResult = {
        id: item.id,
        query: item.query,
        category: item.category ?? "uncategorized",
        unanswerable: Boolean(item.unanswerable),
        bestRank,
        vectorRank,
        bm25Rank,
        hitsInTop10,
        expectedCount: expected.length,
      };
      results[mode].push(result);

      if (item.unanswerable) {
        // Not scored as a hit. Reported so the top result can be inspected.
        console.log(
          `? ${item.id}: ${item.query.slice(0, 60)} (no answer expected)`,
        );
        continue;
      }

      scored += 1;
      if (bestRank > 0 && bestRank <= 5) top5 += 1;
      if (bestRank > 0 && bestRank <= 10) top10 += 1;
      mrrSum += bestRank > 0 ? 1 / bestRank : 0;

      const reason = missReason(bestRank, vectorRank, bm25Rank);
      const mark = bestRank > 0 && bestRank <= 5 ? "✓" : bestRank > 0 ? "~" : "✗";
      console.log(
        `${mark} ${item.id}: ${item.query.slice(0, 56)}  rank=${bestRank || "-"}${
          reason === "hit" ? "" : `  (${reason})`
        }`,
      );
    }

    summaries.push({
      mode,
      top5,
      top10,
      mrr: scored ? mrrSum / scored : 0,
      total: scored,
      avgMs: filled.length ? totalMs / filled.length : 0,
    });
  }

  console.log(`\nEvaluated ${filled.length}/${items.length} filled questions`);
  console.log("");
  console.log(
    "mode     top5      top10     MRR       avg ms",
  );
  for (const s of summaries) {
    console.log(
      `${s.mode.padEnd(8)} ${((s.top5 / (s.total || 1)) * 100).toFixed(1).padStart(5)}%   ${(
        (s.top10 / (s.total || 1)) *
        100
      )
        .toFixed(1)
        .padStart(5)}%   ${s.mrr.toFixed(3).padStart(6)}   ${s.avgMs.toFixed(0).padStart(6)}`,
    );
  }

  // Miss breakdown for the hybrid mode — this is what decides which
  // candidate change is worth trying.
  const missCounts = new Map<string, string[]>();
  for (const r of results.hybrid) {
    if (r.unanswerable) continue;
    const reason = missReason(r.bestRank, r.vectorRank, r.bm25Rank);
    if (reason === "hit") continue;
    const list = missCounts.get(reason) ?? [];
    list.push(r.id);
    missCounts.set(reason, list);
  }
  if (missCounts.size) {
    console.log("\nMiss reasons (hybrid):");
    for (const [reason, ids] of missCounts) {
      console.log(`  ${reason}: ${ids.join(", ")}`);
    }
  }

  // Questions that need several documents are only counted as a hit when all
  // of them are in the top 10.
  const multi = results.hybrid.filter(
    (r) => !r.unanswerable && r.expectedCount > 1,
  );
  if (multi.length) {
    const complete = multi.filter((r) => r.hitsInTop10 === r.expectedCount);
    console.log(
      `\nMulti-document questions: ${complete.length}/${multi.length} fully in top 10`,
    );
  }

  const reportPath = resolve(repoRoot, ".data", "eval-report.json");
  writeFileSync(
    reportPath,
    JSON.stringify(
      { generated_at: new Date().toISOString(), summaries, results },
      null,
      2,
    ) + "\n",
    "utf8",
  );
  console.log(`\nReport written to ${reportPath}`);

  index.close();
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
