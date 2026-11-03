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
_CHOICE = re.compile(r"選べる|選択|サイズ違い|容量違い")
# 内容量セルに併記する内訳（商品名の「7L×6袋」「100枚×4袋」など、量×個数の部分だけ）
_DETAIL = re.compile(r"\d+(?:\.\d+)?\s*(?:kg|g|ml|l|L|枚|匹)\s*(?:[×xX*]\s*\d+\s*" + PACK + r"?)+")


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
                rest = _BRACKET.sub(lambda b: " " if any(r2.search(b.group(0)) for r2, _ in UNIT_RE[unit]) else b.group(0), rest)
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


LABEL = {"kg": "1kgあたり", "g": "1gあたり", "L": "1Lあたり", "枚": "1枚あたり", "匹": "1匹あたり"}
SHOP = {"rakuten": "楽天市場", "yahoo": "Yahoo!ショッピング"}
AD = {"rakuten": "楽天アフィリエイト", "yahoo": "バリューコマース"}
CREDIT = {
    # 楽天ウェブサービスのクレジット: 公式テキスト版スニペットを改変せずに使う（guide/credit、2026-11-03 確認）
    "rakuten": '<!-- Rakuten Web Services Attribution Snippet FROM HERE --><a href="https://developers.rakuten.com/" target="_blank">Supported by Rakuten Developers</a><!-- Rakuten Web Services Attribution Snippet TO HERE -->',
    "yahoo": '<a href="https://developer.yahoo.co.jp/sitemap/" rel="nofollow">Webサービス by Yahoo! JAPAN</a>',
}


def page(title, body, desc, shops, sample):
    ads = "・".join(AD[s] for s in shops) or "アフィリエイト"
    credits = " ／ ".join(CREDIT[s] for s in shops)
    band = ('<p class="sample">【サンプル表示】API 未接続のため架空の商品・価格です。実在の商品ではありません。</p>'
            if sample else "")
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><meta name="description" content="{html.escape(desc)}">
<link rel="stylesheet" href="style.css"></head><body>
<header><a href="index.html">{SITE_NAME}</a><span class="pr">PR</span></header>
<p class="promo">本ページはプロモーション（アフィリエイト広告）が含まれています。</p>
{band}<main>{body}</main>
<footer><p>当サイトはアフィリエイト広告（{ads}）を利用しています。価格・送料・在庫は取得時点のもので、購入前に各ショップでご確認ください。</p>
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


