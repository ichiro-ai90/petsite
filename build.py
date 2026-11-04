"""data/prices.json から静的サイト（site/）を生成する。依存ライブラリなし。

- 商品名から内容量（kg/g/L/枚/匹 × 袋数）を読み取り、単位あたり価格で安い順に並べる。
- 全角数字・「1,000g」・「120g入」・「2袋×1kg」・「3袋セット」・ml・キロ表記に対応。
- 2026-11-02 堅牢化: 「×12袋×2箱」の連鎖、括弧内の内訳「1kg(500g×2袋)」・合計注記「400g×5袋(2kg)」、
  「3袋セット（1袋あたり1.5kg）」、「1袋あたり」を倍数にしない、選択式「1kg/3kg」「選べる」は読まない。
- 「Lサイズ」などのサイズ表記は容量として読まない。読めない商品（None）はページに出さず、件数をログに出す（推測で埋めない）。
- リンクはすべて相対パス（github.io/<repo>/ 配下でも独自ドメインでも動く）。
- 環境変数 SITE_ORIGIN（例 https://ichiro-ai90.github.io/petsite）があれば sitemap.xml を絶対URLで出し、
  robots.txt に Sitemap 行を入れる。未設定なら sitemap.xml は出さず robots.txt は全許可のみ。
- 取得元に出てきたショップのクレジット・広告表記だけをフッターに出す（未提携の ASP を書かない）。
- fetched_at が SAMPLE で始まるときは全ページに「サンプルデータ」の帯を出す（架空価格を本物に見せない）。
- 2026-11-02 20:5x 公開判定: 本番データ（fetched_at が SAMPLE 以外）では「表示 MIN_SHOWN 件以上かつ
  解析率 MIN_RATE 以上」のカテゴリだけページを作り index・sitemap に載せる（不合格は理由をログ、古いページは削除）。
  サンプル時は全カテゴリを生成。公開カテゴリが 0 なら exit 1（空サイトを出さない）。
  index は products.json の priority（小さいほど上）→ title 順。
  前回から引き継いだ商品（item に fetched_at がある）を含むページは meta に「一部前回取得」と注記する。
- 2026-11-02 21:5x 単価の信頼表示: 各カテゴリの表の上に ①単価の式（{LABEL}＝税込価格÷内容量の合計）
  ②価格取得日時の枠 を出し、③送料セルをバッジ化（送料無料＝free／条件付き送料無料＝cond／送料別・その他＝paid）、
  ④内容量セルは読み取った合計（式の分母と同じ値）を出し、商品名の内訳（例 7L×6袋）を小さく併記。
  index にも式の説明と取得日時を出す。
- 2026-11-03 01:5x 規約チェック反映: 楽天クレジットを公式テキストスニペット（改変なし）に、全ページ冒頭（ファーストビュー）に
  「本ページはプロモーションが含まれています」帯、タイトル・リードの「最安値／最安」を「今回取得した N 件のうち」に限定。
- 期待値テスト: python3 tests_amount.py ／ 公開判定・表示テスト: python3 tests_gate.py
"""
import datetime
import html
import json
import os
import re
import sys
import unicodedata

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE_NAME = "ペット用品 単価くらべ（仮）"
MIN_SHOWN = 3     # 本番で公開する最低表示件数
MIN_RATE = 0.70   # 本番で公開する最低解析率（表示件数／対象件数）

