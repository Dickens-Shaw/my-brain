import { createHash } from "node:crypto";
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import { toString } from "mdast-util-to-string";
import type { Root, RootContent, Heading } from "mdast";
import type { Chunk } from "./types.js";
import type { Scope, Source } from "@my-brain/core";

const MAX_TOKENS = 1200;
const MIN_TOKENS = 100;
const OVERLAP_TOKENS = 200;

/** Rough token estimate: CJK ~1 token/char, latin ~1/4. */
export function estimateTokens(text: string): number {
  const cjk = (text.match(/[\u4e00-\u9fff]/g) || []).length;
  const rest = text.length - cjk;
  return cjk + Math.ceil(rest / 4);
}

function sha(text: string): string {
  return createHash("sha256").update(text).digest("hex").slice(0, 16);
}

type Section = {
  headingPath: string[];
  nodes: RootContent[];
};

function isHeading(n: RootContent): n is Heading {
  return n.type === "heading";
}

function splitIntoSections(tree: Root): Section[] {
  const sections: Section[] = [];
  const stack: { depth: number; text: string }[] = [];
  let current: Section = { headingPath: [], nodes: [] };

  const flush = () => {
    if (current.nodes.length) sections.push(current);
  };

  for (const node of tree.children) {
    if (isHeading(node)) {
      flush();
      const depth = node.depth;
      const text = toString(node).trim();
      while (stack.length && stack[stack.length - 1]!.depth >= depth) {
        stack.pop();
      }
      stack.push({ depth, text });
      current = {
        headingPath: stack.map((s) => s.text),
        nodes: [node],
      };
    } else {
      current.nodes.push(node);
    }
  }
  flush();
  return sections;
}

function nodesToMarkdown(nodes: RootContent[]): string {
  // Prefer mdast-util-to-string for search text; keep fence blocks intact via stringify-ish walk
  const parts: string[] = [];
  for (const n of nodes) {
    if (n.type === "code") {
      parts.push(`\`\`\`${n.lang || ""}\n${n.value}\n\`\`\``);
    } else {
      parts.push(toString(n));
    }
  }
  return parts.join("\n\n").trim();
}

function splitLongSection(text: string): string[] {
  const tokens = estimateTokens(text);
  if (tokens <= MAX_TOKENS) return [text];

  // Split by paragraphs, keep code fences atomic
  const blocks: string[] = [];
  const fence = /```[\s\S]*?```/g;
  let last = 0;
  let m: RegExpExecArray | null;
  const marks: { start: number; end: number; text: string }[] = [];
  while ((m = fence.exec(text))) {
    marks.push({ start: m.index, end: m.index + m[0].length, text: m[0] });
  }
  if (!marks.length) {
    blocks.push(...text.split(/\n{2,}/));
  } else {
    for (const mark of marks) {
      if (mark.start > last) {
        blocks.push(...text.slice(last, mark.start).split(/\n{2,}/));
      }
      blocks.push(mark.text);
      last = mark.end;
    }
    if (last < text.length) {
      blocks.push(...text.slice(last).split(/\n{2,}/));
    }
  }

  const chunks: string[] = [];
  let buf: string[] = [];
  let bufTokens = 0;

  const pushBuf = () => {
    if (!buf.length) return;
    chunks.push(buf.join("\n\n"));
    // overlap: keep tail paragraphs totaling ~OVERLAP_TOKENS
    const kept: string[] = [];
    let keptTok = 0;
    for (let i = buf.length - 1; i >= 0; i--) {
      const t = estimateTokens(buf[i]!);
      if (keptTok + t > OVERLAP_TOKENS && kept.length) break;
      kept.unshift(buf[i]!);
      keptTok += t;
    }
    buf = kept;
    bufTokens = keptTok;
  };

  for (const block of blocks) {
    const t = estimateTokens(block);
    if (bufTokens + t > MAX_TOKENS && buf.length) pushBuf();
    buf.push(block);
    bufTokens += t;
  }
  pushBuf();
  return chunks.filter(Boolean);
}

function mergeSmall(chunks: string[]): string[] {
  if (!chunks.length) return chunks;
  const out: string[] = [];
  let i = 0;
  while (i < chunks.length) {
    let cur = chunks[i]!;
    while (
      i + 1 < chunks.length &&
      estimateTokens(cur) < MIN_TOKENS &&
      estimateTokens(cur + "\n\n" + chunks[i + 1]!) <= MAX_TOKENS
    ) {
      cur = cur + "\n\n" + chunks[i + 1]!;
      i += 1;
    }
    out.push(cur);
    i += 1;
  }
  return out;
}

export function chunkMarkdown(options: {
  markdown: string;
  docId: string;
  relPath: string;
  title: string;
  source: Source | "manual" | "unknown";
  scope: Scope;
  primaryTopic: string;
  contentHash: string;
}): Chunk[] {
  const tree = unified()
    .use(remarkParse)
    .use(remarkGfm)
    .parse(options.markdown) as Root;

  const sections = splitIntoSections(tree);
  const chunks: Chunk[] = [];
  let idx = 0;

  for (const section of sections) {
    const text = nodesToMarkdown(section.nodes);
    if (!text.trim()) continue;
    const headingPath = [options.title, ...section.headingPath].join(" > ");
    const parts = mergeSmall(splitLongSection(text));
    for (const part of parts) {
      chunks.push({
        chunkId: `${options.docId}#${String(idx).padStart(4, "0")}-${sha(part)}`,
        docId: options.docId,
        relPath: options.relPath,
        title: options.title,
        headingPath,
        text: part,
        source: options.source,
        scope: options.scope,
        primaryTopic: options.primaryTopic,
        contentHash: options.contentHash,
      });
      idx += 1;
    }
  }

  if (!chunks.length) {
    const text = options.markdown.trim();
    if (text) {
      chunks.push({
        chunkId: `${options.docId}#0000-${sha(text)}`,
        docId: options.docId,
        relPath: options.relPath,
        title: options.title,
        headingPath: options.title,
        text,
        source: options.source,
        scope: options.scope,
        primaryTopic: options.primaryTopic,
        contentHash: options.contentHash,
      });
    }
  }

  return chunks;
}
