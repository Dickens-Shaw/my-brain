import { join } from "node:path";
import type { AppConfig } from "@my-brain/config";
import type { ClassifiedDoc, NormalizedDoc } from "./types.js";
import type { DocEmbeddingStore } from "./embed-docs.js";
import { loadTaxonomy, type TopicTaxonomy } from "./topics.js";
import { nearestCentroid } from "./kmeans.js";
import { chatJson } from "./openai.js";
import { existsSync } from "node:fs";
import { readJson, writeJson } from "./fsutil.js";

const PENDING = "待确认/未分类";
const SCORE_LOW = 0.35;
const MARGIN_LOW = 0.05;

export type ClassifyReport = {
  total: number;
  by_method: Record<string, number>;
  pending_count: number;
  pending_ratio: number;
  pending_ids: string[];
};

function classifiedPath(migrateRoot: string): string {
  return join(migrateRoot, "classified.json");
}

export function loadClassified(migrateRoot: string): ClassifiedDoc[] | null {
  const path = classifiedPath(migrateRoot);
  if (!existsSync(path)) return null;
  return readJson<ClassifiedDoc[]>(path);
}

export async function classifyDocs(options: {
  migrateRoot: string;
  docs: NormalizedDoc[];
  embeddings: DocEmbeddingStore;
  config: AppConfig;
}): Promise<{ docs: ClassifiedDoc[]; report: ClassifyReport }> {
  const { migrateRoot, docs, embeddings, config } = options;
  const tax = loadTaxonomy(migrateRoot);
  if (!tax) throw new Error("No topic-taxonomy.json — run induce first");
  if (tax.status !== "confirmed") {
    throw new Error(
      `Taxonomy status is "${tax.status}". Set status to "confirmed" after review before classify.`,
    );
  }
  if (!tax.leaves.length) throw new Error("Taxonomy has no leaves");

  const centroids = tax.leaves.map((l) => l.centroid);
  const out: ClassifiedDoc[] = [];
  const pendingIds: string[] = [];
  const byMethod: Record<string, number> = {
    cluster: 0,
    llm: 0,
    pending: 0,
    book: 0,
  };

  // Checkpoint: resume
  const existing = loadClassified(migrateRoot) ?? [];
  const done = new Map(existing.map((d) => [d.id, d]));

  for (const doc of docs) {
    if (done.has(doc.id)) {
      out.push(done.get(doc.id)!);
      continue;
    }

    if (doc.type === "book-chapter") {
      const classified: ClassifiedDoc = {
        ...doc,
        topics: [],
        classify_method: "book",
      };
      out.push(classified);
      done.set(doc.id, classified);
      byMethod.book! += 1;
      continue;
    }

    const vec = embeddings.vectors[doc.id];
    if (!vec) {
      const classified: ClassifiedDoc = {
        ...doc,
        topics: [PENDING],
        classify_method: "pending",
      };
      out.push(classified);
      done.set(doc.id, classified);
      pendingIds.push(doc.id);
      byMethod.pending! += 1;
      continue;
    }

    const near = nearestCentroid(vec, centroids);
    let topics: string[];
    let method: ClassifiedDoc["classify_method"] = "cluster";

    const margin = near.score - near.secondScore;
    if (near.score < SCORE_LOW || margin < MARGIN_LOW) {
      if (!config.chatApiKey) {
        topics = [PENDING];
        method = "pending";
      } else {
        const result = await chatJson<{ topics: string[]; pending?: boolean }>({
          apiKey: config.chatApiKey,
          baseUrl: config.chatBaseUrl,
          model: config.chatModel,
          system:
            "你是知识库分类助手。从给定类目列表中为文档选 1-3 个主题，首项为主主题。返回 JSON：{topics: string[], pending?: boolean}。topics 必须来自列表；无法判断时 pending=true。",
          user: JSON.stringify({
            title: doc.title,
            summary: doc.summary,
            candidates: tax.leaves.map((l) => l.name),
            nearest: tax.leaves[near.index]?.name,
            nearest_score: near.score,
          }),
        });
        if (result.pending || !result.topics?.length) {
          topics = [PENDING];
          method = "pending";
        } else {
          topics = result.topics.filter((t) =>
            tax.leaves.some((l) => l.name === t),
          );
          if (!topics.length) {
            topics = [PENDING];
            method = "pending";
          } else {
            method = "llm";
          }
        }
      }
    } else {
      const primary = tax.leaves[near.index]!.name;
      topics = [primary];
      // Optional second topic if close
      if (near.secondScore >= SCORE_LOW && margin < 0.12) {
        const second = tax.leaves[near.second]?.name;
        if (second && second !== primary) topics.push(second);
      }
      method = "cluster";
    }

    const classified: ClassifiedDoc = { ...doc, topics, classify_method: method };
    out.push(classified);
    done.set(doc.id, classified);
    byMethod[method]! += 1;
    if (method === "pending") pendingIds.push(doc.id);

    if (out.length % 50 === 0) {
      writeJson(classifiedPath(migrateRoot), [...done.values()]);
      console.log(`  classified ${out.length}/${docs.length}`);
    }
  }

  writeJson(classifiedPath(migrateRoot), out);

  const articleCount = docs.filter((d) => d.type === "article").length;
  const report: ClassifyReport = {
    total: out.length,
    by_method: byMethod,
    pending_count: pendingIds.length,
    pending_ratio: articleCount ? pendingIds.length / articleCount : 0,
    pending_ids: pendingIds,
  };
  writeJson(join(migrateRoot, "classify-report.json"), report);

  if (report.pending_ratio > 0.05) {
    console.warn(
      `WARNING: pending ratio ${(report.pending_ratio * 100).toFixed(1)}% > 5% — taxonomy may need adjustment`,
    );
  }
  return { docs: out, report };
}

export type { TopicTaxonomy };
