import type { IdPrefix } from "@my-brain/core";
import type { NormalizedDoc } from "../types.js";

/** Assign sequential permanent ids. Stable given a stable sort of docs. */
export function assignIds(
  docs: Omit<NormalizedDoc, "id">[],
  prefix: IdPrefix,
): NormalizedDoc[] {
  return docs.map((doc, i) => ({
    ...doc,
    id: `${prefix}-${String(i + 1).padStart(6, "0")}`,
  }));
}

export function sortKey(doc: Omit<NormalizedDoc, "id">): string {
  return [
    doc.source_url ?? "",
    doc.book ?? "",
    String(doc.chapter ?? ""),
    doc.body_path,
  ].join("\t");
}
