"""build.py の公開判定・priority 順・前回取得注記・単価の信頼表示のテスト（標準ライブラリのみ）。python3 tests_gate.py
本物の data/prices.json・site/ は触らず、一時ディレクトリに build.py をコピーして実行する。"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def run(data, keep_min=True):
    d = tempfile.mkdtemp()
    shutil.copy(os.path.join(HERE, "build.py"), d)
    pj = json.load(open(os.path.join(HERE, "products.json"), encoding="utf-8"))
    if not keep_min:  # 公開判定のテストは min（大袋の下限）を外して数える
        for g in pj["groups"]:
            g.pop("min", None)
    json.dump(pj, open(os.path.join(d, "products.json"), "w", encoding="utf-8"), ensure_ascii=False)
    os.makedirs(os.path.join(d, "data"))
    with open(os.path.join(d, "data", "prices.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.makedirs(os.path.join(d, "site"))
    open(os.path.join(d, "site", "goldfish-food.html"), "w").write("old")  # 前回の古いページ
    p = subprocess.run([sys.executable, "build.py"], cwd=d, capture_output=True, text=True)
    return d, p


def item(group, name, price=1000, shop="rakuten", **kw):
    return dict(group=group, shop=shop, name=name, price=price, shipping="送料無料", url="https://example.com/x", image="", **kw)


def prod_data():
    items = []
    groups = json.load(open(os.path.join(HERE, "products.json"), encoding="utf-8"))["groups"]
    for g in groups:
        u = {"kg": "kg", "g": "g", "L": "L", "枚": "枚", "匹": "匹"}[g["unit"]]
        for i in range(1, 5):  # 4件すべて読める＝解析率100%
            items.append(item(g["slug"], f"テスト {g['title']} {i * 2}{u}", 1000 * i))
    # 猫砂: 読める4件＋読めない3件 → 4/7=57% → 不公開
    items += [item("cat-litter-tofu", f"猫砂 Lサイズ 徳用 {k}") for k in "ABC"]
    # 金魚: 2件だけ → 表示<3 → 不公開
    items = [it for it in items if not (it["group"] == "goldfish-food" and it["name"].endswith(("6g", "8g")))]
    # 犬シーツ: 1件だけ前回取得
    items.append(item("dog-pee-sheet-regular", "前回の シーツ 300枚", 900, fetched_at="2026-11-01 06:00 JST"))
    return {"fetched_at": "2026-11-02 06:00 JST", "items": items}


class T(unittest.TestCase):
    def test_sample_all(self):
        data = json.load(open(os.path.join(HERE, "data", "sample_prices.json"), encoding="utf-8"))
        d, p = run(data)
        self.assertEqual(p.returncode, 0, p.stderr)
        pages = [f for f in os.listdir(os.path.join(d, "site")) if f.endswith(".html")]
        self.assertEqual(len(pages), 26)  # 20カテゴリ＋ペット5＋index

    def test_trust_display(self):
        data = json.load(open(os.path.join(HERE, "data", "sample_prices.json"), encoding="utf-8"))
        d, p = run(data)
        site = os.path.join(d, "site")
        groups = json.load(open(os.path.join(HERE, "products.json"), encoding="utf-8"))["groups"]
        for g in groups:
            h = open(os.path.join(site, g["slug"] + ".html"), encoding="utf-8").read()
            self.assertIn("税込価格 ÷ 内容量の合計", h, g["slug"])
            self.assertIn("価格取得日時:", h, g["slug"])
            self.assertLess(h.index('class="best"'), h.index("class=trust"), g["slug"])  # いちばん安い商品が先
            self.assertIn('class="ship ', h, g["slug"])
        litter = open(os.path.join(site, "cat-litter-tofu.html"), encoding="utf-8").read()
        self.assertIn('<span class="ship free">送料無料</span>', litter)
        self.assertIn('<span class="ship paid">送料別</span>', litter)
        self.assertIn("内容量 42L（7L×6袋）", litter)  # 合計＝式の分母、内訳を併記
        self.assertIn("119<small>円", litter)  # 4980÷42＝118.6（100円以上は整数表示）
        self.assertIn('rel="sponsored nofollow"', litter)
        sheet = open(os.path.join(site, "dog-pee-sheet-regular.html"), encoding="utf-8").read()
        self.assertIn('<span class="ship cond">条件付き送料無料</span>', sheet)
        deo = open(os.path.join(site, "pet-deodorizer-refill.html"), encoding="utf-8").read()
        self.assertIn("1.2L", deo)  # 400ml×3 の浮動小数誤差を丸める
        self.assertNotIn("1.2000", deo)
        self.assertNotIn("最安値", litter)  # 比較範囲を限定しない「最安」表現は使わない
        self.assertIn("今回取得した2件のうち", litter)
        idx = open(os.path.join(site, "index.html"), encoding="utf-8").read()
        self.assertIn("価格取得日時:", idx)
        for h in (idx, litter):  # 規約チェック 2026-11-03
            self.assertLess(h.index("本ページはプロモーション"), h.index("<main>"))  # ファーストビュー
            self.assertIn('<a href="https://developers.rakuten.com/" target="_blank">Supported by Rakuten Developers</a>', h)
        self.assertIn("税込価格 ÷ 内容量の合計", idx)

    def test_prod_gate(self):
        d, p = run(prod_data(), keep_min=False)
        self.assertEqual(p.returncode, 0, p.stderr)
        site = os.path.join(d, "site")
        self.assertFalse(os.path.exists(os.path.join(site, "cat-litter-tofu.html")))
        self.assertFalse(os.path.exists(os.path.join(site, "goldfish-food.html")))  # 古いページも消える
        self.assertIn("不公開 cat-litter-tofu: 解析率57% < 70%", p.stdout)
        self.assertIn("不公開 goldfish-food: 表示2件 < 3件", p.stdout)
        idx = open(os.path.join(site, "index.html"), encoding="utf-8").read()
        self.assertNotIn("【サンプル表示】", idx)
        self.assertIn("2026-11-02 06:00 JST", idx)
        cats = set()
        for f in os.listdir(site):
            if f.startswith("pet-"):
                h = open(os.path.join(site, f), encoding="utf-8").read()
                cats |= set(re.findall(r'class="cat" href="([a-z-]+)\.html"', h))
        self.assertNotIn("cat-litter-tofu", cats)
        self.assertEqual(len(cats), 18)
        dog = open(os.path.join(site, "pet-dog.html"), encoding="utf-8").read()
        hrefs = re.findall(r'class="cat" href="([a-z-]+)\.html"', dog)
        self.assertEqual(set(hrefs[:3]), {"dog-food-large", "dog-pee-sheet-regular", "pet-deodorizer-refill"})  # priority 1 が先頭
        sheet = open(os.path.join(site, "dog-pee-sheet-regular.html"), encoding="utf-8").read()
        self.assertIn("一部前回取得: 2026-11-01 06:00 JST", sheet)
        food = open(os.path.join(site, "dog-food-large.html"), encoding="utf-8").read()
        self.assertNotIn("一部前回取得", food)
        for h in (idx, food):  # 楽天だけ取得した本番: 未提携の Yahoo!/VC 表記を出さない（規約チェック 2026-11-03）
            self.assertNotIn("バリューコマース", h)
            self.assertNotIn("Yahoo! JAPAN", h)
            self.assertIn("本ページはプロモーション", h)

    def test_prod_all_fail(self):
        data = {"fetched_at": "2026-11-02 06:00 JST", "items": [item("dog-food-large", "読めない フード")]}
        d, p = run(data)
        self.assertEqual(p.returncode, 1)
        self.assertFalse(os.path.exists(os.path.join(d, "site", "index.html")))


if __name__ == "__main__":
    unittest.main(verbosity=1)
