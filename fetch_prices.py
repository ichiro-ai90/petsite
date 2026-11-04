"""価格取得スクリプト（楽天市場だけ。2026-11-04 v2: 商品ごとの最安値とポイント）。

流れ（カテゴリごと）:
  1. 楽天の製品検索API（Product/Search）でカテゴリの「製品」を探す（同じ商品を売るショップをまとめた単位）。
  2. 人気（レビュー件数）の多い順に最大 MAX_PRODUCTS 件を選び、JANコードで楽天市場の商品検索API（IchibaItem/Search）を
     価格の安い順に引いて、その商品を売っているショップの価格・ポイント倍率・送料・アフィリエイトURLを集める。
  3. data/prices.json に products（製品ごとのショップ一覧）として保存する。

- 環境変数: RAKUTEN_APP_ID, RAKUTEN_ACCESS_KEY, RAKUTEN_AFFILIATE_ID, SITE_ORIGIN（Origin ヘッダー）, PETSITE_PROD=1（本番）
- キー未設定かつ非本番なら data/sample_prices.json を使う（外部通信なし）。本番でキー未設定は exit 2。
- 1req/秒を守る（INTERVAL）。429・5xx は1回だけ再試行。エラー本文（先頭200字）をログに出す。
- カテゴリ単位で失敗・0件なら前回 data/prices.json の同カテゴリを引き継ぐ（carried）。全体で新規0件なら、前回の本番データが
  あれば残して exit 0、無ければ exit 1（公開しない）。
- ポイントは「ショップのポイント倍率 × 税抜価格の1%」の目安（SPU・キャンペーンは含まない）。
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
JST = timezone(timedelta(hours=9))
HOST = "https://openapi.rakuten.co.jp"
ITEM_URL = HOST + "/ichibams/api/IchibaItem/Search/20260401"
PRODUCT_URL = HOST + "/ichibaproduct/api/Product/Search/20250801"
SITE_ORIGIN = os.environ.get("SITE_ORIGIN", "https://example.com")
MAX_PRODUCTS = 8      # カテゴリあたりの製品数
MAX_OFFERS = 10       # 製品あたりに残すショップ数（安い順）
INTERVAL = 1.1        # 秒
_last = {"t": -1e9}


def _throttle():
    wait = INTERVAL - (time.monotonic() - _last["t"])
    if wait > 0:
        time.sleep(wait)
    _last["t"] = time.monotonic()


def _get(url, params, headers=None):
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers=headers or {})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _call(url, params):
    p = dict(params, applicationId=os.environ["RAKUTEN_APP_ID"], accessKey=os.environ["RAKUTEN_ACCESS_KEY"],
             format="json", formatVersion=2)
    if os.environ.get("RAKUTEN_AFFILIATE_ID"):
        p["affiliateId"] = os.environ["RAKUTEN_AFFILIATE_ID"]
    for attempt in (1, 2):
        _throttle()
        try:
            return _get(url, p, {"Origin": SITE_ORIGIN})
        except urllib.error.HTTPError as e:
            if attempt == 1 and (e.code == 429 or e.code >= 500):
                time.sleep(3)
                continue
            try:
                body = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                body = ""
            raise RuntimeError(f"HTTP {e.code} {body}") from e


def points(price, rate):
    """ショップのポイント倍率から計算した目安（税抜価格の1%×倍率、切り捨て）。"""
    return int(int(price / 1.1) * (rate or 1) // 100)


def _prod(raw):
    return raw.get("Product", raw) if isinstance(raw, dict) else {}


def search_products(group):
    d = _call(PRODUCT_URL, {"keyword": group["keyword"], "hits": 30})
    out = []
    for raw in d.get("Products", []):
        p = _prod(raw)
        name = p.get("productName") or ""
        if not name or any(x in name for x in group.get("exclude", [])):
            continue
        out.append({
            "productId": p.get("productId") or "", "name": name, "jan": str(p.get("productCode") or ""),
            "maker": p.get("makerName") or p.get("brandName") or "",
            "image": p.get("mediumImageUrl") or p.get("productImageUrl") or "",
            "minPrice": int(p.get("salesMinPrice") or p.get("minPrice") or 0),
            "itemCount": int(p.get("salesItemCount") or p.get("itemCount") or 0),
            "productUrl": p.get("affiliateUrl") or p.get("productUrlPC") or "",
            "genre": p.get("genreName") or "",
            "reviewCount": int(p.get("reviewCount") or 0), "reviewAverage": float(p.get("reviewAverage") or 0),
        })
    out = [p for p in out if p["minPrice"] > 0]
    out.sort(key=lambda p: (-p["reviewCount"], -p["itemCount"]))
    return out[:MAX_PRODUCTS]


def search_offers(product):
    """JAN があれば JAN、無ければ製品名で、楽天市場の商品を価格の安い順に。"""
    kw = product["jan"] if re.fullmatch(r"\d{8,14}", product["jan"]) else product["name"]
    d = _call(ITEM_URL, {"keyword": kw, "hits": 30, "sort": "+itemPrice", "availability": 1})
    if "Items" not in d:
        raise RuntimeError(f"想定外の応答形式: keys={list(d)[:10]}")
    lo = product["minPrice"] * 0.7  # 製品の最安値より大幅に安いものは別商品（付属品など）とみなす
    offers = []
    for it in d["Items"]:
        price = int(it.get("itemPrice") or 0)
        url = it.get("affiliateUrl") or it.get("itemUrl") or ""
        if price <= 0 or not url or price < lo:
            continue
        rate = int(it.get("pointRate") or 1)
        pt = points(price, rate)
        offers.append({
            "shop": it.get("shopName") or "", "name": it.get("itemName") or "", "price": price,
            "pointRate": rate, "points": pt, "effective": price - pt,
            "shipping": "送料無料" if it.get("postageFlag") == 0 else "送料別",
            "url": url, "reviewCount": int(it.get("reviewCount") or 0),
            "reviewAverage": float(it.get("reviewAverage") or 0),
            "image": (it.get("mediumImageUrls") or [""])[0] if isinstance((it.get("mediumImageUrls") or [""])[0], str)
            else ((it.get("mediumImageUrls") or [{}])[0].get("imageUrl", "")),
        })
    # 同じショップは一番安い1件だけ
    best = {}
    for o in sorted(offers, key=lambda o: o["price"]):
        best.setdefault(o["shop"], o)
    return sorted(best.values(), key=lambda o: o["price"])[:MAX_OFFERS]


def fetch_group(group):
    prods = []
    for p in search_products(group):
        offers = search_offers(p)
        if not offers:
            continue
        p = dict(p, group=group["slug"], offers=offers)
        if not p["image"]:
            p["image"] = offers[0]["image"]
        prods.append(p)
    return prods


def _load_prev(dst):
    try:
        d = json.load(open(dst, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if str(d.get("fetched_at", "")).startswith("SAMPLE") or not d.get("products"):
        return None
    return d


def main():
    groups = json.load(open(os.path.join(ROOT, "products.json"), encoding="utf-8"))["groups"]
    prod = os.environ.get("PETSITE_PROD") == "1"
    has_key = all(os.environ.get(k) for k in ("RAKUTEN_APP_ID", "RAKUTEN_ACCESS_KEY"))
    dst = os.path.join(ROOT, "data", "prices.json")
    if prod and not has_key:
        missing = [k for k in ("RAKUTEN_APP_ID", "RAKUTEN_ACCESS_KEY") if not os.environ.get(k)]
        print(f"::error::本番実行なのに楽天 Secrets が未設定: {', '.join(missing)}（サンプルでは公開しません）", file=sys.stderr)
        return 2
    if not has_key:
        src = os.path.join(ROOT, "data", "sample_prices.json")
        json.dump(json.load(open(src, encoding="utf-8")), open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("API キー未設定: サンプルデータを使用（開発用。本番では使われない）")
        return 0
    prev = _load_prev(dst)
    products, errors, carried = [], [], []
    for g in groups:
        got = []
        try:
            got = fetch_group(g)
        except Exception as e:  # 1カテゴリの失敗で全体を止めない
            errors.append(f"{g['slug']}: {type(e).__name__}: {e}")
        if not got and prev:
            old = [p for p in prev["products"] if p.get("group") == g["slug"]]
            if old:
                carried.append(g["slug"])
                got = [dict(p, fetched_at=p.get("fetched_at") or prev.get("fetched_at")) for p in old]
        products += got
    new_count = sum(1 for g in groups if g["slug"] not in carried and any(p["group"] == g["slug"] for p in products))
    if new_count == 0:
        if prev:
            print(f"::warning::新規取得 0 件。前回データ（{prev.get('fetched_at')}）を残して再生成します", errors[:5], file=sys.stderr)
            return 0
        print("::error::取得 0 件かつ前回の本番データなし。公開しません", errors[:5], file=sys.stderr)
        return 1
    json.dump({"fetched_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"), "products": products,
               "errors": errors, "carried": carried},
              open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    offers = sum(len(p["offers"]) for p in products)
    print(f"製品 {len(products)} 件・ショップ {offers} 件（新規取得 {new_count} カテゴリ／前回引き継ぎ {len(carried)} カテゴリ）、エラー {len(errors)} 件")
    for e in errors[:10]:
        print("  ", e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
