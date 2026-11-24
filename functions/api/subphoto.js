// GET /api/subphoto?id=<12桁> … 情報提供の写真（掲載の前に一郎が取り込むため。idは推測できない長さ。60日で消える）
export async function onRequestGet({ request, env }) {
  const id = new URL(request.url).searchParams.get("id") || "";
  if (!env.SUBS || !/^[0-9a-f]{12}$/.test(id)) return new Response("not found", { status: 404 });
  const b = await env.SUBS.get("ph:" + id);
  if (!b) return new Response("not found", { status: 404 });
  const bin = Uint8Array.from(atob(b), c => c.charCodeAt(0));
  return new Response(bin, { headers: { "content-type": "image/jpeg", "cache-control": "private, no-store", "x-robots-tag": "noindex" } });
}
