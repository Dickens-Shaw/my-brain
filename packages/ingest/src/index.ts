export { normalizeAll } from "./normalize/index.js";
export { dedupeDocs, bodyTextFingerprint, completeness } from "./dedupe.js";
export { dedupeByBodyText } from "./dedupe-body.js";
export { normalizeSourceUrl } from "./url.js";
export {
  naturalKey,
  aliasNaturalKeys,
  idSequence,
} from "./normalize/identity.js";
export {
  assignIdsByNaturalKey,
  registryFromDocs,
  emptyRegistry,
  type IdRegistry,
} from "./normalize/assign-ids.js";
export { registryFromVault } from "./normalize/vault-registry.js";
export { embedKey, loadDocEmbeddings } from "./embed-docs.js";
export type { NormalizedDoc, ClassifiedDoc, DedupeResult } from "./types.js";
