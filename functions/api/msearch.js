// GET /api/msearch?q=<言葉> … 楽天市場とYahoo!ショッピングのペット用品を、その場で検索して安い順に返す（11/17 Taku「あらゆるモールのペット用品を集約」）
// - 事前に集めた約1,300商品（search.json）に無い商品も探せるようにする
// - 結果はエッジのキャッシュに最大6時間だけ置く（楽天の規約：価格・販売可能情報の保存は24時間まで）。KV・ファイルには保存しない
// - 楽天ジャンル 101213（ペット・ペットグッズ）、Yahoo カテゴリ 2509（ペット用品、生き物）に限定
const R_URL = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260401";
const Y_URL = "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch";
const TTL = 6 * 3600;

function J(o, status = 200, extra = {}) {
  return new Response(JSON.stringify(o), { status, headers: { "content-type": "application/json; charset=utf-8", ...extra } });
}

async function rakuten(env, q) {
  if (!env.RAKUTEN_APP_ID || !env.RAKUTEN_ACCESS_KEY) return [];
  const p = new URLSearchParams({ applicationId: env.RAKUTEN_APP_ID, accessKey: env.RAKUTEN_ACCESS_KEY, format: "json", formatVersion: "2",
    keyword: q, genreId: "101213", hits: "30", availability: "1" });
  if (env.RAKUTEN_AFFILIATE_ID) p.set("affiliateId", env.RAKUTEN_AFFILIATE_ID);
  const r = await fetch(R_URL + "?" + p, { headers: { Origin: env.RAKUTEN_ORIGIN || "https://petnavi.niconote.jp" } });
  if (!r.ok) throw new Error("rakuten " + r.status);
  const d = await r.json();
  return (d.Items || []).map((it) => {
    const price = +it.itemPrice || 0, rate = +it.pointRate || 1, img = (it.mediumImageUrls || [""])[0];
    return { m: "r", n: it.itemName || "", s: it.shopName || "", p: price, pt: Math.floor(Math.floor(price * 10 / 11) * rate / 100), r: rate,
      sh: it.postageFlag === 0 ? 1 : 0, u: it.affiliateUrl || it.itemUrl || "", img: typeof img === "string" ? img : (img && img.imageUrl) || "",
      rv: +it.reviewCount || 0, ra: +it.reviewAverage || 0 };
  }).filter((x) => x.p > 0 && x.u);
}

async function yahoo(env, q) {
  if (!env.YAHOO_APP_ID) return [];
  const p = new URLSearchParams({ appid: env.YAHOO_APP_ID, query: q, genre_category_id: "2509", results: "30", in_stock: "true" });
  if (env.VC_AFFILIATE_ID) { p.set("affiliate_type", "vc"); p.set("affiliate_id", env.VC_AFFILIATE_ID); }
  const r = await fetch(Y_URL + "?" + p);
  if (!r.ok) throw new Error("yahoo " + r.status);
  const d = await r.json();
  return (d.hits || []).map((h) => {
    const code = (h.shipping || {}).code;
    return { m: "y", n: h.name || "", s: (h.seller || {}).name || "", p: +h.price || 0, pt: +((h.point || {}).amount) || 0, r: +((h.point || {}).times) || 1,
      sh: code === 2 ? 1 : 0, u: h.url || "", img: (h.image || {}).medium || "", rv: +((h.review || {}).count) || 0, ra: +((h.review || {}).rate) || 0,
      jan: h.janCode || "" };
  }).filter((x) => x.p > 0 && x.u && x.s);
}

export async function onRequestGet({ request, env, waitUntil }) {
  const url = new URL(request.url);
  const q = (url.searchParams.get("q") || "").normalize("NFKC").replace(/\s+/g, " ").trim().slice(0, 60);
  if (q.length < 2) return J({ items: [] }, 400);
  const key = new Request(url.origin + "/api/msearch?q=" + encodeURIComponent(q.toLowerCase()));
  const cache = caches.default;
  const hit = await cache.match(key);
  if (hit) return hit;
  const [a, b] = await Promise.allSettled([rakuten(env, q), yahoo(env, q)]);
  const items = [...(a.status === "fulfilled" ? a.value : []), ...(b.status === "fulfilled" ? b.value : [])].sort((x, y) => x.p - y.p);
  const ok = a.status === "fulfilled" || b.status === "fulfilled";
  const res = J({ q, at: new Date().toISOString(), items, err: [a, b].filter((x) => x.status === "rejected").map((x) => String(x.reason).slice(0, 60)) },
    ok ? 200 : 503, ok ? { "cache-control": `public, max-age=${TTL}` } : {});
  if (ok && items.length) waitUntil(cache.put(key, res.clone()));
  return res;
}
