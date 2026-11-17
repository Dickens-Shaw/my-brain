import { readFileSync, writeFileSync } from 'node:fs';

const scanPath = process.argv[2];
const outPath = process.argv[3];
const scan = JSON.parse(readFileSync(scanPath, 'utf-8'));
const importMap = scan.importMap;
const fileSet = new Set(scan.files.map(f => f.path));

const PKG_DIRS = {
  '@my-brain/core': 'packages/core/src/index.ts',
  '@my-brain/config': 'packages/config/src/index.ts',
  '@my-brain/vault': 'packages/vault/src/index.ts',
  '@my-brain/ingest': 'packages/ingest/src/index.ts',
  '@my-brain/retrieval': 'packages/retrieval/src/index.ts',
};

function resolveRelative(dir, rel) {
  const parts = (dir ? dir.split('/').filter(Boolean) : []).concat(rel.split('/').filter(Boolean));
  const stack = [];
  for (const part of parts) {
    if (part === '' || part === '.') continue;
    if (part === '..') { if (stack.length === 0) return ''; stack.pop(); }
    else stack.push(part);
  }
  return stack.join('/');
}

function probeNodeNext(base, set) {
  if (!base) return null;
  if (set.has(base)) return base;
  const m = base.match(/^(.*)\.(js|jsx|mjs|cjs)$/);
  if (m) {
    for (const ext of ['.ts', '.tsx']) {
      const c = m[1] + ext;
      if (set.has(c)) return c;
    }
  }
  return null;
}

let added = 0;
for (const [path, targets] of Object.entries(importMap)) {
  if (!Array.isArray(targets)) continue;
  if (!/\.(ts|tsx|js|jsx|mjs)$/.test(path)) continue;
  let content;
  try { content = readFileSync('/Users/worker/Desktop/github/my-brain/' + path, 'utf-8'); }
  catch { continue; }
  const dir = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
  const re = /(?:import|export)\s+(?:type\s+)?(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]/g;
  const found = new Set(targets);
  let m;
  while ((m = re.exec(content)) !== null) {
    const src = m[1];
    if (src.startsWith('@my-brain/')) {
      const pkg = PKG_DIRS[src];
      if (pkg && fileSet.has(pkg)) found.add(pkg);
      continue;
    }
    if (src.startsWith('./') || src.startsWith('../')) {
      const base = resolveRelative(dir, src);
      const resolved = probeNodeNext(base, fileSet);
      if (resolved && resolved !== path) found.add(resolved);
    }
  }
  added += found.size - targets.length;
  importMap[path] = [...found].sort((a, b) => a.localeCompare(b));
}

scan.importMap = importMap;
writeFileSync(outPath, JSON.stringify(scan, null, 2) + '\n');
console.log(`supplemented +${added} imports`);
