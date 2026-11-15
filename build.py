"""data/prices.json から静的サイト（site/）を生成する。依存ライブラリなし。

- 商品名から内容量（kg/g/L/枚/匹 × 袋数）を読み取り、単位あたり価格で安い順に並べる。
- 全角数字・「1,000g」・「120g入」・「2袋×1kg」・「3袋セット」・ml・キロ表記に対応。
- 2026-11-02 堅牢化: 「×12袋×2箱」の連鎖、括弧内の内訳「1kg(500g×2袋)」・合計注記「400g×5袋(2kg)」、
  「3袋セット（1袋あたり1.5kg）」、「1袋あたり」を倍数にしない、選択式「1kg/3kg」「選べる」は読まない。
- 「Lサイズ」などのサイズ表記は容量として読まない。読めない商品（None）はページに出さず、件数をログに出す（推測で埋めない）。
- リンクはすべて相対パス（github.io/<repo>/ 配下でも独自ドメインでも動く）。
- 環境変数 SITE_ORIGIN（例 https://petnavi.niconote.jp）があれば sitemap.xml を絶対URLで出し、
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
import shutil
import os
import re
import sys
import unicodedata
import urllib.parse

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
# 線画アイコン（24px・線幅1.8・currentColor）。絵文字は使わない（2026-11-06 監査: AIが作った感の典型）
_SVG = '<svg class="ic" viewBox="0 0 24 24" width="{s}" height="{s}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{d}</svg>'
ICON = {
    "paw": '<circle cx="12" cy="15" r="3.6"/><path d="M12 11.5c-1.1-2-3.4-2.3-4.3-.6-.8 1.6.6 3.4 2.1 3.4M12 11.5c1.1-2 3.4-2.3 4.3-.6.8 1.6-.6 3.4-2.1 3.4"/><circle cx="6" cy="9" r="1.6"/><circle cx="18" cy="9" r="1.6"/><circle cx="9" cy="5.5" r="1.6"/><circle cx="15" cy="5.5" r="1.6"/>',
    "dog": '<path d="M5 10c0-3 2-5 5-5h4c3 0 5 2 5 5v3c0 3-2 5-5 5h-4c-3 0-5-2-5-5z"/><path d="M5 11 3 7l4 1M19 11l2-4-4 1"/><circle cx="9.5" cy="11.5" r=".9" fill="currentColor"/><circle cx="14.5" cy="11.5" r=".9" fill="currentColor"/><path d="M12 14.5c-1 0-1.6-.6-1.6-1.2h3.2c0 .6-.6 1.2-1.6 1.2zM12 14.5v1.5M10.5 17c.8.8 2.2.8 3 0"/>',
    "cat": '<path d="M6 8 5 3l4 3h6l4-3-1 5c1 1.5 1.5 3 1.5 5 0 4-3.5 7-7.5 7S4.5 17 4.5 13c0-2 .5-3.5 1.5-5z"/><circle cx="9.5" cy="12" r=".9" fill="currentColor"/><circle cx="14.5" cy="12" r=".9" fill="currentColor"/><path d="M12 14.2l-.9.9h1.8zM3 13h3M18 13h3M3.5 16l2.8-1M20.5 16l-2.8-1"/>',
    "small": '<ellipse cx="12" cy="14" rx="7.5" ry="6"/><circle cx="7.5" cy="8" r="2.3"/><circle cx="16.5" cy="8" r="2.3"/><circle cx="9.5" cy="13" r=".9" fill="currentColor"/><circle cx="14.5" cy="13" r=".9" fill="currentColor"/><path d="M12 15.5v1.2M10.8 17.5c.7.6 1.7.6 2.4 0"/>',
    "reptile": '<path d="M4 16c3-1 4-4 7-5s5 1 8-1"/><path d="M11 11c-1-2 0-4 2-5 2.5-1 4.5 1 4 3.5-.3 1.5-1.5 2.3-3 2.5"/><circle cx="14.5" cy="8.5" r=".8" fill="currentColor"/><path d="M8.5 12.8 7 10.5M10 12 9.5 9M14 16l-1.5 2.5M17 14.5l1 3"/>',
    "bird": '<path d="M4 14c0-4 3-7 7-7 2 0 3.5 1 4.5 2.5L20 9l-3 3c0 4-3 7-7 7H6"/><path d="M8 13l4 2"/><circle cx="14" cy="10.5" r=".9" fill="currentColor"/>',
    "fish": '<path d="M3 12c3-4 7-6 11-6 2.5 0 4.5 1.5 6 3-1.5 1.5-3.5 3-6 3-4 0-8-2-11-6"/><path d="M3 12c3 4 7 6 11 6"/><path d="M20 9c.5 1 .5 5 0 6l-3-3z"/><circle cx="7.5" cy="11" r=".9" fill="currentColor"/>',
    "truck": '<path d="M3 7h10v9H3zM13 10h4l3 3v3h-7z"/><circle cx="7" cy="17" r="1.8"/><circle cx="17" cy="17" r="1.8"/>',
    "star": '<path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1.1 5.9L12 16.9l-5.3 2.8 1.1-5.9-4.3-4.1 5.9-.8z"/>',
    "star-f": '<path fill="currentColor" stroke="none" d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1.1 5.9L12 16.9l-5.3 2.8 1.1-5.9-4.3-4.1 5.9-.8z"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.3-4.3"/>',
    "share": '<circle cx="18" cy="5" r="2.5"/><circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="19" r="2.5"/><path d="m8.2 10.8 7.6-4.6M8.2 13.2l7.6 4.6"/>',
    "bag": '<path d="M6 8h12l1 12H5z"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/>',
}


# アイコン担当（ChatGPT）が描いた版があれば上書き（design/design_icons.py が検査済みのものだけを書き出す）
_ICON_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icons.json")
if os.path.exists(_ICON_FILE):
    ICON.update({k: v for k, v in json.load(open(_ICON_FILE, encoding="utf-8")).items() if k in ICON and v})


def favicon_svg():
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#0f6b62"/>'
            '<g transform="translate(4 4)" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            + ICON["paw"].replace('"currentColor"', '"#fff"') + '</g></svg>')


def icon(name, size=24):
    return _SVG.format(s=size, d=ICON[name])


PETS = [
    ("dog", "犬", "dog", ["犬", "犬・猫"]),
    ("cat", "猫", "cat", ["猫", "犬・猫"]),
    ("small", "小動物", "small", ["ハムスター", "うさぎ・モルモット", "うさぎ", "フェレット"]),
    ("reptile", "爬虫類", "reptile", ["爬虫類"]),
    ("bird", "小鳥", "bird", ["小鳥"]),
    ("fish", "魚", "fish", ["金魚", "魚"]),
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
    pct = min(avg, 5) / 5 * 100
    return (f'<span class="rv"><span class="st" aria-hidden="true"><span class="st-bg">{icon("star-f", 13) * 5}</span>'
            f'<span class="st-fg" style="width:{pct:.0f}%">{icon("star-f", 13) * 5}</span></span>{avg:.1f}<span class="rvn">（{n:,}件・楽天）</span></span>')


def ship(text):
    return f'<span class="ship{" free" if text == "送料無料" else " paid"}">{icon("truck", 13)}{E(text or "送料不明")}</span>'


SHIP_RULE = '<p class="ship-rule">価格は税込・<b>送料は含みません</b></p>'
HI_RATE = 5  # この倍率以上のショップに「ポイント◯倍」タグ（楽天 API の pointRate をそのまま表示）


def hi_tag(rate):
    return f'<span class="hi-pt">ポイント{rate}倍</span>' if rate >= HI_RATE else ""


GA_ID = os.environ.get("GA_MEASUREMENT_ID", "").strip()
if not re.fullmatch(r"G-[A-Z0-9]{4,20}", GA_ID):
    GA_ID = ""  # 未設定・形式違いならアクセス解析を入れない


def ga_tag():
    if not GA_ID:
        return ""
    return (f'<script async src="https://www.googletagmanager.com/gtag/js?id={GA_ID}"></script>'
            f'<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments)}}gtag("js",new Date());gtag("config","{GA_ID}");</script>')


def ga_note():
    if not GA_ID:
        return ""
    return ('<p>当サイトはアクセス解析に Google アナリティクスを使っています。Cookie を使って、個人を特定しない形でアクセスの情報を集めます。'
            '<a href="privacy.html">アクセス解析について</a></p>')


def page(title, body, desc, sample, crumbs=(), canonical="", og_image="", noindex=False):
    if not og_image:  # 個別の画像が無いページ（検索・マイリスト等）は、トップの共有画像を使う
        og_image = (ORIGIN_FOR_LD + "/og-today.png") if ORIGIN_FOR_LD else "og-today.png"
    band = ('<p class="sample">【サンプル表示】API 未接続のため架空の商品・価格です。実在の商品ではありません。</p>' if sample else "")
    bc = ""
    if crumbs:
        bc = '<nav class="bc" aria-label="現在地"><a href="index.html">トップ</a>' + "".join(
            f' <span aria-hidden="true">&gt;</span> ' + (f'<a href="{h}">{E(t)}</a>' if h else E(t)) for t, h in crumbs) + "</nav>"
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="favicon.svg" type="image/svg+xml"><meta name="google-site-verification" content="mYJrOMFA2vmJ7c0_N9cyQ9Vpia0qqpT8w31GSgfulHw">
<title>{E(title)}</title><meta name="description" content="{E(desc)}">{head_extra(title, desc, canonical, og_image, noindex)}
<link rel="stylesheet" href="style.css">{ga_tag()}<script src="app.js" defer></script></head><body>
<p class="promo">本ページはプロモーション（アフィリエイト広告）が含まれています。リンク先で購入されると、当サイトに紹介料が支払われます。</p>
{band}<header class="hd"><div class="hd-in"><a class="logo" href="index.html"><span class="mk">{icon("paw", 20)}</span>{SITE}</a>
<span class="hd-note">楽天の公式サイトではありません</span><a class="hd-fav" href="favorites.html">{icon("star", 16)}マイリスト<span id="favn"></span></a></div>
<form class="hd-search" action="search.html" role="search"><label for="hq" class="sr">商品を探す</label>
<input type="search" id="hq" name="q" placeholder="商品名・メーカー・カテゴリで探す" autocomplete="off"><button type="submit">{icon("search", 18)}<span>検索</span></button></form></header>
<main>{bc}{body}</main>
<footer id="notes"><p class="fn">※ ポイント＝税抜価格の1%×ショップの倍率（目安。SPU・クーポンは含みません）／実質＝価格−ポイント（支払額ではありません）／単価＝商品名の内容量から計算／「N店中の最安」＝当サイトが取得したショップの中での最安／「他店」＝同じ内容量のショップ（3店以上）の価格の中央値／価格は取得時点・送料はショップにより別</p><p>このサイトで掲載されている情報は、ペット用品 最安ナビの作成者により運営されています。価格、販売可能情報は、変更される場合があります。購入時に楽天市場店舗（www.rakuten.co.jp）に表示されている価格が、その商品の販売に適用されます。楽天アフィリエイトを利用した個人運営の比較サイトで、楽天グループ株式会社とは関係ありません。</p>
<p>運営: 合同会社にこのて（価格の取得・ページ作成は AI〈一郎〉が自動で行っています）</p>{ga_note()}
<p>{CREDIT}</p></footer>
</body></html>"""


HISTORY = {}
MIN_HISTORY_DAYS = 7   # これ未満のあいだは「記録中」とだけ出す
WINDOW = 14


def too_old(fetched_at, hours=24):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})", str(fetched_at or ""))
    if not m:
        return False
    D = datetime
    t = D.datetime(*map(int, m.groups()), tzinfo=D.timezone(D.timedelta(hours=9)))
    return D.datetime.now(D.timezone.utc) - t > D.timedelta(hours=hours)


def load_history():
    global HISTORY
    try:
        HISTORY = {}  # 楽天の規約：価格情報の保存は24時間まで（11/15 Taku 承認で価格履歴を廃止）。過去の価格は読まない
    except (OSError, ValueError):
        HISTORY = {}


def trend(pid, today_price):
    """過去 WINDOW 日の記録から「買い時」の事実を返す。記録が MIN_HISTORY_DAYS 未満なら days だけ返す。
    返り値: {days, lo, hi, prev, diff_prev, is_low, is_high, series}（series は [[日付, 最安], ...]）"""
    rows = HISTORY.get(pid, [])[-WINDOW:]
    out = {"days": len(rows), "series": [[r[0], r[1]] for r in rows]}
    if len(rows) < MIN_HISTORY_DAYS:
        return out
    prices = [r[1] for r in rows]
    out.update(lo=min(prices), hi=max(prices), prev=rows[-2][1] if len(rows) >= 2 else None)
    out["diff_prev"] = today_price - out["prev"] if out["prev"] is not None else 0
    out["is_low"] = today_price <= out["lo"]
    out["is_high"] = today_price >= out["hi"] and out["hi"] > out["lo"]
    return out


def trend_badge(t):
    """一覧用の短いバッジ。根拠は過去 WINDOW 日の取得記録（事実のみ）。"""
    if t["days"] < MIN_HISTORY_DAYS:
        return ""
    if t["is_low"] and t["hi"] > t["lo"]:
        return f'<span class="tb low">過去{t["days"]}日でいちばん安い</span>'
    if t["is_high"]:
        return f'<span class="tb high">過去{t["days"]}日でいちばん高い</span>'
    if t["diff_prev"] < 0:
        return f'<span class="tb down">前回より{-t["diff_prev"]:,}円安い</span>'
    return ""


def sparkline(series):
    if len(series) < 2:
        return ""
    ps = [v for _, v in series]
    lo, hi = min(ps), max(ps)
    w, h = 160, 36
    pts = []
    for i, v in enumerate(ps):
        x = i * (w - 4) / (len(ps) - 1) + 2
        y = h - 4 - ((v - lo) / (hi - lo) * (h - 8) if hi > lo else (h - 8) / 2)
        pts.append(f"{x:.1f},{y:.1f}")
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="過去{len(ps)}日の最安値の推移">'
            f'<polyline fill="none" stroke="currentColor" stroke-width="2" points="{" ".join(pts)}"/>'
            f'<circle cx="{pts[-1].split(",")[0]}" cy="{pts[-1].split(",")[1]}" r="3" fill="currentColor"/></svg>')


def trend_block(pid, o):
    t = trend(pid, o["price"])
    if t["days"] == 0:
        return ""
    if t["days"] < MIN_HISTORY_DAYS:
        return ""
    if t["is_low"] and t["hi"] > t["lo"]:
        head = f'<b class="low">今日は過去{t["days"]}日でいちばん安い価格です</b>'
    elif t["is_high"]:
        head = f'<b class="high">今日は過去{t["days"]}日でいちばん高い価格です</b>（最安は {t["lo"]:,}円）'
    elif t["hi"] == t["lo"]:
        head = f'過去{t["days"]}日、価格は変わっていません'
    else:
        head = f'過去{t["days"]}日の最安 {t["lo"]:,}円・最高 {t["hi"]:,}円（今日は最安より {o["price"] - t["lo"]:,}円高い）'
    prev = (f'　前回の取得より <b>{-t["diff_prev"]:,}円安く</b>なりました' if t["diff_prev"] < 0 else
            f'　前回の取得より {t["diff_prev"]:,}円上がりました' if t["diff_prev"] > 0 else "")
    return (f'<div class="trend">{sparkline(t["series"])}<p>{head}{prev}<br><small>根拠: 当サイトが毎日1回取得した、取得ショップ内の最安値（{t["series"][0][0]}〜{t["series"][-1][0]}）。それ以外の日・ショップの価格は含みません。</small></p></div>')


def free_best(offers):
    f = [x for x in offers if x.get("shipping") == "送料無料"]
    return min(f, key=lambda x: x["price"]) if f else None


def head_extra(title, desc, canonical, og_image, noindex):
    """SEO・共有プレビュー用の head 要素。canonical/og:url は SITE_ORIGIN があるときだけ（相対 URL の canonical は出さない）"""
    h = ""
    if noindex:
        h += '\n<meta name="robots" content="noindex,follow">'
    if canonical:
        h += f'\n<link rel="canonical" href="{E(canonical)}"><meta property="og:url" content="{E(canonical)}">'
    h += (f'\n<meta property="og:type" content="website"><meta property="og:site_name" content="{E(SITE)}">'
          f'<meta property="og:title" content="{E(title)}"><meta property="og:description" content="{E(desc)}">')
    if og_image:
        h += f'<meta property="og:image" content="{E(og_image)}">'
    return h


