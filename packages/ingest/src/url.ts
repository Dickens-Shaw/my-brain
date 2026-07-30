/**
 * Normalize source URLs for cross-source dedupe.
 * - Juejin: article id
 * - WeChat: sn param (or mid+idx fallback)
 * - Other: lowercase, strip fragment, strip trailing slash
 */
export function normalizeSourceUrl(raw: string | undefined | null): string | null {
  if (!raw) return null;
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    return raw.trim().toLowerCase();
  }

  const host = url.hostname.toLowerCase().replace(/^www\./, "");

  // Juejin post
  const juejin = host.match(/^(juejin\.(cn|im)|juejin\.cn)$/);
  if (juejin || host.endsWith("juejin.cn") || host.endsWith("juejin.im")) {
    const m = url.pathname.match(/\/post\/(\d+)/);
    if (m) return `juejin:post:${m[1]}`;
  }

  // WeChat
  if (
    host.includes("mp.weixin.qq.com") ||
    host.includes("weixin.qq.com")
  ) {
    const sn = url.searchParams.get("sn");
    if (sn) return `wechat:sn:${sn.toLowerCase()}`;
    const mid = url.searchParams.get("mid") || url.searchParams.get("appmsgid");
    const idx = url.searchParams.get("idx") || url.searchParams.get("itemidx") || "1";
    if (mid) return `wechat:mid:${mid}:${idx}`;
  }

  url.hash = "";
  // Drop common tracking params
  for (const key of [...url.searchParams.keys()]) {
    if (
      /^(utm_|spm|from|scene|mpshare|srcid|sharer_|chksm|exportkey|wx_header)/i.test(
        key,
      )
    ) {
      url.searchParams.delete(key);
    }
  }
  let out = `${url.protocol}//${host}${url.pathname}${url.search}`.toLowerCase();
  out = out.replace(/\/+$/, "");
  return out;
}
