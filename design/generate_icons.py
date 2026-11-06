"""動物アイコンを画像生成（OpenAI Images API）で作り、ベクター化して候補にする。

図形の組み合わせで描かせる方式（design_icons.py）では動物が描けなかったため（2026-11-06 Taku: 動物はバツ）、
画像生成で描かせ、白黒の画像を輪郭追跡（potrace）で SVG のパスにする。色は currentColor で塗るので、文字色・背景に合わせて変わる。

  python design/generate_icons.py            候補を生成（各アイコン N 枚）→ design/candidates.json と design/candidates.png
  python design/generate_icons.py --apply dog=1,cat=2 ...   選んだ候補を assets/icons.json に反映（番号は1始まり）

環境変数：OPENAI_API_KEY、OPENAI_IMAGE_MODEL（任意。既定 gpt-image-2 → 使えなければ gpt-image-1.5 → gpt-image-1）、
         ICON_CANDIDATES（各アイコンの候補数。既定 2）、ICON_QUALITY（既定 medium）、ICON_ONLY（例: dog,cat。一部だけ作り直す）
"""
import base64
import io
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "design"))
import build  # noqa: E402

STYLE = (
    "A single pictogram icon for a Japanese pet-supplies price comparison website. "
    "Solid pure black (#000000) shapes on a pure white background, nothing else. "
    "Flat filled silhouette with a few white negative-space details (eyes, inner lines) cut out of the black shape. "
    "No outlines-only drawing, no gradients, no shading, no texture, no text, no border, no frame, no drop shadow. "
    "Bold, simple, calm and trustworthy — like a well-made public signage pictogram, not a cute mascot, not clip art, not emoji. "
    "Must stay recognizable at 28 pixels: chunky proportions, wide gaps between parts, no thin details. "
    "Centered, filling about 80% of the canvas, generous white margin on all sides. Original design."
)
SUBJECTS = {
    "paw": "A paw print used as a website logo mark: one large main pad and four toe pads, with a slightly distinctive, balanced arrangement so it reads as a brand mark.",
    # 2026-11-06 Taku: ビーグル風は日本で一般的でない → 柴犬・トイプードル
    "dog": ["The head of a Shiba Inu dog seen from the front: small upright triangular ears, fox-like face, curled fluffy cheeks, the classic Japanese Shiba look. Clearly a Shiba Inu dog, not a cat or fox.",
            "The head of a Toy Poodle dog seen from the front: round fluffy curly-haired head and drooping fluffy ears, teddy-bear cut popular in Japan. Clearly a dog, not a bear or sheep."],
    "cat": "The head of a cat seen from the front, with pointed triangular ears and short whiskers. Clearly a cat.",
    "small": "A hamster seen from the front, round chubby body with small round ears and tiny paws held in front. Represents small pets (hamster, rabbit, guinea pig).",
    "reptile": "A gecko lizard seen from directly above: head, four splayed legs with toes, and a long curved tail. Clearly a lizard.",
    # 2026-11-06 Taku: 小鳥と魚はアイコン（カテゴリ）を分ける
    "bird": "A small songbird such as a budgerigar or Java sparrow, perched, in side view, with a short beak and a visible eye. Clearly a pet bird.",
    "fish": "A goldfish in side view with a rounded body and a large flowing double tail fin. Clearly a goldfish.",
}


