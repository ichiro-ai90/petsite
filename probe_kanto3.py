"""関東5,010件の一覧用：OSMのタグ（SNS・業態）と国土地理院の住所を付ける"""
import json, time, urllib.request, urllib.parse, concurrent.futures as cf
exec(open("probe_kanto.py", encoding="utf-8").read().split("def overpass")[0])
src = open("probe_kanto.py", encoding="utf-8").read()
Q = src.split('Q = f"""')[1].split('"""')[0].replace("{BB}", "34.85,138.35,37.20,140.95")
els = None
for i in range(6):
    for ep in ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter", "https://overpass.private.coffee/api/interpreter"]:
        try:
            r = urllib.request.urlopen(urllib.request.Request(ep, data=urllib.parse.urlencode({"data": Q}).encode(), headers={"User-Agent": "petnavi-probe/1.0 (https://petnavi.niconote.jp)"}), timeout=700)
            els = json.load(r)["elements"]; print("ok", ep, len(els)); break
        except Exception as e:
            print("ng", ep, e); time.sleep(15)
    if els: break
prev = {r["id"]: r for r in json.load(open("kanto_prev.json", encoding="utf-8"))}
out = {}
for e in els:
    k = f'{e["type"]}/{e["id"]}'
    if k in prev:
        t = e.get("tags", {})
        out[k] = {kk: v for kk, v in t.items() if kk.startswith(("contact:", "instagram", "facebook", "twitter", "addr:", "cuisine", "opening_hours", "dog", "name"))}
def geo(r):
    try:
        u = f'https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress?lat={r["lat"]}&lon={r["lon"]}'
        d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "petnavi-probe/1.0"}), timeout=15))
        return r["id"], d.get("results", {}).get("muniCd"), d.get("results", {}).get("lv01Nm")
    except Exception:
        return r["id"], None, None
G = {}
with cf.ThreadPoolExecutor(6) as ex:
    for k, m, l in ex.map(geo, [r for r in prev.values() if r.get("lat")]):
        G[k] = [m, l]
json.dump({"tags": out, "geo": G}, open("shots/kanto3.json", "w"), ensure_ascii=False)
print("tags", len(out), "geo", sum(1 for v in G.values() if v[0]))
