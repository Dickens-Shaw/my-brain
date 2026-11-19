import { createHash } from "node:crypto";
import type { Frontmatter } from "@my-brain/core";

/**
 * Which fields belong to whom.
 *
 * The machine owns the content it imported; the person owns the fields they
 * edit in Obsidian. Without this rule a sync overwrites the person's
 * categories with the machine's guess, and they stop trusting the tool.
 */
export const MACHINE_FIELDS = [
  "title",
  "source",
  "source_url",
  "published_at",
  "saved_at",
  "author",
  "publisher",
  "summary",
  "lang",
  "book",
  "chapter",
  "chapter_total",
  "type",
] as const;

/** Fields the person owns. The machine only sets them on first write. */
export const HUMAN_FIELDS = ["topics", "tags", "status"] as const;

export type FieldOwnership = "machine" | "human" | "unknown";

export function fieldOwnership(field: string): FieldOwnership {
  if ((MACHINE_FIELDS as readonly string[]).includes(field)) return "machine";
  if ((HUMAN_FIELDS as readonly string[]).includes(field)) return "human";
  return "unknown";
}

/**
 * Hash of the machine-owned frontmatter.
 *
 * Only machine-owned fields go in, so a person editing `topics` or adding a
 * `status` does not make the document look changed to the machine.
 */
export function machineFieldsHash(fm: Record<string, unknown>): string {
  const subset: Record<string, unknown> = {};
  for (const field of MACHINE_FIELDS) {
    if (fm[field] !== undefined) subset[field] = fm[field];
  }
  return hashJson(subset);
}

/** Hash of the body as stored in the Vault. */
export function bodyHash(body: string): string {
  return createHash("sha256").update(body).digest("hex");
}

/**
 * The three hashes compared during a sync.
 *
 * - `source`: the body the exporter currently holds
 * - `recorded`: what the machine last wrote, saved in `origin.body_hash`
 * - `vault`: what the Vault holds right now
 *
 * Only the source hash changing means the upstream article was updated, and
 * the Vault copy should follow. The vault hash changing on its own means the
 * person edited it, and it must be left alone. Both changing means a
 * conflict, which is reported rather than resolved automatically.
 */
export type SyncDecision = "update" | "keep-human" | "conflict" | "unchanged";

export function decideSync(options: {
  sourceHash: string;
  recordedHash: string | undefined;
  vaultHash: string;
}): SyncDecision {
  const { sourceHash, recordedHash, vaultHash } = options;

  if (!recordedHash) {
    // Nothing recorded yet: the Vault copy is the only evidence.
    return sourceHash === vaultHash ? "unchanged" : "keep-human";
  }
  if (sourceHash === recordedHash) {
    // The source did not change since the machine last wrote it.
    return vaultHash === recordedHash ? "unchanged" : "keep-human";
  }
  // The source changed.
  if (vaultHash === recordedHash) {
    // Nobody touched the Vault copy, so it can be updated safely.
    return "update";
  }
  return "conflict";
}

export function hashJson(value: unknown): string {
  return createHash("sha256")
    .update(JSON.stringify(value, Object.keys(value as object).sort()))
    .digest("hex");
}

/**
 * Merge the machine's frontmatter with the one in the Vault.
 *
 * Machine fields come from the source. Human fields keep the Vault's value.
 * A field the schema does not know about is preserved as-is — it belongs to
 * the person who added it.
 */
export function mergeFrontmatter(options: {
  incoming: Frontmatter;
  existing?: Record<string, unknown>;
}): Record<string, unknown> {
  const { incoming, existing } = options;
  const merged: Record<string, unknown> = { ...existing };

  for (const [key, value] of Object.entries(incoming)) {
    if (value === undefined) continue;
    const owner = fieldOwnership(key);
    if (owner === "human" && existing && key in existing) {
      // Keep the person's value.
      continue;
    }
    merged[key] = value;
  }
  return merged;
}
