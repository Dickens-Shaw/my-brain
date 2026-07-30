#!/usr/bin/env node
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { loadConfig } from "@my-brain/config";
import { buildIndex } from "../build-index.js";

async function main(): Promise<void> {
  const full = process.argv.includes("--full");
  const config = loadConfig({ requireOpenAI: true });
  mkdirSync(dirname(config.indexPath), { recursive: true });
  console.log(`Indexing Vault ${config.vaultPath}`);
  console.log(`Index file: ${config.indexPath}${full ? " (full rebuild)" : ""}`);
  if (full) {
    // delete file for true full rebuild
    const { unlinkSync, existsSync } = await import("node:fs");
    if (existsSync(config.indexPath)) unlinkSync(config.indexPath);
  }
  const result = await buildIndex({ config, full });
  console.log(
    `Done. indexed=${result.indexed} skipped=${result.skipped} deleted=${result.deleted}`,
  );
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