def item_meta(p, g, fetched_date):
    """商品ページの title / description。「最安」は必ず「今回取得した N店のうち」に限定する（楽天アフィリエイト ガイドライン）"""
    offers = p["offers"]
    o = min(offers, key=lambda x: x["price"])
    oe = min(offers, key=lambda x: (x["effective"], x["price"]))
    n = len(offers)
    nm = show_name(p["name"])
    short = nm if len(nm) <= 40 else nm[:39] + "…"
    title = f"{short}の最安値（楽天{n}店を比較）｜{SITE}"
    desc = (f"{nm[:50]}を楽天市場の{n}ショップで比較（{fetched_date}取得）。今回取得した{n}店のうち最安は{o['price']:,}円"
            f"（{ship_word(o['shipping'])}）、ポイント分を引いた目安では約{oe['effective']:,}円。")
    return title, desc


def ship_word(text):
    return text if text in ("送料無料",) else (text or "送料不明")


DROP_DAYS = 7      # 値下がりランキング：直近何日の最高値と比べるか
DROP_MAX = 0.40    # これより大きい下がり幅は内容量違い・別商品の混入の可能性が高いので載せない


def deal(p, unit=None):
    """今の取得値だけで作る「お得」：同じ内容量の他店の価格（中央値・3店以上）と、今の最安の差。価格は保存しない（規約：24時間まで）。
    楽天の製品データの平均価格（averagePrice）は実際には空で返るため使わない（11/15 確認）"""
    unit = unit or GROUP_UNIT.get(p.get("group"), "")
    avg = median_price(p, unit) if unit else 0
    now = best_offer(p)["price"]
    if not avg or now >= avg or (avg - now) / avg > DROP_MAX or (avg - now) / avg < DEAL_MIN:
        return None
    return {"hi": avg, "now": now, "d": avg - now, "since": None}


GROUP_UNIT = {}
DEAL_MIN = 0.10  # 他店より10%以上安いものだけ「お得」に出す（最安はほぼ必ず中央値より安いため、小さな差は出さない）


def drops(published):
    """同じ内容量の他店より安い商品（差額の大きい順）"""
    out = []
    for g, prods in published:
        for p in prods:
            GROUP_UNIT[p.get("group") or g["slug"]] = g["unit"]
            x = deal(p, g["unit"])
            if x:
                out.append(dict(x, p=p, g=g))
    return sorted(out, key=lambda x: (-x["d"], x["now"]))


def item_drop(pid, now):
    return None  # 価格履歴は廃止（規約）。お得は deal(p) で表示


def today_drops(published):
    """同じ内容量の他店より安い商品の数"""
    return len(drops(published))


def today_strip(published, fdate):
    n = today_drops(published)
    if not n:
        return ""
    return (f'<a class="today" href="#drops" data-ga="today_strip"><span class="td-k">{E(fdate[5:].replace("-", "/"))}のお得</span>'
            f'<span class="td-n">他店より安い <b>{n}</b>件</span><span class="td-b">ランキングを見る</span></a>')


