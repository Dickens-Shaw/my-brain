import type { Scope } from "@my-brain/core";

const MACHINE_PREFIXES = ["Inbox/", "Library/", "Books/", "Maps/"] as const;
const HANDWRITTEN_PREFIXES = [
  "Notes/",
  "Projects/",
  "Daily/",
  "Templates/",
] as const;

export function isMachinePath(relPath: string): boolean {
  const p = relPath.replace(/\\/g, "/");
  return MACHINE_PREFIXES.some((prefix) => p.startsWith(prefix));
}

export function isHandwrittenPath(relPath: string): boolean {
  const p = relPath.replace(/\\/g, "/");
  return HANDWRITTEN_PREFIXES.some((prefix) => p.startsWith(prefix));
}

/** Map a Vault-relative path to an index scope. Returns null for Maps/Templates/Assets. */
export function scopeFromPath(relPath: string): Scope | null {
  const p = relPath.replace(/\\/g, "/");
  if (p.startsWith("Library/")) return "library";
  if (p.startsWith("Books/")) return "books";
  if (p.startsWith("Notes/")) return "notes";
  if (p.startsWith("Projects/")) return "projects";
  if (p.startsWith("Daily/")) return "daily";
  if (p.startsWith("Inbox/")) return "inbox";
  return null;
}
