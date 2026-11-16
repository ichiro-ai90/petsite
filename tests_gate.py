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


def run(data, env=None):
    d = tempfile.mkdtemp()
    shutil.copy(os.path.join(HERE, "build.py"), d)
    shutil.copy(os.path.join(HERE, "products.json"), d)
    shutil.copy(os.path.join(HERE, "concerns.json"), d)
    shutil.copy(os.path.join(HERE, "brands.json"), d)
    os.makedirs(os.path.join(d, "data"))
    json.dump(data, open(os.path.join(d, "data", "prices.json"), "w", encoding="utf-8"), ensure_ascii=False)
    os.makedirs(os.path.join(d, "site"))
    open(os.path.join(d, "site", "item-old.html"), "w").write("old")  # 前回の古いページ
    p = subprocess.run([sys.executable, "build.py"], cwd=d, capture_output=True, text=True, env=dict(os.environ, **(env or {})))
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
            self.assertIn("購入時に楽天市場店舗（www.rakuten.co.jp）に表示されている価格が、その商品の販売に適用されます。", h)  # 楽天の免責（各ページ）
            self.assertIn('href="about.html"', h)
            self.assertNotIn("【サンプル表示】", h)
            self.assertNotIn("Yahoo", h)
        self.assertIn('href="cat-litter-tofu.html"', idx)
        self.assertEqual(len(re.findall(r'href="item-lit\d\.html"', cat)), 3)
        # 商品ページ: 最安＝2,000円（A店）、ポイント込みは B店（10倍）が安い
        self.assertIn("今回取得した2ショップ中の最安値", item)
        self.assertIn("<b>2,000</b>円", item)
        self.assertIn("の店：<b>B店</b>", item)
        self.assertEqual(item.count('rel="sponsored nofollow"'), 4)  # 最安ボタン＋ショップ2件＋全ショップ
        self.assertIn("1Lあたり", item)
        self.assertIn("楽天カード払いなら <b>+約38pt</b>", item)  # 2,000円：通常分20＋特典分（税抜1,818円）18
        self.assertNotIn("最安値挑戦", item)
        sj = json.load(open(os.path.join(site, "search.json"), encoding="utf-8"))  # 検索・お気に入り用データ
        self.assertEqual(len(sj["items"]), 3)
        self.assertEqual({k for k in ("id", "n", "m", "c", "p", "e", "pt") if k in sj["items"][0]}, {"id", "n", "m", "c", "p", "e", "pt"})
        for f in ("search.html", "favorites.html", "app.js"):
            self.assertTrue(os.path.exists(os.path.join(site, f)), f)
        self.assertIn('data-fav="lit1"', item)
        self.assertIn('action="search.html"', idx)

    def test_prod_all_fail(self):
        d, p = run({"fetched_at": "2026-11-04 05:17 JST", "products": [prod("goldfish-food", "g", "金魚 200g", [offer("A", 1)])]})
        self.assertEqual(p.returncode, 1)
        self.assertFalse(os.path.exists(os.path.join(d, "site", "index.html")))

    def test_savings(self):
        ps = [prod("cat-litter-tofu", f"s{i}", f"おからの猫砂 7L 商品{i}", [offer("A店", 1000, name="猫砂 7L"), offer("B店", 1300, name="猫砂 7L"),
                                                                   offer("F店", 1400, name="猫砂 7L"), offer("C店", 5000, name="猫砂 7L×6袋")]) for i in range(3)]
        ps.append(prod("cat-litter-tofu", "out", "おからの猫砂 7L 訳あり", [offer("D店", 500, name="【アウトレット】猫砂 7L"), offer("E店", 1200, name="猫砂 7L")]))
        d, p = run({"fetched_at": "2026-11-05 05:17 JST", "products": ps})
        self.assertEqual(p.returncode, 0, p.stderr)
        item = open(os.path.join(d, "site", "item-s0.html"), encoding="utf-8").read()
        self.assertIn("中央値 1,300円）より <b>300円安い</b>", item)  # 同じ内容量（7L）の3店の中央値と比べる。6袋のケースとは比べない
        out = open(os.path.join(d, "site", "item-out.html"), encoding="utf-8").read()
        self.assertNotIn('class="save"', out)  # 最安が訳あり品ならお得額を出さない
        self.assertIn('class="outlet"', out)

    def test_yahoo(self):
        """Yahoo!ショッピングの価格があるときだけ、モールの印・両モールの最安・Yahoo のクレジットが出る"""
        ps = [prod("cat-litter-tofu", f"y{i}", f"おからの猫砂 7L 商品{i}",
                   [offer("A店", 1000, name="猫砂 7L"), offer("B店", 1300, name="猫砂 7L"),
                    dict(offer("Yストア", 900, name="猫砂 7L"), mall="yahoo", url="https://ck.jp.ap.valuecommerce.com/x")]) for i in range(3)]
        d, p = run({"fetched_at": "2026-11-05 05:17 JST", "products": ps})
        self.assertEqual(p.returncode, 0, p.stderr)
        item = open(os.path.join(d, "site", "item-y0.html"), encoding="utf-8").read()
        self.assertIn('<span class="mall y">Yahoo!</span><span class="sn">Yストア</span>', item)
        self.assertIn('<span class="mall r">楽天</span><span class="sn">A店</span>', item)
        self.assertIn("楽天の最安 1,000円／Yahoo!の最安 900円：Yahoo!が <b>100円安い</b>", item)
        self.assertIn("楽天・Yahoo!3店を比較", item)
        self.assertIn('<span style="margin:15px 15px 15px 15px"><a href="https://developer.yahoo.co.jp/sitemap/">Webサービス by Yahoo! JAPAN</a></span>', item)  # 公式スニペットを改変なしで
        self.assertIn("Supported by Rakuten Developers", item)  # 楽天のクレジットは残す

    def test_concerns(self):
        """悩みから探す：商品名に語がある商品が3つ以上のときだけページを作る。効果はうたわない"""
        ps = [prod("dog-food-small", f"t{i}", f"ドッグフード 涙やけ 小粒 1kg 商品{i}", [offer("A店", 2000 + i, name="フード 1kg")]) for i in range(3)]
        ps += [prod("dog-food-small", f"n{i}", f"ドッグフード 関節 1kg 商品{i}", [offer("A店", 2000 + i, name="フード 1kg")]) for i in range(2)]
        d, p = run({"fetched_at": "2026-11-05 05:17 JST", "products": ps})
        self.assertEqual(p.returncode, 0, p.stderr)
        h = open(os.path.join(d, "site", "nayami-dog-tear.html"), encoding="utf-8").read()
        self.assertEqual(len(re.findall(r'href="item-t\d\.html"', h)), 3)
        self.assertIn("効果を示すものではありません", h)
        self.assertFalse(os.path.exists(os.path.join(d, "site", "nayami-dog-joint.html")))  # 2商品 → 不公開
        idx = open(os.path.join(d, "site", "index.html"), encoding="utf-8").read()
        self.assertIn('href="nayami-dog-tear.html"', idx)
        self.assertLess(idx.index("悩みから探す"), idx.index("sd-cta"))  # 一番上（ペットのタブの直後）に出す
        self.assertNotIn("5,000円以上", idx)
        self.assertIn('id="notes"', open(os.path.join(d, "site", "about.html"), encoding="utf-8").read())

    def test_brands(self):
        """海外ブランド：製造国を確認済み（show）のブランドだけ、3商品以上でページを作る"""
        ps = [prod("dog-food-small", f"b{i}", f"モグワン ドッグフード 1.8kg 商品{i}", [offer("A店", 5000 + i, name="モグワン 1.8kg")]) for i in range(3)]
        ps += [prod("dog-food-small", f"k{i}", f"カナガン ドッグフード 2kg 商品{i}", [offer("A店", 5000 + i, name="カナガン 2kg")]) for i in range(3)]
        d, p = run({"fetched_at": "2026-11-05 05:17 JST", "products": ps})
        self.assertEqual(p.returncode, 0, p.stderr)
        idx = open(os.path.join(d, "site", "index.html"), encoding="utf-8").read()
        self.assertIn("海外ブランド", idx)
        self.assertIn("モグワン<small>イギリス</small>", idx)
        self.assertNotIn("カナガン<small>", idx)  # 製造国が未確認（show=false）は出さない

    def test_card_link(self):
        """楽天カードの申込リンクは、楽天の正規のアフィリエイト URL のときだけ出す"""
        for url, shown in (("https://hb.afl.rakuten.co.jp/hsc/x/?pc=card", True), ("https://example.com/x", False)):
            d, p = run(prod_data(), {"RAKUTEN_CARD_URL": url})
            item = open(os.path.join(d, "site", "item-lit1.html"), encoding="utf-8").read()
            self.assertEqual('class="card-ln"' in item, shown)

    def test_msearch(self):
        """検索ページに楽天・Yahoo のその場検索の枠と呼び出しがある"""
        d, p = run(prod_data())
        h = open(os.path.join(d, "site", "search.html"), encoding="utf-8").read()
        self.assertIn('id="msec"', h)
        self.assertIn("/api/msearch?q=", open(os.path.join(d, "site", "app.js"), encoding="utf-8").read())
        self.assertIn("購入時にリンク先に表示されている価格が適用されます", h)

    def test_no_price_history(self):
        """楽天の規約（価格情報の保存は24時間まで）：過去の価格を読まず、お得は今の平均価格との差だけで出す"""
        sys.path.insert(0, HERE)
        import build
        build.HISTORY = {"p": [["2026-11-01", 1200, 1200, 3, None], ["2026-11-02", 1000, 1000, 3, None]]}
        build.load_history() if hasattr(build, "load_history") else None
        self.assertEqual(build.HISTORY, {}) if hasattr(build, "load_history") else None
        build.HISTORY = {}
        self.assertEqual(build.trend_block("p", {"price": 1000}), "")
        p = prod("cat-litter-tofu", "p", "猫砂 7L", [offer("A", 900, name="猫砂 7L"), offer("B", 1000, name="猫砂 7L")])
        self.assertIsNone(build.deal(p, "L"))   # 同じ内容量の他店が3店未満なら出さない
        p3 = prod("cat-litter-tofu", "p", "猫砂 7L", [offer(n, v, name="猫砂 7L") for n, v in (("A", 850), ("B", 1000), ("C", 1100))])
        d = build.deal(p3, "L")
        self.assertEqual((d["hi"], d["now"], d["d"]), (1000, 850, 150))
        p4 = prod("cat-litter-tofu", "p", "猫砂 7L", [offer(n, v, name="猫砂 7L") for n, v in (("A", 950), ("B", 1000), ("C", 1010))])
        self.assertIsNone(build.deal(p4, "L"))  # 10%未満は出さない

    def test_free_shipping(self):
        ps = [prod("cat-litter-tofu", f"f{i}", f"猫砂 7L 商品{i}", [offer("A店", 1000, ship="送料別", name="猫砂 7L"), offer("B店", 1300, 10, "送料無料", name="猫砂 7L")]) for i in range(3)]
        d, p = run({"fetched_at": "2026-11-05 05:17 JST", "products": ps})
        self.assertEqual(p.returncode, 0, p.stderr)
        item = open(os.path.join(d, "site", "item-f0.html"), encoding="utf-8").read()
        self.assertIn("この価格は送料別です", item)
        self.assertIn("送料無料で最安は <b>B店</b> の 1,300円", item)
        self.assertIn('data-bought="f0"', item)
        self.assertIn('data-share=', item)
        cat = open(os.path.join(d, "site", "cat-litter-tofu.html"), encoding="utf-8").read()
        self.assertIn('data-fprice="1300"', cat)
        self.assertIn('id="freeonly"', cat)
        sj = json.load(open(os.path.join(d, "site", "search.json"), encoding="utf-8"))
        self.assertEqual(sj["items"][0]["fp"], 1300)

    def test_points(self):
        sys.path.insert(0, HERE)
        from fetch_prices import points
        self.assertEqual(points(1100, 1), 10)
        self.assertEqual(points(1100, 10), 100)
        self.assertEqual(points(1029, 1), 9)


if __name__ == "__main__":
    unittest.main(verbosity=1)