def og_card(path, title, big, sub, foot="ペット用品 最安ナビ　楽天のショップを比較"):
    """共有プレビュー用の画像（1200×630）。商品名は使わず、決まった文言と数字だけ（フォントは assets/og-font.otf のサブセット）。
    Pillow が無い環境では作らない（画像なしで公開を続ける）"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False
    fp = os.path.join(ROOT, "assets", "og-font.otf")
    if not os.path.exists(fp):
        return False
    F = lambda n: ImageFont.truetype(fp, n)
    im = Image.new("RGB", (1200, 630), "#fffaf6")
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 1200, 14], fill="#0f6b62")
    d.text((70, 60), title, font=F(46), fill="#1f2328")
    d.text((70, 150), big, font=F(150), fill="#c2255c")
    d.text((70, 370), sub, font=F(36), fill="#1f2328")
    d.rectangle([0, 540, 1200, 630], fill="#0f6b62")
    d.text((70, 560), foot, font=F(36), fill="#ffffff")
    im.save(path, optimize=True)
    return True


def prev_drop(pid, now):
    """前日の記録からの値下がり額（内容量違いの疑いがある DROP_MAX 超は 0）"""
    rows = HISTORY.get(pid, [])
    if len(rows) < 2 or not rows[-2][1]:
        return 0
    prev = rows[-2][1]
    return prev - now if now < prev and (prev - now) / prev <= DROP_MAX else 0


def weekly_page(published, fdate):
    ds = drops(published)[:20]
    if not ds:
        return "", None
    li = []
    for i, x in enumerate(ds, 1):
        p = x["p"]
        th = img(p.get("image"), 128)
        pic = f'<img src="{E(th)}" alt="" width="64" height="64" loading="lazy">' if th else '<span class="noimg" aria-hidden="true"></span>'
        pd = prev_drop(p["productId"], x["now"])
        li.append(f'<li><a class="dr" href="item-{E(p["productId"])}.html" data-ga="weekly_rank" data-rank="{i}">'
                  f'<span class="rk">{i}</span><span class="pic">{pic}</span><span class="info"><span class="nm">{E(show_name(p["name"]))}</span>'
                  f'<span class="dr-pr"><small>他店</small><s>{x["hi"]:,}円</s> → <b>{x["now"]:,}</b>円</span>' +
                  f'</span><span class="dr-d">−{x["d"]:,}円<small>{round(x["d"] / x["hi"] * 100)}%</small></span></a></li>')
    top = ds[0]
    txt = (f'今日のペット用品 お得ランキング（{fdate[5:].replace("-", "/")}）1位は他店より −{top["d"]:,}円。'
           f'同じ内容量の他店（中央値）と、取得したショップの最安の比較（送料別・目安）')
    body = (f'<h1 class="ttl">お得ランキング</h1>'
            f'<p class="lead">他店（中央値）より安い商品 上位{len(ds)}件・1日2回更新</p>'
            f'<div class="wk-share"><button type="button" class="btn-main" data-share="{E(txt)}" data-kind="weekly">ランキングを共有</button>'
            f'<a class="btn-sub" href="https://twitter.com/intent/tweet?text={urllib.parse.quote(txt)}&url=__URL__" target="_blank" rel="noopener" data-ga="weekly_x">X に投稿</a>'
            f'<a class="btn-sub" href="https://line.me/R/msg/text/?{urllib.parse.quote(txt)}%20__URL__" target="_blank" rel="noopener" data-ga="weekly_line">LINE で送る</a></div>'
            f'<ol class="drops">{"".join(li)}</ol>'
            f'<p class="ptn">他店＝同じ内容量のショップの中央値（{fdate}取得）・{int(DROP_MAX*100)}%超の差は除外・送料別</p>')
    return body, top


def record_low(pid, now):
    """記録した期間（最大90日）の中で今がいちばん安いか。記録が5日未満なら判定しない"""
    rows = HISTORY.get(pid, [])
    if len(rows) < 5:
        return 0
    past = [r[1] for r in rows[:-1] if r[1]]
    return len(rows) if past and now < min(past) else 0


def spotlight(published, fdate):
    """人気（レビュー件数）×値下がり率の上位1件と、5,000円以上の商品の値下がり上位3件。すべて取得した事実から"""
    ds = drops(published)
    import math
    pop = [x for x in ds if x["p"].get("reviewCount", 0) >= 30]
    out = ""
    if pop:
        x = max(pop, key=lambda x: (x["d"] / x["hi"]) * math.log10(x["p"].get("reviewCount", 0) + 10))
        p = x["p"]
        th = img(p.get("image"), 300)
        pic = f'<img src="{E(th)}" alt="" width="120" height="120" loading="lazy">' if th else '<span class="noimg" aria-hidden="true"></span>'
        rl = record_low(p["productId"], x["now"])
        out += (f'<section class="hero"><p class="hero-k">人気商品のお得<span class="h2s">同じ内容量の他店と比べて</span></p><a class="hero-a" href="item-{E(p["productId"])}.html" data-ga="hero_click">'
                f'<span class="pic">{pic}</span><span class="info"><span class="nm">{E(show_name(p["name"]))}</span>'
                f'<span class="hero-rv">レビュー {p.get("reviewCount", 0):,}件の定番</span>'
                f'<span class="hero-d">−<b class="cnt" data-to="{x["d"]}">{x["d"]:,}</b>円<small>（{round(x["d"] / x["hi"] * 100)}%・他店 {x["hi"]:,}円）</small></span>'
                f'<span class="hero-pr"><b>{x["now"]:,}</b>円</span>'
                + (f'<span class="tb low">記録した{rl}日間でいちばん安い</span>' if rl else "") +
                '</span></a><p class="ptn">人気（レビュー数）×他店との差で選出・送料別</p></section>')
    prem = sorted([x for x in ds if x["now"] >= 5000], key=lambda x: -x["d"] / x["hi"])[:3]
    if prem:
        li = "".join(f'<li><a class="dr" href="item-{E(x["p"]["productId"])}.html" data-ga="premium_drop" data-rank="{i}"><span class="info"><span class="nm">{E(show_name(x["p"]["name"]))}</span>'
                     f'<span class="dr-pr"><small>他店</small><s>{x["hi"]:,}円</s> → <b>{x["now"]:,}</b>円</span></span><span class="dr-d">−{x["d"]:,}円<small>{round(x["d"] / x["hi"] * 100)}%</small></span></a></li>'
                     for i, x in enumerate(prem, 1))
        out += (f'<section class="drop-sec"><h2 class="h2">5,000円以上でお得な商品<span class="h2s">同じ内容量の他店と比べて・送料別</span></h2>'
                f'<ol class="drops">{li}</ol></section>')
    return out


def drop_section(published, fdate):
    ds = drops(published)[:5]
    if not ds:
        return ""
    li = []
    for i, x in enumerate(ds, 1):
        p = x["p"]
        th = img(p.get("image"), 128)
        pic = f'<img src="{E(th)}" alt="" width="64" height="64" loading="lazy">' if th else '<span class="noimg" aria-hidden="true"></span>'
        pct = round(x["d"] / x["hi"] * 100)
        li.append(f'<li><a class="dr" href="item-{E(p["productId"])}.html" data-ga="drop_rank" data-rank="{i}">'
                  f'<span class="rk">{i}</span><span class="pic">{pic}</span><span class="info"><span class="nm">{E(show_name(p["name"]))}</span>'
                  f'<span class="dr-pr"><small>他店</small><s>{x["hi"]:,}円</s> → <b>{x["now"]:,}</b>円</span>' +
                  f'</span><span class="dr-d">−{x["d"]:,}円<small>{pct}%</small></span></a></li>')
    return (f'<section class="drop-sec" id="drops"><h2 class="h2">他店より安い商品<span class="h2s">同じ内容量の他店と比べて・送料別</span></h2>'
            f'<ol class="drops">{"".join(li)}</ol><a class="more-link" href="weekly.html" data-ga="to_weekly">お得ランキング（上位20件）を見る</a></section>')


def median_price(p, unit):
    """お得額の基準：最安ショップと同じ内容量の通常品の価格の中央値（3店以上あるときだけ）"""
    ps = sorted(o["price"] for o in same_size(p["offers"], unit))
    if len(ps) < 3:
        return 0
    m = len(ps) // 2
    return ps[m] if len(ps) % 2 else (ps[m - 1] + ps[m]) // 2


def best_offer(p, key="price"):
    return min(p["offers"], key=lambda o: (o[key], o["price"]))


def unit_price(p, g):
    return best_offer(p)["price"] / p["amount"] * SCALE[g["unit"]] if p.get("amount") else None


OUTLET = re.compile(r"アウトレット|訳あり|わけあり|ワケアリ|中古|難あり|袋穴|外箱|パッケージ不良|賞味期限(?:間近|が近い|短い)")


def same_size(offers, unit):
    """お得額の比較に使うショップ: 最安ショップと商品名の内容量が同じ通常品（ケース売り・別容量・訳あり品との比較で差額を大きく見せない）。"""
    offers = [o for o in offers if not OUTLET.search(o["name"])] or offers[:1]
    base = min(offers, key=lambda o: o["price"])
    a0 = amount(base["name"], unit)
    if not a0:
        return [base]
    return [o for o in offers if (amount(o["name"], unit) or 0) and abs(amount(o["name"], unit) - a0) < 1e-6]


PT_INFO = ''
PT_NOTE = '<a class="kome" href="#notes" aria-label="注記を見る">※</a>'


def product_row(p, g, fn):
    o = best_offer(p)
    oe = best_offer(p, "effective")
    up = unit_price(p, g)
    th = img(p.get("image"), 128)
    pic = f'<img src="{E(th)}" alt="" width="96" height="96" loading="lazy">' if th else '<span class="noimg" aria-hidden="true"></span>'
    upt = f'<span class="up">{LABEL[g["unit"]]} {up:,.0f}円</span>' if up and up >= 10 else (f'<span class="up">{LABEL[g["unit"]]} {up:,.1f}円</span>' if up else "")
    buy = f'・購入単位 {fmt_amount(p["amount"], g["unit"])}' if p.get("amount") else ""
    same = same_size(p["offers"], g["unit"])
    gap = max(x["price"] for x in same) - o["price"]
    _d = deal(p, g["unit"])
    gapt = (f'<span class="save-s">他店より {_d["d"]:,}円安い</span>' if _d and not OUTLET.search(o["name"]) else "")
    fb = free_best(p["offers"])
    fattr = (f' data-fprice="{fb["price"]}" data-feff="{fb["effective"]}" data-fpt="{fb["points"]}" data-frate="{fb["pointRate"]}" data-fshop="{E(fb["shop"])}"' if fb else "")
    tbadge = trend_badge(trend(p["productId"], o["price"]))
    mr = max(x["pointRate"] for x in p["offers"])
    hit = f'<span class="hi-pt">ポイント{mr}倍の店あり</span>' if mr >= HI_RATE else ""
    return (f'<li data-pop="{p.get("reviewCount", 0)}" data-price="{o["price"]}" data-eff="{oe["effective"]}" data-unit="{up or 0:.4f}" data-aprice="{o["price"]}" data-aeff="{oe["effective"]}"{fattr}>'
            f'<button type="button" class="fav mini" data-fav="{E(p["productId"])}" data-price="{o["price"]}" aria-pressed="false" aria-label="お気に入りに追加">{icon("star", 18)}</button>'
            f'<a class="row" href="{fn}"><span class="pic">{pic}</span><span class="info">'
            f'<span class="nm">{E(show_name(p["name"]))}</span>{stars(p.get("reviewAverage", 0), p.get("reviewCount", 0))}'
            f'<span class="pr"><small class="scope">{len(p["offers"])}店中の最安</small><b class="pv">{o["price"]:,}</b>円 <span class="shipv">{ship(o["shipping"])}</span>{tbadge}</span>'
            f'<span class="pt"><span class="ptv">{o["points"]:,}pt（{o["pointRate"]}倍）</span></span>{hit}'
            f'<span class="meta">{buy.lstrip("・")}{"・" + upt if upt else ""}</span>{gapt}</span></a></li>')


SORT_JS = ('<script>(function(){var b=document.querySelectorAll(".sort button"),l=document.getElementById("list");'
           'b.forEach(function(x){x.addEventListener("click",function(){var k=x.dataset.k,a=[].slice.call(l.children);'
           'a.sort(function(p,q){return k==="pop"?q.dataset.pop-p.dataset.pop:(p.dataset[k]||1e12)-(q.dataset[k]||1e12)});'
           'a.forEach(function(e){l.appendChild(e)});b.forEach(function(y){y.setAttribute("aria-pressed",y===x)})})})})();</script>')


def category_body(g, prods, pet, fetched, carried):
    prods = sorted(prods, key=lambda p: -p.get("reviewCount", 0))
    rows = "".join(product_row(p, g, f"item-{p['productId']}.html") for p in prods)
    cheapest = min(prods, key=lambda p: best_offer(p)["price"])
    return (f'<h1 class="ttl">{E(g["title"])}</h1>'
            f'<p class="fresh1">{len(prods)}商品・<time data-fetched="{fetched}">{fetched}</time>・1日2回更新</p>'
            f'<div class="sort" role="group" aria-label="並べ替え"><span>並べ替え</span>'
            f'<label class="free-only"><input type="checkbox" id="freeonly"> 送料無料のショップだけ</label>'
            f'<button type="button" data-k="pop" aria-pressed="true">人気順</button>'
            f'<button type="button" data-k="price" aria-pressed="false">安い順</button>'
            f'<button type="button" data-k="eff" aria-pressed="false">ポイント込みで安い順</button>'
            f'<button type="button" data-k="unit" aria-pressed="false">{LABEL[g["unit"]]}が安い順</button></div>'
            f'<p class="free-note" id="freenote" hidden>送料無料のショップの中での最安に切り替えています。価格はそのショップの税込価格で、送料別の最安より高いことがあります。</p>'
            f'{SHIP_RULE}<ul class="list" id="list">{rows}</ul>'
            f'<div class="note">{("<p>" + carried.replace("<br>", "") + "</p>") if carried else ""}<p>{LABEL[g["unit"]]}の価格＝税込価格 ÷ 内容量（商品名から読み取り。読み取れない商品は載せていません）。'
            f'ポイントはショップの倍率から計算した目安（税抜価格の1%×倍率、1円未満切り捨て）。「ポイント込み」＝税込価格−この目安。送料はどちらにも含みません。計算根拠の最終確認：2026-11-08（楽天市場の通常ポイント 税抜100円につき1ポイント）。</p></div>{SORT_JS}')


def item_ld(p, g, pet_name, fn):
    """検索エンジン・AI 向けの構造化データ（Product＋AggregateOffer、パンくず）。評価は第三者のため載せない"""
    offers = p["offers"]
    prod = {"@context": "https://schema.org", "@type": "Product", "name": show_name(p["name"]),
            "category": g["title"], "image": img(p.get("image"), 300) or None,
            "offers": {"@type": "AggregateOffer", "priceCurrency": "JPY", "lowPrice": min(o["price"] for o in offers),
                       "highPrice": max(o["price"] for o in offers), "offerCount": len(offers),
                       "description": f'楽天市場で当サイトが取得した{len(offers)}ショップの税込価格の範囲（送料別）。{str(p.get("fetched_at") or FETCHED_FOR_LD)} 取得時点の目安'}}
    if p.get("maker"):
        prod["brand"] = {"@type": "Brand", "name": p["maker"]}
    if p.get("jan"):
        prod["gtin13"] = p["jan"]
    prod = {k: v for k, v in prod.items() if v is not None}
    base = (ORIGIN_FOR_LD + "/") if ORIGIN_FOR_LD else ""
    bc = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "トップ", "item": base or "index.html"},
        {"@type": "ListItem", "position": 2, "name": g["title"], "item": base + fn},
        {"@type": "ListItem", "position": 3, "name": show_name(p["name"])}]}
    return ''.join('<script type="application/ld+json">' + json.dumps(x, ensure_ascii=False).replace("</", "<\\/") + '</script>' for x in (prod, bc))


ORIGIN_FOR_LD = ""
FETCHED_FOR_LD = ""


def item_body(p, g, fetched):
    offers = sorted(p["offers"], key=lambda o: o["price"])
    o = offers[0]
    oe = min(offers, key=lambda x: (x["effective"], x["price"]))
    big = img(p.get("image"), 300)
    pic = f'<img src="{E(big)}" alt="" width="240" height="240">' if big else '<span class="noimg big" aria-hidden="true"></span>'
    up = unit_price(p, g)
    rows = "".join(
        f'<li data-price="{x["price"]}" data-eff="{x["effective"]}" data-free="{1 if x.get("shipping") == "送料無料" else 0}"><div class="shop"><span class="sn">{E(x["shop"])}</span>{hi_tag(x["pointRate"])}'
        + ('<span class="outlet">訳あり・アウトレットの可能性（商品名より）</span>' if OUTLET.search(x["name"]) else "") +
        f'<span class="pr"><b>{x["price"]:,}</b>円 {ship(x["shipping"])}</span>'
        f'<span class="pt">{x["points"]:,}pt（{x["pointRate"]}倍）{PT_NOTE}</span></div>'
        f'<a class="btn" href="{E(x["url"])}" rel="sponsored nofollow" target="_blank">ショップで見る</a></li>'
        for x in offers)
    same = same_size(offers, g["unit"])
    hi = max(same, key=lambda x: x["price"])
    hie = max(x["effective"] for x in same)
    _dl = deal(p, g["unit"])
    save = "" if OUTLET.search(o["name"]) or not _dl else (
        f'<p class="save">他店（同じ内容量 {len(same)}店の中央値 {_dl["hi"]:,}円）より <b>{_dl["d"]:,}円安い</b>{PT_NOTE}</p>')
    fb = free_best(offers)
    shipnote = ""
    if o.get("shipping") != "送料無料":
        shipnote = ('<p class="shipnote">この価格は送料別です。支払額は送料の分だけ増えます。'
                    + (f'送料無料で最安は <b>{E(fb["shop"])}</b> の {fb["price"]:,}円です。' if fb else "今回取得した中に送料無料のショップはありませんでした。") + '</p>')
    tr = trend_block(p["productId"], o)
    md = median_price(p, g["unit"])
    dr = deal(p, g["unit"])
    if dr:
        card_k, card_v = f'他店（中央値）{dr["hi"]:,}円より', f'−{dr["d"]:,}円'
    elif save:
        card_k, card_v = f'同じ内容量の{len(same)}ショップで', f'最大{hi["price"] - o["price"]:,}円の差'
    else:
        card_k, card_v = f'楽天{len(offers)}ショップを比較', ''
    card_attr = (f' data-cn="{E(show_name(p["name"])[:60])}" data-cp="{o["price"]}" data-ck="{E(card_k)}" data-cv="{E(card_v)}"'
                 f' data-cd="{E(str(p.get("fetched_at") or fetched)[:10])}"')
    share_text = (f'{show_name(p["name"])[:50]}｜楽天{len(offers)}ショップの価格比較（{str(p.get("fetched_at") or fetched)[:10]} 取得分）。取得した中で最安 {o["price"]:,}円'
                  + (f'（同じ内容量で最大 {hi["price"] - o["price"]:,}円の差）' if save else "") + '※価格・ポイントは取得時点の目安。PR（アフィリエイト）サイト')
    actions = (f'<div class="actions"><button type="button" class="fav" data-fav="{E(p["productId"])}" data-price="{o["price"]}" aria-pressed="false">{icon("star", 18)}お気に入り</button>'
               f'<button type="button" class="bought" data-bought="{E(p["productId"])}" data-price="{o["price"]}" data-md="{md}" aria-pressed="false">{icon("bag", 18)}買った日をメモ</button>'
               f'<button type="button" class="wish" data-wish="{E(p["productId"])}" data-price="{o["price"]}" aria-pressed="false">{icon("search", 18)}値下がりを待つ</button>'
               f'<button type="button" class="share" data-share="{E(share_text)}" data-card="1"{card_attr}>{icon("share", 18)}この比較を送る</button></div>'
               f'<form class="wish-f" id="wish-f" hidden><label>この値段になったら買う <input type="number" inputmode="numeric" min="1" id="wish-v"> 円</label><button type="submit">登録</button></form>'
               f'<p class="bought-msg" id="bought-msg" hidden></p><div id="pushbox"></div>')
    alt = ""
    if oe is not o:
        alt = (f'<p class="alt">ポイント込みなら <b>{E(oe["shop"])}</b> が {oe["effective"]:,}円でいちばんお得です'
               f'（{oe["pointRate"]}倍・{oe["points"]:,}ポイント）。</p>')
    allshops = (f'<a class="sub-link" href="{E(p["productUrl"])}" rel="sponsored nofollow" target="_blank">楽天市場の商品ページで全{p.get("itemCount", 0):,}ショップを見る</a>'
                if p.get("productUrl") else "")
    summary = (f'<p class="facts">要点：{E(show_name(p["name"])[:60])}は、楽天市場で取得した{len(offers)}ショップのうち最安 {o["price"]:,}円（{E(o["shop"])}・{"送料無料" if o.get("shipping") == "送料無料" else "送料別"}）。'
               + (f'同じ内容量のショップ間で最大{hi["price"] - o["price"]:,}円の差。' if save else "")
               + f'価格は{E(str(p.get("fetched_at") or fetched))}時点。</p>')
    return (f'<div class="item"><div class="item-pic">{pic}</div><div class="item-info">'
            f'<h1 class="ttl">{E(show_name(p["name"]))}</h1>{summary}'
            + (f'<p class="mk2"><a href="search.html?maker={urllib.parse.quote(p["maker"])}">{E(p["maker"])}の商品をもっと見る</a></p>' if p.get("maker") else "")
            + f'{stars(p.get("reviewAverage", 0), p.get("reviewCount", 0))}'
            f'{actions}'
            f'<div class="best"><p class="best-l">今回取得した{len(offers)}ショップ中の最安値（{E(str(p.get("fetched_at") or fetched))} 時点）</p>'
            f'<p class="pr big"><b>{o["price"]:,}</b>円 {ship(o["shipping"])}</p>'
            f'<p class="pr-note">税込・{"送料無料" if o.get("shipping") == "送料無料" else "送料は別（ショップにより異なる）"}・ポイントは目安（SPU・クーポンは含みません）</p>'
            f'<p class="pt">{o["points"]:,}ポイント（{o["pointRate"]}倍）{PT_NOTE}</p><p class="shop-l">{E(o["shop"])}</p>'
            + (f'<p class="up">{LABEL[g["unit"]]} {up:,.1f}円（内容量 {fmt_amount(p["amount"], g["unit"])}・商品名「{E(detail(o["name"]) or fmt_amount(p["amount"], g["unit"]))}」から読み取り）</p>' if up else "") +
            f'{shipnote}{save}{tr}<a class="btn big" href="{E(o["url"])}" rel="sponsored nofollow" target="_blank">取得した中で最安のショップで見る</a>{alt}</div></div></div>'
            f'<h2 class="h2">この商品を売っているショップ</h2>'
            f'<div class="sort" role="group" aria-label="並べ替え"><span>並べ替え</span>'
            f'<label class="free-only"><input type="checkbox" id="freeonly"> 送料無料だけ</label>'
            f'<button type="button" data-k="price" aria-pressed="true">安い順</button>'
            f'<button type="button" data-k="eff" aria-pressed="false">ポイント込みで安い順</button></div>'
            f'{SHIP_RULE}<ul class="shops" id="list">{rows}</ul><p class="empty" id="freeempty" hidden>送料無料のショップは、今回の取得にはありませんでした。</p>{allshops}'
            f'<div class="note"><p>価格取得日時: {E(str(p.get("fetched_at") or fetched))}</p></div>{SORT_JS}')


def main():
    groups = json.load(open(os.path.join(ROOT, "products.json"), encoding="utf-8"))["groups"]
    data = json.load(open(os.path.join(ROOT, "data", "prices.json"), encoding="utf-8"))
    sample = str(data.get("fetched_at", "")).startswith("SAMPLE")
    load_history()
    if sample:
        HISTORY.clear()
    origin = os.environ.get("SITE_ORIGIN", "").rstrip("/")
    out = os.path.join(ROOT, "site")
    if os.path.isdir(out):  # 前回のページを残さない（消えた商品・不公開カテゴリ）
        for f in os.listdir(out):
            if f.endswith(".html"):
                os.remove(os.path.join(out, f))
    os.makedirs(out, exist_ok=True)
    fetched = E(str(data.get("fetched_at", "不明")))
    fdate = str(data.get("fetched_at", ""))[:10] or "取得日不明"
    global ORIGIN_FOR_LD, FETCHED_FOR_LD
    ORIGIN_FOR_LD = origin or ""
    FETCHED_FOR_LD = str(data.get("fetched_at", ""))
    gmap = {g["slug"]: g for g in groups}
    published, files, index_rows = [], [], []
    for g in groups:
        prods = []
        for p in data.get("products", []):
            if p.get("group") != g["slug"] or not p.get("offers"):
                continue
            if any(x in p["name"] for x in g.get("exclude", [])):
                continue
            if not sample and too_old(p.get("fetched_at")):  # 楽天の規約：価格情報は24時間まで。前回取得の引き継ぎが24時間を超えたら載せない
                continue
            # 内容量は「実際に買う最安ショップの商品名」を優先（製品名は1袋分、ショップはケース売りのことがある）
            cheap = min(p["offers"], key=lambda o: o["price"])
            a = amount(cheap["name"], g["unit"]) or amount(p["name"], g["unit"])
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
            page(f"{g['title']}の価格・ポイント比較（楽天{len(prods)}商品）｜{SITE}", category_body(g, prods, pet, fetched, carried),
                 f"{g['title']}を楽天市場の人気{len(prods)}商品で比較（{fdate}取得）。商品ごとに、今回取得したショップのうち最安の価格とポイントの目安が分かります。",
                 sample, crumbs, canonical=(f"{origin}/{fn}" if origin else ""),
                 og_image=img(min(prods, key=lambda p: best_offer(p)["price"]).get("image"), 300) or ""))
        files.append(fn)
        for p in prods:
            ifn = f"item-{p['productId']}.html"
            o, oe, up = best_offer(p), best_offer(p, "effective"), unit_price(p, g)
            index_rows.append({"id": p["productId"], "n": show_name(p["name"]), "m": p.get("maker") or "", "c": g["slug"], "ct": g["title"],
                               "pet": [pt[0] for pt in PETS if g["animal"] in pt[3]], "kw": g["keyword"] + " " + g.get("pkeyword", ""),
                               "jan": p.get("jan") or "", "p": o["price"], "e": oe["effective"], "pt": o["points"], "r": o["pointRate"],
                               "sh": o["shipping"], "s": len(p["offers"]), "u": round(up, 2) if up else 0, "ul": LABEL[g["unit"]],
                               "img": img(p.get("image"), 128), "rv": p.get("reviewCount", 0), "ra": p.get("reviewAverage", 0),
                               "fp": (free_best(p["offers"]) or {}).get("price", 0), "fe": (free_best(p["offers"]) or {}).get("effective", 0),
                               "md": median_price(p, g["unit"]), "dl": (deal(p, g["unit"]) or {}).get("d", 0)})
            it, idsc = item_meta(p, g, fdate)
            open(os.path.join(out, ifn), "w", encoding="utf-8").write(
                page(it, item_body(p, g, fetched) + item_ld(p, g, pet[1], fn), idsc, sample,
                     ((pet[1], f"index.html#{pet[0]}"), (g["title"], fn), ("ショップ比較", None)),
                     canonical=(f"{origin}/{ifn}" if origin else ""), og_image=img(p.get("image"), 300) or ""))
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
        tabs.append(f'<li><a href="#{pid}">{globals()["icon"](icon, 20)}{name}</a></li>')
        cs = []
        for g, ps in sorted(cats, key=lambda c: (c[0].get("priority", 9), c[0]["title"])):
            cp = min(ps, key=lambda p: best_offer(p)["price"])
            th = img(cp.get("image"), 128)
            pic = f'<img src="{E(th)}" alt="" width="72" height="72" loading="lazy">' if th else '<span class="noimg" aria-hidden="true"></span>'
            cs.append(f'<li data-q="{E(g["title"] + " " + g["keyword"])}"><a class="cat" href="{g["slug"]}.html"><span class="pic">{pic}</span>'
                      f'<span class="info"><span class="nm">{E(g["title"])}</span><span class="meta">{len(ps)}商品を比較</span></span></a></li>')
        secs.append(f'<section class="pet-sec" id="{pid}"><h2 class="h2">{globals()["icon"](icon, 22)}{name}</h2><ul class="cats">{"".join(cs)}</ul></section>')
    from collections import Counter
    makers = [m for m, n in Counter(r["m"] for r in index_rows if r["m"]).most_common(10) if n >= 2]
    mk = "".join(f'<li><a href="search.html?maker={urllib.parse.quote(m)}">{E(m)}</a></li>' for m in makers)
    idx = ('<div class="vp"><p class="vp-t">楽天のペット用品、<b>どのショップがいちばん安い？</b></p>'
           f'<p class="vp-s">{sum(len(x[1]) for x in published)}商品・ショップごとの価格を1日2回チェック</p></div>'
           f'<ul class="tabs">{"".join(tabs)}</ul>'
           + '<a class="sd-cta" href="shindan.html" data-ga="shindan_cta"><span class="sd-cta-t">うちの子の消耗品、月いくら？</span><span class="sd-cta-s">いつもの商品を選ぶだけ・1分でわかる</span><span class="sd-cta-b">診断する</span></a>'
           + '<div id="welcome"></div>' + today_strip(published, fdate) + spotlight(published, fdate) + drop_section(published, fdate)
           + (f'<section class="mk-sec"><h2 class="h2">メーカーから探す</h2><ul class="chips">{mk}</ul></section>' if mk else "")
           + f'{"".join(secs)}<div class="note"><p>価格取得日時: {fetched}</p></div>')
    _ds = drops(published)
    og_ok = og_card(os.path.join(out, "og-today.png"), f"{fdate[5:].replace('-', '/')} ペット用品のお得（楽天市場）",
                    f"お得 {today_drops(published)}件", (f"他店（中央値）より最大 −{_ds[0]['d']:,}円（取得時点・送料別）" if _ds else "楽天のショップの価格を比較（1日2回更新）")) if fdate[:2] == "20" else False
    open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(
        page(f"{SITE}｜楽天市場のペット用品を価格・ポイントで比較", idx,
             f"楽天市場のペット用品{len(index_rows)}商品を、ショップごとの価格とポイントで比較（{fdate}取得）。猫砂・ドッグフード・小動物や爬虫類、小鳥・魚の餌まで。",
             sample, canonical=(f"{origin}/" if origin else ""),
             og_image=((f"{origin}/og-today.png" if origin else "og-today.png") if og_ok else "")))
    wk, wtop = weekly_page(published, fdate)
    if wk:
        wog = og_card(os.path.join(out, "og-weekly.png"), f"ペット用品 お得ランキング（{fdate[5:].replace('-', '/')}）",
                      f"1位 −{wtop['d']:,}円", f"他店（中央値）より {round(wtop['d'] / wtop['hi'] * 100)}% 安い（取得時点・送料別）")
        wurl = f"{origin}/weekly.html" if origin else "weekly.html"
        open(os.path.join(out, "weekly.html"), "w", encoding="utf-8").write(page(
            f"ペット用品 お得ランキング（{fdate}）｜{SITE}", wk.replace("__URL__", urllib.parse.quote(wurl, safe="")),
            f"楽天市場のペット用品で、他店（中央値）より安い商品のランキング（{fdate}更新）。",
            sample, (("お得ランキング", None),), canonical=(wurl if origin else ""),
            og_image=((f"{origin}/og-weekly.png" if origin else "og-weekly.png") if wog else "")))
        files.append("weekly.html")
    open(os.path.join(out, "style.css"), "w", encoding="utf-8").write(CSS)
    vp = os.path.join(ROOT, "push", "vapid_public.txt")  # 値下がり通知の公開鍵（毎朝の更新処理が KV を使えるときだけ作る。無ければ通知ボタンは出ない）
    vkey = open(vp, encoding="utf-8").read().strip() if os.path.exists(vp) else ""
    open(os.path.join(out, "app.js"), "w", encoding="utf-8").write(APP_JS.replace("__VAPID__", vkey if re.match(r"^[A-Za-z0-9_-]{80,100}$", vkey) else ""))
    if os.path.exists(os.path.join(ROOT, "assets", "sw.js")):
        shutil.copy(os.path.join(ROOT, "assets", "sw.js"), os.path.join(out, "sw.js"))
    json.dump({"fetched_at": str(data.get("fetched_at", "")), "pets": [[p[0], p[1]] for p in PETS],
               "cats": [[g["slug"], g["title"]] for g, _ in published], "items": index_rows},
              open(os.path.join(out, "search.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    open(os.path.join(out, "search.html"), "w", encoding="utf-8").write(page(
        f"商品を探す｜{SITE}", '<h1 class="ttl">商品を探す</h1>'
        '<form class="filters" id="sf"><label class="sr" for="q">キーワード</label>'
        '<input type="search" id="q" name="q" placeholder="商品名・メーカー・カテゴリ・JANコード" autocomplete="off">'
        '<div class="fl-row"><label>ペット<select name="pet" id="pet"><option value="">すべて</option></select></label>'
        '<label>カテゴリ<select name="cat" id="cat"><option value="">すべて</option></select></label>'
        '<label>メーカー<select name="maker" id="maker"><option value="">すべて</option></select></label>'
        '<label class="free-only"><input type="checkbox" name="free" id="free"> 送料無料のショップだけ</label><label>並べ替え<select name="sort" id="sort"><option value="pop">人気順</option><option value="price">安い順</option>'
        '<option value="eff">ポイント込みで安い順</option><option value="unit">単価が安い順</option></select></label></div></form>'
        '<p class="count" id="count" aria-live="polite"></p><ul class="list" id="results"></ul>'
        f'<div class="note"><p>価格取得日時: {fetched}</p><p>「最安値」は今回取得したショップの中での最安です。ポイントはショップのポイント倍率から計算した目安です。</p></div>',
        "楽天市場のペット用品を、商品名・メーカー・カテゴリで探して価格とポイントを比較", sample, (("商品を探す", None),), noindex=True))
    open(os.path.join(out, "shindan.html"), "w", encoding="utf-8").write(page(
        f"うちの子の消耗品、月いくら？ 1分診断｜{SITE}",
        '<h1 class="ttl">うちの子の消耗品、月いくら？</h1>'
        '<p class="lead">ペットを選んで、いつも買っている商品と月に使う数を選ぶだけ。楽天市場で取得したショップの最安で買ったときの月額と、取得したショップの中央値で買った場合との差がわかります。</p>'
        '<div id="shindan"><div class="sd-pets" id="sd-pets" role="group" aria-label="ペット"></div><div id="sd-list"></div>'
        '<section class="sd-res" id="sd-res" hidden aria-live="polite"></section></div>'
        f'<div class="note"><p>価格取得日時: <time data-fetched="{fetched}">{fetched}</time></p><p>月額は「取得したショップの最安価格（税込・送料別）×月に使う数」です。差額は、最安ショップと同じ内容量の通常品が3店以上あるときだけ、その価格の中央値との差で計算しています（3店未満の商品は差額0として扱います）。ポイントは目安です。</p></div>',
        "ペットの消耗品（フード・猫砂・シーツなど）の月額を、楽天市場の最安価格で1分診断。結果は画像にして共有できます。",
        sample, (("1分診断", None),), canonical=(f"{origin}/shindan.html" if origin else ""),
        og_image=(f"{origin}/og-shindan.png" if origin else "og-shindan.png")))
    files.append("shindan.html")
    og_src = os.path.join(ROOT, "assets", "og-shindan.png")
    if os.path.exists(og_src):
        shutil.copy(og_src, os.path.join(out, "og-shindan.png"))
    open(os.path.join(out, "favorites.html"), "w", encoding="utf-8").write(page(
        f"マイリスト（いつもの買い物・値下がり待ち・お気に入り）｜{SITE}", '<h1 class="ttl">マイリスト</h1>'
        '<div id="savings"></div><div id="wshare" class="sd-share"></div>'
        '<div class="ml-empty" id="allempty" hidden><p class="ml-t">まだ何もありません</p><ol class="ml-steps">'
        f'<li>{globals()["icon"]("search", 22)}<b>商品を見る</b><span>価格を自動で覚えます</span></li>'
        f'<li>{globals()["icon"]("star", 22)}<b>お気に入り</b><span>☆で登録</span></li>'
        '<li><span class="ml-n">円</span><b>安くなったら</b><span>ここと通知でお知らせ</span></li></ol>'
        '<a class="btn-main" href="index.html">商品を探す</a></div>'
        '<section class="ml" id="sec-bought"><h2 class="h2">いつもの買い物</h2><ul class="list" id="boughtlist"></ul></section>'
        '<section class="ml" id="sec-wish"><h2 class="h2">値下がり待ち</h2><ul class="list" id="wishlist"></ul></section>'
        '<section class="ml" id="sec-seen"><h2 class="h2">最近見た商品</h2><ul class="list" id="seenlist"></ul></section>'
        '<section class="ml" id="sec-fav"><h2 class="h2">お気に入り</h2><ul class="list" id="favlist"></ul></section>'
        '<div id="pushbox"></div>'
        '<p class="empty" id="boughtempty" hidden></p><p class="empty" id="wishempty" hidden></p><p class="empty" id="seenempty" hidden></p><p class="empty" id="favempty" hidden></p>'
        f'<details class="about"><summary>このページについて</summary><p>記録はこのブラウザだけに保存します（ログイン不要。端末間で共有されず、サイトデータを消すと消えます）。楽天の購入履歴とは連動しません。価格は {fetched} 取得、当サイトが取得したショップの最安（税込・送料別）です。</p></details>',
        "いつもの買い物・値下がり待ち・お気に入りの価格", sample, (("マイリスト", None),), noindex=True))
    open(os.path.join(out, "favicon.svg"), "w", encoding="utf-8").write(favicon_svg())
    cats = "\n".join(f"- [{g['title']}]({(origin + '/') if origin else ''}{g['slug']}.html): {len(prods)}商品" for g, prods in published)
    open(os.path.join(out, "llms.txt"), "w", encoding="utf-8").write(
        f"# {SITE}\n\n> 楽天市場のペット消耗品（フード・猫砂・ペットシーツ・小動物/爬虫類/小鳥/魚の餌など）を、商品ごとに取得したショップの最安価格・ポイント・送料の有無で比べるサイト。"
        f"運営: 合同会社にこのて（価格の取得とページ作成は AI〈一郎〉が自動で実施）。楽天グループとは関係のない個人運営の比較サイトで、楽天アフィリエイトを利用しています。\n\n"
        f"- 価格の取得: 毎日1回、楽天ウェブサービスの API から。最新の取得日 {fdate}\n"
        "- 「最安」は取得したショップの中での最安。送料・クーポン・SPU は含まない。ポイントはショップ倍率からの目安\n"
        "- 価格は1日2回取り直し、保存しない（楽天の規約：価格情報は24時間まで）。お得は同じ内容量の他店（中央値）と今の最安の差で表示\n\n"
        f"## カテゴリ\n{cats}\n\n## 主なページ\n- [1分診断：うちの子の消耗品、月いくら？]({(origin + '/') if origin else ''}shindan.html)\n- [商品を探す]({(origin + '/') if origin else ''}search.html)\n")
    if GA_ID:
        open(os.path.join(out, "privacy.html"), "w", encoding="utf-8").write(page(
            f"アクセス解析について｜{SITE}", '<h1 class="ttl">アクセス解析について</h1><div class="note" style="font-size:13px">'
            '<p>当サイトは、サイトの改善のために Google アナリティクス（Google LLC）を使っています。Google アナリティクスは Cookie を使って、'
            '見られたページ、楽天市場へのリンクのクリック、検索・並べ替え・お気に入りの操作などの情報を、個人を特定しない形で集めます。</p>'
            '<p>集めた情報は Google のプライバシーポリシーに基づいて管理されます。詳しくは '
            '<a href="https://policies.google.com/technologies/partner-sites?hl=ja" rel="nofollow" target="_blank">Google のサービスを使用するサイトやアプリから収集した情報の Google による使用</a> をご覧ください。</p>'
            '<p>Cookie を使われたくない場合は、ブラウザの設定で Cookie を無効にするか、'
            '<a href="https://tools.google.com/dlpage/gaoptout?hl=ja" rel="nofollow" target="_blank">Google アナリティクス オプトアウト アドオン</a> を使ってください。</p>'
            '<p>お気に入りはお使いのブラウザの中だけに保存され、当サイトや Google には送られません。</p></div>',
            "アクセス解析について", sample, (("アクセス解析について", None),)))
    robots = "User-agent: *\nAllow: /\n"
    if origin:
        today = fdate if re.match(r"\d{4}-\d{2}-\d{2}$", fdate) else datetime.date.today().isoformat()
        locs = [f"{origin}/"] + [f"{origin}/{f}" for f in files]  # noindex の search/favorites は載せない
        urls = "".join(f"<url><loc>{E(u)}</loc><lastmod>{today}</lastmod></url>" for u in locs)
        open(os.path.join(out, "sitemap.xml"), "w", encoding="utf-8").write(
            f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n')
        robots += f"Sitemap: {origin}/sitemap.xml\n"
    open(os.path.join(out, "robots.txt"), "w", encoding="utf-8").write(robots)
    return 0


APP_JS = r"""(function(){
var VAPID="__VAPID__";var KEY="petsite_fav_v1",BKEY="petsite_bought_v1",WKEY="petsite_wish_v1",SKEY="petsite_seen_v1",mem={},bmem={},wmem={},smem={},DATA=null;
function sload(){try{return JSON.parse(localStorage.getItem(SKEY)||"{}")}catch(e){return smem}}
function ssave(o){var ids=Object.keys(o).sort(function(a,b){return o[b].t-o[a].t});if(ids.length>60){ids.slice(60).forEach(function(k){delete o[k]})}try{localStorage.setItem(SKEY,JSON.stringify(o))}catch(e){smem=o}}
function seen(){var b=document.querySelector(".actions [data-fav]");if(!b)return;var id=b.dataset.fav,o=sload(),r=o[id];  // 価格は保存しない（楽天の規約：価格情報は24時間まで）
 if(!r)o[id]={t:Date.now(),v:1,c:0};else{r.t=Date.now();r.v=(r.v||1)+1;delete r.p;delete r.lo;delete r.cp}ssave(o)}
document.addEventListener("click",function(e){var a=e.target.closest('a[rel~="sponsored"]');if(!a)return;var b=document.querySelector(".actions [data-fav]");if(!b)return;var o=sload(),r=o[b.dataset.fav];if(r){r.c=(r.c||0)+1;r.ct=Date.now();ssave(o)}},true);
function renderSeen(){var l=document.getElementById("seenlist");if(!l)return;data(function(d){var o=sload(),by={};d.items.forEach(function(x){by[x.id]=x});
 var ids=Object.keys(o).filter(function(id){return by[id]}).sort(function(a,b){return(by[b].dl||0)-(by[a].dl||0)||o[b].t-o[a].t}).slice(0,20);
 l.innerHTML=ids.map(function(id){var x=by[id],r=o[id];
  var msg=x.dl>0?'<span class="badge win">他店より '+yen(x.dl)+'円安い</span>':'';
  return row(x,'<span class="diff">'+msg+'</span>')}).join("");
 var e=document.getElementById("seenempty");if(e)e.hidden=ids.length>0;paint()})}
function wload(){try{return JSON.parse(localStorage.getItem(WKEY)||"{}")}catch(e){return wmem}}
function wsave(v){wmem=v;try{localStorage.setItem(WKEY,JSON.stringify(v))}catch(e){}}
function hist(r){return r.h&&r.h.length?r.h:[[r.t,r.p]]}
function cyc(r){var h=hist(r);if(h.length<2)return 0;return Math.round((h[h.length-1][0]-h[0][0])/864e5/(h.length-1))}
function bload(){try{return JSON.parse(localStorage.getItem(BKEY)||"{}")}catch(e){return bmem}}
function bsave(v){bmem=v;try{localStorage.setItem(BKEY,JSON.stringify(v))}catch(e){}}
function load(){try{return JSON.parse(localStorage.getItem(KEY)||"{}")}catch(e){return mem}}
function save(v){mem=v;try{localStorage.setItem(KEY,JSON.stringify(v))}catch(e){}}
function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
function norm(s){s=String(s||"");try{s=s.normalize("NFKC")}catch(e){}s=s.toLowerCase();
 return s.replace(/[\u30a1-\u30f6]/g,function(c){return String.fromCharCode(c.charCodeAt(0)-0x60)}).replace(/\s+/g," ")}
function yen(n){return Number(n).toLocaleString("ja-JP")}
function toast(m){var t=document.getElementById("toast");if(!t){t=document.createElement("div");t.id="toast";t.setAttribute("role","status");document.body.appendChild(t)}t.textContent=m;t.classList.add("on");clearTimeout(toast._t);toast._t=setTimeout(function(){t.classList.remove("on")},3500)}
function mlEmpty(){var a=document.getElementById("allempty");if(!a)return;var n=0;["boughtlist","wishlist","seenlist","favlist"].forEach(function(i){var l=document.getElementById(i);if(l&&l.children.length)n++});a.hidden=n>0;var sv=document.getElementById("savings");if(sv&&n===0)sv.hidden=true}
function paint(){mlEmpty();var bm=bload(),wl=wload();document.querySelectorAll("[data-bought]").forEach(function(b){var r=bm[b.dataset.bought],h=r?hist(r):[],today=h.length&&new Date(h[h.length-1][0]).toDateString()===new Date().toDateString();b.setAttribute("aria-pressed",!!today);var bag='<svg class="ic" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 8h12l1 12H5z"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/></svg>';b.innerHTML=bag+(today?"今日買った（記録済み）":h.length?"買った（"+(h.length+1)+"回目を記録）":"買った"); var m=document.getElementById("bought-msg");if(m){m.hidden=!r;if(r){var cur=+b.dataset.price||0,d=cur-r.p,c=cyc(r);m.innerHTML=(d<0?"前回（"+new Date(r.t).toLocaleDateString("ja-JP")+"・"+yen(r.p)+"円）より <b class=\"win\">"+yen(-d)+"円安い</b>（あなたが記録した価格との比較）":d>0?"前回（"+yen(r.p)+"円）より "+yen(d)+"円上がっています。<b>前回はいいタイミングで買えています</b>":"前回と同じ価格です")+(c?"（いつも約"+c+"日ごとに購入）":"")}}});document.querySelectorAll("[data-wish]").forEach(function(b){var w=wl[b.dataset.wish];b.setAttribute("aria-pressed",!!w);b.lastChild.nodeValue=w?"目標 "+yen(w.g)+"円で待ち中":"値下がりを待つ"});
 var f=load();document.querySelectorAll("[data-fav]").forEach(function(b){var on=!!f[b.dataset.fav];
 b.setAttribute("aria-pressed",on);b.setAttribute("aria-label",on?"お気に入りから外す":"お気に入りに追加");
 var sv=on?'<svg class="ic" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1.1 5.9L12 16.9l-5.3 2.8 1.1-5.9-4.3-4.1 5.9-.8z"/></svg>':'<svg class="ic" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1.1 5.9L12 16.9l-5.3 2.8 1.1-5.9-4.3-4.1 5.9-.8z"/></svg>';
 b.innerHTML=b.classList.contains("mini")?sv:sv+(on?"お気に入り済み":"お気に入り")});
 var c=document.getElementById("favn");if(c){var n=Object.keys(f).length;c.textContent=n?"（"+n+"）":""}}
function ga(n,p){try{if(window.gtag)gtag("event",n,p||{})}catch(e){}}
document.addEventListener("click",function(e){var g=e.target.closest("[data-ga]");if(g)ga(g.dataset.ga,{rank:+g.dataset.rank||0})});
document.addEventListener("click",function(e){var a=e.target.closest('a[rel~="sponsored"]');
 if(a)ga("click_rakuten",{link_type:a.classList.contains("big")?"best_shop":(a.classList.contains("sub-link")?"all_shops":"shop"),page_path:location.pathname})},true);
document.addEventListener("click",function(e){var b=e.target.closest("[data-bought]");if(!b)return;var bm=bload(),id=b.dataset.bought;
 var pr=+b.dataset.price||0,md=+b.dataset.md||0,sv=md>pr?md-pr:0,now=Date.now(),r=bm[id];
 if(r){var h=hist(r);if(new Date(h[h.length-1][0]).toDateString()===new Date().toDateString()){h.pop();if(h.length){r.h=h;r.t=h[h.length-1][0];r.p=h[h.length-1][1]}else delete bm[id];ga("unmark_bought",{item_id:id});bsave(bm);paint();toast("今日の記録を取り消しました");return}
  var cheaper=pr<r.p;h.push([now,pr,sv]);r.h=h;r.t=now;r.p=pr;ga("mark_bought",{item_id:id,times:h.length});toast(h.length+"回目の購入を記録しました（"+yen(pr)+"円）"+(sv?"。中央値より "+yen(sv)+"円安く買えました":"")+"。いつも約"+cyc(r)+"日ごとに買っています");if(cheaper)happy("前回より安く買えました","前回より"+yen(h[h.length-2][1]-pr)+"円安く買えました")}
 else{bm[id]={p:pr,t:now,h:[[now,pr,sv]]};ga("mark_bought",{item_id:id,times:1});toast("買った価格（"+yen(pr)+"円）を記録しました"+(sv?"。中央値より "+yen(sv)+"円安く買えました":"")+"。次に来たとき、前回より安ければマイリストの一番上に出します")}
 bsave(bm);paint();if(document.getElementById("boughtlist"))renderBought()});
document.addEventListener("click",function(e){var b=e.target.closest("[data-bdel]");if(!b)return;var bm=bload();delete bm[b.dataset.bdel];bsave(bm);renderBought()});
document.addEventListener("click",function(e){var b=e.target.closest("[data-wdel]");if(!b)return;var w=wload();delete w[b.dataset.wdel];wsave(w);renderWish();paint()});
document.addEventListener("click",function(e){var b=e.target.closest("[data-wish]");if(!b)return;var w=wload(),id=b.dataset.wish,f=document.getElementById("wish-f");
 if(w[id]){delete w[id];wsave(w);paint();ga("unwish",{item_id:id});toast("値下がり待ちから外しました");return}
 if(!f)return;f.hidden=false;var v=document.getElementById("wish-v");v.value=Math.floor((+b.dataset.price||0)*0.9/10)*10;v.focus();
 f.onsubmit=function(ev){ev.preventDefault();var g=+v.value;if(!(g>0))return;var w2=wload();w2[id]={g:g,t:Date.now()};wsave(w2);f.hidden=true;paint();ga("wish",{item_id:id,target:g});toast(yen(g)+"円以下になったらマイリストとトップでお知らせします（このブラウザで開いたとき）")}});
document.addEventListener("click",function(e){var b=e.target.closest("[data-share]");if(!b)return;var t=b.dataset.share,u=b.dataset.url?new URL(b.dataset.url,location.href).href:location.href;ga("share",{item_id:b.dataset.url||location.pathname,kind:b.dataset.kind||"item"});
 if(b.dataset.card){var cv=itemCard(b);if(cv&&cv.toBlob&&navigator.canShare){cv.toBlob(function(bl){var f=bl&&new File([bl],"petsite.png",{type:"image/png"});if(f&&navigator.canShare({files:[f]}))navigator.share({files:[f],text:t,url:u}).catch(function(){});else navigator.share?navigator.share({title:document.title,text:t,url:u}).catch(function(){}):toast("共有に対応していないブラウザです")});return}}
 if(navigator.share){navigator.share({title:document.title,text:t,url:u}).catch(function(){})}else if(navigator.clipboard){navigator.clipboard.writeText(t+" "+u).then(function(){toast("比較の内容とリンクをコピーしました")}).catch(function(){prompt("コピーしてください",t+" "+u)})}else{prompt("コピーしてください",t+" "+u)}});
document.addEventListener("change",function(e){if(e.target.id!=="freeonly")return;var on=e.target.checked,l=document.getElementById("list");if(!l)return;ga("free_only",{on:on});var fn=document.getElementById("freenote");if(fn)fn.hidden=!on;
 var n=0;[].slice.call(l.children).forEach(function(li){
  if(li.dataset.free!==undefined){li.hidden=on&&li.dataset.free!=="1";if(!li.hidden)n++;return}
  if(on){if(!li.dataset.fprice){li.hidden=true;return}li.hidden=false;n++;li.dataset.price=li.dataset.fprice;li.dataset.eff=li.dataset.feff;
   li.querySelector(".pv").textContent=yen(li.dataset.fprice);li.querySelector(".shipv").innerHTML='<span class="ship free">送料無料</span>'+(li.dataset.fprice!==li.dataset.aprice?'<small class="alt-p">（送料別の最安 '+yen(li.dataset.aprice)+'円）</small>':'');li.querySelector(".ptv").textContent=yen(li.dataset.fpt)+"ポイント（"+li.dataset.frate+"倍・目安）";var sc=li.querySelector(".scope");if(sc){sc.dataset.o=sc.dataset.o||sc.textContent;sc.textContent="送料無料のショップの中で最安"}li.querySelectorAll(".up,.save-s").forEach(function(z){z.style.display="none"})}
  else{li.hidden=false;n++;li.dataset.price=li.dataset.aprice;li.dataset.eff=li.dataset.aeff;var o=li.dataset.orig;if(o){var x=JSON.parse(o);li.querySelector(".pv").textContent=x.p;li.querySelector(".shipv").innerHTML=x.s;li.querySelector(".ptv").textContent=x.t}var sc2=li.querySelector(".scope");if(sc2&&sc2.dataset.o)sc2.textContent=sc2.dataset.o;li.querySelectorAll(".up,.save-s").forEach(function(z){z.style.display=""})}});
 var fe=document.getElementById("freeempty");if(fe)fe.hidden=n>0;var act=document.querySelector('.sort button[aria-pressed="true"]');if(act)act.click()});
function keepOrig(){document.querySelectorAll("#list li[data-fprice], #list li[data-aprice]").forEach(function(li){if(li.dataset.orig)return;var pv=li.querySelector(".pv"),sv=li.querySelector(".shipv"),tv=li.querySelector(".ptv");if(pv&&sv&&tv)li.dataset.orig=JSON.stringify({p:pv.textContent,s:sv.innerHTML,t:tv.textContent})})}
document.addEventListener("click",function(e){var b=e.target.closest("[data-fav]");if(!b)return;e.preventDefault();
 var f=load(),id=b.dataset.fav;if(f[id]){delete f[id];ga("remove_favorite",{item_id:id})}else{f[id]={t:Date.now()};ga("add_favorite",{item_id:id});toast("お気に入りに入れました（"+yen(+b.dataset.price||0)+"円・"+new Date().toLocaleDateString("ja-JP")+"）。値下がりしたら お気に入り ページで分かります")}save(f);paint();
 if(document.getElementById("favlist"))renderFav()});
function data(cb){if(DATA)return cb(DATA);fetch("search.json").then(function(r){return r.json()}).then(function(d){DATA=d;cb(d)})
 .catch(function(){var c=document.getElementById("count")||document.getElementById("favempty");if(c){c.hidden=false;c.textContent="商品データを読み込めませんでした。ページを再読み込みしてください。"}})}
function unit(x){return x.u?x.ul+" "+(x.u>=10?yen(Math.round(x.u)):x.u.toFixed(1))+"円":""}
var STAR='<svg class="ic" viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path fill="currentColor" d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1.1 5.9L12 16.9l-5.3 2.8 1.1-5.9-4.3-4.1 5.9-.8z"/></svg>';
function row(x,extra){var s5=STAR+STAR+STAR+STAR+STAR,st=x.rv?'<span class="rv"><span class="st" aria-hidden="true"><span class="st-bg">'+s5+'</span><span class="st-fg" style="width:'+Math.round(Math.min(x.ra,5)/5*100)+'%">'+s5+'</span></span>'+Number(x.ra).toFixed(1)+'<span class="rvn">（'+yen(x.rv)+'件・楽天）</span></span>':"";
 return '<li><button type="button" class="fav mini" data-fav="'+esc(x.id)+'" data-price="'+x.p+'" aria-pressed="false" aria-label="お気に入りに追加"></button>'+
 '<a class="row" href="item-'+esc(x.id)+'.html"><span class="pic">'+(x.img?'<img src="'+esc(x.img)+'" alt="" width="96" height="96" loading="lazy">':'<span class="noimg"></span>')+'</span><span class="info">'+
 '<span class="nm">'+esc(x.n)+'</span><span class="meta">'+esc(x.m)+(x.m?"・":"")+esc(x.ct)+'</span>'+st+
 '<span class="pr"><b>'+yen(x.p)+'</b>円 <span class="ship'+(x.sh==="送料無料"?" free":" paid")+'">'+esc(x.sh)+'</span></span>'+
 (x._free?'':'<span class="pt">'+yen(x.pt)+'pt（'+x.r+'倍）<span class="kome">※</span></span>')+
 '<span class="meta">'+x.s+'店中の最安'+(unit(x)?"・"+unit(x):"")+'</span>'+(extra||"")+'</span></a></li>'}
function fill(sel,opts,val){var el=document.getElementById(sel);opts.forEach(function(o){var op=document.createElement("option");op.value=o[0];op.textContent=o[1];el.appendChild(op)});el.value=val||""}
function search(){var f=document.getElementById("sf");if(!f)return;var u=new URLSearchParams(location.search);
 data(function(d){var mk={};d.items.forEach(function(x){if(x.m)mk[x.m]=(mk[x.m]||0)+1});
  var makers=Object.keys(mk).sort(function(a,b){return mk[b]-mk[a]||a.localeCompare(b,"ja")}).map(function(m){return[m,m+"（"+mk[m]+"）"]});
  fill("pet",d.pets,u.get("pet"));fill("cat",d.cats,u.get("cat"));fill("maker",makers,u.get("maker"));
  document.getElementById("q").value=u.get("q")||"";document.getElementById("sort").value=u.get("sort")||"pop";
  d.items.forEach(function(x){x._h=norm([x.n,x.m,x.ct,x.kw,x.jan].join(" "))});
  document.getElementById("free").checked=u.get("free")==="1";
  function run(){var q=norm(document.getElementById("q").value).trim(),pet=f.pet.value,cat=f.cat.value,m=f.maker.value,s=f.sort.value,fo=f.free.checked;
   var t=q?q.split(" "):[],r=d.items.filter(function(x){return(!pet||x.pet.indexOf(pet)>-1)&&(!cat||x.c===cat)&&(!m||x.m===m)&&(!fo||x.fp>0)&&t.every(function(w){return x._h.indexOf(w)>-1})});
   r=r.map(function(x){return fo?Object.assign({},x,{p:x.fp,e:x.fe,sh:"送料無料",pt:x.pt,r:x.r,_free:true}):x});
   r.sort(function(a,b){return s==="price"?a.p-b.p:s==="eff"?a.e-b.e:s==="unit"?((a.u||1e12)-(b.u||1e12)):b.rv-a.rv});
   document.getElementById("results").innerHTML=r.map(function(x){return row(x)}).join("");
   document.getElementById("count").textContent=r.length?r.length+"件の商品":"見つかりませんでした。言葉を短くするか、条件を「すべて」に戻してください。";
   var p=new URLSearchParams();["q","pet","cat","maker","sort"].forEach(function(k){var v=f[k].value;if(v&&!(k==="sort"&&v==="pop"))p.set(k,v)});if(fo)p.set("free","1");
   history.replaceState(null,"",location.pathname+(p.toString()?"?"+p:""));paint();
   clearTimeout(run._t);run._t=setTimeout(function(){ga("search",{search_term:q,pet:pet,category:cat,maker:m,sort:s,results:r.length})},1500)}
  f.free.addEventListener("change",function(){ga("free_only",{on:f.free.checked})});f.addEventListener("input",run);f.addEventListener("change",run);f.addEventListener("submit",function(e){e.preventDefault();run()});run()})}
function renderFav(){var l=document.getElementById("favlist");if(!l)return;data(function(d){var f=load(),by={};d.items.forEach(function(x){by[x.id]=x});
 var ids=Object.keys(f).sort(function(a,b){return f[b].t-f[a].t});
 l.innerHTML=ids.map(function(id){var x=by[id];if(!x)return '<li class="gone"><span>この商品は今回の取得で見つかりませんでした。</span><button type="button" class="fav" data-fav="'+esc(id)+'">外す</button></li>';
  return row(x,x.dl>0?'<span class="diff down"><span class="badge win">他店より '+yen(x.dl)+'円安い</span></span>':'')}).join("");
 document.getElementById("favempty").hidden=ids.length>0;paint()})}
function ics(x,r){var c=cyc(r)||30,d=new Date(r.t+c*864e5),f=function(n){return("0"+n).slice(-2)},ds=d.getFullYear()+f(d.getMonth()+1)+f(d.getDate());
 return "data:text/calendar;charset=utf-8,"+encodeURIComponent("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//petsite//JA\r\nBEGIN:VEVENT\r\nUID:"+x.id+"-"+ds+"@petsite\r\nDTSTART;VALUE=DATE:"+ds+"\r\nSUMMARY:"+x.n.slice(0,40)+" の買い時チェック\r\nDESCRIPTION:"+location.origin+location.pathname.replace(/[^\/]*$/,"")+"item-"+x.id+".html\r\nEND:VEVENT\r\nEND:VCALENDAR")}
function renderBought(){var l=document.getElementById("boughtlist");if(!l)return;data(function(d){var bm=bload(),by={};d.items.forEach(function(x){by[x.id]=x});
 var ids=Object.keys(bm).sort(function(a,b){var xa=by[a],xb=by[b],da=xa?xa.p-bm[a].p:0,db=xb?xb.p-bm[b].p:0;return(da<0?da:1)-(db<0?db:1)||bm[b].t-bm[a].t});
 l.innerHTML=ids.map(function(id){var x=by[id],r=bm[id],h=hist(r),day=new Date(r.t).toLocaleDateString("ja-JP"),c=cyc(r);if(!x)return '<li class="gone"><span>この商品は今回の取得で見つかりませんでした（前回 '+day+'・'+yen(r.p)+'円）。</span><button type="button" class="fav" data-bdel="'+esc(id)+'">記録を消す</button></li>';
  var dd=x.p-r.p,next=c?Math.round((r.t+c*864e5-Date.now())/864e5):null;
  var msg=(dd<0?'<span class="badge win">あなたの前回より '+yen(-dd)+'円安い</span> ':dd>0?'<span class="badge up2">▲'+yen(dd)+'円</span> ':'')+'前回 '+day+'・'+yen(r.p)+'円（'+h.length+'回記録'+(c?'・約'+c+'日ごと':'')+'）'+(next!==null?(next<=0?'。記録の間隔ではそろそろ買う頃です':'。記録の間隔では次の目安まであと'+next+'日'):'');
  if(x.lo&&x.p<x.lo&&x.hd>=5)msg+='<br><span class="tb low">記録した'+x.hd+'日間でいちばん安い</span>';
  return row(x,'<span class="diff">'+msg+'</span>').replace(/<\/li>$/,'<div class="mini-acts"><a href="'+ics(x,r)+'" download="kaidoki.ics" data-ga="ics">次の目安日をカレンダーへ（記録の間隔から計算）</a>'+(dd<0?'<button type="button" class="link" data-kind="routine" data-url="item-'+esc(id)+'.html" data-share="'+esc(x.n.slice(0,40)+" がいつもより "+yen(-dd)+"円安くなっていました（取得時点）")+'">シェアする</button>':'')+'<button type="button" class="link" data-bdel="'+esc(id)+'">記録を消す</button></div></li>')}).join("");
 document.getElementById("boughtempty").hidden=ids.length>0;paint()})}
function renderWish(){var l=document.getElementById("wishlist");if(!l)return;data(function(d){var w=wload(),by={};d.items.forEach(function(x){by[x.id]=x});
 var ids=Object.keys(w).sort(function(a,b){var xa=by[a],xb=by[b],ra=xa?xa.p/w[a].g:9,rb=xb?xb.p/w[b].g:9;return ra-rb});
 l.innerHTML=ids.map(function(id){var x=by[id],r=w[id];if(!x)return '<li class="gone"><span>この商品は今回の取得で見つかりませんでした（目標 '+yen(r.g)+'円）。</span><button type="button" class="fav" data-wdel="'+esc(id)+'">外す</button></li>';
  var reach=x.p<=r.g,pct=Math.max(0,Math.min(100,Math.round(r.g/x.p*100)));
  var msg=reach?'<span class="badge win">目標の '+yen(r.g)+'円に届きました</span> いま '+yen(x.p)+'円':'目標 '+yen(r.g)+'円まであと <b>'+yen(x.p-r.g)+'円</b><span class="bar"><i style="width:'+pct+'%"></i></span>';
  if(x.lo&&x.p<x.lo&&x.hd>=5)msg+='<br><span class="tb low">記録した'+x.hd+'日間でいちばん安い</span>';
  return row(x,'<span class="diff">'+msg+'</span>').replace(/<\/li>$/,'<div class="mini-acts">'+(reach?'<button type="button" class="link" data-kind="wish" data-url="item-'+esc(id)+'.html" data-share="'+esc(x.n.slice(0,40)+" が待っていた "+yen(r.g)+"円以下（いま "+yen(x.p)+"円・取得時点）になりました")+'">シェアする</button>':'')+'<button type="button" class="link" data-wdel="'+esc(id)+'">外す</button></div></li>')}).join("");
 document.getElementById("wishempty").hidden=ids.length>0;paint()})}
function wstats(cb){var bm=bload(),w=wload(),f=load(),sv=sload();if(!Object.keys(bm).length&&!Object.keys(w).length&&!Object.keys(f).length&&!Object.keys(sv).length)return;
 data(function(d){var by={};d.items.forEach(function(x){by[x.id]=x});var a=0,as=0,b=0,c=0,cs=0;
  Object.keys(bm).forEach(function(id){var x=by[id];if(x&&x.p<bm[id].p){a++;as+=bm[id].p-x.p}});
  Object.keys(w).forEach(function(id){var x=by[id];if(x&&x.p<=w[id].g)b++});
  Object.keys(f).forEach(function(id){var x=by[id];if(x&&x.dl>0){c++;cs+=x.dl}});
  var s2=0,ss=0;Object.keys(sv).forEach(function(id){var x=by[id];if(x&&!bm[id]&&!f[id]&&!w[id]&&x.dl>0){s2++;ss+=x.dl}});
  if(!a&&!b&&!c&&!s2)return;cb({a:a,as:as,b:b,c:c,cs:cs,s2:s2,ss:ss,t:as+cs+ss,n:a+c+s2})})}
function welcome(){var el=document.getElementById("welcome");if(!el)return;wstats(function(r){var a=r.a,b=r.b,c=r.c,s2=r.s2,parts=[];if(a)parts.push('いつもの商品 <b>'+a+'件</b>が前回より計 <b class="win">'+yen(r.as)+'円</b>安い');if(b)parts.push('値下がり待ち <b>'+b+'件</b>が目標価格に到達');if(c)parts.push('お気に入り <b>'+c+'件</b>が他店より計 '+yen(r.cs)+'円安い');if(s2)parts.push('前に見た商品 <b>'+s2+'件</b>が他店より計 <b class="win">'+yen(r.ss)+'円</b>安い');
  el.innerHTML='<div class="wl-wrap"><a class="welcome" href="favorites.html" data-ga="welcome_click"><span class="wl-k">おかえりなさい</span><span class="wl-t">'+parts.join('、')+'<small class="wl-n">同じ内容量の他店（中央値）と最安の比較・送料別</small></span><span class="wl-b">マイリストを見る</span></a>'+(r.t>0?'<button type="button" class="link wl-sh" data-wsh="1">この結果をシェア</button>':'')+'</div>';ga("welcome_show",{routine:a,wish:b,fav:c,seen:s2});bindWsh(r)})}
function wshareBox(){var el=document.getElementById("wshare");if(!el)return;wstats(function(r){if(r.t>0){el.innerHTML='<button type="button" class="btn-sub" data-wsh="1">この結果をシェア</button>';bindWsh(r)}})}
function bindWsh(r){document.querySelectorAll("[data-wsh]").forEach(function(b){b.onclick=function(){shareWelcome(r)}})}
function shareWelcome(r){var c=document.createElement("canvas");c.width=1200;c.height=630;var x=c.getContext("2d");if(!x)return;
 x.fillStyle="#fffaf6";x.fillRect(0,0,1200,630);x.fillStyle="#0f6b62";x.fillRect(0,0,1200,14);
 x.fillStyle="#1f2328";x.font="700 44px "+F2;x.fillText("前に見た・お気に入りの商品が他店より",70,110);
 x.fillStyle="#c2255c";x.font="800 150px "+F2;var s=yen(r.t);x.fillText(s,70,300);var w=x.measureText(s).width;x.font="700 64px "+F2;x.fillText("円 安い",90+w,300);
 x.fillStyle="#1f2328";x.font="700 36px "+F2;x.fillText(r.n+"件の商品の合計",70,370);
 x.fillStyle="#6b7280";x.font="400 24px "+F2;x.fillText("同じ内容量の他店（中央値）と最安（税込・送料別）の差の合計",70,440);x.fillText("目安・PR（アフィリエイト）",70,480);
 x.fillStyle="#0f6b62";x.fillRect(0,540,1200,90);x.fillStyle="#fff";x.font="700 36px "+F2;x.fillText("ペット用品 最安ナビ　楽天のショップを比較",70,598);
 var txt="見た商品・お気に入りの "+r.n+"件が、他店（中央値）より合計 "+yen(r.t)+"円 安くなっていました（取得時点・目安）",url=location.origin+location.pathname.replace(/[^\/]*$/,"")+"index.html";ga("share",{kind:"welcome",value:r.t});
 c.toBlob(function(b){var f=new File([b],"petsite-welcome.png",{type:"image/png"});
  if(navigator.canShare&&navigator.canShare({files:[f]}))navigator.share({files:[f],text:txt,url:url}).catch(function(){});
  else{var a=document.createElement("a");a.href=c.toDataURL("image/png");a.download="petsite-welcome.png";a.click();toast("画像を保存しました。X や LINE に貼ってシェアできます")}})}
function ago(){document.querySelectorAll("time[data-fetched]").forEach(function(t){var m=/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})/.exec(t.dataset.fetched);if(!m)return;
 var d=Date.UTC(+m[1],m[2]-1,+m[3],+m[4]-9,+m[5]),h=Math.floor((Date.now()-d)/36e5);if(h<0)return;var s=h<1?"取得から1時間以内":h<24?"取得から約"+h+"時間":"取得から約"+Math.floor(h/24)+"日";
 t.textContent=t.dataset.fetched+"・"+s;if(h>=24&&!document.getElementById("stale")){var hd=document.querySelector("header");if(hd)hd.insertAdjacentHTML("afterend",'<p class="stale" id="stale" role="status">表示の価格は約'+Math.floor(h/24)+'日前（'+esc(t.dataset.fetched)+'）の取得です。最新の価格は楽天市場のページでご確認ください。</p>');ga("stale_banner",{hours:h})}})}

