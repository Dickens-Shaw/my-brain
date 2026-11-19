import { existsSync } from "node:fs";
import { join } from "node:path";
import type { AppConfig } from "@my-brain/config";
import type { NormalizedDoc } from "./types.js";
import { ensureDir, readJson, writeJson } from "./fsutil.js";
import { docEmbedText } from "./outline.js";
import { embedTexts } from "./openai.js";

export type DocEmbeddingStore = {
  model: string;
  dimensions: number;
  /**
   * Cache key → vector.
   *
   * The key is `id` + body hash, not just `id`: ids are permanent, but the
   * body behind an id can change when the source article is updated. Keying
   * on the id alone would keep serving a stale vector for changed content.
   */
  vectors: Record<string, number[]>;
};

function storePath(migrateRoot: string): string {
  return join(migrateRoot, "doc-embeddings.json");
}

/** Cache key for a document's vector. */
export function embedKey(doc: {
  id: string;
  body_hash: string;
}): string {
  return `${doc.id}@${doc.body_hash.slice(0, 16)}`;
}

export function loadDocEmbeddings(migrateRoot: string): DocEmbeddingStore {
  const path = storePath(migrateRoot);
  if (!existsSync(path)) {
    return { model: "", dimensions: 0, vectors: {} };
  }
  const parsed = readJson<Partial<DocEmbeddingStore>>(path);
  return {
    model: parsed.model ?? "",
    dimensions: parsed.dimensions ?? 0,
    vectors: parsed.vectors ?? {},
  };
}

export async function embedDocuments(options: {
  migrateRoot: string;
  docs: NormalizedDoc[];
  config: AppConfig;
  batchSize?: number;
}): Promise<DocEmbeddingStore> {
  const { migrateRoot, docs, config } = options;
  if (!config.openaiApiKey) {
    throw new Error("OPENAI_API_KEY required for document embeddings");
  }

  ensureDir(migrateRoot);
  const store = loadDocEmbeddings(migrateRoot);
  store.model = config.embeddingModel;
  store.dimensions = config.embeddingDimensions;

  const targets = docs.filter((d) => d.type === "article");
  const pending = targets.filter((d) => !store.vectors[embedKey(d)]);
  console.log(
    `Doc embeddings: ${targets.length - pending.length}/${targets.length} cached, ${pending.length} to fetch`,
  );

  const batchSize = options.batchSize ?? 64;
  for (let i = 0; i < pending.length; i += batchSize) {
    const batch = pending.slice(i, i + batchSize);
    const texts = batch.map((d) => docEmbedText(d));
    const { embeddings } = await embedTexts({
      apiKey: config.openaiApiKey,
      baseUrl: config.openaiBaseUrl,
      model: config.embeddingModel,
      dimensions: config.embeddingDimensions,
      texts,
    });
    for (let j = 0; j < batch.length; j++) {
      store.vectors[embedKey(batch[j]!)] = embeddings[j]!;
    }
    writeJson(storePath(migrateRoot), store);
    console.log(
      `  embedded ${Math.min(i + batchSize, pending.length)}/${pending.length}`,
    );
  }

  writeJson(storePath(migrateRoot), store);
  return store;
}
