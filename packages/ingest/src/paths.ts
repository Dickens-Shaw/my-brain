import { existsSync } from "node:fs";
import { resolve, join } from "node:path";

export function findRepoRoot(start = process.cwd()): string {
  let dir = resolve(start);
  for (;;) {
    if (existsSync(resolve(dir, "pnpm-workspace.yaml"))) return dir;
    const parent = resolve(dir, "..");
    if (parent === dir) {
      throw new Error("Cannot find repo root (no pnpm-workspace.yaml)");
    }
    dir = parent;
  }
}

export function migrateDir(repoRoot: string): string {
  return join(repoRoot, ".data", "migrate");
}

export const EXPORTER_OUTPUTS = {
  wechat: "exporters/wechat-favorites/output/articles",
  chrome: "exporters/chrome-bookmarks/output/pages",
  juejinArticles: "exporters/juejin-articles/output/articles",
  juejinCollections: "exporters/juejin-articles/output/collections",
  books: "exporters/juejin-books/output/books",
} as const;