var PETN={};
function shindan(){var root=document.getElementById("shindan");if(!root)return;data(function(d){
 d.pets.forEach(function(p){PETN[p[0]]=p[1]});
 var u=new URLSearchParams(location.search),pet=u.get("pet")||"",sel={},by={};d.items.forEach(function(x){by[x.id]=x});
 (u.get("i")||"").split(",").forEach(function(s){var a=s.split(":");if(a[0]&&by[a[0]])sel[a[0]]=+a[1]||1});
 var pets=document.getElementById("sd-pets"),list=document.getElementById("sd-list"),res=document.getElementById("sd-res");
 pets.innerHTML=d.pets.filter(function(p){return d.items.some(function(x){return x.pet.indexOf(p[0])>-1})}).map(function(p){return '<button type="button" data-pet="'+esc(p[0])+'">'+esc(p[1])+'</button>'}).join("");
 pets.addEventListener("click",function(e){var b=e.target.closest("button");if(!b)return;pet=b.dataset.pet;sel={};draw();ga("shindan_pet",{pet:pet})});
 function draw(){pets.querySelectorAll("button").forEach(function(b){b.setAttribute("aria-pressed",b.dataset.pet===pet?"true":"false")});
  if(!pet){list.innerHTML="";res.hidden=true;return}var g={};
  d.items.forEach(function(x){if(x.pet.indexOf(pet)>-1)(g[x.c]=g[x.c]||{t:x.ct,xs:[]}).xs.push(x)});
  list.innerHTML='<p class="sd-step">いつも買っている商品を選んでください（使っていないカテゴリはそのままで大丈夫です）</p>'+Object.keys(g).map(function(c){var G=g[c];G.xs.sort(function(a,b){return b.rv-a.rv});
   var cur=G.xs.filter(function(x){return sel[x.id]})[0],q=cur?sel[cur.id]:1;
   return '<div class="sd-cat"><label class="sd-nm">'+esc(G.t)+'<select data-c="'+esc(c)+'"><option value="">使っていない</option>'+G.xs.map(function(x){return '<option value="'+esc(x.id)+'"'+(cur&&cur.id===x.id?" selected":"")+'>'+esc(x.n.length>42?x.n.slice(0,41)+"…":x.n)+'（'+yen(x.p)+'円）</option>'}).join("")+'</select></label>'+
   '<label class="sd-q">月に<select data-q="'+esc(c)+'">'+[0.5,1,2,3,4,6].map(function(v){return '<option value="'+v+'"'+(v==q?" selected":"")+'>'+v+'</option>'}).join("")+'</select>個</label></div>'}).join("");
  calc(false)}
 function calc(user){sel={};list.querySelectorAll("select[data-c]").forEach(function(s){if(s.value){var q=list.querySelector('select[data-q="'+s.dataset.c+'"]');sel[s.value]=+q.value}});
  var ids=Object.keys(sel),p=new URLSearchParams();if(pet)p.set("pet",pet);if(ids.length)p.set("i",ids.map(function(id){return id+":"+sel[id]}).join(","));
  history.replaceState(null,"",location.pathname+(p.toString()?"?"+p:""));
  if(!ids.length){res.hidden=true;return}
  var m=0,sv=0,pt=0;ids.forEach(function(id){var x=by[id],q=sel[id];m+=x.p*q;pt+=x.pt*q;if(x.md>x.p)sv+=(x.md-x.p)*q});
  m=Math.round(m);sv=Math.round(sv);pt=Math.round(pt);var R={m:m,y:m*12,sv:sv*12,pt:pt*12,n:ids.length,pet:PETN[pet]||""};
  res.hidden=false;res.innerHTML='<p class="sd-k">うちの'+esc(R.pet)+'の消耗品（'+R.n+'品目）</p><p class="sd-m">月 <b class="cnt" data-to="'+R.m+'">0</b>円</p><p class="sd-y">年間 約'+yen(R.y)+'円</p>'+
   (R.sv>0?'<p class="sd-sv">取得したショップの中央値で買う場合より<br><b class="cnt" data-to="'+R.sv+'">0</b>円/年 安い</p><p class="ptn sd-basis">差額＝同じ内容量の通常品が3店以上ある商品だけ、その価格の中央値と最安の差×月の数×12。送料・クーポンは含みません</p>':'<p class="sd-sv0">選んだ商品は、ショップ間の価格差が小さいか、比べられるショップが3店未満でした</p>')+
   '<p class="sd-pt">ポイント（目安）は年 約'+yen(R.pt)+'ポイント</p><canvas id="sd-card" width="1200" height="630" hidden></canvas>'+
   '<div class="sd-share"><button type="button" class="btn-main" id="sd-sh">結果を画像でシェア</button><a class="btn-sub" id="sd-x" target="_blank" rel="noopener">X にポスト</a><a class="btn-sub" id="sd-line" target="_blank" rel="noopener">LINE で送る</a><a class="btn-sub" id="sd-dl" download="petsite-shindan.png">画像を保存</a></div>'+
   '<p class="ptn">価格は '+esc(d.fetched_at)+' 時点の楽天市場の取得結果です。送料は含みません</p>';
  countUp(res);card(R);
  var url=location.href,txt="うちの"+R.pet+"の消耗品、月"+yen(R.m)+"円だった"+(R.sv>0?"（ショップ選びで年"+yen(R.sv)+"円の差）":"")+"｜あなたの子はいくら？";
  document.getElementById("sd-x").href="https://twitter.com/intent/tweet?text="+encodeURIComponent(txt)+"&url="+encodeURIComponent(url);
  document.getElementById("sd-line").href="https://line.me/R/msg/text/?"+encodeURIComponent(txt+" "+url);
  ["sd-x","sd-line","sd-dl"].forEach(function(id){document.getElementById(id).onclick=function(){ga("shindan_share",{method:id.slice(3),monthly:R.m})}});
  document.getElementById("sd-sh").onclick=function(){var c=document.getElementById("sd-card");ga("shindan_share",{method:"native",monthly:R.m});
   c.toBlob(function(b){var f=new File([b],"petsite-shindan.png",{type:"image/png"});
    if(navigator.canShare&&navigator.canShare({files:[f]}))navigator.share({files:[f],text:txt,url:url}).catch(function(){});
    else{var a=document.getElementById("sd-dl");a.click();toast("画像を保存しました。X や LINE に貼ってシェアできます")}})};
  if(user)ga("shindan_result",{pet:pet,items:R.n,monthly:R.m,yearly_diff:R.sv})}
 list.addEventListener("change",function(){calc(true)});
 if(pet)draw()})}
