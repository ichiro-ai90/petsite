"""fetch_prices.py の失敗モードテスト（外部通信なし。_get を差し替える）。python3 tests_fetch.py"""
import importlib, io, json, os, shutil, sys, tempfile, time, urllib.error
from contextlib import redirect_stdout, redirect_stderr

HERE = os.path.dirname(os.path.abspath(__file__))
GROUPS = {"groups": [{"slug": "a", "keyword": "A", "unit": "kg"}, {"slug": "b", "keyword": "B", "unit": "kg"}]}
SAMPLE = {"fetched_at": "SAMPLE（架空）", "items": [{"group": "a", "shop": "rakuten", "name": "【サンプル】1kg", "price": 100, "shipping": "送料別", "url": "#", "image": ""}]}
PREV = {"fetched_at": "2026-11-01 05:17 JST", "items": [
    {"group": "a", "shop": "rakuten", "name": "旧A 1kg", "price": 500, "shipping": "送料別", "url": "https://x/a", "image": ""},
    {"group": "b", "shop": "rakuten", "name": "旧B 2kg", "price": 900, "shipping": "送料別", "url": "https://x/b", "image": ""}]}
ENV_KEYS = ["PETSITE_PROD", "RAKUTEN_APP_ID", "RAKUTEN_ACCESS_KEY", "RAKUTEN_AFFILIATE_ID", "YAHOO_APP_ID", "VC_AFFILIATE_ID"]


def setup(prev=None):
    d = tempfile.mkdtemp()
    shutil.copy(os.path.join(HERE, "fetch_prices.py"), d)
    os.makedirs(os.path.join(d, "data"))
    json.dump(GROUPS, open(os.path.join(d, "products.json"), "w"))
    json.dump(SAMPLE, open(os.path.join(d, "data", "sample_prices.json"), "w"))
    if prev is not None:
        json.dump(prev, open(os.path.join(d, "data", "prices.json"), "w"))
    sys.path.insert(0, d)
    sys.modules.pop("fetch_prices", None)
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
            calls.append((time.monotonic(), url))
            return getter(url, params)
        m._get = g
    m.INTERVAL = 0.05
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = m.main()
    return rc, out.getvalue() + err.getvalue(), calls


def load(d):
    return json.load(open(os.path.join(d, "data", "prices.json")))


R = {"RAKUTEN_APP_ID": "x", "RAKUTEN_ACCESS_KEY": "y"}
ok_items = lambda url, p: {"Items": [{"itemName": p["keyword"] + " 新 3kg", "itemPrice": 1200, "postageFlag": 0, "itemUrl": "https://r/" + p["keyword"]}]}
fails = 0


def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    fails += 0 if cond else 1


# 1 開発時（キーなし・非本番）→ サンプル
d, m = setup(); rc, log, _ = run(m, {})
check("dev: キーなしはサンプル exit0", rc == 0 and load(d)["fetched_at"].startswith("SAMPLE"))
# 2 本番・Secrets 未設定 → exit2、prices.json を書かない
d, m = setup(PREV); rc, log, _ = run(m, {"PETSITE_PROD": "1"})
check("prod: Secrets 未設定は exit2・前回データ維持", rc == 2 and "RAKUTEN_APP_ID" in log and load(d) == PREV)
d, m = setup(); rc, log, _ = run(m, {"PETSITE_PROD": "1", "YAHOO_APP_ID": "z"})
check("prod: Yahoo だけ設定でも楽天なしは exit2", rc == 2 and not os.path.exists(os.path.join(d, "data", "prices.json")))
# 3 正常 → 新データ、1req/秒間隔
d, m = setup(PREV); rc, log, calls = run(m, dict(R, PETSITE_PROD="1"), ok_items)
gaps = [b[0] - a[0] for a, b in zip(calls, calls[1:])]
check("prod: 正常取得 exit0・2カテゴリ新規", rc == 0 and len(load(d)["items"]) == 2 and load(d)["carried"] == [])
check("楽天は間隔 INTERVAL 以上", len(calls) == 2 and all(g >= 0.049 for g in gaps))
# 4 全失敗（認証エラー）＋前回あり → exit0・前回維持
def auth_err(url, p): raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
d, m = setup(PREV); rc, log, calls = run(m, dict(R, PETSITE_PROD="1"), auth_err)
check("prod: 全失敗・前回あり → exit0・前回データそのまま", rc == 0 and load(d) == PREV and len(calls) == 2)
# 5 全失敗＋前回なし → exit1
d, m = setup(); rc, log, _ = run(m, dict(R, PETSITE_PROD="1"), auth_err)
check("prod: 全失敗・前回なし → exit1・書き込みなし", rc == 1 and not os.path.exists(os.path.join(d, "data", "prices.json")))
# 6 全失敗＋前回がサンプル → exit1（サンプルを本番扱いしない）
d, m = setup(SAMPLE); rc, log, _ = run(m, dict(R, PETSITE_PROD="1"), auth_err)
check("prod: 前回がサンプルなら引き継がず exit1", rc == 1 and load(d) == SAMPLE)
# 7 一部カテゴリ0件 → そのカテゴリだけ前回引き継ぎ、元の取得日時を保持
def b_empty(url, p): return {"Items": []} if p["keyword"] == "B" else ok_items(url, p)
d, m = setup(PREV); rc, log, _ = run(m, dict(R, PETSITE_PROD="1"), b_empty)
out = load(d); bi = [i for i in out["items"] if i["group"] == "b"]
check("prod: B だけ0件 → B は前回引き継ぎ（元日時つき）", rc == 0 and out["carried"] == ["b"] and bi and bi[0]["fetched_at"] == PREV["fetched_at"])
# 8 429 → 1回再試行で成功
n = {"c": 0}
def flaky(url, p):
    n["c"] += 1
    if n["c"] == 1: raise urllib.error.HTTPError(url, 429, "Too Many", {}, None)
    return ok_items(url, p)
d, m = setup(PREV)
m.time = type("T", (), {"sleep": staticmethod(lambda s: None), "monotonic": staticmethod(time.monotonic)})  # 再試行待ちを飛ばす
rc, log, calls = run(m, dict(R, PETSITE_PROD="1"), flaky)
check("429 は1回再試行して成功", rc == 0 and len(calls) == 3 and load(d)["carried"] == [])
# 9 価格0・URLなしの不正行は捨てる
def bad(url, p): return {"Items": [{"itemName": "x 1kg", "itemPrice": 0, "itemUrl": "https://r"}, {"itemName": "y 1kg", "itemPrice": 300, "itemUrl": ""}]}
d, m = setup(PREV); rc, log, _ = run(m, dict(R, PETSITE_PROD="1"), bad)
check("不正行（価格0・URL空）は捨て、全カテゴリ前回引き継ぎ", rc == 0 and load(d) == PREV)

print("FAILS", fails)
sys.exit(1 if fails else 0)
