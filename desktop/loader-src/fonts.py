"""Cut the two typefaces the loading screen needs down to what it sets, small enough to live in the page as data URIs.

Chivo 900 keeps only the letters of the wordmark (MODEL DEPOT); Anaheim is fixed at 500, 600 and 700 and keeps the
Latin letters, figures and the few signs the codes and readouts use. Writes fonts.css next to this file.
Needs fontTools; where brotli is missing, the shim folder stands in for it through Node. Run again only when the
fonts or the letters change; fonts.css is kept in the repository."""
import base64
import io
import os
import sys

BUILD = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(os.path.dirname(os.path.dirname(BUILD)), "web", "fonts")
try:
    import brotli  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(BUILD, "shim"))

from fontTools.ttLib import TTFont  # noqa: E402
from fontTools.varLib import instancer  # noqa: E402
from fontTools import subset  # noqa: E402

WORDMARK = "MODEL DEPOT"
CODES = ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 .,:;/-+%#()[]×·|_'"
         "\u2009\u00a0")


def cut(path, wght, text):
    font = TTFont(path)
    if "fvar" in font:
        font = instancer.instantiateVariableFont(font, {"wght": wght})
    opts = subset.Options()
    opts.layout_features = ["kern", "liga", "tnum"]
    opts.name_IDs = []
    opts.notdef_outline = False
    opts.hinting = False
    opts.desubroutinize = True
    opts.flavor = "woff2"
    sub = subset.Subsetter(opts)
    sub.populate(text=text)
    sub.subset(font)
    font.flavor = "woff2"
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue()


def face(family, weight, data):
    b64 = base64.b64encode(data).decode("ascii")
    return (f'@font-face{{font-family:"{family}";font-weight:{weight};font-style:normal;font-display:block;'
            f'src:url(data:font/woff2;base64,{b64}) format("woff2")}}')


def main():
    chivo = cut(os.path.join(FONTS, "Chivo-latin.woff2"), 900, WORDMARK)
    faces = [face("MD Mark", 900, chivo)]
    sizes = {"chivo 900": len(chivo)}
    for w in (500, 600, 700):
        data = cut(os.path.join(FONTS, "Anaheim-latin.woff2"), w, CODES)
        faces.append(face("MD Code", w, data))
        sizes[f"anaheim {w}"] = len(data)
    css = "\n".join(faces)
    with open(os.path.join(BUILD, "fonts.css"), "w", encoding="utf-8") as f:
        f.write(css + "\n")
    print(sizes, "fonts.css:", len(css.encode("utf-8")), "bytes")


if __name__ == "__main__":
    main()