function countUp(el){el.querySelectorAll(".cnt").forEach(function(c){var to=+c.dataset.to,t0=null,dur=700;
 if(window.matchMedia&&matchMedia("(prefers-reduced-motion: reduce)").matches){c.textContent=yen(to);return}
 function step(t){if(!t0)t0=t;var k=Math.min((t-t0)/dur,1);c.textContent=yen(Math.round(to*(1-Math.pow(1-k,3))));if(k<1)requestAnimationFrame(step)}requestAnimationFrame(step)})}
function card(R){var c=document.getElementById("sd-card");if(!c||!c.getContext)return;var x=c.getContext("2d"),F='"Hiragino Sans","Noto Sans JP","Yu Gothic",sans-serif';
 x.fillStyle="#fffaf6";x.fillRect(0,0,1200,630);x.fillStyle="#0f6b62";x.fillRect(0,0,1200,14);
 x.fillStyle="#1f2328";x.font="700 44px "+F;x.fillText("うちの"+R.pet+"の消耗品、月いくら？",70,110);
 x.fillStyle="#6b7280";x.font="400 30px "+F;x.fillText(R.n+"品目・楽天市場の最安価格で計算",70,160);
 x.fillStyle="#1f2328";x.font="700 64px "+F;x.fillText("月",70,300);x.font="800 150px "+F;var s=yen(R.m);x.fillText(s,150,310);var w=x.measureText(s).width;x.font="700 64px "+F;x.fillText("円",160+w,300);
 if(R.sv>0){x.fillStyle="#c2255c";x.font="800 54px "+F;x.fillText("ショップ選びで 年"+yen(R.sv)+"円 の差",70,410)}
 x.fillStyle="#6b7280";x.font="400 26px "+F;x.fillText("取得したショップの中央値と比べた目安（送料別・"+String(DATA.fetched_at).slice(0,10)+"時点）",70,465);
 x.fillStyle="#0f6b62";x.fillRect(0,540,1200,90);x.fillStyle="#fff";x.font="700 36px "+F;x.fillText("ペット用品 最安ナビ　あなたの子はいくら？ 1分診断",70,598);
 try{document.getElementById("sd-dl").href=c.toDataURL("image/png")}catch(e){}}
