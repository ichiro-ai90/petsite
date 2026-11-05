"""アイコン担当（ChatGPT）にアイコン一式を描かせ、安全な SVG だけを assets/icons.json に保存する。

流れ：
  1. 依頼書（design/icon_brief.md）と現行アイコンを渡して、一式を描かせる（第1稿）
  2. 第1稿を 13/18/32/64px で描画した見本画像を、本人に見せて自己レビュー・修正させる（第2稿）
  3. 要素・属性を許可リストで検査し、通ったものだけを保存。見本画像も design/preview.png に残す

環境変数：OPENAI_API_KEY（必須）、OPENAI_DESIGN_MODEL（任意。既定 gpt-5、使えなければ gpt-5-mini）
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import build  # noqa: E402

KEYS = list(build.ICON)
ALLOWED_TAGS = {"path", "circle", "ellipse", "line", "polyline", "polygon", "rect", "g"}
ALLOWED_ATTRS = {"d", "cx", "cy", "r", "rx", "ry", "x", "y", "x1", "y1", "x2", "y2", "width", "height", "points",
                 "transform", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "opacity",
                 "fill-rule", "clip-rule"}
COLOR_ATTRS = {"fill", "stroke"}
NS = "http://www.w3.org/2000/svg"


def sanitize(inner):
    """許可リスト外の要素・属性・色があれば None。通れば正規化した文字列を返す"""
    try:
        root = ET.fromstring(f'<svg xmlns="{NS}">{inner}</svg>')
    except ET.ParseError:
        return None
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if el is root:
            continue
        if tag not in ALLOWED_TAGS:
            return None
        for k, v in el.attrib.items():
            if k not in ALLOWED_ATTRS:
                return None
            if k in COLOR_ATTRS and v not in ("currentColor", "none"):
                return None
            if any(bad in v.lower() for bad in ("url(", "javascript", "<", "&")):
                return None
        if el.text and el.text.strip():
            return None
    ET.register_namespace("", NS)
    return "".join(ET.tostring(c, encoding="unicode").replace(f' xmlns="{NS}"', "") for c in root)


def schema():
    props = {k: {"type": "string"} for k in KEYS}
    return {"type": "object", "additionalProperties": False, "required": ["intent", "icons", "notes"], "properties": {
        "intent": {"type": "string"},
        "icons": {"type": "object", "additionalProperties": False, "required": KEYS, "properties": props},
        "notes": {"type": "array", "items": {"type": "string"}}}}


def call(model, content):
    body = {"model": model, "input": [{"role": "user", "content": content}],
            "text": {"format": {"type": "json_schema", "name": "icons", "schema": schema(), "strict": True}},
            "reasoning": {"effort": os.environ.get("OPENAI_DESIGN_REASONING") or "medium"},
            "background": True, "store": True}
    hdr = {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"}
    req = urllib.request.Request("https://api.openai.com/v1/responses", data=json.dumps(body).encode(), method="POST", headers=hdr)
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.load(r)
    for _ in range(120):  # 最大20分
        if d.get("status") in ("completed", "failed", "cancelled", "incomplete"):
            break
        time.sleep(10)
        with urllib.request.urlopen(urllib.request.Request(f"https://api.openai.com/v1/responses/{d['id']}", headers=hdr), timeout=60) as r:
            d = json.load(r)
    if d.get("status") != "completed":
        raise RuntimeError(f"status={d.get('status')} {json.dumps(d.get('error') or d.get('incomplete_details'), ensure_ascii=False)[:300]}")
    text = d.get("output_text") or "".join(c.get("text", "") for o in d.get("output", []) if o.get("type") == "message"
                                          for c in o.get("content", []))
    return json.loads(text), d.get("usage", {})


def ask(content):
    models = [os.environ.get("OPENAI_DESIGN_MODEL") or "gpt-5", "gpt-5-mini"]
    last = None
    for m in dict.fromkeys(models):
        try:
            out, usage = call(m, content)
            return out, usage, m
        except urllib.error.HTTPError as e:  # モデルが使えない（404/400）なら次へ
            last = f"{m}: HTTP {e.code} {e.read()[:200]!r}"
            print("model fallback:", last)
    raise RuntimeError(last)


def preview_html(icons, title):
    def svg(d, s):
        return build._SVG.format(s=s, d=d)
    rows = "".join(
        f'<tr><th>{k}</th>' + "".join(f"<td>{svg(icons[k], s)}</td>" for s in (13, 18, 32, 64))
        + f'<td class="dk">{svg(icons[k], 18)}{svg(icons[k], 32)}</td></tr>' for k in KEYS)
    return f"""<!doctype html><meta charset="utf-8"><style>
