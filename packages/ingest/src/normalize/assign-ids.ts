import type { IdPrefix } from "@my-brain/core";
import type { NormalizedDoc } from "../types.js";
import { idSequence, naturalKey, aliasNaturalKeys } from "./identity.js";

/** Assign permanent ids. Stable given a stable sort of docs. */
export function assignIds(
  docs: Omit<NormalizedDoc, "id">[],
  prefix: IdPrefix,
): NormalizedDoc[] {
  return docs.map((doc, i) => ({
    ...doc,
    id: `${prefix}-${String(i + 1).padStart(6, "0")}`,
  }));
}

/**
 * Id registry: natural key → permanent id, plus the next free sequence number
 * per prefix.
 *
 * The registry is derived from the Vault, never stored as a second source of
 * truth: every machine document carries its `id` plus the fields the natural
 * key is built from, and merged-away copies are kept in `origin.aliases`.
 */
export type IdRegistry = {
  /** natural key → id */
  byKey: Map<string, string>;
  /** id → natural key (for the doc that owns the id) */
  keyById: Map<string, string>;
  /** prefix → highest sequence number already handed out */
  maxSeq: Map<IdPrefix, number>;
};

export function emptyRegistry(): IdRegistry {
  return { byKey: new Map(), keyById: new Map(), maxSeq: new Map() };
}

/**
 * Build a registry from docs that already carry an id (e.g. read back from
 * the Vault). Docs whose natural key is already taken keep the earlier id —
 * the first owner of a key wins, so a re-import cannot steal an id.
 */
export function registryFromDocs(docs: {
  id: string;
  source: string;
  source_url?: string;
  book?: string;
  origin?: Record<string, unknown>;
}[]): IdRegistry {
  const reg = emptyRegistry();
  for (const doc of docs) {
    const seq = idSequence(doc.id);
    if (seq !== null) {
      const prefix = doc.id.slice(0, 2) as IdPrefix;
      reg.maxSeq.set(prefix, Math.max(reg.maxSeq.get(prefix) ?? 0, seq));
    }
    const key = naturalKey(doc);
    if (key && !reg.byKey.has(key)) {
      reg.byKey.set(key, doc.id);
      reg.keyById.set(doc.id, key);
    }
    // A merged-away copy's key is recorded in `origin.aliases`; map it to the
    // surviving id so the same article is never handed a new id later.
    for (const aliasKey of aliasNaturalKeys(doc.origin)) {
      if (!reg.byKey.has(aliasKey)) {
        reg.byKey.set(aliasKey, doc.id);
      }
    }
  }
  return reg;
}

/**
 * Assign ids by natural key: a document whose key is already known keeps its
 * id, a new document gets the next free number for its prefix.
 *
 * Within a single run, two docs can share a key (same article exported twice).
 * They are merged by the dedupe stage, but they still need distinct ids here;
 * the second one is given its own id and the duplicate is recorded in
 * `origin.aliases` so the key can be recovered later.
 */
export function assignIdsByNaturalKey(
  docs: Omit<NormalizedDoc, "id">[],
  prefix: IdPrefix,
  registry: IdRegistry,
): NormalizedDoc[] {
  const claimed = new Set<string>();
  const out: NormalizedDoc[] = [];

  for (const doc of docs) {
    const key = naturalKey(doc);
    const existingId = key ? registry.byKey.get(key) : undefined;

    if (existingId && !claimed.has(existingId)) {
      claimed.add(existingId);
      out.push({ ...doc, id: existingId });
      continue;
    }

    const next = (registry.maxSeq.get(prefix) ?? 0) + 1;
    const id = `${prefix}-${String(next).padStart(6, "0")}`;
    registry.maxSeq.set(prefix, next);
    claimed.add(id);

    if (key && existingId) {
      // Same key seen twice in this run: remember the alias so the key can
      // still be resolved from the Vault later.
      const aliases = Array.isArray(doc.origin?.aliases)
        ? (doc.origin!.aliases as unknown[])
        : [];
      doc.origin = {
        ...doc.origin,
        aliases: [
          ...aliases,
          { id: existingId, source: doc.source, source_url: doc.source_url, title: doc.title },
        ],
      };
    }
    out.push({ ...doc, id });
  }

  return out;
}

export function sortKey(doc: Omit<NormalizedDoc, "id">): string {
  return [
    doc.source_url ?? "",
    doc.book ?? "",
    String(doc.chapter ?? ""),
    doc.body_path,
  ].join("\t");
}
