"""価格の履歴を data/history.json に積む（2026-11-05 Taku指示「このタイミングで買えてよかった」の土台）。

- 入力: data/prices.json（fetch_prices.py v2 の形。products[].offers[]）
- 出力: data/history.json = {"days": 90, "items": {productId: [[日付, 最安価格, ポイント込み最安, ショップ数, 送料無料の最安 or null], ...]}}
  1日1行（同じ日に複数回実行したら上書き）。90日より古い行は捨てる。
- 使い方: python3 record_history.py            … prices.json の内容を今日（fetched_at の日付）として記録
          python3 record_history.py --backfill … git の履歴にある prices.json をすべて読んで記録（初回のみ）
- 価格は「取得した範囲の中の最安」。引き継ぎ（carried）の商品はその日の値として記録しない。
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DST = os.path.join(ROOT, "data", "history.json")
DAYS = 90


def load():
    try:
        return json.load(open(DST, encoding="utf-8"))
    except (OSError, ValueError):
        return {"days": DAYS, "items": {}}


def rows_from(data):
    """prices.json の中身 → {productId: [日付, 最安, ポイント込み最安, ショップ数, 送料無料の最安]}"""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(data.get("fetched_at", "")))
    if not m or not data.get("products"):
        return None, {}
    day = m.group(1)
    out = {}
    for p in data["products"]:
        if p.get("fetched_at") or not p.get("offers"):  # 前回引き継ぎはその日の価格ではない
            continue
        offers = p["offers"]
        free = [o["price"] for o in offers if o.get("shipping") == "送料無料"]
        out[p["productId"]] = [day, min(o["price"] for o in offers), min(o["effective"] for o in offers), len(offers),
                               min(free) if free else None]
    return day, out


def merge(hist, day, rows):
    items = hist.setdefault("items", {})
    for pid, row in rows.items():
        lst = [r for r in items.get(pid, []) if r[0] != day]
        lst.append(row)
        lst.sort(key=lambda r: r[0])
        items[pid] = lst[-DAYS:]
    return hist


def main():
    hist = load()
    if "--backfill" in sys.argv:
        shas = subprocess.run(["git", "log", "--format=%H", "--reverse", "--", "data/prices.json"], cwd=ROOT,
                              capture_output=True, text=True).stdout.split()
        n = 0
        for sha in shas:
            raw = subprocess.run(["git", "show", f"{sha}:data/prices.json"], cwd=ROOT, capture_output=True, text=True).stdout
            try:
                day, rows = rows_from(json.loads(raw))
            except ValueError:
                continue
            if day and rows:
                merge(hist, day, rows)
                n += 1
        print(f"backfill: {n} 回分を取り込み")
    day, rows = rows_from(json.load(open(os.path.join(ROOT, "data", "prices.json"), encoding="utf-8")))
    if not day:
        print("::warning::prices.json に記録できるデータがありません（履歴は変更なし）", file=sys.stderr)
        return 0
    merge(hist, day, rows)
    hist["days"] = DAYS
    json.dump(hist, open(DST, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    days = sorted({r[0] for v in hist["items"].values() for r in v})
    print(f"履歴: {len(hist['items'])} 商品・{len(days)} 日分（{days[0]}〜{days[-1]}）、今日 {day} は {len(rows)} 商品")
    return 0


if __name__ == "__main__":
    sys.exit(main())