body{{font:13px sans-serif;margin:16px;color:#1f2328;background:#fff}} h1{{font-size:15px}}
table{{border-collapse:collapse}} th{{text-align:left;padding:6px 12px 6px 0;font-weight:600}}
td{{padding:6px 14px;border-left:1px solid #eee;color:#0f6b62;vertical-align:middle}} td.dk{{background:#0f6b62;color:#fff}}
td svg{{display:block}} td.dk svg{{display:inline-block;margin-right:10px}}
</style><h1>{title}（左から 13 / 18 / 32 / 64px、右端は濃い背景）</h1><table>{rows}</table>"""


def render(html_text, path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 520, "height": 900}, device_scale_factor=2)
        pg.set_content(html_text)
        png = pg.screenshot(full_page=True)
        b.close()
    with open(path, "wb") as f:
        f.write(png)
    return png


def clean(out):
    good, bad = {}, []
    for k in KEYS:
        s = sanitize(out["icons"].get(k, ""))
        (good.__setitem__(k, s) if s else bad.append(k))
    return good, bad


def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY が未設定です")
        return 1
    brief = open(os.path.join(ROOT, "design", "icon_brief.md"), encoding="utf-8").read()
    current = json.dumps(build.ICON, ensure_ascii=False, indent=1)
    extra = ""
    fb = os.path.join(ROOT, "design", "feedback.md")  # 開発・Taku・監査役からの差し戻し（あれば）
    if os.path.exists(fb):
        extra = "\n\n## 前回の案への差し戻し\n" + open(fb, encoding="utf-8").read()
    usage_all = []

    # 第1稿
    p1 = (brief + extra + "\n\n## 参考：いま使っている仮のアイコン（開発が仮に描いたもの。作り直してよい）\n```json\n" + current + "\n```\n"
          "条件を守ったうえで、セットとして最も完成度の高い案を返してください。")
    out1, u, model = ask([{"type": "input_text", "text": p1}])
    usage_all.append(u)
    good1, bad1 = clean(out1)
    icons1 = {**build.ICON, **good1}
    png1 = render(preview_html(icons1, "第1稿"), os.path.join(ROOT, "design", "preview_draft1.png"))

    # 第2稿：自分の案を実寸で見て直す
    p2 = (brief + "\n\n## 自己レビュー\nあなたの第1稿を実寸で描画した見本を添付します。デザイナーとして厳しく見直し、"
          "小さいサイズで潰れる線、セット内で太さや大きさが揃っていないもの、何の絵か分かりにくいもの、"
          "どこかで見た汎用アイコンに見えるものを直した最終案を返してください。直す必要がないものはそのまま返してください。"
          + (f"\n技術条件を守れず採用できなかったもの：{', '.join(bad1)}（条件を守って描き直す）" if bad1 else "")
          + "\n\n第1稿：\n```json\n" + json.dumps(out1["icons"], ensure_ascii=False, indent=1) + "\n```")
    out2, u, model = ask([{"type": "input_text", "text": p2},
                          {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(png1).decode()}])
    usage_all.append(u)
    good2, bad2 = clean(out2)
    final = {**build.ICON, **good1, **good2}
    render(preview_html(final, "最終案"), os.path.join(ROOT, "design", "preview.png"))

    os.makedirs(os.path.join(ROOT, "assets"), exist_ok=True)
    with open(os.path.join(ROOT, "assets", "icons.json"), "w", encoding="utf-8") as f:
        json.dump({k: final[k] for k in KEYS}, f, ensure_ascii=False, indent=1)
    tin = sum(x.get("input_tokens", 0) for x in usage_all)
    tout = sum(x.get("output_tokens", 0) for x in usage_all)
    report = [f"# アイコン案（担当: ChatGPT {model}）", "", "## 設計意図", out2.get("intent") or out1.get("intent", ""), "",
              "## メモ"] + [f"- {n}" for n in (out2.get("notes") or [])] + [
              "", f"- 採用できず現行のまま：{', '.join(k for k in KEYS if k not in good1 and k not in good2) or 'なし'}",
              f"- トークン：入力 {tin:,} / 出力 {tout:,}", "", "見本：design/preview.png（第1稿は preview_draft1.png）"]
    open(os.path.join(ROOT, "design", "REPORT.md"), "w", encoding="utf-8").write("\n".join(report) + "\n")
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
