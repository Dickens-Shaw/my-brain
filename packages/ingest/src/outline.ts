import { readFileSync } from "node:fs";
import matter from "gray-matter";

/** Extract heading outline (up to ~12 lines) for document-level embedding. */
export function extractOutline(bodyPath: string, maxHeadings = 12): string {
  const raw = readFileSync(bodyPath, "utf8");
  const { content } = matter(raw);
  const headings: string[] = [];
  for (const line of content.split(/\r?\n/)) {
    const m = /^(#{1,3})\s+(.+)$/.exec(line.trim());
    if (!m) continue;
    headings.push(`${m[1]} ${m[2]!.trim()}`);
    if (headings.length >= maxHeadings) break;
  }
  return headings.join("\n");
}

export function docEmbedText(doc: {
  title: string;
  summary?: string;
  body_path: string;
}): string {
  const outline = extractOutline(doc.body_path);
  return [
    `标题: ${doc.title}`,
    doc.summary ? `摘要: ${doc.summary}` : "",
    outline ? `大纲:\n${outline}` : "",
  ]
    .filter(Boolean)
    .join("\n");
}