def api(path, body):
    hdr = {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"}
    req = urllib.request.Request("https://api.openai.com/v1/" + path, data=json.dumps(body).encode(), method="POST", headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if path == "responses":  # 理由が分かるように本文を出す
            raise RuntimeError(f"responses: HTTP {e.code} {e.read()[:400]!r}")
        raise


def generate(key, n):
    subj = SUBJECTS[key]
    if isinstance(subj, list):  # 描き分け（例：犬種）ごとに n 枚
        pngs, m, u = [], None, {}
        for i, one in enumerate(subj):
            got, m, u = _generate(key, n, one)
            pngs += got
        return pngs, m, u
    return _generate(key, n, subj)


def _generate(key, n, subject):
    models = [os.environ.get("OPENAI_IMAGE_MODEL") or "gpt-image-2", "gpt-image-1.5", "gpt-image-1"]
    last = None
    for m in dict.fromkeys(models):
        body = {"model": m, "prompt": STYLE + "\n\nSubject: " + subject, "n": n, "size": "1024x1024",
                "quality": os.environ.get("ICON_QUALITY") or "medium"}
        try:
            d = api("images/generations", body)
            return [base64.b64decode(x["b64_json"]) for x in d["data"]], m, d.get("usage", {})
        except urllib.error.HTTPError as e:
            last = f"{m}: HTTP {e.code} {e.read()[:300]!r}"
            print("model fallback:", last)
    # Images API の権限が無いキーでも、Responses API の画像生成ツール経由なら通る場合がある
    out = []
    for _ in range(n):
        d = api("responses", {"model": os.environ.get("OPENAI_MODEL") or "gpt-5-mini",
                              "input": STYLE + "\n\nSubject: " + subject + "\nGenerate exactly one image.",
                              "tools": [{"type": "image_generation", "size": "1024x1024",
                                         "quality": os.environ.get("ICON_QUALITY") or "medium"}],
                              "tool_choice": {"type": "image_generation"}})
        out += [base64.b64decode(o["result"]) for o in d.get("output", []) if o.get("type") == "image_generation_call" and o.get("result")]
    if out:
        return out, "responses+image_generation", {}
    raise RuntimeError(last)


def vectorize(png):
    """白黒画像 → 24×24 座標（2〜22 に収める）の塗りパス"""
    import numpy as np
    import potrace
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("L")
    a = np.array(im) < 128  # 黒い部分
    ys, xs = np.nonzero(a)
    if len(xs) == 0:
        return None
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    side = max(x1 - x0, y1 - y0)
    scale = 20.0 / side
    ox = 2 + (20 - (x1 - x0) * scale) / 2 - x0 * scale  # 中央寄せ
    oy = 2 + (20 - (y1 - y0) * scale) / 2 - y0 * scale
    bm = potrace.Bitmap(~a)  # potracer は「値が小さい側」を塗りとして追跡する
    paths = bm.trace(turdsize=int(side * 0.004) ** 2 + 4, alphamax=1.0, opticurve=True, opttolerance=0.2)

    def P(pt):
        return f"{pt.x * scale + ox:.2f} {pt.y * scale + oy:.2f}"
    d = []
    for curve in paths:
        d.append("M" + P(curve.start_point))
        for seg in curve.segments:
            if seg.is_corner:
                d.append("L" + P(seg.c) + "L" + P(seg.end_point))
            else:
                d.append("C" + P(seg.c1) + " " + P(seg.c2) + " " + P(seg.end_point))
        d.append("Z")
    return f'<path fill="currentColor" stroke="none" fill-rule="evenodd" d="{"".join(d)}"/>'


def sheet(cands, path):
    """候補の見本：実際の使われ方に近いサイズ（ペット選択 32px・ロゴ 20px）と、採用済みアイコンとの並び"""
    from design_icons import render
    keep = "".join(build._SVG.format(s=18, d=build.ICON[k]) for k in ("star", "search", "share"))
    rows = []
    for k, lst in cands.items():
        for i, inner in enumerate(lst, 1):
            cells = "".join(f"<td>{build._SVG.format(s=s, d=inner)}</td>" for s in (20, 32, 64))
            rows.append(f'<tr><th>{k} 案{i}</th>{cells}<td class="tile">{build._SVG.format(s=32, d=inner)}<span>{k}</span></td>'
                        f'<td class="dk">{build._SVG.format(s=32, d=inner)}</td><td class="mix">{keep}{build._SVG.format(s=18, d=inner)}</td></tr>')
    html = f"""<!doctype html><meta charset="utf-8"><style>
body{{font:13px sans-serif;margin:16px;color:#1f2328;background:#fff}} h1{{font-size:15px}}
table{{border-collapse:collapse}} th{{text-align:left;padding:8px 12px 8px 0;font-weight:600;white-space:nowrap}}
td{{padding:8px 14px;border-left:1px solid #eee;color:#0f6b62;vertical-align:middle}} td svg{{display:block}}
td.tile{{text-align:center}} td.tile svg{{margin:0 auto 4px}} td.tile span{{font-size:11px;color:#1f2328}}
td.dk{{background:#0f6b62;color:#fff}} td.mix svg{{display:inline-block;margin-right:8px;color:#1f2328}}
</style><h1>動物アイコン候補（20 / 32 / 64px・ペット選択の見え方・濃い背景・採用済みアイコンと並べた18px）</h1><table>{"".join(rows)}</table>"""
    return render(html, path)


def apply(spec):
    cands = json.load(open(os.path.join(ROOT, "design", "candidates.json"), encoding="utf-8"))
    fp = os.path.join(ROOT, "assets", "icons.json")
    icons = json.load(open(fp, encoding="utf-8")) if os.path.exists(fp) else {}
    for part in spec.split(","):
        k, i = part.split("=")
        icons[k] = cands[k][int(i) - 1]
    json.dump(icons, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("applied:", spec)


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--apply":
        apply(sys.argv[2])
        return 0
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY が未設定です")
        return 1
    from design_icons import sanitize
    n = int(os.environ.get("ICON_CANDIDATES") or 2)
    only = [k for k in (os.environ.get("ICON_ONLY") or "").split(",") if k] or list(SUBJECTS)
    out_dir = os.path.join(ROOT, "design", "gen")
    os.makedirs(out_dir, exist_ok=True)
    cpath = os.path.join(ROOT, "design", "candidates.json")
    cands = json.load(open(cpath, encoding="utf-8")) if os.path.exists(cpath) else {}
    log, model = [], None
    for k in only:
        pngs, model, usage = generate(k, n)
        cands[k] = []
        for i, png in enumerate(pngs, 1):
            open(os.path.join(out_dir, f"{k}_{i}.png"), "wb").write(png)
            v = vectorize(png)
            s = sanitize(v) if v else None
            if s:
                cands[k].append(s)
        log.append(f"- {k}: 候補 {len(cands[k])}/{len(pngs)} 枚（usage {json.dumps(usage, ensure_ascii=False)}）")
    json.dump(cands, open(cpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    sheet(cands, os.path.join(ROOT, "design", "candidates.png"))
    report = [f"# 動物アイコン候補（画像生成: {model}・品質 {os.environ.get('ICON_QUALITY') or 'medium'}）", ""] + log + [
        "", "見本：design/candidates.png、元画像：design/gen/"]
    open(os.path.join(ROOT, "design", "GEN_REPORT.md"), "w", encoding="utf-8").write("\n".join(report) + "\n")
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
