import json, re, urllib.request, urllib.parse, unicodedata, time
UA = {"User-Agent": "PetnaviGuideBot/1.0 (https://petnavi.niconote.jp/; ichiro-ai90@users.noreply.github.com) python-urllib/3"}
def get(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30))
def norm(s): return re.sub(r"[\s・()（）「」\-ー]", "", unicodedata.normalize("NFKC", s)).lower()
out = []
for t in json.load(open("probe_commons_list.json")):
    g = {"slug": t["slug"]}; i = t["i"]; e = t
    if True:
        q = e["name"].split("（")[0].strip()
        try:
            r = get("https://ja.wikipedia.org/w/api.php?" + urllib.parse.urlencode({"action": "query", "format": "json", "list": "search", "srsearch": q, "srlimit": 3}))
            hit = next((h["title"] for h in r["query"]["search"] if norm(q) in norm(h["title"]) or norm(h["title"]) in norm(q)), None)
            res = None
            if hit:
                r2 = get("https://ja.wikipedia.org/w/api.php?" + urllib.parse.urlencode({"action": "query", "format": "json", "titles": hit, "prop": "pageimages", "piprop": "name", "redirects": 1}))
                pgs = list(r2["query"]["pages"].values()); fn = pgs[0].get("pageimage") if pgs else None
                if fn:
                    r3 = get("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({"action": "query", "format": "json", "titles": "File:" + fn, "prop": "imageinfo", "iiprop": "url|extmetadata|size", "iiurlwidth": 800}))
                    pp = list(r3["query"]["pages"].values())[0]
                    if "imageinfo" in pp:
                        ii = pp["imageinfo"][0]; md = ii.get("extmetadata", {})
                        artist = re.sub("<[^>]+>", "", md.get("Artist", {}).get("value", "")).strip()[:60]
                        lic = md.get("LicenseShortName", {}).get("value", "")
                        if ii.get("width", 0) >= 600 and re.search(r"CC|Public domain|PD", lic, re.I) and not fn.lower().endswith((".svg", ".png")):
                            fname = f"{g['slug']}-{i}.jpg"
                            open(f"shots/img/{fname}","wb").write(urllib.request.urlopen(urllib.request.Request(ii["thumburl"], headers=UA), timeout=30).read())
                            res = {"file": fname, "title": fn, "artist": artist, "license": lic, "page": ii["descriptionurl"], "wiki": hit}
            out.append({"slug": g["slug"], "i": i, "name": e["name"], "c": res}); print(g["slug"], i, q, "->", hit, bool(res), flush=True)
        except Exception as ex:
            out.append({"slug": g["slug"], "i": i, "name": e["name"], "c": None}); print("ERR", q, ex)
        time.sleep(0.5)
json.dump(out, open("shots/commons.json", "w"), ensure_ascii=False, indent=0)
