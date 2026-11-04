"""fetch_prices.py（v2）の失敗モードテスト。外部通信なし（_get を差し替える）。python3 tests_fetch.py"""
import importlib, io, json, os, shutil, sys, tempfile, urllib.error
from contextlib import redirect_stdout, redirect_stderr

HERE = os.path.dirname(os.path.abspath(__file__))
GROUPS = {"groups": [{"slug": "a", "keyword": "A", "pkeyword": "A", "unit": "kg"},
                     {"slug": "b", "keyword": "B", "pkeyword": "B", "unit": "kg"}]}
SAMPLE = {"fetched_at": "SAMPLE（架空）", "products": [{"group": "a", "name": "s 1kg", "offers": [{"price": 1}]}]}
PREV = {"fetched_at": "2026-11-03 05:17 JST", "products": [
    {"group": "a", "productId": "pa", "name": "旧A 1kg", "offers": [{"price": 500}]},
    {"group": "b", "productId": "pb", "name": "旧B 2kg", "offers": [{"price": 900}]}]}
ENV_KEYS = ["PETSITE_PROD", "RAKUTEN_APP_ID", "RAKUTEN_ACCESS_KEY", "RAKUTEN_AFFILIATE_ID"]
R = {"RAKUTEN_APP_ID": "x", "RAKUTEN_ACCESS_KEY": "y", "PETSITE_PROD": "1"}


def setup(prev=None):
    d = tempfile.mkdtemp()
    for f in ("fetch_prices.py", "build.py"):
        shutil.copy(os.path.join(HERE, f), d)
    os.makedirs(os.path.join(d, "data"))
    json.dump(GROUPS, open(os.path.join(d, "products.json"), "w"))
    json.dump(SAMPLE, open(os.path.join(d, "data", "sample_prices.json"), "w"))
    if prev is not None:
        json.dump(prev, open(os.path.join(d, "data", "prices.json"), "w"))
    sys.path.insert(0, d)
    for m in ("fetch_prices", "build"):
        sys.modules.pop(m, None)
    m = importlib.import_module("fetch_prices")
    sys.path.pop(0)
    return d, m


def run(m, env, getter=None):
    for k in ENV_KEYS:
        os.environ.pop(k, None)
    os.environ.update(env)
    calls = []
    if getter:
        def g(url, params, headers=None):
            calls.append((url, dict(params)))
            return getter(url, params)
        m._get = g
    m.INTERVAL = 0
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = m.main()
    return rc, out.getvalue() + err.getvalue(), calls


def load(d):
    return json.load(open(os.path.join(d, "data", "prices.json")))


def ok(url, p):
    if "Product" in url:
        return {"Products": [{"productId": "p" + p["keyword"], "productCode": "4900000000001", "productName": p["keyword"] + " 製品 3kg",
                              "salesMinPrice": 1000, "salesItemCount": 5, "reviewCount": 9, "reviewAverage": 4.2}]}
    return {"Items": [{"itemName": "店1 3kg", "itemPrice": 1100, "pointRate": 10, "postageFlag": 0, "shopName": "S1", "affiliateUrl": "https://afl/1"},
                      {"itemName": "店2 3kg", "itemPrice": 1000, "pointRate": 1, "postageFlag": 1, "shopName": "S2", "affiliateUrl": "https://afl/2"},
                      {"itemName": "付属品", "itemPrice": 100, "shopName": "S3", "affiliateUrl": "https://afl/3"}]}


fails = 0


def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    fails += 0 if cond else 1


d, m = setup(); rc, log, _ = run(m, {})
check("dev: キーなしはサンプル exit0", rc == 0 and load(d)["fetched_at"].startswith("SAMPLE"))
d, m = setup(PREV); rc, log, _ = run(m, {"PETSITE_PROD": "1"})
check("prod: Secrets 未設定は exit2・前回データ維持", rc == 2 and load(d) == PREV)
d, m = setup(PREV); rc, log, calls = run(m, R, ok)
out = load(d)
pa = [p for p in out["products"] if p["group"] == "a"][0]
check("prod: 正常取得 2カテゴリ", rc == 0 and len(out["products"]) == 2 and out["carried"] == [])
check("JAN でショップ検索・安い順", any(c[1].get("keyword") == "4900000000001" and c[1].get("sort") == "+itemPrice" for c in calls))
check("付属品（製品最安の7割未満）を除外・安い順", [o["shop"] for o in pa["offers"]] == ["S2", "S1"])
check("ポイントと実質価格", pa["offers"][1]["points"] == 100 and pa["offers"][1]["effective"] == 1000 and pa["amount"] == 3)
check("Origin・accessKey・affiliateId なしでも可", all(c[1].get("accessKey") == "y" for c in calls))
def auth_err(url, p): raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
d, m = setup(PREV); rc, log, _ = run(m, R, auth_err)
check("全失敗・前回あり → exit0・前回データそのまま", rc == 0 and load(d) == PREV)
d, m = setup(); rc, log, _ = run(m, R, auth_err)
check("全失敗・前回なし → exit1", rc == 1 and not os.path.exists(os.path.join(d, "data", "prices.json")))
d, m = setup(SAMPLE); rc, log, _ = run(m, R, auth_err)
check("前回がサンプルなら引き継がず exit1", rc == 1 and load(d) == SAMPLE)
def b_fail(url, p):
    if p.get("keyword") == "B":
        raise urllib.error.HTTPError(url, 400, "Bad", {}, None)
    return ok(url, p)
d, m = setup(PREV); rc, log, _ = run(m, R, b_fail)
out = load(d); pb = [p for p in out["products"] if p["group"] == "b"]
check("B だけ失敗 → B は前回引き継ぎ（元日時つき）", rc == 0 and out["carried"] == ["b"] and pb and pb[0]["fetched_at"] == PREV["fetched_at"])

print("FAILS", fails)
sys.exit(1 if fails else 0)
