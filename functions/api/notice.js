import { J } from "../../lib/push.js";
// GET ?h=<宛先のハッシュ> … sw.js が通知の文面を取りに来る
export async function onRequestGet({ request, env }) {
  if (!env.SUBS) return J({}, 503);
  const h = new URL(request.url).searchParams.get("h") || "";
  if (!/^[0-9a-f]{32}$/.test(h)) return J({}, 400);
  return J((await env.SUBS.get("m:" + h, "json")) || { title: "ペット用品 最安ナビ", body: "見た商品の価格が変わりました", url: "/favorites.html" });
}
