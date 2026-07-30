import { join } from "node:path";
import type { NormalizedDoc, SkipRecord } from "../types.js";
import { EXPORTER_OUTPUTS } from "../paths.js";
import { walkMd } from "../fsutil.js";
import { assignIds, sortKey } from "./assign-ids.js";
import { num, parseExporterMd, str, strArr } from "./parse.js";

type PartialDoc = Omit<NormalizedDoc, "id">;

function baseOrigin(
  source: NormalizedDoc["source"],
  data: Record<string, unknown>,
  extras: Record<string, unknown> = {},
): Record<string, unknown> {
  return { exporter: source, ...extras, raw_keys: Object.keys(data).sort() };
}

export function normalizeWechat(repoRoot: string): {
  docs: NormalizedDoc[];
  skipped: SkipRecord[];
} {
  const root = join(repoRoot, EXPORTER_OUTPUTS.wechat);
  const skipped: SkipRecord[] = [];
  const partials: PartialDoc[] = [];

  for (const path of walkMd(root)) {
    const parsed = parseExporterMd(path);
    if (!parsed.ok) {
      skipped.push({ path, reason: parsed.reason, source: "wechat" });
      continue;
    }
    const { data, bodyHash } = parsed.value;
    const title = str(data, "title");
    if (!title) {
      skipped.push({ path, reason: "missing title", source: "wechat" });
      continue;
    }
    partials.push({
      title,
      type: "article",
      source: "wechat",
      source_url: str(data, "source_url"),
      published_at: str(data, "publish_time"),
      saved_at: str(data, "favorite_time"),
      author: str(data, "author"),
      publisher: str(data, "account"),
      tags: [],
      summary: str(data, "digest"),
      body_path: path,
      body_hash: bodyHash,
      origin: baseOrigin("wechat", data, {
        favorite_id: str(data, "favorite_id"),
        fetched_at: str(data, "fetched_at"),
      }),
    });
  }

  partials.sort((a, b) => sortKey(a).localeCompare(sortKey(b)));
  return { docs: assignIds(partials, "wx"), skipped };
}

export function normalizeChrome(repoRoot: string): {
  docs: NormalizedDoc[];
  skipped: SkipRecord[];
} {
  const root = join(repoRoot, EXPORTER_OUTPUTS.chrome);
  const skipped: SkipRecord[] = [];
  const partials: PartialDoc[] = [];

  for (const path of walkMd(root)) {
    const parsed = parseExporterMd(path);
    if (!parsed.ok) {
      skipped.push({ path, reason: parsed.reason, source: "chrome" });
      continue;
    }
    const { data, bodyHash } = parsed.value;
    const title = str(data, "title");
    if (!title) {
      skipped.push({ path, reason: "missing title", source: "chrome" });
      continue;
    }
    partials.push({
      title,
      type: "article",
      source: "chrome",
      source_url: str(data, "source_url"),
      saved_at: str(data, "bookmark_time"),
      publisher: str(data, "site"),
      tags: [],
      summary: str(data, "digest"),
      body_path: path,
      body_hash: bodyHash,
      origin: baseOrigin("chrome", data, {
        bookmark_id: str(data, "bookmark_id"),
        folder: str(data, "folder"),
        fetched_at: str(data, "fetched_at"),
      }),
    });
  }

  partials.sort((a, b) => sortKey(a).localeCompare(sortKey(b)));
  return { docs: assignIds(partials, "cb"), skipped };
}

export function normalizeJuejin(repoRoot: string): {
  docs: NormalizedDoc[];
  skipped: SkipRecord[];
} {
  const skipped: SkipRecord[] = [];
  const partials: PartialDoc[] = [];
  const seenArticleIds = new Set<string>();

  const roots = [
    join(repoRoot, EXPORTER_OUTPUTS.juejinArticles),
    join(repoRoot, EXPORTER_OUTPUTS.juejinCollections),
  ];

  for (const root of roots) {
    const fromCollections = root.includes("/collections");
    for (const path of walkMd(root)) {
      const parsed = parseExporterMd(path);
      if (!parsed.ok) {
        skipped.push({ path, reason: parsed.reason, source: "juejin" });
        continue;
      }
      const { data, bodyHash } = parsed.value;
      const title = str(data, "title");
      const articleId = str(data, "article_id");
      if (!title) {
        skipped.push({ path, reason: "missing title", source: "juejin" });
        continue;
      }
      if (articleId) {
        if (seenArticleIds.has(articleId)) {
          skipped.push({
            path,
            reason: `duplicate article_id ${articleId} within juejin normalize`,
            source: "juejin",
          });
          continue;
        }
        seenArticleIds.add(articleId);
      }

      const tags = strArr(data, "tags");
      const category = str(data, "category");
      if (category && !tags.includes(category)) tags.unshift(category);

      partials.push({
        title,
        type: "article",
        source: "juejin",
        source_url: str(data, "source_url"),
        published_at: str(data, "publish_time"),
        author: str(data, "author"),
        publisher: str(data, "site") ?? "juejin.cn",
        tags,
        summary: str(data, "digest"),
        body_path: path,
        body_hash: bodyHash,
        origin: baseOrigin("juejin", data, {
          article_id: articleId,
          digg_rank: num(data, "digg_rank"),
          collections: data.collections,
          collection_rank: num(data, "collection_rank"),
          from_collections: fromCollections,
          fetched_at: str(data, "fetched_at"),
          author_url: str(data, "author_url"),
          read_time: str(data, "read_time"),
        }),
      });
    }
  }

  partials.sort((a, b) => sortKey(a).localeCompare(sortKey(b)));
  return { docs: assignIds(partials, "jj"), skipped };
}

export function normalizeBooks(repoRoot: string): {
  docs: NormalizedDoc[];
  skipped: SkipRecord[];
} {
  const root = join(repoRoot, EXPORTER_OUTPUTS.books);
  const skipped: SkipRecord[] = [];
  const partials: PartialDoc[] = [];

  for (const path of walkMd(root)) {
    if (/00-目录\.md$/i.test(path) || /目录\.md$/i.test(path.split("/").pop() ?? "")) {
      skipped.push({ path, reason: "book toc / index file", source: "book" });
      continue;
    }
    const parsed = parseExporterMd(path);
    if (!parsed.ok) {
      skipped.push({ path, reason: parsed.reason, source: "book" });
      continue;
    }
    const { data, bodyHash } = parsed.value;
    const title = str(data, "title");
    const book = str(data, "book");
    if (!title || !book) {
      skipped.push({ path, reason: "missing title or book", source: "book" });
      continue;
    }
    partials.push({
      title,
      type: "book-chapter",
      source: "book",
      publisher: str(data, "site") ?? "juejin.cn",
      tags: [],
      book,
      chapter: num(data, "chapter") ?? str(data, "chapter"),
      chapter_total: num(data, "chapter_total"),
      body_path: path,
      body_hash: bodyHash,
      origin: baseOrigin("book", data, {
        source_format: str(data, "source_format"),
        order_source: str(data, "order_source"),
        title_source: str(data, "title_source"),
        source_file: str(data, "source_file"),
        converted_at: str(data, "converted_at"),
      }),
    });
  }

  partials.sort((a, b) => sortKey(a).localeCompare(sortKey(b)));
  return { docs: assignIds(partials, "bk"), skipped };
}
