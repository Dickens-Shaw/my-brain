export type {
  Chunk,
  SearchHit,
  SearchOptions,
  SearchTrace,
  Embedder,
  VectorStore,
  TextIndex,
} from "./types.js";
export { OpenAIEmbedder } from "./embedder.js";
export { chunkMarkdown, estimateTokens } from "./chunker.js";
export { SqliteIndex } from "./sqlite-store.js";
export { hybridSearch } from "./search.js";
export { buildIndex } from "./build-index.js";
export { scanVaultDocs } from "./scan.js";
export { rrfFuse } from "./rrf.js";
