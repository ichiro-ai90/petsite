import json, urllib.request, urllib.parse, time
Q = """[out:json][timeout:180];
area["ISO3166-1"="JP"][admin_level=2]->.jp;
(
 nwr(area.jp)["dog"~"^(yes|leashed|outside|designated|unleashed)$"]["name"];
 nwr(area.jp)["leisure"="dog_park"];
);
out center tags;"""
r = urllib.request.urlopen(urllib.request.Request("https://overpass-api.de/api/interpreter", data=urllib.parse.urlencode({"data": Q}).encode(), headers={"User-Agent": "petnavi-probe (contact via github ichiro-ai90)"}), timeout=300)
d = json.load(r)
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
