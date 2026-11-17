import Database from "better-sqlite3";
import * as sqliteVec from "sqlite-vec";

/** CJK runs and alphanumeric words, with punctuation dropped. */
const TERM_RE =
  /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]+|[A-Za-z0-9_]+/gu;

export function splitTerms(query: string): string[] {
  return [...new Set(query.match(TERM_RE) ?? [])];
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
   * three tables therefore share one rowid, resolved from `chunks` first.
   */
  private deleteChunkRows(rowids: number[]): void {
    const delVec = this.db.prepare(`DELETE FROM chunk_vec WHERE rowid = ?`);
    const delFts = this.db.prepare(`DELETE FROM chunks_fts WHERE rowid = ?`);
    const delChunk = this.db.prepare(`DELETE FROM chunks WHERE rowid = ?`);
    for (const rowid of rowids) {
      delVec.run(BigInt(rowid));
      delFts.run(rowid);
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
    const terms = splitTerms(query);
    if (terms.length === 0) return [];

    // The trigram tokenizer cannot index anything shorter than 3 characters, so
    // two-character CJK words ("前端", "缓存") never match via MATCH. Route those
    // through LIKE, which trigram still accelerates, and merge both result sets.
    const longTerms = terms.filter((t) => t.length >= 3);
    const shortTerms = terms.filter((t) => t.length < 3);

    const ftsHits = longTerms.length ? this.ftsMatch(longTerms, k, filter) : [];
    const likeHits = shortTerms.length
      ? this.likeMatch(shortTerms, k, filter)
      : [];
    if (!likeHits.length) return ftsHits;
    if (!ftsHits.length) return likeHits;

    // Both lists are already ordered but their scores are not comparable
    // (BM25 vs term frequency), so fuse on rank position instead.
    const merged = new Map<string, number>();
    for (const list of [ftsHits, likeHits]) {
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
      return rows.map((r) => ({
        chunkId: r.chunkId,
        score: 1 / (1 + Math.abs(r.rank)),
      }));
    } catch {
      return [];
    }
  }

  private likeMatch(
    terms: string[],
    k: number,
    filter?: { scope?: string; topic?: string; source?: string },
  ): { chunkId: string; score: number }[] {
    const where = this.filterClause(filter);
    // Term frequency via length delta after removing the term; normalised by
    // chunk length so long chunks do not automatically win.
    const tf = terms
      .map(
        () =>
          `((LENGTH(c.text) - LENGTH(REPLACE(LOWER(c.text), ?, ''))) / ?)`,
      )
      .join(" + ");
    const anyMatch = terms.map(() => `LOWER(c.text) LIKE ?`).join(" OR ");
    const sql = `
      SELECT c.chunk_id AS chunkId,
             (${tf}) * 1.0 / (LENGTH(c.text) + 1) AS tf
      FROM chunks c
      WHERE (${anyMatch})${where.sql}
      ORDER BY tf DESC LIMIT ?
    `;
    const params: unknown[] = [];
    for (const t of terms) params.push(t.toLowerCase(), t.length);
    for (const t of terms) params.push(`%${t.toLowerCase()}%`);
    params.push(...where.params, k);

    try {
      const rows = this.db.prepare(sql).all(...params) as {
        chunkId: string;
        tf: number;
      }[];
      return rows.map((r) => ({ chunkId: r.chunkId, score: r.tf }));
    } catch {
      return [];
    }
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
}
