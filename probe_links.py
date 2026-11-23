import json, re, time, unicodedata, urllib.parse
from playwright.sync_api import sync_playwright
MEDIA = re.compile(r"(honda\.co\.jp|global\.honda|wankonowa|petokoto|inutome|rurubu|walkerplus|nap-camp|recommend-dog|tabelog|hotpepper|retty|jalan|google\.|goo\.gl|amazon|rakuten|valuecommerce|a8\.net|moshimo|twitter\.com|x\.com|facebook\.com|line\.me|lin\.ee|youtube|yahoo|tripadvisor|navitime|mapion|ikyu|booking\.com|gnavi|hatena|pinterest|tiktok|apple\.com|doubleclick|wordpress\.org|gstatic|cloudflare|hoshinoresorts\.com/(jp/)?guide|asoview|activityjapan|veltra|jtb|his-j|travel\.co\.jp|mapple|enjoytokyo|iko-yo|tabiiro|co-trip|retrip|aumo|gurunavi|ekiten|minkou|holiday\.|tokubai|creema|minne|base\.shop|stores\.jp/(?!.)|shopify|note\.com|ameblo|hotel|dog-hotel)", re.I)
def norm(s): return re.sub(r"[\s・･()（）「」『』【】\-ー－〜~!！?？&＆.,、。'’\"＆/／:：]", "", unicodedata.normalize("NFKC", s)).lower()
def keys(name):
    base = re.split(r"[（(]", name)[0]
    ks = {norm(base)}
    for part in re.split(r"[\s・／/]", unicodedata.normalize("NFKC", base)):
        if len(norm(part)) >= 3: ks.add(norm(part))
    return [k for k in ks if len(k) >= 3]
d = json.load(open("probe_guides.json")); out = []
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
with sync_playwright() as p:
    b = p.chromium.launch(); ctx = b.new_context(user_agent=UA, locale="ja-JP"); pg = ctx.new_page()
    cache = {}
    def grab(src):
        if src in cache: return cache[src]
        try:
            pg.goto(src, timeout=45000, wait_until="domcontentloaded"); pg.wait_for_timeout(1500)
            cache[src] = pg.evaluate(r"""()=>{const out=[];let cur='';
              for(const e of document.querySelectorAll('h1,h2,h3,h4,h5,strong,b,a,tr,dt')){
                if(/^H[1-5]$/.test(e.tagName)||e.tagName=='STRONG'||e.tagName=='B'){cur=e.textContent.trim().slice(0,80)}
                if(e.tagName=='TR'||e.tagName=='DT'){const a=e.querySelector('a[href^="http"]'); const h=(e.querySelector('th,dt')||e).textContent.trim().slice(0,20); if(a) out.push(['ROW:'+h,a.href,a.textContent.trim().slice(0,60)]); continue}
                if(e.tagName=='A'&&e.href.startsWith('http')) out.push([cur,e.href,(e.textContent||'').trim().slice(0,60)])}
              return out}""")
        except Exception: cache[src] = []
        return cache[src]
    vcache = {}
    def verify(url, name):
        """open url; return (ok, final, title, igs)"""
        if url in vcache: page = vcache[url]
        else:
            try:
                r = pg.goto(url, timeout=40000, wait_until="domcontentloaded"); pg.wait_for_timeout(1200)
                page = {"st": r.status if r else 0, "final": pg.url, "title": pg.title()[:120], "text": norm(pg.inner_text("body")[:20000]),
                        "igs": pg.evaluate("[...document.querySelectorAll('a[href*=\"instagram.com/\"]')].map(a=>a.href)")}
            except Exception: page = {"st": 0, "final": url, "title": "", "text": "", "igs": []}
            vcache[url] = page
        ks = keys(name)
        hit = any(k in norm(page["title"]) or k in page["text"] for k in ks)
        return (200 <= page["st"] < 400 and hit), page
    def igname(u):
        m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)/?(?:\?|$)", u)
        return m.group(1) if m and m.group(1) not in ("p", "reel", "reels", "explore", "accounts", "stories", "tv") else ""
    for g in d["guides"]:
        for kind in ("spots", "shops"):
            for i, e in enumerate(g[kind]):
                src = e["src"]; ks = keys(e["name"])
                pu = urllib.parse.urlparse(src)
                if not MEDIA.search(pu.netloc + pu.path):
                    cands = [(src, "self")]
                else:
                    rows = grab(src); cands = []
                    for sec, h, t in rows:
                        hu = urllib.parse.urlparse(h)
                        if "instagram.com" in hu.netloc:
                            if any(k in norm(sec) or k in norm(t) for k in ks) or sec.startswith("ROW:"): cands.append((h, "ig"))
                            continue
                        if MEDIA.search(hu.netloc + hu.path): continue
                        if sec.startswith("ROW:") and re.search(r"URL|ＵＲＬ|ホームページ|HP|公式|サイト|Web|WEB", sec): cands.append((h, "row"))
                        elif any(k in norm(sec) for k in ks) or any(k in norm(t) for k in ks) or re.search(r"公式|ホームページ|HP", t):
                            if any(k in norm(sec) for k in ks) or any(k in norm(t) for k in ks): cands.append((h, "sec"))
                site = ""; ig = ""; title = ""
                for h, how in cands:
                    if how == "ig":
                        ig = ig or igname(h); continue
                    if site: continue
                    ok, page = verify(h, e["name"])
                    if ok:
                        site = page["final"]; title = page["title"]
                        if not ig:
                            ig = next((igname(x) for x in page["igs"] if igname(x)), "")
                out.append({"slug": g["slug"], "kind": kind, "i": i, "name": e["name"], "src": src, "site": site, "title": title, "ig": ig, "ncand": len(cands)})
                print(g["slug"], kind, i, e["name"][:16], "|", site[:60], "|", ig, "|", title[:40], flush=True)
    pg.goto("https://www.instagram.com/ellspark_nagatoro/embed/", timeout=40000); pg.wait_for_timeout(4000); pg.set_viewport_size({"width": 390, "height": 700}); pg.screenshot(path="shots/ig_embed.png")
    b.close()
json.dump(out, open("shots/links.json", "w"), ensure_ascii=False, indent=0)
