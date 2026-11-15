// Web プッシュの共通処理（Cloudflare Pages Functions）。
// 保存するのは「通知の宛先（endpoint）」と「商品ID・覚えた価格」だけ。氏名・メール等は持たない。
// 通知は本文なし（暗号化不要）で送り、端末の sw.js が /api/notice から文面を取りに来る。
export const J = (o, s = 200) => new Response(JSON.stringify(o), { status: s, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } });
export async function hid(endpoint) {
  const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
  return [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("").slice(0, 32);
}
const b64u = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const b64uStr = (s) => b64u(new TextEncoder().encode(s));
export async function vapidHeaders(endpoint, env) {
  const jwk = JSON.parse(env.VAPID_PRIVATE);
  const key = await crypto.subtle.importKey("jwk", jwk, { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
  const aud = new URL(endpoint).origin;
  const h = b64uStr(JSON.stringify({ typ: "JWT", alg: "ES256" }));
  const p = b64uStr(JSON.stringify({ aud, exp: Math.floor(Date.now() / 1000) + 12 * 3600, sub: env.VAPID_SUBJECT || "https://petnavi.niconote.jp/" }));
  const sig = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, key, new TextEncoder().encode(h + "." + p));
  return { Authorization: `vapid t=${h}.${p}.${b64u(sig)}, k=${env.VAPID_PUBLIC}`, TTL: "86400", Urgency: "normal", "Content-Length": "0" };
}
export function clean(items) {
  const out = {};
  if (!items || typeof items !== "object") return out;
  for (const [id, p] of Object.entries(items).slice(0, 80)) {
    if (/^[0-9a-f]{16,40}$/.test(id) && Number.isFinite(+p) && +p > 0 && +p < 10000000) out[id] = Math.round(+p);
  }
  return out;
}
