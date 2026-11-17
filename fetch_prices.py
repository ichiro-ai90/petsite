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
MAX_PRODUCTS = 80     # 11/17 Taku「商品数が少ない」で 40→80。 カテゴリあたりの製品数（11/15 Taku「品揃えが悪すぎる」で 10→40）
MAX_TRIES = 120       # カテゴリあたりにショップ検索する製品の上限（1req/秒で全体 約50分以内に収める）
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
    return (price * 10 // 11) * (rate or 1) // 100


def _prod(raw):
    return raw.get("Product", raw) if isinstance(raw, dict) else {}


PRODUCT_PAGES = 5     # 11/17 3→5。 製品検索のページ数（30件×3＝最大90候補）


def search_products(group):
    raws, seen = [], set()
    kws = group.get("pkeywords") or [group.get("pkeyword") or group["keyword"]]  # 複数の言い方で探して合わせる（11/15）
    for ki, kw in enumerate(kws):
        for page in range(1, int(group.get("pages") or PRODUCT_PAGES) + 1):
            try:
                d = _call(PRODUCT_URL, {"keyword": kw, "hits": 30, "page": page})
            except Exception as e:  # 1つ目の言い方の1ページ目以外の失敗は、取れた分で続ける
                if page == 1 and ki == 0:
                    raise
                print(f"  製品検索「{kw}」{page}ページ目を省略: {str(e)[:120]}")
                break
            got = d.get("Products", [])
            raws += got
            if len(got) < 30:
                break
    out = []
    for raw in raws:
        p = _prod(raw)
        name = p.get("productName") or ""
        if not name or any(x in name for x in group.get("exclude", [])):
            continue
        if (p.get("productId") or "") in seen:
            continue
        seen.add(p.get("productId") or "")
        out.append({
            "productId": p.get("productId") or "", "name": name, "jan": str(p.get("productCode") or ""),
            "maker": p.get("makerName") or p.get("brandName") or "",
            "image": p.get("mediumImageUrl") or p.get("productImageUrl") or "",
            "minPrice": int(p.get("salesMinPrice") or p.get("minPrice") or 0),
            "avgPrice": int(p.get("averagePrice") or 0),  # 楽天の製品データの平均価格（今の取得値。保存しない＝毎回取り直す）
            "maxPrice": int(p.get("salesMaxPrice") or p.get("maxPrice") or 0),
            "itemCount": int(p.get("salesItemCount") or p.get("itemCount") or 0),
            "productUrl": p.get("affiliateUrl") or p.get("productUrlPC") or "",
            "genre": p.get("genreName") or "",
            "reviewCount": int(p.get("reviewCount") or 0), "reviewAverage": float(p.get("reviewAverage") or 0),
        })
    out = [p for p in out if p["minPrice"] > 0]
    out.sort(key=lambda p: (-p["reviewCount"], -p["itemCount"]))
    return out


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


YAHOO_URL = "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"
YAHOO_OFFERS = 5      # 製品あたりに残す Yahoo!ショッピングのストア数（安い順）
YAHOO_INTERVAL = 3.1  # 秒。1.1秒・2秒だと「The AppID is denied: total count of AppID reached the URL's limit count」の 429 になる（11/16 一時ワークフローで確認。3秒は 20回連続で成功）
YAHOO_BUDGET = 60 * 60  # 秒。Yahoo の取得は実行開始から60分まで（全体を90分以内に収める。残りは楽天だけ）
_ylast = {"t": -1e9, "start": time.monotonic(), "pause": 0.0}
_ycache = {}


def yahoo_offers(product):
    """同じ JAN の Yahoo!ショッピングの商品（安い順）。YAHOO_APP_ID が無い・JAN が無い・失敗のときは []（楽天だけで続ける）。"""
    appid = os.environ.get("YAHOO_APP_ID")
    if not appid or not re.fullmatch(r"\d{8,14}", product.get("jan") or ""):
        return []
    p = {"appid": appid, "jan_code": product["jan"], "results": 20, "sort": "+price", "in_stock": "true"}
    if os.environ.get("VC_AFFILIATE_ID"):
        p.update(affiliate_type="vc", affiliate_id=os.environ["VC_AFFILIATE_ID"])
    if product["jan"] in _ycache:  # 同じ商品が2つのカテゴリにあるとき
        return parse_yahoo(_ycache[product["jan"]], product["minPrice"])
    now = time.monotonic()
    if now - _ylast["start"] > YAHOO_BUDGET or now < _ylast["pause"]:
        return []
    wait = YAHOO_INTERVAL - (now - _ylast["t"])  # 楽天とは別枠
    if wait > 0:
        time.sleep(wait)
    _ylast["t"] = time.monotonic()
    try:
        d = _get(YAHOO_URL, p)
    except urllib.error.HTTPError as e:
        if e.code == 429:  # 上限に達したら2分休む（続けて呼ぶと回復しない）
            _ylast["pause"] = time.monotonic() + 120
        print(f"  Yahoo 取得を省略（{product['jan']}）: HTTP {e.code}")
        return []
    except Exception as e:
        print(f"  Yahoo 取得を省略（{product['jan']}）: {type(e).__name__} {str(e)[:80]}")
        return []
    _ycache[product["jan"]] = d
    return parse_yahoo(d, product["minPrice"])


def parse_yahoo(d, min_price):
    lo = min_price * 0.7
    best = {}
    for h in d.get("hits") or []:
        price = int(h.get("price") or 0)
        url = h.get("url") or ""
        shop = (h.get("seller") or {}).get("name") or ""
        if price <= 0 or not url or not shop or price < lo or h.get("inStock") is False:
            continue
        pt = int((h.get("point") or {}).get("amount") or 0)
        code = (h.get("shipping") or {}).get("code")
        o = {"mall": "yahoo", "shop": shop, "name": h.get("name") or "", "price": price,
             "pointRate": int((h.get("point") or {}).get("times") or 1), "points": pt, "effective": price - pt,
             "shipping": "送料無料" if code == 2 else ("条件付き送料無料" if code == 3 else "送料別"),
             "url": url, "reviewCount": int((h.get("review") or {}).get("count") or 0),
             "reviewAverage": float((h.get("review") or {}).get("rate") or 0),
             "image": (h.get("image") or {}).get("medium") or ""}
        if shop not in best or price < best[shop]["price"]:
            best[shop] = o
    return sorted(best.values(), key=lambda o: o["price"])[:YAHOO_OFFERS]


def fetch_group(group):
    """人気順に製品を見て、ショップが見つかり内容量が読めるものを最大 MAX_PRODUCTS 件。API 呼び出しは製品 MAX_TRIES 件まで。"""
    from build import amount
    prods = []
    cap = int(group.get("max") or MAX_PRODUCTS)  # 売れ筋の大カテゴリは products.json で上限を上げる（11/15）
    for p in search_products(group)[:max(MAX_TRIES, int(cap * 1.5))]:
        if len(prods) >= cap:
            break
        offers = search_offers(p)
        if not offers:
            continue
        a = amount(offers[0]["name"], group["unit"]) or amount(p["name"], group["unit"])  # 買う商品（最安ショップ）の内容量を優先
        if not a or (group.get("min") and a < group["min"]):
            continue  # 内容量が読めない（別ジャンルの商品が混ざることが多い）・大袋の下限未満は載せない
        # 同じ JAN の Yahoo!ショッピングも合わせる（内容量が楽天の最安と同じものだけ＝別サイズの混入を防ぐ）
        ys = [y for y in yahoo_offers(p) if abs((amount(y["name"], group["unit"]) or 0) - a) < 1e-6]
        offers = sorted(offers + ys, key=lambda o: o["price"])
        p = dict(p, group=group["slug"], offers=offers, amount=a)
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


def mall_counts():
    """楽天（ペット・ペットグッズ）と Yahoo!ショッピング（ペット用品、生き物）の掲載件数。トップの「約◯万点から検索」に使う（価格ではない）"""
    out = {}
    try:
        out["r"] = int(_call(ITEM_URL, {"genreId": 101213, "hits": 1, "availability": 1}).get("count") or 0)
    except Exception as e:
        print(f"  楽天の件数を省略: {str(e)[:80]}")
    if os.environ.get("YAHOO_APP_ID"):
        try:
            out["y"] = int(_get(YAHOO_URL, {"appid": os.environ["YAHOO_APP_ID"], "genre_category_id": 2509, "results": 1,
                                            "in_stock": "true"}).get("totalResultsAvailable") or 0)
        except Exception as e:
            print(f"  Yahoo の件数を省略: {str(e)[:80]}")
    print("掲載件数", out)
    return out


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
               "errors": errors, "carried": carried, "mall_counts": mall_counts()},
              open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    offers = sum(len(p["offers"]) for p in products)
    ny = sum(1 for p in products if any(o.get("mall") == "yahoo" for o in p["offers"]))
    print(f"Yahoo!ショッピングの価格あり {ny} 製品（Yahoo の取得 {len(_ycache)} 件）")
    print(f"製品 {len(products)} 件・ショップ {offers} 件（新規取得 {new_count} カテゴリ／前回引き継ぎ {len(carried)} カテゴリ）、エラー {len(errors)} 件")
    for e in errors[:10]:
        print("  ", e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
