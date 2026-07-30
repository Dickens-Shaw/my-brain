import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import {
  FrontmatterSchema,
  type Scope,
  type Source,
} from "@my-brain/core";
import { parseFrontmatter, scopeFromPath } from "@my-brain/vault";

export type VaultDoc = {
  docId: string;
  relPath: string;
  title: string;
  source: Source | "manual" | "unknown";
  scope: Scope;
  primaryTopic: string;
  contentHash: string;
  metaHash: string;
  body: string;
};

const INDEX_SCOPES = new Set<Scope>([
  "library",
  "books",
  "notes",
  "projects",
  "daily",
  "inbox",
]);

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

function hash(text: string): string {
  return createHash("sha256").update(text).digest("hex");
}

export function scanVaultDocs(vaultPath: string): VaultDoc[] {
  const docs: VaultDoc[] = [];
  for (const abs of walkMd(vaultPath)) {
    const rel = relative(vaultPath, abs).replace(/\\/g, "/");
    if (rel.startsWith("Maps/") || rel.startsWith("Templates/") || rel.startsWith("Assets/")) {
      continue;
    }
    const scope = scopeFromPath(rel);
    if (!scope || !INDEX_SCOPES.has(scope)) continue;

    const raw = readFileSync(abs, "utf8");
    const parsed = parseFrontmatter(raw);
    const body = parsed.content.replace(/^\uFEFF/, "").trim();
    if (!body) continue;

    const data = (parsed.data ?? {}) as Record<string, unknown>;
    const contentHash = hash(body);
    const topics = Array.isArray(data.topics) ? (data.topics as string[]) : [];
    const primaryTopic = scope === "library" ? String(topics[0] ?? "") : "";
    const metaHash = hash(JSON.stringify({ topics, source: data.source }));

    let docId: string;
    let title: string;
    let source: Source | "manual" | "unknown";

    if (scope === "notes" || scope === "projects" || scope === "daily") {
      docId = `path:${rel}`;
      title = String(data.title ?? rel.split("/").pop()?.replace(/\.md$/, "") ?? rel);
      source = "manual";
    } else {
      const fm = FrontmatterSchema.safeParse(data);
      if (!fm.success) {
        // Machine area without valid frontmatter — skip (validate CLI will catch)
        continue;
      }
      docId = fm.data.id;
      title = fm.data.title;
      source = fm.data.source;
    }

    docs.push({
      docId,
      relPath: rel,
      title,
      source,
      scope,
      primaryTopic,
      contentHash,
      metaHash,
      body,
    });
  }
  return docs;
}
