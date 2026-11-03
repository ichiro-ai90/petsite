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


# ---- 表示（2026-11-04 リデザイン: ペットを選ぶ → カテゴリを選ぶ → 楽天市場でいちばん安い単価の商品） ----
LABEL = {"kg": "1kgあたり", "g": "100gあたり", "L": "1Lあたり", "枚": "1枚あたり", "匹": "1匹あたり"}
SCALE = {"kg": 1, "g": 100, "L": 1, "枚": 1, "匹": 1}  # g は 100g あたりで見せる（1gあたりだと小さすぎて比べにくい）
SHOP = {"rakuten": "楽天市場", "yahoo": "Yahoo!ショッピング"}
AD = {"rakuten": "楽天アフィリエイト", "yahoo": "バリューコマース"}
CREDIT = {
    # 楽天ウェブサービスのクレジット: 公式テキスト版スニペットを改変せずに使う（guide/credit、2026-11-03 確認）
    "rakuten": '<!-- Rakuten Web Services Attribution Snippet FROM HERE --><a href="https://developers.rakuten.com/" target="_blank">Supported by Rakuten Developers</a><!-- Rakuten Web Services Attribution Snippet TO HERE -->',
    "yahoo": '<a href="https://developer.yahoo.co.jp/sitemap/" rel="nofollow">Webサービス by Yahoo! JAPAN</a>',
}
# ペットの入口。animal（products.json）がどのペットに出るか
PETS = [
    ("dog", "犬", "🐕", ["犬", "犬・猫"]),
    ("cat", "猫", "🐈", ["猫", "犬・猫"]),
    ("small", "小動物", "🐹", ["ハムスター", "うさぎ・モルモット", "うさぎ", "フェレット"]),
    ("reptile", "爬虫類", "🦎", ["爬虫類"]),
    ("bird-fish", "小鳥・魚", "🐟", ["小鳥", "金魚"]),
]
FONT = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=M+PLUS+Rounded+1c:wght@500;800&display=swap" rel="stylesheet">')


