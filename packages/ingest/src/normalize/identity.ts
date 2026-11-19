import { normalizeSourceUrl } from "../url.js";

/**
 * A document's natural key is the identity the source already carries, so it
 * survives insertions elsewhere in the corpus. Sequential ids do not: one new
 * article shifts every later id, and the embedding / classification caches
 * (keyed by id) then attach vectors to the wrong documents.
 *
 * The key is namespaced by source because Chrome and WeChat URLs can collide
 * on the same string, and books have no URL at all.
 */
export function naturalKey(doc: {
  source: string;
  source_url?: string;
  book?: string;
  origin?: Record<string, unknown>;
}): string | null {
  const origin = doc.origin ?? {};

  if (doc.source === "book") {
    const sourceFile = str(origin.source_file);
    if (!doc.book && !sourceFile) return null;
    return `book::${doc.book ?? ""}::${sourceFile ?? ""}`;
  }

  if (doc.source === "juejin") {
    const articleId = str(origin.article_id);
    if (articleId) return `juejin::post::${articleId}`;
  }

  const url = doc.source_url;
  if (!url) return null;
  const normalized = normalizeSourceUrl(url);
  if (!normalized) return null;
  return `${doc.source}::${normalized}`;
}

/** Natural keys of every merged-away copy, recorded in `origin.aliases`. */
export function aliasNaturalKeys(origin: Record<string, unknown> | undefined): string[] {
  if (!origin) return [];
  const aliases = origin.aliases;
  if (!Array.isArray(aliases)) return [];
  const keys: string[] = [];
  for (const alias of aliases) {
    if (!alias || typeof alias !== "object") continue;
    const rec = alias as Record<string, unknown>;
    const source = str(rec.source);
    if (!source) continue;
    const key = naturalKey({
      source,
      source_url: str(rec.source_url),
    });
    if (key) keys.push(key);
  }
  return keys;
}

function str(v: unknown): string | undefined {
  if (v === undefined || v === null || v === "") return undefined;
  return String(v);
}

/** `wx-000123` → 123 */
export function idSequence(id: string): number | null {
  const m = /^([a-z]{2})-(\d{6})$/.exec(id);
  if (!m) return null;
  return Number(m[2]);
}
