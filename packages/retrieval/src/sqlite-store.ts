import Database from "better-sqlite3";
import * as sqliteVec from "sqlite-vec";

/** CJK runs and alphanumeric words, with punctuation dropped. */
const TERM_RE =
  /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]+|[A-Za-z0-9_]+/gu;

/**
 * Chinese function words that carry no retrieval signal.
 *
 * A question like "Webpack 持久化缓存怎么配置" ends in "怎么配置". Requiring
 * that phrase to appear turns a good query into one that matches almost
 * nothing — the words are about the question, not the subject.
 */
const CJK_STOPWORDS = new Set([
  "怎么", "如何", "什么", "为什么", "哪些", "哪里", "可以", "需要",
  "应该", "怎样", "是不是", "有没有", "这个", "那个", "我们", "你们",
  "他们", "它们", "以及", "并且", "或者", "但是", "因为", "所以",
  "一下", "一个", "一些", "这些", "那些", "关于", "对于", "通过",
  "使用", "进行", "实现", "方法", "方式", "办法", "时候", "问题",
]);

/** English function words, same reasoning. */
const LATIN_STOPWORDS = new Set([
  "the", "a", "an", "is", "are", "was", "were", "be", "to", "of", "in",
  "on", "for", "and", "or", "how", "what", "why", "which", "when",
  "with", "can", "should", "does", "do", "it", "this", "that",
]);

export function splitTerms(query: string): string[] {
  return [...new Set(query.match(TERM_RE) ?? [])];
}

/**
 * Split a query into terms and drop the ones that carry no signal.
 *
 * Chinese runs are split into overlapping bigrams, because the bigram index
 * stores them that way; Latin words are kept whole.
 */
export function queryTerms(query: string): string[] {
  const terms: string[] = [];
  for (const raw of splitTerms(query)) {
    if (isCjk(raw)) {
      if (raw.length === 1) {
        terms.push(raw);
        continue;
      }
      for (const segment of splitOnStopwords(raw)) {
        if (segment.length === 1) {
          terms.push(segment);
          continue;
        }
        for (let i = 0; i + 1 < segment.length; i++) {
          terms.push(segment.slice(i, i + 2));
        }
      }
    } else {
      const lower = raw.toLowerCase();
      if (lower.length < 2) continue;
      if (LATIN_STOPWORDS.has(lower)) continue;
      terms.push(lower);
    }
  }
  return [...new Set(terms)];
}

/**
 * Split a Chinese run at stopword boundaries.
 *
 * "持久化缓存怎么配置" is two words — "持久化缓存" and "配置" — but the
 * bigrams taken across the boundary ("存怎", "么配") match nothing real.
 * Cutting the run at the stopword first keeps only the bigrams that lie
 * inside a word.
 */
