import type { DedupeGroup, DedupeResult, NormalizedDoc } from "./types.js";
import { normalizeSourceUrl } from "./url.js";

function completeness(doc: NormalizedDoc): number {
  let score = 0;
  if (doc.summary) score += 2;
  if (doc.author) score += 1;
  if (doc.publisher) score += 1;
  if (doc.published_at) score += 1;
  if (doc.saved_at) score += 1;
  if (doc.tags.length) score += 1;
  score += Math.min(3, Math.floor(doc.body_hash.length > 0 ? 1 : 0));
  // Prefer longer body path length as weak proxy? Better: prefer wechat/juejin over chrome for same URL
  const sourceWeight: Record<string, number> = {
    juejin: 3,
    wechat: 3,
    book: 2,
    chrome: 1,
    manual: 0,
  };
  score += sourceWeight[doc.source] ?? 0;
  return score;
}

/**
 * Merge cross-source duplicates by normalized source_url.
 * Book chapters are never deduped against articles (no URL / different nature).
 */
export function dedupeDocs(docs: NormalizedDoc[]): DedupeResult {
  const byKey = new Map<string, NormalizedDoc[]>();
  const unique: NormalizedDoc[] = [];

  for (const doc of docs) {
    if (doc.type === "book-chapter") {
      unique.push(doc);
      continue;
    }
    const key = normalizeSourceUrl(doc.source_url);
    if (!key) {
      unique.push(doc);
      continue;
    }
    const list = byKey.get(key) ?? [];
    list.push(doc);
    byKey.set(key, list);
  }

  const groups: DedupeGroup[] = [];

  for (const [key, list] of byKey) {
    if (list.length === 1) {
      unique.push(list[0]!);
      continue;
    }
    const ranked = [...list].sort((a, b) => completeness(b) - completeness(a));
    const kept = ranked[0]!;
    const dropped = ranked.slice(1);
    const aliases = dropped.map((d) => ({
      id: d.id,
      source: d.source,
      source_url: d.source_url,
      title: d.title,
    }));
    kept.origin = {
      ...kept.origin,
      aliases: [...((kept.origin.aliases as unknown[]) ?? []), ...aliases],
    };
    unique.push(kept);
    groups.push({
      key,
      kept_id: kept.id,
      dropped_ids: dropped.map((d) => d.id),
      reason: `kept highest completeness (score ${completeness(kept)})`,
    });
  }

  unique.sort((a, b) => a.id.localeCompare(b.id));
  return { docs: unique, groups };
}
