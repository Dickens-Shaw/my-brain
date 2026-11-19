import { createHash } from "node:crypto";
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import { toString } from "mdast-util-to-string";
import type { Root, RootContent, Heading } from "mdast";
import type { Chunk } from "./types.js";
import { CHUNKER_PARAMS } from "./chunker-config.js";
import type { Scope, Source } from "@my-brain/core";

const MAX_TOKENS = CHUNKER_PARAMS.maxTokens;
const MIN_TOKENS = CHUNKER_PARAMS.minTokens;
const OVERLAP_TOKENS = CHUNKER_PARAMS.overlapTokens;

/**
 * Depth used for headings promoted from bold lines.
 *
 * Deeper than any Markdown heading level, so a real heading after a bold
 * title still nests under it, while two bold titles are siblings.
 */
const BOLD_HEADING_DEPTH = 7;

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

/**
 * A paragraph that starts with a bold line acts as a heading.
 *
 * WeChat articles do not use Markdown headings; the layout puts the section
 * title in bold, often followed by a line break and the body text. Measured on
 * the corpus: 280 of the 680 WeChat documents with two or fewer real headings
 * use at least three bold-only lines, so without this the heading-based
 * splitting degrades to splitting those documents by paragraph alone, and the
 * chunks carry no heading path.
 *
 * Returns the bold text to use as the heading, or null when the paragraph is
 * not a heading candidate.
 */
function boldOnlyHeading(n: RootContent): string | null {
  if (n.type !== "paragraph") return null;
  const children = n.children;
  if (!children.length) return null;

  // The paragraph must begin with a bold run; anything else is prose that
  // merely mentions a bold phrase.
  const first = children[0]!;
  if (first.type !== "strong") return null;

  const strongText = toString(first).trim();
  if (!strongText || strongText.length > 60) return null;

  // Everything after the bold run must be plain text — a code span, a link or
  // a second bold run means this is not a bare title.
  for (const child of children.slice(1)) {
    if (child.type !== "text") return null;
  }

  return strongText;
}

/**
 * The part of a paragraph that follows its bold heading run.
 *
 * The bold run becomes the heading, so it is removed from the body; the text
 * after it is the section's content.
 */
function restOfParagraph(node: RootContent): RootContent[] {
  if (node.type !== "paragraph") return [node];
  const rest = node.children.slice(1);
  if (!rest.length) return [];
  if (!rest.some((c) => c.type === "text" && c.value.trim())) return [];
  return [{ ...node, children: rest } as RootContent];
}

/**
 * Strip leftover markup markers from heading text.
 *
 * Some WeChat articles keep the layout tool's `unsetunset` marker around
 * headings — 19 documents have them. The heading path is prefixed to every
 * chunk's embedding text, so the residue ends up in the index.
 */
function cleanHeadingText(text: string): string {
  return text.replace(/unsetunset/g, "").replace(/\s+/g, " ").trim();
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
      const text = cleanHeadingText(toString(node).trim());
      while (stack.length && stack[stack.length - 1]!.depth >= depth) {
        stack.pop();
      }
      stack.push({ depth, text });
      current = {
        headingPath: stack.map((s) => s.text),
        nodes: [node],
      };
    } else if (CHUNKER_PARAMS.boldAsHeading) {
      const boldText = boldOnlyHeading(node);
      if (boldText !== null) {
        // Promoted to a heading: close the current section and start a new
        // one, so the bold line becomes part of the heading path. The bold
        // text itself is dropped from the body — it is the title — but the
        // rest of the paragraph stays.
        //
        // Bold titles are siblings of each other, not nested, so the path
        // does not grow with every section.
        flush();
        while (stack.length && stack[stack.length - 1]!.depth >= BOLD_HEADING_DEPTH) {
          stack.pop();
        }
        const text = cleanHeadingText(boldText);
        stack.push({ depth: BOLD_HEADING_DEPTH, text });
        current = {
          headingPath: stack.map((s) => s.text),
          nodes: restOfParagraph(node),
        };
        continue;
      }
      current.nodes.push(node);
    } else {
      current.nodes.push(node);
    }
  }
  flush();
  return sections;
}

/**
 * Remove the leading blockquote that cites the source.
 *
 * WeChat bodies usually start with a quote naming the account and linking the
 * original. It is repeated in every article and adds no meaning, but it sits
 * in the first chunk of every document and dilutes its embedding.
 */
function stripSourceQuote(nodes: RootContent[]): RootContent[] {
  if (!CHUNKER_PARAMS.stripSourceQuote) return nodes;
  const out = [...nodes];
  while (out.length) {
    const first = out[0]!;
    const isQuote =
      first.type === "blockquote" ||
      (first.type === "paragraph" &&
        first.children.length > 0 &&
        first.children.every((c) => c.type === "link" || c.type === "text"));
    if (!isQuote) break;
    out.shift();
  }
  return out;
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
  // The layout tool's `unsetunset` marker survives in some WeChat bodies and
  // would otherwise end up in every chunk that contains it.
  return parts.join("\n\n").replace(/unsetunset/g, "").trim();
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
    const nodes = stripSourceQuote(section.nodes);
    const text = nodesToMarkdown(nodes);
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
