export { MACHINE_DIRS, HANDWRITTEN_DIRS, VAULT_ROOT_DIRS, ensureVaultSkeleton } from "./skeleton.js";
export {
  safeFilename,
  parseFrontmatter,
  parseAndValidateFrontmatter,
  serializeDocument,
} from "./frontmatter.js";
export { scopeFromPath, isMachinePath, isHandwrittenPath } from "./paths.js";
export {
  scanMachineDocuments,
  getDocumentById,
  writeMachineDocument,
  type VaultDocument,
} from "./documents.js";
export {
  MACHINE_FIELDS,
  HUMAN_FIELDS,
  fieldOwnership,
  machineFieldsHash,
  bodyHash,
  decideSync,
  mergeFrontmatter,
  type SyncDecision,
  type FieldOwnership,
} from "./sync.js";
export { tidyMachineArea, type TidyResult } from "./tidy.js";
