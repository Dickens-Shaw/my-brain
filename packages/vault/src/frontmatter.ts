import matter from "gray-matter";
import { stringify as yamlStringify } from "yaml";
import { FrontmatterSchema, type Frontmatter } from "@my-brain/core";

const ILLEGAL = /[\\/:*?"<>|]/g;

/** Make a filename safe for all common filesystems. */
export function safeFilename(title: string, maxLen = 80): string {
  const cleaned = title.replace(ILLEGAL, "-").replace(/\s+/g, " ").trim();
  if (!cleaned) return "untitled";
  if (cleaned.length <= maxLen) return cleaned;
  return cleaned.slice(0, maxLen).trimEnd();
}

/**
 * YAML turns `2024-01-15` and timestamps into Date objects, but the frontmatter
 * schema stores them as strings. Convert back, keeping date-only values
 * date-only so a parse/serialize round trip does not rewrite the file.
 */
function datesToStrings(value: unknown): unknown {
  if (value instanceof Date) {
    const iso = value.toISOString();
    return iso.endsWith("T00:00:00.000Z") ? iso.slice(0, 10) : iso;
  }
  if (Array.isArray(value)) return value.map(datesToStrings);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([k, v]) => [
        k,
        datesToStrings(v),
      ]),
    );
  }
  return value;
}

export function parseFrontmatter(raw: string): {
  data: unknown;
  content: string;
} {
  const parsed = matter(raw);
  return { data: datesToStrings(parsed.data), content: parsed.content };
}

export function parseAndValidateFrontmatter(raw: string): {
  data: Frontmatter;
  content: string;
} {
  const { data, content } = parseFrontmatter(raw);
  return { data: FrontmatterSchema.parse(data), content };
}

export function serializeDocument(fm: Frontmatter, body: string): string {
  const validated = FrontmatterSchema.parse(fm);
  // Drop undefined keys so YAML stays clean
  const clean = JSON.parse(JSON.stringify(validated)) as Record<string, unknown>;
  const yaml = yamlStringify(clean, { lineWidth: 0 }).trimEnd();
  const bodyNorm = body.replace(/^\uFEFF/, "").replace(/^\n+/, "");
  return `---\n${yaml}\n---\n\n${bodyNorm}`;
}
