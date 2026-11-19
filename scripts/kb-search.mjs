#!/usr/bin/env node
/**
 * Search the Obsidian Vault with grep-style matching, no index required.
 *
 * This is the retrieval half of the `knowledge-search` skill. It replaces the
 * vector index for the common case: find the documents most likely to
 * contain the answer, and hand back enough context to decide which one to
 * read.
 *
 * What it does better than a raw `grep -r`:
 *
 * - It narrows the search with the topic map before reading anything, so the
 *   common case does not touch all 5000+ files.
 * - It scores by term coverage rather than by match count, so a document that
 *   mentions three of the query's terms outranks one that repeats a single
 *   rare term.
 * - It matches Chinese on overlapping bigrams, so "持久化缓存" finds a
 *   document that says "持久化缓存" without needing the exact same token.
 * - It returns Obsidian deep links, so the caller can open a document
 *   without constructing a path.
 *
 * Usage:
 *   node scripts/kb-search.mjs "<query>" [--vault PATH] [--limit N]
 *                                 [--scope library|books|all] [--paths-only]
 */
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { join, relative, resolve } from "node:path";

const args = process.argv.slice(2);
const query = args.find((a) => !a.startsWith("--"));
if (!query) {
  console.error('Usage: node scripts/kb-search.mjs "<query>" [--vault PATH] [--limit N]');
  process.exit(1);
}

function flag(name, fallback) {
  const i = args.indexOf(`--${name}`);
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback;
}
const has = (name) => args.includes(`--${name}`);

const limit = Number(flag("limit", "10"));
const scope = flag("scope", "all");
const pathsOnly = has("paths-only");

