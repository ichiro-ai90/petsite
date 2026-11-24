"""関東（1都6県）の OpenStreetMap 上の飲食店・施設のうち、公式サイトURLがあるものを全部集め、
公式サイトに「ペット同伴可」などの記載があるかを確かめる（11/24 Taku 承認の方法A）。結果は shots/kanto.json"""
import json, re, time, urllib.request, urllib.parse, unicodedata, concurrent.futures as cf
from urllib.parse import urljoin, urlparse
BB = "34.85,138.35,37.20,140.95"
Q = f"""[out:json][timeout:600];
(
 nwr({BB})["amenity"~"^(cafe|restaurant|fast_food|bar|pub|ice_cream|biergarten|food_court)$"]["website"];
 nwr({BB})["amenity"~"^(cafe|restaurant|fast_food|bar|pub|ice_cream)$"]["contact:website"];
 nwr({BB})["shop"~"^(bakery|pastry|confectionery|deli|coffee|tea)$"]["website"];
);
out center tags;"""
def overpass():
    for ep in ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter", "https://overpass.private.coffee/api/interpreter"]:
        try:
            r = urllib.request.urlopen(urllib.request.Request(ep, data=urllib.parse.urlencode({"data": Q}).encode(), headers={"User-Agent": "petnavi-probe/1.0 (https://petnavi.niconote.jp)"}), timeout=700)
            return json.load(r)["elements"]
        except Exception as e:
            print("ng", ep, e); time.sleep(10)
    return []
POS = re.compile(r"(ペット|犬|わんちゃん|ワンちゃん|わんこ|ワンコ|愛犬|(?<!ホット)ドッグ|(?<!hot)dog)[^。\n]{0,12}?(同伴|連れ|ok|可能|可|歓迎|大歓迎|入店|一緒|入れ|専用メニュー|メニュー|フレンドリー|friendly)|ドッグカフェ|dog\s?cafe|ドッグラン|ペット同伴|犬同伴|ペット可|ペットok|ドッグフレンドリー|dog\s?friendly|テラス席[^。\n]{0,15}(ペット|犬|わんちゃん|ワンちゃん)", re.I)
NEG = re.compile(r"(ペット|犬|動物|わんちゃん|ワンちゃん)[^。\n]{0,15}(不可|お断り|ご遠慮|禁止|ng|できません|入れません|入店不可|持ち込み不可)", re.I)
SVC = re.compile(r"(盲導犬|補助犬|介助犬|聴導犬|身体障害者補助犬)")
SUB = re.compile(r"(よくある|faq|q&a|ご利用|ご案内|案内|info|インフォ|access|アクセス|店舗情報|about|お店について|guide|ガイド|テラス|pet|ペット|dog|わんちゃん|ワンちゃん)", re.I)
UA = {"User-Agent": "Mozilla/5.0 (compatible; petnavi-check/1.0; +https://petnavi.niconote.jp)"}
def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
        ct = r.headers.get("content-type", "")
        if "html" not in ct and ct: return r.status, r.geturl(), ""
        return r.status, r.geturl(), r.read(800000).decode(r.headers.get_content_charset() or "utf-8", "ignore")
def text(html):
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    t = re.sub(r"(?s)<[^>]+>", " ", html)
    t = unicodedata.normalize("NFKC", t)
    return re.sub(r"\s+", " ", t)
def judge(t):
    pos = [m for m in POS.finditer(t)]
    hits = []
    for m in pos:
        a, b = max(0, m.start() - 40), min(len(t), m.end() + 60)
        ctx = t[a:b]
        if NEG.search(ctx) or SVC.search(ctx): continue
        hits.append(ctx)
    neg = [t[max(0, m.start() - 30):m.end() + 30] for m in NEG.finditer(t) if not SVC.search(t[max(0, m.start() - 30):m.end() + 30])]
    return hits, neg
def check(e):
    tg = e.get("tags", {}); url = tg.get("website") or tg.get("contact:website")
    lat = e.get("lat") or e.get("center", {}).get("lat"); lon = e.get("lon") or e.get("center", {}).get("lon")
    r = {"id": f'{e["type"]}/{e["id"]}', "name": tg.get("name", ""), "kind": tg.get("amenity") or tg.get("shop"), "url": url, "lat": lat, "lon": lon,
         "addr": " ".join(filter(None, [tg.get("addr:province"), tg.get("addr:city"), tg.get("addr:quarter"), tg.get("addr:neighbourhood")])), "osm_dog": tg.get("dog", "")}
    if not url or not url.startswith("http"): r["err"] = "nourl"; return r
    try:
        st, fu, html = get(url)
        r["status"] = st; r["final"] = fu
        t = text(html); r["title"] = (re.search(r"(?is)<title[^>]*>(.*?)</title>", html) or [None, ""])[1].strip()[:120]
        hits, neg = judge(t)
        if not hits and not neg:  # 下層ページも少し見る
            host = urlparse(fu).netloc; seen = 0
            for m in re.finditer(r'(?is)<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', html):
                href, lab = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
                u = urljoin(fu, href)
                if urlparse(u).netloc != host or not (SUB.search(lab) or SUB.search(href)): continue
                try:
                    _, fu2, h2 = get(u); h, n = judge(text(h2))
                    if h or n: hits, neg = h, n; r["page"] = fu2; break
                except Exception: pass
                seen += 1
                if seen >= 3: break
        r["hits"] = hits[:4]; r["neg"] = neg[:3]
    except Exception as ex:
        r["err"] = type(ex).__name__ + ":" + str(ex)[:80]
    return r
els = overpass()
seen = set(); todo = []
for e in els:
    u = (e.get("tags", {}).get("website") or e.get("tags", {}).get("contact:website") or "").strip()
    if not u or u in seen: continue
    seen.add(u); todo.append(e)
print("candidates", len(els), "unique urls", len(todo))
out = []
with cf.ThreadPoolExecutor(24) as ex:
    for i, r in enumerate(ex.map(check, todo)):
        out.append(r)
        if i % 200 == 0: print(i, flush=True)
json.dump(out, open("shots/kanto.json", "w"), ensure_ascii=False)
ok = [r for r in out if r.get("hits") and not r.get("neg")]
print("done", len(out), "hits", len(ok), "neg", sum(1 for r in out if r.get("neg")), "err", sum(1 for r in out if r.get("err")))
