import type { NormalizeResult } from "../types.js";
import {
  normalizeBooks,
  normalizeChrome,
  normalizeJuejin,
  normalizeWechat,
} from "./sources.js";

export function normalizeAll(repoRoot: string): NormalizeResult {
  const wechat = normalizeWechat(repoRoot);
  const chrome = normalizeChrome(repoRoot);
  const juejin = normalizeJuejin(repoRoot);
  const books = normalizeBooks(repoRoot);

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
