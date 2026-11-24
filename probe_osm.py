import json, urllib.request, urllib.parse, time
Q = """[out:json][timeout:180];
(
 nwr(24.0,122.9,45.6,146.0)["dog"~"^(yes|leashed|outside|designated|unleashed)$"]["name"];
 nwr(24.0,122.9,45.6,146.0)["leisure"="dog_park"];
);
out center tags;"""
d = None
for ep in ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter", "https://overpass.private.coffee/api/interpreter"]:
    try:
        r = urllib.request.urlopen(urllib.request.Request(ep, data=urllib.parse.urlencode({"data": Q}).encode(), headers={"User-Agent": "petnavi-probe/1.0 (https://petnavi.niconote.jp)", "Accept": "application/json"}), timeout=300)
        d = json.load(r); print("ok", ep); break
    except Exception as e:
        print("ng", ep, getattr(e, "code", ""), str(e)[:200]); time.sleep(5)
els = d["elements"]
from collections import Counter
c = Counter(); w = 0
for e in els:
    t = e.get("tags", {})
    k = t.get("amenity") or t.get("leisure") or t.get("tourism") or t.get("shop") or "other"
    c[k] += 1
    if t.get("website") or t.get("contact:website"): w += 1
json.dump({"n": len(els), "kinds": c.most_common(30), "with_site": w, "sample": els[:40]}, open("shots/osm.json", "w"), ensure_ascii=False, indent=1)
json.dump(els, open("shots/osm_all.json", "w"), ensure_ascii=False)