NUM = r"(\d+(?:\.\d+)?)"
# 単位の正規表現（NFKC 後の文字列に当てる）。「Lサイズ」「M寸」などのサイズ表記は容量にしない。
_KG = re.compile(NUM + r"\s*(?:kg|キロ(?:グラム)?)(?![a-z])", re.I)
_G = re.compile(NUM + r"\s*(?:g|グラム)(?![a-z])", re.I)
_L = re.compile(NUM + r"\s*(?:l|リットル)(?![a-zサ])", re.I)
_ML = re.compile(NUM + r"\s*(?:ml|ミリリットル)(?![a-z])", re.I)
_SHEET = re.compile(NUM + r"\s*枚")
_HEAD = re.compile(NUM + r"\s*(?:匹|頭)")
UNIT_RE = {
    "kg": [(_KG, 1.0), (_G, 0.001)],
    "g": [(_KG, 1000.0), (_G, 1.0)],
    "L": [(_L, 1.0), (_ML, 0.001)],
    "枚": [(_SHEET, 1.0)],
    "匹": [(_HEAD, 1.0)],
}
PACK = r"(?:袋|個|パック|箱|セット|缶|本|p|P|コ|ケース)"
# 量の直後: 「×2」「x3袋」「*4個」（「×12袋×2箱」のように連鎖可）
MULT_AFTER = re.compile(r"^\s*(?:入り?)?\s*[×xX*]\s*(\d+)\s*" + PACK + r"?")
# 量と離れた位置: 「3袋セット」「2個セット」「12個入」。「1袋あたり」は倍数にしない
MULT_ANY = re.compile(r"(\d+)\s*(?:袋|個|パック|箱|缶)(?!\s*(?:あたり|当たり|当り))\s*(?:セット|組|まとめ|入)?")
# 量の直前: 「2袋×1.5kg」
MULT_BEFORE = re.compile(r"(\d+)\s*(?:袋|個|パック|箱|缶)?\s*[×xX*]\s*$")
# 括弧（内訳・合計の注記）
_BRACKET = re.compile(r"[\(\[（【「][^\)\]）】」]*[\)\]）】」]")
# 選択式（「1kg/3kg」「選べる」）は量が1つに決まらないので読まない
_CHOICE = re.compile(r"選べる|選択|サイズ違い|容量違い|\sor\s|または")
# 内容量セルに併記する内訳（商品名の「7L×6袋」「100枚×4袋」など、量×個数の部分だけ）
_DETAIL = re.compile(r"\d+(?:\.\d+)?\s*(?:kg|g|ml|l|L|枚|匹)\s*(?:[×xX*]\s*\d+\s*" + PACK + r"?)+")


# 単位のない内訳「（10×3個）」: 合計がすでに外に書かれているので倍数に使わない（2026-11-04 実データで 30枚→90枚と誤読）
_BREAKDOWN = re.compile(r"\d+\s*[×xX*]\s*\d+")


# 購入数の制限（「お一人様2個まで」「3点限り」）は内容量の倍数ではないので消す（2026-11-03 実データで誤読）
_LIMIT = re.compile(r"(?:お一人様|おひとり様|お1人様|1人|一人)\s*\d+\s*(?:袋|個|点|セット|パック|箱|缶)?\s*(?:まで|限り|限定)?|\d+\s*(?:袋|個|点|セット|パック|箱|缶)\s*(?:まで|限り)")


def _norm(name):
    s = unicodedata.normalize("NFKC", name)  # 全角数字・全角英字・ｋｇ・ℓ→l 等
    s = s.replace("ℓ", "l").replace("㎏", "kg").replace("㎖", "ml").replace("✕", "×")
    s = re.sub(r"(\d),(\d{3})", r"\1\2", s)  # 1,000g → 1000g
    s = _LIMIT.sub(" ", s)
    return s


def _in_bracket(s, pos):
    for b in _BRACKET.finditer(s):
        if b.start() < pos < b.end():
            return b
    return None


