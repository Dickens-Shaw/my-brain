function cosine(a: number[], b: number[]): number {
  let dot = 0;
  let na = 0;
  let nb = 0;
  for (let i = 0; i < a.length; i++) {
    const x = a[i]!;
    const y = b[i]!;
    dot += x * y;
    na += x * x;
    nb += y * y;
  }
  if (na === 0 || nb === 0) return 0;
  return dot / (Math.sqrt(na) * Math.sqrt(nb));
}

function addInPlace(acc: number[], v: number[]): void {
  for (let i = 0; i < acc.length; i++) acc[i]! += v[i]!;
}

function scaleInPlace(v: number[], s: number): void {
  for (let i = 0; i < v.length; i++) v[i]! *= s;
}

function zeros(n: number): number[] {
  return Array.from({ length: n }, () => 0);
}

/** Spherical k-means using cosine similarity. */
export function kmeansCosine(
  vectors: number[][],
  k: number,
  maxIter = 40,
): { labels: number[]; centroids: number[][] } {
  if (vectors.length === 0) return { labels: [], centroids: [] };
  const dim = vectors[0]!.length;
  const kk = Math.min(k, vectors.length);

  // Init: even spread through the list (deterministic)
  const centroids: number[][] = [];
  for (let i = 0; i < kk; i++) {
    const idx = Math.floor((i * vectors.length) / kk);
    centroids.push([...vectors[idx]!]);
  }

  let labels = new Array(vectors.length).fill(0) as number[];

  for (let iter = 0; iter < maxIter; iter++) {
    let changed = 0;
    for (let i = 0; i < vectors.length; i++) {
      let best = 0;
      let bestSim = -Infinity;
      for (let c = 0; c < kk; c++) {
        const sim = cosine(vectors[i]!, centroids[c]!);
        if (sim > bestSim) {
          bestSim = sim;
          best = c;
        }
      }
      if (labels[i] !== best) {
        labels[i] = best;
        changed += 1;
      }
    }

    const sums = Array.from({ length: kk }, () => zeros(dim));
    const counts = new Array(kk).fill(0) as number[];
    for (let i = 0; i < vectors.length; i++) {
      const c = labels[i]!;
      addInPlace(sums[c]!, vectors[i]!);
      counts[c]! += 1;
    }
    for (let c = 0; c < kk; c++) {
      if (counts[c]! === 0) continue;
      scaleInPlace(sums[c]!, 1 / counts[c]!);
      centroids[c] = sums[c]!;
    }

    if (changed === 0) break;
  }

  return { labels, centroids };
}

export function nearestCentroid(
  vector: number[],
  centroids: number[][],
): { index: number; score: number; second: number; secondScore: number } {
  let index = 0;
  let score = -Infinity;
  let second = 0;
  let secondScore = -Infinity;
  for (let i = 0; i < centroids.length; i++) {
    const s = cosine(vector, centroids[i]!);
    if (s > score) {
      second = index;
      secondScore = score;
      index = i;
      score = s;
    } else if (s > secondScore) {
      second = i;
      secondScore = s;
    }
  }
  return { index, score, second, secondScore };
}

export { cosine };
