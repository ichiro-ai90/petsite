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
import hashlib
import html
import json
import math
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
    "bed": '<path d="M3 18V7"/><path d="M3 14h18v4"/><path d="M21 14v-2a3 3 0 0 0-3-3h-7v5"/><circle cx="7" cy="11" r="1.6"/>',
}


# アイコン担当（ChatGPT）が描いた版があれば上書き（design/design_icons.py が検査済みのものだけを書き出す）
_ICON_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icons.json")
if os.path.exists(_ICON_FILE):
    ICON.update({k: v for k, v in json.load(open(_ICON_FILE, encoding="utf-8")).items() if k in ICON and v})


def favicon_svg():
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#ea580c"/>'
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
    s = re.sub(r"\s[\u3041-\u309f\u30a1-\u30ff]$", "", s)  # 販促の括弧を外した後に残る1文字のかな（例「…ワイド り」）は落とす（監査#70）
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


_TVFONT = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
           '<link href="https://fonts.googleapis.com/css2?family=Klee+One:wght@600&display=swap" rel="stylesheet">')  # 旅のしおり風の手書き見出し（無料・OFL。11/24 Taku）
def page(title, body, desc, sample, crumbs=(), canonical="", og_image="", noindex=False, brand=""):
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
<link rel="stylesheet" href="style.css">{_TVFONT if brand == "travel" else ""}{ga_tag()}<script src="app.js" defer></script></head><body>
<p class="promo">本ページはプロモーション（アフィリエイト広告）が含まれています。リンク先で購入されると、当サイトに紹介料が支払われます。</p>
{band}{header_html(brand)}
<main>{bc}{body}</main>
<footer><p>このサイトで掲載されている情報は、ペット用品 最安ナビの作成者により運営されています。価格、販売可能情報は、変更される場合があります。購入時に楽天市場店舗（www.rakuten.co.jp）に表示されている価格が、その商品の販売に適用されます。</p>
<p>運営: 合同会社にこのて（AI〈一郎〉が自動で作成）・楽天グループ株式会社とは関係ありません｜<a href="about.html">注記・このサイトについて</a></p>
<p>{CREDIT}</p>{yahoo_foot()}</footer>
</body></html>"""



try:  # 行き先ごとの「ペットとおでかけガイド」（travel_guides.json）
    _TG = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "travel_guides.json"), encoding="utf-8"))
except (OSError, ValueError):
    _TG = {"guides": [], "manners": []}
_GUIDES, _MANNERS = _TG["guides"], _TG["manners"]
_GBP = {g["pref"]: g for g in _GUIDES}  # 都道府県 → ガイド
def _licu(name):  # ライセンス本文のURL（なければ空）
    m = re.match(r"CC (BY(?:-SA)?) (\d\.\d)(?: (jp))?$", name)
    if m:
        return f"https://creativecommons.org/licenses/{m.group(1).lower()}/{m.group(2)}/{m.group(3) + '/' if m.group(3) else ''}deed.ja"
    return "https://creativecommons.org/publicdomain/zero/1.0/deed.ja" if name == "CC0" else ""
def _lic(name):  # ライセンス名に、ライセンス本文へのリンクを付ける（CCの表示義務）
    m = re.match(r"CC (BY(?:-SA)?) (\d\.\d)(?: (jp))?$", name)
    if m:
        u = f"https://creativecommons.org/licenses/{m.group(1).lower()}/{m.group(2)}/{m.group(3) + '/' if m.group(3) else ''}deed.ja"
    elif name == "CC0":
        u = "https://creativecommons.org/publicdomain/zero/1.0/deed.ja"
    else:
        return E(name)
    return f'<a href="{u}" target="_blank" rel="noopener nofollow license">{E(name)}</a>'


def _gcov(g):  # 表紙にするスポット：ガイド名の（ ）内の地名を順に見て、その地名の写真を優先（11/24 Taku「群馬（草津・前橋）なのに草津の写真でない」）
    ph = [r for r in g["spots"] if r.get("photo")]
    if g.get("cover"):
        c = next((r for r in ph if r["name"] == g["cover"]), None)
        if c: return c
    m = re.search(r"（(.+)）", g["name"])
    for kw in (m.group(1).split("・") if m else []):
        for k in (kw, kw[:3]):
            c = next((r for r in ph if k in r["name"] or k in (r.get("where") or "")), None)
            if c: return c
    return ph[0] if ph else None
def _gcover(g):  # ガイドの表紙写真：スポットの実写（Wikimedia Commons）があればそれ、なければテーマ写真
    r = _gcov(g)
    return "travel/g/" + r["photo"]["file"] if r else f'travel/{g["photo"]}.jpg'
def _gplace(g):  # 表紙写真がどこか（カードに出す）
    r = _gcov(g)
    return f'{r["name"]}（{r["where"]}）' if r else ""
def _gcred(g):  # 表紙写真のクレジット（カードに小さく出す。CCの表示義務）
    r = _gcov(g)
    return f'写真：{r["photo"]["artist"] or "不明"}／{r["photo"]["license"]}' if r else ""
def _gthumbs(g, n=3):
    return ["travel/g/" + r["photo"]["file"] for r in g["spots"] if r.get("photo")][:n]

TRAVEL_BRAND = "最安ナビ トラベル"  # 旅行ページのサブブランド（11/23 Taku）


def header_html(brand=""):
    if brand == "travel":  # サブブランド: 名前を前に、親ブランドは小さく添える。用品の検索窓は出さない（旅行ページに検索カードがあるため）
        return (f'<header class="hd hd-tv"><div class="hd-in"><a class="logo lg-tv" href="travel.html"><span class="lg-tv-n">{TRAVEL_BRAND}</span></a>'
                f'<a class="hd-par" href="index.html">by {SITE}</a><span class="hd-note">楽天の公式サイトではありません</span></div></header>')
    return f'''<header class="hd"><div class="hd-in"><a class="logo" href="index.html"><span class="mk">{icon("paw", 20)}</span>{SITE}</a>
<span class="hd-note">楽天の公式サイトではありません</span><a class="hd-fav" href="favorites.html">{icon("star", 16)}マイリスト<span id="favn"></span></a></div>
<form class="hd-search" action="search.html" role="search"><label for="hq" class="sr">商品を探す</label>
<input type="search" id="hq" name="q" placeholder="商品名・メーカー・カテゴリで探す" autocomplete="off"><button type="submit">{icon("search", 18)}<span>検索</span></button></form></header>'''


HISTORY = {}
MIN_HISTORY_DAYS = 7   # これ未満のあいだは「記録中」とだけ出す
WINDOW = 14


HAS_YAHOO = False  # Yahoo!ショッピングの価格が1件でもあるとき True（main で設定）。False のとき表示は楽天だけの従来どおり
# 公式のテキスト版スニペット（改変禁止。https://developer.yahoo.co.jp/attribution/ 2026-11-16 確認）。ページ下部に置く
YAHOO_CREDIT = ('<!-- Begin Yahoo! JAPAN Web Services Attribution Snippet -->\n'
                '<span style="margin:15px 15px 15px 15px"><a href="https://developer.yahoo.co.jp/sitemap/">Webサービス by Yahoo! JAPAN</a></span>\n'
                '<!-- End Yahoo! JAPAN Web Services Attribution Snippet -->')


def yahoo_foot():
    if not HAS_YAHOO:
        return ""
    return YAHOO_CREDIT


YAHOO_NOTE = ('<p>Yahoo!ショッピングの価格・ポイント（PayPayポイント等の付与予定。条件により変わります）は Yahoo!ショッピングの API の取得結果です。'
              '購入時に Yahoo!ショッピングのページに表示されている価格が適用されます。LINEヤフー株式会社とは関係のない個人運営の比較サイトです。</p>')


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
    title = f"{short}の最安値（{ML(offers)}{n}店を比較）｜{SITE}"
    desc = (f"{nm[:50]}を{MLL(offers)}の{n}ショップで比較（{fetched_date}取得）。今回取得した{n}店のうち最安は{o['price']:,}円"
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
    out, seen_ = [], set()
    for g, prods in published:
        for p in prods:
            GROUP_UNIT[p.get("group") or g["slug"]] = g["unit"]
            x = deal(p, g["unit"])
            if x and p["productId"] not in seen_:  # 同じ商品が2つのカテゴリにあっても1回だけ（11/16 Taku のスクショで重複）
                seen_.add(p["productId"])
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
            f'<span class="td-n">他店より安い <b>{n}</b>件</span><span class="td-f">同じ内容量のショップ（3店以上）の中央値と比べて・取得時点</span><span class="td-b">ランキングを見る</span></a>')


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
    d.rectangle([0, 0, 1200, 14], fill="#ea580c")
    d.text((70, 60), title, font=F(46), fill="#1f2328")
    d.text((70, 150), big, font=F(150), fill="#c2255c")
    d.text((70, 370), sub, font=F(36), fill="#1f2328")
    d.rectangle([0, 540, 1200, 630], fill="#ea580c")
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
    """人気（レビュー件数）×他店との差の上位1件（5,000円以上の枠は 11/16 オーナー判断で削除）"""
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
PT_NOTE = '<a class="kome" href="about.html#notes" aria-label="注記を見る">※</a>'
YPT_NOTE = PT_NOTE  # Yahoo!ショッピングのポイントは API の付与予定（注記はフッターに併記）


def ptxt(x, unit="pt"):
    """ポイント表示。楽天は「◯pt（◯倍）」、Yahoo!は倍率の考え方が違うので PayPay ポイントの数だけ（0 ならリンク先で確認）"""
    if is_y(x):
        return f'PayPayポイント {x["points"]:,}{unit}' if x.get("points", 0) > 0 else "ポイントはリンク先で確認"
    return f'{x["points"]:,}{unit}（{x["pointRate"]}倍）'


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
    gapt = (f'<span class="save-s">他店より {_d["d"]:,}円安い※</span>' if _d and not OUTLET.search(o["name"]) else "")
    fb = free_best(p["offers"])
    fattr = (f' data-fprice="{fb["price"]}" data-feff="{fb["effective"]}" data-fpt="{fb["points"]}" data-frate="{fb["pointRate"]}" data-fptt="{E(ptxt(fb, 'ポイント'))}（目安）" data-fshop="{E(fb["shop"])}"' if fb else "")
    tbadge = trend_badge(trend(p["productId"], o["price"]))
    mr = max(x["pointRate"] for x in p["offers"])
    hit = f'<span class="hi-pt">ポイント{mr}倍の店あり</span>' if mr >= HI_RATE else ""
    return (f'<li data-pop="{p.get("reviewCount", 0)}" data-price="{o["price"]}" data-eff="{oe["effective"]}" data-unit="{up or 0:.4f}" data-aprice="{o["price"]}" data-aeff="{oe["effective"]}"{fattr}>'
            f'<button type="button" class="fav mini" data-fav="{E(p["productId"])}" data-price="{o["price"]}" aria-pressed="false" aria-label="お気に入りに追加">{icon("star", 18)}</button>'
            f'<a class="row" href="{fn}"><span class="pic">{pic}</span><span class="info">'
            f'<span class="nm">{E(show_name(p["name"]))}</span>{stars(p.get("reviewAverage", 0), p.get("reviewCount", 0))}'
            f'<span class="pr"><small class="scope">{len(p["offers"])}店中の最安</small><b class="pv">{o["price"]:,}</b>円 <span class="shipv">{ship(o["shipping"])}</span>{tbadge}</span>'
            f'<span class="pt"><span class="ptv">{ptxt(o)}</span></span>{hit}' + (f'<span class="effn">ポイント分を引いた目安 約{oe["effective"]:,}円</span>' if oe["effective"] < o["price"] else '<span class="effn">ポイント不明のため価格のまま並べています</span>') + ''
            f'<span class="meta">{buy.lstrip("・")}{"・" + upt if upt else ""}</span>{gapt}</span></a></li>')


SORT_JS = ('<script>(function(){var b=document.querySelectorAll(".sort button"),l=document.getElementById("list");'
           'b.forEach(function(x){x.addEventListener("click",function(){var k=x.dataset.k,a=[].slice.call(l.children);'
           'a.sort(function(p,q){return k==="pop"?q.dataset.pop-p.dataset.pop:(p.dataset[k]||1e12)-(q.dataset[k]||1e12)});'
           'a.forEach(function(e){l.appendChild(e)});b.forEach(function(y){y.setAttribute("aria-pressed",y===x)});l.classList.toggle("eff-on",k==="eff")})})})();</script>')


LV_NOTE = "リンク先のモールの商品ページへ移動します。価格は検索した時点のもので、購入時にリンク先に表示されている価格が適用されます。"


def mlab():
    return "楽天・Yahoo!" if HAS_YAHOO else "楽天"


def live_sec(q, title=""):
    """楽天・Yahoo をその場で検索した結果（/api/msearch）。価格は保存しない（エッジで最大6時間だけ）"""
    title = title or f"{mlab()}のほかの商品"
    return (f'<section class="lv" data-q="{E(q)}"><h2 class="h2">{E(title)}</h2><p class="meta lv-c">{mlab()}を検索しています…</p>'
            f'<ul class="list gl lv-l"></ul><a class="lv-more" href="search.html?q={urllib.parse.quote(q)}">「{E(q)}」でもっと探す</a>'
            f'<p class="nayami-n">{LV_NOTE}</p></section>')


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
            f'<button type="button" data-k="unit" aria-pressed="false">{LABEL[g["unit"]]}が安い順</button></div><p class="effnote">ポイント込み＝ショップのポイント倍率の分だけ価格から引いた目安です（SPU・クーポン・楽天カード分は含みません）。ポイントが分からない商品は価格のまま並べます</p>'
            f'<p class="free-note" id="freenote" hidden>送料無料のショップの中での最安に切り替えています。価格はそのショップの税込価格で、送料別の最安より高いことがあります。</p>'
            f'{SHIP_RULE}<p class="h2n">※「他店より◯円安い」＝同じ内容量の他店（3店以上）の価格の中央値との差・取得時点・送料別</p><ul class="list gl" id="list">{rows}</ul>{live_sec(g["keyword"])}'
            f'<div class="note">{("<p>" + carried.replace("<br>", "") + "</p>") if carried else ""}<p>{LABEL[g["unit"]]}の価格＝税込価格 ÷ 内容量（商品名から読み取り。読み取れない商品は載せていません）。'
            f'ポイントはショップの倍率から計算した目安（税抜価格の1%×倍率、1円未満切り捨て）。「ポイント込み」＝税込価格−この目安。送料はどちらにも含みません。計算根拠の最終確認：2026-11-08（楽天市場の通常ポイント 税抜100円につき1ポイント）。</p></div>{SORT_JS}')


def item_ld(p, g, pet_name, fn):
    """検索エンジン・AI 向けの構造化データ（Product＋AggregateOffer、パンくず）。評価は第三者のため載せない"""
    offers = p["offers"]
    prod = {"@context": "https://schema.org", "@type": "Product", "name": show_name(p["name"]),
            "category": g["title"], "image": img(p.get("image"), 300) or None,
            "offers": {"@type": "AggregateOffer", "priceCurrency": "JPY", "lowPrice": min(o["price"] for o in offers),
                       "highPrice": max(o["price"] for o in offers), "offerCount": len(offers),
                       "description": f'{MLL(offers)}で当サイトが取得した{len(offers)}ショップの税込価格の範囲（送料別）。{str(p.get("fetched_at") or FETCHED_FOR_LD)} 取得時点の目安'}}
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


def is_y(o):
    return o.get("mall") == "yahoo"


def ML(offers):
    """比較した範囲の短い名前（Yahoo!ショッピングの価格があるときだけ併記。無ければ従来どおり「楽天」）"""
    return "楽天・Yahoo!" if any(is_y(o) for o in offers) else "楽天"


def MLL(offers):
    return "楽天市場・Yahoo!ショッピング" if any(is_y(o) for o in offers) else "楽天市場"


def mall_tag(o, offers):
    if not any(is_y(x) for x in offers):
        return ""
    return '<span class="mall y">Yahoo!</span>' if is_y(o) else '<span class="mall r">楽天</span>'


def mall_cmp(offers):
    """楽天と Yahoo! それぞれの最安（両方あるときだけ）"""
    r = [o for o in offers if not is_y(o)]
    y = [o for o in offers if is_y(o)]
    if not r or not y:
        return ""
    a, b = min(r, key=lambda o: o["price"]), min(y, key=lambda o: o["price"])
    d = abs(a["price"] - b["price"])
    win = "同じ価格" if d == 0 else (f'{"Yahoo!" if b["price"] < a["price"] else "楽天"}が <b>{d:,}円安い</b>')
    return (f'<p class="mallcmp">楽天の最安 {a["price"]:,}円／Yahoo!の最安 {b["price"]:,}円：{win}'
            f'<small>（比較条件：取得時点の税込価格どうし。送料とポイントは含めずに比べています）</small></p>')


_CU = os.environ.get("RAKUTEN_CARD_URL", "").strip()
CARD_URL = _CU if _CU.startswith("https://hb.afl.rakuten.co.jp/") else ""  # 楽天の正規のアフィリエイトリンクだけ使う


def card_pt(price):
    """楽天カード払いで増えるポイントの目安（楽天カード公式 2026-11-16 確認）：
    通常分＝利用額（税込）100円につき1pt・上限なし／特典分＝利用額（税抜）100円につき1pt・月1,000ptまで（期間限定）"""
    return price // 100 + int(price / 1.1) // 100


def item_body(p, g, fetched):
    offers = sorted(p["offers"], key=lambda o: o["price"])
    o = offers[0]
    oe = min(offers, key=lambda x: (x["effective"], x["price"]))
    big = img(p.get("image"), 300)
    pic = f'<img src="{E(big)}" alt="" width="240" height="240">' if big else '<span class="noimg big" aria-hidden="true"></span>'
    up = unit_price(p, g)
    rows = "".join(
        f'<li data-price="{x["price"]}" data-eff="{x["effective"]}" data-free="{1 if x.get("shipping") == "送料無料" else 0}"><div class="shop">{mall_tag(x, offers)}<span class="sn">{E(x["shop"])}</span>{hi_tag(x["pointRate"])}'
        + ('<span class="outlet">訳あり・アウトレットの可能性（商品名より）</span>' if OUTLET.search(x["name"]) else "") +
        f'<span class="pr"><b>{x["price"]:,}</b>円 {ship(x["shipping"])}</span>'
        f'<span class="pt">{ptxt(x)}{YPT_NOTE if is_y(x) else PT_NOTE}</span>' + (f'<span class="effn">ポイント分を引いた目安 約{x["effective"]:,}円</span>' if x["effective"] < x["price"] else '<span class="effn">ポイント不明のため価格のまま並べています</span>') + '</div>'
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
        card_k, card_v = f'{ML(offers)}{len(offers)}ショップを比較', ''
    card_attr = (f' data-cn="{E(show_name(p["name"])[:60])}" data-cp="{o["price"]}" data-ck="{E(card_k)}" data-cv="{E(card_v)}"'
                 f' data-cd="{E(str(p.get("fetched_at") or fetched)[:10])}"')
    share_text = (f'{show_name(p["name"])[:50]}｜{ML(offers)}{len(offers)}ショップの価格比較（{str(p.get("fetched_at") or fetched)[:10]} 取得分）。取得した中で最安 {o["price"]:,}円'
                  + (f'（同じ内容量で最大 {hi["price"] - o["price"]:,}円の差）' if save else "") + '※価格・ポイントは取得時点の目安。PR（アフィリエイト）サイト')
    actions = (f'<div class="actions"><button type="button" class="fav" data-fav="{E(p["productId"])}" data-price="{o["price"]}" aria-pressed="false">{icon("star", 18)}お気に入り</button>'
               f'<button type="button" class="bought" data-bought="{E(p["productId"])}" data-price="{o["price"]}" data-md="{md}" aria-pressed="false">{icon("bag", 18)}買った日をメモ</button>'
               f'<button type="button" class="wish" data-wish="{E(p["productId"])}" data-price="{o["price"]}" aria-pressed="false">{icon("search", 18)}値下がりを待つ</button>'
               f'<button type="button" class="share" data-share="{E(share_text)}" data-card="1"{card_attr}>{icon("share", 18)}この比較を送る</button></div>'
               f'<form class="wish-f" id="wish-f" hidden><label>この値段になったら買う <input type="number" inputmode="numeric" min="1" id="wish-v"> 円</label><button type="submit">登録</button></form>'
               f'<p class="bought-msg" id="bought-msg" hidden></p><div id="pushbox"></div>')
    alt = ""
    if oe is not o:
        alt = (f'<p class="alt">ポイント{oe["pointRate"]}倍の店：<b>{E(oe["shop"])}</b>（{oe["price"]:,}円・{oe["points"]:,}pt）{PT_NOTE}</p>')
    allshops = (f'<a class="sub-link" href="{E(p["productUrl"])}" rel="sponsored nofollow" target="_blank">楽天市場の商品ページで全{p.get("itemCount", 0):,}ショップを見る</a>'
                if p.get("productUrl") else "")
    summary = (f'<p class="facts">要点：{E(show_name(p["name"])[:60])}は、{MLL(offers)}で取得した{len(offers)}ショップのうち最安 {o["price"]:,}円（{E(o["shop"])}・{"送料無料" if o.get("shipping") == "送料無料" else "送料別"}）。'
               + (f'同じ内容量のショップ間で最大{hi["price"] - o["price"]:,}円の差。' if save else "")
               + f'価格は{E(str(p.get("fetched_at") or fetched))}時点。</p>')
    return (f'<div class="item"><div class="item-pic">{pic}</div><div class="item-info">'
            f'<h1 class="ttl">{E(show_name(p["name"]))}</h1>{summary}'
            + (f'<p class="mk2"><a href="search.html?maker={urllib.parse.quote(p["maker"])}">{E(p["maker"])}の商品をもっと見る</a></p>' if p.get("maker") else "")
            + f'{stars(p.get("reviewAverage", 0), p.get("reviewCount", 0))}'
            f'{actions}'
            f'<div class="best"><p class="best-l">今回取得した{len(offers)}ショップ中の最安値（{E(str(p.get("fetched_at") or fetched))} 時点）</p>'
            f'<p class="pr big"><b>{o["price"]:,}</b>円 {ship(o["shipping"])}</p>'
            f'<p class="pr-note">税込価格・{"このショップは送料無料" if o.get("shipping") == "送料無料" else "送料は別にかかる（ショップにより異なる）"}・ポイントは目安（SPU・クーポンは含みません）</p>'
            f'<p class="pt">{ptxt(o, "ポイント")}{PT_NOTE}</p>'
            + ("" if is_y(o) else f'<p class="card-pt">楽天カード払いでポイントが増える場合あり<span class="card-c">（条件は楽天カード公式で確認）</span><a class="kome" href="about.html#card" aria-label="楽天カードの注記">※</a>'
                         + (f'<a class="card-ln" href="{E(CARD_URL)}" rel="sponsored nofollow noopener" target="_blank">楽天カード（公式サイト）</a>' if CARD_URL else "")
                         + '</p>')
            + f'<p class="shop-l">{E(o["shop"])}</p>'
            + (f'<p class="up">{LABEL[g["unit"]]} {up:,.1f}円（内容量 {fmt_amount(p["amount"], g["unit"])}・商品名「{E(detail(o["name"]) or fmt_amount(p["amount"], g["unit"]))}」から読み取り）</p>' if up else "") +
            f'{shipnote}{save}{mall_cmp(offers)}{tr}<a class="btn big" href="{E(o["url"])}" rel="sponsored nofollow" target="_blank">取得した中で最安のショップで見る</a>{alt}</div></div></div>'
            f'<h2 class="h2">この商品を売っているショップ</h2>'
            f'<div class="sort" role="group" aria-label="並べ替え"><span>並べ替え</span>'
            f'<label class="free-only"><input type="checkbox" id="freeonly"> 送料無料だけ</label>'
            f'<button type="button" data-k="price" aria-pressed="true">安い順</button>'
            f'<button type="button" data-k="eff" aria-pressed="false">ポイント込みで安い順</button></div><p class="effnote">ポイント込み＝ショップのポイント倍率の分だけ価格から引いた目安です（SPU・クーポン・楽天カード分は含みません）。ポイントが分からない商品は価格のまま並べます</p>'
            f'{SHIP_RULE}<ul class="shops" id="list">{rows}</ul><p class="empty" id="freeempty" hidden>送料無料のショップは、今回の取得にはありませんでした。</p>{allshops}'
            f'<div class="note"><p>価格取得日時: {E(str(p.get("fetched_at") or fetched))}</p></div>{SORT_JS}')


def main():
    groups = json.load(open(os.path.join(ROOT, "products.json"), encoding="utf-8"))["groups"]
    data = json.load(open(os.path.join(ROOT, "data", "prices.json"), encoding="utf-8"))
    sample = str(data.get("fetched_at", "")).startswith("SAMPLE")
    load_history()
    global HAS_YAHOO
    HAS_YAHOO = any(is_y(o) for p in data.get("products", []) for o in p.get("offers", []))
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
    by_group = {}
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
        by_group[g["slug"]] = prods
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
    tabs, secs, side, circ, deal_tiles, pet_names, nayami_urls, brand_urls = [], [], [], [], [], [], [], []
    for pid, name, icon, animals in PETS:
        cats = [(g, ps) for g, ps in published if g["animal"] in animals]
        if not cats:
            continue
        pet_names.append((pid, name))
        tabs.append(f'<li><a href="#{pid}">{globals()["icon"](icon, 20)}{name}</a></li>')
        # 写真：そのペットでいちばんレビューの多い商品の画像（楽天の商品画像をそのまま表示。加工しない）
        rp = max((p for _, ps in cats for p in ps), key=lambda p: p.get("reviewCount", 0))
        rth = img(rp.get("image"), 128)
        n_p = sum(len(ps) for _, ps in cats)
        _pp = f"pets/{pid}.jpg" if os.path.exists(os.path.join(ROOT, "assets", "pets", f"{pid}.jpg")) else rth
        circ.append(f'<li><a href="#{pid}" data-tab="{pid}"><span class="circ-p">' + (f'<img src="{E(_pp)}" alt="{name}" width="100" height="100" loading="lazy">' if _pp else globals()["icon"](icon, 32))
                    + f'</span><span class="circ-n">{name}</span><span class="circ-c">{n_p}商品</span></a></li>')
        side.append(f'<li class="side-pet"><a href="#{pid}">{globals()["icon"](icon, 18)}{name}</a><ul>'
                    + "".join(f'<li><a href="{g["slug"]}.html">{E(g["title"])}</a></li>'
                              for g, ps in sorted(cats, key=lambda c: (c[0].get("priority", 9), c[0]["title"])) if g["animal"] != "犬・猫")
                    + '</ul></li>')
        if pid == "cat":  # 犬・猫共通の商品（ウェットティッシュ・消臭剤など）はサイドで1回だけ
            _both = sorted([g for g, ps in published if g["animal"] == "犬・猫"], key=lambda g: (g.get("priority", 9), g["title"]))
            if _both:
                side.append(f'<li class="side-pet"><a href="#dog">{globals()["icon"]("dog", 18)}犬・猫 共通</a><ul>'
                            + "".join(f'<li><a href="{g["slug"]}.html">{E(g["title"])}</a></li>' for g in _both) + '</ul></li>')
        cs = []
        for g, ps in sorted(cats, key=lambda c: (c[0].get("priority", 9), c[0]["title"])):
            cp = min(ps, key=lambda p: best_offer(p)["price"])
            th = img(cp.get("image"), 128)
            pic = f'<img src="{E(th)}" alt="" width="72" height="72" loading="lazy">' if th else '<span class="noimg" aria-hidden="true"></span>'
            cs.append(f'<li data-q="{E(g["title"] + " " + g["keyword"])}"><a class="cat" href="{g["slug"]}.html"><span class="pic">{pic}</span>'
                      f'<span class="info"><span class="nm">{E(g["title"])}</span><span class="meta">{len(ps)}商品を比較</span></span></a></li>')
            _md = [x for x in drops([(g, ps)])]
            if _md and not any(t[1] is g for t in deal_tiles):  # 犬・猫共通のカテゴリは1枚だけ（11/23 Taku）
                deal_tiles.append((max(round(x["d"] / x["hi"] * 100) for x in _md), g, len(_md), "犬・猫" if g["animal"] == "犬・猫" else name))
        secs.append(f'<section class="pet-sec" id="{pid}" data-pet="{pid}"><h2 class="h2">{globals()["icon"](icon, 22)}{name}</h2><ul class="cats">{"".join(cs)}</ul></section>')
    from collections import Counter
    makers = [m for m, n in Counter(r["m"] for r in index_rows if r["m"]).most_common(10) if n >= 2]
    mk = "".join(f'<li><a href="search.html?maker={urllib.parse.quote(m)}">{E(m)}</a></li>' for m in makers)
    # 悩みから探す：すでに載せている商品のうち、商品名に語がある商品だけ（追加の取得なし・効果はうたわない）
    nayami, nayami_cards = [], []
    try:
        concerns = json.load(open(os.path.join(ROOT, "concerns.json"), encoding="utf-8"))["concerns"]
    except (OSError, ValueError):
        concerns = []
    for c in concerns:
        src = [s_ for s_ in c["from"] if s_ in by_group]
        cps = [p for s_ in src for p in by_group[s_]
               if any(w in p["name"] or w in best_offer(p)["name"] for w in c["words"])]
        if not src:
            continue
        lq = f'{c["pet"]} {c["words"][0]} フード'
        g0 = dict(gmap[src[0]], slug=c["slug"], title=f'{c["title"]}（{c["pet"]}のフード）', keyword=lq)
        pet = next((pt for pt in PETS if c["pet"] in pt[3]), PETS[0])
        fn = c["slug"] + ".html"
        _nn = f'<p class="nayami-n">商品名に「{E(c["words"][0])}」などの記載がある商品です。効果を示すものではありません。気になる症状は動物病院へ</p>'
        body = (category_body(g0, cps, pet, fetched, "").replace("</h1>", "</h1>" + _nn, 1) if cps
                else f'<h1 class="ttl">{E(g0["title"])}</h1>{_nn}{live_sec(lq, f"{mlab()}の商品")}')
        open(os.path.join(out, fn), "w", encoding="utf-8").write(
            page(f'{c["pet"]}の{c["title"]}｜商品名に記載のあるフード{len(cps)}商品の価格比較｜{SITE}', body,
                 f'商品名に「{c["words"][0]}」などの記載がある{c["pet"]}用フード{len(cps)}商品を、ショップごとの価格とポイントで比較（{fdate}取得）。効果を示すものではありません。',
                 sample, ((pet[1], f"index.html#{pet[0]}"), (g0["title"], None)), canonical=(f"{origin}/{fn}" if origin else "")))
        files.append(fn)
        nayami_urls.append(fn)
        nayami.append(f'<li><a href="{fn}">{E(c["pet"])}｜{E(c["title"])}</a></li>')
        nayami_cards.append((c, fn, lq, len(cps)))
    # 海外ブランド：製造国を確認できたブランド（brands.json の show）だけ。すでに載せている国内の正規品から
    kaigai, brand_cards = [], []
    try:
        brands = [b for b in json.load(open(os.path.join(ROOT, "brands.json"), encoding="utf-8"))["brands"] if b.get("show") and b.get("country")]
    except (OSError, ValueError):
        brands = []
    allp = {p["productId"]: (p, gmap[p["group"]]) for ps_ in by_group.values() for p in ps_}
    for b in brands:
        bps = [(p, g_) for p, g_ in allp.values() if any(w.lower() in (p["name"] + " " + (p.get("maker") or "")).lower() for w in b["words"])]
        fn = "brand-" + hashlib.md5(b["name"].encode()).hexdigest()[:8] + ".html"
        rows = "".join(product_row(p, g_, f"item-{p['productId']}.html") for p, g_ in sorted(bps, key=lambda x: -x[0].get("reviewCount", 0)))
        body = (f'<h1 class="ttl">{E(b["name"])}<span class="cty">{E(b["country"])}製</span></h1>'
                + (f'<p class="fresh1">くわしい比較 {len(bps)}商品・国内の正規販売店の価格</p><ul class="list gl">{rows}</ul>' if bps else "")
                + live_sec(b["name"], f'{mlab()}の{b["name"]}'))
        open(os.path.join(out, fn), "w", encoding="utf-8").write(
            page(f'{b["name"]}（{b["country"]}）の価格比較 {len(bps)}商品｜{SITE}', body,
                 f'{b["name"]}（{b["country"]}製）のペットフード{len(bps)}商品を、国内のショップの価格とポイントで比較（{fdate}取得）。',
                 sample, (("海外ブランド", None), (b["name"], None)), canonical=(f"{origin}/{fn}" if origin else "")))
        files.append(fn)
        brand_urls.append(fn)
        kaigai.append(f'<li><a href="{fn}">{E(b["name"])}<small>{E(b["country"])}</small></a></li>')
        brand_cards.append((b, fn, len(bps)))
    if nayami_cards:
        _pets = []
        for c, fn, lq, n in nayami_cards:
            if c["pet"] not in _pets:
                _pets.append(c["pet"])
        hub = (f'<h1 class="ttl">悩みから探す</h1><p class="lead">うちの子の困りごとを選ぶと、{mlab()}で商品名にその言葉がある商品を安い順に出します。効果を示すものではありません。気になる症状は動物病院へ。</p>'
               '<form class="nq" id="nqf" action="search.html" role="search"><input type="hidden" name="q" id="nqq"><select id="nqp" aria-label="ペット">' + "".join(f'<option>{E(x)}</option>' for x in _pets) + '</select>'
               '<input type="search" id="nqw" placeholder="悩みを入力（例：食いつき、口臭）" required><button type="submit">探す</button></form>'
               + "".join(f'<section class="nh"><h2 class="h2">{E(pn)}の悩み</h2><ul class="hub">'
                         + "".join(f'<li><a href="{fn}"><b>{E(c["title"])}</b><span class="hub-c" data-cnt-q="{E(lq)}">…</span><small>{mlab()}の件数</small></a></li>'
                                   for c, fn, lq, n in nayami_cards if c["pet"] == pn) + '</ul></section>' for pn in _pets))
        open(os.path.join(out, "nayami.html"), "w", encoding="utf-8").write(page(f"悩みから探す｜{SITE}", hub,
             "涙やけ・毛玉・下部尿路・シニア・体重管理など、うちの子の悩みからペットフードを探して、楽天・Yahoo!の価格を比較。", sample,
             (("悩みから探す", None),), canonical=(f"{origin}/nayami.html" if origin else "")))
        files.append("nayami.html")
        nayami_urls.insert(0, "nayami.html")
    if brand_cards:
        hub = (f'<h1 class="ttl">海外ブランド</h1><p class="lead">製造国を販売元・公式の表示で確かめたブランドだけを載せています。国内のショップ（{mlab()}）の価格で比べられます。</p>'
               '<form class="nq" action="search.html" role="search"><input type="search" name="q" id="bq" placeholder="ブランド名で探す（例：ジウィピーク）" autocomplete="off"><button type="submit">探す</button></form>'
               '<ul class="hub hub-b" id="bhub">' + "".join(
                   f'<li data-n="{E(b["name"] + " " + " ".join(b["words"]))}"><a href="{fn}"><b>{E(b["name"])}</b><em>{E(b["country"])}製</em>'
                   f'<span class="hub-c" data-cnt-q="{E(b["name"])}">…</span><small>{mlab()}の件数</small></a></li>' for b, fn, n in brand_cards) + '</ul>')
        open(os.path.join(out, "brands.html"), "w", encoding="utf-8").write(page(f"海外ブランドのペットフード｜{SITE}", hub,
             "ジウィピーク・モグワン・アカナ・ニュートロなど海外ブランドのペットフードを、国内ショップ（楽天・Yahoo!）の価格で比較。", sample,
             (("海外ブランド", None),), canonical=(f"{origin}/brands.html" if origin else "")))
        files.append("brands.html")
        brand_urls.insert(0, "brands.html")
    # ペットと泊まれる宿（楽天トラベル・その場検索。11/21 Taku「旅行系のアフィリエイトもとりたい」）
    _prefs = "北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 埼玉県 千葉県 東京都 神奈川県 新潟県 富山県 石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県 三重県 滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県 鳥取県 島根県 岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県".split()
    _tags = ["ドッグラン", "温泉", "大型犬", "猫", "貸切", "部屋食"]
    _themes = [("beach", "海辺の宿で、夕陽を眺める", "海"), ("mountain", "高原の風を、一緒に歩く", "高原"), ("lake", "湖畔で、静かな朝を", "湖"),
               ("bed", "同じ部屋で、くつろぐ夜", "同室"), ("beach2", "ドッグランで、思いきり", "ドッグラン"), ("car", "車で、気ままに", "")]
    _popular = [("長野県", "軽井沢・白馬"), ("栃木県", "那須"), ("静岡県", "伊豆"), ("神奈川県", "箱根"), ("山梨県", "富士五湖"), ("千葉県", "房総")]
    _regions = [('北海道・東北', '北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県'), ('関東', '茨城県 栃木県 群馬県 埼玉県 千葉県 東京都 神奈川県'), ('甲信越・北陸', '新潟県 富山県 石川県 福井県 山梨県 長野県'), ('東海', '岐阜県 静岡県 愛知県 三重県'), ('近畿', '滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県'), ('中国・四国', '鳥取県 島根県 岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県'), ('九州・沖縄', '福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県')]
    _budgets = [("", "予算こだわらない"), ("0-10000", "〜1万円"), ("10000-20000", "1〜2万円"), ("20000-30000", "2〜3万円"), ("30000-50000", "3〜5万円"), ("50000-", "5万円〜")]
    # お泊まりの持ち物チェックリスト（予約のあと、ここへ戻ってきてもらう。11/23 Taku）
    _goods = [("キャリーバッグ", "ペット キャリー"), ("ドライブボックス", "犬 ドライブボックス"), ("折りたたみボウル", "ペット 折りたたみ ボウル"),
              ("いつものフード（小分け）", "ドッグフード 小分け"), ("マナーパンツ", "マナーパンツ 犬"), ("携帯シーツ", "トイレシーツ 携帯"),
              ("消臭スプレー", "ペット 消臭スプレー"), ("粘着クリーナー", "粘着クリーナー ペット"), ("迷子札", "迷子札 犬"),
              ("リード（予備）", "犬 リード"), ("おやつ", "犬 おやつ"), ("ライフジャケット", "犬 ライフジャケット")]
    _sh = lambda en, ja, sub="": f'<div class="tv-hd"><span class="tv-en">{en}</span><h2 class="tv-h2">{ja}</h2>' + (f'<p class="tv-sub">{sub}</p>' if sub else "") + '</div>'
    # 一休のような上質なトーンと構成（11/23 Taku。旅行ページだけ）: 写真主役・明朝の見出し・余白・検索カード→特集→行き先→エリア→宿
    tv = ('<div class="tvx"><div class="tv-hero"><span class="tv-ph" style="background-image:url(travel/car.jpg)"></span><div class="tv-t"><small>PET TRAVEL GUIDE</small>'
          '<h1>ペットと行ける旅先も、<br>泊まれる宿も、ここで。</h1><p>都道府県をえらぶと、宿・スポット・お店がまとめて出ます</p></div></div>'
          '<form class="tv-f" id="tvf"><div class="tv-fr"><label for="tva">行き先</label><select id="tva"><option value="">都道府県を選ぶ</option>'
          + "".join(f'<option>{E(p)}</option>' for p in _prefs) + '</select></div>'
          '<div class="tv-fr"><span class="tv-fl">ご予算（1室1泊）</span><div class="tv-bd" id="tvbd">'
          + "".join(f'<button type="button" data-b="{v}" aria-pressed="{"true" if not v else "false"}">{E(n)}</button>' for v, n in _budgets) + '</div></div>'
          '<div class="tv-fr"><span class="tv-fl">こだわり（選ぶと宿を絞り込みます）</span><div class="tv-tags" id="tvt">' + "".join(f'<button type="button" data-q="{E(t)}" aria-pressed="false">{E(t)}</button>' for t in _tags) + '</div>'
          '<p class="tv-tn">例：栃木県のペットと泊まれる宿 168軒 → 「貸切」71軒・「ドッグラン」50軒・「部屋食」14軒（楽天トラベル 2026/11/23 調べ）</p></div>'
          '<button type="submit" class="tv-go-b">さがす</button></form>'
          + '<section class="tv-gs" id="guide">' + _sh("GUIDE", "まだ決めていないなら、写真から", "写真をえらぶと、その県の宿・スポット・お店が下に出ます")
          + '<p class="tv-gv" id="tvgv" hidden></p>'
          + '<div class="tv-rg tv-grg" id="tvgrg" role="tablist">' + "".join(f'<button type="button" role="tab" data-g="{k}" aria-selected="{"true" if k == 1 else "false"}">{E(n)}</button>' for k, (n, _p) in enumerate(_regions)) + '</div>'
          + "".join(f'<div class="tv-gl" data-g="{k}"{"" if k == 1 else " hidden"}>' + "".join(
                f'<button type="button" class="tv-glb" data-a="{E(p)}" data-ga="guide_pick"><span class="tv-glp" data-bg="{_gcover(_GBP[p])}"{" style=" + chr(34) + "background-image:url(" + _gcover(_GBP[p]) + ")" + chr(34) if k == 1 else ""}><em class="tv-glpl">{E(_gplace(_GBP[p]))}</em></span>'
                f'<span class="tv-glt"><b>{E(_GBP[p]["name"])}</b><small>スポット{len(_GBP[p]["spots"])}・お店{len(_GBP[p]["shops"])}</small><small class="tv-glc">{E(_gcred(_GBP[p]))}</small></span></button>' for p in ps.split() if p in _GBP) + '</div>'
                for k, (_n, ps) in enumerate(_regions)) + '</section>'
          + '<script>(function(){var R=' + json.dumps({p: k for k, (_n, ps) in enumerate(_regions) for p in ps.split()}, ensure_ascii=False) + ',tb=document.querySelectorAll("#tvgrg button"),gl=document.querySelectorAll(".tv-gl[data-g]");'
          'function sel(k){tb.forEach(function(b){b.setAttribute("aria-selected",b.dataset.g==k?"true":"false")});gl.forEach(function(x){var on=x.dataset.g==k;x.hidden=!on;if(on)x.querySelectorAll("[data-bg]").forEach(function(s){s.style.backgroundImage="url("+s.dataset.bg+")"})})}'
          'tb.forEach(function(b){b.onclick=function(){sel(b.dataset.g);if(window.ga)ga("guide_region",{r:+b.dataset.g})}});'
          'var a="";try{a=localStorage.getItem("pn_tva")||""}catch(e){}if(a&&R[a]!=null)sel(R[a]);'
          'var v=null;try{v=JSON.parse(localStorage.getItem("pn_gv")||"null")}catch(e){}var e=document.getElementById("tvgv");'
          'if(v&&v.s&&e){e.innerHTML=\'<a href="travel-\'+v.s+\'.html" data-ga="guide_recent"><i style="background-image:url(\'+v.c+\')"></i><span><small>前に見たガイド</small><b>\'+String(v.n).replace(/[<>&"]/g,"")+\'</b></span><em>続きを見る ›</em></a>\';e.hidden=false}})()</script>'
          + '<div id="tvh">' + _sh("SHIORI", "旅のしおり", "宿・スポット・お店を、えらんだ県でまとめて") + '</div><p class="tv-gdl" id="tvgd" hidden></p>'
          '<div class="tv-sel" id="tvsel" hidden><div class="tv-tabs" role="tablist" id="tvtabs"><button type="button" role="tab" data-t="h" aria-selected="true">宿</button>'
          '<button type="button" role="tab" data-t="s" aria-selected="false">スポット<span id="tvns"></span></button><button type="button" role="tab" data-t="d" aria-selected="false">お店<span id="tvnd"></span></button></div>'
          '<div class="tv-gp" id="tvgp" hidden><div class="tv-chips" id="tvchips"></div><ul class="tg-l" id="tvgi"></ul>'
          '<p class="tv-warn">営業時間・ペット同伴の条件は変わることがあります。お出かけ前に、公式サイトや公式Instagramで最新の情報をご確認ください。</p>'
          '<a class="tg-back" id="tvgl" href="#">この県のガイドをぜんぶ見る ›</a>'
          '<a class="tg-back" href="submit.html" data-ga="submit_from_travel">ペットOKのお店・スポットを教えてください ›</a></div></div>'
          + '<script>window.TG=' + json.dumps({p: [g["slug"], g["name"], _gcover(g), len(g["spots"]), len(g["shops"]), _gthumbs(g), _gcred(g)] for p, g in _GBP.items()}, ensure_ascii=False) + '</script>'
          '<p class="meta tv-c" id="tvc">行き先を選ぶと、ペットと泊まれる宿が出ます</p>'
          '<section class="tv-ru" id="tvru" hidden><div class="tv-ru-h"><b>迷ったら、宿ルーレット</b><span id="tvrn"></span></div>'
          '<div class="tv-wh"><div class="tv-wp" aria-hidden="true"></div><div class="tv-wl" id="tvwl"></div></div>'
          '<button type="button" class="tv-sp" id="tvsp" data-ga="roulette_spin">ルーレットを回す</button><ol class="tv-lg" id="tvlg"></ol>'
          '<div class="tv-win" id="tvwin" hidden></div><p class="h2n">エリア・予算・テーマに合う宿から、最大8件をランダムに選んで回します</p></section>'
          '<ul class="tv-l" id="tvl"></ul>'
          '<a class="tv-rr" href="https://ck.jp.ap.valuecommerce.com/servlet/referral?sid=3788390&amp;pid=892753722&amp;vc_url=https%3A%2F%2Fwww.rurubu.travel%2Ftheme%2Fpet%2F%3Futm_source%3Dvaluecommerce%26utm_medium%3Daffiliate" '
          'target="_blank" rel="sponsored nofollow noopener" data-ga="rurubu_pet"><img src="https://ad.jp.ap.valuecommerce.com/servlet/gifbanner?sid=3788390&amp;pid=892753722" height="1" width="0" alt="" style="border:0">'
          '<span class="tv-rrp" style="background-image:url(travel/beach.jpg)"></span><span class="tv-rr-t"><small>るるぶトラベル（JTB）の特集</small><b>ペットと泊まれる宿</b><span>ドッグラン・愛犬用風呂・大型犬OKなどで選べる</span></span><span class="tv-rr-go">特集を見る ›</span></a>'
          f'<section class="tv-pr" id="prep">' + _sh("PREPARATION", "お泊まりの持ち物") + f'<div class="tv-prr"><span>チェックして準備。足りない物は、その場で最安を探せます</span><b class="tv-prn" id="tvprn">0/{len(_goods)}</b></div>'
          '<div class="tv-prb"><i id="tvprb"></i></div><ul class="tv-pl">'
          + "".join(f'<li><label><input type="checkbox" data-pk="{i}"><span>{E(n)}</span></label><a href="search.html?q={urllib.parse.quote(q)}" data-ga="travel_goods">最安を見る ›</a></li>' for i, (n, q) in enumerate(_goods)) + '</ul></section>'
          '<script>(function(){var k="pn_prep",m={};try{m=JSON.parse(localStorage.getItem(k)||"{}")}catch(e){}var bs=document.querySelectorAll("[data-pk]"),n=document.getElementById("tvprn"),b=document.getElementById("tvprb");'
          'function d(){var c=0;bs.forEach(function(x){x.checked=!!m[x.dataset.pk];x.closest("li").classList.toggle("on",x.checked);if(x.checked)c++});n.textContent=c+"/"+bs.length;b.style.width=(100*c/bs.length)+"%";if(c===bs.length)n.textContent="そろった！"}'
          'bs.forEach(function(x){x.onchange=function(){m[x.dataset.pk]=x.checked?1:0;try{localStorage.setItem(k,JSON.stringify(m))}catch(e){}d();if(x.checked){ga("prep_check",{i:+x.dataset.pk});if(window.pnXP)pnXP()}}});d()})()</script>'
          '<p class="nayami-n">宿の名前やプランに「ペット」とある宿を出しています。泊まれるペットの種類・大きさ・頭数・料金は宿ごとに違うので、予約の前にリンク先の「ペット宿泊情報」で必ず確かめてください。'
          '料金は1室1泊の最安の目安（検索した時点）で、予約時はリンク先の料金が適用されます。リンク先は楽天トラベル・るるぶトラベルです。</p></div>')
    open(os.path.join(out, "travel.html"), "w", encoding="utf-8").write(page(f"ペットと泊まれる宿を探す｜{TRAVEL_BRAND}（{SITE}）", tv,
         "犬・猫と泊まれる宿を都道府県から探して、楽天トラベルで予約。ドッグラン・温泉・大型犬OKなどで絞り込めます。", sample,
         ((TRAVEL_BRAND, None),), canonical=(f"{origin}/travel.html" if origin else ""), og_image=(f"{origin}/travel/car.jpg" if origin else "travel/car.jpg"), brand="travel"))
    files.append("travel.html")
    # 情報提供フォーム（11/24 Taku「あれば教えてねというスタンス。店名・URL・店内OK/テラスOK・写真は任意」）
    _sub = ('<div class="tvx"><div class="tv-hd"><span class="tv-en">TELL US</span><h1 class="tv-h2">ペットOKのお店・スポットを教えてください</h1>'
            '<p class="tv-sub">いただいた情報は、一郎（このサイトのAI）が公式サイトで確かめてから載せます。載せるまで数日かかることがあります</p></div>'
            '<form class="sb-f" id="sbf"><input type="text" name="hp" tabindex="-1" autocomplete="off" class="sb-hp" aria-hidden="true">'
            '<fieldset><legend>種類</legend><label><input type="radio" name="kind" value="shop" checked> お店（カフェ・レストランなど）</label><label><input type="radio" name="kind" value="spot"> スポット（公園・観光地・ドッグランなど）</label></fieldset>'
            '<label>名前<span class="sb-r">必須</span><input name="name" required maxlength="80" placeholder="例：〇〇カフェ"></label>'
            '<label>都道府県<span class="sb-r">必須</span><select name="pref" required><option value="">えらぶ</option>' + "".join(f'<option>{E(p)}</option>' for p in _prefs) + '</select></label>'
            '<label>市区町村<input name="where" maxlength="60" placeholder="例：那須町"></label>'
            '<label>公式サイトのURL<input name="url" type="url" maxlength="300" placeholder="https://"></label>'
            '<label>公式Instagram<input name="ig" maxlength="80" placeholder="@から始まるアカウント名"></label><p class="sb-n">URLかInstagramのどちらかは必須です</p>'
            '<fieldset><legend>ペットと入れるところ</legend>' + "".join(f'<label><input type="radio" name="dog" value="{v}"{" checked" if v == "わからない" else ""}> {v}</label>' for v in ["店内OK", "テラス席OK", "屋外OK", "わからない"]) + '</fieldset>'
            '<fieldset><legend>大型犬</legend>' + "".join(f'<label><input type="radio" name="big" value="{v}"{" checked" if v == "わからない" else ""}> {v}</label>' for v in ["可", "不可", "わからない"]) + '</fieldset>'
            '<fieldset><legend>あなたは</legend>' + "".join(f'<label><input type="radio" name="rel" value="{v}"{" checked" if v == "利用した人" else ""}> {v}</label>' for v in ["利用した人", "お店・施設の人"]) + '</fieldset>'
            '<label>ひとこと（任意）<textarea name="note" maxlength="400" rows="3" placeholder="例：テラス席にリードフックあり"></textarea></label>'
            '<label>写真（任意・1枚）<input type="file" name="photo" accept="image/*"></label>'
            '<label class="sb-ok"><input type="checkbox" name="photo_ok"> 写真は自分で撮影したもの（またはお店・施設の人として権利がある）で、このサイトに載せることを許可します。写真に写っている人の顔は載せません</label>'
            '<p class="sb-n">お名前・メールアドレスなどの個人情報は受け取りません。送った内容は、確認のため公開の場所（GitHub の issue）に記録されます。写真は最大60日保管し、載せる場合は縮小してこのサイトに置きます。載せるかどうか・文章は当サイトが決めます</p>'
            '<button type="submit" class="tv-go-b" id="sbgo">送る</button><p class="sb-m" id="sbm" role="status"></p></form>'
            '<script>(function(){var f=document.getElementById("sbf"),m=document.getElementById("sbm"),b=document.getElementById("sbgo");'
            'function shrink(file){return new Promise(function(ok,ng){var im=new Image(),u=URL.createObjectURL(file);im.onload=function(){var k=Math.min(1,1200/Math.max(im.width,im.height)),c=document.createElement("canvas");c.width=Math.round(im.width*k);c.height=Math.round(im.height*k);c.getContext("2d").drawImage(im,0,0,c.width,c.height);URL.revokeObjectURL(u);ok(c.toDataURL("image/jpeg",.8))};im.onerror=ng;im.src=u})}'
            'f.addEventListener("submit",function(e){e.preventDefault();var d={};new FormData(f).forEach(function(v,k){if(k!=="photo")d[k]=v});d.photo_ok=!!f.photo_ok.checked;'
            'if(!d.url&&!d.ig){m.textContent="公式サイトのURLか、公式Instagramを入れてください";return}var fl=f.photo.files[0];'
            'b.disabled=true;m.textContent="送っています…";(fl&&d.photo_ok?shrink(fl):Promise.resolve("")).then(function(p){d.photo=p;return fetch("/api/submit",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(d)})})'
            '.then(function(r){return r.json()}).then(function(r){b.disabled=false;if(r.ok){f.reset();m.textContent="ありがとうございます！受け付けました。確かめてから載せます";if(window.ga)ga("submit_ok",{})}'
            'else m.textContent=r.error==="limit"?"今日はこれ以上送れません。また明日お願いします":r.error==="disabled"?"ただいま準備中です。もう少しお待ちください":"送れませんでした。入力を見直してください"})'
            '.catch(function(){b.disabled=false;m.textContent="送れませんでした。時間をおいてお試しください"})})})()</script></div>')
    open(os.path.join(out, "submit.html"), "w", encoding="utf-8").write(page(f"ペットOKのお店・スポットを教えてください｜{TRAVEL_BRAND}", _sub,
         "ペットと行けるお店・スポットの情報をお寄せください。公式サイトで確かめてから載せます。", sample,
         ((TRAVEL_BRAND, "travel.html"), ("情報提供", None)), canonical=(f"{origin}/submit.html" if origin else ""), brand="travel"))
    files.append("submit.html")
    os.makedirs(os.path.join(out, "travel", "gd"), exist_ok=True)  # 旅のしおり：県を選んだ時に読むスポット・お店（写真のあるものを先に）
    def _gj(r):
        ph = r.get("photo")
        return {"n": r["name"], "w": r.get("where") or "", "d": r.get("desc") or "", "c": r.get("cond") or "", "s": r.get("site") or "", "i": r.get("ig") or "",
                "ph": ({"f": ph["file"], "a": ph["artist"] or "不明", "l": ph["license"], "u": _licu(ph["license"]), "p": ph["page"], "x": 1 if ph.get("user") else 0} if ph else None)}
    for g in _GUIDES:
        _o = lambda rows: [_gj(r) for r in sorted(rows, key=lambda r: 0 if r.get("photo") else (1 if r.get("ig") else 2))]
        json.dump({"s": _o(g["spots"]), "d": _o(g["shops"])}, open(os.path.join(out, "travel", "gd", g["slug"] + ".json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    for g in _GUIDES:  # 行き先ごとの「ペットとおでかけガイド」（宿は載せない）
        def _item(r, kind):
            ph, ig = r.get("photo"), r.get("ig", "")
            top = ""
            if ph:
                top = (f'<figure class="tg-f"><img src="travel/g/{E(ph["file"])}" alt="{E(r["name"])}" loading="lazy" decoding="async">'
                       f'<figcaption><b class="tg-fp">{E(r["name"])}（{E(r["where"])}）</b>' + ('写真：投稿者提供（掲載許可済み）' if ph.get("user") else f'写真：<a href="{E(ph["page"])}" target="_blank" rel="noopener nofollow">{E(ph["artist"] or "不明")}（Wikimedia Commons）</a>／{_lic(ph["license"])}・縮小して掲載') + '</figcaption></figure>')
            elif ig:
                top = f'<div class="tg-ig"><iframe src="https://www.instagram.com/{E(ig)}/embed/" loading="lazy" title="{E(r["name"])}の公式Instagram" scrolling="no"></iframe></div>'
            lk = (f'<a href="{E(r["site"])}" target="_blank" rel="noopener nofollow" data-ga="guide_official">公式サイト ›</a>' if r.get("site") else "") + \
                 (f'<a href="https://www.instagram.com/{E(ig)}/" target="_blank" rel="noopener nofollow" data-ga="guide_ig">公式Instagram ›</a>' if ig else "")
            return (f'<li class="tg-i{" tg-hp" if top else ""}">{top}<div class="tg-b"><span class="tg-k">{kind}</span><h3 class="tg-n">{E(r["name"])}</h3><p class="tg-w">{E(r["where"])}</p>'
                    f'<p class="tg-d">{E(r["desc"])}</p><p class="tg-c"><b>同伴</b>{E(r["cond"])}</p>' + (f'<p class="tg-o">{lk}</p>' if lk else "") + '</div></li>')
        def _items(rows, kind):  # 写真のあるものを先に
            return "".join(_item(r, kind) for r in sorted(rows, key=lambda r: 0 if (r.get("photo") or r.get("ig")) else 1))
        _rg = next((ps.split() for _n, ps in _regions if g["pref"] in ps.split()), [])
        _near = [_GBP[q] for q in _rg if q in _GBP and q != g["pref"]]
        _hq = f'travel.html?area={urllib.parse.quote(g["pref"])}#tvh'
        gb = ('<div class="tvx"><div class="tv-hero tg-hero"><span class="tv-ph" style="background-image:url(' + _gcover(g) + ')"></span>' + (f'<span class="tg-hc">{E(_gcred(g))}・縮小して掲載</span>' if _gcred(g) else '') + '<div class="tv-t"><small>PET FRIENDLY GUIDE</small>'
              f'<h1>{E(g["name"])}<br>ペットとおでかけ</h1><p>{E(g["lead"])}</p></div></div>'
              f'<div class="tg-act"><a class="tg-a1" href="{_hq}" data-ga="guide_to_hotels_top">{E(g["pref"])}の泊まれる宿を探す ›</a><a class="tg-a2" href="#near" data-ga="guide_near_top">ほかの行き先と比べる</a></div>'
              f'<p class="tg-sum">スポット{len(g["spots"])}・お店{len(g["shops"])}　<a href="#dining">お店へ ↓</a></p>'
              '<p class="tg-warn">営業時間・ペット同伴の条件は変わることがあります。お出かけ前に、各施設・お店の公式サイトや公式Instagramで最新の情報をご確認ください。</p>'
              + _sh("SPOTS", "立ち寄りスポット") + '<ul class="tg-l">' + _items(g["spots"], "スポット") + '</ul>'
              + '<div id="dining"></div>' + _sh("DINING", "ペットと一緒に行けるお店", "同伴の条件（テラス席のみ／店内OK など）はお店ごとに違います") + '<ul class="tg-l">' + _items(g["shops"], "お店") + '</ul>'
              + f'<a class="tg-cta" href="{_hq}" data-ga="guide_to_hotels">{E(g["pref"])}のペットと泊まれる宿を探す ›</a>'
              + '<a class="tg-back" href="travel.html#prep">お泊まりの持ち物をそろえる ›</a>'
              + '<a class="tg-back" href="submit.html" data-ga="submit_from_guide">ペットOKのお店・スポットを教えてください ›</a>'
              + '<section id="near">' + _sh("NEARBY", "近くの行き先", "同じ地方のガイドと比べて、旅先を決める") + '<div class="tv-gl tg-near">'
              + "".join(f'<a href="travel-{n["slug"]}.html" data-ga="guide_near"><span class="tv-glp" style="background-image:url({_gcover(n)})"></span><span class="tv-glt"><b>{E(n["name"])}</b><small>スポット{len(n["spots"])}・お店{len(n["shops"])}</small><small class="tv-glc">{E(_gcred(n))}</small></span></a>' for n in _near)
              + '</div><a class="tg-back" href="travel.html#guide" data-ga="guide_all">全国のガイドから選ぶ ›</a></section>'
              + _sh("MANNERS", "わんこと歩くときのマナー") + '<ul class="tg-m">' + "".join(f'<li>{E(m)}</li>' for m in _MANNERS) + '</ul>'
              '<p class="nayami-n">同伴の条件は2026年11月時点で当サイトが調べた内容です。ルール・営業時間は変わることがあり、天候や混雑でテラス席が使えない日もあります。'
              'お出かけの前に、各施設・お店の公式サイトや公式Instagramで最新の情報をご確認ください。写真はWikimedia Commonsの自由ライセンス画像（撮影者・ライセンスは各写真の下）と、各施設の公式Instagramの埋め込みです。</p></div>'
              f'<a class="tg-stk" id="tgstk" href="{_hq}" data-ga="guide_sticky" hidden>{E(g["pref"])}の泊まれる宿を探す ›</a>'
              '<script>(function(){try{localStorage.setItem("pn_gv",JSON.stringify(' + json.dumps({"s": g["slug"], "n": g["name"], "c": _gcover(g)}, ensure_ascii=False) + '))}catch(e){}'
              'var k=document.getElementById("tgstk"),c=document.querySelector(".tg-cta");function f(){var r=c.getBoundingClientRect();k.hidden=!(scrollY>420&&r.top>innerHeight)}addEventListener("scroll",f,{passive:true});f()})()</script>')
        open(os.path.join(out, f'travel-{g["slug"]}.html'), "w", encoding="utf-8").write(page(
            f'{g["name"]}のペットと行ける観光スポット・お店｜{TRAVEL_BRAND}', gb,
            f'{g["name"]}で愛犬と行ける観光スポット{len(g["spots"])}か所と、ペット同伴OKのお店{len(g["shops"])}軒。同伴の条件つき。', sample,
            ((TRAVEL_BRAND, "travel.html"), (g["name"], None)), canonical=(f'{origin}/travel-{g["slug"]}.html' if origin else ""),
            og_image=(f'{origin}/{_gcover(g)}' if origin else _gcover(g)), brand="travel"))
        files.append(f'travel-{g["slug"]}.html')
    kaigai_sec = (f'<section class="nayami"><h2 class="nayami-h">海外ブランド<a class="h2a" href="brands.html">すべて見る</a></h2><ul class="nayami-l">{"".join(kaigai)}</ul></section>' if kaigai else "")
    nayami_sec = (f'<section class="nayami"><h2 class="nayami-h">悩みから探す<a class="h2a" href="nayami.html">すべて見る</a></h2><ul class="nayami-l">{"".join(nayami)}</ul>'
                  '<p class="nayami-n">商品名に記載のあるフード（効果を示すものではありません）</p></section>' if nayami else "")
    mc = data.get("mall_counts") or {}
    tot = (mc.get("r") or 0) + (mc.get("y") or 0)
    mall_s = "楽天市場・Yahoo!ショッピング" if HAS_YAHOO else "楽天市場"
    man = f"{tot // 10000:,}万" if tot >= 10000 else ""
    mnote = "「%」は同じ内容量の他店（3店以上の中央値）との差の最大値。" + (f"「{man}件」は{mall_s}のペット用品ジャンルの出品数（同じ商品の別ショップを含む・取得時点）。" if man else "") + f"検索欄からは{mall_s}をその場で検索します（くわしい比較ページは{len(index_rows):,}商品）"
    _ds = drops(published)
    top = max((round(x["d"] / x["hi"] * 100) for x in _ds), default=0)
    # バナーは「みんなが買っている定番」で見せる（レビュー件数×お得率）。数字はその商品自身の%（別商品の%と写真を混ぜない）
    # 「これ欲しい」と思われやすい物を優先: フード・おやつ・おもちゃ・ベッド等。砂・シーツ・ティッシュ等の衛生用品は外す
    _plain = re.compile(r"砂|シーツ|ティッシュ|消臭|除菌|トイレ|おむつ|マナー|ペレット|ゴミ袋|うんち")
    _want = re.compile(r"フード|おやつ|ちゅ|ジャーキー|ガム|おもちゃ|ベッド|ハウス|タワー|キャリー|カート|首輪|ハーネス|服")
    def _bscore(x):
        t = x["g"].get("title", "") + " " + x["p"].get("name", "")
        w = 2.0 if _want.search(t) else 1.0
        return w * (x["d"] / x["hi"]) * math.log10(x["p"].get("reviewCount", 0) + 10) * max(x["p"].get("reviewAverage", 0), 3.5)
    _ok = [x for x in _ds if x["p"].get("image") and not _plain.search(x["g"].get("title", "") + " " + x["p"].get("name", ""))]
    _pop = ([x for x in _ok if x["p"].get("reviewCount", 0) >= 100 and x["p"].get("reviewAverage", 0) >= 4.2] or _ok
            or [x for x in _ds if x["p"].get("image")])
    _bx = max(_pop, key=_bscore) if _pop else None
    _dimg = img(_bx["p"].get("image"), 300) if _bx else ""
    _bpct = round(_bx["d"] / _bx["hi"] * 100) if _bx else top
    _ny = next((u for u in nayami_urls), "")
    _br = next((u for u in brand_urls), "")
    slides = []
    slides.append('<a class="bn bn-a" href="search.html" data-ga="bn_search"><span class="bn-ph" style="background-image:url(pets/hero.jpg)"></span>'
                  f'<span class="bn-t"><small>{mall_s}のペット用品</small><b>{man or "全商品"}</b>{"件から" if man else "から"}<br>安いお店を探す</span>'
                  f'<span class="bn-go">探してみる</span><span class="bn-n">件数は出品数（同じ商品の別ショップ含む）※</span></a>')
    if _ds:
        slides.append(f'<a class="bn bn-b" href="weekly.html" data-ga="bn_deals"><span class="bn-t"><small>{("レビュー" + format(_bx["p"].get("reviewCount", 0), ",") + "件の定番が") if _bx else "同じ内容量の他店と比べて"}</small>他店より<br><b class="cnt" data-to="{_bpct}">{_bpct}</b><i>%</i>安い</span>'
                      + (f'<img class="bn-im" src="{E(_dimg)}" alt="" width="150" height="150">' if _dimg else "")
                      + f'<span class="bn-go">ほかのお得も見る</span><span class="bn-n">※同じ内容量の他店中央値との差・取得時点・送料別</span></a>')
    if _ny:
        slides.append(f'<a class="bn bn-c" href="{_ny}" data-ga="bn_nayami"><span class="bn-ph" style="background-image:url(pets/cat.jpg)"></span>'
                      '<span class="bn-t"><small>毛玉・下部尿路・シニア・体重</small><b>悩み</b>から<br>フードを探す</span><span class="bn-go">悩みを選ぶ</span></a>')
    slides.append('<a class="bn bn-d" href="shindan.html" data-ga="bn_shindan"><span class="bn-ph" style="background-image:url(pets/dog.jpg)"></span>'
                  '<span class="bn-t"><small>いつもの商品を選ぶだけ</small>うちの子の消耗品<br><b>月いくら？</b></span><span class="bn-go">1分診断</span></a>')
    if _br:
        slides.append(f'<a class="bn bn-e" href="{_br}" data-ga="bn_brand"><span class="bn-ph" style="background-image:url(pets/hero2.jpg)"></span>'
                      '<span class="bn-t"><small>製造国を確かめた</small><b>海外ブランド</b><br>を国内ショップで比べる</span><span class="bn-go">ブランドを見る</span></a>')
    slides.append('<a class="bn bn-f" href="travel.html" data-ga="bn_travel"><span class="bn-ph" style="background-image:url(travel/car.jpg)"></span>'
                  '<span class="bn-t"><small>るるぶ・楽天トラベルから</small>うちの子と<br><b>泊まれる宿</b></span><span class="bn-go">宿を探す</span></a>')
    if _GUIDES:  # おでかけガイド（写真で旅先を選ぶ → 宿へ）
        _gc = next((_gcover(g) for g in _GUIDES if _gcover(g).startswith("travel/g/")), "travel/mountain.jpg")
        _gcc = next((_gcred(g) for g in _GUIDES if _gcover(g) == _gc), "")
        slides.append(f'<a class="bn bn-f" href="travel.html#guide" data-ga="bn_guide"><span class="bn-ph" style="background-image:url({_gc})"></span>'
                      f'<span class="bn-t"><small>{len(_GUIDES)}都道府県のおでかけガイド</small>愛犬と行ける<br><b>旅先を探す</b></span><span class="bn-go">ガイドを見る</span>' + (f'<span class="tg-hc">{E(_gcc)}</span>' if _gcc else '') + '</a>')
    hero = ('<form class="hq" action="search.html" role="search"><label class="sr" for="hq2">ペット用品を探す</label>'
            f'<input type="search" id="hq2" name="q" placeholder="{man + "件の" if man else ""}ペット用品から探す" autocomplete="off"><button type="submit" aria-label="探す">' + globals()["icon"]("search", 20) + '</button></form>'
            f'<div class="bns" id="bns">{"".join(slides)}</div><div class="bn-dots" aria-hidden="true">{"".join("<i></i>" for _ in slides)}</div>')
    quick = ('<ul class="qk">'
             '<li><a href="weekly.html"><span class="qk-i qk-1">' + globals()["icon"]("star-f", 22) + '</span>お得<br>ランキング</a></li>'
             + (f'<li><a href="{_ny}"><span class="qk-i qk-2">' + globals()["icon"]("cat", 22) + '</span>悩みから<br>探す</a></li>' if _ny else "")
             + (f'<li><a href="{_br}"><span class="qk-i qk-3">' + globals()["icon"]("paw", 22) + '</span>海外<br>ブランド</a></li>' if _br else "")
             + '<li><a href="shindan.html"><span class="qk-i qk-4">' + globals()["icon"]("bag", 22) + '</span>1分<br>診断</a></li>'
             + '<li><a href="travel.html"><span class="qk-i qk-5">' + globals()["icon"]("bed", 22) + '</span>泊まれる<br>宿</a></li></ul>')
    tiles = sorted(deal_tiles, key=lambda t: -t[0])[:10]
    tiles_sec = ('<section class="tl-sec"><h2 class="h2">カテゴリ別 お得</h2><p class="h2n">※同じ内容量の他店（3店以上の中央値）との差・取得時点・送料別</p><ul class="tls">'
                 + "".join(f'<li><a class="tl tl{k % 5}" href="{g["slug"]}.html" data-ga="tile"><span class="tl-p">{E(pn)}</span><span class="tl-c">{E(g["title"])}</span><span class="tl-v"><small>他店より最大</small><b>{v}</b>%安い商品あり※</span><span class="tl-n">お得な商品 {n}件 ›</span></a></li>'
                           for k, (v, g, n, pn) in enumerate(tiles)) + '</ul></section>') if tiles else ""
    deals = "".join(f'<li><a class="dc" href="item-{E(x["p"]["productId"])}.html" data-ga="deal_card"><span class="dc-p">'
                    + (f'<img src="{E(img(x["p"].get("image"), 200))}" alt="" width="120" height="120" loading="lazy">' if x["p"].get("image") else "")
                    + f'<span class="dc-b">{round(x["d"] / x["hi"] * 100)}%安い※</span></span><span class="dc-n">{E(show_name(x["p"]["name"]))}</span>'
                    f'<span class="dc-pr"><b>{x["now"]:,}</b>円</span><span class="dc-o">他店 {x["hi"]:,}円</span></a></li>' for x in _ds[:12])
    deal_sec = (f'<section class="dc-sec"><h2 class="h2">他店より安い商品<a class="h2a" href="weekly.html">すべて見る</a></h2><p class="h2n">※同じ内容量の他店中央値との差・取得時点・送料別</p><ul class="dcs">{deals}</ul></section>' if deals else "")
    # ガチャもバナーと同じ条件（11/21 Taku）: 衛生用品（砂・シーツ等）を外し、レビュー100件以上・★4.2以上の定番を優先、欲しい物は重み2倍
    # 療法食は獣医師の指示で与えるものなので、ガチャの当たりにはしない（11/23 Taku）。比較ページには載せる
    _ok = [x for x in _ok if not re.search(r"療法食|食事療法|消化器サポート|腎臓サポート|pHコントロール|ユリナリー|肝臓サポート|心臓サポート|皮膚サポート|満腹感サポート|減量サポート|退院サポート|[cdikjlrwz]/d\b|プリスクリプション|ベテリナリー|VETERINARY|PRESCRIPTION", x["g"].get("title", "") + " " + x["p"].get("name", ""), re.I)]
    _gpop = [x for x in _ok if x["p"].get("reviewCount", 0) >= 100 and x["p"].get("reviewAverage", 0) >= 4.2]
    _gs = sorted(_gpop if len(_gpop) >= 30 else _ok, key=lambda x: -_bscore(x))[:300]
    _gcond = "レビュー100件以上・★4.2以上の定番で" if len(_gpop) >= 30 else ""
    _a2p = {a: pid for pid, _n, _i, ans in PETS for a in ans}
    gdata = json.dumps([{"t": _a2p.get(x["g"].get("animal"), ""), "i": x["p"]["productId"], "n": show_name(x["p"]["name"])[:40], "m": img(x["p"].get("image"), 200) or "",
                         "p": x["now"], "h": x["hi"], "c": round(x["d"] / x["hi"] * 100)} for x in _gs], ensure_ascii=False).replace("</", "<\\/")
    _fm = re.search(r"(\d{1,2}):\d{2}", str(data.get("fetched_at", "")))
    gacha = ((f'<section class="gc" id="gacha"><div class="uk" id="uk" data-pets="{E(json.dumps(pet_names, ensure_ascii=False))}"></div><div class="gc-top"><img class="gc-mc" id="gmc" src="gacha/machine.webp" alt="" width="110" height="165">'
              f'<div class="gc-hd"><b>お得ガチャ</b><span class="gc-s">{_gcond}いま他店より安い商品から1回ひとつ（砂・シーツなどの日用品は除く）</span><span class="gc-s2">{_fm.group(0) + "取得の" if _fm else ""}お得 {len(_gs):,}件・何回でも無料</span>'
              '<span class="gc-new" id="gnew" hidden></span><span class="gc-dex" id="gdex"></span></div></div>'
              '<div class="gc-st" id="gst" hidden><img class="gc-bu" id="gbu" alt="" hidden><img class="gc-cap" id="gcap" src="gacha/capsule.webp" alt="" width="96" height="96"><a class="gc-card" id="gcard" hidden data-ga="gacha_card"></a></div>'
              '<button type="button" class="gc-btn" id="gbtn" data-ga="gacha_pull">ガチャを引く</button>'
              '<p class="gc-rr"><span>★★★ 25%以上安い</span><span>★★ 12%以上</span><span>★ それ以下</span></p><p class="h2n">※％は同じ内容量の他店（3店以上の中央値）との差・取得時点・送料別</p>'
              f'<script type="application/json" id="gdata">{gdata}</script></section>') if _gs else "")
    SEL = ' aria-selected="true"'
    pt = "".join(f'<li><button type="button" data-tab="{pid}"{SEL if k == 0 else ""}>{n}</button></li>' for k, (pid, n) in enumerate(pet_names))
    idx = ('<div class="home"><aside class="side" aria-label="カテゴリ"><p class="side-h">カテゴリ</p><ul>' + "".join(side) + '</ul>'
           '<p class="side-h">ほかの探し方</p><ul class="side-x"><li><a href="weekly.html">お得ランキング</a></li><li><a href="shindan.html">1分診断</a></li><li><a href="search.html">商品を探す</a></li></ul></aside>'
           '<div class="home-main">' + hero + quick + gacha + f'<ul class="circ">{"".join(circ)}</ul>'
           + '<div id="welcome"></div>' + tiles_sec + deal_sec + nayami_sec + kaigai_sec
           + f'<section class="cat-sec"><h2 class="h2">カテゴリから探す</h2><ul class="ptabs" role="tablist">{pt}</ul>{"".join(secs)}</section>'
           + (f'<section class="mk-sec"><h2 class="h2">メーカーから探す</h2><ul class="chips">{mk}</ul></section>' if mk else "")
           + f'<div class="note"><p>価格取得日時: {fetched}</p><p class="ptn">{mnote}</p></div></div></div>')
    _ds = drops(published)
    og_ok = og_card(os.path.join(out, "og-today.png"), f"{fdate[5:].replace('-', '/')} ペット用品のお得（楽天市場）",
                    f"お得 {today_drops(published)}件", (f"他店（中央値）より最大 −{_ds[0]['d']:,}円（取得時点・送料別）" if _ds else "楽天のショップの価格を比較（1日2回更新）")) if fdate[:2] == "20" else False
    open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(
        page(f"{SITE}｜楽天市場のペット用品を価格・ポイントで比較", idx,
             f"{mall_s}のペット用品を検索して、ショップごとの価格とポイントで比較（{fdate}取得）。猫砂・ドッグフード・小動物や爬虫類、小鳥・魚の餌まで。",
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
    pdir = os.path.join(ROOT, "assets", "pets")
    if os.path.isdir(pdir):  # 動物の写真（Pexels・商用可。出典は assets/pets/CREDITS.md）
        shutil.copytree(pdir, os.path.join(out, "pets"), dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.md"))
    tdir = os.path.join(ROOT, "assets", "travel")
    if os.path.isdir(tdir):  # 旅行ページの写真（Pexels・商用可。出典は assets/travel/CREDITS.md）
        shutil.copytree(tdir, os.path.join(out, "travel"), dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.md"))
    gdir = os.path.join(ROOT, "assets", "gacha")
    if os.path.isdir(gdir):  # お得ガチャの素材（ChatGPT 画像生成・2026-11-20 Taku承認）
        shutil.copytree(gdir, os.path.join(out, "gacha"), dirs_exist_ok=True)
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
        '<p class="count" id="count" aria-live="polite"></p><ul class="list gl" id="results"></ul>'
        '<section id="msec" hidden><h2 class="h2">楽天・Yahoo!の全商品から</h2><p class="meta" id="mcount" aria-live="polite" style="padding:0 16px"></p><ul class="list gl" id="mres"></ul>'
        '<p class="nayami-n">リンク先のモールの商品ページへ移動します。価格は検索した時点のもので、購入時にリンク先に表示されている価格が適用されます。</p></section>'
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
    open(os.path.join(out, "about.html"), "w", encoding="utf-8").write(page(
        f"このサイトについて｜{SITE}", '<h1 class="ttl">このサイトについて</h1><div class="note" style="font-size:13px">'
        '<h2 class="h2" id="notes">表示の意味（※）</h2><ul>'
        '<li>ポイント＝税抜価格の1%×ショップの倍率（目安。SPU・クーポンは含みません）</li>'
        '<li>実質＝価格−ポイント（支払額ではありません）</li>'
        '<li>単価＝商品名の内容量から計算</li>'
        '<li>「N店中の最安」＝当サイトが取得したショップの中での最安</li>'
        '<li>「他店」＝同じ内容量のショップ（3店以上）の価格の中央値</li>'
        '<li>価格は取得時点・税込。送料はショップにより別</li>'
        '<li id="card">楽天カード払い＝楽天市場で楽天カードで支払うと、カードのポイントが別に付く場合があります。付く量や上限は会員の条件や時期で変わるため、このサイトでは数値を出していません。条件は楽天カード公式サイトでご確認ください</li></ul>'
        '<h2 class="h2">運営</h2><p>合同会社にこのて。価格の取得とページ作成は AI〈一郎〉が自動で行っています。'
        '楽天アフィリエイトを利用した個人運営の比較サイトで、楽天グループ株式会社とは関係ありません。'
        'リンク先で購入されると、当サイトに紹介料が支払われます。</p>'
        + (YAHOO_NOTE if HAS_YAHOO else "")
        + ('<h2 class="h2">アクセス解析</h2><p>Google アナリティクスを使っています（Cookie を使い、個人を特定しない形で集めます）。'
           '<a href="privacy.html">詳しく</a></p>' if GA_ID else "")
        + '<p>お気に入り・買った日のメモは、お使いのブラウザの中だけに保存されます。</p></div>',
        "ペット用品 最安ナビの表示の意味・運営者", sample, (("このサイトについて", None),)))
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
(function(){var K="pn_trip";function ld(){try{return JSON.parse(localStorage.getItem(K)||"null")}catch(e){return null}}function sv(v){try{localStorage.setItem(K,JSON.stringify(v))}catch(e){}}
 document.addEventListener("click",function(e){var a=e.target.closest(".tv-l a,.tv-rt a,a.tv-rr,.tv-win a");if(!a)return;var ar=document.getElementById("tva");sv({a:ar?ar.value:"",t:Date.now()})},true);
 function show(from){var t=ld(),now=Date.now();if(!t||t.d||now-t.t<5000||now-t.t>14*864e5||(t.sn&&now<t.sn)||location.hash==="#prep"||document.getElementById("tvbk"))return;
  var s=document.createElement("div");s.id="tvbk";s.className="tvbk";s.setAttribute("role","dialog");s.setAttribute("aria-label","お泊まりの準備");
  s.innerHTML='<button type="button" class="tvbk-x" aria-label="閉じる">×</button><b>'+(t.a?esc(t.a)+"の":"")+'お宿、決まりましたか？</b><p>うちの子とのお泊まり。持ち物を最安でそろえましょう</p><a class="tvbk-go" href="travel.html#prep" data-ga="trip_return">持ち物チェックリストへ ›</a><button type="button" class="tvbk-n">まだ決まっていない</button>';
  document.body.appendChild(s);ga("trip_return_show",{from:from});
  s.addEventListener("click",function(e){if(e.target.closest(".tvbk-go")){t.d=1;t.ok=1}else if(e.target.closest(".tvbk-n")){t.sn=Date.now()+864e5}else if(e.target.closest(".tvbk-x")){t.d=1}else return;sv(t);s.remove()})}
 function bar(){var t=ld();if(!t||!t.ok||t.bx||Date.now()-t.t>30*864e5||/travel\.html/.test(location.pathname)||document.getElementById("tvbar"))return;var m={};try{m=JSON.parse(localStorage.getItem("pn_prep")||"{}")}catch(e){}
  var c=0;for(var k in m)if(m[k])c++;if(c>=12)return;var a=document.createElement("a");a.id="tvbar";a.className="tvbar";a.href="travel.html#prep";a.setAttribute("data-ga","trip_bar");
  a.innerHTML='<b>'+(t.a?esc(t.a)+"への":"")+'旅の準備</b><span class="tvbar-g"><i style="width:'+(100*c/12)+'%"></i></span><span>'+c+'/12 ›</span><button type="button" class="tvbar-x" aria-label="この表示を消す">×</button>';
  a.addEventListener("click",function(e){if(e.target.closest(".tvbar-x")){e.preventDefault();t.bx=1;sv(t);a.remove();ga("trip_bar_close",{})}});var h=document.querySelector("main")||document.body;h.insertBefore(a,h.firstChild)}
 document.addEventListener("visibilitychange",function(){if(document.visibilityState==="visible")show("back")});show("load");bar()})();
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
   li.querySelector(".pv").textContent=yen(li.dataset.fprice);li.querySelector(".shipv").innerHTML='<span class="ship free">送料無料</span>'+(li.dataset.fprice!==li.dataset.aprice?'<small class="alt-p">（送料別の最安 '+yen(li.dataset.aprice)+'円）</small>':'');li.querySelector(".ptv").textContent=li.dataset.fptt||(yen(li.dataset.fpt)+"ポイント（"+li.dataset.frate+"倍・目安）");var sc=li.querySelector(".scope");if(sc){sc.dataset.o=sc.dataset.o||sc.textContent;sc.textContent="送料無料のショップの中で最安"}li.querySelectorAll(".up,.save-s").forEach(function(z){z.style.display="none"})}
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
   document.getElementById("count").textContent=r.length?r.length+"件の商品":(document.getElementById("q").value.trim().length>=2?"このサイトの一覧にはありません。下に楽天・Yahoo!の検索結果を出します。":"見つかりませんでした。言葉を短くするか、条件を「すべて」に戻してください。");
   var p=new URLSearchParams();["q","pet","cat","maker","sort"].forEach(function(k){var v=f[k].value;if(v&&!(k==="sort"&&v==="pop"))p.set(k,v)});if(fo)p.set("free","1");
   history.replaceState(null,"",location.pathname+(p.toString()?"?"+p:""));paint();msearch(document.getElementById("q").value.trim());
   clearTimeout(run._t);run._t=setTimeout(function(){ga("search",{search_term:q,pet:pet,category:cat,maker:m,sort:s,results:r.length})},1500)}
  f.free.addEventListener("change",function(){ga("free_only",{on:f.free.checked})});f.addEventListener("input",run);f.addEventListener("change",run);f.addEventListener("submit",function(e){e.preventDefault();run()});run()})}
function mrow(x){return '<li><a class="row" href="'+esc(x.u)+'" target="_blank" rel="sponsored nofollow noopener" data-m="'+x.m+'"><span class="pic">'+(x.img?'<img src="'+esc(x.img)+'" alt="" width="96" height="96" loading="lazy">':'<span class="noimg"></span>')+'</span><span class="info">'+
    '<span class="nm"><span class="mall '+x.m+'">'+(x.m==="y"?"Yahoo!":"楽天")+'</span>'+esc(x.n)+'</span><span class="meta">'+esc(x.s)+'</span>'+
    '<span class="pr"><b>'+yen(x.p)+'</b>円 <span class="ship'+(x.sh?" free":" paid")+'">'+(x.sh?"送料無料":"送料別")+'</span></span>'+
    (x.pt>0?'<span class="pt">'+(x.m==="y"?'PayPayポイント '+yen(x.pt)+'pt':yen(x.pt)+'pt（'+x.r+'倍）')+'<span class="kome">※</span></span>':'')+'</span></a></li>'}
var MS={},MQ2=Promise.resolve();function ms(q,cb){if(MS[q])return MS[q].then(cb,function(){cb(null)});
 MS[q]=MQ2=MQ2.then(function(){return new Promise(function(ok){setTimeout(ok,1100)})}).then(function(){return fetch("/api/msearch?o=rel&q="+encodeURIComponent(q)).then(function(r){return r.json()})});MQ2=MQ2.catch(function(){});MS[q].then(cb,function(){cb(null)})}
function totTxt(t){var a=[];if(t&&t.r)a.push("楽天 "+yen(t.r)+"件");if(t&&t.y)a.push("Yahoo! "+yen(t.y)+"件");return a.join("・")}
(function(){var io="IntersectionObserver" in window?new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){io.unobserve(e.target);go(e.target)}})},{rootMargin:"400px"}):null;
 function go(s){var q=s.dataset.q,c=s.querySelector(".lv-c"),l=s.querySelector(".lv-l");ms(q,function(d){if(!d||!d.items){c.textContent="今は楽天・Yahoo!の検索ができません。時間をおいてお試しください。";return}
  var t=totTxt(d.tot);c.textContent=d.items.length?(t?t+"から ":"")+"関連の高い順に"+Math.min(d.items.length,24)+"件":"楽天・Yahoo!でも見つかりませんでした。";l.innerHTML=d.items.slice(0,24).map(mrow).join("");ga("live_search",{q:q,results:d.items.length})})}
 document.querySelectorAll(".lv[data-q]").forEach(function(s){io?io.observe(s):go(s)});
 document.querySelectorAll("[data-cnt-q]").forEach(function(e){ms(e.dataset.cntQ,function(d){var t=d&&d.tot?(d.tot.r||0)+(d.tot.y||0):0;e.textContent=t?yen(t)+"件":(d&&d.items?d.items.length+"件":"—")})});
 var f=document.getElementById("nqf");if(f)f.addEventListener("submit",function(){document.getElementById("nqq").value=document.getElementById("nqp").value+" "+document.getElementById("nqw").value.trim();ga("nayami_free",{})});
 var bq=document.getElementById("bq"),bh=document.getElementById("bhub");if(bq&&bh)bq.addEventListener("input",function(){var k=bq.value.trim().toLowerCase();bh.querySelectorAll("li").forEach(function(li){li.hidden=k&&li.dataset.n.toLowerCase().indexOf(k)<0})});})();
var MQ="";function msearch(q){var sec=document.getElementById("msec");if(!sec)return;clearTimeout(msearch._t);
 if(q.length<2){sec.hidden=true;MQ="";return}if(q===MQ)return;
 msearch._t=setTimeout(function(){MQ=q;var c=document.getElementById("mcount"),l=document.getElementById("mres");sec.hidden=false;c.textContent="楽天市場・Yahoo!ショッピングを検索しています…";l.innerHTML="";
  fetch("/api/msearch?q="+encodeURIComponent(q)).then(function(r){return r.json()}).then(function(d){if(q!==MQ)return;var it=d.items||[];
   c.textContent=it.length?"楽天・Yahoo!のペット用品 "+it.length+"件（安い順）":"楽天・Yahoo!でも見つかりませんでした。";ga("msearch",{search_term:q,results:it.length});
   l.innerHTML=it.slice(0,40).map(mrow).join("")})
  .catch(function(){if(q===MQ)c.textContent="今は楽天・Yahoo!の検索ができません。時間をおいてお試しください。"})},700)}
document.addEventListener("click",function(e){var a=e.target.closest("#mres a[data-m],.lv-l a[data-m]");if(a)ga("click_mall",{mall:a.dataset.m==="y"?"yahoo":"rakuten",from:"msearch"})});
(function(){var b=document.getElementById("bns");if(b){var ds=document.querySelectorAll(".bn-dots i"),n=b.children.length,cur=0,t;
 function on(){var w=b.children[0].offsetWidth+10;cur=Math.round(b.scrollLeft/w);ds.forEach(function(d,k){d.classList.toggle("on",k===cur)})}
 b.addEventListener("scroll",function(){clearTimeout(b._t);b._t=setTimeout(on,80)});on();
 function nx(){if(document.hidden)return;var w=b.children[0].offsetWidth+10;var k=(cur+1)%n;b.scrollTo({left:k*w,behavior:"smooth"})}
 t=setInterval(nx,4000);["touchstart","mouseenter"].forEach(function(e){b.addEventListener(e,function(){clearInterval(t)},{passive:true})})}
 var cs=document.querySelector(".cat-sec");if(cs){cs.classList.add("js");function tab(id){cs.querySelectorAll(".ptabs button").forEach(function(x){x.setAttribute("aria-selected",x.dataset.tab===id?"true":"false")});
  cs.querySelectorAll(".pet-sec").forEach(function(x){x.classList.toggle("on",x.dataset.pet===id)})}
  var mp=null;try{mp=JSON.parse(localStorage.getItem("pn_pet")||"null")}catch(e){}
  var f=(mp&&cs.querySelector('.ptabs button[data-tab="'+mp.t+'"]'))||cs.querySelector(".ptabs button");if(f)tab(f.dataset.tab);
  cs.addEventListener("click",function(e){var x=e.target.closest(".ptabs button");if(x)tab(x.dataset.tab)});
  document.querySelectorAll(".circ a[data-tab]").forEach(function(a){a.addEventListener("click",function(e){e.preventDefault();tab(a.dataset.tab);cs.scrollIntoView({behavior:"smooth"})})})}})();
(function(){var u=document.getElementById("uk");if(!u)return;var P=JSON.parse(u.dataset.pets||"[]");
 function ld(k,d){try{return JSON.parse(localStorage.getItem(k)||"null")||d}catch(e){return d}}function sv(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}
 function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
 var td=new Date().toISOString().slice(0,10),yd=new Date(Date.now()-864e5).toISOString().slice(0,10),v=ld("pn_visit",{d:"",s:0,x:0,g:0,gd:""});
 if(v.d!==td){v.s=v.d===yd?v.s+1:1;v.d=td;v.x+=2;sv("pn_visit",v)}
 window.pnXP=function(){if(v.gd!==td){v.gd=td;v.g=0}if(v.g<10){v.g++;v.x++;sv("pn_visit",v);draw()}};
 function lv(x){return Math.floor(Math.sqrt(x/3))+1}
 var TT=["はじめまして","なかよし","ともだち","あいぼう","家族","ずっと一緒"];
 function draw(){var p=ld("pn_pet",null);if(!p){u.innerHTML='<button type="button" class="uk-add" data-ga="uk_open">＋ うちの子を登録（お得をうちの子向けに）</button>';return}
  var L=lv(v.x),lo=3*(L-1)*(L-1),hi=3*L*L,pc=Math.round((v.x-lo)/(hi-lo)*100),nm=null;P.forEach(function(q){if(q[0]===p.t)nm=q[1]});
  u.innerHTML='<div class="uk-me"><span class="uk-n">'+esc(p.n||"うちの子")+'<small>'+esc(nm||"")+'</small></span><span class="uk-s">連続'+v.s+'日</span><span class="uk-l">Lv'+L+' '+TT[Math.min(L-1,TT.length-1)]+'</span><button type="button" class="uk-ed" aria-label="うちの子を変更">変更</button></div><div class="uk-b"><i style="width:'+pc+'%"></i></div>'}
 function form(){var p=ld("pn_pet",{t:"",n:""});u.innerHTML='<div class="uk-f"><div class="uk-c">'+P.map(function(q){return'<button type="button" data-t="'+esc(q[0])+'" aria-pressed="'+(q[0]===p.t)+'">'+esc(q[1])+'</button>'}).join("")+'</div><input id="ukn" maxlength="12" placeholder="名前（なくてもOK）" value="'+esc(p.n||"")+'"><button type="button" class="uk-ok" data-ga="uk_save">登録</button></div>'}
 u.addEventListener("click",function(e){var b=e.target.closest("button");if(!b)return;
  if(b.classList.contains("uk-add")||b.classList.contains("uk-ed"))form();
  else if(b.dataset.t){u.querySelectorAll(".uk-c button").forEach(function(x){x.setAttribute("aria-pressed",x===b)})}
  else if(b.classList.contains("uk-ok")){var s=u.querySelector('.uk-c [aria-pressed="true"]');if(!s)return;sv("pn_pet",{t:s.dataset.t,n:(document.getElementById("ukn").value||"").trim()});draw();
   var tb=document.querySelector('.ptabs button[data-tab="'+s.dataset.t+'"]');if(tb)tb.click();if(window.gtag)gtag("event","uk_register",{pet:s.dataset.t})}});
 draw()})();
(function(){var el=document.getElementById("gdata");if(!el)return;var G=JSON.parse(el.textContent),K="pn_gdex",S="pn_gseen";
 function ld(k){try{return JSON.parse(localStorage.getItem(k)||"[]")}catch(e){return[]}}function sv(k,v){try{localStorage.setItem(k,JSON.stringify(v.slice(-2000)))}catch(e){}}
 var seen=ld(S),fresh=G.filter(function(g){return seen.indexOf(g.i)<0}),nw=document.getElementById("gnew");
 if(seen.length&&fresh.length){nw.textContent="前回から新しいお得 "+fresh.length+"件";nw.hidden=false}
 sv(S,seen.concat(fresh.map(function(g){return g.i})));
 var dex=ld(K),dx=document.getElementById("gdex");function dd(){var n=G.filter(function(g){return dex.indexOf(g.i)>=0}).length;dx.textContent="ずかん "+n+"/"+G.length}dd();
 var cap=document.getElementById("gcap"),card=document.getElementById("gcard"),btn=document.getElementById("gbtn"),box=cap.parentNode,busy=0,mc=document.getElementById("gmc"),bu=document.getElementById("gbu");
 function rar(c){return c>=25?3:c>=12?2:1}
 function pick(){var pool=fresh.length?fresh:G,u=pool.filter(function(g){return dex.indexOf(g.i)<0});if(!u.length)u=pool;
  var mp=null;try{mp=JSON.parse(localStorage.getItem("pn_pet")||"null")}catch(e){}
  var w=u.map(function(g){return (1+rar(g.c))*(mp&&g.t===mp.t?3:1)}),t=w.reduce(function(a,b){return a+b},0),r=Math.random()*t;for(var k=0;k<u.length;k++){r-=w[k];if(r<=0)return u[k]}return u[0]}
 function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
 btn.addEventListener("click",function(){if(busy)return;busy=1;box.hidden=false;card.hidden=true;bu.hidden=true;cap.src="gacha/capsule.webp";cap.hidden=false;
  mc.classList.remove("go");cap.classList.remove("go");void cap.offsetWidth;mc.classList.add("go");cap.classList.add("go");
  setTimeout(function(){cap.src="gacha/capsule_open.webp"},750);
  setTimeout(function(){var g=pick(),r=rar(g.c);cap.hidden=true;bu.src="gacha/burst"+r+".webp";bu.hidden=false;card.className="gc-card r"+r;card.href="item-"+encodeURIComponent(g.i)+".html";
   card.innerHTML=(g.m?'<img src="'+esc(g.m)+'" alt="">':'')+'<span><span class="r">'+"★★★".slice(0,r)+' 他店より'+g.c+'%安い※</span><span class="nm" style="display:block">'+esc(g.n)+'</span><span class="pc">'+g.p.toLocaleString()+'円<small>他店 '+g.h.toLocaleString()+'円</small></span></span>';
   card.hidden=false;if(dex.indexOf(g.i)<0){dex.push(g.i);sv(K,dex);dd()}
   if(r>=2){for(var k=0;k<(r==3?28:14);k++){var f=document.createElement("i");f.className="gc-fx";f.style.background=["#facc15","#f472b6","#a78bfa","#34d399"][k%4];
    f.style.setProperty("--x",(Math.random()*240-120)+"px");f.style.setProperty("--y",(Math.random()*-160-20)+"px");box.appendChild(f);setTimeout(function(x){x.remove()}.bind(null,f),1100)}}
   btn.textContent="もう1回引く";busy=0;if(window.pnXP)pnXP();if(window.gtag)gtag("event","gacha_result",{rarity:r})},1100)});})();
(function(){var f=document.getElementById("tvf");if(!f)return;var a=document.getElementById("tva"),t=document.getElementById("tvt"),c=document.getElementById("tvc"),l=document.getElementById("tvl"),
 tag="",bud="",H=[],T=0,seq=0,ru=document.getElementById("tvru"),wl=document.getElementById("tvwl"),lg=document.getElementById("tvlg"),sp=document.getElementById("tvsp"),win=document.getElementById("tvwin"),rot=0,pick=[];
 try{var s0=localStorage.getItem("pn_tva");if(s0)a.value=s0}catch(e){}
 function card(h){return '<a href="'+esc(h.u)+'" target="_blank" rel="sponsored nofollow noopener" data-ga="hotel"><span class="tv-im" style="background-image:url(&quot;'+esc(h.img)+'&quot;)"></span><span class="tv-b"><span class="tv-n">'+esc(h.n)+'</span>'+
  (h.ra?'<span class="tv-s">★'+h.ra.toFixed(2)+'（'+yen(h.rc)+'件）</span>':'')+'<span class="tv-s">'+esc(h.st?h.st+"駅周辺":h.ad)+'</span>'+(h.min?'<span class="tv-p">1泊 <b>'+yen(h.min)+'</b>円〜<small>（1室・目安）</small></span>':'')+'<span class="tv-go">楽天トラベルで空室を見る ›</span></span></a>'}
 function inb(h){if(!bud)return true;var r=bud.split("-"),lo=+r[0]||0,hi=r[1]?+r[1]:1e9;return h.min>0&&h.min>=lo&&h.min<hi}
 function draw(tot){if(tot)T=tot;var xs=H.filter(inb);
  c.textContent=H.length?a.value+(tag?"・"+tag:"")+(bud?"・予算で絞り込み":"")+"："+xs.length+"件（楽天トラベル "+yen(T||H.length)+"件中）":"見つかりませんでした。条件を変えてお試しください。";
  l.innerHTML=xs.map(function(h){return "<li>"+card(h)+"</li>"}).join("");setup(xs)}
 function setup(xs){if(xs.length<2){ru.hidden=true;return}ru.hidden=false;win.hidden=true;
  pick=xs.slice().sort(function(){return Math.random()-.5}).slice(0,8);var n=pick.length,cols=["#1f2937","#8c6a3f","#374151","#a8885a","#4b5563","#6f5432","#111827","#c2a476"];
  wl.style.transition="none";rot=0;wl.style.transform="rotate(0deg)";void wl.offsetWidth;wl.style.transition="";
  wl.style.background="conic-gradient("+pick.map(function(_,k){return cols[k%8]+" "+(k*360/n)+"deg "+((k+1)*360/n)+"deg"}).join(",")+")";
  wl.innerHTML=pick.map(function(_,k){return '<i style="transform:rotate('+((k+.5)*360/n-90)+'deg)">'+(k+1)+'</i>'}).join("");
  lg.innerHTML=pick.map(function(h,k){return "<li>"+(k+1)+". "+esc(h.n)+"</li>"}).join("");document.getElementById("tvrn").textContent="候補 "+n+"件";sp.textContent="ルーレットを回す"}
 sp.addEventListener("click",function(){if(!pick.length)return;sp.disabled=true;win.hidden=true;lg.querySelectorAll("li").forEach(function(x){x.className=""});
  var n=pick.length,k=Math.floor(Math.random()*n),seg=360/n,tgt=360-((k+.5)*seg)+(Math.random()-.5)*seg*.6;rot=rot-(rot%360)+360*6+tgt;wl.style.transform="rotate("+rot+"deg)";ga("roulette_spin",{n:n});
  setTimeout(function(){sp.disabled=false;sp.textContent="もう一回まわす";lg.children[k].className="on";win.innerHTML='<p class="tv-wint">今回の旅の候補</p><p style="margin:0 0 6px;font-size:11px">空室・料金・ペットの条件はリンク先で確認してください</p>'+card(pick[k]);win.hidden=false;
   for(var j=0;j<24;j++){var fx=document.createElement("i");fx.className="gc-fx";fx.style.background=["#c2a476","#e7d7b8","#8c6a3f","#fff"][j%4];fx.style.setProperty("--x",(Math.random()*260-130)+"px");fx.style.setProperty("--y",(Math.random()*-180-20)+"px");ru.appendChild(fx);setTimeout(function(x){x.remove()}.bind(null,fx),1100)}
   ga("roulette_result",{})},4100)});
 function get(p){return fetch("/api/hotels?area="+encodeURIComponent(a.value)+"&q="+encodeURIComponent(tag)+"&page="+p).then(function(r){return r.json()})}
 function gd(){document.dispatchEvent(new CustomEvent("tvarea",{detail:a.value}))}
 function go(){gd();if(!a.value){c.textContent="行き先を選ぶと、ペットと泊まれる宿が出ます";l.innerHTML="";ru.hidden=true;return}try{localStorage.setItem("pn_tva",a.value)}catch(e){}
  document.querySelectorAll(".tv-pf button").forEach(function(x){x.setAttribute("aria-pressed",x.dataset.a===a.value?"true":"false")});
  var my=++seq;c.textContent=a.value+"で探しています…";l.innerHTML="";ru.hidden=true;H=[];
  get(1).then(function(d){if(my!==seq)return;if(d.err&&!d.hotels.length){tvfail(d.rt);return}
   H=d.hotels;draw(d.tot);ga("hotel_search",{area:a.value,q:tag,results:H.length});
   if(d.pages>1)setTimeout(function(){if(my!==seq)return;get(2).then(function(e){if(my!==seq||!e.hotels)return;var seen={};H.forEach(function(h){seen[h.u]=1});e.hotels.forEach(function(h){if(!seen[h.u])H.push(h)});draw(d.tot)}).catch(function(){})},1200)
  }).catch(function(){tvfail()})}
  function tvfail(rt){if(rt){c.textContent="楽天トラベルで探せます";l.innerHTML='<li class="tv-rt"><a href="'+esc(rt)+'" target="_blank" rel="sponsored nofollow noopener" data-ga="hotel_rt"><b>'+esc(a.value+(tag?"・"+tag:""))+'</b>のペットと泊まれる宿を<br>楽天トラベルで見る（外部サイト）›</a><small>検索条件：「'+esc(["ペット",a.value,tag].filter(Boolean).join(" "))+'」。楽天トラベルの検索結果に移動します。空室・料金・ペットの条件はリンク先で確認してください。予約できたら、下の持ち物チェックリストへ</small></li>';ga("hotel_rt_show",{area:a.value,q:tag});return}
   c.innerHTML="今は楽天トラベルの宿を出せません。<a href=\"#\" id=\"tvgo\">るるぶトラベルのペット特集</a>からお探しいただけます。";var g=document.getElementById("tvgo");if(g)g.onclick=function(e){e.preventDefault();var r=document.querySelector(".tv-rr");if(r)r.scrollIntoView({behavior:"smooth",block:"center"})}}
 f.addEventListener("submit",function(e){e.preventDefault();go();setTimeout(seen,50)});a.addEventListener("change",go);
 t.addEventListener("click",function(e){var b=e.target.closest("button");if(!b)return;tag=tag===b.dataset.q?"":b.dataset.q;t.querySelectorAll("button").forEach(function(x){x.setAttribute("aria-pressed",x.dataset.q===tag?"true":"false")});var tn=document.querySelector(".tv-tn");if(a.value)go();else if(tn&&tag){tn.textContent="「"+tag+"」で絞ります。行き先を選んで「宿を探す」を押してください";a.focus()}});
 document.addEventListener("click",function(e){var x=e.target.closest(".tv-l a,.tv-win a");if(x)ga("hotel_click",{area:a.value,from:x.closest(".tv-win")?"roulette":"list"})});
 var th=document.querySelector(".tv-th"),pp=document.querySelector(".tv-pop"),hh=document.getElementById("tvh"),rg=document.getElementById("tvrg"),bd=document.getElementById("tvbd");
 function seen(){if(hh)hh.scrollIntoView({behavior:"smooth",block:"start"})}
 if(rg)rg.addEventListener("click",function(e){var b=e.target.closest("button");if(!b)return;rg.querySelectorAll("button").forEach(function(x){x.setAttribute("aria-selected",x===b?"true":"false")});
  document.querySelectorAll(".tv-pf").forEach(function(x){x.hidden=x.dataset.r!==b.dataset.r})});
 document.addEventListener("click",function(e){var b=e.target.closest(".tv-pf button,.tv-glb");if(!b)return;a.value=b.dataset.a;ga("travel_pref",{a:b.dataset.a});go();seen()});
 if(bd)bd.addEventListener("click",function(e){var b=e.target.closest("button");if(!b)return;bud=b.dataset.b;bd.querySelectorAll("button").forEach(function(x){x.setAttribute("aria-pressed",x===b?"true":"false")});ga("travel_budget",{b:bud});if(H.length)draw()});
 if(th)th.addEventListener("click",function(e){var b=e.target.closest("button");if(!b)return;tag=b.dataset.q;th.querySelectorAll("button").forEach(function(x){x.setAttribute("aria-pressed",x===b?"true":"false")});
  t.querySelectorAll("button").forEach(function(x){x.setAttribute("aria-pressed",x.dataset.q===tag?"true":"false")});ga("travel_theme",{q:tag});if(!a.value){c.textContent="行き先（エリア）を選んでください";document.querySelector(".tv-ar").scrollIntoView({behavior:"smooth"});return}go();seen()});
 if(pp)pp.addEventListener("click",function(e){var b=e.target.closest("button");if(!b)return;a.value=b.dataset.a;ga("travel_popular",{a:b.dataset.a});go();seen()});
 try{var pa=new URLSearchParams(location.search).get("area");if(pa&&[].some.call(a.options,function(x){return x.value===pa})){a.value=pa;setTimeout(function(){var h=document.getElementById("tvh");if(h)h.scrollIntoView({behavior:"smooth"})},300)}}catch(e){}
 if(a.value)go()})();
(function(){var sel=document.getElementById("tvsel");if(!sel)return;var tabs=document.getElementById("tvtabs"),gp=document.getElementById("tvgp"),gi=document.getElementById("tvgi"),ch=document.getElementById("tvchips"),gl=document.getElementById("tvgl"),
 hx=["tvc","tvru","tvl"].map(function(i){return document.getElementById(i)}).concat([document.querySelector(".tv-rr")]),D=null,T="h",W="";
 function li(r,k){var ph=r.ph,top="",lk="";
  if(ph)top='<figure class="tg-f"><img src="travel/g/'+esc(ph.f)+'" alt="'+esc(r.n)+'" loading="lazy" decoding="async"><figcaption><b class="tg-fp">'+esc(r.n)+'（'+esc(r.w)+'）</b>'+(ph.x?'写真：投稿者提供（掲載許可済み）':'写真：<a href="'+esc(ph.p)+'" target="_blank" rel="noopener nofollow">'+esc(ph.a)+'（Wikimedia Commons）</a>／'+(ph.u?'<a href="'+esc(ph.u)+'" target="_blank" rel="noopener nofollow license">'+esc(ph.l)+'</a>':esc(ph.l))+'・縮小して掲載')+'</figcaption></figure>';
  if(r.s)lk+='<a href="'+esc(r.s)+'" target="_blank" rel="noopener nofollow" data-ga="guide_official">公式サイト ›</a>';
  if(r.i)lk+='<a href="https://www.instagram.com/'+esc(r.i)+'/" target="_blank" rel="noopener nofollow" data-ga="guide_ig">公式Instagram ›</a>';
  return '<li class="tg-i'+(top?' tg-hp':'')+'">'+top+'<div class="tg-b"><span class="tg-k">'+k+'</span><h3 class="tg-n">'+esc(r.n)+'</h3><p class="tg-w">'+esc(r.w)+'</p><p class="tg-d">'+esc(r.d)+'</p><p class="tg-c"><b>同伴</b>'+esc(r.c)+'</p>'+(lk?'<p class="tg-o">'+lk+'</p>':'')+'</div></li>'}
 function draw(){var on=T!=="h";hx.forEach(function(x){if(x)x.style.display=on?"none":""});gp.hidden=!on;tabs.querySelectorAll("button").forEach(function(b){b.setAttribute("aria-selected",b.dataset.t===T?"true":"false")});
  if(!on||!D)return;var rows=T==="s"?D.s:D.d,cnt={};rows.forEach(function(r){cnt[r.w]=(cnt[r.w]||0)+1});
  var ws=Object.keys(cnt).sort(function(x,y){return cnt[y]-cnt[x]}).slice(0,8);if(W&&!cnt[W])W="";
  ch.innerHTML=ws.length>1?'<span>エリアでしぼる</span><button type="button" data-w="" aria-pressed="'+(!W)+'">すべて</button>'+ws.map(function(w){return '<button type="button" data-w="'+esc(w)+'" aria-pressed="'+(W===w)+'">'+esc(w)+'<small>'+cnt[w]+'</small></button>'}).join(""):"";
  gi.innerHTML=rows.filter(function(r){return !W||r.w===W}).map(function(r){return li(r,T==="s"?"スポット":"お店")}).join("")}
 tabs.addEventListener("click",function(e){var b=e.target.closest("button");if(!b)return;T=b.dataset.t;W="";draw();if(window.ga)ga("shiori_tab",{t:T})});
 ch.addEventListener("click",function(e){var b=e.target.closest("button");if(!b)return;W=b.dataset.w;draw()});
 document.addEventListener("tvarea",function(e){var t=window.TG&&window.TG[e.detail];D=null;T="h";W="";if(!e.detail){sel.hidden=true;draw();return}sel.hidden=false;
  document.getElementById("tvns").textContent=t?" "+t[3]:" 0";document.getElementById("tvnd").textContent=t?" "+t[4]:" 0";gl.hidden=!t;if(t)gl.href="travel-"+t[0]+".html";draw();
  if(t)fetch("travel/gd/"+t[0]+".json").then(function(r){return r.json()}).then(function(d){if(window.TG[a2()]===t){D=d;draw()}}).catch(function(){})});
 function a2(){var x=document.getElementById("tva");return x?x.value:""}})();
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
 x.fillStyle="#fffaf6";x.fillRect(0,0,1200,630);x.fillStyle="#ea580c";x.fillRect(0,0,1200,14);
 x.fillStyle="#1f2328";x.font="700 44px "+F2;x.fillText("前に見た・お気に入りの商品が他店より",70,110);
 x.fillStyle="#c2255c";x.font="800 150px "+F2;var s=yen(r.t);x.fillText(s,70,300);var w=x.measureText(s).width;x.font="700 64px "+F2;x.fillText("円 安い",90+w,300);
 x.fillStyle="#1f2328";x.font="700 36px "+F2;x.fillText(r.n+"件の商品の合計",70,370);
 x.fillStyle="#6b7280";x.font="400 24px "+F2;x.fillText("同じ内容量の他店（中央値）と最安（税込・送料別）の差の合計",70,440);x.fillText("目安・PR（アフィリエイト）",70,480);
 x.fillStyle="#ea580c";x.fillRect(0,540,1200,90);x.fillStyle="#fff";x.font="700 36px "+F2;x.fillText("ペット用品 最安ナビ　楽天のショップを比較",70,598);
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
 var QS=[0.5,1,2,3,4,6],openC="",mk="",kw="";
 var sheet=document.createElement("div");sheet.className="sd-sh";sheet.hidden=true;sheet.innerHTML='<div class="sd-sh-bg"></div><div class="sd-sh-p" role="dialog" aria-modal="true"><div class="sd-sh-h"><b id="sd-sh-t"></b><button type="button" class="sd-x" aria-label="閉じる">×</button></div><input type="search" id="sd-kw" placeholder="商品名・メーカーで絞り込む" enterkeyhint="search"><div class="sd-mk" id="sd-mk"></div><ul class="sd-cand" id="sd-cand"></ul><button type="button" class="sd-done">決定</button></div>';
 document.body.appendChild(sheet);var bar=document.createElement("div");bar.className="sd-bar";bar.hidden=true;document.body.appendChild(bar);
 function grp(){var g={};d.items.forEach(function(x){if(x.pet.indexOf(pet)>-1)(g[x.c]=g[x.c]||{t:x.ct,xs:[]}).xs.push(x)});Object.keys(g).forEach(function(c){g[c].xs.sort(function(a,b){return b.rv-a.rv})});return g}
 function cut(n,k){return n.length>k?n.slice(0,k-1)+"…":n}
 function draw(){pets.querySelectorAll("button").forEach(function(b){b.setAttribute("aria-pressed",b.dataset.pet===pet?"true":"false")});
  if(!pet){list.innerHTML="";res.hidden=true;bar.hidden=true;return}var g=grp();
  list.innerHTML='<p class="sd-step">いつも買っている商品を追加してください（1つのカテゴリに何品でも追加できます）</p>'+Object.keys(g).map(function(c){var G=g[c],ch=G.xs.filter(function(x){return sel[x.id]});
   return '<div class="sd-cat"><div class="sd-ch"><span class="sd-nm">'+esc(G.t)+(ch.length?'<em>'+ch.length+'品</em>':'')+'</span><button type="button" class="sd-add" data-c="'+esc(c)+'">＋ 追加</button></div>'+
    ch.map(function(x){return '<div class="sd-it">'+(x.img?'<img src="'+esc(x.img)+'" alt="" loading="lazy">':'<span class="sd-ni"></span>')+'<span class="sd-in"><span class="sd-n">'+esc(cut(x.n,40))+'</span><span class="sd-p">'+yen(x.p)+'円</span></span>'+
     '<span class="sd-st" data-id="'+esc(x.id)+'"><button type="button" data-d="-1" aria-label="減らす">−</button><b>月'+sel[x.id]+'</b><button type="button" data-d="1" aria-label="増やす">＋</button></span><button type="button" class="sd-rm" data-id="'+esc(x.id)+'" aria-label="外す">×</button></div>'}).join("")+'</div>'}).join("");
  calc(false)}
 function cand(){var G=grp()[openC];if(!G)return;var ms={};G.xs.forEach(function(x){if(x.m)ms[x.m]=(ms[x.m]||0)+1});
  var top=Object.keys(ms).sort(function(a,b){return ms[b]-ms[a]}).slice(0,12);
  document.getElementById("sd-mk").innerHTML=['<button type="button" data-m=""'+(mk?"":' aria-pressed="true"')+'>すべて</button>'].concat(top.map(function(m){return '<button type="button" data-m="'+esc(m)+'"'+(m===mk?' aria-pressed="true"':'')+'>'+esc(cut(m,10))+'<small>'+ms[m]+'</small></button>'})).join("");
  var k=kw.toLowerCase(),xs=G.xs.filter(function(x){return(!mk||x.m===mk)&&(!k||(x.n+" "+x.m).toLowerCase().indexOf(k)>-1)});
  document.getElementById("sd-cand").innerHTML=xs.length?xs.slice(0,60).map(function(x){return '<li><button type="button" data-id="'+esc(x.id)+'" aria-pressed="'+(sel[x.id]?"true":"false")+'">'+(x.img?'<img src="'+esc(x.img)+'" alt="" loading="lazy">':'<span class="sd-ni"></span>')+'<span class="sd-in"><span class="sd-n">'+esc(cut(x.n,48))+'</span><span class="sd-p">'+yen(x.p)+'円<small>'+esc(x.m||"")+'</small></span></span><i class="sd-ck"></i></button></li>'}).join(""):'<li class="sd-0">見つかりませんでした</li>'}
 function openSh(c){openC=c;mk="";kw="";document.getElementById("sd-kw").value="";document.getElementById("sd-sh-t").textContent=grp()[c].t;cand();sheet.hidden=false;document.documentElement.style.overflow="hidden";ga("shindan_open",{c:c})}
 function closeSh(){sheet.hidden=true;document.documentElement.style.overflow="";draw();calc(true)}
 sheet.addEventListener("click",function(e){var t=e.target;if(t.classList.contains("sd-sh-bg")||t.closest(".sd-x")||t.closest(".sd-done"))return closeSh();
  var m=t.closest("[data-m]");if(m){mk=m.dataset.m;cand();return}
  var b=t.closest("#sd-cand button");if(b){var id=b.dataset.id;if(sel[id])delete sel[id];else sel[id]=1;b.setAttribute("aria-pressed",sel[id]?"true":"false")}});
 document.getElementById("sd-kw").addEventListener("input",function(e){kw=e.target.value.trim();cand()});
 list.addEventListener("click",function(e){var t=e.target,a=t.closest(".sd-add");if(a)return openSh(a.dataset.c);
  var r=t.closest(".sd-rm");if(r){delete sel[r.dataset.id];draw();calc(true);return}
  var st=t.closest(".sd-st button");if(st){var id=st.parentNode.dataset.id,i=QS.indexOf(sel[id]);if(i<0)i=1;i=Math.max(0,Math.min(QS.length-1,i+(+st.dataset.d)));sel[id]=QS[i];draw();calc(true)}});
 bar.addEventListener("click",function(){res.scrollIntoView({behavior:"smooth"})});
 function calc(user){
  var ids=Object.keys(sel),p=new URLSearchParams();if(pet)p.set("pet",pet);if(ids.length)p.set("i",ids.map(function(id){return id+":"+sel[id]}).join(","));
  history.replaceState(null,"",location.pathname+(p.toString()?"?"+p:""));
  if(!ids.length){res.hidden=true;bar.hidden=true;return}
  var m=0,sv=0,pt=0;ids.forEach(function(id){var x=by[id],q=sel[id];m+=x.p*q;pt+=x.pt*q;if(x.md>x.p)sv+=(x.md-x.p)*q});
  m=Math.round(m);sv=Math.round(sv);pt=Math.round(pt);var R={m:m,y:m*12,sv:sv*12,pt:pt*12,n:ids.length,pet:PETN[pet]||""};
  res.hidden=false;res.innerHTML='<p class="sd-k">うちの'+esc(R.pet)+'の消耗品（'+R.n+'品目）</p><p class="sd-m">月 <b class="cnt" data-to="'+R.m+'">0</b>円</p><p class="sd-y">年間 約'+yen(R.y)+'円</p>'+
   (R.sv>0?'<p class="sd-sv">取得したショップの中央値で買う場合より<br><b class="cnt" data-to="'+R.sv+'">0</b>円/年 安い</p><p class="ptn sd-basis">差額＝同じ内容量の通常品が3店以上ある商品だけ、その価格の中央値と最安の差×月の数×12。送料・クーポンは含みません</p>':'<p class="sd-sv0">選んだ商品は、ショップ間の価格差が小さいか、比べられるショップが3店未満でした</p>')+
   '<p class="sd-pt">ポイント（目安）は年 約'+yen(R.pt)+'ポイント</p><canvas id="sd-card" width="1200" height="630" hidden></canvas>'+
   '<div class="sd-share"><button type="button" class="btn-main" id="sd-sh">結果を画像でシェア</button><a class="btn-sub" id="sd-x" target="_blank" rel="noopener">X にポスト</a><a class="btn-sub" id="sd-line" target="_blank" rel="noopener">LINE で送る</a><a class="btn-sub" id="sd-dl" download="petsite-shindan.png">画像を保存</a></div>'+
   '<p class="ptn">価格は '+esc(d.fetched_at)+' 時点の楽天市場の取得結果です。送料は含みません</p>';
  countUp(res);card(R);bar.hidden=false;bar.innerHTML='<span>'+R.n+'品 月<b>'+yen(R.m)+'</b>円'+(R.sv>0?'<em>年'+yen(R.sv)+'円の差</em>':'')+'</span><span class="sd-bar-go">結果を見る</span>';
  var url=location.href,txt="うちの"+R.pet+"の消耗品、月"+yen(R.m)+"円だった"+(R.sv>0?"（ショップ選びで年"+yen(R.sv)+"円の差）":"")+"｜あなたの子はいくら？";
  document.getElementById("sd-x").href="https://twitter.com/intent/tweet?text="+encodeURIComponent(txt)+"&url="+encodeURIComponent(url);
  document.getElementById("sd-line").href="https://line.me/R/msg/text/?"+encodeURIComponent(txt+" "+url);
  ["sd-x","sd-line","sd-dl"].forEach(function(id){document.getElementById(id).onclick=function(){ga("shindan_share",{method:id.slice(3),monthly:R.m})}});
  document.getElementById("sd-sh").onclick=function(){var c=document.getElementById("sd-card");ga("shindan_share",{method:"native",monthly:R.m});
   c.toBlob(function(b){var f=new File([b],"petsite-shindan.png",{type:"image/png"});
    if(navigator.canShare&&navigator.canShare({files:[f]}))navigator.share({files:[f],text:txt,url:url}).catch(function(){});
    else{var a=document.getElementById("sd-dl");a.click();toast("画像を保存しました。X や LINE に貼ってシェアできます")}})};
  if(user)ga("shindan_result",{pet:pet,items:R.n,monthly:R.m,yearly_diff:R.sv})}
 if(pet)draw()})}
function countUp(el){el.querySelectorAll(".cnt").forEach(function(c){var to=+c.dataset.to,t0=null,dur=700;
 if(window.matchMedia&&matchMedia("(prefers-reduced-motion: reduce)").matches){c.textContent=yen(to);return}
 function step(t){if(!t0)t0=t;var k=Math.min((t-t0)/dur,1);c.textContent=yen(Math.round(to*(1-Math.pow(1-k,3))));if(k<1)requestAnimationFrame(step)}requestAnimationFrame(step)})}
function card(R){var c=document.getElementById("sd-card");if(!c||!c.getContext)return;var x=c.getContext("2d"),F='"Hiragino Sans","Noto Sans JP","Yu Gothic",sans-serif';
 x.fillStyle="#fffaf6";x.fillRect(0,0,1200,630);x.fillStyle="#ea580c";x.fillRect(0,0,1200,14);
 x.fillStyle="#1f2328";x.font="700 44px "+F;x.fillText("うちの"+R.pet+"の消耗品、月いくら？",70,110);
 x.fillStyle="#6b7280";x.font="400 30px "+F;x.fillText(R.n+"品目・楽天市場の最安価格で計算",70,160);
 x.fillStyle="#1f2328";x.font="700 64px "+F;x.fillText("月",70,300);x.font="800 150px "+F;var s=yen(R.m);x.fillText(s,150,310);var w=x.measureText(s).width;x.font="700 64px "+F;x.fillText("円",160+w,300);
 if(R.sv>0){x.fillStyle="#c2255c";x.font="800 54px "+F;x.fillText("ショップ選びで 年"+yen(R.sv)+"円 の差",70,410)}
 x.fillStyle="#6b7280";x.font="400 26px "+F;x.fillText("取得したショップの中央値と比べた目安（送料別・"+String(DATA.fetched_at).slice(0,10)+"時点）",70,465);
 x.fillStyle="#ea580c";x.fillRect(0,540,1200,90);x.fillStyle="#fff";x.font="700 36px "+F;x.fillText("ペット用品 最安ナビ　あなたの子はいくら？ 1分診断",70,598);
 try{document.getElementById("sd-dl").href=c.toDataURL("image/png")}catch(e){}}
var F2='"Hiragino Sans","Noto Sans JP","Yu Gothic",sans-serif';
function wrap(x,t,w,maxL){var out=[],l="";for(var i=0;i<t.length;i++){var c=l+t[i];if(x.measureText(c).width>w){out.push(l);l=t[i];if(out.length===maxL-1){var r=t.slice(i);while(x.measureText(r+"…").width>w&&r.length)r=r.slice(0,-1);out.push(r+(r.length<t.length-i?"…":""));return out}}else l=c}if(l)out.push(l);return out}
function itemCard(b){var c=document.createElement("canvas");c.width=1200;c.height=630;if(!c.getContext)return null;var x=c.getContext("2d"),d=b.dataset;
 x.fillStyle="#fffaf6";x.fillRect(0,0,1200,630);x.fillStyle="#ea580c";x.fillRect(0,0,1200,14);
 x.fillStyle="#1f2328";x.font="700 44px "+F2;wrap(x,d.cn,1060,2).forEach(function(l,i){x.fillText(l,70,100+i*60)});
 x.fillStyle="#6b7280";x.font="400 30px "+F2;x.fillText(d.ck,70,260);
 if(d.cv){x.fillStyle="#c2255c";x.font="800 110px "+F2;x.fillText(d.cv,70,380)}
 x.fillStyle="#1f2328";x.font="700 40px "+F2;x.fillText("取得した最安 "+yen(+d.cp)+"円（送料別の場合あり）",70,d.cv?460:380);
 x.fillStyle="#6b7280";x.font="400 24px "+F2;x.fillText(d.cd+" 取得時点の目安・PR（アフィリエイト）",70,505);
 x.fillStyle="#ea580c";x.fillRect(0,540,1200,90);x.fillStyle="#fff";x.font="700 36px "+F2;x.fillText("ペット用品 最安ナビ　楽天のショップを比較",70,598);return c}
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
 x.fillStyle="#fffaf6";x.fillRect(0,0,1200,630);x.fillStyle="#ea580c";x.fillRect(0,0,1200,14);
 x.fillStyle="#1f2328";x.font="700 44px "+F2;x.fillText("ペット用品、ショップ選びで",70,110);
 x.fillStyle="#c2255c";x.font="800 150px "+F2;var s=yen(S.t);x.fillText(s,70,300);var w=x.measureText(s).width;x.font="700 64px "+F2;x.fillText("円 節約",90+w,300);
 x.fillStyle="#6b7280";x.font="400 28px "+F2;x.fillText(S.n+"回の購入記録・取得したショップの中央値との差の合計（目安）",70,380);
 x.fillStyle="#ea580c";x.fillRect(0,540,1200,90);x.fillStyle="#fff";x.font="700 36px "+F2;x.fillText("ペット用品 最安ナビ　楽天のショップを比較",70,598);
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


CSS = """:root{--txt:#1f2328;--sub:#6b7280;--line:#e6e8eb;--bg:#f6f7f5;--price:#e11d48;--brand:#ea580c;--brand-d:#c2410c;--brand-bg:#fff1e6;--warn-bg:#fff7e6;--win:#e11d48;--win-bg:#fff1f2;--warn:#8a5a00;--link:#0b5db3;--r:8px}
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
.mk{width:32px;height:32px;border-radius:10px;background:linear-gradient(135deg,#f59e0b,#ea580c);color:#fff;display:inline-grid;place-items:center}
.hd-note{font-size:11px;color:var(--sub);line-height:1.4;margin-left:auto;text-align:right}
.hd-fav{font-size:13px;color:var(--txt);white-space:nowrap;display:inline-flex;align-items:center;gap:4px;border:1px solid var(--line);border-radius:var(--r);padding:0 10px;height:36px;background:#fff}
.hd-search{max-width:960px;margin:0 auto;padding:0 16px 12px;display:flex}
.hd-search input{flex:1;min-width:0;font:inherit;font-size:16px;padding:0 16px;height:44px;border:2px solid var(--brand);border-right:0;border-radius:999px 0 0 999px;background:#fff}
.hd-search button{font:inherit;font-size:14px;font-weight:700;background:var(--brand);color:#fff;border:0;border-radius:0 999px 999px 0;padding:0 18px;height:44px;cursor:pointer;display:inline-flex;align-items:center;gap:6px}
.hd-search button:hover{background:var(--brand-d)}
main{max-width:960px;margin:0 auto;padding:0 0 24px}
.bc{font-size:12px;color:var(--sub);padding:10px 16px 0}.bc a{color:var(--link)}
.lead{font-size:13px;color:var(--sub);margin:12px 16px;line-height:1.7}
.ttl{font-size:21px;line-height:1.4;margin:8px 16px 0;font-weight:800;padding-left:12px;border-left:5px solid var(--brand)}
.h2{font-size:16px;font-weight:800;margin:0;padding:14px 16px 8px;display:flex;align-items:center;gap:8px;color:var(--txt)}.h2 .ic{color:var(--brand)}
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
.ship.free{color:var(--brand-d);border-color:#fdba74;background:var(--brand-bg)}.ship.paid{color:var(--warn);border-color:#ecd7a8;background:var(--warn-bg)}
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
.sd-cat{background:#fff;border:1px solid var(--line);border-radius:14px;padding:10px 12px;margin:0 0 8px}
.sd-ch{display:flex;align-items:center;gap:8px}.sd-nm{flex:1;min-width:0;font-size:14px;font-weight:800}.sd-nm em{font-style:normal;margin-left:6px;background:var(--brand);color:#fff;font-size:11px;border-radius:999px;padding:1px 8px}
.sd-add{flex:none;height:36px;padding:0 14px;border:0;border-radius:999px;background:linear-gradient(#fb923c,#ea580c);color:#fff;font-weight:800;font-size:13px;cursor:pointer}
.sd-it{display:flex;align-items:center;gap:8px;margin-top:8px;padding-top:8px;border-top:1px dashed var(--line)}
.sd-it img,.sd-ni,.sd-cand img{width:48px;height:48px;object-fit:contain;flex:none;border-radius:8px;background:#fff}.sd-ni{background:#f0f1f2}
.sd-in{flex:1;min-width:0;display:flex;flex-direction:column}.sd-n{font-size:12px;line-height:1.35;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.sd-p{font-size:13px;font-weight:800;color:var(--price)}.sd-p small{margin-left:6px;font-weight:400;color:var(--sub);font-size:11px}
.sd-st{flex:none;display:flex;align-items:center;border:1px solid var(--line);border-radius:999px;overflow:hidden}.sd-st button{width:32px;height:32px;border:0;background:#fff;font-size:16px;cursor:pointer}.sd-st b{font-size:12px;min-width:40px;text-align:center}
.sd-rm{flex:none;width:28px;height:28px;border:0;background:none;color:var(--sub);font-size:18px;cursor:pointer}
.sd-sh{position:fixed;inset:0;z-index:50}.sd-sh[hidden]{display:none}.sd-sh-bg{position:absolute;inset:0;background:rgba(0,0,0,.45)}
.sd-sh-p{position:absolute;left:0;right:0;bottom:0;max-height:88vh;display:flex;flex-direction:column;background:var(--bg);border-radius:18px 18px 0 0;padding:12px 12px calc(12px + env(safe-area-inset-bottom))}
@media (min-width:700px){.sd-sh-p{left:50%;right:auto;top:50%;bottom:auto;transform:translate(-50%,-50%);width:640px;border-radius:18px;max-height:84vh}}
.sd-sh-h{display:flex;align-items:center;margin-bottom:8px}.sd-sh-h b{flex:1;font-size:16px}.sd-x{width:36px;height:36px;border:0;background:#fff;border-radius:50%;font-size:20px;cursor:pointer}
#sd-kw{height:44px;border:2px solid var(--brand);border-radius:999px;padding:0 16px;font-size:16px;background:#fff}
.sd-mk{display:flex;gap:6px;overflow-x:auto;padding:8px 0;scrollbar-width:none;flex:none}.sd-mk::-webkit-scrollbar{display:none}
.sd-mk button{flex:none;height:32px;padding:0 12px;border:1px solid var(--line);border-radius:999px;background:#fff;font-size:12px;font-weight:700;cursor:pointer;white-space:nowrap}.sd-mk small{margin-left:4px;color:var(--sub);font-weight:400}
.sd-mk button[aria-pressed="true"]{background:var(--brand);border-color:var(--brand);color:#fff}.sd-mk button[aria-pressed="true"] small{color:#ffe4cc}
.sd-cand{list-style:none;margin:0;padding:0;overflow-y:auto;flex:1;display:grid;gap:6px;align-content:start}
.sd-cand button{width:100%;display:flex;align-items:center;gap:10px;text-align:left;background:#fff;border:2px solid transparent;border-radius:12px;padding:6px 10px;cursor:pointer;font:inherit;color:var(--txt)}
.sd-cand button[aria-pressed="true"]{border-color:var(--brand);background:#fff7ed}.sd-ck{flex:none;width:24px;height:24px;border-radius:50%;border:2px solid var(--line)}
.sd-cand button[aria-pressed="true"] .sd-ck{background:var(--brand);border-color:var(--brand);box-shadow:inset 0 0 0 4px #fff}.sd-0{padding:20px;text-align:center;color:var(--sub)}
.sd-done{flex:none;margin-top:8px;height:48px;border:0;border-radius:999px;background:linear-gradient(#fb923c,#ea580c);color:#fff;font-size:16px;font-weight:800;cursor:pointer}
.sd-bar{position:fixed;left:8px;right:8px;bottom:calc(8px + env(safe-area-inset-bottom));z-index:40;display:flex;align-items:center;gap:8px;padding:10px 14px;border-radius:999px;background:linear-gradient(90deg,#7c3aed,#db2777);color:#fff;box-shadow:0 6px 20px rgba(0,0,0,.25);cursor:pointer;max-width:560px;margin:0 auto}
.sd-bar[hidden]{display:none}.sd-bar>span:first-child{flex:1;font-size:13px}.sd-bar b{font-size:18px;margin:0 2px}.sd-bar em{font-style:normal;margin-left:8px;color:#fde047;font-weight:800}.sd-bar-go{background:#fde047;color:#3b0764;font-weight:900;font-size:13px;border-radius:999px;padding:6px 12px}
#shindan{padding-bottom:72px}
.sd-res{margin:16px 0;padding:20px 16px;border-radius:18px;background:linear-gradient(135deg,#fff7ed,#fff1f2);border:2px solid #fb923c;text-align:center;box-shadow:0 4px 14px rgba(234,88,12,.12)}
.sd-k{margin:0;font-size:14px;color:var(--sub)}.sd-m{margin:4px 0 0;font-size:20px;font-weight:700}.sd-m b{font-size:44px;color:var(--txt);font-variant-numeric:tabular-nums}
.sd-y{margin:0;font-size:14px;color:var(--sub)}.sd-sv{margin:12px 0 0;font-size:14px;color:var(--win);font-weight:700;line-height:1.5}.sd-sv b{font-size:30px;font-variant-numeric:tabular-nums}
.sd-sv0{margin:12px 0 0;font-size:13px;color:var(--sub)}.sd-pt{margin:8px 0 0;font-size:13px;color:var(--brand)}
.sd-share{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin:16px 0 8px}
.btn-main{min-height:48px;padding:0 20px;border:0;border-radius:999px;background:linear-gradient(#fb923c,#ea580c);color:#fff;font-size:15px;font-weight:700}
.btn-sub{min-height:44px;display:inline-flex;align-items:center;padding:0 14px;border:1px solid var(--line);border-radius:999px;background:#fff;color:var(--txt);font-size:14px;text-decoration:none}
.tb.new{background:var(--win-bg);color:var(--win);border:1px solid #f3b6c9;font-weight:700}
.more-link{display:block;margin:8px 16px 0;padding:12px;text-align:center;border-radius:var(--r);border:1px solid #f3b6c9;color:var(--win);font-weight:700;text-decoration:none;background:#fff}
.wk-share{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.cty{margin-left:8px;font-size:12px;font-weight:700;color:var(--sub);border:1px solid var(--line,#e5e7eb);border-radius:4px;padding:1px 6px;vertical-align:middle}
.card-pt{margin:2px 0;font-size:13px}.td-f{display:block;font-size:11px;color:var(--sub);font-weight:400}.card-ln{margin-left:8px;font-size:12px}.card-pt b{color:#bf0000}
.lv{margin:16px 0 0}.lv-c{padding:0 16px;margin:0 0 6px}.lv-more{display:block;margin:4px 16px 6px;text-align:center;height:44px;line-height:44px;border-radius:999px;background:#fff;border:2px solid var(--brand);color:var(--brand-d);font-weight:800}
.nq{display:flex;gap:6px;margin:8px 16px 12px}.nq select{height:44px;border:2px solid var(--brand);border-radius:999px;padding:0 10px;background:#fff;font-size:15px}.nq input{flex:1;min-width:0;height:44px;border:2px solid var(--brand);border-radius:999px;padding:0 14px;font-size:16px;background:#fff}
.nq button{height:44px;border:0;border-radius:999px;padding:0 18px;background:linear-gradient(#fb923c,#ea580c);color:#fff;font-weight:800;font-size:15px}
.hub{list-style:none;margin:0;padding:0 16px 8px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}@media (min-width:700px){.hub{grid-template-columns:repeat(4,minmax(0,1fr))}}
.hub a{display:flex;flex-direction:column;gap:2px;padding:14px;border-radius:16px;background:linear-gradient(135deg,#fff7ed,#ffe4e6);border:1px solid #fed7aa;color:var(--txt);min-height:96px}
.hub-b a{background:linear-gradient(135deg,#eef2ff,#e0f2fe);border-color:#c7d2fe}.hub b{font-size:16px;font-weight:900}.hub em{font-style:normal;font-size:11px;color:var(--sub)}
.hub-c{margin-top:auto;font-size:20px;font-weight:900;color:var(--win)}.hub small{font-size:10px;color:var(--sub)}.hub li[hidden]{display:none}
.nayami-h{display:flex;align-items:baseline}
.nayami-n{margin:2px 16px 8px;font-size:11px;color:var(--sub)}
.nayami{margin:12px 0 4px}.nayami-h{margin:0 16px 6px;font-size:16px;font-weight:800}
.nayami-l{display:flex;gap:8px;overflow-x:auto;list-style:none;margin:0;padding:0 16px 4px;scrollbar-width:none}
.nayami-l a{display:block;white-space:nowrap;padding:8px 14px;border-radius:999px;background:var(--win-bg,#fff4e5);border:1px solid var(--line,#e5e7eb);color:inherit;text-decoration:none;font-weight:700;font-size:14px}
.nayami-l small{margin-left:6px;color:var(--sub);font-weight:400}
.hx-txt{min-width:0;position:relative;z-index:1}.home-main{min-width:0}
.hx{margin:12px 16px 8px;border-radius:20px;background:#fff3e2;color:#3b2a1a;position:relative;overflow:hidden;display:flex;flex-direction:column}
.hx-ph{aspect-ratio:3/2;background:url(pets/hero.jpg) center 35%/cover no-repeat}
.hx-txt{padding:16px 18px 20px}
.hx-k{margin:0;font-size:12px;font-weight:700;display:inline-flex;gap:6px;align-items:center;background:#fff;color:#c2410c;border-radius:999px;padding:4px 12px;box-shadow:0 1px 2px rgba(0,0,0,.06)}
.hx-t{margin:10px 0 6px;font-size:22px;font-weight:800;line-height:1.4}.hx-t b{color:#ea580c}
.hx-s{margin:0 0 14px;font-size:13px;color:#7c5a3a}
.hx-q{display:flex;max-width:520px;box-shadow:0 4px 14px rgba(234,88,12,.18);border-radius:999px}.hx-q input{flex:1;min-width:0;height:50px;border:2px solid #fdba74;border-right:0;border-radius:999px 0 0 999px;padding:0 18px;font-size:15px;background:#fff}
.hx-q button{height:50px;border:0;border-radius:0 999px 999px 0;padding:0 24px;background:#ea580c;color:#fff;font-weight:800;font-size:16px;cursor:pointer}
.hq{position:sticky;top:0;z-index:20;display:flex;margin:0;padding:8px 12px;background:var(--bg)}
.hq input{flex:1;min-width:0;height:44px;border:2px solid #ea580c;border-right:0;border-radius:999px 0 0 999px;padding:0 16px;font-size:15px;background:#fff}
.hq button{width:56px;height:44px;border:0;border-radius:0 999px 999px 0;background:#ea580c;color:#fff;display:grid;place-items:center;cursor:pointer}
.bns{display:flex;gap:10px;overflow-x:auto;scroll-snap-type:x mandatory;padding:4px 12px 0;scrollbar-width:none}.bns::-webkit-scrollbar{display:none}
.bn{position:relative;flex:0 0 86%;max-width:520px;height:190px;border-radius:18px;overflow:hidden;scroll-snap-align:center;color:#fff;text-decoration:none;display:block;box-shadow:0 4px 14px rgba(0,0,0,.15)}
.bn-ph{position:absolute;inset:0;background:center/cover no-repeat}.bn-ph::after{content:"";position:absolute;inset:0;background:linear-gradient(90deg,rgba(0,0,0,.62),rgba(0,0,0,.08) 70%)}
.bn-t{position:absolute;left:16px;top:16px;z-index:1;font-size:19px;font-weight:800;line-height:1.3;text-shadow:0 2px 6px rgba(0,0,0,.35)}
.bn-t small{display:block;font-size:11px;font-weight:700;opacity:.95;margin-bottom:4px}.bn-t b{font-size:38px;color:#ffd84d;letter-spacing:-.5px}.bn-t i{font-style:normal;font-size:24px;color:#ffd84d}
.bn-go{position:absolute;left:16px;bottom:14px;z-index:1;background:#fff;color:#c2410c;font-weight:800;font-size:13px;border-radius:999px;padding:6px 14px}
.bn-n{position:absolute;right:10px;bottom:8px;max-width:48%;text-align:right;line-height:1.3;font-size:10px;opacity:.9;z-index:1}
.bn-b{background:radial-gradient(circle at 80% 30%,#ffd84d 0,#ff7a00 40%,#e11d48 100%)}.bn-b .bn-t{text-shadow:none}.bn-b .bn-t b{font-size:64px;color:#fff}.bn-b .bn-t i{color:#fff;font-size:30px}
.bn-im{position:absolute;right:12px;top:18px;width:120px;height:120px;object-fit:contain;background:#fff;border-radius:14px;padding:6px;transform:rotate(4deg);box-shadow:0 6px 16px rgba(0,0,0,.25)}
.bn-c .bn-ph::after{background:linear-gradient(90deg,rgba(190,24,93,.85),rgba(190,24,93,.05) 75%)}
.bn-d .bn-ph::after{background:linear-gradient(90deg,rgba(13,110,100,.88),rgba(13,110,100,.05) 75%)}
.bn-e .bn-ph::after{background:linear-gradient(90deg,rgba(30,41,99,.88),rgba(30,41,99,.05) 75%)}
.bn-f .bn-ph::after{background:linear-gradient(90deg,rgba(3,105,161,.88),rgba(3,105,161,.05) 75%)}
.tvx{--g:#8c6a3f;--gl:#c2a476;--ink:#1c1c1c;--mute:#6b6b6b;--hair:#e7e2d9;--paper:#faf8f4;background:var(--paper);color:var(--ink);padding-bottom:8px}
.tvx .tv-h2,.tvx h1,.tv-tht,.tv-n,.tv-pop b,.tv-rr-t b,.tv-ru-h b,.tv-wint{font-family:"Hiragino Mincho ProN","Yu Mincho","YuMincho","Noto Serif JP",serif;font-weight:600;letter-spacing:.04em}
.tv-hero{position:relative;height:440px;overflow:hidden;color:#fff}.tv-ph{position:absolute;inset:0;background:center 40%/cover no-repeat}
.tv-ph::after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.15),rgba(0,0,0,.05) 35%,rgba(0,0,0,.55))}
.tv-t{position:absolute;left:24px;right:24px;bottom:72px}.tv-t small{font-size:11px;letter-spacing:.3em;opacity:.9}
.tvx h1{margin:10px 0 8px;font-size:30px;line-height:1.5}.tv-t p{margin:0;font-size:13px;letter-spacing:.06em;opacity:.95}
.tv-f{position:relative;z-index:2;margin:-44px 16px 0;padding:18px 16px 16px;background:#fff;border:1px solid var(--hair);box-shadow:0 10px 30px rgba(28,28,28,.10)}
.tv-fr{display:flex;flex-direction:column;gap:6px;padding:0 0 14px;margin:0 0 14px;border-bottom:1px solid var(--hair)}.tv-fr label,.tv-fl{font-size:11px;letter-spacing:.12em;color:var(--mute)}
.tv-f select{height:46px;border:0;border-bottom:1px solid var(--ink);border-radius:0;background:transparent;font-size:17px;padding:0 2px;color:var(--ink)}
.tv-bd{display:flex;flex-wrap:wrap;gap:6px}.tv-bd button{height:40px;padding:0 12px;border:1px solid var(--hair);border-radius:2px;background:#fff;color:var(--ink);font-size:13px;cursor:pointer}
.tv-bd button[aria-pressed="true"]{border-color:var(--ink);background:var(--ink);color:#fff}
.tv-go-b{display:block;width:100%;height:52px;border:0;border-radius:2px;background:var(--ink);color:#fff;font-size:15px;letter-spacing:.2em;cursor:pointer}
.tv-hd{padding:40px 24px 16px;text-align:center}.tv-en{display:block;font-size:11px;letter-spacing:.35em;color:var(--g)}.tv-h2{margin:8px 0 0;font-size:22px;line-height:1.5}
.tv-hd::after{content:"";display:block;width:28px;height:1px;background:var(--g);margin:14px auto 0}.tv-sub{margin:12px 0 0;font-size:12px;color:var(--mute);line-height:1.7}
.tv-th{list-style:none;margin:0;padding:0 16px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px 12px}
.tv-th button{display:block;width:100%;padding:0;border:0;background:none;text-align:left;cursor:pointer;color:var(--ink)}
.tv-thp{display:block;aspect-ratio:4/5;background:#ddd center/cover no-repeat;transition:transform .4s}.tv-th button:hover .tv-thp{transform:scale(1.02)}
.tv-tht{display:block;margin-top:10px;font-size:14px;line-height:1.6}.tv-th button[aria-pressed="true"] .tv-thp{outline:2px solid var(--g);outline-offset:3px}
.tv-th button[aria-pressed="true"] .tv-tht{color:var(--g)}
.tv-pop{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));margin:0 16px;border-top:1px solid var(--hair)}
.tv-pop button{display:flex;flex-direction:column;align-items:flex-start;gap:2px;padding:16px 8px;border:0;border-bottom:1px solid var(--hair);background:none;text-align:left;cursor:pointer;color:var(--ink)}
.tv-pop button:nth-child(odd){border-right:1px solid var(--hair)}.tv-pop b{font-size:16px}.tv-pop small{font-size:11px;color:var(--mute);letter-spacing:.08em}
.tv-rg{display:flex;gap:18px;overflow-x:auto;padding:0 24px;border-bottom:1px solid var(--hair);scrollbar-width:none}.tv-rg::-webkit-scrollbar{display:none}
.tv-rg button{flex:none;height:42px;padding:0;border:0;border-bottom:2px solid transparent;background:none;color:var(--mute);font-size:13px;letter-spacing:.06em;cursor:pointer}
.tv-rg button[aria-selected="true"]{color:var(--ink);border-bottom-color:var(--g)}
.tv-pf{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));padding:8px 16px 0}.tv-pf[hidden]{display:none}
.tv-pf button{height:46px;border:0;border-bottom:1px solid var(--hair);background:none;color:var(--ink);font-size:14px;cursor:pointer}
.tv-pf button[aria-pressed="true"]{color:var(--g);font-weight:700}
.tv-tags{display:flex;flex-wrap:wrap;gap:6px}.tv-tn{margin:2px 0 0;font-size:11px;line-height:1.6;color:var(--mute)}.tv-tags::-webkit-scrollbar{display:none}
.tv-tags button{flex:none;height:40px;padding:0 14px;border:1px solid var(--hair);border-radius:2px;background:#fff;color:var(--ink);font-size:13px;cursor:pointer}
.tv-tags button[aria-pressed="true"]{border-color:var(--g);background:#f3ece1;color:#5c4526}.tv-c{padding:0 24px;font-size:12px;color:var(--mute);text-align:center}
.tv-l{list-style:none;margin:0;padding:8px 16px;display:grid;grid-template-columns:minmax(0,1fr);gap:28px}@media (min-width:700px){.tv-l{grid-template-columns:repeat(2,minmax(0,1fr))}}@media (min-width:1024px){.tv-l{grid-template-columns:repeat(3,minmax(0,1fr))}}
.tv-l a{display:block;color:var(--ink);text-decoration:none}.tv-im{display:block;aspect-ratio:16/10;background:#e7e2d9 center/cover no-repeat}
.tv-b{display:flex;flex-direction:column;gap:4px;padding:12px 2px 0}.tv-n{font-size:17px;line-height:1.5}.tv-s{font-size:12px;color:var(--mute)}
.tv-p{margin-top:4px;font-size:12px;color:var(--mute)}.tv-p b{font-size:20px;color:var(--ink);font-weight:600;margin:0 2px}
.tv-go{margin-top:8px;align-self:flex-start;font-size:12px;letter-spacing:.06em;color:var(--g);border-bottom:1px solid var(--g);padding-bottom:2px}
.tv-rt{list-style:none;grid-column:1/-1}.tv-rt a{display:block;padding:20px;background:var(--ink);color:#fff;text-decoration:none;font-size:14px;line-height:1.8;letter-spacing:.04em}
.tv-rt a b{font-family:"Hiragino Mincho ProN","Yu Mincho","Noto Serif JP",serif;font-size:19px;color:var(--gl);font-weight:600}.tv-rt small{display:block;margin-top:8px;color:var(--mute);font-size:11px;line-height:1.7}
.tv-ru{margin:24px 16px 8px;padding:24px 16px;background:var(--ink);color:#fff;text-align:center;position:relative;overflow:hidden}.tv-ru[hidden]{display:none}
.tv-ru-h{display:flex;flex-direction:column;align-items:center;gap:4px}.tv-ru-h b{font-size:18px;color:var(--gl)}.tv-ru-h span{font-size:11px;letter-spacing:.1em;opacity:.8}
.tv-wh{position:relative;width:230px;height:230px;margin:14px auto 6px}.tv-wp{position:absolute;left:50%;top:-6px;transform:translateX(-50%);width:0;height:0;border-left:10px solid transparent;border-right:10px solid transparent;border-top:20px solid var(--gl);z-index:2}
.tv-wl{position:absolute;inset:0;border-radius:50%;border:2px solid var(--gl);transition:transform 4s cubic-bezier(.12,.75,.12,1)}
.tv-wl i{position:absolute;left:50%;top:50%;width:50%;height:24px;margin-top:-12px;transform-origin:0 50%;font-style:normal;font-size:14px;color:#fff;text-align:right;padding-right:16px;box-sizing:border-box}
.tv-sp{display:block;width:100%;max-width:320px;margin:10px auto 0;height:48px;border:1px solid var(--gl);border-radius:2px;background:none;color:var(--gl);font-size:14px;letter-spacing:.2em;cursor:pointer}
.tv-sp:disabled{opacity:.5}.tv-lg{list-style:none;margin:14px 0 0;padding:0;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px 12px;text-align:left}
.tv-lg li{font-size:11px;color:rgba(255,255,255,.7);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:3px 0;border-bottom:1px solid rgba(255,255,255,.12)}.tv-lg li.on{color:var(--gl)}
.tv-ru .h2n{color:rgba(255,255,255,.6);font-size:11px;margin:12px 0 0}
.tv-win{margin-top:18px;text-align:left}.tv-win[hidden]{display:none}.tv-wint{margin:0 0 4px;font-size:16px;color:var(--gl);text-align:center}
.tv-win p{color:rgba(255,255,255,.7)!important;text-align:center}.tv-win a{display:block;background:#fff;color:var(--ink);text-decoration:none;animation:gin .45s ease-out}.tv-win .tv-b{padding:12px 14px 14px}
.tv-rr{position:relative;display:grid;grid-template-columns:38% minmax(0,1fr);margin:36px 16px 0;background:#fff;border:1px solid var(--hair);color:var(--ink);text-decoration:none}.tv-rr img{position:absolute;left:0;top:0}
.tv-rrp{grid-row:1/3;min-height:150px;background:center 78%/cover no-repeat}.tv-rr-t{min-width:0;display:flex;flex-direction:column;gap:4px;padding:16px 14px 6px}
.tv-rr-t small{font-size:10px;letter-spacing:.12em;color:var(--mute)}.tv-rr-t b{font-size:18px}.tv-rr-t span{font-size:11px;color:var(--mute);line-height:1.6}
.tv-rr-go{grid-column:2;justify-self:start;align-self:start;padding-bottom:2px;margin:0 14px 14px;font-size:12px;color:var(--g);border-bottom:1px solid var(--g);white-space:nowrap}
.tv-prr{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:0 16px 8px;font-size:12px;color:var(--mute)}.tv-prn{font-size:15px;color:var(--g);font-weight:700;white-space:nowrap}
.tv-prb{margin:0 16px 8px;height:2px;background:var(--hair)}.tv-prb i{display:block;height:100%;width:0;background:var(--g);transition:width .3s}
.tv-pl{list-style:none;margin:0 16px;padding:0;border-top:1px solid var(--hair)}.tv-pl li{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:12px 2px;border-bottom:1px solid var(--hair)}
.tv-pl li.on span{color:var(--mute);text-decoration:line-through}.tv-pl label{display:flex;align-items:center;gap:12px;font-size:14px;flex:1;min-height:28px}
.tv-pl input{width:20px;height:20px;accent-color:#8c6a3f}.tv-pl a{font-size:12px;color:var(--g);white-space:nowrap}
.tvx .nayami-n{margin:28px 16px 8px;font-size:11px;line-height:1.8;color:var(--mute)}
@media (min-width:1024px){.tv-hero{height:520px}.tv-t{left:64px;bottom:96px}.tvx h1{font-size:40px}.tv-f{max-width:760px;margin:-56px auto 0;display:grid;grid-template-columns:1fr 1.6fr auto;gap:20px;align-items:end}.tv-fr{border:0;margin:0;padding:0}.tv-go-b{width:180px}.tv-th{grid-template-columns:repeat(3,minmax(0,1fr));max-width:1000px;margin:0 auto}.tv-pop{grid-template-columns:repeat(3,minmax(0,1fr));max-width:1000px;margin:0 auto}.tv-pop button,.tv-pop button:nth-child(odd){border-right:1px solid var(--hair)}.tv-pf{grid-template-columns:repeat(6,minmax(0,1fr));max-width:1000px;margin:0 auto}.tv-l,.tv-pl,.tv-rr{max-width:1000px;margin-left:auto;margin-right:auto}.tv-rr{grid-template-columns:300px minmax(0,1fr)}.tv-rrp{min-height:200px}.tv-f{grid-template-columns:1fr 1.6fr;}.tv-f .tv-fr:nth-of-type(3){grid-column:1/-1}.tv-go-b{grid-column:1/-1;justify-self:center;width:280px}}

.hd-tv .hd-in{align-items:baseline;gap:10px;flex-wrap:wrap}.lg-tv{text-decoration:none}.lg-tv-n{font-family:"Hiragino Mincho ProN","Yu Mincho","Noto Serif JP",serif;font-size:21px;font-weight:600;letter-spacing:.08em;color:#1c1c1c}
.hd-par{font-size:11px;color:#8c6a3f;letter-spacing:.04em;text-decoration:none;border-bottom:1px solid #c2a476}.hd-tv .hd-note{margin-left:auto}
.tv-gl{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding:0 16px}.tv-gl a{display:block;color:var(--ink);text-decoration:none;background:#fff;border:1px solid var(--hair)}
.tv-glp{display:block;aspect-ratio:16/10;background:center/cover no-repeat}.tv-glt{display:flex;flex-direction:column;gap:2px;padding:10px 12px 12px}.tv-glt b{font-family:"Hiragino Mincho ProN","Yu Mincho","Noto Serif JP",serif;font-size:16px;font-weight:600}.tv-glt small{font-size:11px;color:var(--mute)}
.tv-glc{display:block;margin-top:4px;font-size:10px;line-height:1.4;color:var(--mute);word-break:break-all}.tg-hc{position:absolute;right:8px;bottom:6px;z-index:2;font-size:10px;color:rgba(255,255,255,.85);text-shadow:0 1px 2px rgba(0,0,0,.6)}.bn .tg-hc{left:14px;right:auto;bottom:4px;font-size:9px}.tg-hero{height:380px}.tg-hero .tv-t{bottom:40px}.tg-hero .tv-t p{line-height:1.8;max-width:560px}
.tg-l{list-style:none;margin:0 16px;padding:0;display:grid;gap:12px}.tg-i{background:#fff;border:1px solid var(--hair);padding:16px}
.tg-k{font-size:10px;letter-spacing:.2em;color:var(--g)}.tg-n{margin:4px 0 2px;font-family:"Hiragino Mincho ProN","Yu Mincho","Noto Serif JP",serif;font-size:18px;font-weight:600;line-height:1.5}
.tg-w{margin:0;font-size:12px;color:var(--mute)}.tg-d{margin:8px 0 0;font-size:14px;line-height:1.8}.tg-c{margin:10px 0 0;padding:10px 12px;background:#f3ece1;font-size:13px;line-height:1.7;color:#4a3a22}.tg-c b{margin-right:8px;font-size:11px;letter-spacing:.1em;color:var(--g)}
.tg-s{display:inline-block;margin-top:10px;font-size:12px;color:var(--g);border-bottom:1px solid var(--g)}
.tg-m{margin:0 16px;padding:16px 16px 16px 34px;background:#fff;border:1px solid var(--hair);font-size:13px;line-height:1.9}
.tg-cta{display:block;margin:32px 16px 0;padding:18px;background:var(--ink);color:#fff;text-align:center;text-decoration:none;font-size:15px;letter-spacing:.06em}
.tg-back{display:block;margin:12px 16px 0;padding:14px;border:1px solid var(--ink);color:var(--ink);text-align:center;text-decoration:none;font-size:14px}
@media (min-width:1024px){.tv-ga{max-width:968px;margin-left:auto;margin-right:auto}.tv-gl{grid-template-columns:repeat(3,minmax(0,1fr));max-width:1000px;margin:0 auto}.tg-l{grid-template-columns:repeat(2,minmax(0,1fr));max-width:1000px;margin:0 auto}.tg-m,.tg-cta,.tg-back{max-width:968px;margin-left:auto;margin-right:auto}}
.tv-ga{margin:20px 16px 0;padding:16px;background:#fff;border:1px solid var(--hair)}.tv-gah{margin:0 0 8px;font-size:12px;letter-spacing:.12em;color:var(--mute)}
.tv-gar{display:flex;gap:12px;padding:8px 0;border-top:1px solid var(--hair)}.tv-gar span{flex:none;width:78px;font-size:12px;color:var(--g)}.tv-gar p{margin:0;display:flex;flex-wrap:wrap;gap:4px 12px}.tv-gar a{font-size:14px;color:var(--ink);text-decoration:none;border-bottom:1px solid var(--hair);line-height:1.8}
/* 旅のしおり風（11/24 Taku「一休風→旅のしおり風・手書き風フォントで彩り」） */
.tvx{--paper:#fbf6ea;--g:#9a6b3a;--gl:#d9b98a;--hair:#e9dfcc;background:var(--paper) repeating-linear-gradient(0deg,transparent 0 31px,rgba(154,107,58,.05) 31px 32px)}
.tvx h1,.tvx .tv-h2,.tvx .tg-n,.tvx .tv-glt b,.tv-tabs button,.tvx .tv-wint{font-family:"Klee One","Hiragino Maru Gothic ProN","Yu Gothic",sans-serif;font-weight:600;letter-spacing:.02em}
.tvx .tv-en{display:inline-block;padding:3px 12px;background:rgba(217,185,138,.55);color:#5a3d1e;letter-spacing:.25em;transform:rotate(-1.5deg)}
.tvx .tv-h2{font-size:24px}.tvx .tg-i{border-radius:6px}.tvx .tv-glb,.tvx .tv-gl a{border-radius:6px;box-shadow:0 2px 0 rgba(154,107,58,.12)}
.tv-glb{display:block;width:100%;padding:0;text-align:left;font:inherit;color:var(--ink);background:#fff;border:1px solid var(--hair);cursor:pointer;overflow:hidden}
.tv-glp{position:relative}.tv-glpl{position:absolute;left:0;right:0;bottom:0;padding:18px 8px 5px;font-style:normal;font-size:11px;line-height:1.35;color:#fff;background:linear-gradient(transparent,rgba(0,0,0,.6))}
.tv-sel{margin:0 16px 8px}.tv-tabs{display:flex;gap:6px;margin:0 0 12px;border-bottom:2px dashed var(--gl)}
.tv-tabs button{flex:1;padding:10px 4px 8px;border:0;background:none;font-size:17px;color:var(--mute);cursor:pointer;border-radius:8px 8px 0 0}
.tv-tabs button span{font-family:system-ui,sans-serif;font-size:12px;margin-left:2px}.tv-tabs button[aria-selected=true]{background:#fff;color:#5a3d1e;box-shadow:inset 0 -3px 0 var(--g)}
.tv-chips{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:0 0 12px;font-size:12px;color:var(--mute)}
.tv-chips button{padding:6px 10px;border:1px solid var(--hair);border-radius:999px;background:#fff;font-size:13px;color:var(--ink);cursor:pointer}.tv-chips button small{margin-left:4px;color:var(--mute)}
.tv-chips button[aria-pressed=true]{background:#5a3d1e;border-color:#5a3d1e;color:#fff}.tv-chips button[aria-pressed=true] small{color:#e9dfcc}
.tv-gp .tg-l{padding:0}.tv-warn,.tg-warn{margin:12px 0;padding:10px 12px;border:1px dashed var(--g);border-radius:6px;background:#fff;font-size:12px;line-height:1.6;color:#5a3d1e}
.tg-fp{display:block;font-weight:600;color:#3a2a14;margin-bottom:2px}
.sb-f{display:flex;flex-direction:column;gap:14px;margin:0 16px 24px;padding:18px;background:#fff;border:1px solid var(--hair);border-radius:8px}
.sb-f label{display:block;font-size:14px}.sb-f label>input:not([type=radio]):not([type=checkbox]),.sb-f label>select,.sb-f label>textarea{display:block;width:100%;box-sizing:border-box;margin-top:6px}.sb-f input:not([type=radio]):not([type=checkbox]),.sb-f select,.sb-f textarea{padding:10px;border:1px solid var(--hair);border-radius:6px;font-size:16px;background:#fff}
.sb-f fieldset{border:0;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:6px 14px}.sb-f legend{font-size:14px;margin-bottom:6px;padding:0}.sb-f fieldset label{flex-direction:row;align-items:center;gap:4px}
.sb-ok{flex-direction:row!important;align-items:flex-start;font-size:12px!important;line-height:1.6}.sb-r{margin-left:6px;font-size:11px;color:#b3261e}.sb-n{margin:-6px 0 0;font-size:12px;color:var(--mute)}.sb-m{margin:0;font-size:14px;color:#5a3d1e}.sb-hp{position:absolute;left:-9999px;width:1px;height:1px}
.effn{display:none;font-size:12px;color:#5f6b7a;margin-top:2px}.eff-on .effn{display:block}.effnote{margin:4px 16px 8px;font-size:12px;line-height:1.6;color:#5f6b7a}
.tv-gl[hidden]{display:none}.tv-grg{margin:0 0 14px}
.tv-gv[hidden]{display:none}.tv-gv{margin:0}.tv-gv a{display:flex;align-items:center;gap:12px;margin:0 16px 14px;padding:10px;background:#fff;border:1px solid var(--gl);color:var(--ink);text-decoration:none}.tv-gv i{flex:none;width:56px;height:56px;background:center/cover no-repeat}.tv-gv span{flex:1;display:flex;flex-direction:column;gap:2px}.tv-gv small{font-size:11px;color:var(--g)}.tv-gv b{font-size:14px}.tv-gv em{font-style:normal;font-size:12px;color:var(--g)}
.tv-gdl a.tv-gdc{display:flex;gap:0;padding:0;text-align:left;background:#fff;border:1px solid var(--gl);color:var(--ink);font-size:13px;overflow:hidden}.tv-gdp{flex:none;width:38%;min-height:132px;background:center/cover no-repeat}.tv-gdt{flex:1;display:flex;flex-direction:column;gap:4px;padding:12px}.tv-gdt small{font-size:11px;color:var(--g)}.tv-gdt b{font-family:"Hiragino Mincho ProN","Yu Mincho","Noto Serif JP",serif;font-size:16px;line-height:1.4}.tv-gdt>span{font-size:12px;color:var(--mute)}.tv-gdth{display:flex;gap:4px;margin-top:4px}.tv-gdth i{width:40px;height:40px;background:center/cover no-repeat}
.tg-act{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:16px 16px 0}.tg-act a{display:flex;align-items:center;justify-content:center;min-height:48px;padding:8px;text-align:center;text-decoration:none;font-size:13px;line-height:1.4}.tg-a1{background:var(--ink);color:#fff}.tg-a2{border:1px solid var(--ink);color:var(--ink)}
.tg-sum{margin:10px 16px 0;font-size:12px;color:var(--mute);text-align:center}.tg-sum a{color:var(--g)}
.tg-i.tg-hp{padding:0;overflow:hidden}.tg-hp .tg-b{padding:14px 16px 16px}.tg-f{margin:0}.tg-f img{display:block;width:100%;aspect-ratio:16/10;object-fit:cover;background:#eee}.tg-f figcaption{padding:4px 10px;font-size:10px;line-height:1.5;color:var(--mute);background:#faf7f2}.tg-f figcaption a{color:inherit;text-decoration:none}
.tg-ig{display:flex;justify-content:center;overflow:hidden;background:#fafafa}.tg-ig iframe{display:block;width:330px;max-width:100%;height:310px;border:0}
.tg-o{display:flex;flex-wrap:wrap;gap:8px 16px;margin:12px 0 0}.tg-o a{font-size:13px;color:var(--g);border-bottom:1px solid var(--g);text-decoration:none}
.tg-near{margin-top:4px}
.tg-stk{position:fixed;left:12px;right:12px;bottom:12px;z-index:50;display:block;padding:14px;background:var(--ink);color:#fff;text-align:center;text-decoration:none;font-size:14px;letter-spacing:.04em;box-shadow:0 6px 20px rgba(0,0,0,.25)}.tg-stk[hidden]{display:none}
@media (min-width:1024px){.tg-act{max-width:968px;margin-left:auto;margin-right:auto}.tg-stk{left:auto;right:24px;width:320px}}
.tv-gdl{margin:0 16px 12px;text-align:center}.tv-gdl[hidden]{display:none}.tv-gdl a{display:inline-block;padding:10px 16px;border:1px solid var(--g);color:#5c4526;background:#f3ece1;text-decoration:none;font-size:13px}
.tvbar-x{flex:none;margin-left:4px;width:28px;height:28px;border:0;border-radius:50%;background:#dcfce7;color:#166534;font-size:15px;line-height:1;cursor:pointer}.tvbar{display:flex;align-items:center;gap:10px;margin:8px 16px;padding:10px 14px;border-radius:12px;background:#f0fdf4;border:1px solid #bbf7d0;color:#166534;font-size:13px;font-weight:700;text-decoration:none}.tvbar b{white-space:nowrap}.tvbar-g{flex:1;height:8px;border-radius:4px;background:#dcfce7;overflow:hidden}.tvbar-g i{display:block;height:100%;background:#16a34a}
.tvbk{position:fixed;left:12px;right:12px;bottom:12px;z-index:60;max-width:480px;margin:0 auto;padding:16px 16px 12px;border-radius:16px;background:#fff;box-shadow:0 8px 32px rgba(0,0,0,.22);animation:tvbk .25s ease-out}
@keyframes tvbk{from{transform:translateY(24px);opacity:0}}.tvbk b{display:block;font-size:17px;padding-right:28px}.tvbk p{margin:4px 0 10px;font-size:13px;color:#6b7280}
.tvbk-go{display:block;height:48px;line-height:48px;text-align:center;border-radius:12px;background:var(--brand);color:#fff;font-weight:800;text-decoration:none}
.tvbk-n{display:block;width:100%;margin-top:6px;padding:8px;border:0;background:none;color:#6b7280;font-size:13px}.tvbk-x{position:absolute;top:8px;right:8px;width:36px;height:36px;border:0;background:none;font-size:20px;color:#6b7280}
.bn-dots{display:flex;justify-content:center;gap:6px;margin:8px 0 4px}.bn-dots i{width:6px;height:6px;border-radius:3px;background:#d6d3d1;transition:width .2s}.bn-dots i.on{width:18px;background:#ea580c}
.card-c{font-size:11px;color:var(--sub);margin-left:4px}
.gc{margin:12px 12px 4px;padding:12px;border-radius:20px;background:url(gacha/confetti.webp) center/cover,linear-gradient(140deg,#3b0764,#7c3aed 55%,#db2777);color:#fff;position:relative;overflow:hidden}
.gc .h2n{color:#e9d5ff;margin:6px 0 0;font-size:10px}
.uk{margin-bottom:8px}.uk-add{width:100%;height:38px;border:1.5px dashed #f0abfc;border-radius:12px;background:rgba(255,255,255,.08);color:#fff;font-weight:800;font-size:13px;cursor:pointer}
.uk-me{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:700}.uk-n{font-size:15px;font-weight:900}.uk-n small{font-size:10px;margin-left:4px;opacity:.85}
.uk-s{background:#f97316;border-radius:999px;padding:1px 8px}.uk-l{background:#facc15;color:#3b0764;border-radius:999px;padding:1px 8px}.uk-ed{margin-left:auto;background:none;border:0;color:#e9d5ff;font-size:11px;text-decoration:underline;cursor:pointer}
.uk-b{height:6px;border-radius:3px;background:rgba(255,255,255,.25);margin-top:6px;overflow:hidden}.uk-b i{display:block;height:100%;background:linear-gradient(90deg,#facc15,#f472b6)}
.uk-f{display:flex;flex-wrap:wrap;gap:6px}.uk-c{display:flex;flex-wrap:wrap;gap:6px;width:100%}.uk-c button{border:1px solid #f0abfc;background:none;color:#fff;border-radius:999px;padding:5px 12px;font-weight:700;font-size:12px;cursor:pointer}
.uk-c button[aria-pressed="true"]{background:#facc15;color:#3b0764;border-color:#facc15}.uk-f input{flex:1;min-width:0;height:36px;border-radius:10px;border:0;padding:0 10px;font-size:14px}.uk-ok{height:36px;border:0;border-radius:10px;background:#facc15;color:#3b0764;font-weight:900;padding:0 16px;cursor:pointer}
.gc-btn,.gc-rr{position:relative;z-index:2}
.gc-top{display:flex;gap:12px;align-items:center}.gc-mc{flex:none;width:110px;height:auto;filter:drop-shadow(0 6px 10px rgba(0,0,0,.35))}
.gc-mc.go{animation:gmc .6s}@keyframes gmc{20%{transform:rotate(-6deg)}40%{transform:rotate(6deg)}60%{transform:rotate(-4deg)}80%{transform:rotate(3deg)}}
.gc-hd{display:flex;flex-direction:column;gap:4px;min-width:0}.gc-hd b{font-size:26px;font-weight:900;letter-spacing:1px;text-shadow:0 0 14px #f0abfc,0 2px 0 #7c2d12}
.gc-s{font-size:13px;font-weight:800;line-height:1.4}.gc-s2{font-size:11px;opacity:.9}
.gc-new{align-self:flex-start;background:#facc15;color:#3b0764;font-weight:900;font-size:11px;border-radius:999px;padding:2px 10px;animation:gpulse 1.2s infinite}
@keyframes gpulse{50%{transform:scale(1.08)}}
.gc-dex{align-self:flex-start;font-size:11px;font-weight:800;background:rgba(255,255,255,.18);border-radius:999px;padding:2px 10px}
.gc-st{position:relative;min-height:128px;display:grid;place-items:center;margin-top:8px}.gc-st[hidden]{display:none}
.gc-cap{width:96px;height:96px}.gc-cap.go{animation:gdrop .75s cubic-bezier(.3,1.4,.5,1)}@keyframes gdrop{0%{transform:translateY(-60px) scale(.6);opacity:0}60%{transform:translateY(0) scale(1.05)}80%{transform:rotate(-10deg)}100%{transform:rotate(8deg) scale(1.1)}}
.gc-bu{position:absolute;left:50%;top:50%;width:300px;transform:translate(-50%,-50%);pointer-events:none;animation:gbu .6s ease-out}.gc-bu[hidden]{display:none}
@keyframes gbu{from{transform:translate(-50%,-50%) scale(.3);opacity:0}}
.gc-card{position:relative;display:flex;gap:10px;align-items:center;width:100%;max-width:420px;background:#fff;color:var(--txt);border-radius:14px;padding:10px;text-decoration:none;animation:gin .45s cubic-bezier(.2,1.6,.4,1)}
@keyframes gin{from{transform:scale(.4) rotate(-6deg);opacity:0}}
.gc-card[hidden],.gc-cap[hidden]{display:none}
.gc-card img{width:84px;height:84px;object-fit:contain;flex:none}.gc-card .r{display:block;font-size:13px;font-weight:900;color:#a16207}.gc-card .nm{font-size:12px;line-height:1.35;max-height:2.7em;overflow:hidden}
.gc-card .pc{font-size:20px;font-weight:900;color:#e11d48}.gc-card .pc small{font-size:12px;margin-left:6px;color:var(--sub);text-decoration:line-through;font-weight:400}
.gc-card.r3{box-shadow:0 0 0 3px #facc15,0 0 28px #facc15}.gc-card.r2{box-shadow:0 0 0 3px #c084fc,0 0 18px #c084fc}
.gc-btn{display:block;width:100%;margin-top:10px;height:52px;border:0;border-radius:999px;background:linear-gradient(#fde047,#f59e0b);color:#3b0764;font-size:18px;font-weight:900;box-shadow:0 5px 0 #b45309;cursor:pointer}
.gc-btn:active{transform:translateY(3px);box-shadow:0 2px 0 #b45309}
.gc-rr{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0 0}.gc-rr span{font-size:10px;font-weight:800;background:rgba(0,0,0,.25);border-radius:6px;padding:2px 6px}
.gc-fx{position:absolute;width:8px;height:8px;border-radius:2px;top:40%;left:50%;animation:gfx 1s ease-out forwards;pointer-events:none}
@keyframes gfx{to{transform:translate(var(--x),var(--y)) rotate(540deg);opacity:0}}
@media (min-width:1024px){.gc{margin:12px 0 4px}}
.qk{list-style:none;margin:6px 0 2px;padding:0 12px;display:grid;grid-template-columns:repeat(5,1fr);gap:6px}
.qk a{display:flex;flex-direction:column;align-items:center;gap:4px;font-size:11px;font-weight:700;line-height:1.25;text-align:center;color:var(--txt);text-decoration:none}
.qk-i{width:48px;height:48px;border-radius:14px;display:grid;place-items:center;color:#fff}.qk-1{background:linear-gradient(135deg,#f59e0b,#ef4444)}.qk-2{background:linear-gradient(135deg,#ec4899,#a855f7)}.qk-3{background:linear-gradient(135deg,#06b6d4,#2563eb)}.qk-4{background:linear-gradient(135deg,#10b981,#0d9488)}.qk-5{background:linear-gradient(135deg,#facc15,#f97316)}
.tls,.dcs{list-style:none;margin:0;padding:0 12px 6px;display:flex;gap:10px;overflow-x:auto;scrollbar-width:none}.tls::-webkit-scrollbar,.dcs::-webkit-scrollbar{display:none}
.tl{display:flex;flex-direction:column;width:150px;min-height:150px;padding:10px 12px 8px;border-radius:14px;color:#3b1d06;text-decoration:none;position:relative;overflow:hidden}
.tl::after{content:"";position:absolute;left:0;right:0;bottom:30px;border-top:2px dashed rgba(255,255,255,.75)}
.tl0{background:linear-gradient(135deg,#bbf7d0,#4ade80)}.tl1{background:linear-gradient(135deg,#fed7aa,#fb923c)}.tl2{background:linear-gradient(135deg,#fbcfe8,#f472b6)}.tl3{background:linear-gradient(135deg,#fef08a,#facc15)}.tl4{background:linear-gradient(135deg,#bfdbfe,#60a5fa)}
.tl-p{flex:none;align-self:flex-start;background:rgba(255,255,255,.75);border-radius:999px;font-size:10px;font-weight:800;padding:0 8px}
.tl-c{flex:none;margin-top:4px;font-size:13px;font-weight:900;line-height:1.3;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.tl-v{flex:none;margin-top:auto;margin-bottom:34px;font-size:11px;font-weight:800;line-height:1.1}.tl-v small{display:block;font-size:10px}.tl-v b{font-size:28px;letter-spacing:-1px}
.tl-n{position:absolute;left:12px;bottom:7px;font-size:11px;font-weight:800}
.dc{display:flex;flex-direction:column;width:136px;color:var(--txt);text-decoration:none}.dc-p{position:relative;width:136px;height:136px;background:#fff;border:1px solid var(--line);border-radius:12px;display:grid;place-items:center}
.dc-p img{width:116px;height:116px;object-fit:contain}.dc-b{position:absolute;left:6px;top:6px;background:#e11d48;color:#fff;font-size:11px;font-weight:800;border-radius:6px;padding:2px 6px}
.dc-n{margin-top:6px;font-size:12px;line-height:1.35;max-height:2.7em;overflow:hidden}.dc-pr{font-size:12px}.dc-pr b{font-size:17px;color:#e11d48}.dc-o{font-size:11px;color:var(--sub);text-decoration:line-through}
.h2n{margin:-6px 12px 8px;font-size:11px;color:var(--sub)}@media (min-width:1024px){.h2n{margin:-6px 0 8px}}
.h2a{margin-left:auto;font-size:12px;font-weight:700}.h2:has(.h2a){display:flex;align-items:baseline}
.ptabs{list-style:none;margin:0 0 8px;padding:0 12px;display:flex;gap:6px;overflow-x:auto;scrollbar-width:none}.ptabs button{border:1px solid var(--line);background:#fff;border-radius:999px;padding:7px 14px;font-weight:700;font-size:13px;white-space:nowrap;cursor:pointer;color:var(--txt)}
.ptabs button[aria-selected="true"]{background:#ea580c;border-color:#ea580c;color:#fff}
.cat-sec.js .pet-sec{display:none}.cat-sec.js .pet-sec.on{display:block}.cat-sec.js .pet-sec>.h2{display:none}
.cat-sec .cats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;padding:0 12px}
@media (min-width:1024px){.hq{position:static;padding:12px 0 4px}.bns{padding:4px 0 0}.bn{flex-basis:calc(50% - 5px)}.qk{padding:0;max-width:520px}.tls,.dcs{padding:0 0 6px;flex-wrap:wrap}.cat-sec .cats{grid-template-columns:repeat(3,minmax(0,1fr));padding:0}.ptabs{padding:0}}
.circ{list-style:none;margin:8px 0 4px;padding:0 16px;display:flex;gap:14px;overflow-x:auto;scrollbar-width:none}.circ::-webkit-scrollbar{display:none}
.circ a{display:flex;flex-direction:column;align-items:center;width:88px;color:var(--txt);text-decoration:none}
.circ-p{width:80px;height:80px;border-radius:50%;background:#fff;border:1px solid var(--line);display:grid;place-items:center;overflow:hidden;color:var(--brand)}
.circ-p img{width:100%;height:100%;object-fit:cover}.circ-n{margin-top:6px;font-size:13px;font-weight:700}.circ-c{font-size:11px;color:var(--sub)}
.side{display:none}
body:has(.home) .hd-search{display:none}@media (min-width:1024px){main:has(.home){max-width:1200px}body:has(.home) .hd-in{max-width:1200px}.home{display:grid;grid-template-columns:220px minmax(0,1fr);gap:20px;padding:0 16px}.home-main{min-width:0}
 .side{display:block;position:sticky;top:12px;align-self:start;max-height:calc(100vh - 24px);overflow:auto;background:#fff;border:1px solid var(--line);border-radius:12px;padding:8px 0;margin-top:12px}
 .side ul{list-style:none;margin:0;padding:0}.side-h{margin:8px 14px 4px;font-size:11px;font-weight:700;color:var(--sub)}
 .side-pet>a{display:flex;gap:8px;align-items:center;padding:8px 14px;font-weight:800;color:var(--txt)}.side-pet>a .ic{color:var(--brand)}
 .side-pet li a,.side-x a{display:block;padding:5px 14px 5px 40px;font-size:13px;color:var(--txt)}.side-x a{padding-left:14px}.side a:hover{background:var(--brand-bg)}
 .home-main .hx{margin:12px 0 8px;min-height:360px;justify-content:center}.hx-ph{position:absolute;inset:0 0 0 38%;aspect-ratio:auto;-webkit-mask-image:linear-gradient(to right,transparent,#000 35%);mask-image:linear-gradient(to right,transparent,#000 35%)}.hx-txt{padding:36px 40px;max-width:62%}.hx-t{font-size:32px}
 .circ{padding:0;justify-content:flex-start;gap:20px;flex-wrap:wrap;overflow:visible}.circ a{width:110px}.circ-p{width:110px;height:110px;border:3px solid #fff;box-shadow:0 2px 10px rgba(0,0,0,.12)}}

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
.list.gl{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;padding:0 8px 8px}
.list.gl .row{flex-direction:column;gap:6px;padding:8px 10px 10px;height:100%}
.list.gl .pic{display:block;width:100%;aspect-ratio:1;background:#fff;border-radius:10px;display:grid;place-items:center;overflow:hidden}
.list.gl .pic img,.list.gl .noimg{width:100%;height:100%;max-width:none;object-fit:contain}
.list.gl .info{width:100%;gap:1px}.list.gl .nm{font-size:12.5px;line-height:1.4;min-height:2.8em}
.list.gl .pr{margin:2px 0 0}.list.gl .pr b{font-size:20px}.list.gl .scope{font-size:10px}
.list.gl .pt,.list.gl .meta{font-size:11px}.list.gl .rv{font-size:11px;flex-wrap:wrap;white-space:normal;gap:2px 4px}.list.gl .rvn{font-size:10px}
.list.gl .save-s{position:absolute;left:14px;top:14px;margin:0;background:var(--win);color:#fff;font-weight:800;font-size:11px;border-radius:6px;padding:2px 6px;z-index:2}
.list.gl .fav.mini{top:12px;right:12px;width:34px;height:34px;background:rgba(255,255,255,.92)}
@media (min-width:700px){.list.gl{grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;padding:0 16px 8px}}
@media (min-width:960px){.list.gl{grid-template-columns:repeat(4,minmax(0,1fr))}}
/* 並べ替え・絞り込み */
.sort{display:flex;flex-wrap:nowrap;gap:8px;align-items:center;padding:10px 16px;background:#fff;border-top:1px solid var(--line);border-bottom:1px solid var(--line);font-size:12px;color:var(--sub);position:sticky;top:0;z-index:5;overflow-x:auto;scrollbar-width:none}.sort::-webkit-scrollbar{display:none}
.sort>span{white-space:nowrap}
.sort button{font:inherit;font-size:13px;white-space:nowrap;background:#fff;border:1px solid #cfd4d9;border-radius:999px;height:36px;padding:0 14px;color:var(--txt);cursor:pointer}
.sort button[aria-pressed="true"]{background:var(--brand);color:#fff;border-color:var(--brand);font-weight:700}
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
.mall{font-size:10px;font-weight:700;border-radius:4px;padding:0 5px;margin-right:4px;align-self:flex-start}.mall.r{color:#bf0000;background:#fdecec}.mall.y{color:#c2185b;background:#fde7f0}
.mallcmp{font-size:13px;margin:8px 0}.mallcmp small{display:block;color:var(--sub);font-size:11px}
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
