"""価格取得スクリプト（楽天市場商品検索API／Yahoo!ショッピング商品検索API v3）。

- 環境変数が未設定なら API を呼ばず data/sample_prices.json をそのまま使う（開発時のみ。外部通信なし）。
- 設定済みなら products.json の各グループを検索し data/prices.json に保存する。
  RAKUTEN_APP_ID, RAKUTEN_ACCESS_KEY, RAKUTEN_AFFILIATE_ID
  YAHOO_APP_ID, VC_AFFILIATE_ID（バリューコマース。未設定ならアフィリエイトなしの URL）
- 楽天の新エンドポイント（openapi.rakuten.co.jp, 20220601 版, accessKey 必須）は
  Referer ではなく Origin ヘッダーが必要という報告あり（要・本番キーで確認）。

2026-11-02 失敗モード対策（Actions で空サイト・架空データを公開しない）:
- 本番判定: 環境変数 PETSITE_PROD=1（update.yml で設定）。
- 本番で楽天 Secrets（RAKUTEN_APP_ID / RAKUTEN_ACCESS_KEY）が未設定 → 明示エラーで終了（exit 2）。サンプルに落とさない。
- API は店ごとに 1.1 秒以上の間隔を空ける（楽天・Yahoo とも 1req/秒の目安）。HTTP 429/5xx は 1 回だけ待って再試行。
- カテゴリ単位で失敗・0件なら、前回 data/prices.json の同カテゴリの商品を引き継ぐ（carried に記録）。
- 全体で新規取得 0 件 → 前回の本番データがあればそのまま残して exit 0（サイトは前回データで再生成）、
  前回が無い／サンプルなら exit 1（公開しない）。
- 終了コード: 0=正常（引き継ぎ含む） 1=公開できるデータなし 2=本番の設定不備
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
JST = timezone(timedelta(hours=9))
RAKUTEN_URL = os.environ.get("RAKUTEN_URL", "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260401")  # 2026-11-03: 20220601 は新規アプリで「API Configuration not found」
YAHOO_URL = "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"
SITE_ORIGIN = os.environ.get("SITE_ORIGIN", "https://example.com")
HITS = 20
INTERVAL = 1.1  # 秒。店ごとの最小リクエスト間隔
_last = {}


def _throttle(shop):
    wait = INTERVAL - (time.monotonic() - _last.get(shop, -1e9))
    if wait > 0:
        time.sleep(wait)
    _last[shop] = time.monotonic()


def _get(url, params, headers=None):
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers=headers or {})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _call(shop, url, params, headers=None):
    """1req/秒を守って呼ぶ。429・5xx だけ 1 回再試行（認証エラー等は即失敗）。"""
    for attempt in (1, 2):
        _throttle(shop)
        try:
            return _get(url, params, headers)
        except urllib.error.HTTPError as e:
            if attempt == 1 and (e.code == 429 or e.code >= 500):
                time.sleep(3)
                continue
            # 原因切り分け用に API の返したエラー本文（先頭200字）を付ける。キーは本文に含まれない
            try:
                body = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                body = ""
            raise RuntimeError(f"HTTP {e.code} {body}") from e


def fetch_rakuten(group):
    p = {
        "format": "json", "formatVersion": 2,
        "applicationId": os.environ["RAKUTEN_APP_ID"],
        "accessKey": os.environ["RAKUTEN_ACCESS_KEY"],
        "keyword": group["keyword"], "hits": HITS, "sort": "+itemPrice",
        "availability": 1,
    }
    if os.environ.get("RAKUTEN_AFFILIATE_ID"):
        p["affiliateId"] = os.environ["RAKUTEN_AFFILIATE_ID"]
    d = _call("rakuten", RAKUTEN_URL, p, {"Origin": SITE_ORIGIN})
    if "Items" not in d:
        raise RuntimeError(f"想定外の応答形式: keys={list(d)[:10]}")
    out = []
    for it in d.get("Items", []):
        out.append({
            "group": group["slug"], "shop": "rakuten", "name": it.get("itemName", ""),
            "price": int(it.get("itemPrice", 0)),
            "shipping": "送料無料" if it.get("postageFlag") == 0 else "送料別",
            "url": it.get("affiliateUrl") or it.get("itemUrl", ""),
            "image": (it.get("mediumImageUrls") or [""])[0],
        })
    return out


def fetch_yahoo(group):
    p = {"appid": os.environ["YAHOO_APP_ID"], "query": group["keyword"],
         "results": HITS, "sort": "+price", "in_stock": "true"}
    if os.environ.get("VC_AFFILIATE_ID"):
        p.update({"affiliate_type": "vc", "affiliate_id": os.environ["VC_AFFILIATE_ID"]})
    d = _call("yahoo", YAHOO_URL, p)
    ship = {"1": "送料別", "2": "送料無料", "3": "条件付き送料無料"}
    out = []
    for it in d.get("hits", []):
        out.append({
            "group": group["slug"], "shop": "yahoo", "name": it.get("name", ""),
            "price": int(it.get("price", 0)),
            "shipping": ship.get(str((it.get("shipping") or {}).get("code")), "不明"),
            "url": it.get("url", ""),
            "image": (it.get("image") or {}).get("medium", ""),
        })
    return out


def _load_prev(dst):
    """前回の本番データ（サンプルでないもの）を返す。無ければ None。"""
    try:
        d = json.load(open(dst, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if str(d.get("fetched_at", "")).startswith("SAMPLE") or not d.get("items"):
        return None
    return d


def _valid(it):
    return it.get("name") and it.get("url") and isinstance(it.get("price"), int) and it["price"] > 0


def main():
    groups = json.load(open(os.path.join(ROOT, "products.json"), encoding="utf-8"))["groups"]
    prod = os.environ.get("PETSITE_PROD") == "1"
    use_r = all(os.environ.get(k) for k in ("RAKUTEN_APP_ID", "RAKUTEN_ACCESS_KEY"))
    use_y = bool(os.environ.get("YAHOO_APP_ID"))
    dst = os.path.join(ROOT, "data", "prices.json")
    if prod and not use_r:
        missing = [k for k in ("RAKUTEN_APP_ID", "RAKUTEN_ACCESS_KEY") if not os.environ.get(k)]
        print(f"::error::本番実行なのに楽天 Secrets が未設定: {', '.join(missing)}。"
              "リポジトリの Settings → Secrets and variables → Actions に登録してください（サンプルでは公開しません）",
              file=sys.stderr)
        return 2
    if not (use_r or use_y):
        src = os.path.join(ROOT, "data", "sample_prices.json")
        json.dump(json.load(open(src, encoding="utf-8")), open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("API キー未設定: サンプルデータを使用（開発用。本番では使われない）")
        return 0
    prev = _load_prev(dst)
    items, errors, carried = [], [], []
    for g in groups:
        got = []
        for ok, fn, name in ((use_r, fetch_rakuten, "rakuten"), (use_y, fetch_yahoo, "yahoo")):
            if not ok:
                continue
            try:
                got += [it for it in fn(g) if _valid(it)]
            except Exception as e:  # 1グループの失敗で全体を止めない
                errors.append(f"{name}/{g['slug']}: {type(e).__name__}: {e}")
        if not got and prev:
            old = [it for it in prev["items"] if it.get("group") == g["slug"]]
            if old:
                carried.append(g["slug"])
                # 引き継いだ商品には元の取得日時を残す（新しい日時で上書きしない）
                got = [dict(it, fetched_at=it.get("fetched_at") or prev.get("fetched_at")) for it in old]
        items += got
    new_count = sum(1 for g in groups if g["slug"] not in carried and any(it["group"] == g["slug"] for it in items))
    if new_count == 0:
        if prev:
            print(f"::warning::新規取得 0 件。前回データ（{prev.get('fetched_at')}）を残して再生成します",
                  errors[:5], file=sys.stderr)
            return 0
        print("::error::取得 0 件かつ前回の本番データなし。公開しません", errors[:5], file=sys.stderr)
        return 1
    json.dump({"fetched_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"), "items": items,
               "errors": errors, "carried": carried},
              open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{len(items)} 件（新規取得 {new_count} カテゴリ／前回引き継ぎ {len(carried)} カテゴリ）、エラー {len(errors)} 件")
    for e in errors[:10]:
        print("  ", e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
