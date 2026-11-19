import type { AppConfig } from "@my-brain/config";
import { chunkMarkdown } from "./chunker.js";
import { chunkerSignature } from "./chunker-config.js";
import { OpenAIEmbedder } from "./embedder.js";
import { scanVaultDocs } from "./scan.js";
import { SqliteIndex } from "./sqlite-store.js";
import type { Embedder } from "./types.js";

export type IndexBuildResult = {
  indexed: number;
  skipped: number;
  deleted: number;
  /** Chunks written by this run. */
  chunks: number;
  /** Set when the index had to be rebuilt because its parameters changed. */
  rebuilt: boolean;
};

export async function buildIndex(options: {
  config: AppConfig;
  full?: boolean;
  /** Override the embedding implementation (tests, local models). */
  embedder?: Embedder;
}): Promise<IndexBuildResult> {
  const { config } = options;
  if (!options.embedder && !config.openaiApiKey) {
    throw new Error("OPENAI_API_KEY required for indexing");
  }

  const index = new SqliteIndex(config.indexPath, config.embeddingDimensions);
  const embedder =
    options.embedder ??
    new OpenAIEmbedder({
      apiKey: config.openaiApiKey!,
      baseUrl: config.openaiBaseUrl,
      model: config.embeddingModel,
      dimensions: config.embeddingDimensions,
    });

  // The index records how it was built. Vectors from different models are not
  // comparable, and changing the chunker changes the chunks — so a mismatch
  // means the stored vectors are meaningless for the current configuration.
  const meta = index.getMeta();
  const signature = chunkerSignature();
  const expected = {
    embedding_model: config.embeddingModel,
    embedding_dimensions: String(config.embeddingDimensions),
    chunker: signature,
  };

  const mismatches = Object.entries(expected).filter(
    ([key, value]) => meta[key] !== undefined && meta[key] !== value,
  );
  const isEmpty = index.chunkCount() === 0;
  let rebuilt = false;

  if (mismatches.length && !isEmpty && !options.full) {
    const detail = mismatches
      .map(([key, value]) => `${key}: indexed "${meta[key]}" vs current "${value}"`)
      .join("; ");
    index.close();
    throw new Error(
      `Index was built with different settings (${detail}).\n` +
        "Vectors from different models or chunkers are not comparable, so the " +
        "index must be rebuilt. Run `pnpm index -- --full` to rebuild it.",
    );
  }

  if (mismatches.length || isEmpty) {
    // A rebuild starts from scratch: old chunks were produced by settings that
    // no longer apply.
    if (!isEmpty) {
      console.log(
        `Index settings changed (${mismatches
          .map(([k]) => k)
          .join(", ")}) — rebuilding from scratch`,
      );
      index.deleteByDocIds([...index.getDocHashes().keys()]);
    }
    for (const [key, value] of Object.entries(expected)) {
      index.setMeta(key, value);
    }
    rebuilt = !isEmpty;
  }

  const docs = scanVaultDocs(config.vaultPath);
  const existing = index.getDocHashes();
  const seen = new Set<string>();

  let indexed = 0;
  let skipped = 0;
  let deleted = 0;
  let chunksWritten = 0;

  const toEmbed: {
    doc: (typeof docs)[0];
    chunks: ReturnType<typeof chunkMarkdown>;
  }[] = [];

  for (const doc of docs) {
    seen.add(doc.docId);
    const prev = existing.get(doc.docId);
    if (!options.full && prev) {
      if (prev.contentHash === doc.contentHash) {
        if (prev.metaHash !== doc.metaHash) {
          index.updateDocMeta(doc.docId, doc.primaryTopic, doc.metaHash);
        }
        skipped += 1;
        continue;
      }
    }
    const chunks = chunkMarkdown({
      markdown: doc.body,
      docId: doc.docId,
      relPath: doc.relPath,
      title: doc.title,
      source: doc.source,
      scope: doc.scope,
      primaryTopic: doc.primaryTopic,
      contentHash: doc.contentHash,
    });
    toEmbed.push({ doc, chunks });
  }

  // Delete removed docs
  for (const id of existing.keys()) {
    if (!seen.has(id)) {
      index.deleteByDocIds([id]);
      deleted += 1;
    }
  }

  const batchSize = 64;
  for (let i = 0; i < toEmbed.length; i++) {
    const { doc, chunks } = toEmbed[i]!;
    // Delete old chunks for this doc first
    index.deleteByDocIds([doc.docId]);
    index.upsertDoc({
      docId: doc.docId,
      relPath: doc.relPath,
      title: doc.title,
      source: doc.source,
      scope: doc.scope,
      primaryTopic: doc.primaryTopic,
      contentHash: doc.contentHash,
      metaHash: doc.metaHash,
    });

    for (let j = 0; j < chunks.length; j += batchSize) {
      const slice = chunks.slice(j, j + batchSize);
      const texts = slice.map((c) => `${c.headingPath}\n\n${c.text}`);
      const embeddings = await embedder.embed(texts);
      index.upsertChunks(
        slice.map((c, k) => ({
          ...c,
          embedding: embeddings[k]!,
        })),
      );
      chunksWritten += slice.length;
    }
    indexed += 1;
    if (indexed % 20 === 0) {
      console.log(`  indexed ${indexed}/${toEmbed.length} docs`);
    }
  }

  index.setMeta("last_indexed_at", new Date().toISOString());
  index.setMeta("chunk_count", String(chunksWritten || index.chunkCount()));
  // The trace table is the only one that grows without bound.
  const pruned = index.pruneTraces();
  if (pruned > 0) console.log(`  pruned ${pruned} old search traces`);
  index.close();
  return { indexed, skipped, deleted, chunks: chunksWritten, rebuilt };
}
