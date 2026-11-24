"""情報提供フォームの投稿（issue）を確かめて、travel_guides.json に足す（11/24 Taku「登録をトリガーに一郎が作る」）
- 公式サイト：開けるか（200〜399）・名前がページにあるか・ペットに関する言葉があるか
- Instagram だけの投稿は、アカウント名の形だけ確かめて載せる（中身は人が見る）
- 結果は issue にコメントし、載せられるものは PR を作る（マージは監査のあと）
使い方: python scripts/intake.py <issue本文のファイル>  → 標準出力に結果のJSON
"""
import json, re, sys, unicodedata, urllib.request, os, base64

def keys(name):
    s = unicodedata.normalize("NFKC", name).lower()
    s = re.sub(r"[（(].*?[)）]", " ", s)
    return [w for w in re.split(r"[\s・/｜|、,]+", s) if len(w) >= 2]

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (petnavi intake; +https://petnavi.niconote.jp/)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.read(600000).decode("utf-8", "ignore")

def main(path):
    body = open(path, encoding="utf-8").read()
    m = re.search(r"```json\s*(\{.*?\})\s*```", body, re.S)
    if not m: return {"ok": False, "why": "投稿の形式が読めませんでした"}
    o = json.loads(m.group(1))
    res = {"ok": False, "o": o, "checks": []}
    site = ""
    if o.get("url"):
        try:
            st, html = fetch(o["url"])
            t = unicodedata.normalize("NFKC", html).lower()
            name_ok = any(k in t for k in keys(o["name"]))
            pet_ok = bool(re.search(r"犬|ペット|わんちゃん|ワンちゃん|わんこ|ドッグ|dog", t))
            res["checks"].append(f"公式サイト：開けた（{st}）・名前 {'あり' if name_ok else 'なし'}・ペットの言葉 {'あり' if pet_ok else 'なし'}")
            if 200 <= st < 400 and name_ok: site = o["url"]
            res["pet_word"] = pet_ok
        except Exception as e:
            res["checks"].append(f"公式サイト：開けませんでした（{type(e).__name__}）")
    ig = o.get("ig") if re.fullmatch(r"[A-Za-z0-9._]{1,30}", o.get("ig") or "") else ""
    if ig: res["checks"].append(f"Instagram：@{ig}（形式のみ確認）")
    if not site and not ig:
        res["why"] = "公式サイトで名前を確かめられず、Instagramもありませんでした"; return res
    g = json.load(open("travel_guides.json", encoding="utf-8"))
    gd = next((x for x in g["guides"] if x["pref"] == o.get("pref")), None)
    if not gd: res["why"] = "この都道府県のガイドがまだありません"; return res
    k = "spots" if o.get("kind") == "spot" else "shops"
    nk = lambda s: re.sub(r"\s", "", unicodedata.normalize("NFKC", s).lower())
    if any(nk(e["name"]) == nk(o["name"]) for e in gd["spots"] + gd["shops"]):
        res["why"] = "同じ名前がすでに載っています"; return res
    cond = {"店内OK": "店内で犬連れ可", "テラス席OK": "テラス席で犬連れ可", "屋外OK": "屋外で犬連れ可"}.get(o.get("dog"), "犬連れの条件は公式で確認")
    if o.get("big") == "可": cond += "・大型犬も可"
    elif o.get("big") == "不可": cond += "（大型犬は不可）"
    e = {"name": o["name"], "where": o.get("where") or gd["pref"], "desc": ("お店" if k == "shops" else "スポット") + "（利用者からの情報）",
         "cond": cond + "（利用者からの情報）", "site": site or None, "ig": ig or None, "ref": "user:" + o.get("id", ""), "src": "user"}
    if o.get("photo") and o.get("photo_ok"):
        e["photo"] = {"file": "u-" + o["id"] + ".jpg", "artist": "投稿者提供", "license": "掲載許可済み", "page": "", "user": True}
        res["photo"] = o["photo"]
    gd[k].append(e)
    json.dump(g, open("travel_guides.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    res.update(ok=True, slug=gd["slug"], kind=k)
    return res

if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1]), ensure_ascii=False))