var F2='"Hiragino Sans","Noto Sans JP","Yu Gothic",sans-serif';
function wrap(x,t,w,maxL){var out=[],l="";for(var i=0;i<t.length;i++){var c=l+t[i];if(x.measureText(c).width>w){out.push(l);l=t[i];if(out.length===maxL-1){var r=t.slice(i);while(x.measureText(r+"…").width>w&&r.length)r=r.slice(0,-1);out.push(r+(r.length<t.length-i?"…":""));return out}}else l=c}if(l)out.push(l);return out}
function itemCard(b){var c=document.createElement("canvas");c.width=1200;c.height=630;if(!c.getContext)return null;var x=c.getContext("2d"),d=b.dataset;
 x.fillStyle="#fffaf6";x.fillRect(0,0,1200,630);x.fillStyle="#0f6b62";x.fillRect(0,0,1200,14);
 x.fillStyle="#1f2328";x.font="700 44px "+F2;wrap(x,d.cn,1060,2).forEach(function(l,i){x.fillText(l,70,100+i*60)});
 x.fillStyle="#6b7280";x.font="400 30px "+F2;x.fillText(d.ck,70,260);
 if(d.cv){x.fillStyle="#c2255c";x.font="800 110px "+F2;x.fillText(d.cv,70,380)}
 x.fillStyle="#1f2328";x.font="700 40px "+F2;x.fillText("取得した最安 "+yen(+d.cp)+"円（送料別の場合あり）",70,d.cv?460:380);
 x.fillStyle="#6b7280";x.font="400 24px "+F2;x.fillText(d.cd+" 取得時点の目安・PR（アフィリエイト）",70,505);
 x.fillStyle="#0f6b62";x.fillRect(0,540,1200,90);x.fillStyle="#fff";x.font="700 36px "+F2;x.fillText("ペット用品 最安ナビ　楽天のショップを比較",70,598);return c}
