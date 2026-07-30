import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { config as loadDotenv } from "dotenv";
import { z } from "zod";

const ConfigSchema = z.object({
  vaultPath: z.string().min(1),
  openaiApiKey: z.string().optional(),
  openaiBaseUrl: z.string().optional(),
  indexPath: z.string(),
  embeddingModel: z.string(),
  embeddingDimensions: z.number().int().positive(),
  chatModel: z.string(),
  /** Chat may live on a different provider than embeddings (e.g. embeddings on
   *  SiliconFlow, chat on DeepSeek, which has no embeddings endpoint). */
  chatApiKey: z.string().optional(),
  chatBaseUrl: z.string().optional(),
});

export type AppConfig = z.infer<typeof ConfigSchema>;

function findRepoRoot(start = process.cwd()): string {
  let dir = resolve(start);
  for (;;) {
    if (existsSync(resolve(dir, "pnpm-workspace.yaml"))) return dir;
    const parent = resolve(dir, "..");
    if (parent === dir) {
      throw new Error(
        "Cannot find repo root (no pnpm-workspace.yaml). Run from inside the my-brain repo.",
      );
    }
    dir = parent;
  }
}

/**
 * Load app config from repo-root `.env` (and process.env).
 * Fails fast when VAULT_PATH is missing or does not exist.
 */
export function loadConfig(options?: {
  requireOpenAI?: boolean;
  requireVaultExists?: boolean;
}): AppConfig {
  const repoRoot = findRepoRoot();
  loadDotenv({ path: resolve(repoRoot, ".env") });

  const vaultPath = process.env.VAULT_PATH?.trim();
  if (!vaultPath) {
    throw new Error(
      "VAULT_PATH is not set. Copy .env.example to .env and set VAULT_PATH to your private Vault directory.",
    );
  }

  const requireVaultExists = options?.requireVaultExists ?? true;
  if (requireVaultExists && !existsSync(vaultPath)) {
    throw new Error(
      `VAULT_PATH points to a missing directory: ${vaultPath}\nCreate the Vault repo/folder first, or fix VAULT_PATH in .env.`,
    );
  }

  const openaiApiKey = process.env.OPENAI_API_KEY?.trim() || undefined;
  if (options?.requireOpenAI && !openaiApiKey) {
    throw new Error(
      "OPENAI_API_KEY is not set. Required for embedding / classification.",
    );
  }

  const indexPath =
    process.env.INDEX_PATH?.trim() ||
    resolve(repoRoot, ".data", "index.db");

  const openaiBaseUrl = process.env.OPENAI_BASE_URL?.trim() || undefined;

  return ConfigSchema.parse({
    vaultPath: resolve(vaultPath),
    openaiApiKey,
    openaiBaseUrl,
    indexPath,
    embeddingModel:
      process.env.EMBEDDING_MODEL?.trim() || "text-embedding-3-small",
    embeddingDimensions: Number(process.env.EMBEDDING_DIMENSIONS || 512),
    chatModel: process.env.CHAT_MODEL?.trim() || "gpt-4o-mini",
    // Fall back to the embedding provider when no separate chat provider is set.
    chatApiKey: process.env.CHAT_API_KEY?.trim() || openaiApiKey,
    chatBaseUrl: process.env.CHAT_BASE_URL?.trim() || openaiBaseUrl,
  });
}
