#!/usr/bin/env node
import { existsSync } from "node:fs";
import { join } from "node:path";
import { loadConfig } from "@my-brain/config";
import { normalizeAll } from "../normalize/index.js";
import { dedupeDocs } from "../dedupe.js";
import { embedDocuments, loadDocEmbeddings } from "../embed-docs.js";
import { induceTopics, loadTaxonomy } from "../topics.js";
import { classifyDocs, loadClassified } from "../classify.js";
import { writeVault } from "../write-vault.js";
import { ensureDir, readJson, writeJson } from "../fsutil.js";
import { findRepoRoot, migrateDir } from "../paths.js";
import type { NormalizedDoc } from "../types.js";

const STAGES = [
  "normalize",
  "dedupe",
  "embed",
  "induce",
  "classify",
  "write",
] as const;
type Stage = (typeof STAGES)[number];

function parseArgs(argv: string[]): { stage?: Stage; from?: Stage; help?: boolean } {
  const out: { stage?: Stage; from?: Stage; help?: boolean } = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;
    if (a === "--help" || a === "-h") out.help = true;
    else if (a === "--stage") out.stage = argv[++i] as Stage;
    else if (a === "--from") out.from = argv[++i] as Stage;
  }
  return out;
}

function requireStageFile<T>(path: string, hint: string): T {
  if (!existsSync(path)) {
    throw new Error(`Missing ${path}. ${hint}`);
  }
  return readJson<T>(path);
}

async function runStage(stage: Stage, migrateRoot: string): Promise<void> {
  switch (stage) {
    case "normalize": {
      const repoRoot = findRepoRoot();
      console.log("Stage normalize…");
      const result = normalizeAll(repoRoot);
      writeJson(join(migrateRoot, "normalized.json"), result.docs);
      writeJson(join(migrateRoot, "normalize-skipped.json"), result.skipped);
      const bySource: Record<string, number> = {};
      for (const d of result.docs) {
        bySource[d.source] = (bySource[d.source] ?? 0) + 1;
      }
      console.log(`  docs: ${result.docs.length}`, bySource);
      console.log(`  skipped: ${result.skipped.length}`);
      return;
    }
    case "dedupe": {
      console.log("Stage dedupe…");
      const docs = requireStageFile<NormalizedDoc[]>(
        join(migrateRoot, "normalized.json"),
        "Run --stage normalize first.",
      );
      const result = dedupeDocs(docs);
      writeJson(join(migrateRoot, "deduped.json"), result.docs);
      writeJson(join(migrateRoot, "dedupe-groups.json"), result.groups);
      console.log(
        `  docs: ${result.docs.length} (removed ${docs.length - result.docs.length} duplicates)`,
      );
      console.log(`  groups: ${result.groups.length}`);
      return;
    }
    case "embed": {
      console.log("Stage embed (document-level)…");
      const config = loadConfig({ requireOpenAI: true, requireVaultExists: false });
      const docs = requireStageFile<NormalizedDoc[]>(
        join(migrateRoot, "deduped.json"),
        "Run --stage dedupe first.",
      );
      await embedDocuments({ migrateRoot, docs, config });
      return;
    }
    case "induce": {
      console.log("Stage induce (topic taxonomy)…");
      const config = loadConfig({ requireOpenAI: true, requireVaultExists: false });
      const docs = requireStageFile<NormalizedDoc[]>(
        join(migrateRoot, "deduped.json"),
        "Run --stage dedupe first.",
      );
      const embeddings = loadDocEmbeddings(migrateRoot);
      if (!Object.keys(embeddings.vectors).length) {
        throw new Error("No doc embeddings — run --stage embed first.");
      }
      await induceTopics({ migrateRoot, docs, embeddings, config });
      return;
    }
    case "classify": {
      console.log("Stage classify…");
      const config = loadConfig({ requireOpenAI: true, requireVaultExists: false });
      const docs = requireStageFile<NormalizedDoc[]>(
        join(migrateRoot, "deduped.json"),
        "Run --stage dedupe first.",
      );
      const embeddings = loadDocEmbeddings(migrateRoot);
      const { report } = await classifyDocs({
        migrateRoot,
        docs,
        embeddings,
        config,
      });
      console.log("  classify report:", report.by_method);
      console.log(
        `  pending: ${report.pending_count} (${(report.pending_ratio * 100).toFixed(1)}%)`,
      );
      return;
    }
    case "write": {
      console.log("Stage write…");
      const config = loadConfig({ requireVaultExists: false });
      const tax = loadTaxonomy(migrateRoot);
      if (tax && tax.status !== "confirmed") {
        console.warn(
          `Warning: taxonomy status is "${tax?.status}" — write will still run using classified.json`,
        );
      }
      const docs = loadClassified(migrateRoot);
      if (!docs?.length) {
        throw new Error("No classified.json — run --stage classify first.");
      }
      const report = writeVault({
        vaultPath: config.vaultPath,
        docs,
        migrateRoot,
      });
      console.log(
        `  articles: ${report.written_articles}, chapters: ${report.written_chapters}, maps: ${report.maps}`,
      );
      if (report.errors.length) {
        console.error(`  errors: ${report.errors.length}`);
        for (const e of report.errors.slice(0, 20)) console.error(`    ${e}`);
      }
      return;
    }
  }
}

async function main(): Promise<void> {
  const args = parseArgs(process.argv.slice(2));
  if (args.help || (!args.stage && !args.from)) {
    console.log(`Usage:
  pnpm migrate --stage <${STAGES.join("|")}>
  pnpm migrate --from <stage>     # run this stage and all following until induce stop / end

Notes:
  - Stages chain via JSON under .data/migrate/
  - After induce, review topic-taxonomy.json and set status to "confirmed" before classify
`);
    process.exit(args.help ? 0 : 1);
  }

  const repoRoot = findRepoRoot();
  const migrateRoot = migrateDir(repoRoot);
  ensureDir(migrateRoot);

  if (args.stage) {
    if (!STAGES.includes(args.stage)) {
      throw new Error(`Unknown stage: ${args.stage}`);
    }
    await runStage(args.stage, migrateRoot);
    return;
  }

  const from = args.from!;
  const start = STAGES.indexOf(from);
  if (start < 0) throw new Error(`Unknown stage: ${from}`);

  for (let i = start; i < STAGES.length; i++) {
    const stage = STAGES[i]!;
    await runStage(stage, migrateRoot);
    if (stage === "induce") {
      console.log("Stopped after induce for human review.");
      break;
    }
  }
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
