/**
 * Normalize a topic path so it satisfies the frontmatter schema.
 *
 * The schema requires `一级类目/二级类目` with no whitespace and no extra
 * slashes. A topic name produced by the model can contain any of those — a
 * space in "前端 工程", a slash inside a name — and the whole document then
 * fails validation and is dropped from the Vault.
 *
 * A document with a usable topic is worth keeping; a document whose topic
 * cannot be repaired falls back to the pending bucket instead of vanishing.
 */

/** The bucket documents land in when their topic is unknown or unusable. */
export const PENDING_TOPIC = "待确认/未分类";

export function normalizeTopicPath(topic: string): string | null {
  const trimmed = topic.trim();
  if (!trimmed) return null;

  const parts = trimmed
    .split("/")
    .map((p) => p.trim().replace(/\s+/g, ""))
    .filter(Boolean);

  if (parts.length === 0) return null;
  if (parts.length === 1) return `${parts[0]}/未分类`;

  // More than two levels: keep the first and the last, which is where the
  // meaningful distinction usually sits.
  if (parts.length > 2) {
    return `${parts[0]}/${parts[parts.length - 1]}`;
  }
  return `${parts[0]}/${parts[1]}`;
}

/**
 * Repair a document's topics.
 *
 * Returns the topics to write and the ones that were dropped, so the caller
 * can report them instead of losing the document silently.
 */
export function normalizeTopics(topics: string[] | undefined): {
  topics: string[];
  dropped: string[];
} {
  const out: string[] = [];
  const dropped: string[] = [];

  for (const topic of topics ?? []) {
    const normalized = normalizeTopicPath(topic);
    if (normalized) {
      if (!out.includes(normalized)) out.push(normalized);
    } else {
      dropped.push(topic);
    }
  }

  return { topics: out, dropped };
}
