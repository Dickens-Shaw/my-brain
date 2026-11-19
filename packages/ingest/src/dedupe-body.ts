import { readFileSync } from "node:fs";
import type { DedupeGroup, DedupeResult, NormalizedDoc } from "./types.js";
import { bodyTextFingerprint, completeness } from "./dedupe.js";

/**
 * Merge documents whose body text is identical.
 *
 * The same article saved under two different URLs (a repost from another
 * account, or the short link vs the long link) survives URL-based dedupe and
 * then occupies several slots in the same search result. The body text is
 * the reliable signal here.
 *
 * Only exact text matches are merged. Documents with the same title but
 * different text are left alone — merging those would need semantic
 * judgement, and 11 such pairs exist in the current corpus where the text
 * genuinely differs.
 */
export function dedupeByBodyText(docs: NormalizedDoc[]): DedupeResult {
  const byFingerprint = new Map<string, NormalizedDoc[]>();
  const unique: NormalizedDoc[] = [];

  for (const doc of docs) {
    if (doc.type === "book-chapter") {
      unique.push(doc);
      continue;
    }
    const body = readBody(doc.body_path);
    if (!body) {
      unique.push(doc);
      continue;
    }
    const fp = bodyTextFingerprint(body);
    const list = byFingerprint.get(fp) ?? [];
    list.push(doc);
    byFingerprint.set(fp, list);
  }

  const groups: DedupeGroup[] = [];

  for (const [fp, list] of byFingerprint) {
    if (list.length === 1) {
      unique.push(list[0]!);
      continue;
    }
    // Keep the one with the most metadata; the others become aliases so the
    // natural key can still be resolved later.
    const ranked = [...list].sort(
      (a, b) => completeness(b) - completeness(a) || a.id.localeCompare(b.id),
    );
    const kept = ranked[0]!;
    const dropped = ranked.slice(1);
    kept.origin = {
      ...kept.origin,
      aliases: [
        ...((kept.origin.aliases as unknown[]) ?? []),
        ...dropped.map((d) => ({
          id: d.id,
          source: d.source,
          source_url: d.source_url,
          title: d.title,
        })),
      ],
    };
    unique.push(kept);
    groups.push({
      key: `body:${fp.slice(0, 16)}`,
      kept_id: kept.id,
      dropped_ids: dropped.map((d) => d.id),
      reason: "identical body text (images and links ignored)",
    });
  }

  unique.sort((a, b) => a.id.localeCompare(b.id));
  return { docs: unique, groups };
}

function readBody(bodyPath: string): string | null {
  try {
    const raw = readFileSync(bodyPath, "utf8");
    const match = raw.match(/^---\n[\s\S]*?\n---\n?([\s\S]*)$/);
    const body = (match?.[1] ?? raw).trim();
    return body.length >= 40 ? body : null;
  } catch {
    return null;
  }
}