def show_name(name):
    """表示用の商品名: 先頭の販促ブラケット（【11/5限定】など）と記号を除く。比較・内容量の読み取りには元の名前を使う。"""
    s = re.sub(r"[【\[［][^】\]］]{0,40}[】\]］]", " ", name)
    s = re.sub(r"[★☆◆◇■□●○※!！]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or name


def yen(v):
    return f"{v:,.1f}" if v < 100 else f"{v:,.0f}"


def img(url, size):
    """楽天のサムネイル URL のサイズ指定（_ex=128x128）を差し替える。無ければ空。"""
    if not url:
        return ""
    return re.sub(r"_ex=\d+x\d+", f"_ex={size}x{size}", url) if "_ex=" in url else url


def page(title, body, desc, shops, sample, crumb=""):
    ads = "・".join(AD[s] for s in shops) or "アフィリエイト"
    credits = " ／ ".join(CREDIT[s] for s in shops)
    band = ('<p class="sample">【サンプル表示】API 未接続のため架空の商品・価格です。実在の商品ではありません。</p>'
            if sample else "")
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><meta name="description" content="{html.escape(desc)}">
{FONT}<link rel="stylesheet" href="style.css"></head><body>
<p class="promo">本ページはプロモーション（アフィリエイト広告）が含まれています。</p>
{band}<header class="top"><a class="logo" href="index.html"><span class="mark" aria-hidden="true">¥</span>ペット用品 単価くらべ</a>{crumb}</header>
<main>{body}</main>
<footer><p>楽天市場の商品だけを比べています。当サイトはアフィリエイト広告（{ads}）を利用しています。価格・送料・在庫は取得時点のもので、購入前に商品ページでご確認ください。</p>
<p>運営: 合同会社にこのて（価格の取得・ページ作成は AI〈一郎〉が自動で行っています）</p>
<p>{credits}</p></footer>
</body></html>"""


def gate(total, shown, sample):
    """公開してよいか（ok, 理由）。サンプル時は常に可。"""
    if sample:
        return True, "サンプル"
    if total == 0 or shown < MIN_SHOWN:
        return False, f"表示{shown}件 < {MIN_SHOWN}件"
    rate = shown / total
    if rate < MIN_RATE:
        return False, f"解析率{rate * 100:.0f}% < {MIN_RATE * 100:.0f}%（表示{shown}/対象{total}）"
    return True, f"解析率{rate * 100:.0f}%（表示{shown}/対象{total}）"


def tag(up, unit, big=False):
    """値札（このサイトの主役）。単価と単位。"""
    return (f'<span class="tag{" big" if big else ""}"><span class="per">{LABEL[unit]}</span>'
            f'<span class="v">{yen(up * SCALE[unit])}<small>円</small></span></span>')


def category_body(g, rows, fetched, carried):
    u = g["unit"]
    up, a, best = rows[0]
    d = detail(best["name"])
    pic = img(best.get("image"), 300)
    pic_html = (f'<img src="{html.escape(pic)}" alt="" width="300" height="300" loading="eager">' if pic
                else '<span class="noimg" aria-hidden="true">？</span>')
    hero = (f'<section class="best"><p class="best-head">今回取得した{len(rows)}件のうち、{LABEL[u]}がいちばん安い商品</p>'
            f'<div class="best-card"><a class="best-pic" href="{html.escape(best["url"])}" rel="sponsored nofollow" target="_blank">{pic_html}</a>'
            f'<div class="best-info">{tag(up, u, True)}'
            f'<p class="best-name">{html.escape(show_name(best["name"]))}</p></div>'
            f'<p class="facts"><span>税込 {best["price"]:,}円</span><span>内容量 {fmt_amount(a, u)}{"（" + html.escape(d) + "）" if d else ""}</span>{ship_badge(best.get("shipping"))}</p>'
            f'<a class="buy" href="{html.escape(best["url"])}" rel="sponsored nofollow" target="_blank">楽天市場で見る</a></div></section>')
    li = []
    for i, (up2, a2, it) in enumerate(rows[1:], 2):
        p2 = img(it.get("image"), 128)
        th = f'<img src="{html.escape(p2)}" alt="" width="64" height="64" loading="lazy">' if p2 else '<span class="noimg" aria-hidden="true">？</span>'
        li.append(f'<li><a href="{html.escape(it["url"])}" rel="sponsored nofollow" target="_blank">'
                  f'<span class="rk">{i}</span><span class="th">{th}</span>'
                  f'<span class="nm">{html.escape(show_name(it["name"]))}<span class="sub">税込 {it["price"]:,}円・{fmt_amount(a2, u)} {ship_badge(it.get("shipping"))}</span></span>'
                  f'{tag(up2, u)}</a></li>')
    rest = (f'<section class="others"><h2>ほかの候補（{LABEL[u]}が安い順）</h2><ol class="rank">{"".join(li)}</ol></section>' if li else "")
    trust = (f'<div class=trust><p class=fetched>価格取得日時: <b>{fetched}</b>{carried}</p>'
             f'<p class=formula>{LABEL[u]}＝税込価格 ÷ 内容量の合計（1袋の量×袋数）。送料は含みません。商品名から内容量を読み取れない商品は載せていません。</p></div>')
    return f'<h1 class="cat-title">{html.escape(g["title"])}</h1>{hero}{rest}{trust}'


def pet_card(p, cats):
    pid, name, icon, _ = p
    return (f'<li><a class="pet" href="pet-{pid}.html"><span class="pet-icon" aria-hidden="true">{icon}</span>'
            f'<span class="pet-name">{name}</span><span class="pet-n">{len(cats)}カテゴリ</span></a></li>')


def main():
    groups = json.load(open(os.path.join(ROOT, "products.json"), encoding="utf-8"))["groups"]
    data = json.load(open(os.path.join(ROOT, "data", "prices.json"), encoding="utf-8"))
    sample = str(data.get("fetched_at", "")).startswith("SAMPLE")
    shops = [s for s in SHOP if any(it["shop"] == s for it in data["items"])]
    origin = os.environ.get("SITE_ORIGIN", "").rstrip("/")
    out = os.path.join(ROOT, "site")
    os.makedirs(out, exist_ok=True)
    fetched = html.escape(str(data.get("fetched_at", "不明")))
    published = []  # (priority, title, fn, group, best_row)
    stats = []
    for g in groups:
        rows, hidden, total = [], 0, 0
        for it in data["items"]:
            if it["group"] != g["slug"] or any(x in it["name"] for x in g.get("exclude", [])):
                continue
            a = amount(it["name"], g["unit"])
            if a and g.get("min") and a < g["min"]:
                continue  # 「大袋」カテゴリにお試し・小袋を混ぜない（products.json の min）
            total += 1
            if not a:
                hidden += 1  # 内容量が読めない商品は出さない（単価を推測しない）
                continue
            rows.append((it["price"] / a, a, it))
        ok, why = gate(total, len(rows), sample)
        stats.append((g["slug"], total, len(rows), hidden, ok, why))
        fn = g["slug"] + ".html"
        if not ok or not rows:
            if os.path.exists(os.path.join(out, fn)):
                os.remove(os.path.join(out, fn))  # 前回の古いページを残さない
            continue
        rows.sort(key=lambda r: r[0])
        old = sorted({str(r[2]["fetched_at"]) for r in rows if r[2].get("fetched_at")})
        carried = (f"<br>一部前回取得: {html.escape('・'.join(old))}（今回の取得に失敗したため前回の価格を表示しています）" if old else "")
        pets_of = [p for p in PETS if g["animal"] in p[3]]
        crumb = (f'<a class="back" href="pet-{pets_of[0][0]}.html">{pets_of[0][1]}の用品一覧へ</a>' if pets_of
                 else '<a class="back" href="index.html">ペットを選びなおす</a>')
        open(os.path.join(out, fn), "w", encoding="utf-8").write(
            page(f"{g['title']}の単価比較（{LABEL[g['unit']]}の価格）｜ペット用品 単価くらべ", category_body(g, rows, fetched, carried),
                 f"{g['title']}を楽天市場で{LABEL[g['unit']]}の価格で比較。", shops, sample, crumb))
        published.append((g.get("priority", 9), g["title"], fn, g, rows[0]))
    published.sort(key=lambda p: (p[0], p[1]))
    files = [p[2] for p in published]
    # ペット別ページ
    pet_pages = []
    for p in PETS:
        cats = [x for x in published if x[3]["animal"] in p[3]]
        if not cats:
            if os.path.exists(os.path.join(out, f"pet-{p[0]}.html")):
                os.remove(os.path.join(out, f"pet-{p[0]}.html"))
            continue
        pet_pages.append((p, cats))
        items = []
        for _, title, fn, g, (up, a, it) in cats:
            th = img(it.get("image"), 128)
            th_html = f'<img src="{html.escape(th)}" alt="" width="72" height="72" loading="lazy">' if th else '<span class="noimg" aria-hidden="true">？</span>'
            items.append(f'<li data-q="{html.escape(title + " " + g["keyword"])}"><a class="cat" href="{fn}"><span class="th">{th_html}</span>'
                         f'<span class="nm">{html.escape(title)}<span class="sub">いちばん安い単価</span></span>{tag(up, g["unit"])}</a></li>')
        body = (f'<section class="pet-hero"><span class="pet-icon big" aria-hidden="true">{p[2]}</span><h1>{p[1]}の用品</h1>'
                f'<p>カテゴリを選ぶと、楽天市場で単価がいちばん安い商品が出ます。</p></section>'
                f'<label class="search"><span class="sr">カテゴリを探す</span><input type="search" id="q" placeholder="カテゴリを探す（例: フード、シーツ）" autocomplete="off"></label>'
                f'<ul class="cats" id="cats">{"".join(items)}</ul><p class="empty" id="empty" hidden>一致するカテゴリがありません。言葉を短くして探してください。</p>'
                f'<div class=trust><p class=fetched>価格取得日時: <b>{fetched}</b></p></div>'
                '<script>(function(){var q=document.getElementById("q"),l=document.querySelectorAll("#cats li"),e=document.getElementById("empty");'
                'q.addEventListener("input",function(){var v=q.value.trim(),n=0;l.forEach(function(x){var m=!v||x.dataset.q.indexOf(v)>-1;x.hidden=!m;if(m)n++;});e.hidden=n>0;});})();</script>')
        open(os.path.join(out, f"pet-{p[0]}.html"), "w", encoding="utf-8").write(
            page(f"{p[1]}の用品を単価で比較｜ペット用品 単価くらべ", body, f"{p[1]}の消耗品を楽天市場の単価で比較。", shops, sample,
                 '<a class="back" href="index.html">ペットを選びなおす</a>'))
    # トップ
    idx = ('<section class="hero"><h1>うちの子の消耗品、<br>単価がいちばん安いのは？</h1>'
           '<p class="hero-lead">楽天市場の商品を1kg・1枚あたりの価格に直して比べます。</p><p class="pick">ペットを選んでください</p>'
           f'<ul class="pets">{"".join(pet_card(p, cats) for p, cats in pet_pages)}</ul></section>'
           '<section class="how"><h2>使い方</h2><ol class="steps"><li><b>ペットを選ぶ</b>犬・猫・小動物など</li>'
           '<li><b>カテゴリを選ぶ</b>フード・シーツ・猫砂など</li><li><b>楽天市場で買う</b>単価がいちばん安い商品から見られます</li></ol></section>'
           f'<div class=trust><p class=fetched>価格取得日時: <b>{fetched}</b></p>'
           '<p class=formula>単価＝税込価格 ÷ 内容量の合計（1袋の量×袋数）。内容量は商品名から読み取り、読み取れない商品は載せません。送料は含みません。</p></div>')
    tot = sum(s[1] for s in stats)
    shown = sum(s[2] for s in stats)
    hid = sum(s[3] for s in stats)
    print(f"内容量の解析: 対象{tot}件／表示{shown}件／非表示{hid}件（解析率 {shown / tot * 100 if tot else 0:.0f}%）")
    for slug, t, s, h, ok, why in stats:
        if not ok:
            print(f"  不公開 {slug}: {why}")
        elif h:
            print(f"  非表示 {slug}: {h}/{t}件")
    if not published:
        print("::error::公開できるカテゴリが 0 件。サイトを生成しません", file=sys.stderr)
        return 1
    open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(
        page("ペット用品 単価くらべ｜楽天市場の1kg・1枚あたり価格", idx, "ペット用品を楽天市場の単価（1kg・1L・1枚あたり）で比較", shops, sample))
    open(os.path.join(out, "style.css"), "w", encoding="utf-8").write(CSS)
    robots = "User-agent: *\nAllow: /\n"
    if origin:
        today = datetime.date.today().isoformat()
        urls = "".join(f"<url><loc>{html.escape(origin)}/{f}</loc><lastmod>{today}</lastmod></url>"
                       for f in ["index.html"] + [f"pet-{p[0]}.html" for p, _ in pet_pages] + files)
        open(os.path.join(out, "sitemap.xml"), "w", encoding="utf-8").write(
            f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n')
        robots += f"Sitemap: {origin}/sitemap.xml\n"
    open(os.path.join(out, "robots.txt"), "w", encoding="utf-8").write(robots)
    print(f"{len(files)}/{len(groups)} ページ＋ペット{len(pet_pages)}＋index を生成" + ("（サンプル: 全カテゴリ）" if sample else "（本番: 公開判定あり）")
          + ("（sitemap.xml あり）" if origin else "（SITE_ORIGIN 未設定: sitemap.xml なし）"))
    return 0


CSS = """:root{--ink:#1D2B3A;--muted:#5E6E7D;--bg:#EEF4F1;--paper:#fff;--leaf:#17805F;--leaf-d:#0F6249;--tag:#FFD84A;--line:#D9E3DE}
*{box-sizing:border-box}
body{margin:0;font-family:"Hiragino Sans","Noto Sans JP",system-ui,sans-serif;color:var(--ink);background:var(--bg);line-height:1.7;-webkit-text-size-adjust:100%}
h1,h2,.logo,.tag .v,.pet-name,.rk,.buy{font-family:"M PLUS Rounded 1c","Hiragino Maru Gothic ProN","Hiragino Sans",sans-serif}
a{color:inherit}
a:focus-visible,input:focus-visible{outline:3px solid var(--leaf);outline-offset:2px}
.promo{margin:0;padding:5px 12px;background:var(--ink);color:#fff;font-size:.7rem;text-align:center}
.sample{margin:0;padding:8px 16px;background:#fff4d6;color:#7a5200;font-size:.85rem;text-align:center}
.top{max-width:720px;margin:0 auto;padding:14px 16px;display:flex;justify-content:space-between;align-items:center;gap:12px}
.logo{font-weight:800;text-decoration:none;font-size:1.05rem;display:flex;align-items:center;gap:8px}
.mark{display:inline-grid;place-items:center;width:30px;height:30px;border-radius:9px 9px 9px 2px;background:var(--tag);font-size:1rem}
.back{font-size:.85rem;color:var(--leaf-d);text-decoration:none;white-space:nowrap}
main{max-width:720px;margin:0 auto;padding:0 16px 24px}
/* トップ */
.hero{padding:12px 0 8px}
.hero h1{font-size:clamp(1.55rem,6.2vw,2.3rem);line-height:1.35;margin:0 0 8px;font-weight:800}
.hero-lead{margin:0 0 18px;color:var(--muted);max-width:34em;font-size:.92rem}
.pick{margin:0 0 10px;font-weight:700;font-size:.95rem}
.pets{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:12px}
.pet{display:flex;flex-direction:column;align-items:center;gap:2px;padding:18px 8px 14px;background:var(--paper);border-radius:22px;text-decoration:none;border:2px solid transparent}
.pet:hover{border-color:var(--leaf)}
.pet-icon{font-size:2.6rem;line-height:1}
.pet-icon.big{font-size:2.6rem}
.pet-name{font-weight:800;font-size:1.15rem;margin-top:6px}
.pet-n{font-size:.78rem;color:var(--muted)}
.how h2,.others h2{font-size:1.05rem;margin:32px 0 10px}
.steps{list-style:none;counter-reset:s;margin:0;padding:0;display:grid;gap:8px}
.steps li{counter-increment:s;display:grid;grid-template-columns:32px 1fr;column-gap:10px;color:var(--muted);font-size:.9rem}
.steps li::before{content:counter(s);grid-row:span 2;width:32px;height:32px;border-radius:50%;background:var(--paper);display:grid;place-items:center;font-weight:800;color:var(--leaf-d)}
.steps b{color:var(--ink);display:block}
/* 値札 */
.tag{display:inline-flex;flex-direction:column;align-items:flex-end;background:var(--tag);padding:5px 12px 5px 16px;border-radius:6px 14px 14px 6px;position:relative;white-space:nowrap;flex-shrink:0}
.tag::before{content:"";position:absolute;left:5px;top:50%;width:6px;height:6px;margin-top:-3px;border-radius:50%;background:var(--paper)}
.tag .per{font-size:.68rem;line-height:1.2}
.tag .v{font-weight:800;font-size:1.15rem;line-height:1.2}
.tag small{font-size:.7em;margin-left:1px}
.tag.big{align-items:flex-start;padding:8px 18px 8px 24px;border-radius:8px 20px 20px 8px}
.tag.big::before{left:8px;width:9px;height:9px;margin-top:-4.5px}
.tag.big .per{font-size:.8rem}.tag.big .v{font-size:2.4rem}
/* ペット別 */
.pet-hero{text-align:center;padding:4px 0 0}
.pet-hero h1{margin:6px 0 4px;font-size:1.6rem}
.pet-hero p{margin:0 auto;color:var(--muted);font-size:.9rem;max-width:30em}
.search{display:block;margin:18px 0 12px}
.search input{width:100%;font:inherit;padding:12px 16px;border-radius:999px;border:2px solid var(--line);background:var(--paper)}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.cats,.rank{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.cat,.rank a{display:flex;align-items:center;gap:12px;padding:10px 12px;background:var(--paper);border-radius:16px;text-decoration:none}
.th img,.noimg{display:block;width:64px;height:64px;object-fit:contain;border-radius:10px;background:#f6f8f7}
.cat .th img,.cat .noimg{width:72px;height:72px}
.noimg{display:grid;place-items:center;color:var(--muted)}
.nm{flex:1;min-width:0;font-size:.92rem;font-weight:600;line-height:1.45}
.rank .nm{font-weight:400;font-size:.85rem;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.sub{display:block;font-size:.75rem;color:var(--muted);font-weight:400;margin-top:2px}
.empty{color:var(--muted);text-align:center}
/* カテゴリ */
.cat-title{font-size:1.4rem;margin:8px 0 12px}
.best-head{margin:0 0 8px;font-size:.88rem;color:var(--muted)}
.best-card{background:var(--paper);border-radius:24px;padding:16px;display:grid;grid-template-columns:200px 1fr;grid-template-areas:"pic info" "pic facts" "pic buy";column-gap:18px;align-items:center}
.best-pic{grid-area:pic}.best-info{grid-area:info}.facts{grid-area:facts}.buy{grid-area:buy}
.best-pic img,.best-pic .noimg{width:100%;height:auto;aspect-ratio:1;object-fit:contain;border-radius:16px;background:#f6f8f7}
.best-name{margin:10px 0 6px;font-size:.92rem;line-height:1.5;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.facts{display:flex;flex-wrap:wrap;gap:4px 12px;margin:0 0 14px;font-size:.82rem;color:var(--muted);align-items:center}
.buy{display:block;text-align:center;background:var(--leaf);color:#fff;text-decoration:none;font-weight:800;padding:14px;border-radius:999px;font-size:1.05rem}
.buy:hover{background:var(--leaf-d)}
.rk{width:22px;text-align:center;font-weight:800;color:var(--muted);flex-shrink:0}
.ship{display:inline-block;font-size:11px;border-radius:4px;padding:0 6px;white-space:nowrap;vertical-align:1px}
.ship.free{background:#DDF1E8;color:#0F6249}.ship.cond{background:#fff4d6;color:#7a5200}.ship.paid{background:#EDF0F2;color:#4A5866}
.trust{margin:28px 0 0;padding:12px 14px;border-radius:14px;border:1px dashed var(--line);font-size:.78rem;color:var(--muted)}
.trust p{margin:2px 0}
footer{max-width:720px;margin:12px auto 0;padding:16px;color:var(--muted);font-size:.74rem}
@media (max-width:520px){.pets{grid-template-columns:repeat(3,1fr);gap:8px}.pet{padding:12px 4px 10px;border-radius:18px}.pet-icon{font-size:2.1rem}.pet-name{font-size:1rem;margin-top:4px}.best-card{grid-template-columns:112px 1fr;grid-template-areas:"pic info" "facts facts" "buy buy";column-gap:14px;padding:14px}.best-name{-webkit-line-clamp:3;font-size:.82rem;margin:8px 0 0}.facts{margin:12px 0}.tag.big .v{font-size:1.9rem}.tag.big{padding:6px 14px 6px 22px}.rank .th img,.rank .noimg{width:52px;height:52px}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

if __name__ == "__main__":
    sys.exit(main())
