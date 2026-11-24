"""方法Aの2回目：1回目で拾えた店の判定をやり直し、開けなかった店はブラウザで開き直す。住所（都道府県・市区町村）は国土地理院の逆ジオコーダで付ける"""
import json, re, asyncio, urllib.request, unicodedata
from playwright.async_api import async_playwright
exec(open("probe_kanto.py", encoding="utf-8").read().split("els = overpass()")[0].split("def overpass")[0])
POS2 = POS
NEG2 = re.compile(r"(ペット|犬|動物|わんちゃん|ワンちゃん)[^。\n]{0,20}(不可|お断り|ご遠慮|控え|禁止|ng|できません|入れません|入店不可|持ち込み不可|お控え)", re.I)
BAD = re.compile(r"(?i)hot\s?dog|ホットドッグ|ジャーマンドッ|チリドッグ|ドッグカフェとは|猫カフェ|ねこカフェ|保護猫")
def judge2(t):
    hits = []
    for m in POS2.finditer(t):
        a, b = max(0, m.start() - 40), min(len(t), m.end() + 60); ctx = t[a:b]
        if NEG2.search(ctx) or SVC.search(ctx) or BAD.search(t[max(0, m.start() - 12):m.end() + 4]): continue
        hits.append(ctx)
    neg = [t[max(0, m.start() - 30):m.end() + 30] for m in NEG2.finditer(t) if not SVC.search(t[max(0, m.start() - 30):m.end() + 30])]
    return hits, neg
prev = json.load(open("kanto_prev.json", encoding="utf-8"))
async def main():
    out = []
    async with async_playwright() as p:
        b = await p.chromium.launch(); ctx = await b.new_context(user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36", locale="ja-JP", ignore_https_errors=True)
        sem = asyncio.Semaphore(8)
        async def one(r):
            if not (r.get("hits") or (r.get("err") and r["err"].split(":")[0] in ("HTTPError", "URLError", "TimeoutError", "UnicodeEncodeError", "ConnectionResetError"))): return
            async with sem:
                pg = await ctx.new_page()
                try:
                    await pg.goto(r.get("page") or r["url"], wait_until="domcontentloaded", timeout=25000); await pg.wait_for_timeout(1500)
                    t = unicodedata.normalize("NFKC", re.sub(r"\s+", " ", await pg.inner_text("body")))
                    h, n = judge2(t)
                    r2 = dict(r, hits=h[:4], neg=n[:3], title=(await pg.title())[:120], final=pg.url); r2.pop("err", None); out.append(r2)
                except Exception as e:
                    out.append(dict(r, err2=type(e).__name__))
                finally:
                    await pg.close()
        await asyncio.gather(*[one(r) for r in prev])
        await b.close()
    ok = [r for r in out if r.get("hits") and not r.get("neg")]
    for r in ok:  # 住所
        try:
            u = f'https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress?lat={r["lat"]}&lon={r["lon"]}'
            d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "petnavi-probe/1.0"}), timeout=15))
            r["muni"] = d.get("results", {}).get("muniCd"); r["lv01"] = d.get("results", {}).get("lv01Nm")
        except Exception: pass
    json.dump(out, open("shots/kanto2.json", "w"), ensure_ascii=False)
    print("rechecked", len(out), "ok", len(ok))
asyncio.run(main())
