"""共有画像用フォント assets/og-font.otf を作り直す（build.py の文字＋かな・カナ・数字のサブセット）。
元フォント: Noto Sans CJK JP Bold（Ubuntu なら `sudo apt-get install -y fonts-noto-cjk`）。pip install fonttools"""
import os
from fontTools import subset
from fontTools.ttLib import TTCollection

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
c = TTCollection(SRC)
font = next(f for f in c.fonts if "JP" in f["name"].getDebugName(1))
txt = set(open(os.path.join(ROOT, "build.py"), encoding="utf-8").read()) | set("0123456789,./−-%()（）・〜")
txt |= {chr(x) for x in range(0x3041, 0x3097)} | {chr(x) for x in range(0x30A1, 0x30FB)}
o = subset.Options(); o.layout_features = ["*"]
s = subset.Subsetter(o); s.populate(text="".join(txt)); s.subset(font)
font.save(os.path.join(ROOT, "assets", "og-font.otf"))
print("ok", os.path.getsize(os.path.join(ROOT, "assets", "og-font.otf")))
