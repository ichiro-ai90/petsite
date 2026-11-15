import { J, vapidHeaders } from "../../lib/push.js";
// POST（毎朝の更新処理から、その回だけ有効な SEND_TOKEN 付きで呼ぶ）
// 覚えた価格より今の取得価格が安い商品があれば、1宛先につき1日1回まで通知する
export async function onRequestPost({ request, env }) {
  if (!env.SUBS || !env.SEND_TOKEN || !env.VAPID_PRIVATE) return J({ ok: false, error: "disabled" }, 503);
  if (request.headers.get("x-send-token") !== env.SEND_TOKEN) return J({ ok: false }, 403);
  const data = await (await env.ASSETS.fetch(new URL("/search.json", request.url))).json();
  const by = {}; for (const x of data.items) by[x.id] = x;
  let cursor, sent = 0, gone = 0, checked = 0;
  const now = Date.now();
  do {
    const r = await env.SUBS.list({ prefix: "s:", cursor, limit: 1000 });
    for (const k of r.keys) {
      const s = await env.SUBS.get(k.name, "json"); if (!s) continue; checked++;
      if (now - (s.last || 0) < 20 * 3600e3) continue;
      let cnt = 0, sum = 0, top = null;
      for (const [id, p] of Object.entries(s.i || {})) {
        const x = by[id]; if (!x) continue;
        const base = Math.min(p, s.n[id] || Infinity);
        if (x.p < base) { cnt++; sum += base - x.p; if (!top || base - x.p > top.d) top = { d: base - x.p, n: x.n, id }; }
      }
      if (!cnt) continue;
      const msg = { title: `見た商品 ${cnt}件が値下がり（計 −${sum.toLocaleString("ja-JP")}円）`,
        body: `${top.n.slice(0, 40)} が −${top.d.toLocaleString("ja-JP")}円。当サイトが取得したショップの最安・送料別で比較（${String(data.fetched_at).slice(0, 10)}）`,
        url: cnt === 1 ? `/item-${top.id}.html` : "/favorites.html" };
      const res = await fetch(s.e, { method: "POST", headers: await vapidHeaders(s.e, env) });
      if (res.status === 404 || res.status === 410) { await env.SUBS.delete(k.name); gone++; continue; }
      if (res.ok) {
        for (const id of Object.keys(s.i)) if (by[id] && by[id].p < Math.min(s.i[id], s.n[id] || Infinity)) s.n[id] = by[id].p;
        s.last = now; await env.SUBS.put(k.name, JSON.stringify(s));
        await env.SUBS.put("m:" + k.name.slice(2), JSON.stringify(msg), { expirationTtl: 7 * 86400 });
        sent++;
      }
    }
    cursor = r.list_complete ? null : r.cursor;
  } while (cursor);
  return J({ ok: true, checked, sent, gone });
}