// The Vault path comes from the repo's .env, so the script and the rest of
// the project agree on where the Vault is.
function vaultFromEnv() {
  const envPath = resolve(import.meta.dirname, "..", ".env");
  if (!existsSync(envPath)) return null;
  const m = readFileSync(envPath, "utf8").match(/^VAULT_PATH\s*=\s*["']?(.+?)["']?\s*$/m);
  return m ? m[1] : null;
}

const vaultPath = resolve(
  flag("vault", vaultFromEnv() ?? join(homedir(), "Documents", "Obsidian Vault")),
);

if (!existsSync(vaultPath)) {
  console.error(`Vault not found: ${vaultPath}`);
  console.error("Pass --vault PATH or set VAULT_PATH in .env");
  process.exit(1);
}

// ---------------------------------------------------------------- terms

const TERM_RE =
  /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]+|[A-Za-z0-9_]+/gu;

const CJK_STOPWORDS = new Set([
  "怎么", "如何", "什么", "为什么", "哪些", "哪里", "可以", "需要",
  "应该", "怎样", "是不是", "有没有", "这个", "那个", "我们", "你们",
  "他们", "它们", "以及", "并且", "或者", "但是", "因为", "所以",
  "一下", "一个", "一些", "这些", "那些", "关于", "对于", "通过",
  "使用", "进行", "实现", "方法", "方式", "办法", "时候", "问题",
  "介绍", "了解", "知道", "看看", "推荐",
]);

const LATIN_STOPWORDS = new Set([
  "the", "a", "an", "is", "are", "was", "were", "be", "to", "of", "in",
  "on", "for", "and", "or", "how", "what", "why", "which", "when",
  "with", "can", "should", "does", "do", "it", "this", "that",
]);

/** Split a Chinese run at stopword boundaries. */
function splitOnStopwords(run) {
  const segments = [];
  let current = "";
  let i = 0;
  while (i < run.length) {
    let matched = "";
    for (const stop of CJK_STOPWORDS) {
      if (run.startsWith(stop, i) && stop.length > matched.length) matched = stop;
    }
    if (matched) {
      if (current) segments.push(current);
      current = "";
      i += matched.length;
      continue;
    }
    current += run[i];
    i += 1;
  }
  if (current) segments.push(current);
  return segments;
}

/**
 * Turn a query into search terms.
 *
 * Chinese becomes overlapping bigrams so that a term of any length matches;
 * Latin words are kept whole. Stopwords are dropped because they describe
 * the question rather than the subject, and requiring them to appear turns a
 * good query into one that matches nothing.
 */
function queryTerms(text) {
  const terms = [];
  for (const raw of new Set(text.match(TERM_RE) ?? [])) {
    if (/[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/u.test(raw)) {
      if (raw.length === 1) {
        terms.push(raw);
        continue;
      }
      for (const seg of splitOnStopwords(raw)) {
        if (seg.length === 1) terms.push(seg);
        else for (let i = 0; i + 1 < seg.length; i++) terms.push(seg.slice(i, i + 2));
      }
    } else {
      const lower = raw.toLowerCase();
      if (lower.length < 2 || LATIN_STOPWORDS.has(lower)) continue;
      terms.push(lower);
    }
  }
  return [...new Set(terms)];
}

// ---------------------------------------------------------------- files

const SCOPE_DIRS = {
  library: "Library",
  books: "Books",
  notes: "Notes",
  projects: "Projects",
  daily: "Daily",
  inbox: "Inbox",
};

function walkMd(root, out = []) {
  let entries;
  try {
    entries = readdirSync(root);
  } catch {
    return out;
  }
  for (const name of entries) {
    if (name.startsWith(".")) continue;
    const full = join(root, name);
    let st;
    try {
      st = statSync(full);
    } catch {
      continue;
    }
    if (st.isDirectory()) walkMd(full, out);
    else if (name.endsWith(".md")) out.push(full);
  }
  return out;
}

function parseFrontmatter(raw) {
  const m = raw.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!m) return { data: {}, body: raw };
  const data = {};
  for (const line of m[1].split("\n")) {
    const kv = line.match(/^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$/);
    if (kv) data[kv[1]] = kv[2].replace(/^["']|["']$/g, "");
  }
  return { data, body: raw.slice(m[0].length) };
}

// ---------------------------------------------------------------- topic map

/**
 * Build a topic → document map from the generated Maps pages.
 *
 * The map is what keeps a search from touching every file: a query is first
 * matched against topic names, and the documents under a matching topic get
 * a boost, so a topic-specific document outranks a passing mention.
 */
function loadTopicMap() {
  const mapsRoot = join(vaultPath, "Maps");
  if (!existsSync(mapsRoot)) return [];

  const entries = [];
  for (const abs of walkMd(mapsRoot)) {
    const rel = relative(vaultPath, abs).replace(/\\/g, "/");
    // Maps/<一级>/<二级>.md → Library/<一级>/<二级>
    const parts = rel.split("/");
    if (parts.length < 3) continue;
    const topic = `${parts[1]}/${parts[2].replace(/\.md$/, "")}`;
    let raw;
    try {
      raw = readFileSync(abs, "utf8");
    } catch {
      continue;
    }
    const ids = [...raw.matchAll(/`([a-z]{2,3}-\d{6})`/g)].map((m) => m[1]);
    entries.push({ topic, ids });
  }
  return entries;
}

// ---------------------------------------------------------------- search

const terms = queryTerms(query);
if (terms.length === 0) {
  console.error("Query has no searchable terms");
  process.exit(1);
}

const topicMap = loadTopicMap();

// Score each topic against the query so the search can start narrow.
const scoredTopics = topicMap
  .map((t) => {
    const hay = t.topic.toLowerCase();
    let hits = 0;
    for (const term of terms) if (hay.includes(term)) hits++;
    return { ...t, hits };
  })
  .filter((t) => t.hits > 0)
  .sort((a, b) => b.hits - a.hits);

const topicIds = new Set(scoredTopics.flatMap((t) => t.ids));

const roots = [];
if (scope === "all") {
  roots.push(join(vaultPath, "Library"), join(vaultPath, "Books"));
} else {
  const dir = SCOPE_DIRS[scope];
  if (dir) roots.push(join(vaultPath, dir));
}

const started = Date.now();
const candidates = [];
for (const root of roots) {
  for (const abs of walkMd(root)) {
    const rel = relative(vaultPath, abs).replace(/\\/g, "/");
    let raw;
    try {
      raw = readFileSync(abs, "utf8");
    } catch {
      continue;
    }
    const { data, body } = parseFrontmatter(raw);
    const title = String(data.title ?? "");
    const id = String(data.id ?? "");
    const titleLower = title.toLowerCase();
    const bodyLower = body.toLowerCase();

    let titleHits = 0;
    let bodyHits = 0;
    for (const term of terms) {
      if (titleLower.includes(term)) titleHits++;
      else if (bodyLower.includes(term)) bodyHits++;
    }
    if (!titleHits && !bodyHits) continue;

    const coverage = (titleHits + bodyHits) / terms.length;
    const topicBoost = id && topicIds.has(id) ? 2 : 0;
    candidates.push({
      id,
      title,
      relPath: rel,
      topic: String(data.topics ?? "")
        .replace(/^\[|\]$/g, "")
        .replace(/"/g, ""),
      coverage,
      titleHits,
      bodyHits,
      score: coverage * 10 + titleHits * 2 + topicBoost,
    });
  }
}

candidates.sort((a, b) => b.score - a.score);
const results = candidates.slice(0, limit);
const elapsed = Date.now() - started;

// ---------------------------------------------------------------- output

if (pathsOnly) {
  for (const r of results) console.log(r.relPath);
  process.exit(0);
}

// A query whose terms only partly match is usually a sign the Vault does not
// cover the topic. The generic words ("原理", "工作", "流程") match almost
// anything, so a 60% coverage on a three-word query can still mean "not
// here". Reporting this is what stops the caller from reading a document
// that has nothing to do with the question.
const topCoverage = results.length ? results[0].coverage : 0;
const weakMatch = results.length > 0 && topCoverage < 0.75;

console.log(`Query: ${query}`);
console.log(`Terms: ${terms.join(", ")}`);
console.log(
  `Scanned ${scope === "all" ? "Library+Books" : scope}; ${candidates.length} matches in ${elapsed}ms`,
);
if (scoredTopics.length) {
  console.log(
    `Topics: ${scoredTopics.slice(0, 5).map((t) => t.topic).join(", ")}`,
  );
}
if (weakMatch) {
  console.log(
    `\n!! Weak match (best candidate covers only ${(topCoverage * 100).toFixed(0)}% of terms).`,
  );
  console.log(
    "   The Vault probably does not cover this. Verify before answering, or retry with different terms.",
  );
}
console.log("");

for (const r of results) {
  console.log(`## ${r.title}`);
  console.log(`   id:    ${r.id}`);
  console.log(`   path:  ${r.relPath}`);
  if (r.topic) console.log(`   topic: ${r.topic}`);
  console.log(
    `   match: ${(r.coverage * 100).toFixed(0)}% of terms (title ${r.titleHits}, body ${r.bodyHits})`,
  );
  console.log(
    `   open:  obsidian://open?path=${encodeURIComponent(join(vaultPath, r.relPath))}`,
  );
  console.log("");
}

if (results.length === 0) {
  console.log("No matches. Try:");
  console.log("  - a different technical term (English often works better)");
  console.log("  - a shorter, more concrete phrase");
  console.log("  - the exact title fragment if you know it");
}
