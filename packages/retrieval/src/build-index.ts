import type { AppConfig } from "@my-brain/config";
import { chunkMarkdown } from "./chunker.js";
import { OpenAIEmbedder } from "./embedder.js";
import { scanVaultDocs } from "./scan.js";
import { SqliteIndex } from "./sqlite-store.js";
import type { Embedder } from "./types.js";

export async function buildIndex(options: {
  config: AppConfig;
  full?: boolean;
  /** Override the embedding implementation (tests, local models). */
  embedder?: Embedder;
}): Promise<{ indexed: number; skipped: number; deleted: number }> {
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

  const docs = scanVaultDocs(config.vaultPath);
  const existing = index.getDocHashes();
  const seen = new Set<string>();

  let indexed = 0;
  let skipped = 0;
  let deleted = 0;

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
    }
    indexed += 1;
    if (indexed % 20 === 0) {
      console.log(`  indexed ${indexed}/${toEmbed.length} docs`);
    }
  }

  index.close();
  return { indexed, skipped, deleted };
}
