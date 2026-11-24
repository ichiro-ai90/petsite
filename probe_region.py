"""地方ごとに方法Aを回す：OSMの公式サイト付き飲食店を集め、公式サイトのペット記載を確かめ、
開けない/記載ありはブラウザで再確認し、住所（国土地理院）とタグを付ける。REGION=hokkaido など。結果は shots/{REGION}.json"""
import os, json, re, time, asyncio, urllib.request, urllib.parse, unicodedata, concurrent.futures as cf
exec(open("probe_kanto.py", encoding="utf-8").read().split("els = overpass()")[0])
REG = {"hokkaido": [1], "tohoku": range(2, 8), "kanto": range(8, 15), "chubu": range(15, 24), "kinki": range(24, 31),
       "chugoku": range(31, 36), "shikoku": range(36, 40), "kyushu": range(40, 48)}
R = os.environ["REGION"]
def q(code):
    return f"""[out:json][timeout:600];
area["ISO3166-2"="JP-{code:02d}"]->.a;
(
 nwr(area.a)["amenity"~"^(cafe|restaurant|fast_food|bar|pub|ice_cream|biergarten|food_court)$"]["website"];
 nwr(area.a)["amenity"~"^(cafe|restaurant|fast_food|bar|pub|ice_cream)$"]["contact:website"];
 nwr(area.a)["shop"~"^(bakery|pastry|confectionery|deli|coffee|tea)$"]["website"];
);
out center tags;"""
def fetch(code):
    for i in range(5):
        for ep in ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter", "https://overpass.private.coffee/api/interpreter"]:
            try:
                r = urllib.request.urlopen(urllib.request.Request(ep, data=urllib.parse.urlencode({"data": q(code)}).encode(), headers={"User-Agent": "petnavi-probe/1.0 (https://petnavi.niconote.jp)"}), timeout=700)
                e = json.load(r)["elements"]; print("pref", code, ep, len(e), flush=True); return e
            except Exception as ex:
                print("ng", code, ep, ex, flush=True); time.sleep(20)
    raise SystemExit(f"overpass failed for {code}")
els = []
for c in REG[R]:
    els += fetch(c); time.sleep(5)
seen = set(); todo = []; TAGS = {}
for e in els:
    t = e.get("tags", {}); u = (t.get("website") or t.get("contact:website") or "").strip()
    k = f'{e["type"]}/{e["id"]}'
    if not u or u in seen or k in TAGS: continue
    seen.add(u); todo.append(e)
    TAGS[k] = {kk: v for kk, v in t.items() if kk.startswith(("contact:", "instagram", "facebook", "twitter", "addr:", "cuisine", "opening_hours", "dog", "name", "brand", "operator", "website"))}
print("candidates", len(els), "unique", len(todo), flush=True)
out = []
with cf.ThreadPoolExecutor(24) as ex:
    for i, r in enumerate(ex.map(check, todo)):
        out.append(r)
        if i % 500 == 0: print(i, flush=True)
# ブラウザで再確認（記載あり or 開けなかったもの）
from playwright.async_api import async_playwright
NEG2 = re.compile(r"(ペット|犬|動物|わんちゃん|ワンちゃん)[^。\n]{0,20}(不可|お断り|ご遠慮|控え|禁止|ng|できません|入れません|入店不可|持ち込み不可|お控え)", re.I)
BAD = re.compile(r"(?i)hot\s?dog|ホットドッグ|ジャーマンドッ|チリドッグ|ドッグカフェとは|猫カフェ|ねこカフェ|保護猫")
def judge2(t):
    hits = []
    for m in POS.finditer(t):
        a, b = max(0, m.start() - 40), min(len(t), m.end() + 60); ctx = t[a:b]
        if NEG2.search(ctx) or SVC.search(ctx) or BAD.search(t[max(0, m.start() - 12):m.end() + 4]): continue
        hits.append(ctx)
    neg = [t[max(0, m.start() - 30):m.end() + 30] for m in NEG2.finditer(t) if not SVC.search(t[max(0, m.start() - 30):m.end() + 30])]
    return hits, neg
async def recheck():
    async with async_playwright() as p:
        b = await p.chromium.launch(); ctx = await b.new_context(user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36", locale="ja-JP", ignore_https_errors=True)
        sem = asyncio.Semaphore(8)
        async def one(r):
            if not (r.get("hits") or (r.get("err") and r["err"].split(":")[0] in ("HTTPError", "URLError", "TimeoutError", "UnicodeEncodeError", "ConnectionResetError", "RemoteDisconnected", "IncompleteRead"))): return
            async with sem:
                pg = await ctx.new_page()
                try:
                    await pg.goto(r.get("page") or r["url"], wait_until="domcontentloaded", timeout=25000); await pg.wait_for_timeout(1500)
                    t = unicodedata.normalize("NFKC", re.sub(r"\s+", " ", await pg.inner_text("body")))
                    h, n = judge2(t)
                    r.update(hits=h[:4], neg=n[:3], title=(await pg.title())[:120], final=pg.url, rechecked=True); r.pop("err", None)
                except Exception as e:
                    r["err2"] = type(e).__name__
                finally:
                    await pg.close()
        await asyncio.gather(*[one(r) for r in out])
        await b.close()
asyncio.run(recheck())
def geo(r):
    for i in range(3):
        try:
            u = f'https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress?lat={r["lat"]}&lon={r["lon"]}'
            d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "petnavi-probe/1.0"}), timeout=15))
            return d.get("results", {}).get("muniCd"), d.get("results", {}).get("lv01Nm")
        except Exception:
            time.sleep(2)
    return None, None
with cf.ThreadPoolExecutor(6) as ex:
    for r, g in zip(out, ex.map(geo, out)):
        r["geo"] = list(g); r["tags"] = TAGS.get(r["id"], {})
os.makedirs("shots", exist_ok=True)
json.dump(out, open(f"shots/{R}.json", "w"), ensure_ascii=False)
print("done", R, len(out), "hits", sum(1 for r in out if r.get("hits") and not r.get("neg")), "neg", sum(1 for r in out if r.get("neg")),
      "err", sum(1 for r in out if r.get("err") or r.get("err2")))
