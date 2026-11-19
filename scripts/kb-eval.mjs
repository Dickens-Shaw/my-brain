#!/usr/bin/env node
/**
 * Evaluate the grep-based retrieval path against the golden question set.
 *
 * This is the counterpart to `pnpm eval`, which measures the index-backed
 * retrieval. Both run against the same questions so the two approaches can
 * be compared on the same terms.
 *
 * Scoring is by distinct document, not by chunk. The RAG arm returns chunks,
 * so its top 10 can hold ten chunks of one article; a document that matched
 * ten times is still one document, and that is what a reader has to open.
 *
 * The grep arm here is deliberately generous: it scores on term coverage
 * across title and body, which is what a careful agent does after grepping
 * and reading the surrounding context. It does not model the tokens an agent
 * burns on the misses, so it is an upper bound on the approach, not a
 * prediction of it.
 *
 * Usage: node scripts/kb-eval.mjs [--vault PATH] [--questions PATH]
 */
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { join, relative, resolve } from "node:path";

const args = process.argv.slice(2);
function flag(name, fallback) {
  const i = args.indexOf(`--${name}`);
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback;
}

const repoRoot = resolve(import.meta.dirname, "..");
const vaultPath = resolve(
  flag("vault", (() => {
    const envPath = join(repoRoot, ".env");
    if (existsSync(envPath)) {
      const m = readFileSync(envPath, "utf8").match(
        /^VAULT_PATH\s*=\s*["']?(.+?)["']?\s*$/m,
      );
      if (m) return m[1];
    }
    return join(homedir(), "Documents", "Obsidian Vault");
  })()),
);
const questionsPath = resolve(
  flag("questions", join(repoRoot, "eval/golden-questions.json")),
);

if (!existsSync(vaultPath)) {
  console.error(`Vault not found: ${vaultPath}`);
  process.exit(1);
}
if (!existsSync(questionsPath)) {
  console.error(`Questions not found: ${questionsPath}`);
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

function queryTerms(text) {
  const terms = [];
  for (const raw of new Set(text.match(TERM_RE) ?? [])) {
    if (/[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/u.test(raw)) {
      if (raw.length === 1) { terms.push(raw); continue; }
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

// ---------------------------------------------------------------- vault

function walkMd(root, out = []) {
  let entries;
  try { entries = readdirSync(root); } catch { return out; }
  for (const name of entries) {
    if (name.startsWith(".")) continue;
    const full = join(root, name);
    let st;
    try { st = statSync(full); } catch { continue; }
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

console.log(`Vault:  ${vaultPath}`);
console.log(`Golden: ${questionsPath}\n`);

const t0 = Date.now();
const docs = [];
for (const abs of walkMd(vaultPath)) {
  const rel = relative(vaultPath, abs).replace(/\\/g, "/");
  if (rel.startsWith("Maps/") || rel.startsWith("Templates/") || rel.startsWith("Assets/")) continue;
  let raw;
  try { raw = readFileSync(abs, "utf8"); } catch { continue; }
  const { data, body } = parseFrontmatter(raw);
  if (!data.id) continue;
  docs.push({
    id: String(data.id),
    title: String(data.title ?? ""),
    titleLower: String(data.title ?? "").toLowerCase(),
    bodyLower: body.toLowerCase(),
  });
}
console.log(`  ${docs.length} documents, ${((Date.now() - t0) / 1000).toFixed(1)}s\n`);

// ---------------------------------------------------------------- eval

const golden = JSON.parse(readFileSync(questionsPath, "utf8"));
const answerable = golden.filter((q) => q.expected_doc_ids?.length);
const unanswerable = golden.filter((q) => q.unanswerable);

let top5 = 0;
let top10 = 0;
let mrrSum = 0;
let totalMs = 0;
const misses = [];
const details = [];

for (const item of answerable) {
  const started = Date.now();
  const terms = queryTerms(item.query);

  const ranked = docs
    .map((doc) => {
      let titleHits = 0;
      let bodyHits = 0;
      for (const term of terms) {
        if (doc.titleLower.includes(term)) titleHits++;
        else if (doc.bodyLower.includes(term)) bodyHits++;
      }
      if (!titleHits && !bodyHits) return null;
      return {
        id: doc.id,
        score: ((titleHits + bodyHits) / terms.length) * 10 + titleHits * 2,
      };
    })
    .filter(Boolean)
    .sort((a, b) => b.score - a.score);

  totalMs += Date.now() - started;

  const ids = ranked.map((r) => r.id);
  let bestRank = 0;
  for (const id of item.expected_doc_ids) {
    const pos = ids.indexOf(id);
    if (pos >= 0 && (bestRank === 0 || pos + 1 < bestRank)) bestRank = pos + 1;
  }

  if (bestRank > 0 && bestRank <= 5) top5++;
  if (bestRank > 0 && bestRank <= 10) top10++;
  if (bestRank > 0) mrrSum += 1 / bestRank;
  else misses.push(`${item.id}: ${item.query}`);
  details.push({ id: item.id, category: item.category, rank: bestRank });
}

const n = answerable.length;
console.log("Grep-based retrieval (no index, no embeddings)");
console.log(`  top5      ${((top5 / n) * 100).toFixed(1)}%`);
console.log(`  top10     ${((top10 / n) * 100).toFixed(1)}%`);
console.log(`  MRR       ${(mrrSum / n).toFixed(3)}`);
console.log(`  avg       ${(totalMs / n).toFixed(0)}ms per query`);
console.log(`  questions ${n} answerable, ${unanswerable.length} unanswerable`);

const byCategory = {};
for (const d of details) {
  const c = byCategory[d.category] ?? { n: 0, top5: 0, top10: 0, mrr: 0 };
  c.n++;
  if (d.rank > 0 && d.rank <= 5) c.top5++;
  if (d.rank > 0 && d.rank <= 10) c.top10++;
  if (d.rank > 0) c.mrr += 1 / d.rank;
  byCategory[d.category] = c;
}
console.log("\nBy category");
console.log("category       n   top5   top10    MRR");
for (const [cat, c] of Object.entries(byCategory)) {
  console.log(
    `${cat.padEnd(14)}${String(c.n).padStart(2)}  ${((c.top5 / c.n) * 100).toFixed(1).padStart(5)}%  ${((c.top10 / c.n) * 100).toFixed(1).padStart(5)}%  ${(c.mrr / c.n).toFixed(3).padStart(6)}`,
  );
}

if (misses.length) {
  console.log("\nNot recalled:");
  for (const m of misses) console.log(`  ${m}`);
}
