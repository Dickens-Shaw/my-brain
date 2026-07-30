import { readdirSync, readFileSync, existsSync, statSync } from "node:fs";
import { join, relative, dirname } from "node:path";
import { DocTypeSchema, FrontmatterSchema, SourceSchema } from "@my-brain/core";
import { loadConfig } from "@my-brain/config";
import { parseFrontmatter } from "../frontmatter.js";
import { isHandwrittenPath, isMachinePath, scopeFromPath } from "../paths.js";

const IMAGE_RE = /!\[[^\]]*\]\(([^)]+)\)|<img[^>]+src=["']([^"']+)["']/gi;

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

const config = loadConfig();
const vault = config.vaultPath;
const errors: string[] = [];
const imageErrors: string[] = [];
let machineCount = 0;
let handwrittenCount = 0;

for (const file of walkMd(vault)) {
  const rel = relative(vault, file).replace(/\\/g, "/");
  if (rel.startsWith("Maps/") || rel.startsWith("Templates/") || rel.startsWith("Assets/")) {
    continue;
  }
  const raw = readFileSync(file, "utf8");
  const { data, content } = parseFrontmatter(raw);

  if (isMachinePath(rel)) {
    machineCount += 1;
    const result = FrontmatterSchema.safeParse(data);
    if (!result.success) {
      for (const issue of result.error.issues) {
        errors.push(`${rel}: ${issue.path.join(".")} — ${issue.message}`);
      }
    }
  } else if (isHandwrittenPath(rel)) {
    handwrittenCount += 1;
    // Handwritten: optional frontmatter; if present and has id/type/source, soft-check enums
    if (data && typeof data === "object" && Object.keys(data as object).length) {
      const partial = data as Record<string, unknown>;
      if (partial.type !== undefined) {
        const r = DocTypeSchema.safeParse(partial.type);
        if (!r.success) errors.push(`${rel}: type — ${r.error.issues[0]?.message}`);
      }
      if (partial.source !== undefined) {
        const r = SourceSchema.safeParse(partial.source);
        if (!r.success) errors.push(`${rel}: source — ${r.error.issues[0]?.message}`);
      }
    }
  }

  // Broken image refs (all indexed scopes + maps)
  if (scopeFromPath(rel) || rel.startsWith("Maps/")) {
    let m: RegExpExecArray | null;
    IMAGE_RE.lastIndex = 0;
    while ((m = IMAGE_RE.exec(content))) {
      const ref = (m[1] || m[2] || "").trim();
      if (!ref || /^https?:\/\//i.test(ref) || ref.startsWith("data:")) continue;
      const cleaned = ref.replace(/[?#].*$/, "");
      const target = join(dirname(file), cleaned);
      if (!existsSync(target)) {
        imageErrors.push(`${rel}: missing image ${ref}`);
      }
    }
  }
}

console.log(`Validated Vault at ${vault}`);
console.log(`  machine docs: ${machineCount}`);
console.log(`  handwritten docs: ${handwrittenCount}`);
console.log(`  frontmatter errors: ${errors.length}`);
console.log(`  broken image refs: ${imageErrors.length}`);

for (const e of errors) console.error(`ERROR ${e}`);
for (const e of imageErrors) console.error(`IMAGE ${e}`);

if (errors.length > 0) process.exit(1);
