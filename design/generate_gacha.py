"""お得ガチャの素材を画像生成で作る（Taku 11/20 承認: 8枚前後・約80円まで）。
出力: design/gacha/*.png（透過）と GACHA_REPORT.md。1回の実行で最大 MAX 枚。"""
import base64, io, json, os, urllib.request, urllib.error
from PIL import Image

MAX = 8
STYLE = ("Flat vector illustration for a Japanese mobile shopping app, glossy and playful like a game reward screen, "
         "bold clean shapes, soft highlights, no text, no letters, no numbers, no logos, no brand marks, centered, "
         "transparent background, high contrast, vivid orange (#F97316), magenta (#DB2777), purple (#7C3AED) and gold (#FACC15) palette.")
ITEMS = {
    "machine": ("1024x1536", "A cute capsule toy vending machine (gacha machine): a round clear glass dome on top filled with colorful capsules, "
                "a sturdy orange body with a big round gold turning knob in the middle and a capsule exit slot at the bottom. "
                "A small white paw-print emblem on the body. Front view."),
    "capsule": ("1024x1024", "A single closed capsule toy ball: top half glossy orange, bottom half white, with a thin purple band and a small paw-print on top. Slightly tilted."),
    "capsule_open": ("1024x1024", "A capsule toy ball popping open: top half (orange) lifting off the white bottom half, golden light rays and small sparkles bursting out of the inside."),
    "burst3": ("1024x1024", "A radiant golden starburst reward effect with three big gold stars in an arc, shining rays and confetti, for a top-rarity win."),
    "burst2": ("1024x1024", "A purple and pink sparkle reward effect with two silver-lilac stars and soft rays, for a medium-rarity win."),
    "burst1": ("1024x1024", "A small soft orange sparkle effect with one simple star, for a common win."),
    "confetti": ("1536x1024", "Scattered festive confetti pieces and small stars spread across the whole image, sparse in the center so content can sit on top."),
}


def api(path, body):
    hdr = {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"}
    req = urllib.request.Request("https://api.openai.com/v1/" + path, data=json.dumps(body).encode(), method="POST", headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{path}: HTTP {e.code} {e.read()[:300]!r}")


def gen(key, size, subject):
    last = None
    for m in dict.fromkeys([os.environ.get("OPENAI_IMAGE_MODEL") or "gpt-image-2", "gpt-image-1.5", "gpt-image-1"]):
        body = {"model": m, "prompt": STYLE + "\n\nSubject: " + subject, "n": 1, "size": size, "quality": "medium", "background": "transparent"}
        try:
            d = api("images/generations", body)
            return base64.b64decode(d["data"][0]["b64_json"]), m, d.get("usage", {})
        except RuntimeError as e:
            last = e
            print("model fallback:", e)
    raise last


def main():
    os.makedirs("design/gacha", exist_ok=True)
    only = [k for k in (os.environ.get("GACHA_ONLY") or "").split(",") if k] or list(ITEMS)
    rep = ["# お得ガチャ素材（画像生成）", ""]
    for k in only[:MAX]:
        size, subj = ITEMS[k]
        png, m, u = gen(k, size, subj)
        im = Image.open(io.BytesIO(png)).convert("RGBA")
        im.save(f"design/gacha/{k}.png", optimize=True)
        sm = im.copy(); sm.thumbnail((512, 512)); sm.save(f"design/gacha/{k}.webp", quality=88)
        rep.append(f"- {k}: {m} {size} usage={json.dumps(u)}")
        print(k, m, u)
    open("design/gacha/GACHA_REPORT.md", "w", encoding="utf-8").write("\n".join(rep) + "\n")


main()