function saved(){var bm=bload(),tot=0,n=0;Object.keys(bm).forEach(function(id){hist(bm[id]).forEach(function(r){if(r[2]>0){tot+=r[2];n++}})});return{t:tot,n:n}}
var MS=[1000,3000,5000,10000,30000];
function renderSavings(){var el=document.getElementById("savings");if(!el)return;var S=saved();if(!S.n){el.innerHTML="";return}
 var next=MS.filter(function(m){return m>S.t})[0],got=MS.filter(function(m){return m<=S.t}),pct=next?Math.round(S.t/next*100):100;
 el.innerHTML='<section class="sv-box"><p class="sv-k">このサイトで記録した節約額の合計</p><p class="sv-v"><b class="cnt" data-to="'+S.t+'">0</b>円</p>'+
  '<p class="sv-s">中央値より安く買えた'+S.n+'回の記録から。取得したショップの中央値（同じ内容量・3店以上）と、記録した価格の差を足しています</p>'+
  (got.length?'<p class="sv-ms">'+got.map(function(m){return '<span class="badge win">'+yen(m)+'円 達成</span>'}).join(" ")+'</p>':'')+
  (next?'<p class="sv-nx">次の '+yen(next)+'円まであと '+yen(next-S.t)+'円<span class="bar"><i style="width:'+pct+'%"></i></span></p>':'')+
  '<button type="button" class="btn-main" id="sv-sh">節約額を画像でシェア</button></section>';
 countUp(el);document.getElementById("sv-sh").onclick=function(){shareSaved(S)};
 var lm=+(localStorage.getItem("petsite_ms_v1")||0),top=got.length?got[got.length-1]:0;if(top>lm){try{localStorage.setItem("petsite_ms_v1",top)}catch(e){}ga("savings_milestone",{value:top});toast("節約額が "+yen(top)+"円 を超えました")}}
function shareSaved(S){var c=document.createElement("canvas");c.width=1200;c.height=630;var x=c.getContext("2d");
 x.fillStyle="#fffaf6";x.fillRect(0,0,1200,630);x.fillStyle="#0f6b62";x.fillRect(0,0,1200,14);
 x.fillStyle="#1f2328";x.font="700 44px "+F2;x.fillText("ペット用品、ショップ選びで",70,110);
 x.fillStyle="#c2255c";x.font="800 150px "+F2;var s=yen(S.t);x.fillText(s,70,300);var w=x.measureText(s).width;x.font="700 64px "+F2;x.fillText("円 節約",90+w,300);
 x.fillStyle="#6b7280";x.font="400 28px "+F2;x.fillText(S.n+"回の購入記録・取得したショップの中央値との差の合計（目安）",70,380);
 x.fillStyle="#0f6b62";x.fillRect(0,540,1200,90);x.fillStyle="#fff";x.font="700 36px "+F2;x.fillText("ペット用品 最安ナビ　楽天のショップを比較",70,598);
 var txt="ペット用品のショップ選びで、これまでに "+yen(S.t)+"円 節約できました（取得価格の中央値との差・目安）",url=location.origin+location.pathname.replace(/[^\/]*$/,"")+"index.html";ga("share",{kind:"savings",value:S.t});
 c.toBlob(function(b){var f=new File([b],"petsite-savings.png",{type:"image/png"});
  if(navigator.canShare&&navigator.canShare({files:[f]}))navigator.share({files:[f],text:txt,url:url}).catch(function(){});
  else{var a=document.createElement("a");a.href=c.toDataURL("image/png");a.download="petsite-savings.png";a.click();toast("画像を保存しました。X や LINE に貼ってシェアできます")}})}
function happy(k,t){var el=document.getElementById("bought-msg");if(!el)return;var b=document.querySelector(".actions [data-card]");if(!b)return;
 el.insertAdjacentHTML("afterend",'<p class="happy" id="happy"><span>'+esc(t)+'</span><button type="button" class="link" data-share="'+esc(t+"。"+b.dataset.share)+'" data-card="1" data-kind="happy" data-cn="'+esc(b.dataset.cn)+'" data-cp="'+b.dataset.cp+'" data-ck="'+esc(b.dataset.ck)+'" data-cv="'+esc(b.dataset.cv)+'" data-cd="'+esc(b.dataset.cd)+'">シェアする</button></p>');ga("happy_show",{kind:k})}
function pushItems(){var o={};[sload(),load(),wload(),bload()].forEach(function(m){Object.keys(m).forEach(function(id){o[id]=1})});return o}  // 商品IDだけ送る（価格は送らない・保存しない）
function pushSync(sub,method){return fetch("/api/subscribe",{method:method||"POST",headers:{"content-type":"application/json"},body:JSON.stringify({endpoint:sub.endpoint,items:pushItems()})}).then(function(r){return r.ok})}
function u8(b){var p="=".repeat((4-b.length%4)%4),r=atob((b+p).replace(/-/g,"+").replace(/_/g,"/")),a=new Uint8Array(r.length);for(var i=0;i<r.length;i++)a[i]=r.charCodeAt(i);return a}
function pushUI(){var el=document.getElementById("pushbox");if(!el||!VAPID)return;var ios=/iPhone|iPad/.test(navigator.userAgent),sa=window.navigator.standalone||matchMedia("(display-mode: standalone)").matches;
 if(!("serviceWorker" in navigator)||!("PushManager" in window)||!window.Notification){if(ios&&!sa)el.innerHTML='<p class="push-h">iPhone は、Safari の共有ボタンから「ホーム画面に追加」して開くと、値下がりの通知を受け取れます</p>';return}
 navigator.serviceWorker.register("/sw.js").then(function(reg){return reg.pushManager.getSubscription().then(function(sub){
  var D='<details class="push-d"><summary>通知のしくみ・保存する情報</summary><p>条件：他店（中央値）より、取得時点の最安（税込・送料別）が安いとき。価格は保存しません。</p><p>保存するもの：通知の宛先（ブラウザが発行するURL）と、見た商品・お気に入り等の商品IDだけです。氏名・メールアドレス・楽天のアカウントは受け取りません。</p><p>使い道：価格の更新のあと、他店より安い商品があるときに、1日1回まで通知を送るためだけに使います。</p><p>保存場所：Cloudflare（本サイトの配信事業者）のデータ保存領域。</p><p>消し方：このボタンの「通知をやめる」で、宛先と商品の記録をすぐに消します。ブラウザの設定で通知を切った場合も、次の送信時に届かなくなった宛先として自動で消します。</p></details>';
  function draw(on){el.innerHTML=(on?'<p class="push-on">🔔 値下がり通知オン<button type="button" class="link" id="push-off">やめる</button></p>':
   '<button type="button" class="btn-main push-btn" id="push-on">値下がりしたら通知する</button><p class="push-n">見た商品・お気に入りが他店より安くなったら・1日1回まで・いつでも解除</p>')+D;
   var a=document.getElementById("push-on"),b=document.getElementById("push-off");
   if(a)a.onclick=function(){ga("push_ask",{});Notification.requestPermission().then(function(p){if(p!=="granted"){toast("通知が許可されませんでした。ブラウザの設定から変更できます");ga("push_denied",{});return}
    reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:u8(VAPID)}).then(function(s){return pushSync(s).then(function(ok){if(ok){sub=s;try{localStorage.setItem("petsite_push_t",Date.now())}catch(e){}draw(true);ga("push_on",{items:Object.keys(pushItems()).length});toast("値下がりしたらお知らせします")}else toast("登録できませんでした。時間をおいてお試しください")})}).catch(function(){toast("登録できませんでした")})})};
   if(b)b.onclick=function(){pushSync(sub,"DELETE").then(function(){return sub.unsubscribe()}).then(function(){sub=null;draw(false);ga("push_off",{});toast("通知をやめました（記録も消しました）")})}}
  draw(!!sub);
  if(sub){var t=+(localStorage.getItem("petsite_push_t")||0);if(Date.now()-t>6*36e5){pushSync(sub).then(function(){try{localStorage.setItem("petsite_push_t",Date.now())}catch(e){}})}}})}).catch(function(){})}
function init(){seen();renderSeen();pushUI();var hr=document.querySelector(".hero");if(hr)countUp(hr);var wu=document.querySelector(".wk-share");if(wu)ga("weekly_view",{});ago();keepOrig();renderBought();renderWish();renderSavings();welcome();wshareBox();shindan();var h=document.getElementById("hq");if(h){var q=new URLSearchParams(location.search).get("q");if(q&&!document.getElementById("sf"))h.value=q}
 paint();search();renderFav()}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",init);else init()})();
