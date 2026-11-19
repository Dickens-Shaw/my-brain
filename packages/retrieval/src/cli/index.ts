#!/usr/bin/env node
import { mkdirSync, statSync } from "node:fs";
import { dirname } from "node:path";
import { loadConfig } from "@my-brain/config";
import { buildIndex } from "../build-index.js";

function fileSizeMb(path: string): string {
  try {
    return `${(statSync(path).size / 1024 / 1024).toFixed(1)} MB`;
  } catch {
    return "n/a";
  }
}

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
    `Done. indexed=${result.indexed} skipped=${result.skipped} deleted=${result.deleted} chunks=${result.chunks}`,
  );
  console.log(`Index size: ${fileSizeMb(config.indexPath)}`);
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
