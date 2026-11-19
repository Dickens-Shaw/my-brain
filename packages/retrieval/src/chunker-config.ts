/**
 * Chunker configuration, versioned.
 *
 * Changing any of these changes the chunks, which changes the vectors. The
 * index stores this version so a rebuild can be required when it changes
 * instead of mixing incompatible vectors in one database.
 */
export const CHUNKER_VERSION = 2;

/** Current chunker parameters, recorded in the index metadata table. */
export const CHUNKER_PARAMS = {
  maxTokens: 1200,
  minTokens: 100,
  overlapTokens: 200,
  /** Treat a line that is entirely bold as a heading. */
  boldAsHeading: false,
  /** Drop leading blockquote lines that cite the source. */
  stripSourceQuote: false,
} as const;

/** Signature of the chunker, used to detect a change that needs a rebuild. */
export function chunkerSignature(): string {
  return [
    `v${CHUNKER_VERSION}`,
    `max=${CHUNKER_PARAMS.maxTokens}`,
    `min=${CHUNKER_PARAMS.minTokens}`,
    `overlap=${CHUNKER_PARAMS.overlapTokens}`,
    `bold=${CHUNKER_PARAMS.boldAsHeading ? 1 : 0}`,
    `quote=${CHUNKER_PARAMS.stripSourceQuote ? 1 : 0}`,
  ].join(",");
}