def main():
    groups = json.load(open(os.path.join(ROOT, "products.json"), encoding="utf-8"))["groups"]
    data = json.load(open(os.path.join(ROOT, "data", "prices.json"), encoding="utf-8"))
    sample = str(data.get("fetched_at", "")).startswith("SAMPLE")
    shops = [s for s in SHOP if any(it["shop"] == s for it in data["items"])]
    origin = os.environ.get("SITE_ORIGIN", "").rstrip("/")
    out = os.path.join(ROOT, "site")
    os.makedirs(out, exist_ok=True)
    fetched = html.escape(str(data.get("fetched_at", "不明")))
    published = []  # (priority, title, fn, group)
    stats = []  # (slug, 対象件数, 表示件数, 非表示件数, 公開可否, 理由)
    for g in groups:
        rows, hidden, total = [], 0, 0
        for it in data["items"]:
            if it["group"] != g["slug"] or any(x in it["name"] for x in g.get("exclude", [])):
                continue
            total += 1
            a = amount(it["name"], g["unit"])
            if not a:
                hidden += 1  # 内容量が読めない商品は出さない（単価を推測しない）
                continue
            rows.append((it["price"] / a, a, it))
        ok, why = gate(total, len(rows), sample)
        stats.append((g["slug"], total, len(rows), hidden, ok, why))
        fn = g["slug"] + ".html"
        if not ok:
            if os.path.exists(os.path.join(out, fn)):
                os.remove(os.path.join(out, fn))  # 前回の古いページを残さない
            continue
        rows.sort(key=lambda r: r[0])
        lab = LABEL[g["unit"]]
        tr = []
        for i, (up, a, it) in enumerate(rows, 1):
            d = detail(it["name"])
            dt = f'<br><small class=dt>{html.escape(d)}</small>' if d else ""
            tr.append(f"<tr><td>{i}</td><td><a href=\"{html.escape(it['url'])}\" rel=\"sponsored nofollow\">{html.escape(it['name'])}</a></td>"
                      f"<td>{SHOP[it['shop']]}</td><td class=n>{it['price']:,}円</td><td>{ship_badge(it.get('shipping'))}</td>"
                      f"<td class=n>{fmt_amount(a, g['unit'])}{dt}</td><td class=n><b>{up:,.1f}円</b></td></tr>")
        best = rows[0] if rows else None
        lead = (f"<p class=lead>今回取得した{len(rows)}件のうち、{lab}がいちばん安いのは <b>{best[0]:,.1f}円</b>（{SHOP[best[2]['shop']]}）。</p>" if best else
                "<p class=lead>現在、比較できる商品がありません（次回の価格取得で更新します）。</p>")
        old = sorted({str(r[2]["fetched_at"]) for r in rows if r[2].get("fetched_at")})
        carried = (f"<br>一部前回取得: {html.escape('・'.join(old))}（今回の取得に失敗したため前回の価格を表示しています）" if old else "")
        trust = (f"<div class=trust><p class=fetched>価格取得日時: <b>{fetched}</b>{carried}</p>"
                 f"<p class=formula>{lab}＝税込価格 ÷ 内容量の合計（1袋の量×袋数）。送料は含みません。</p></div>")
        body = (f"<h1>{html.escape(g['title'])} の単価くらべ</h1>{lead}{trust}"
                f"<p class=meta>{len(rows)}件／商品名から内容量を読み取れない商品は載せていません</p>"
                f"<div class=wrap><table><thead><tr><th>#</th><th>商品</th><th>ショップ</th><th>税込価格</th><th>送料</th><th>内容量（合計）</th><th>{lab}</th></tr></thead>"
                f"<tbody>{''.join(tr)}</tbody></table></div>"
                f"<p><a href=\"index.html\">← カテゴリ一覧へ</a></p>")
        open(os.path.join(out, fn), "w", encoding="utf-8").write(
            page(f"{g['title']}の単価比較（{lab}の価格）｜{SITE_NAME}", body,
                 f"{g['title']}を{'・'.join(SHOP[s] for s in shops)}で{lab}の価格で比較。", shops, sample))
        published.append((g.get("priority", 9), g["title"], fn, g))
    published.sort(key=lambda p: (p[0], p[1]))
    files = [p[2] for p in published]
    links = [f"<li><span class=tag>{html.escape(g['animal'])}</span><a href=\"{fn}\">{html.escape(g['title'])}</a></li>"
             for _, _, fn, g in published]
    idx = (f"<h1>{SITE_NAME}</h1><p>ペット用品を「1kgあたり」「1枚あたり」の価格で比べます。{'・'.join(SHOP[s] for s in shops)}の価格を取得して並べています。</p>"
           f"<div class=trust><p class=fetched>価格取得日時: <b>{fetched}</b></p>"
           f"<p class=formula>単価＝税込価格 ÷ 内容量の合計（1袋の量×袋数）。内容量は商品名から読み取り、読み取れない商品は載せません。送料は含みません。</p></div>"
           f"<ul class=list>{''.join(links)}</ul>")
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
        page(SITE_NAME, idx, "ペット用品の単位あたり価格を比較", shops, sample))
    open(os.path.join(out, "style.css"), "w", encoding="utf-8").write(CSS)
    robots = "User-agent: *\nAllow: /\n"
    if origin:
        today = datetime.date.today().isoformat()
        urls = "".join(f"<url><loc>{html.escape(origin)}/{f}</loc><lastmod>{today}</lastmod></url>"
                       for f in ["index.html"] + files)
        open(os.path.join(out, "sitemap.xml"), "w", encoding="utf-8").write(
            f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n')
        robots += f"Sitemap: {origin}/sitemap.xml\n"
    open(os.path.join(out, "robots.txt"), "w", encoding="utf-8").write(robots)
    print(f"{len(files)}/{len(groups)} ページ＋index を生成" + ("（サンプル: 全カテゴリ）" if sample else "（本番: 公開判定あり）")
          + ("（sitemap.xml あり）" if origin else "（SITE_ORIGIN 未設定: sitemap.xml なし）"))
    return 0


CSS = """body{margin:0;font-family:system-ui,"Hiragino Sans","Noto Sans JP",sans-serif;color:#222;background:#fafaf7;line-height:1.6}
header{display:flex;justify-content:space-between;align-items:center;padding:12px 16px;background:#fff;border-bottom:1px solid #ddd}
header a{font-weight:700;color:#2a6b4f;text-decoration:none}.pr{font-size:12px;border:1px solid #999;padding:0 6px;border-radius:4px;color:#666}
.promo{margin:0;padding:6px 16px;background:#fff;border-bottom:1px solid #ddd;color:#333;font-size:.85rem;text-align:center}
.sample{margin:0;padding:8px 16px;background:#fff4d6;color:#7a5200;font-size:.85rem;text-align:center}
main{max-width:960px;margin:0 auto;padding:16px}h1{font-size:1.4rem}.lead{font-size:1.1rem}.meta{color:#666;font-size:.85rem}
.trust{background:#fff;border:1px solid #dfe8e3;border-left:4px solid #2a6b4f;border-radius:6px;padding:8px 12px;margin:12px 0;font-size:.9rem}
.trust p{margin:2px 0}.formula{color:#2a6b4f}
.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;background:#fff;font-size:.9rem}th,td{border-bottom:1px solid #eee;padding:8px;text-align:left}
td.n{text-align:right;white-space:nowrap}.dt{color:#888}.list{list-style:none;padding:0}.list li{padding:10px 0;border-bottom:1px solid #eee}
.ship{display:inline-block;font-size:12px;border-radius:4px;padding:0 6px;white-space:nowrap}
.ship.free{background:#e7f2ec;color:#1f5a40}.ship.cond{background:#fff4d6;color:#7a5200}.ship.paid{background:#f1f1f1;color:#555}
.tag{display:inline-block;font-size:12px;background:#e7f2ec;color:#2a6b4f;border-radius:4px;padding:0 6px;margin-right:8px}
footer{max-width:960px;margin:24px auto;padding:16px;color:#666;font-size:.8rem}
"""

if __name__ == "__main__":
    sys.exit(main())
