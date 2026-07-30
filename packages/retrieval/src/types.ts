import type { Scope, Source } from "@my-brain/core";

export type Chunk = {
  chunkId: string;
  docId: string;
  relPath: string;
  title: string;
  headingPath: string;
  text: string;
  source: Source | "manual" | "unknown";
  scope: Scope;
  primaryTopic: string;
  contentHash: string;
};

export type SearchHit = {
  chunkId: string;
  docId: string;
  relPath: string;
  title: string;
  headingPath: string;
  text: string;
  source: string;
  scope: Scope;
  score: number;
  vectorRank: number | null;
  bm25Rank: number | null;
};

export type SearchOptions = {
  query: string;
  limit?: number;
  scope?: Scope | "all";
  topic?: string;
  source?: string;
};

export type SearchTrace = {
  query: string;
  options: SearchOptions;
  vectorHits: { chunkId: string; rank: number; score: number }[];
  bm25Hits: { chunkId: string; rank: number; score: number }[];
  fused: { chunkId: string; rank: number; score: number }[];
  timingsMs: Record<string, number>;
};

export interface Embedder {
  readonly model: string;
  readonly dimensions: number;
  embed(texts: string[]): Promise<number[][]>;
}

export interface VectorStore {
  upsert(
    rows: {
      chunkId: string;
      embedding: number[];
      docId: string;
      source: string;
      scope: string;
      primaryTopic: string;
    }[],
  ): void;
  deleteByDocIds(docIds: string[]): void;
  search(
    embedding: number[],
    k: number,
    filter?: { scope?: string; topic?: string; source?: string },
  ): { chunkId: string; score: number }[];
}

export interface TextIndex {
  upsert(rows: { chunkId: string; text: string; docId: string }[]): void;
  deleteByDocIds(docIds: string[]): void;
  search(
    query: string,
    k: number,
    filter?: { scope?: string; topic?: string; source?: string },
  ): { chunkId: string; score: number }[];
}
