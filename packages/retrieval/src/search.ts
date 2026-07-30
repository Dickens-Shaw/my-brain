import type { Scope } from "@my-brain/core";
import type { Embedder, SearchHit, SearchOptions, SearchTrace } from "./types.js";
import type { SqliteIndex } from "./sqlite-store.js";
import { rrfFuse } from "./rrf.js";

export async function hybridSearch(
  index: SqliteIndex,
  embedder: Embedder,
  options: SearchOptions,
): Promise<{ hits: SearchHit[]; trace: SearchTrace }> {
  const limit = Math.min(Math.max(options.limit ?? 10, 1), 20);
  const filter = {
    scope: options.scope && options.scope !== "all" ? options.scope : undefined,
    topic: options.topic,
    source: options.source,
  };

  const timings: Record<string, number> = {};
  const t0 = Date.now();

  let vectorHits: { chunkId: string; score: number }[] = [];
  try {
    const [qEmb] = await embedder.embed([options.query]);
    const t1 = Date.now();
    timings.embed = t1 - t0;
    vectorHits = index.vectorSearch(qEmb!, 50, filter);
    timings.vector = Date.now() - t1;
  } catch (err) {
    timings.embed_error = 1;
    console.error(
      "vector search skipped:",
      err instanceof Error ? err.message : err,
    );
  }

  const t2 = Date.now();
  let bm25Hits: { chunkId: string; score: number }[] = [];
  try {
    bm25Hits = index.bm25Search(options.query, 50, filter);
  } catch {
    bm25Hits = [];
  }
  timings.bm25 = Date.now() - t2;

  const t3 = Date.now();
  const fused = rrfFuse([vectorHits, bm25Hits]).slice(0, limit);
  timings.fuse = Date.now() - t3;

  const chunkMap = index.getChunks(fused.map((f) => f.chunkId));
  const vectorRank = new Map(vectorHits.map((h, i) => [h.chunkId, i + 1]));
  const bm25Rank = new Map(bm25Hits.map((h, i) => [h.chunkId, i + 1]));

  const hits: SearchHit[] = fused
    .map((f) => {
      const c = chunkMap.get(f.chunkId);
      if (!c) return null;
      return {
        chunkId: c.chunkId,
        docId: c.docId,
        relPath: c.relPath,
        title: c.title,
        headingPath: c.headingPath,
        text: c.text,
        source: c.source,
        scope: c.scope as Scope,
        score: f.score,
        vectorRank: vectorRank.get(f.chunkId) ?? null,
        bm25Rank: bm25Rank.get(f.chunkId) ?? null,
      } satisfies SearchHit;
    })
    .filter((h): h is SearchHit => h !== null);

  const trace: SearchTrace = {
    query: options.query,
    options,
    vectorHits: vectorHits.map((h, i) => ({
      chunkId: h.chunkId,
      rank: i + 1,
      score: h.score,
    })),
    bm25Hits: bm25Hits.map((h, i) => ({
      chunkId: h.chunkId,
      rank: i + 1,
      score: h.score,
    })),
    fused: fused.map((h, i) => ({
      chunkId: h.chunkId,
      rank: i + 1,
      score: h.score,
    })),
    timingsMs: timings,
  };

  index.writeTrace({
    query: options.query,
    options,
    vector: trace.vectorHits,
    bm25: trace.bm25Hits,
    fused: trace.fused,
    timings,
  });

  return { hits, trace };
}
