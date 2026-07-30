import { readdirSync, readFileSync, writeFileSync, mkdirSync, statSync } from "node:fs";
import { join, dirname, relative } from "node:path";
import type { Frontmatter } from "@my-brain/core";
import {
  parseAndValidateFrontmatter,
  safeFilename,
  serializeDocument,
} from "./frontmatter.js";
import { isMachinePath } from "./paths.js";

export type VaultDocument = {
  absPath: string;
  relPath: string;
  frontmatter: Frontmatter;
  body: string;
};

function walkMd(root: string): string[] {
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

/** Scan machine-maintained Markdown and build id → document map. */
export function scanMachineDocuments(vaultPath: string): Map<string, VaultDocument> {
  const map = new Map<string, VaultDocument>();
  for (const abs of walkMd(vaultPath)) {
    const rel = relative(vaultPath, abs).replace(/\\/g, "/");
    if (!isMachinePath(rel) || rel.startsWith("Maps/")) continue;
    const raw = readFileSync(abs, "utf8");
    try {
      const { data, content } = parseAndValidateFrontmatter(raw);
      map.set(data.id, {
        absPath: abs,
        relPath: rel,
        frontmatter: data,
        body: content,
      });
    } catch {
      // Invalid frontmatter — leave for validate CLI; skip map entry
    }
  }
  return map;
}

export function getDocumentById(
  vaultPath: string,
  id: string,
): VaultDocument | null {
  return scanMachineDocuments(vaultPath).get(id) ?? null;
}

/**
 * Write (or overwrite) a machine-maintained document.
 * Caller supplies the relative directory under Vault (e.g. Library/前端/构建工具).
 * Never writes into handwritten paths.
 */
export function writeMachineDocument(
  vaultPath: string,
  relativeDir: string,
  fm: Frontmatter,
  body: string,
): string {
  const dir = relativeDir.replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
  if (!isMachinePath(`${dir}/x.md`) && !["Inbox", "Library", "Books", "Maps"].includes(dir.split("/")[0]!)) {
    throw new Error(`Refusing to write outside machine area: ${dir}`);
  }
  if (dir.startsWith("Notes") || dir.startsWith("Projects") || dir.startsWith("Daily") || dir.startsWith("Templates")) {
    throw new Error(`Refusing to write into handwritten area: ${dir}`);
  }

  const absDir = join(vaultPath, dir);
  mkdirSync(absDir, { recursive: true });

  let filename = `${safeFilename(fm.title)}.md`;
  let absPath = join(absDir, filename);
  // Collision: same title different id → suffix with id
  if (statExists(absPath)) {
    try {
      const existing = parseAndValidateFrontmatter(readFileSync(absPath, "utf8"));
      if (existing.data.id !== fm.id) {
        filename = `${safeFilename(fm.title)}-${fm.id}.md`;
        absPath = join(absDir, filename);
      }
    } catch {
      filename = `${safeFilename(fm.title)}-${fm.id}.md`;
      absPath = join(absDir, filename);
    }
  }

  writeFileSync(absPath, serializeDocument(fm, body), "utf8");
  return relative(vaultPath, absPath).replace(/\\/g, "/");
}

function statExists(path: string): boolean {
  try {
    statSync(path);
    return true;
  } catch {
    return false;
  }
}

export function ensureDir(vaultPath: string, relativeDir: string): void {
  mkdirSync(join(vaultPath, relativeDir), { recursive: true });
  void dirname;
}
