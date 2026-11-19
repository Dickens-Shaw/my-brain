import type { NormalizeResult } from "../types.js";
import type { IdRegistry } from "./assign-ids.js";
import { emptyRegistry } from "./assign-ids.js";
import {
  normalizeBooks,
  normalizeChrome,
  normalizeJuejin,
  normalizeWechat,
} from "./sources.js";

export function normalizeAll(
  repoRoot: string,
  registry: IdRegistry = emptyRegistry(),
): NormalizeResult {
  const wechat = normalizeWechat(repoRoot, registry);
  const chrome = normalizeChrome(repoRoot, registry);
  const juejin = normalizeJuejin(repoRoot, registry);
  const books = normalizeBooks(repoRoot, registry);

  return {
    docs: [...wechat.docs, ...chrome.docs, ...juejin.docs, ...books.docs],
    skipped: [
      ...wechat.skipped,
      ...chrome.skipped,
      ...juejin.skipped,
      ...books.skipped,
    ],
  };
}

export {
  normalizeWechat,
  normalizeChrome,
  normalizeJuejin,
  normalizeBooks,
} from "./sources.js";
