"""Draws the logo at the top of the READMEs: the program's icon beside the wordmark and the tagline, as on the page's
top bar. Two versions, for GitHub's light and dark themes, drawn at twice their shown size on a transparent ground.

    python desktop/make_logo.py            docs/images/logo-light.png and docs/images/logo-dark.png

The icon, the fonts and the browser come from make_icon.py.
"""
import os
import sys
import tempfile

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_icon import DESIGN, INK, ON_BLOCK, RED, ROOT, font_face, shoot, svg     # noqa: E402

OUT = os.path.join(ROOT, "docs", "images")
SCALE = 2
ICON = 96                    # shown size of the icon; the wordmark is set to its height
THEMES = {
    # name: (wordmark, tagline); the red O is the same in both
    "light": (INK, "#393b3d"),
    "dark": (ON_BLOCK, "#a3a6a9"),
}


def page(word, tag):
    s = SCALE
    css = (font_face() +
           "html,body{margin:0;background:transparent}"
           ".lockup{display:inline-flex;align-items:center;gap:%dpx;padding:%dpx}"
           ".mark{font:900 %dpx/0.86 Chivo;letter-spacing:0.01em;color:%s;white-space:nowrap}"
           ".mark .o{color:%s}"
           ".tag{margin-top:%dpx;font:600 %dpx/1 Anaheim;letter-spacing:0.36em;text-transform:uppercase;color:%s;white-space:nowrap}"
           % (22 * s, 8 * s, 50 * s, word, RED, 14 * s, 15 * s, tag))
    body = ('<div class="lockup">%s<div><div class="mark">MODEL DEP<span class="o">O</span>T</div>'
            '<div class="tag">Local Model Archive</div></div></div>' % svg(DESIGN, ICON * s))
    return "<!doctype html><html><head><style>%s</style></head><body>%s</body></html>" % (css, body)


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, (word, tag) in THEMES.items():
        with tempfile.TemporaryDirectory() as tmp:
            raw = os.path.join(tmp, "raw.png")
            shoot(page(word, tag), 700 * SCALE, 140 * SCALE, raw)
            img = Image.open(raw).convert("RGBA")
            left, top, right, bottom = img.getchannel("A").getbbox()
            pad = 4 * SCALE
            img = img.crop((max(0, left - pad), max(0, top - pad), right + pad, bottom + pad))
            out = os.path.join(OUT, "logo-%s.png" % name)
            img.save(out, optimize=True)
            print("logo written:", out, img.size)


if __name__ == "__main__":
    main()