def amount(name, unit):
    """商品名から内容量（unit 単位）を返す。読めなければ None（推測で埋めない）。"""
    s = _norm(name)
    for rx, factor in UNIT_RE[unit]:
        ms = list(rx.finditer(s))
        if not ms:
            continue
        # 同じ次元の量（kg と g など）を全部集める
        allm = [mm for r2, _ in UNIT_RE[unit] for mm in r2.finditer(s)]
        # 選択式で異なる量が並ぶ／「1kg/3kg」は読めない
        vals = {float(mm.group(1)) * f2 for r2, f2 in UNIT_RE[unit] for mm in r2.finditer(s)}
        if len(vals) > 1 and (_CHOICE.search(s) or re.search(NUM + r"\s*[a-zA-Zキロリットルグラム枚匹]*\s*/\s*\d", s)):
            return None
        # 括弧の外にある量を優先（「400g×5袋（2kg）」の 2kg は合計注記）
        outside = [mm for mm in ms if not _in_bracket(s, mm.start())]
        others_out = [mm for mm in allm if not _in_bracket(s, mm.start())]
        out_vals = {round(float(mm.group(1)) * f2, 6) for r2, f2 in UNIT_RE[unit] for mm in r2.finditer(s)
                    if not _in_bracket(s, mm.start()) and not re.search(r"(?:約|合計|計|総量|総)\s*$", s[:mm.start()])
                    and not re.match(r"\s*(?:以上|以下|未満|まで|用)", s[mm.end():])}
        if len(out_vals) > 1 and not MULT_AFTER.match(s[(outside or ms)[0].end():]):
            return None  # 容量違いの列挙（2026-11-04 実データ: 20L と誤読し最安に出ていた）
        if outside:
            m = outside[0]
        elif others_out:
            # 外に別単位の量がある（「400g×5袋（2kg）」）→ 次の単位候補で読む
            continue
        else:
            m = ms[0]
        q = float(m.group(1)) * factor
        if q <= 0:
            return None
        n = 1
        tail = s[m.end():]
        after = MULT_AFTER.match(tail)
        before = MULT_BEFORE.search(s[:m.start()])
        if after:
            while after:  # 連鎖「×12袋×2箱」
                k = int(after.group(1))
                if k <= 0 or k > 100:
                    return None
                n *= k
                tail = tail[after.end():]
                after = MULT_AFTER.match(tail)
        elif before:
            n = int(before.group(1))
        else:
            br = _in_bracket(s, m.start())
            rest = s[:m.start()] + " " + s[m.end():]
            if br is None:
                # 量が括弧の外: 量を含む括弧（内訳「(500g×2袋)」）は倍数に使わない。【2袋セット】は使う
                rest = _BRACKET.sub(lambda b: " " if (any(r2.search(b.group(0)) for r2, _ in UNIT_RE[unit]) or _BREAKDOWN.search(b.group(0))) else b.group(0), rest)
            else:
                # 量が括弧の中（「3袋セット（1袋あたり1.5kg）」）: 括弧の外だけを見る
                rest = s[:br.start()] + " " + s[br.end():]
            mm = MULT_ANY.search(rest)
            n = int(mm.group(1)) if mm else 1
        if n <= 0 or n > 1000:
            return None
        return q * n
    return None


def detail(name):
    """内容量セルに小さく併記する内訳（「7L×6袋」など）。無ければ空文字。合計は amount() の値を使う。"""
    m = _DETAIL.search(_norm(name))
    return re.sub(r"\s+", "", m.group(0)) if m else ""


def fmt_amount(a, unit):
    """合計内容量の表示。浮動小数の誤差を丸める（0.4×3＝1.2）。"""
    return f"{round(a, 3):g}{unit}"


def ship_badge(text):
    """送料テキスト（楽天 postageFlag・Yahoo shipping.code 由来）を CSS バッジにする。"""
    t = text or "送料不明"
    if "条件" in t:
        cls = "cond"
    elif "無料" in t or "込" in t:
        cls = "free"
    else:
        cls = "paid"
    return f'<span class="ship {cls}">{html.escape(t)}</span>'


# ---- 表示（2026-11-04 v2: 楽天市場の使い勝手に寄せる。ペット → カテゴリ → 商品ごとの最安ショップとポイント） ----
LABEL = {"kg": "1kgあたり", "g": "100gあたり", "L": "1Lあたり", "枚": "1枚あたり", "匹": "1匹あたり"}
SCALE = {"kg": 1, "g": 100, "L": 1, "枚": 1, "匹": 1}
SITE = "ペット用品 最安ナビ"
CREDIT = ('<!-- Rakuten Web Services Attribution Snippet FROM HERE --><a href="https://developers.rakuten.com/" target="_blank">'
          'Supported by Rakuten Developers</a><!-- Rakuten Web Services Attribution Snippet TO HERE -->')
PETS = [
    ("dog", "犬", "🐕", ["犬", "犬・猫"]),
    ("cat", "猫", "🐈", ["猫", "犬・猫"]),
    ("small", "小動物", "🐹", ["ハムスター", "うさぎ・モルモット", "うさぎ", "フェレット"]),
    ("reptile", "爬虫類", "🦎", ["爬虫類"]),
    ("bird-fish", "小鳥・魚", "🐟", ["小鳥", "金魚"]),
]
MIN_PRODUCTS = 3  # 本番でカテゴリを公開する最低商品数
E = html.escape


