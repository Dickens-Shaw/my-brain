import type { DocType, Frontmatter, Source } from "@my-brain/core";

/** Intermediate record between migration stages (JSON-serializable). */
export type NormalizedDoc = {
  id: string;
  title: string;
  type: DocType;
  source: Source;
  source_url?: string;
  published_at?: string;
  saved_at?: string;
  author?: string;
  publisher?: string;
  tags: string[];
  summary?: string;
  lang?: string;
  book?: string;
  chapter?: number | string;
  chapter_total?: number;
  /** Absolute path to the exporter Markdown body file. */
  body_path: string;
  /** Content hash of body (sha256 hex) for change detection. */
  body_hash: string;
  origin: Record<string, unknown>;
};

export type SkipRecord = {
  path: string;
  reason: string;
  source: Source;
};

export type NormalizeResult = {
  docs: NormalizedDoc[];
  skipped: SkipRecord[];
};

export type DedupeGroup = {
  key: string;
  kept_id: string;
  dropped_ids: string[];
  reason: string;
};

export type DedupeResult = {
  docs: NormalizedDoc[];
  groups: DedupeGroup[];
};

export type ClassifiedDoc = NormalizedDoc & {
  topics: string[];
  classify_method: "cluster" | "llm" | "pending" | "book";
};

export function toFrontmatter(
  doc: ClassifiedDoc | (NormalizedDoc & { topics?: string[] }),
  importedAt: string,
): Frontmatter {
  const topics = "topics" in doc && doc.topics ? doc.topics : [];
  const fm: Frontmatter = {
    id: doc.id,
    title: doc.title,
    type: doc.type,
    source: doc.source,
    topics,
    tags: doc.tags,
    imported_at: importedAt,
    origin: doc.origin,
  };
  if (doc.source_url) fm.source_url = doc.source_url;
  if (doc.published_at) fm.published_at = doc.published_at;
  if (doc.saved_at) fm.saved_at = doc.saved_at;
  if (doc.author) fm.author = doc.author;
  if (doc.publisher) fm.publisher = doc.publisher;
  if (doc.summary) fm.summary = doc.summary;
  if (doc.lang) fm.lang = doc.lang;
  if (doc.type === "book-chapter") {
    fm.book = doc.book;
    fm.chapter = doc.chapter;
    fm.chapter_total = doc.chapter_total;
  }
  return fm;
}
