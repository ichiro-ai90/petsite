import json, re, unicodedata
from playwright.sync_api import sync_playwright
def norm(s): return re.sub(r"[\s・･()（）「」『』【】\-ー－〜~!！?？&＆.,、。'’\"＆/／:：]", "", unicodedata.normalize("NFKC", s)).lower()
def keys(name):
    base = unicodedata.normalize("NFKC", re.split(r"[（(]", name)[0])
    ks = {norm(base), norm(base)[:6]}
    for part in re.split(r"[\s・／/「」『』]", base):
        if len(norm(part)) >= 3: ks.add(norm(part))
    for x in re.findall(r"[（(]([^）)]+)[）)]", name):
        if len(norm(x)) >= 3: ks.add(norm(x))
    return [k for k in ks if len(k) >= 3 and k not in ("cafe", "dog", "the", "カフェ", "レストラン")]
def igname(u):
    m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", u)
    return m.group(1) if m and m.group(1) not in ("p", "reel", "explore", "accounts") else ""
T = json.load(open("probe_pass2.json")); out = []
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_context(ignore_https_errors=True, locale="ja-JP", user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36").new_page()
    for t in T:
        site = ig = title = ""
        for u in t["cands"]:
            if "instagram.com" in u: ig = ig or igname(u); continue
            if site: continue
            try:
                try:
                    r = pg.goto(u, timeout=40000, wait_until="domcontentloaded"); st = r.status if r else 200
                except Exception as ex:
                    if "interrupted" not in str(ex): raise
                    st = 200
                pg.wait_for_timeout(3000)
                try: pg.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception: pass
                tt = pg.title(); body = norm(pg.inner_text("body")[:30000])
                hit = any(k in norm(tt) or k in body for k in keys(t["name"]))
                print("TRY", t["name"][:14], u[:50], st, hit, tt[:30], flush=True)
                if 200 <= st < 400 and hit:
                    site = pg.url; title = tt
                    if not ig:
                        ig = next((igname(x) for x in pg.evaluate("[...document.querySelectorAll('a[href*=\"instagram.com/\"]')].map(a=>a.href)") if igname(x)), "")
            except Exception as e:
                print("ERR", u[:50], str(e)[:60])
        out.append({**t, "site": site, "ig": ig, "title": title})
    b.close()
json.dump(out, open("shots/pass2.json", "w"), ensure_ascii=False, indent=0)
