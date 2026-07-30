/**
 * Only OpenAI's text-embedding-3 family is trained with Matryoshka
 * representation learning, so only it accepts a `dimensions` request parameter
 * to truncate the vector. Sending that field to other OpenAI-compatible
 * providers (SiliconFlow, DashScope, local gateways) is either rejected or
 * silently ignored, so gate it on the model name.
 */
export function embeddingSupportsDimensions(model: string): boolean {
  return /^text-embedding-3-/.test(model.trim());
}

/**
 * A vector whose length differs from the configured dimension would corrupt the
 * `vec0` table, which is created with a fixed width. Fail loudly instead.
 */
export function assertEmbeddingDimensions(
  vectors: number[][],
  expected: number,
  model: string,
): void {
  const actual = vectors[0]?.length;
  if (actual === undefined || actual === expected) return;
  throw new Error(
    `Embedding dimension mismatch: model "${model}" returned ${actual}-dim vectors ` +
      `but EMBEDDING_DIMENSIONS is ${expected}.\n` +
      `Set EMBEDDING_DIMENSIONS=${actual} in .env, then rebuild the index from scratch ` +
      `(vectors from different models are not comparable).`,
  );
}
