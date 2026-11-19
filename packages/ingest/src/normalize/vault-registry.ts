import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import matter from "gray-matter";
import type { IdRegistry } from "./assign-ids.js";
import { emptyRegistry, registryFromDocs } from "./assign-ids.js";

type VaultDoc = {
  id: string;
  source: string;
  source_url?: string;
  book?: string;
  origin?: Record<string, unknown>;
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
 * Rebuild the id registry from the Vault.
 *
 * The Vault is the single source of truth, so the mapping from natural key to
 * permanent id is derived from it on every run rather than stored separately.
 * Machine areas only — handwritten notes have no stable id.
 */
export function registryFromVault(vaultPath: string): IdRegistry {
  if (!vaultPath || !existsSync(vaultPath)) return emptyRegistry();

  const docs: VaultDoc[] = [];
  for (const dir of ["Library", "Books", "Inbox"]) {
    const root = join(vaultPath, dir);
    for (const abs of walkMd(root)) {
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
      docs.push({
        id,
        source: String(data.source ?? "unknown"),
        source_url:
          typeof data.source_url === "string" ? data.source_url : undefined,
        book: typeof data.book === "string" ? data.book : undefined,
        origin:
          data.origin && typeof data.origin === "object"
            ? (data.origin as Record<string, unknown>)
            : undefined,
      });
    }
  }

  return registryFromDocs(docs);
}

/** Relative path of a file inside the Vault, for reporting. */
export function vaultRelPath(vaultPath: string, abs: string): string {
  return relative(vaultPath, abs).replace(/\\/g, "/");
}