"""


CSS = """:root{--txt:#1f2328;--sub:#6b7280;--line:#e6e8eb;--bg:#f6f7f5;--price:#b4530a;--brand:#0f6b62;--brand-d:#0b5048;--brand-bg:#e9f3f0;--warn-bg:#fff7e6;--win:#c2255c;--win-bg:#fff0f5;--warn:#8a5a00;--link:#0b5db3;--r:8px}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Hiragino Kaku Gothic ProN","Noto Sans JP",Meiryo,sans-serif;color:var(--txt);background:var(--bg);line-height:1.6;font-size:15px;-webkit-text-size-adjust:100%;-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
a:focus-visible,button:focus-visible,input:focus-visible,summary:focus-visible{outline:2px solid var(--link);outline-offset:2px}
.ic{display:inline-block;vertical-align:-0.2em;flex-shrink:0}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
/* 帯・ヘッダー */
.promo{margin:0;padding:6px 16px;background:#2b2f33;color:#fff;font-size:12px;line-height:1.5;text-align:center}
.sample{margin:0;padding:6px 12px;background:#fff4d6;color:#7a5200;font-size:12px;text-align:center}
.hd{background:#fff;border-bottom:1px solid var(--line)}
.hd-in{max-width:960px;margin:0 auto;padding:12px 16px 8px;display:flex;align-items:center;gap:12px}
.logo{font-weight:700;font-size:18px;letter-spacing:.01em;display:flex;align-items:center;gap:8px;color:var(--txt)}
.mk{width:32px;height:32px;border-radius:10px;background:var(--brand);color:#fff;display:inline-grid;place-items:center}
.hd-note{font-size:11px;color:var(--sub);line-height:1.4;margin-left:auto;text-align:right}
.hd-fav{font-size:13px;color:var(--txt);white-space:nowrap;display:inline-flex;align-items:center;gap:4px;border:1px solid var(--line);border-radius:var(--r);padding:0 10px;height:36px;background:#fff}
.hd-search{max-width:960px;margin:0 auto;padding:0 16px 12px;display:flex}
.hd-search input{flex:1;min-width:0;font:inherit;font-size:16px;padding:0 14px;height:44px;border:1px solid #cfd4d9;border-right:0;border-radius:var(--r) 0 0 var(--r);background:#fff}
.hd-search button{font:inherit;font-size:14px;font-weight:700;background:var(--brand);color:#fff;border:0;border-radius:0 var(--r) var(--r) 0;padding:0 16px;height:44px;cursor:pointer;display:inline-flex;align-items:center;gap:6px}
.hd-search button:hover{background:var(--brand-d)}
main{max-width:960px;margin:0 auto;padding:0 0 24px}
.bc{font-size:12px;color:var(--sub);padding:10px 16px 0}.bc a{color:var(--link)}
.lead{font-size:13px;color:var(--sub);margin:12px 16px;line-height:1.7}
.ttl{font-size:20px;line-height:1.4;margin:8px 16px 0;font-weight:700}
.h2{font-size:15px;font-weight:700;margin:0;padding:14px 16px 8px;display:flex;align-items:center;gap:8px;color:var(--txt)}.h2 .ic{color:var(--brand)}
/* トップ */
.tabs{list-style:none;margin:0;padding:4px 16px 12px;display:flex;gap:8px;overflow-x:auto;scrollbar-width:none}.tabs::-webkit-scrollbar{display:none}
.tabs a{display:inline-flex;align-items:center;gap:6px;white-space:nowrap;background:#fff;border:1px solid var(--line);border-radius:999px;padding:0 14px 0 10px;height:40px;font-size:14px;font-weight:700;color:var(--txt)}.tabs a .ic{color:var(--brand)}
.pet-sec{margin-top:4px}
.chips{list-style:none;margin:0;padding:0 16px 12px;display:flex;flex-wrap:wrap;gap:8px}
.chips a{display:inline-block;border:1px solid var(--line);background:#fff;border-radius:999px;padding:6px 12px;font-size:13px;color:var(--txt)}
/* 一覧（カード） */
.cats,.list,.shops{list-style:none;margin:0;padding:0 16px 8px;display:grid;gap:8px}
.cats li,.list li,.shops li{background:#fff;border-radius:12px;border:1px solid var(--line)}
.list li{position:relative}
.cat,.row{display:flex;gap:12px;padding:12px;align-items:flex-start}
.cat{align-items:center}
.pic img,.noimg{display:block;width:88px;height:88px;object-fit:contain;background:#fff;border-radius:6px}
.cat .pic img,.cat .noimg{width:56px;height:56px}
.noimg{background:#f0f1f2}
.info{display:flex;flex-direction:column;gap:2px;min-width:0;flex:1}
.nm{font-size:14px;line-height:1.5;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.cat .nm{font-weight:700;font-size:15px}
.meta{font-size:12px;color:var(--sub);line-height:1.5}
.rv{font-size:12px;color:var(--sub);display:flex;align-items:center;gap:4px;margin-top:2px;white-space:nowrap}
.st{position:relative;display:inline-block;height:13px;line-height:0;color:#e3e3e3}.st .ic{display:inline-block;vertical-align:top}
.st-bg{display:block;white-space:nowrap}.st-fg{position:absolute;left:0;top:0;overflow:hidden;white-space:nowrap;color:#f0a30a}
.pr{color:var(--price);font-size:13px;margin:6px 0 0;line-height:1.3;display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 6px}.pr b{font-size:22px;font-weight:700;letter-spacing:-.01em}
.pr.big{margin:4px 0 0}.pr.big b{font-size:30px}
.scope{flex-basis:100%;font-size:11px;color:var(--sub);font-weight:400;line-height:1.3}
.ship{display:inline-flex;align-items:center;font-size:11px;color:var(--sub);border:1px solid var(--line);border-radius:4px;padding:1px 6px;font-weight:400;line-height:1.4;background:#fff}
.ship.free{color:var(--brand);border-color:#9fc9c0;background:var(--brand-bg)}.ship.paid{color:var(--warn);border-color:#ecd7a8;background:var(--warn-bg)}
.ship-rule{margin:0;padding:6px 16px;font-size:12px;color:var(--sub);line-height:1.5}.ship-rule b{color:var(--txt);font-weight:600}
.hi-pt{display:inline-block;align-self:flex-start;margin:4px 0 0;font-size:11px;font-weight:600;color:#fff;background:var(--brand);border-radius:4px;padding:1px 6px;line-height:1.5}
.drop-sec{margin:16px 0;padding:0 16px}.h2s{font-size:12px;font-weight:400;color:var(--sub);margin-left:8px}
.drops{list-style:none;margin:8px 0 6px;padding:0;background:#fff;border:1px solid var(--line);border-radius:var(--r);overflow:hidden}
.drops li+li{border-top:1px solid var(--line)}
.dr{display:flex;align-items:center;gap:10px;padding:10px 12px;color:var(--txt);text-decoration:none;min-height:48px}
.dr:active{background:var(--win-bg)}
.rk{flex:0 0 24px;height:24px;border-radius:12px;background:var(--win);color:#fff;font-size:13px;font-weight:700;display:grid;place-items:center}
.dr .pic img,.dr .noimg{width:56px;height:56px;border-radius:6px;object-fit:contain;background:#fff;display:block}
.dr .info{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}.dr .nm{font-size:13px;line-height:1.4;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.dr-pr{font-size:13px;color:var(--sub)}.dr-pr small{font-size:11px}.drop-basis{margin:2px 0 0}.sd-basis{margin:4px 0 0}.dr-pr s{color:var(--sub)}.dr-pr b{font-size:16px;color:var(--price)}
.dr-d{flex:0 0 auto;text-align:right;color:var(--win);font-size:20px;font-weight:800;line-height:1.1}.dr-d small{display:block;font-size:12px;font-weight:700}
.sd-cta{display:grid;grid-template-columns:1fr auto;gap:2px 12px;align-items:center;margin:12px 16px;padding:14px 16px;border-radius:12px;background:var(--win-bg);border:1px solid #f6c3d4;color:var(--txt);text-decoration:none}
.sd-cta-t{font-size:16px;font-weight:800}.sd-cta-s{font-size:12px;color:var(--sub);grid-column:1}.sd-cta-b{grid-column:2;grid-row:1/3;background:var(--win);color:#fff;font-weight:700;border-radius:999px;padding:10px 16px;font-size:14px}
#shindan{padding:0 16px}.sd-pets{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 12px}
.sd-pets button{min-height:44px;padding:0 16px;border:1px solid var(--line);border-radius:999px;background:#fff;font-size:15px;color:var(--txt)}
.sd-pets button[aria-pressed="true"]{background:var(--brand);border-color:var(--brand);color:#fff;font-weight:700}
.sd-step{font-size:13px;color:var(--sub);margin:4px 0 8px}
.sd-cat{display:flex;gap:8px;align-items:flex-end;padding:10px 0;border-top:1px solid var(--line)}
.sd-nm{flex:1;min-width:0;display:flex;flex-direction:column;gap:4px;font-size:13px;font-weight:700}
.sd-cat select{min-height:44px;border:1px solid var(--line);border-radius:var(--r);background:#fff;font-size:14px;padding:0 8px;max-width:100%}
.sd-q{flex:0 0 auto;font-size:13px;display:flex;align-items:center;gap:4px}
.sd-res{margin:16px 0;padding:20px 16px;border-radius:12px;background:#fff;border:2px solid var(--win);text-align:center}
.sd-k{margin:0;font-size:14px;color:var(--sub)}.sd-m{margin:4px 0 0;font-size:20px;font-weight:700}.sd-m b{font-size:44px;color:var(--txt);font-variant-numeric:tabular-nums}
.sd-y{margin:0;font-size:14px;color:var(--sub)}.sd-sv{margin:12px 0 0;font-size:14px;color:var(--win);font-weight:700;line-height:1.5}.sd-sv b{font-size:30px;font-variant-numeric:tabular-nums}
.sd-sv0{margin:12px 0 0;font-size:13px;color:var(--sub)}.sd-pt{margin:8px 0 0;font-size:13px;color:var(--brand)}
.sd-share{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin:16px 0 8px}
.btn-main{min-height:48px;padding:0 20px;border:0;border-radius:999px;background:var(--win);color:#fff;font-size:15px;font-weight:700}
.btn-sub{min-height:44px;display:inline-flex;align-items:center;padding:0 14px;border:1px solid var(--line);border-radius:999px;background:#fff;color:var(--txt);font-size:14px;text-decoration:none}
.tb.new{background:var(--win-bg);color:var(--win);border:1px solid #f3b6c9;font-weight:700}
.more-link{display:block;margin:8px 16px 0;padding:12px;text-align:center;border-radius:var(--r);border:1px solid #f3b6c9;color:var(--win);font-weight:700;text-decoration:none;background:#fff}
.wk-share{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.vp{margin:12px 16px 4px}.vp-t{margin:0;font-size:20px;font-weight:800;line-height:1.4}.vp-t b{color:var(--win)}.vp-s{margin:4px 0 0;font-size:13px;color:var(--sub)}
.pr-note{margin:4px 0 0;font-size:12px;color:var(--sub)}
.wl-wrap{position:relative}.wl-sh{display:block;margin:4px 16px 0 auto;padding:4px 0}
.wl-n{display:block;margin-top:4px;font-size:11px;font-weight:400;color:var(--sub)}
.push-d{margin:0 0 12px;font-size:12px;color:var(--sub)}.push-d summary{cursor:pointer;color:var(--link)}.push-d p{margin:6px 0}
.push-btn{width:100%;margin:8px 0 4px}.push-n,.push-h{margin:0 0 8px;font-size:12px;color:var(--sub)}.push-on{margin:8px 0;font-size:13px;color:var(--brand);font-weight:700}
.kome{color:var(--sub);text-decoration:none;font-size:11px;margin-left:2px;vertical-align:super}
.fn{font-size:11px;color:var(--sub)}.fresh1{margin:0 16px 8px;font-size:12px;color:var(--sub)}
.ml:has(ul:empty){display:none}.ml .h2{margin-top:16px}
.ml-empty{margin:16px 0;padding:20px 16px;border-radius:12px;background:#fff;border:1px solid var(--line);text-align:center}
.ml-t{margin:0 0 12px;font-weight:800;font-size:18px}.ml-steps{list-style:none;margin:0 0 16px;padding:0;display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.ml-steps li{display:flex;flex-direction:column;align-items:center;gap:4px;font-size:12px}.ml-steps b{font-size:13px}.ml-steps span{color:var(--sub);font-size:11px}
.ml-steps svg,.ml-n{width:40px;height:40px;padding:9px;border-radius:50%;background:var(--win-bg);color:var(--win);box-sizing:border-box}.ml-n{display:flex;align-items:center;justify-content:center;font-weight:800}
details.about{margin:24px 0 0;font-size:12px;color:var(--sub)}details.about summary{cursor:pointer}
.today{display:grid;grid-template-columns:1fr auto;gap:2px 12px;align-items:center;margin:12px 16px 0;padding:12px 16px;border-radius:12px;background:#fff;border:2px solid var(--win);color:var(--txt);text-decoration:none}
.td-k{font-size:12px;color:var(--sub)}.td-n{font-size:16px;font-weight:700}.td-n b{color:var(--win);font-size:26px;font-variant-numeric:tabular-nums}.td-b{grid-column:2;grid-row:1/3;color:var(--win);font-weight:700;font-size:14px}
.sv-box{margin:12px 0 20px;padding:18px 16px;border-radius:12px;background:var(--win-bg);border:1px solid #f3b6c9;text-align:center}
.sv-k{margin:0;font-size:14px;font-weight:700}.sv-v{margin:4px 0;font-size:20px;font-weight:700;color:var(--win)}.sv-v b{font-size:44px;font-variant-numeric:tabular-nums}
.sv-s{margin:0 0 8px;font-size:12px;color:var(--sub)}.sv-ms{margin:8px 0}.sv-nx{margin:8px 0 12px;font-size:13px}
.happy{display:flex;gap:10px;align-items:center;justify-content:space-between;margin:8px 0;padding:10px 12px;border-radius:var(--r);background:var(--win-bg);color:var(--win);font-weight:700;font-size:14px}
.badge.win,.win{color:var(--win)}.badge.win{background:var(--win-bg);border:1px solid #f3b6c9;font-weight:700}
.mini-acts{display:flex;gap:16px;align-items:center;font-size:13px;padding:0 12px 10px 120px}.mini-acts a{color:var(--brand)}.link{background:none;border:0;padding:0;color:var(--sub);text-decoration:underline;font-size:12px}
.bar{display:block;height:6px;background:#eee;border-radius:3px;margin-top:4px;overflow:hidden}.bar i{display:block;height:100%;background:var(--win)}
.welcome{display:grid;grid-template-columns:1fr auto;gap:2px 12px;align-items:center;margin:12px 16px 0;padding:12px 16px;border-radius:12px;background:#fff;border:2px solid var(--win);color:var(--txt);text-decoration:none}
.wl-k{font-size:12px;color:var(--win);font-weight:700}.wl-t{grid-column:1;font-size:14px;line-height:1.5}.wl-b{grid-column:2;grid-row:1/3;font-size:13px;font-weight:700;color:var(--win)}
.hero{margin:12px 16px;padding:14px;border-radius:12px;background:var(--win-bg);border:1px solid #f3b6c9}.hero-k{margin:0 0 8px;font-size:13px;font-weight:800;color:var(--win)}
.hero-a{display:flex;gap:12px;color:var(--txt);text-decoration:none}.hero-a .pic img,.hero-a .noimg{width:96px;height:96px;border-radius:8px;object-fit:contain;background:#fff;display:block}
.hero-a .info{display:flex;flex-direction:column;gap:2px;min-width:0}.hero-rv{font-size:12px;color:var(--sub)}.hero-d{font-size:26px;font-weight:800;color:var(--win);line-height:1.1}.hero-d small{display:block;font-size:11px;font-weight:600}
.hero-pr b{font-size:18px;color:var(--price)}
.wish-f[hidden]{display:none}.wish-f{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:8px 0;font-size:14px}.wish-f input{width:110px;min-height:40px;border:1px solid var(--line);border-radius:var(--r);padding:0 8px;font-size:16px}
.wish-f button{min-height:40px;padding:0 16px;border:0;border-radius:999px;background:var(--win);color:#fff;font-weight:700}
.facts{font-size:13px;color:var(--sub);line-height:1.6;margin:4px 0 8px}
.save b{color:var(--win);font-size:20px}
.stale{margin:0;padding:10px 16px;font-size:13px;line-height:1.6;color:var(--warn);background:var(--warn-bg);border-bottom:1px solid #e7c27a;font-weight:600}.ship .ic{margin-right:3px}
.pt{font-size:12px;color:var(--brand);margin:4px 0 0;line-height:1.5}.pt b{color:var(--txt)}
.ptn{display:block;font-size:11px;color:var(--sub);font-weight:400}
.shop-l{margin:2px 0 0;font-size:13px;color:var(--txt)}
.up{font-size:12px;color:var(--sub);margin:4px 0 0}
.tb{display:inline-block;font-size:11px;font-weight:700;border-radius:4px;padding:1px 6px;line-height:1.4}.tb.low{background:#fde9d6;color:#9a3f00}.tb.down{background:var(--brand-bg);color:var(--brand)}.tb.high{background:#eef0f2;color:#4a5866}
.save-s{display:inline-block;margin-top:4px;font-size:11px;color:var(--price);background:#fff3e8;border-radius:4px;padding:1px 6px;align-self:flex-start}
.fav{font:inherit;font-size:13px;background:#fff;border:1px solid #cfd4d9;border-radius:var(--r);height:40px;padding:0 12px;cursor:pointer;color:var(--txt);display:inline-flex;align-items:center;gap:6px}
.fav[aria-pressed="true"]{color:#b8860b;border-color:#e3c35a;background:#fffaf0}
.fav.mini{position:absolute;right:8px;top:8px;width:40px;height:40px;padding:0;justify-content:center;color:#b8860b;z-index:1}
.list .row{padding-right:56px}
/* 並べ替え・絞り込み */
.sort{display:flex;flex-wrap:nowrap;gap:8px;align-items:center;padding:10px 16px;background:#fff;border-top:1px solid var(--line);border-bottom:1px solid var(--line);font-size:12px;color:var(--sub);position:sticky;top:0;z-index:5;overflow-x:auto;scrollbar-width:none}.sort::-webkit-scrollbar{display:none}
.sort>span{white-space:nowrap}
.sort button{font:inherit;font-size:13px;white-space:nowrap;background:#fff;border:1px solid #cfd4d9;border-radius:999px;height:36px;padding:0 14px;color:var(--txt);cursor:pointer}
.sort button[aria-pressed="true"]{background:var(--txt);color:#fff;border-color:var(--txt)}
.free-only{display:inline-flex;align-items:center;gap:6px;white-space:nowrap;font-size:13px;color:var(--txt);border:1px solid #cfd4d9;border-radius:999px;padding:0 12px;height:36px;background:#fff}.free-only input{margin:0;accent-color:var(--brand)}
.free-note{margin:0;padding:6px 16px;font-size:12px;color:var(--warn);background:var(--warn-bg)}
.alt-p{font-size:11px;color:var(--sub);margin-left:4px}
.fresh{list-style:none;margin:8px 16px 0;padding:8px 12px;background:#fff;border:1px solid var(--line);border-radius:var(--r);font-size:12px;color:var(--sub);display:grid;gap:2px}
/* 商品ページ */
.item{background:#fff;margin:12px 16px 0;border:1px solid var(--line);border-radius:12px;padding:16px;display:grid;grid-template-columns:200px 1fr;gap:16px}
.item .ttl{margin:0;font-size:18px}
.item-pic img,.noimg.big{width:100%;max-width:200px;height:auto;aspect-ratio:1;object-fit:contain;display:block;margin:0 auto;border-radius:8px}
.mk2{margin:4px 0 0;font-size:12px}.mk2 a{color:var(--link)}
.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.actions .fav,.actions .bought,.actions .share{margin:0}
.bought,.share{font:inherit;font-size:13px;background:#fff;border:1px solid #cfd4d9;border-radius:var(--r);height:40px;padding:0 12px;cursor:pointer;color:var(--txt);display:inline-flex;align-items:center;gap:6px}
.bought[aria-pressed="true"]{background:var(--brand-bg);border-color:var(--brand);color:var(--brand)}
.bought-msg{font-size:12px;margin:8px 0 0;color:var(--txt)}
.best{margin-top:12px;border:1px solid var(--line);border-radius:12px;padding:12px 14px;background:#fafbfa}
.best-l{margin:0;font-size:12px;color:var(--sub)}
.shipnote{margin:10px 0 0;font-size:12px;color:var(--warn);background:var(--warn-bg);border-radius:var(--r);padding:8px 10px;line-height:1.6}
.save{margin:10px 0 0;font-size:13px;color:var(--txt);line-height:1.6}.save b{color:var(--price)}
.trend{margin:10px 0 0;font-size:12px;color:var(--txt);display:flex;gap:10px;align-items:center}.trend p{margin:0;line-height:1.6}.trend .spark{color:var(--brand);flex-shrink:0}.trend b.low{color:var(--price)}.trend b.high{color:#4a5866}.trend.wait{color:var(--sub)}.trend small{color:var(--sub)}
.btn{display:inline-block;background:var(--brand);color:#fff;font-weight:700;border-radius:var(--r);padding:0 14px;height:40px;line-height:40px;font-size:13px;white-space:nowrap;text-align:center}
.btn:hover{background:var(--brand-d)}
.btn.big{display:block;margin-top:12px;height:48px;line-height:48px;font-size:16px}
.alt{font-size:12px;margin:8px 0 0;color:var(--txt)}
.shops li{display:flex;gap:10px;align-items:center;justify-content:space-between;padding:12px}
.shop{display:flex;flex-direction:column;gap:2px;min-width:0}
.sn{font-size:13px;color:var(--link);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.outlet{font-size:11px;color:var(--warn);background:var(--warn-bg);border-radius:4px;padding:0 6px;align-self:flex-start}
.sub-link{display:block;margin:0 16px 8px;padding:12px;background:#fff;border:1px solid var(--line);border-radius:var(--r);color:var(--link);font-size:13px;text-align:center}
/* 検索・お気に入り */
.filters{background:#fff;padding:12px 16px;border-top:1px solid var(--line);border-bottom:1px solid var(--line);margin-top:8px;position:sticky;top:0;z-index:5}
.filters input{width:100%;font:inherit;font-size:16px;height:44px;padding:0 12px;border:1px solid #cfd4d9;border-radius:var(--r)}
.fl-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin-top:8px;align-items:end}
.fl-row label{display:flex;flex-direction:column;font-size:11px;color:var(--sub);gap:2px}
.fl-row select{font:inherit;font-size:14px;height:40px;padding:0 8px;border:1px solid #cfd4d9;border-radius:var(--r);background:#fff;color:var(--txt)}
.fl-row .free-only{height:40px;border-radius:var(--r)}
.count{font-size:13px;margin:10px 16px}
.diff{font-size:12px;color:var(--sub);margin-top:4px;line-height:1.6}.diff.down{color:var(--price);font-weight:700}
.badge{display:inline-block;font-weight:700;border-radius:4px;padding:0 6px;font-size:12px}.badge.down{background:#fde9d6;color:#9a3f00}.badge.up2{background:#eef0f2;color:#4a5866}
.gone{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:12px;font-size:13px;color:var(--sub)}.gone .fav{position:static;margin:0}
.empty{text-align:center;color:var(--sub);font-size:13px;margin:16px}
.note{font-size:11px;color:var(--sub);padding:12px 16px;line-height:1.6}.note p{margin:2px 0}
#toast{position:fixed;left:50%;bottom:24px;transform:translate(-50%,20px);background:#2b2f33;color:#fff;padding:10px 16px;border-radius:var(--r);font-size:13px;opacity:0;transition:.25s;z-index:20;max-width:90vw;pointer-events:none}#toast.on{opacity:1;transform:translate(-50%,0)}
footer{max-width:960px;margin:0 auto;padding:16px;font-size:11px;color:var(--sub);line-height:1.7}footer a{color:var(--link)}
@media (max-width:600px){.item{grid-template-columns:1fr;margin:8px 8px 0}.item-pic img,.noimg.big{max-width:180px}.hd-in{flex-wrap:wrap;gap:8px}.hd-note{order:3;width:100%;margin:0;text-align:left}.hd-note br{display:none}
.cats,.list,.shops{padding:0 8px 8px}.fresh{margin:8px 8px 0}.sub-link{margin:0 8px 8px}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

if __name__ == "__main__":
    sys.exit(main())
