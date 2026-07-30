/** Reciprocal Rank Fusion. */
export function rrfFuse(
  lists: { chunkId: string; score: number }[][],
  k = 60,
): { chunkId: string; score: number; ranks: (number | null)[] }[] {
  const scores = new Map<string, { score: number; ranks: (number | null)[] }>();
  const n = lists.length;

  for (let li = 0; li < n; li++) {
    const list = lists[li]!;
    for (let rank = 0; rank < list.length; rank++) {
      const id = list[rank]!.chunkId;
      const cur = scores.get(id) ?? {
        score: 0,
        ranks: Array.from({ length: n }, () => null),
      };
      cur.score += 1 / (k + rank + 1);
      cur.ranks[li] = rank + 1;
      scores.set(id, cur);
    }
  }

  return [...scores.entries()]
    .map(([chunkId, v]) => ({ chunkId, score: v.score, ranks: v.ranks }))
    .sort((a, b) => b.score - a.score);
}
