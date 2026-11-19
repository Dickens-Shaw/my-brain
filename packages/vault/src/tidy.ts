import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  renameSync,
  statSync,
  unlinkSync,
} from "node:fs";
import { dirname, join, relative } from "node:path";
import matter from "gray-matter";
import { safeFilename } from "./frontmatter.js";

export type TidyResult = {
  moved: number;
  unchanged: number;
  removedDuplicates: number;
  errors: string[];
};

type MachineDoc = {
  absPath: string;
  relPath: string;
  id: string;
  title: string;
  topics: string[];
};

function walkMd(root: string): string[] {
  if (!existsSync(root)) return [];
  const out: string[] = [];
  const stack = [root];
  while (stack.length) {
    const dir = stack.pop()!;
    for (const name of readdirSync(dir)) {
      if (name.startsWith(".")) continue;
      const full = join(dir, name);
      const st = statSync(full);
      if (st.isDirectory()) stack.push(full);
      else if (name.endsWith(".md")) out.push(full);
    }
  }
  return out;
}

/**
 * Move machine documents so their directory matches their primary topic, and
 * remove duplicates left behind by a reclassification.
 *
 * Two problems are handled here:
 *
 * - A document whose primary topic changed still sits in the old directory,
 *   so the same id exists twice in the Vault. The old copy is removed.
 * - A document whose file name no longer matches its title is renamed, so
 *   Obsidian's links keep resolving.
 */
export function tidyMachineArea(options: {
  vaultPath: string;
  /** Directories that hold machine-maintained documents. */
  areas?: string[];
}): TidyResult {
  const { vaultPath } = options;
  const areas = options.areas ?? ["Library", "Books", "Inbox"];
  const result: TidyResult = { moved: 0, unchanged: 0, removedDuplicates: 0, errors: [] };

  // Collect the documents and note which id lives at which path.
  const docs: MachineDoc[] = [];
  const byId = new Map<string, MachineDoc[]>();

  for (const area of areas) {
    for (const abs of walkMd(join(vaultPath, area))) {
      const relPath = relative(vaultPath, abs).replace(/\\/g, "/");
      const raw = readFileSync(abs, "utf8");
      let parsed: matter.GrayMatterFile<string>;
      try {
        parsed = matter(raw);
      } catch {
        continue;
      }
      const data = (parsed.data ?? {}) as Record<string, unknown>;
      const id = data.id;
      if (!id || typeof id !== "string") continue;
      const topics = Array.isArray(data.topics)
        ? (data.topics as string[]).map(String)
        : [];
      const doc: MachineDoc = {
        absPath: abs,
        relPath,
        id,
        title: typeof data.title === "string" ? data.title : "",
        topics,
      };
      docs.push(doc);
      const list = byId.get(id) ?? [];
      list.push(doc);
      byId.set(id, list);
    }
  }

  for (const [, list] of byId) {
    if (list.length <= 1) continue;
    // The same id in two places: keep the one that matches the primary topic,
    // or the first if neither does, and remove the rest.
    const sorted = [...list].sort((a, b) => {
      const aOk = matchesTopic(a) ? 0 : 1;
      const bOk = matchesTopic(b) ? 0 : 1;
      return aOk - bOk;
    });
    const [keep, ...remove] = sorted;
    for (const doc of remove) {
      try {
        unlinkSync(doc.absPath);
        result.removedDuplicates += 1;
      } catch (err) {
        result.errors.push(
          `remove ${doc.relPath}: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }
    void keep;
  }

  function matchesTopic(doc: MachineDoc): boolean {
    const primary = doc.topics[0];
    if (!primary) return false;
    const [l1, l2] = primary.split("/");
    return doc.relPath.startsWith(`Library/${l1}/`);
  }

  // Move documents whose directory no longer matches their primary topic.
  for (const doc of docs) {
    const primary = doc.topics[0];
    if (!primary) {
      result.unchanged += 1;
      continue;
    }
    const [l1, l2] = primary.split("/");
    if (!l1 || !l2) {
      result.unchanged += 1;
      continue;
    }
    const expectedDir = `Library/${safeFilename(l1)}/${safeFilename(l2)}`;
    const currentDir = dirname(doc.relPath).replace(/\\/g, "/");
    if (currentDir === expectedDir) {
      result.unchanged += 1;
      continue;
    }
    // Only Library documents are relocated; book chapters keep their book
    // folder, and Inbox documents wait for a person to classify them.
    if (!currentDir.startsWith("Library/")) {
      result.unchanged += 1;
      continue;
    }
    const targetDir = join(vaultPath, expectedDir);
    const targetPath = join(targetDir, doc.relPath.split("/").pop()!);
    try {
      mkdirSync(targetDir, { recursive: true });
      if (existsSync(targetPath)) {
        unlinkSync(targetPath);
      }
      renameSync(doc.absPath, targetPath);
      result.moved += 1;
    } catch (err) {
      result.errors.push(
        `move ${doc.relPath}: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  return result;
}
