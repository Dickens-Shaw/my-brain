import matter from "gray-matter";
import { readFileSync } from "node:fs";
import { sha256, stripBodyNoise } from "../fsutil.js";

export type ParsedMd = {
  data: Record<string, unknown>;
  body: string;
  bodyHash: string;
  rawPath: string;
};

export type ParseResult =
  | { ok: true; value: ParsedMd }
  | { ok: false; reason: string };

export function parseExporterMd(path: string): ParseResult {
  const raw = readFileSync(path, "utf8");
  if (!raw.includes("---")) {
    return { ok: false, reason: "no frontmatter" };
  }
  let parsed: matter.GrayMatterFile<string>;
  try {
    parsed = matter(raw);
  } catch (err) {
    return {
      ok: false,
      reason: `frontmatter parse failed: ${err instanceof Error ? err.message : String(err)}`,
    };
  }
  const body = stripBodyNoise(parsed.content);
  if (body.length < 40) {
    return { ok: false, reason: "no body" };
  }
  return {
    ok: true,
    value: {
      data: (parsed.data ?? {}) as Record<string, unknown>,
      body,
      bodyHash: sha256(body),
      rawPath: path,
    },
  };
}

export function str(data: Record<string, unknown>, key: string): string | undefined {
  const v = data[key];
  if (v === undefined || v === null || v === "") return undefined;
  return String(v);
}

export function strArr(data: Record<string, unknown>, key: string): string[] {
  const v = data[key];
  if (!Array.isArray(v)) return [];
  return v.map(String).filter(Boolean);
}

export function num(data: Record<string, unknown>, key: string): number | undefined {
  const v = data[key];
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() && !Number.isNaN(Number(v))) return Number(v);
  return undefined;
}
