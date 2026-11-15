import { J, hid, clean } from "../../lib/push.js";
// POST {endpoint, items:{id:price}} … 登録・商品の更新 ／ DELETE {endpoint} … 通知をやめる（記録を消す）
export async function onRequest({ request, env }) {
  if (!env.SUBS) return J({ ok: false, error: "disabled" }, 503);
  if (!["POST", "DELETE"].includes(request.method)) return J({ ok: false }, 405);
  let d;
  try { d = await request.json(); } catch { return J({ ok: false }, 400); }
  const ep = String(d.endpoint || "");
  if (!/^https:\/\/[^\s]{10,800}$/.test(ep)) return J({ ok: false }, 400);
  const k = "s:" + (await hid(ep));
  if (request.method === "DELETE") { await env.SUBS.delete(k); await env.SUBS.delete("m:" + k.slice(2)); return J({ ok: true }); }
  const old = (await env.SUBS.get(k, "json")) || {};
  const items = clean(d.items);
  const n = old.n || {};  // 通知済みの価格（同じ値下がりで何度も送らない）
  await env.SUBS.put(k, JSON.stringify({ e: ep, i: items, n, t: Date.now(), last: old.last || 0 }));
  return J({ ok: true, count: Object.keys(items).length });
}
