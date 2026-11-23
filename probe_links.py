import json, re, time, unicodedata, urllib.parse, urllib.request, sys
from playwright.sync_api import sync_playwright
MEDIA = re.compile(r"(honda\.co\.jp|wankonowa|petokoto|inutome|rurubu|walkerplus|nap-camp|recommend-dog|tabelog|hotpepper|retty|jalan|google\.|goo\.gl|amazon|rakuten|valuecommerce|a8\.net|moshimo|twitter\.com|x\.com|facebook\.com|line\.me|youtube|yahoo|tripadvisor|navitime|mapion|ikyu|booking|gnavi|livedoor|ameblo\.jp/wanko|hatena|note\.com/(?!.*official)|pinterest|tiktok|apple\.com|play\.google|doubleclick|addthis|wordpress\.org|gstatic|cloudflare|hoshinoresorts\.com/guide)", re.I)
def norm(s): return re.sub(r"[\s・･()（）「」『』\-ー〜~!！?？&＆.,、。'’]", "", unicodedata.normalize("NFKC", s)).lower()
d = json.load(open("probe_guides.json"))
out = []
UA = {"User-Agent": "Mozilla/5.0 (compatible; petnavi-guide-check/1.0; +https://petnavi.niconote.jp)"}
def alive(u):
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20); return r.status, r.geturl()
    except urllib.error.HTTPError as e: return e.code, u
    except Exception as e: return 0, u
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(user_agent=UA["User-Agent"])
    cache = {}
    def links_for(src, name):
        if src not in cache:
            try:
                pg.goto(src, timeout=45000, wait_until="domcontentloaded"); pg.wait_for_timeout(1200)
                cache[src] = pg.evaluate("""()=>{const out=[];let cur='';const walk=document.querySelectorAll('h1,h2,h3,h4,h5,a,th,td,dt,dd,strong,b,p');
                  for(const e of walk){ if(/^H[1-5]$/.test(e.tagName)||e.tagName=='STRONG'||e.tagName=='B'){cur=e.textContent.trim().slice(0,80)} if(e.tagName=='A'&&e.href.startsWith('http')) out.push([cur,e.href,(e.textContent||'').trim().slice(0,60)]) } return out}""")
            except Exception as ex:
                cache[src] = []
        n = norm(name.split("（")[0])[:8]
        cands = [h for (sec, h, t) in cache[src] if (n and n in norm(sec)) or (n and n in norm(t))]
        if "honda.co.jp/dog/travel/data" in src: cands = [h for (sec, h, t) in cache[src]]
        return [h for h in cands if not MEDIA.search(urllib.parse.urlparse(h).netloc + urllib.parse.urlparse(h).path)]
    for g in d["guides"]:
        for kind in ("spots", "shops"):
            for i, e in enumerate(g[kind]):
                src = e["src"]; host = urllib.parse.urlparse(src).netloc
                cands = [src] if not MEDIA.search(host + urllib.parse.urlparse(src).path) else links_for(src, e["name"])
                site = next((c for c in cands if "instagram.com" not in c), ""); ig = next((c for c in cands if "instagram.com" in c), "")
                st = 0; final = ""
                if site:
                    st, final = alive(site)
                    if 200 <= st < 400 and not ig:
                        try:
                            pg.goto(site, timeout=30000, wait_until="domcontentloaded")
                            igs = pg.evaluate("[...document.querySelectorAll('a[href*=\"instagram.com/\"]')].map(a=>a.href)")
                            ig = next((x for x in igs if re.search(r"instagram\.com/[A-Za-z0-9_.]+/?(\?.*)?$", x)), "")
                        except Exception: pass
                igu = ""
                if ig:
                    m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", ig)
                    if m and m.group(1) not in ("p", "reel", "explore", "accounts"):
                        igu = m.group(1)
                out.append({"slug": g["slug"], "kind": kind, "i": i, "name": e["name"], "src": src, "site": site, "status": st, "final": final, "ig": igu})
                print(g["slug"], kind, i, st, site[:60], igu, flush=True)
                time.sleep(0.3)
    b.close()
json.dump(out, open("shots/links.json", "w"), ensure_ascii=False, indent=0)
