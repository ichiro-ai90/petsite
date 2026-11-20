// GET /api/hotels?area=<都道府県名>&q=<追加の言葉> … 楽天トラベルで「ペットと泊まれる」宿をその場で探す（11/21 Taku「旅行系のアフィリエイトもとりたい」）
// - 楽天トラベル キーワード検索API。キーワードに「ペット」を必ず含める（施設名・プラン名・部屋名が対象）
// - 都道府県は 地区コードAPI（GetAreaClass）で名前→middleClassCode に変換（7日キャッシュ。地名の一覧で価格は含まない）
// - 結果（料金の目安を含む）はエッジのキャッシュに最大6時間だけ置く。KV・ファイルには保存しない
// - リンクは hotelInformationUrl（affiliateId 付きで楽天アフィリエイトのURLになる。宿泊の料率 1.0%）
const HOSTS = ["https://app.rakuten.co.jp/services/api/Travel/", "https://openapi.rakuten.co.jp/engine/api/Travel/"];
const TTL = 6 * 3600;

function J(o, status = 200, extra = {}) {
  return new Response(JSON.stringify(o), { status, headers: { "content-type": "application/json; charset=utf-8", ...extra } });
}

async function call(env, path, params) {
  const p = new URLSearchParams({ applicationId: env.RAKUTEN_APP_ID, format: "json", formatVersion: "2", ...params });
  if (env.RAKUTEN_ACCESS_KEY) p.set("accessKey", env.RAKUTEN_ACCESS_KEY);
  let last = "";
  for (const h of HOSTS) {
    const r = await fetch(h + path + "?" + p, { headers: { Origin: env.RAKUTEN_ORIGIN || "https://petnavi.niconote.jp" } });
    if (r.ok) return r.json();
    if (r.status === 404 && (await r.text()).includes("not_found")) return { hotels: [], pagingInfo: { recordCount: 0 } };
    last = h.split("/")[2] + " " + r.status;
  }
  throw new Error(last);
}

async function areas(env, waitUntil) {
  const cache = caches.default, key = new Request("https://petnavi.local/api/hotels/areas");
  const hit = await cache.match(key);
  if (hit) return hit.json();
  const d = await call(env, "GetAreaClass/20131024", {});
  const m = {};
  const L = (((d.areaClasses || {}).largeClasses || [])[0] || []);
  const mids = (Array.isArray(L) ? L : [L]).flatMap((x) => (x.middleClasses || x[1]?.middleClasses || []));
  for (const mc of mids) {
    const v = mc.middleClass ? (Array.isArray(mc.middleClass) ? mc.middleClass[0] : mc.middleClass) : mc;
    if (v && v.middleClassName && v.middleClassCode) m[v.middleClassName] = v.middleClassCode;
  }
  waitUntil(cache.put(key, new Response(JSON.stringify(m), { headers: { "cache-control": "public, max-age=604800" } })));
  return m;
}

export async function onRequestGet({ request, env, waitUntil }) {
  if (!env.RAKUTEN_APP_ID) return J({ err: "no_key", hotels: [] }, 200);
  const url = new URL(request.url);
  const area = (url.searchParams.get("area") || "").trim().slice(0, 10);
  const q = (url.searchParams.get("q") || "").trim().slice(0, 30);
  const key = new Request(url.origin + "/api/hotels?area=" + encodeURIComponent(area) + "&q=" + encodeURIComponent(q));
  const cache = caches.default, hit = await cache.match(key);
  if (hit) return hit;
  try {
    const params = { keyword: ("ペット " + q).trim(), hits: "30", responseType: "middle", hotelThumbnailSize: "3" };
    if (env.RAKUTEN_AFFILIATE_ID) params.affiliateId = env.RAKUTEN_AFFILIATE_ID;
    if (area) {
      const m = await areas(env, waitUntil);
      const code = m[area] || m[area.replace(/[都道府県]$/, "")];
      if (!code) return J({ area, err: "area", hotels: [] });
      params.middleClassCode = code;
    }
    const d = await call(env, "KeywordHotelSearch/20170426", params);
    const hotels = (d.hotels || []).map((h) => {
      const arr = Array.isArray(h) ? h : (h.hotel || []);
      const b = (arr.find((x) => x.hotelBasicInfo) || {}).hotelBasicInfo || {};
      return { n: b.hotelName || "", u: b.hotelInformationUrl || "", img: b.hotelImageUrl || b.hotelThumbnailUrl || "",
        min: +b.hotelMinCharge || 0, ra: +b.reviewAverage || 0, rc: +b.reviewCount || 0,
        ad: ((b.address1 || "") + (b.address2 || "")).slice(0, 40), st: b.nearestStation || "", sp: (b.hotelSpecial || "").slice(0, 80) };
    }).filter((x) => x.n && x.u);
    const res = J({ area, q, at: new Date().toISOString(), tot: +(d.pagingInfo || {}).recordCount || hotels.length, hotels }, 200,
      { "cache-control": `public, max-age=${TTL}` });
    if (hotels.length) waitUntil(cache.put(key, res.clone()));
    return res;
  } catch (e) {
    return J({ area, q, err: String(e.message || e).slice(0, 80), hotels: [] }, 200, { "cache-control": "no-store" });
  }
}
