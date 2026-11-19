import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, relative } from "node:path";
import matter from "gray-matter";
import {
  ensureVaultSkeleton,
  safeFilename,
  serializeDocument,
  writeMachineDocument,
} from "@my-brain/vault";
import type { ClassifiedDoc } from "./types.js";
import { toFrontmatter } from "./types.js";
import { normalizeTopics, PENDING_TOPIC } from "./topics-path.js";
import { writeJson } from "./fsutil.js";

export type WriteReport = {
  written_articles: number;
  written_chapters: number;
  maps: number;
  by_source: Record<string, number>;
  by_topic: Record<string, number>;
  /**
   * Documents whose topic could not be repaired and were written into the
   * pending bucket instead.
   */
  repaired_topics: number;
  errors: string[];
};

function copySiblingAssets(
  bodyPath: string,
  destDocPath: string,
  vaultPath: string,
): void {
  const srcDir = dirname(bodyPath);
  const assetsDir = join(srcDir, "assets");
  if (!existsSync(assetsDir) || !statSync(assetsDir).isDirectory()) return;

  const destAssets = join(dirname(join(vaultPath, destDocPath)), "assets");
  mkdirSync(destAssets, { recursive: true });

  const stack = [[assetsDir, destAssets]] as [string, string][];
  while (stack.length) {
    const [from, to] = stack.pop()!;
    for (const name of readdirSync(from)) {
      const s = join(from, name);
      const d = join(to, name);
      const st = statSync(s);
      if (st.isDirectory()) {
        mkdirSync(d, { recursive: true });
        stack.push([s, d]);
      } else {
        copyFileSync(s, d);
      }
    }
  }
}

function rewriteAssetLinks(body: string): string {
  // Exporter books already use relative assets/ paths — keep as-is.
  return body;
}

function loadBody(bodyPath: string): string {
  const raw = readFileSync(bodyPath, "utf8");
  return matter(raw).content.replace(/^\uFEFF/, "").trim();
}

export function writeVault(options: {
  vaultPath: string;
  docs: ClassifiedDoc[];
  migrateRoot: string;
}): WriteReport {
  const { vaultPath, docs, migrateRoot } = options;
  ensureVaultSkeleton(vaultPath);

  const importedAt = new Date().toISOString();
  const report: WriteReport = {
    written_articles: 0,
    written_chapters: 0,
    maps: 0,
    by_source: {},
    by_topic: {},
    repaired_topics: 0,
    errors: [],
  };

  // topic -> list of {id, title, path}
  const mapEntries = new Map<string, { id: string; title: string; path: string }[]>();

  for (const doc of docs) {
    try {
      // A topic name the model produced can violate the frontmatter schema
      // (whitespace, an extra slash). Repairing it here keeps the document;
      // dropping it instead would lose a fifth of the corpus.
      const { topics, dropped } = normalizeTopics(doc.topics);
      if (dropped.length) report.repaired_topics += 1;
      const effectiveTopics = topics.length ? topics : [PENDING_TOPIC];

      const fm = toFrontmatter({ ...doc, topics: effectiveTopics }, importedAt);
      const body = rewriteAssetLinks(loadBody(doc.body_path));
      let relPath: string;

      if (doc.type === "book-chapter") {
        const bookDir = `Books/${safeFilename(doc.book || "untitled-book", 60)}`;
        const chapterNo =
          typeof doc.chapter === "number"
            ? String(doc.chapter).padStart(2, "0")
            : String(doc.chapter ?? "00");
        const filename = `${chapterNo}-${safeFilename(doc.title, 60)}.md`;
        const absDir = join(vaultPath, bookDir);
        mkdirSync(absDir, { recursive: true });
        const absPath = join(absDir, filename);
        writeFileSync(absPath, serializeDocument(fm, body), "utf8");
        relPath = relative(vaultPath, absPath).replace(/\\/g, "/");
        copySiblingAssets(doc.body_path, relPath, vaultPath);
        report.written_chapters += 1;
      } else {
        const primary = effectiveTopics[0] || PENDING_TOPIC;
        const [l1, l2] = primary.split("/");
        const dir = `Library/${safeFilename(l1 || "待确认")}/${safeFilename(l2 || "未分类")}`;
        relPath = writeMachineDocument(vaultPath, dir, fm, body);
        copySiblingAssets(doc.body_path, relPath, vaultPath);
        report.written_articles += 1;
        for (const t of effectiveTopics) {
          report.by_topic[t] = (report.by_topic[t] ?? 0) + 1;
          const list = mapEntries.get(t) ?? [];
          list.push({ id: doc.id, title: doc.title, path: relPath });
          mapEntries.set(t, list);
        }
      }

      report.by_source[doc.source] = (report.by_source[doc.source] ?? 0) + 1;
    } catch (err) {
      report.errors.push(
        `${doc.id}: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  // Generate Maps
  for (const [topic, entries] of mapEntries) {
    const [l1, l2] = topic.split("/");
    const mapRel = `Maps/${safeFilename(l1 || "待确认")}/${safeFilename(l2 || "未分类")}.md`;
    const abs = join(vaultPath, mapRel);
    mkdirSync(dirname(abs), { recursive: true });
    const lines = [
      `---`,
      `title: "${topic}"`,
      `type: map`,
      `generated: true`,
      `---`,
      ``,
      `# ${topic}`,
      ``,
      `- 文档数: ${entries.length}`,
      ``,
      ...entries
        .sort((a, b) => a.title.localeCompare(b.title, "zh"))
        .map((e) => {
          const name = e.path.split("/").pop()!.replace(/\.md$/, "");
          const escaped = name.replace(/[[\]]/g, "\\$&");
          return `- [[${escaped}]] \`${e.id}\``;
        }),
      ``,
    ];
    writeFileSync(abs, lines.join("\n"), "utf8");
    report.maps += 1;
  }

  writeJson(join(migrateRoot, "write-report.json"), report);
  return report;
}
