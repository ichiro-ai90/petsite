import { J, vapidHeaders } from "../../lib/push.js";
// POST（毎朝の更新処理から、その回だけ有効な SEND_TOKEN 付きで呼ぶ）
// 登録された商品のうち、今の最安が楽天の平均価格より安いものがあれば、1宛先につき1日1回まで通知する
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
      const n = s.n || {};  // 通知した日時（同じ商品は7日に1回まで）。価格は保存しない（楽天の規約：価格情報は24時間まで）
      for (const id of Object.keys(s.i || {})) {
        const x = by[id]; if (!x || !(x.dl > 0)) continue;  // 平均価格（今の取得値）より安い
        if (now - (n[id] || 0) < 7 * 86400e3) continue;
        cnt++; sum += x.dl; if (!top || x.dl > top.d) top = { d: x.dl, n: x.n, id };
      }
      if (!cnt) continue;
      const msg = { title: `見た商品 ${cnt}件が平均より安い（計 −${sum.toLocaleString("ja-JP")}円）`,
        body: `${top.n.slice(0, 40)} が楽天の平均価格より −${top.d.toLocaleString("ja-JP")}円（${String(data.fetched_at).slice(0, 16)} 取得・送料別）`,
        url: cnt === 1 ? `/item-${top.id}.html` : "/favorites.html" };
      const res = await fetch(s.e, { method: "POST", headers: await vapidHeaders(s.e, env) });
      if (res.status === 404 || res.status === 410) { await env.SUBS.delete(k.name); gone++; continue; }
      if (res.ok) {
        for (const id of Object.keys(s.i)) if (by[id] && by[id].dl > 0) n[id] = now;
        s.n = n; s.last = now; await env.SUBS.put(k.name, JSON.stringify(s));
        await env.SUBS.put("m:" + k.name.slice(2), JSON.stringify(msg), { expirationTtl: 7 * 86400 });
        sent++;
      }
    }
    cursor = r.list_complete ? null : r.cursor;
  } while (cursor);
  return J({ ok: true, checked, sent, gone });
}
