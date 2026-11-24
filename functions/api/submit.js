// POST /api/submit … 「ペットOKのお店・スポットを教えて」フォームの受け口（11/24 Taku「サイト内に自作。登録をトリガーに一郎が作る」）
// - 受け取った内容を GitHub の issue（ラベル shop-submit）にする。写真は submissions ブランチに保存してリンクする
// - それを合図に .github/workflows/submit-intake.yml が公式サイトを確かめて、掲載のPRを作る
// - 個人情報（メール・氏名・電話）は受け取らない。IPは保存せず、連投よけのハッシュだけ1日で消える形でKVに置く
// - 必要な Secret：SUBMIT_GH_TOKEN（petsite の Issues・Contents だけ書ける細かいトークン）。任意：TURNSTILE_SECRET
const REPO = "ichiro-ai90/petsite";
function J(o, s = 200) { return new Response(JSON.stringify(o), { status: s, headers: { "content-type": "application/json; charset=utf-8" } }); }
const T = (v, n) => String(v == null ? "" : v).replace(/[\u0000-\u001f<>]/g, " ").trim().slice(0, n);
async function sha(s) { const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s)); return [...new Uint8Array(b)].map(x => x.toString(16).padStart(2, "0")).join("").slice(0, 24); }
async function gh(env, path, method, body) {
  const r = await fetch("https://api.github.com/repos/" + REPO + path, { method, headers: { authorization: "Bearer " + env.SUBMIT_GH_TOKEN, accept: "application/vnd.github+json", "user-agent": "petnavi-submit", "content-type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) throw new Error("github " + r.status);
  return r.json();
}
const PREFS = "北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 埼玉県 千葉県 東京都 神奈川県 新潟県 富山県 石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県 三重県 滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県 鳥取県 島根県 岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県".split(" ");
export async function onRequestPost({ request, env }) {
  if (!env.SUBMIT_GH_TOKEN) return J({ ok: false, error: "disabled" }, 503);
  let d; try { d = await request.json(); } catch { return J({ ok: false, error: "bad" }, 400); }
  if (d.hp) return J({ ok: true });  // 迷惑投稿よけの見えない欄に入力があれば、受け付けたふりをして捨てる
  const ip = request.headers.get("cf-connecting-ip") || "";
  if (env.TURNSTILE_SECRET) {
    const f = new FormData(); f.append("secret", env.TURNSTILE_SECRET); f.append("response", T(d.ts, 2048)); f.append("remoteip", ip);
    const v = await (await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", { method: "POST", body: f })).json();
    if (!v.success) return J({ ok: false, error: "robot" }, 400);
  }
  if (env.SUBS) {  // 1日あたり同じ回線から5件まで
    const k = "rl:" + (await sha(ip + new Date().toISOString().slice(0, 10)));
    const n = +(await env.SUBS.get(k) || 0);
    if (n >= 5) return J({ ok: false, error: "limit" }, 429);
    await env.SUBS.put(k, String(n + 1), { expirationTtl: 86400 });
  }
  const o = { kind: d.kind === "spot" ? "spot" : "shop", name: T(d.name, 80), pref: PREFS.includes(d.pref) ? d.pref : "", where: T(d.where, 60),
    url: T(d.url, 300), ig: T(d.ig, 80).replace(/^@/, "").replace(/^https?:\/\/(www\.)?instagram\.com\//, "").replace(/[/?].*$/, ""),
    dog: ["店内OK", "テラス席OK", "屋外OK", "わからない"].includes(d.dog) ? d.dog : "わからない", big: ["可", "不可", "わからない"].includes(d.big) ? d.big : "わからない",
    note: T(d.note, 400), rel: ["お店・施設の人", "利用した人"].includes(d.rel) ? d.rel : "利用した人", photo_ok: !!d.photo_ok, at: new Date().toISOString() };
  if (!o.name || !o.pref || (!/^https?:\/\/[^\s]{4,}$/.test(o.url) && !/^[A-Za-z0-9._]{1,30}$/.test(o.ig))) return J({ ok: false, error: "required" }, 400);
  const id = (await sha(o.name + o.pref + o.at)).slice(0, 12);
  let photo = "";
  if (d.photo && o.photo_ok) {
    const m = /^data:image\/jpeg;base64,([A-Za-z0-9+/=]+)$/.exec(String(d.photo));
    if (!m || m[1].length > 1400000) return J({ ok: false, error: "photo" }, 400);
    try { await gh(env, "/contents/sub/" + id + ".jpg", "PUT", { message: "submission photo " + id + " [skip ci]", content: m[1], branch: "submissions" }); photo = "sub/" + id + ".jpg"; }
    catch (e) { photo = ""; }
  }
  o.photo = photo; o.id = id;
  const body = "フォームからの情報提供です（自動登録）。一郎が公式サイトを確認して、掲載のPRを作ります。\n\n```json\n" + JSON.stringify(o, null, 1) + "\n```";
  try { await gh(env, "/issues", "POST", { title: "情報提供：" + o.name + "（" + o.pref + "）", body, labels: ["shop-submit"] }); }
  catch (e) { return J({ ok: false, error: "save" }, 502); }
  return J({ ok: true, id });
}
