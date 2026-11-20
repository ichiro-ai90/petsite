// GET /api/hotels?area=<都道府県名>&q=<追加の言葉> … 楽天トラベルで「ペットと泊まれる」宿をその場で探す（11/21 Taku「旅行系のアフィリエイトもとりたい」）
// - 楽天トラベル キーワード検索API。キーワードに「ペット」を必ず含める（施設名・プラン名・部屋名が対象）
// - 都道府県は固定表で middleClassCode に変換（楽天トラベル地区コード）
// - 結果（料金の目安を含む）はエッジのキャッシュに最大6時間だけ置く。KV・ファイルには保存しない
// - リンクは hotelInformationUrl（affiliateId 付きで楽天アフィリエイトのURLになる。宿泊の料率 1.0%）
// 2026年の新API（openapi.rakuten.co.jp + accessKey）。旧 20170426 は 2027-01-18 廃止
const BASE = "https://openapi.rakuten.co.jp/engine/api/Travel/";
// 都道府県→middleClassCode は固定表（地区コードAPIを毎回呼ぶと 1秒1回の上限で 429 になるため）
const PREF = "北海道:hokkaido 青森:aomori 岩手:iwate 宮城:miyagi 秋田:akita 山形:yamagata 福島:fukushima 茨城:ibaraki 栃木:tochigi 群馬:gunma 埼玉:saitama 千葉:chiba 東京:tokyo 神奈川:kanagawa 新潟:niigata 富山:toyama 石川:ishikawa 福井:fukui 山梨:yamanashi 長野:nagano 岐阜:gifu 静岡:shizuoka 愛知:aichi 三重:mie 滋賀:shiga 京都:kyoto 大阪:osaka 兵庫:hyogo 奈良:nara 和歌山:wakayama 鳥取:tottori 島根:shimane 岡山:okayama 広島:hiroshima 山口:yamaguchi 徳島:tokushima 香川:kagawa 愛媛:ehime 高知:kochi 福岡:fukuoka 佐賀:saga 長崎:nagasaki 熊本:kumamoto 大分:oita 宮崎:miyazaki 鹿児島:kagoshima 沖縄:okinawa"
  .split(" ").reduce((m, x) => { const [k, v] = x.split(":"); m[k] = v; return m; }, {});
const TTL = 6 * 3600;

function J(o, status = 200, extra = {}) {
  return new Response(JSON.stringify(o), { status, headers: { "content-type": "application/json; charset=utf-8", ...extra } });
}

async function call(env, path, params) {
  const p = new URLSearchParams({ applicationId: env.RAKUTEN_APP_ID, format: "json", formatVersion: "2", ...params });
  if (env.RAKUTEN_ACCESS_KEY) p.set("accessKey", env.RAKUTEN_ACCESS_KEY);
  const r = await fetch(BASE + path + "?" + p, { headers: { Origin: env.RAKUTEN_ORIGIN || "https://petnavi.niconote.jp" } });
  if (r.ok) return r.json();
  const t = (await r.text()).slice(0, 200);
  if (r.status === 404 && t.includes("not_found")) return { hotels: [], pagingInfo: { recordCount: 0 } };
  // 原因切り分け用に楽天の応答本文の先頭だけ返す（キー類は伏せる）
  const safe = t.replace(/[A-Za-z0-9_-]{24,}/g, "*").replace(/\s+/g, " ").slice(0, 120);
  throw new Error(r.status + " " + safe);
}

export async function onRequestGet({ request, env, waitUntil }) {
  if (!env.RAKUTEN_APP_ID) return J({ err: "no_key", hotels: [] }, 200);
  const url = new URL(request.url);
  const area = (url.searchParams.get("area") || "").trim().slice(0, 10);
  const q = (url.searchParams.get("q") || "").trim().slice(0, 30);
  const page = String(Math.min(3, Math.max(1, parseInt(url.searchParams.get("page") || "1", 10) || 1)));
  const key = new Request(url.origin + "/api/hotels?area=" + encodeURIComponent(area) + "&q=" + encodeURIComponent(q) + "&page=" + page);
  const cache = caches.default, hit = await cache.match(key);
  if (hit) return hit;
  try {
    const params = { keyword: ("ペット " + q).trim(), hits: "30", page, responseType: "middle", hotelThumbnailSize: "3" };
    if (env.RAKUTEN_AFFILIATE_ID) params.affiliateId = env.RAKUTEN_AFFILIATE_ID;
    if (area) {
      const code = PREF[area] || PREF[area.replace(/[都府県]$/, "")];
      if (!code) return J({ area, err: "area", hotels: [] });
      params.middleClassCode = code;
    }
    const d = await call(env, "KeywordHotelSearch/20260731", params);
    const hotels = (d.hotels || []).map((h) => {
      const arr = Array.isArray(h) ? h : (h.hotel || []);
      const b = (arr.find((x) => x.hotelBasicInfo) || {}).hotelBasicInfo || {};
      return { n: b.hotelName || "", u: b.hotelInformationUrl || "", img: b.hotelImageUrl || b.hotelThumbnailUrl || "",
        min: +b.hotelMinCharge || 0, ra: +b.reviewAverage || 0, rc: +b.reviewCount || 0,
        ad: ((b.address1 || "") + (b.address2 || "")).slice(0, 40), st: b.nearestStation || "", sp: (b.hotelSpecial || "").slice(0, 80) };
    }).filter((x) => x.n && x.u);
    const res = J({ area, q, page: +page, pages: +(d.pagingInfo || {}).pageCount || 1, at: new Date().toISOString(), tot: +(d.pagingInfo || {}).recordCount || hotels.length, hotels }, 200,
      { "cache-control": `public, max-age=${TTL}` });
    if (hotels.length) waitUntil(cache.put(key, res.clone()));
    return res;
  } catch (e) {
    return J({ area, q, err: String(e.message || e).slice(0, 140), hotels: [] }, 200, { "cache-control": "no-store" });
  }
}
