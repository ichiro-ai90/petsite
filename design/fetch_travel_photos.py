"""旅行ページ用の写真を Pexels から取得（Pexels ライセンス：無料・商用可・クレジット不要）。縮小・切り抜きのみ"""
import io, os, urllib.request
from PIL import Image
P = {"car": 27176278, "beach": 5865334, "beach2": 19571059, "mountain": 1448052, "lake": 1831997, "bed": 3554424}
os.makedirs("assets/travel", exist_ok=True)
for k, pid in P.items():
    u = f"https://images.pexels.com/photos/{pid}/pexels-photo-{pid}.jpeg?auto=compress&cs=tinysrgb&w=1600"
    b = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read()
    im = Image.open(io.BytesIO(b)).convert("RGB")
    w, h = im.size
    t = 3 / 2 if k != "bed" else 4 / 3
    if w / h > t:
        nw = int(h * t); im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = int(w / t); im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    im.thumbnail((1200, 1200)); im.save(f"assets/travel/{k}.jpg", quality=80, optimize=True, progressive=True)
    print(k, im.size)
open("assets/travel/CREDITS.md", "w", encoding="utf-8").write(
    "# 写真の出典（すべて Pexels・Pexels ライセンス：無料・商用利用可・クレジット不要。2026-11-21 取得、縮小・切り抜きのみ）\n"
    + "".join(f"- {k}.jpg … https://www.pexels.com/photo/{v}/\n" for k, v in P.items()))