function splitOnStopwords(run: string): string[] {
  const segments: string[] = [];
  let current = "";
  let i = 0;
  while (i < run.length) {
    let matched = "";
    for (const stop of CJK_STOPWORDS) {
      if (run.startsWith(stop, i) && stop.length > matched.length) {
        matched = stop;
      }
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

/** True when the term contains CJK characters. */
export function isCjk(term: string): boolean {
  return /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/u.test(
    term,
  );
}

/**
 * Convert text into searchable tokens for the CJK bigram index.
 *
 * Chinese runs are split into overlapping bigrams ("前端构建" → "前端 端构 构建"),
 * so a two-character query is an exact token lookup. Latin words are kept
 * whole. Single-character Chinese runs are indexed as-is and matched with a
 * prefix query.
 */
export function cjkBigrams(text: string): string {
  const runs = text.match(/[\p{Script=Han}]+|[\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]+|[\p{L}\p{N}_]+/gu) ?? [];
  const out = new Set<string>();
  for (const run of runs) {
    if (run.length === 1) {
      out.add(run);
      continue;
    }
    if (/^[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]+$/u.test(run)) {
      for (let i = 0; i + 1 < run.length; i++) out.add(run.slice(i, i + 2));
    } else {
      out.add(run.toLowerCase());
    }
  }
  return [...out].join(" ");
}

export type DocRow = {
  docId: string;
  relPath: string;
  title: string;
  source: string;
  scope: string;
  primaryTopic: string;
  contentHash: string;
  metaHash: string;
};

export type ChunkRow = {
  chunkId: string;
  docId: string;
  relPath: string;
  title: string;
  headingPath: string;
  text: string;
  source: string;
  scope: string;
  primaryTopic: string;
  contentHash: string;
  embedding: number[];
};

export class SqliteIndex {
  readonly db: Database.Database;
  private dimensions: number;

  constructor(path: string, dimensions: number) {
    this.dimensions = dimensions;
    this.db = new Database(path);
    this.db.pragma("journal_mode = WAL");
    sqliteVec.load(this.db);
    this.migrate();
  }

  private migrate(): void {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS docs (
        doc_id TEXT PRIMARY KEY,
        rel_path TEXT NOT NULL,
        title TEXT NOT NULL,
        source TEXT NOT NULL,
        scope TEXT NOT NULL,
        primary_topic TEXT NOT NULL DEFAULT '',
        content_hash TEXT NOT NULL,
        meta_hash TEXT NOT NULL DEFAULT ''
      );

      CREATE TABLE IF NOT EXISTS chunks (
        chunk_id TEXT PRIMARY KEY,
        doc_id TEXT NOT NULL,
        rel_path TEXT NOT NULL,
        title TEXT NOT NULL,
        heading_path TEXT NOT NULL,
        text TEXT NOT NULL,
        source TEXT NOT NULL,
        scope TEXT NOT NULL,
        primary_topic TEXT NOT NULL DEFAULT '',
        content_hash TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);

      CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
        text,
        chunk_id UNINDEXED,
        tokenize = 'trigram'
      );

      -- CJK bigram index.
      --
      -- The trigram tokenizer cannot index or match anything shorter than 3
      -- characters, so two-character Chinese words never match through FTS5.
      -- Routing them through a LIKE scan over the plain chunks table walked
      -- every chunk on every query, which with ~82k chunks was the dominant
      -- cost of a short-term query — and short CJK terms are the common case.
      --
      -- This table stores Chinese runs split into overlapping bigrams, so a
      -- two-character query is an exact token lookup instead of a scan. Latin
      -- words are stored whole and matched exactly.
      CREATE VIRTUAL TABLE IF NOT EXISTS chunks_cjk USING fts5(
        bi,
        chunk_id UNINDEXED,
        tokenize = 'unicode61'
      );

      CREATE TABLE IF NOT EXISTS search_traces (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        query TEXT NOT NULL,
        options_json TEXT NOT NULL,
        vector_json TEXT NOT NULL,
        bm25_json TEXT NOT NULL,
        fused_json TEXT NOT NULL,
        timings_json TEXT NOT NULL
      );

      -- How this index was built.
      --
      -- Vectors from different embedding models are not comparable, and
      -- changing the chunker changes the chunks. Without this table an
      -- incremental run after such a change silently mixes incompatible
      -- vectors in one database, and nothing errors.
      CREATE TABLE IF NOT EXISTS index_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
      );
    `);

    const exists = this.db
      .prepare(
        `SELECT name FROM sqlite_master WHERE type='table' AND name='chunk_vec'`,
      )
      .get() as { name?: string } | undefined;
    if (!exists) {
      // chunk_id is a plain metadata column rather than the primary key: vec0
      // keeps a TEXT primary key in an unindexed shadow column, so deleting by
      // it scans every row. The integer rowid is indexed, and is kept equal to
      // chunks.rowid so all three tables can be maintained by rowid.
      this.db.exec(`
        CREATE VIRTUAL TABLE chunk_vec USING vec0(
          embedding float[${this.dimensions}],
          +chunk_id TEXT,
          doc_id TEXT,
          source TEXT,
          scope TEXT,
          primary_topic TEXT
        );
      `);
    }
  }

  close(): void {
    this.db.close();
  }

  /** Read the index metadata (how this index was built). */
  getMeta(): Record<string, string> {
    const rows = this.db
      .prepare(`SELECT key, value FROM index_meta`)
      .all() as { key: string; value: string }[];
    return Object.fromEntries(rows.map((r) => [r.key, r.value]));
  }

  setMeta(key: string, value: string): void {
    this.db
      .prepare(
        `INSERT INTO index_meta(key, value) VALUES (?, ?)
         ON CONFLICT(key) DO UPDATE SET value = excluded.value`,
      )
      .run(key, value);
  }

  /** Number of chunks currently indexed — used to detect an empty index. */
  chunkCount(): number {
    const row = this.db
      .prepare(`SELECT COUNT(*) AS n FROM chunks`)
      .get() as { n: number };
    return row.n;
  }

  getDocHashes(): Map<
    string,
    { contentHash: string; metaHash: string; relPath: string }
  > {
    const rows = this.db
      .prepare(`SELECT doc_id, content_hash, meta_hash, rel_path FROM docs`)
      .all() as {
      doc_id: string;
      content_hash: string;
      meta_hash: string;
      rel_path: string;
    }[];
    return new Map(
      rows.map((r) => [
        r.doc_id,
        {
          contentHash: r.content_hash,
          metaHash: r.meta_hash,
          relPath: r.rel_path,
        },
      ]),
    );
  }

  listTopics(): { topic: string; count: number }[] {
    return this.db
      .prepare(
        `SELECT primary_topic AS topic, COUNT(*) AS count FROM docs
         WHERE primary_topic != '' AND scope = 'library'
         GROUP BY primary_topic ORDER BY count DESC`,
      )
      .all() as { topic: string; count: number }[];
  }

  getDocById(id: string): DocRow | null {
    const r = this.db
      .prepare(
        `SELECT doc_id, rel_path, title, source, scope, primary_topic, content_hash, meta_hash
         FROM docs WHERE doc_id = ?`,
      )
      .get(id) as
      | {
          doc_id: string;
          rel_path: string;
          title: string;
          source: string;
          scope: string;
          primary_topic: string;
          content_hash: string;
          meta_hash: string;
        }
      | undefined;
    if (!r) return null;
    return {
      docId: r.doc_id,
      relPath: r.rel_path,
      title: r.title,
      source: r.source,
      scope: r.scope,
      primaryTopic: r.primary_topic,
      contentHash: r.content_hash,
      metaHash: r.meta_hash,
    };
  }

  getDocByPath(relPath: string): DocRow | null {
    const r = this.db
      .prepare(
        `SELECT doc_id, rel_path, title, source, scope, primary_topic, content_hash, meta_hash
         FROM docs WHERE rel_path = ?`,
      )
      .get(relPath) as
      | {
          doc_id: string;
          rel_path: string;
          title: string;
          source: string;
          scope: string;
          primary_topic: string;
          content_hash: string;
          meta_hash: string;
        }
      | undefined;
    if (!r) return null;
    return {
      docId: r.doc_id,
      relPath: r.rel_path,
      title: r.title,
      source: r.source,
      scope: r.scope,
      primaryTopic: r.primary_topic,
      contentHash: r.content_hash,
      metaHash: r.meta_hash,
    };
  }

  upsertDoc(row: DocRow): void {
    this.db
      .prepare(
        `INSERT INTO docs(doc_id, rel_path, title, source, scope, primary_topic, content_hash, meta_hash)
         VALUES (@docId, @relPath, @title, @source, @scope, @primaryTopic, @contentHash, @metaHash)
         ON CONFLICT(doc_id) DO UPDATE SET
           rel_path=excluded.rel_path,
           title=excluded.title,
           source=excluded.source,
           scope=excluded.scope,
           primary_topic=excluded.primary_topic,
           content_hash=excluded.content_hash,
           meta_hash=excluded.meta_hash`,
      )
      .run(row);
  }

  updateDocMeta(docId: string, primaryTopic: string, metaHash: string): void {
    this.db
      .prepare(
        `UPDATE docs SET primary_topic = ?, meta_hash = ? WHERE doc_id = ?`,
      )
      .run(primaryTopic, metaHash, docId);
    this.db
      .prepare(`UPDATE chunks SET primary_topic = ? WHERE doc_id = ?`)
      .run(primaryTopic, docId);
    // Update via rowid: chunk_id is an unindexed vec0 metadata column, so
    // filtering on it would scan the whole table once per chunk.
    const rows = this.db
      .prepare(`SELECT rowid FROM chunks WHERE doc_id = ?`)
      .all(docId) as { rowid: number }[];
    const upd = this.db.prepare(
      `UPDATE chunk_vec SET primary_topic = ? WHERE rowid = ?`,
    );
    const tx = this.db.transaction(() => {
      for (const r of rows) upd.run(primaryTopic, r.rowid);
    });
    tx();
  }

  /**
   * `chunk_id` is only indexed on the `chunks` table — on `chunk_vec` it is a
   * vec0 metadata column and on `chunks_fts` it is UNINDEXED, so deleting by it
   * scans the whole table and makes bulk indexing degrade quadratically. All
   * four tables therefore share one rowid, resolved from `chunks` first.
   */
  private deleteChunkRows(rowids: number[]): void {
    const delVec = this.db.prepare(`DELETE FROM chunk_vec WHERE rowid = ?`);
    const delFts = this.db.prepare(`DELETE FROM chunks_fts WHERE rowid = ?`);
    const delCjk = this.db.prepare(`DELETE FROM chunks_cjk WHERE rowid = ?`);
    const delChunk = this.db.prepare(`DELETE FROM chunks WHERE rowid = ?`);
    for (const rowid of rowids) {
      delVec.run(BigInt(rowid));
      delFts.run(rowid);
      delCjk.run(rowid);
      delChunk.run(rowid);
    }
  }

  deleteByDocIds(docIds: string[]): void {
    if (!docIds.length) return;
    const rows = this.db
      .prepare(
        `SELECT rowid FROM chunks WHERE doc_id IN (${docIds.map(() => "?").join(",")})`,
      )
      .all(...docIds) as { rowid: number }[];

    const delDoc = this.db.prepare(`DELETE FROM docs WHERE doc_id = ?`);
    const tx = this.db.transaction(() => {
      this.deleteChunkRows(rows.map((r) => r.rowid));
      for (const id of docIds) delDoc.run(id);
    });
    tx();
  }

  upsertChunks(rows: ChunkRow[]): void {
    if (!rows.length) return;

    const findRowid = this.db.prepare(
      `SELECT rowid FROM chunks WHERE chunk_id = ?`,
    );
    const insChunk = this.db.prepare(
      `INSERT INTO chunks(chunk_id, doc_id, rel_path, title, heading_path, text, source, scope, primary_topic, content_hash)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    );
    const insVec = this.db.prepare(
      `INSERT INTO chunk_vec(rowid, embedding, chunk_id, doc_id, source, scope, primary_topic)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
    );
    const insFts = this.db.prepare(
      `INSERT INTO chunks_fts(rowid, text, chunk_id) VALUES (?, ?, ?)`,
    );
    const insCjk = this.db.prepare(
      `INSERT INTO chunks_cjk(rowid, bi, chunk_id) VALUES (?, ?, ?)`,
    );

    const tx = this.db.transaction(() => {
      for (const r of rows) {
        const existing = findRowid.get(r.chunkId) as
          | { rowid: number }
          | undefined;
        if (existing) this.deleteChunkRows([existing.rowid]);

        const info = insChunk.run(
          r.chunkId,
          r.docId,
          r.relPath,
          r.title,
          r.headingPath,
          r.text,
          r.source,
          r.scope,
          r.primaryTopic,
          r.contentHash,
        );
        // vec0 checks for SQLITE_INTEGER on the primary key, and better-sqlite3
        // binds plain JS numbers as REAL, so the rowid must be passed as BigInt.
        const rowid = BigInt(info.lastInsertRowid);

        insVec.run(
          rowid,
          new Float32Array(r.embedding),
          r.chunkId,
          r.docId,
          r.source,
          r.scope,
          r.primaryTopic,
        );
        insFts.run(Number(rowid), r.text, r.chunkId);
        insCjk.run(Number(rowid), cjkBigrams(r.text), r.chunkId);
      }
    });
    tx();
  }

  vectorSearch(
    embedding: number[],
    k: number,
    filter?: { scope?: string; topic?: string; source?: string },
  ): { chunkId: string; score: number }[] {
    const clauses = [`embedding MATCH ?`, `k = ?`];
    const params: unknown[] = [new Float32Array(embedding), k];
    if (filter?.scope && filter.scope !== "all") {
      clauses.push(`scope = ?`);
      params.push(filter.scope);
    }
    if (filter?.topic) {
      clauses.push(`primary_topic = ?`);
      params.push(filter.topic);
    }
    if (filter?.source) {
      clauses.push(`source = ?`);
      params.push(filter.source);
    }
    const rows = this.db
      .prepare(
        `SELECT chunk_id AS chunkId, distance FROM chunk_vec WHERE ${clauses.join(" AND ")}`,
      )
      .all(...params) as { chunkId: string; distance: number }[];
    return rows.map((r) => ({
      chunkId: r.chunkId,
      score: 1 / (1 + r.distance),
    }));
  }

  bm25Search(
    query: string,
    k: number,
    filter?: { scope?: string; topic?: string; source?: string },
  ): { chunkId: string; score: number }[] {
    const terms = queryTerms(query);
    if (terms.length === 0) return [];

    // Two indexes, because neither tokenizer alone covers the corpus:
    //
    // - `chunks_fts` (trigram) matches a term only if it lines up with a
    //   3-character window. A two-character Chinese word never matches, and
    //   neither does a longer Chinese word that is not aligned to a window
    //   ("数据库优化" misses "数据库索引与查询优化").
    // - `chunks_cjk` (bigrams) matches Chinese terms of any length, because
    //   the term is decomposed into bigram tokens.
    //
    // Chinese terms therefore always go to the bigram index. Latin terms of
    // three characters or more go to the trigram index, which handles them
    // exactly. The old LIKE scan over the plain `chunks` table is gone: it
    // walked every chunk on every query, and short CJK terms are the common
    // case.
    const cjkTerms = terms.filter((t) => isCjk(t));
    const latinTerms = terms.filter((t) => !isCjk(t) && t.length >= 3);
    const shortLatin = terms.filter((t) => !isCjk(t) && t.length < 3);

    // Both arms fetch more than `k` candidates: the ranking is by how many
    // query terms a chunk contains, so the candidate pool has to be wider
    // than the final result.
    const pool = Math.max(k * 3, 60);

    const ftsHits = latinTerms.length ? this.ftsMatch(latinTerms, pool, filter) : [];
    const cjkHits =
      cjkTerms.length || shortLatin.length
        ? this.cjkMatch([...cjkTerms, ...shortLatin], pool, filter)
        : [];

    if (!cjkHits.length) return ftsHits.slice(0, k);
    if (!ftsHits.length) return cjkHits.slice(0, k);

    // The two lists are ordered but their scores are not comparable (BM25 over
    // different token spaces), so they are fused on rank position.
    const merged = new Map<string, number>();
    for (const list of [ftsHits, cjkHits]) {
      list.forEach((hit, i) => {
        merged.set(hit.chunkId, (merged.get(hit.chunkId) ?? 0) + 1 / (60 + i + 1));
      });
    }
    return [...merged]
      .map(([chunkId, score]) => ({ chunkId, score }))
      .sort((a, b) => b.score - a.score)
      .slice(0, k);
  }

  private filterClause(filter?: {
    scope?: string;
    topic?: string;
    source?: string;
  }): { sql: string; params: unknown[] } {
    let sql = "";
    const params: unknown[] = [];
    if (filter?.scope && filter.scope !== "all") {
      sql += ` AND c.scope = ?`;
      params.push(filter.scope);
    }
    if (filter?.topic) {
      sql += ` AND c.primary_topic = ?`;
      params.push(filter.topic);
    }
    if (filter?.source) {
      sql += ` AND c.source = ?`;
      params.push(filter.source);
    }
    return { sql, params };
  }

  private ftsMatch(
    terms: string[],
    k: number,
    filter?: { scope?: string; topic?: string; source?: string },
  ): { chunkId: string; score: number }[] {
    // Quote each term as an FTS5 phrase and OR them, so one rare term missing
    // does not drop the whole document (the default between terms is AND).
    const expr = terms.map((t) => `"${t.replace(/"/g, "")}"`).join(" OR ");
    const where = this.filterClause(filter);
    const sql = `
      SELECT c.chunk_id AS chunkId, bm25(chunks_fts) AS rank
      FROM chunks_fts
      JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id
      WHERE chunks_fts MATCH ?${where.sql}
      ORDER BY rank LIMIT ?
    `;
    try {
      const rows = this.db
        .prepare(sql)
        .all(expr, ...where.params, k) as { chunkId: string; rank: number }[];
      // FTS5 bm25() is negative and lower is better; map to a positive score.
      // Coverage decides the order, for the same reason as in cjkMatch.
      return this.rankByTermCoverage(rows, terms, k);
    } catch {
      return [];
    }
  }

  /**
   * Match terms against the bigram index.
   *
   * A Chinese term of two or more characters is decomposed into bigram
   * tokens and matched as a phrase, so the bigrams must appear in order and
   * next to each other — "数据库优化" does not match a document containing
   * "数据库索引". A single-character term is stored as a one-character token
   * and matched with a prefix query, so "前" finds every chunk containing a
   * bigram that starts with it.
   *
   * Terms are ANDed, so a term that is not in the corpus does not silently
   * widen the result to everything else.
   */
  private cjkMatch(
    terms: string[],
    k: number,
    filter?: { scope?: string; topic?: string; source?: string },
  ): { chunkId: string; score: number }[] {
    const clauses = terms.map((t) => {
      const clean = t.replace(/"/g, "");
      if (clean.length === 1) return `"${clean}"*`;
      const bigrams = cjkBigrams(clean).split(" ").filter(Boolean);
      // A term with no Chinese characters (a short Latin word) is indexed
      // whole and matched exactly.
      if (!bigrams.length) return `"${clean.toLowerCase()}"`;
      return `"${bigrams.join(" ")}"`;
    });
    // OR, not AND.
    //
    // A question contains words that are about the question rather than the
    // subject — "怎么配置", "有什么区别". Requiring all of them turns a
    // specific question into one that matches almost nothing, and the
    // document that actually answers it is pushed out of the results.
    //
    // The ranking below compensates: a chunk that contains more of the
    // query terms outranks one that contains fewer.
    const expr = clauses.join(" OR ");
    const where = this.filterClause(filter);
    const sql = `
      SELECT c.chunk_id AS chunkId, bm25(chunks_cjk) AS rank
      FROM chunks_cjk
      JOIN chunks c ON c.chunk_id = chunks_cjk.chunk_id
      WHERE chunks_cjk MATCH ?${where.sql}
      ORDER BY rank LIMIT ?
    `;
    try {
      const rows = this.db
        .prepare(sql)
        .all(expr, ...where.params, k) as { chunkId: string; rank: number }[];
      return this.rankByTermCoverage(rows, terms, k);
    } catch {
      return [];
    }
  }

  /**
   * Re-rank candidates by how many query terms they contain.
   *
   * FTS5's BM25 ranks by term frequency, which treats a chunk containing one
   * rare term as better than a chunk containing three of the query's terms.
   * For a multi-term question, coverage is the better signal: the document
   * that answers the whole question mentions more of it.
   */
  private rankByTermCoverage(
    rows: { chunkId: string; rank: number }[],
    terms: string[],
    k: number,
  ): { chunkId: string; score: number }[] {
    if (rows.length <= 1 || terms.length <= 1) {
      return rows.map((r) => ({
        chunkId: r.chunkId,
        score: 1 / (1 + Math.abs(r.rank)),
      }));
    }

    const chunkIds = rows.map((r) => r.chunkId);
    const placeholders = chunkIds.map(() => "?").join(",");
    const textRows = this.db
      .prepare(`SELECT chunk_id, text FROM chunks WHERE chunk_id IN (${placeholders})`)
      .all(...chunkIds) as { chunk_id: string; text: string }[];
    const textById = new Map(textRows.map((r) => [r.chunk_id, r.text]));

    const scored = rows.map((r) => {
      const text = (textById.get(r.chunkId) ?? "").toLowerCase();
      let hits = 0;
      for (const term of terms) {
        const clean = term.toLowerCase();
        // A two-character Chinese term is a bigram, so it appears literally
        // in the text; a single character is a prefix match.
        if (clean.length === 1) {
          if (text.includes(clean)) hits += 1;
        } else if (text.includes(clean)) {
          hits += 1;
        }
      }
      const coverage = hits / terms.length;
      // BM25 keeps the ordering within the same coverage level.
      const bm25 = 1 / (1 + Math.abs(r.rank));
      return { chunkId: r.chunkId, score: coverage * 10 + bm25 };
    });

    return scored.sort((a, b) => b.score - a.score).slice(0, k);
  }

  getChunks(chunkIds: string[]): Map<string, ChunkRow> {
    const map = new Map<string, ChunkRow>();
    if (!chunkIds.length) return map;
    const stmt = this.db.prepare(
      `SELECT chunk_id, doc_id, rel_path, title, heading_path, text, source, scope, primary_topic, content_hash
       FROM chunks WHERE chunk_id = ?`,
    );
    for (const id of chunkIds) {
      const r = stmt.get(id) as
        | {
            chunk_id: string;
            doc_id: string;
            rel_path: string;
            title: string;
            heading_path: string;
            text: string;
            source: string;
            scope: string;
            primary_topic: string;
            content_hash: string;
          }
        | undefined;
      if (!r) continue;
      map.set(id, {
        chunkId: r.chunk_id,
        docId: r.doc_id,
        relPath: r.rel_path,
        title: r.title,
        headingPath: r.heading_path,
        text: r.text,
        source: r.source,
        scope: r.scope,
        primaryTopic: r.primary_topic,
        contentHash: r.content_hash,
        embedding: [],
      });
    }
    return map;
  }

  /**
   * Map chunk id → document id for a set of chunks.
   *
   * The search trace stores chunk ids, so the evaluation needs the document
   * each candidate belongs to. One query for the whole set, rather than one
   * per candidate.
   */
  getDocIdsByChunkIds(chunkIds: string[]): Map<string, string> {
    const map = new Map<string, string>();
    if (!chunkIds.length) return map;
    const placeholders = chunkIds.map(() => "?").join(",");
    const rows = this.db
      .prepare(
        `SELECT chunk_id, doc_id FROM chunks WHERE chunk_id IN (${placeholders})`,
      )
      .all(...chunkIds) as { chunk_id: string; doc_id: string }[];
    for (const r of rows) map.set(r.chunk_id, r.doc_id);
    return map;
  }

  writeTrace(trace: {
    query: string;
    options: unknown;
    vector: unknown;
    bm25: unknown;
    fused: unknown;
    timings: unknown;
  }): void {
    this.db
      .prepare(
        `INSERT INTO search_traces(created_at, query, options_json, vector_json, bm25_json, fused_json, timings_json)
         VALUES (?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        new Date().toISOString(),
        trace.query,
        JSON.stringify(trace.options),
        JSON.stringify(trace.vector),
        JSON.stringify(trace.bm25),
        JSON.stringify(trace.fused),
        JSON.stringify(trace.timings),
      );
  }

  /**
   * Trim the trace table.
   *
   * The traces are the only table that grows without bound — every query
   * appends a row. They are also what a failed query is later reconstructed
   * from, so they are kept for a while, but not forever.
   */
  pruneTraces(options?: { keepDays?: number; keepRows?: number }): number {
    const keepDays = options?.keepDays ?? 90;
    const keepRows = options?.keepRows ?? 10000;

    const cutoff = new Date(Date.now() - keepDays * 86400_000).toISOString();
    const older = this.db
      .prepare(`SELECT COUNT(*) AS n FROM search_traces WHERE created_at < ?`)
      .get(cutoff) as { n: number };
    if (older.n > 0) {
      this.db.prepare(`DELETE FROM search_traces WHERE created_at < ?`).run(cutoff);
    }

    const total = this.db
      .prepare(`SELECT COUNT(*) AS n FROM search_traces`)
      .get() as { n: number };
    if (total.n > keepRows) {
      this.db
        .prepare(
          `DELETE FROM search_traces WHERE id NOT IN (
             SELECT id FROM search_traces ORDER BY id DESC LIMIT ?
           )`,
        )
        .run(keepRows);
    }
    return older.n;
  }
}