def show_name(name):
    """表示用の商品名: 販促ブラケット（【11/5限定】など）と記号を除く。"""
    s = re.sub(r"[【\[［][^】\]］]{0,40}[】\]］]", " ", name)
    s = re.sub(r"[★☆◆◇■□●○※!！]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or name


def img(url, size):
    if not url:
        return ""
    if "_ex=" in url:
        return re.sub(r"_ex=\d+x\d+", f"_ex={size}x{size}", url)
    if "thumbnail.image.rakuten.co.jp" in url or "r.r10s.jp" in url:
        return url + ("&" if "?" in url else "?") + f"_ex={size}x{size}"
    return url


def stars(avg, n):
    if not n:
        return ""
    return f'<span class="rv"><span class="st" style="--r:{min(avg, 5) / 5:.2f}" aria-hidden="true">★★★★★</span>{avg:.1f}<span class="rvn">（{n:,}件）</span></span>'


def ship(text):
    return f'<span class="ship{" free" if text == "送料無料" else ""}">{E(text or "送料不明")}</span>'


def page(title, body, desc, sample, crumbs=()):
    band = ('<p class="sample">【サンプル表示】API 未接続のため架空の商品・価格です。実在の商品ではありません。</p>' if sample else "")
    bc = ""
    if crumbs:
        bc = '<nav class="bc" aria-label="現在地"><a href="index.html">トップ</a>' + "".join(
            f' <span aria-hidden="true">&gt;</span> ' + (f'<a href="{h}">{E(t)}</a>' if h else E(t)) for t, h in crumbs) + "</nav>"
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(title)}</title><meta name="description" content="{E(desc)}">
<link rel="stylesheet" href="style.css"></head><body>
<p class="promo">本ページはプロモーション（アフィリエイト広告）が含まれています。</p>
{band}<header class="hd"><div class="hd-in"><a class="logo" href="index.html"><span class="mk" aria-hidden="true">最安</span>{SITE}</a>
<span class="hd-note">楽天市場の価格比較（非公式）</span></div></header>
<main>{bc}{body}</main>
<footer><p>楽天市場の商品だけを比べています。当サイトは楽天アフィリエイトを利用した個人運営の比較サイトで、楽天グループ株式会社とは関係ありません。価格・ポイント・送料・在庫は取得時点のもので、購入前に商品ページでご確認ください。ポイントはショップのポイント倍率から計算した目安で、SPU やキャンペーンは含みません。</p>
<p>運営: 合同会社にこのて（価格の取得・ページ作成は AI〈一郎〉が自動で行っています）</p>
<p>{CREDIT}</p></footer>
</body></html>"""


def best_offer(p, key="price"):
    return min(p["offers"], key=lambda o: (o[key], o["price"]))


def unit_price(p, g):
    return best_offer(p)["price"] / p["amount"] * SCALE[g["unit"]] if p.get("amount") else None


def product_row(p, g, fn):
    o = best_offer(p)
    oe = best_offer(p, "effective")
    up = unit_price(p, g)
    th = img(p.get("image"), 128)
    pic = f'<img src="{E(th)}" alt="" width="96" height="96" loading="lazy">' if th else '<span class="noimg" aria-hidden="true"></span>'
    upt = f'<span class="up">{LABEL[g["unit"]]} {up:,.0f}円</span>' if up and up >= 10 else (f'<span class="up">{LABEL[g["unit"]]} {up:,.1f}円</span>' if up else "")
    return (f'<li data-pop="{p.get("reviewCount", 0)}" data-price="{o["price"]}" data-eff="{oe["effective"]}" data-unit="{up or 0:.4f}">'
            f'<a class="row" href="{fn}"><span class="pic">{pic}</span><span class="info">'
            f'<span class="nm">{E(show_name(p["name"]))}</span>{stars(p.get("reviewAverage", 0), p.get("reviewCount", 0))}'
            f'<span class="pr"><b>{o["price"]:,}</b>円 {ship(o["shipping"])}</span>'
            f'<span class="pt">{o["points"]:,}ポイント（{o["pointRate"]}倍）</span>'
            f'<span class="meta">{len(p["offers"])}ショップ中の最安値{"・" + upt if upt else ""}</span></span></a></li>')


SORT_JS = ('<script>(function(){var b=document.querySelectorAll(".sort button"),l=document.getElementById("list");'
           'b.forEach(function(x){x.addEventListener("click",function(){var k=x.dataset.k,a=[].slice.call(l.children);'
           'a.sort(function(p,q){return k==="pop"?q.dataset.pop-p.dataset.pop:(p.dataset[k]||1e12)-(q.dataset[k]||1e12)});'
           'a.forEach(function(e){l.appendChild(e)});b.forEach(function(y){y.setAttribute("aria-pressed",y===x)})})})})();</script>')


def category_body(g, prods, pet, fetched, carried):
    prods = sorted(prods, key=lambda p: -p.get("reviewCount", 0))
    rows = "".join(product_row(p, g, f"item-{p['productId']}.html") for p in prods)
    cheapest = min(prods, key=lambda p: best_offer(p)["price"])
    return (f'<h1 class="ttl">{E(g["title"])}</h1>'
            f'<p class="lead">楽天市場で人気の{len(prods)}商品を比べています。商品を選ぶと、その商品をいちばん安く売っているショップが分かります。</p>'
            f'<div class="sort" role="group" aria-label="並べ替え"><span>並べ替え</span>'
            f'<button type="button" data-k="pop" aria-pressed="true">人気順</button>'
            f'<button type="button" data-k="price" aria-pressed="false">安い順</button>'
            f'<button type="button" data-k="eff" aria-pressed="false">ポイント込みで安い順</button>'
            f'<button type="button" data-k="unit" aria-pressed="false">{LABEL[g["unit"]]}が安い順</button></div>'
            f'<ul class="list" id="list">{rows}</ul>'
            f'<div class="note"><p>価格取得日時: {fetched}{carried}</p><p>「最安値」は今回取得したショップの中での最安です。'
            f'{LABEL[g["unit"]]}の価格＝税込価格 ÷ 内容量（商品名から読み取り）。送料は含みません。</p></div>{SORT_JS}')


def item_body(p, g, fetched):
    offers = sorted(p["offers"], key=lambda o: o["price"])
    o = offers[0]
    oe = min(offers, key=lambda x: (x["effective"], x["price"]))
    big = img(p.get("image"), 300)
    pic = f'<img src="{E(big)}" alt="" width="240" height="240">' if big else '<span class="noimg big" aria-hidden="true"></span>'
    up = unit_price(p, g)
    rows = "".join(
        f'<li data-price="{x["price"]}" data-eff="{x["effective"]}"><div class="shop"><span class="sn">{E(x["shop"])}</span>'
        f'<span class="pr"><b>{x["price"]:,}</b>円 {ship(x["shipping"])}</span>'
        f'<span class="pt">{x["points"]:,}ポイント（{x["pointRate"]}倍）・ポイント込み {x["effective"]:,}円</span></div>'
        f'<a class="btn" href="{E(x["url"])}" rel="sponsored nofollow" target="_blank">ショップで見る</a></li>'
        for x in offers)
    alt = ""
    if oe is not o:
        alt = (f'<p class="alt">ポイント込みなら <b>{E(oe["shop"])}</b> が {oe["effective"]:,}円でいちばんお得です'
               f'（{oe["pointRate"]}倍・{oe["points"]:,}ポイント）。</p>')
    allshops = (f'<a class="sub-link" href="{E(p["productUrl"])}" rel="sponsored nofollow" target="_blank">楽天市場の商品ページで全{p.get("itemCount", 0):,}ショップを見る</a>'
                if p.get("productUrl") else "")
    return (f'<div class="item"><div class="item-pic">{pic}</div><div class="item-info">'
            f'<h1 class="ttl">{E(show_name(p["name"]))}</h1>'
            f'<p class="mk2">{E(p.get("maker") or "")}</p>{stars(p.get("reviewAverage", 0), p.get("reviewCount", 0))}'
            f'<div class="best"><p class="best-l">今回取得した{len(offers)}ショップ中の最安値</p>'
            f'<p class="pr big"><b>{o["price"]:,}</b>円 {ship(o["shipping"])}</p>'
            f'<p class="pt">{o["points"]:,}ポイント（{o["pointRate"]}倍）　{E(o["shop"])}</p>'
            + (f'<p class="up">{LABEL[g["unit"]]} {up:,.1f}円（内容量 {fmt_amount(p["amount"], g["unit"])}）</p>' if up else "") +
            f'<a class="btn big" href="{E(o["url"])}" rel="sponsored nofollow" target="_blank">最安のショップで見る</a>{alt}</div></div></div>'
            f'<h2 class="h2">この商品を売っているショップ</h2>'
            f'<div class="sort" role="group" aria-label="並べ替え"><span>並べ替え</span>'
            f'<button type="button" data-k="price" aria-pressed="true">安い順</button>'
            f'<button type="button" data-k="eff" aria-pressed="false">ポイント込みで安い順</button></div>'
            f'<ul class="shops" id="list">{rows}</ul>{allshops}'
            f'<div class="note"><p>価格取得日時: {E(str(p.get("fetched_at") or fetched))}</p><p>ポイントはショップのポイント倍率から計算した目安です（税抜価格の1%×倍率。SPU・キャンペーン・クーポンは含みません）。</p></div>{SORT_JS}')


def main():
    groups = json.load(open(os.path.join(ROOT, "products.json"), encoding="utf-8"))["groups"]
    data = json.load(open(os.path.join(ROOT, "data", "prices.json"), encoding="utf-8"))
    sample = str(data.get("fetched_at", "")).startswith("SAMPLE")
    origin = os.environ.get("SITE_ORIGIN", "").rstrip("/")
    out = os.path.join(ROOT, "site")
    if os.path.isdir(out):  # 前回のページを残さない（消えた商品・不公開カテゴリ）
        for f in os.listdir(out):
            if f.endswith(".html"):
                os.remove(os.path.join(out, f))
    os.makedirs(out, exist_ok=True)
    fetched = E(str(data.get("fetched_at", "不明")))
    gmap = {g["slug"]: g for g in groups}
    published, files = [], []
    for g in groups:
        prods = []
        for p in data.get("products", []):
            if p.get("group") != g["slug"] or not p.get("offers"):
                continue
            if any(x in p["name"] for x in g.get("exclude", [])):
                continue
            a = p.get("amount") or amount(p["name"], g["unit"]) or amount(p["offers"][0]["name"], g["unit"])
            if a and g.get("min") and a < g["min"]:
                continue
            prods.append(dict(p, amount=a))
        if not prods or (not sample and len(prods) < MIN_PRODUCTS):
            print(f"  不公開 {g['slug']}: 商品{len(prods)}件 < {MIN_PRODUCTS}件")
            continue
        pet = next((pt for pt in PETS if g["animal"] in pt[3]), PETS[0])
        old = sorted({str(p["fetched_at"]) for p in prods if p.get("fetched_at")})
        carried = (f"<br>一部前回取得: {E('・'.join(old))}（今回の取得に失敗したため前回の価格を表示しています）" if old else "")
        fn = g["slug"] + ".html"
        crumbs = ((pet[1], f"index.html#{pet[0]}"), (g["title"], None))
        open(os.path.join(out, fn), "w", encoding="utf-8").write(
            page(f"{g['title']}の最安値・ポイント比較｜{SITE}", category_body(g, prods, pet, fetched, carried),
                 f"{g['title']}を楽天市場のショップごとの価格とポイントで比較。", sample, crumbs))
        files.append(fn)
        for p in prods:
            ifn = f"item-{p['productId']}.html"
            open(os.path.join(out, ifn), "w", encoding="utf-8").write(
                page(f"{show_name(p['name'])[:40]}の最安値｜{SITE}", item_body(p, g, fetched),
                     f"{show_name(p['name'])[:60]}を楽天市場で安く買えるショップ。", sample,
                     ((pet[1], f"index.html#{pet[0]}"), (g["title"], fn), ("ショップ比較", None))))
            files.append(ifn)
        published.append((g, prods))
    print(f"公開 {len(published)}/{len(groups)} カテゴリ・商品ページ {len(files) - len(published)} 件")
    if not published:
        print("::error::公開できるカテゴリが 0 件。サイトを生成しません", file=sys.stderr)
        return 1
    # トップ: ペットのタブ → ペットごとのカテゴリ一覧
    tabs, secs = [], []
    for pid, name, icon, animals in PETS:
        cats = [(g, ps) for g, ps in published if g["animal"] in animals]
        if not cats:
            continue
        tabs.append(f'<li><a href="#{pid}"><span class="ic" aria-hidden="true">{icon}</span>{name}</a></li>')
        cs = []
        for g, ps in sorted(cats, key=lambda c: (c[0].get("priority", 9), c[0]["title"])):
            cp = min(ps, key=lambda p: best_offer(p)["price"])
            th = img(cp.get("image"), 128)
            pic = f'<img src="{E(th)}" alt="" width="72" height="72" loading="lazy">' if th else '<span class="noimg" aria-hidden="true"></span>'
            cs.append(f'<li data-q="{E(g["title"] + " " + g["keyword"])}"><a class="cat" href="{g["slug"]}.html"><span class="pic">{pic}</span>'
                      f'<span class="info"><span class="nm">{E(g["title"])}</span><span class="meta">{len(ps)}商品を比較</span></span></a></li>')
        secs.append(f'<section class="pet-sec" id="{pid}"><h2 class="h2"><span aria-hidden="true">{icon}</span> {name}</h2><ul class="cats">{"".join(cs)}</ul></section>')
    idx = ('<div class="search"><label for="q" class="sr">カテゴリを探す</label>'
           '<input type="search" id="q" placeholder="カテゴリを探す（例: 猫砂、ペットシーツ）" autocomplete="off"></div>'
           '<p class="lead">楽天市場の人気商品を、ショップごとの価格とポイントで比べます。ペットを選んでください。</p>'
           f'<ul class="tabs">{"".join(tabs)}</ul>{"".join(secs)}'
           '<p class="empty" id="empty" hidden>一致するカテゴリがありません。言葉を短くして探してください。</p>'
           f'<div class="note"><p>価格取得日時: {fetched}</p></div>'
           '<script>(function(){var q=document.getElementById("q"),l=document.querySelectorAll(".cats li"),s=document.querySelectorAll(".pet-sec"),e=document.getElementById("empty");'
           'q.addEventListener("input",function(){var v=q.value.trim(),n=0;l.forEach(function(x){var m=!v||x.dataset.q.indexOf(v)>-1;x.hidden=!m;if(m)n++;});'
           's.forEach(function(x){x.hidden=!x.querySelector("li:not([hidden])")});e.hidden=n>0;});})();</script>')
    open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(
        page(f"{SITE}｜楽天市場のペット用品を最安値・ポイントで比較", idx, "楽天市場のペット用品を、ショップごとの最安値とポイントで比較", sample))
    open(os.path.join(out, "style.css"), "w", encoding="utf-8").write(CSS)
    robots = "User-agent: *\nAllow: /\n"
    if origin:
        today = datetime.date.today().isoformat()
        urls = "".join(f"<url><loc>{E(origin)}/{f}</loc><lastmod>{today}</lastmod></url>" for f in ["index.html"] + files)
        open(os.path.join(out, "sitemap.xml"), "w", encoding="utf-8").write(
            f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n')
        robots += f"Sitemap: {origin}/sitemap.xml\n"
    open(os.path.join(out, "robots.txt"), "w", encoding="utf-8").write(robots)
    return 0


CSS = """:root{--txt:#333;--sub:#666;--line:#e5e5e5;--bg:#f4f4f4;--red:#bf0000;--btn:#bf0000;--link:#0b5db3}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Hiragino Kaku Gothic ProN","Hiragino Sans","Noto Sans JP",Meiryo,sans-serif;color:var(--txt);background:var(--bg);line-height:1.55;font-size:15px;-webkit-text-size-adjust:100%}
a{color:var(--txt);text-decoration:none}
a:focus-visible,button:focus-visible,input:focus-visible{outline:2px solid var(--link);outline-offset:2px}
.promo{margin:0;padding:4px 12px;background:#fff;border-bottom:1px solid var(--line);color:var(--sub);font-size:11px;text-align:center}
.sample{margin:0;padding:6px 12px;background:#fff4d6;color:#7a5200;font-size:12px;text-align:center}
.hd{background:#fff;border-bottom:1px solid var(--line)}
.hd-in{max-width:960px;margin:0 auto;padding:10px 12px;display:flex;align-items:center;justify-content:space-between;gap:8px}
.logo{font-weight:700;font-size:17px;display:flex;align-items:center;gap:6px}
.mk{background:var(--red);color:#fff;font-size:11px;padding:3px 5px;border-radius:3px;line-height:1}
.hd-note{font-size:11px;color:var(--sub)}
main{max-width:960px;margin:0 auto;padding:0 0 16px}
.bc{font-size:12px;color:var(--sub);padding:8px 12px}.bc a{color:var(--link)}
.search{background:#fff;padding:10px 12px;border-bottom:1px solid var(--line)}
.search input{width:100%;font:inherit;font-size:16px;padding:9px 12px;border:2px solid var(--red);border-radius:4px}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.lead{font-size:13px;color:var(--sub);margin:10px 12px}
.tabs{list-style:none;margin:0;padding:0 8px 8px;display:flex;gap:6px;overflow-x:auto}
.tabs a{display:flex;align-items:center;gap:4px;white-space:nowrap;background:#fff;border:1px solid var(--line);border-radius:20px;padding:6px 14px;font-size:14px;font-weight:700}
.tabs .ic{font-size:18px}
.h2{font-size:16px;margin:0;padding:12px;background:#fff;border-bottom:1px solid var(--line)}
.pet-sec{margin-top:10px}
.cats,.list,.shops{list-style:none;margin:0;padding:0;background:#fff}
.cats li,.list li{border-bottom:1px solid var(--line)}
.cat,.row{display:flex;gap:12px;padding:10px 12px;align-items:flex-start}
.cat{align-items:center}
.pic img,.noimg{display:block;width:96px;height:96px;object-fit:contain;background:#fff}
.cat .pic img,.cat .noimg{width:56px;height:56px}
.noimg{background:#f0f0f0}
.info{display:flex;flex-direction:column;gap:2px;min-width:0;flex:1}
.nm{font-size:14px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.cat .nm{font-weight:700}
.meta{font-size:12px;color:var(--sub)}
.rv{font-size:12px;color:var(--sub);display:flex;align-items:center;gap:3px}
.st{position:relative;color:#ddd;letter-spacing:-1px;font-size:12px}
.st::after{content:"★★★★★";position:absolute;left:0;top:0;width:calc(var(--r)*100%);overflow:hidden;color:#f5a623}
.pr{color:var(--red);font-size:13px;margin:0}.pr b{font-size:19px}
.pr.big b{font-size:28px}
.ship{display:inline-block;font-size:11px;color:var(--sub);border:1px solid var(--line);border-radius:2px;padding:0 4px;margin-left:4px;vertical-align:2px;font-weight:400}
.ship.free{color:var(--red);border-color:var(--red)}
.pt{font-size:12px;color:var(--red);margin:0}
.up{font-size:12px;color:var(--sub);margin:2px 0 0}
.sort{display:flex;gap:6px;align-items:center;padding:8px 12px;background:#fff;border-bottom:1px solid var(--line);overflow-x:auto;font-size:12px;color:var(--sub)}
.sort span{white-space:nowrap}
.sort button{font:inherit;font-size:12px;white-space:nowrap;background:#fff;border:1px solid #ccc;border-radius:3px;padding:5px 10px;color:var(--txt);cursor:pointer}
.sort button[aria-pressed="true"]{background:#333;color:#fff;border-color:#333}
.ttl{font-size:18px;margin:0;padding:6px 12px 0;line-height:1.4}
.item{background:#fff;padding:12px;display:grid;grid-template-columns:240px 1fr;gap:16px}
.item .ttl{padding:0;font-size:17px}
.item-pic img,.noimg.big{width:100%;max-width:240px;height:auto;aspect-ratio:1;object-fit:contain;display:block;margin:0 auto}
.mk2{margin:2px 0;font-size:12px;color:var(--sub)}
.best{margin-top:10px;border:1px solid var(--line);border-radius:4px;padding:10px 12px}
.best-l{margin:0;font-size:12px;color:var(--sub)}
.btn{display:inline-block;background:var(--btn);color:#fff;font-weight:700;border-radius:4px;padding:8px 12px;font-size:13px;white-space:nowrap;text-align:center}
.btn.big{display:block;margin-top:10px;padding:13px;font-size:16px}
.alt{font-size:12px;margin:8px 0 0;color:var(--txt)}
.shops li{display:flex;gap:10px;align-items:center;justify-content:space-between;padding:10px 12px;border-bottom:1px solid var(--line)}
.shop{display:flex;flex-direction:column;gap:1px;min-width:0}
.sn{font-size:13px;color:var(--link);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sub-link{display:block;padding:12px;background:#fff;color:var(--link);font-size:13px;text-align:center;border-bottom:1px solid var(--line)}
.note{font-size:11px;color:var(--sub);padding:10px 12px}.note p{margin:2px 0}
.empty{text-align:center;color:var(--sub);font-size:13px}
footer{max-width:960px;margin:0 auto;padding:12px;font-size:11px;color:var(--sub)}footer a{color:var(--link)}
@media (max-width:600px){.item{grid-template-columns:1fr}.item-pic img,.noimg.big{max-width:200px}.hd-note{display:none}}
"""

if __name__ == "__main__":
    sys.exit(main())
