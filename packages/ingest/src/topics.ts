import { existsSync } from "node:fs";
import { join } from "node:path";
import type { AppConfig } from "@my-brain/config";
import type { NormalizedDoc } from "./types.js";
import type { DocEmbeddingStore } from "./embed-docs.js";
import { readJson, writeJson } from "./fsutil.js";
import { kmeansCosine } from "./kmeans.js";
import { chatJson } from "./openai.js";

export type TopicLeaf = {
  id: string;
  name: string; // 一级/二级
  level1: string;
  level2: string;
  doc_count: number;
  sample_titles: string[];
  too_large: boolean;
  too_small: boolean;
  centroid: number[];
};

export type TopicTaxonomy = {
  status: "pending_review" | "confirmed";
  created_at: string;
  k: number;
  leaves: TopicLeaf[];
  notes: string;
};

function taxonomyPath(migrateRoot: string): string {
  return join(migrateRoot, "topic-taxonomy.json");
}

export function loadTaxonomy(migrateRoot: string): TopicTaxonomy | null {
  const path = taxonomyPath(migrateRoot);
  if (!existsSync(path)) return null;
  return readJson<TopicTaxonomy>(path);
}

export function saveTaxonomy(migrateRoot: string, tax: TopicTaxonomy): void {
  writeJson(taxonomyPath(migrateRoot), {
    ...tax,
    // Keep centroids for classify; file will be large but OK for local staging
  });
}

/**
 * Cluster article embeddings and ask LLM to name each cluster.
 * Writes topic-taxonomy.json with status pending_review and STOPS.
 */
export async function induceTopics(options: {
  migrateRoot: string;
  docs: NormalizedDoc[];
  embeddings: DocEmbeddingStore;
  config: AppConfig;
  k?: number;
}): Promise<TopicTaxonomy> {
  const { migrateRoot, docs, embeddings, config } = options;
  if (!config.chatApiKey) {
    throw new Error(
      "No chat provider configured. Set OPENAI_API_KEY, or CHAT_API_KEY when chat lives on a different provider than embeddings.",
    );
  }

  const articles = docs.filter((d) => d.type === "article" && embeddings.vectors[d.id]);
  const vectors = articles.map((d) => embeddings.vectors[d.id]!);
  // ~sqrt(n/2) heuristic, clamped
  const k =
    options.k ??
    Math.max(12, Math.min(48, Math.round(Math.sqrt(articles.length / 2))));

  console.log(`Clustering ${articles.length} articles into k=${k}...`);
  const { labels, centroids } = kmeansCosine(vectors, k);

  const byCluster = new Map<number, NormalizedDoc[]>();
  for (let i = 0; i < articles.length; i++) {
    const c = labels[i]!;
    const list = byCluster.get(c) ?? [];
    list.push(articles[i]!);
    byCluster.set(c, list);
  }

  const leaves: TopicLeaf[] = [];

  for (let c = 0; c < centroids.length; c++) {
    const members = byCluster.get(c) ?? [];
    if (members.length === 0) continue;
    const samples = members.slice(0, 8).map((d) => d.title);
    const named = await chatJson<{ level1: string; level2: string; name_zh: string }>({
      apiKey: config.chatApiKey,
      baseUrl: config.chatBaseUrl,
      model: config.chatModel,
      system:
        "你是知识库主题归纳助手。根据样本标题为簇命名两级中文类目。返回 JSON：{level1, level2, name_zh}。level1/level2 简短（2-8字），不要标点。",
      user: JSON.stringify({
        sample_titles: samples,
        doc_count: members.length,
      }),
    });
    const level1 = (named.level1 || "未命名").replace(/[\/\\]/g, "-");
    const level2 = (named.level2 || named.name_zh || `簇${c}`).replace(/[\/\\]/g, "-");
    const name = `${level1}/${level2}`;
    leaves.push({
      id: `leaf-${String(c).padStart(3, "0")}`,
      name,
      level1,
      level2,
      doc_count: members.length,
      sample_titles: samples,
      too_large: members.length > 150,
      too_small: members.length < 10,
      centroid: centroids[c]!,
    });
    console.log(`  ${name} (${members.length})`);
  }

  const tax: TopicTaxonomy = {
    status: "pending_review",
    created_at: new Date().toISOString(),
    k,
    leaves: leaves.sort((a, b) => b.doc_count - a.doc_count),
    notes:
      "审核后把 status 改为 confirmed，可改 name/level1/level2、合并或删除叶子。然后运行 classify。过大(>150)/过小(<10)叶子已标记。",
  };
  saveTaxonomy(migrateRoot, tax);

  const oversized = tax.leaves.filter((l) => l.too_large).length;
  const undersized = tax.leaves.filter((l) => l.too_small).length;
  console.log(
    `\nCandidate taxonomy written to ${taxonomyPath(migrateRoot)}`,
  );
  console.log(
    `Leaves: ${tax.leaves.length}; too_large: ${oversized}; too_small: ${undersized}`,
  );
  console.log(
    "STOP: review the file, set status to \"confirmed\", then run: pnpm migrate --stage classify",
  );
  return tax;
}
