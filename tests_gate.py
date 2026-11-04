"""build.py（v2: 商品ごとの最安ショップとポイント）の表示テスト。標準ライブラリのみ。python3 tests_gate.py
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


def run(data):
    d = tempfile.mkdtemp()
    shutil.copy(os.path.join(HERE, "build.py"), d)
    shutil.copy(os.path.join(HERE, "products.json"), d)
    os.makedirs(os.path.join(d, "data"))
    json.dump(data, open(os.path.join(d, "data", "prices.json"), "w", encoding="utf-8"), ensure_ascii=False)
    os.makedirs(os.path.join(d, "site"))
    open(os.path.join(d, "site", "item-old.html"), "w").write("old")  # 前回の古いページ
    p = subprocess.run([sys.executable, "build.py"], cwd=d, capture_output=True, text=True)
    return d, p


def offer(shop, price, rate=1, ship="送料別", name="x"):
    pt = int(int(price / 1.1) * rate // 100)
    return dict(shop=shop, name=name, price=price, pointRate=rate, points=pt, effective=price - pt, shipping=ship,
                url=f"https://hb.afl.rakuten.co.jp/hgc/x/?pc={shop}", reviewCount=1, reviewAverage=4.0, image="")


def prod(group, pid, name, offers, rc=10):
    return dict(productId=pid, name=name, jan="", maker="M", image="", minPrice=min(o["price"] for o in offers), itemCount=len(offers),
                reviewCount=rc, reviewAverage=4.5, productUrl="https://hb.afl.rakuten.co.jp/hgc/x/?pc=product", group=group, offers=offers)


def prod_data():
    ps = []
    for i in range(3):  # 猫砂: 3商品 → 公開
        ps.append(prod("cat-litter-tofu", f"lit{i}", f"おからの猫砂 7L×{i + 1}袋",
                       [offer("A店", 1000 * (i + 1)), offer("B店", 1050 * (i + 1), 10, "送料無料")], rc=100 - i))
    ps.append(prod("goldfish-food", "gf0", "金魚の餌 200g", [offer("A店", 500)]))  # 1商品 → 不公開
    return {"fetched_at": "2026-11-04 05:17 JST", "products": ps}


class T(unittest.TestCase):
    def test_sample_all(self):
        data = json.load(open(os.path.join(HERE, "data", "sample_prices.json"), encoding="utf-8"))
        d, p = run(data)
        self.assertEqual(p.returncode, 0, p.stderr)
        site = os.path.join(d, "site")
        self.assertFalse(os.path.exists(os.path.join(site, "item-old.html")))
        idx = open(os.path.join(site, "index.html"), encoding="utf-8").read()
        self.assertIn("【サンプル表示】", idx)

    def test_prod(self):
        d, p = run(prod_data())
        self.assertEqual(p.returncode, 0, p.stderr)
        site = os.path.join(d, "site")
        self.assertIn("不公開 goldfish-food", p.stdout)
        self.assertFalse(os.path.exists(os.path.join(site, "goldfish-food.html")))
        idx = open(os.path.join(site, "index.html"), encoding="utf-8").read()
        cat = open(os.path.join(site, "cat-litter-tofu.html"), encoding="utf-8").read()
        item = open(os.path.join(site, "item-lit1.html"), encoding="utf-8").read()
        for h in (idx, cat, item):  # 規約まわり（2026-11-03 規約チェック）
            self.assertLess(h.index("本ページはプロモーション"), h.index("<main>"))
            self.assertIn('<a href="https://developers.rakuten.com/" target="_blank">Supported by Rakuten Developers</a>', h)
            self.assertIn("AI〈一郎〉", h)
            self.assertIn("楽天グループ株式会社とは関係ありません", h)
            self.assertNotIn("【サンプル表示】", h)
            self.assertNotIn("Yahoo", h)
        self.assertIn('href="cat-litter-tofu.html"', idx)
        self.assertEqual(len(re.findall(r'href="item-lit\d\.html"', cat)), 3)
        # 商品ページ: 最安＝2,000円（A店）、ポイント込みは B店（10倍）が安い
        self.assertIn("今回取得した2ショップ中の最安値", item)
        self.assertIn("<b>2,000</b>円", item)
        self.assertIn("ポイント込みなら <b>B店</b>", item)
        self.assertEqual(item.count('rel="sponsored nofollow"'), 4)  # 最安ボタン＋ショップ2件＋全ショップ
        self.assertIn("1Lあたり", item)
        self.assertNotIn("最安値挑戦", item)

    def test_prod_all_fail(self):
        d, p = run({"fetched_at": "2026-11-04 05:17 JST", "products": [prod("goldfish-food", "g", "金魚 200g", [offer("A", 1)])]})
        self.assertEqual(p.returncode, 1)
        self.assertFalse(os.path.exists(os.path.join(d, "site", "index.html")))

    def test_points(self):
        sys.path.insert(0, HERE)
        from fetch_prices import points
        self.assertEqual(points(1100, 1), 10)
        self.assertEqual(points(1100, 10), 100)
        self.assertEqual(points(1029, 1), 9)


if __name__ == "__main__":
    unittest.main(verbosity=1)
