import { mkdirSync } from "node:fs";
import { join } from "node:path";

/** Directories the machine may write into. */
export const MACHINE_DIRS = ["Inbox", "Library", "Books", "Maps"] as const;

/** Handwritten area — machine must never create/modify/delete files here. */
export const HANDWRITTEN_DIRS = [
  "Notes",
  "Projects",
  "Daily",
  "Templates",
] as const;

export const VAULT_ROOT_DIRS = [
  ...MACHINE_DIRS,
  ...HANDWRITTEN_DIRS,
  "Assets",
] as const;

const ASSET_SUBDIRS = ["images", "pdf", "attachments"] as const;

/** Create the Vault top-level skeleton. Idempotent. */
export function ensureVaultSkeleton(vaultPath: string): string[] {
  const created: string[] = [];
  for (const dir of VAULT_ROOT_DIRS) {
    const full = join(vaultPath, dir);
    mkdirSync(full, { recursive: true });
    created.push(dir);
  }
  for (const sub of ASSET_SUBDIRS) {
    mkdirSync(join(vaultPath, "Assets", sub), { recursive: true });
  }
  return created;
}
