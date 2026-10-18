"""Draws the program's icon, desktop/ModelDepot.ico. Every size in the file is drawn at its own size by a browser
(Chrome or Edge, headless) from the same vector drawing, so the small sizes are not shrunk from a large picture.

    python desktop/make_icon.py                   the icon in use (DESIGN below) into desktop/ModelDepot.ico
    python desktop/make_icon.py --sheet out.png   every design side by side, large and at taskbar sizes

The drawings use the wordmark's typeface (web/fonts/Chivo-latin.woff2) and the display face (Anaheim), embedded in the
page. The browser is found in its usual places, or named by the environment variable MODEL_DEPOT_BROWSER.
"""
import base64
import os
import subprocess
import sys
import tempfile

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICO = os.path.join(ROOT, "desktop", "ModelDepot.ico")
FONTS = (("Chivo", os.path.join(ROOT, "web", "fonts", "Chivo-latin.woff2"), "100 900"),
         ("Anaheim", os.path.join(ROOT, "web", "fonts", "Anaheim-latin.woff2"), "400 800"))
SIZES = (256, 64, 48, 40, 32, 24, 20, 16)

INK, PAPER, RED, ON_BLOCK = "#161718", "#f3f3f2", "#e60000", "#f4f4f3"
LIT, MID = "#34383d", "#55595d"       # the index item in view; a card behind another

# Each design is drawn on a 256 x 256 grid. A design's name is what the comparison sheet calls it.
DESIGNS = {
    "o": ("方案一：红色 O",
          '<rect x="16" y="16" width="224" height="224" fill="{ink}"/>'
          '<text x="128" y="133" text-anchor="middle" dominant-baseline="central" font-family="Chivo" font-weight="900" '
          'font-size="200" fill="{red}">O</text>'),
    "card": ("方案二：图鉴卡",
             '<rect x="16" y="16" width="224" height="224" fill="{ink}"/>'
             '<rect x="79" y="50" width="98" height="157" fill="{paper}"/>'
             '<path d="M143 50H177V84Z" fill="{red}"/>'),
    "md": ("方案三：MD 选中卡",
           '<rect x="16" y="16" width="224" height="224" fill="{ink}"/>'
           '<rect x="38" y="38" width="180" height="180" fill="{paper}"/>'
           '<text x="128" y="133" text-anchor="middle" dominant-baseline="central" font-family="Chivo" font-weight="900" '
           'font-size="100" letter-spacing="-3" fill="{ink}">MD</text>'
           '<path d="M186 16H240V70Z" fill="{red}"/>'),
    # Second round (2026-10-02, the user asked for more): the library's own marks.
    "index": ("方案四：索引菱形",
              '<rect x="16" y="16" width="224" height="224" fill="{ink}"/>'
              '<rect x="96" y="54" width="118" height="36" fill="{lit}"/>'
              '<rect x="96" y="110" width="118" height="36" fill="{paper}"/>'
              '<rect x="96" y="166" width="118" height="36" fill="{lit}"/>'
              '<path d="M62 102L88 128L62 154L36 128Z" fill="{red}"/>'),
    "stack": ("方案五：版本叠卡",
              '<rect x="16" y="16" width="224" height="224" fill="{ink}"/>'
              '<rect x="104" y="40" width="102" height="146" fill="{mid}"/>'
              '<rect x="58" y="74" width="102" height="146" fill="{paper}"/>'
              '<path d="M122 74H160V112Z" fill="{red}"/>'),
    # three cards to a row: two by two would be the shape of another company's logo
    "wall": ("方案六：卡片墙",
             '<rect x="16" y="16" width="224" height="224" fill="{ink}"/>'
             '<rect x="38" y="46" width="52" height="76" fill="{paper}"/>'
             '<rect x="102" y="46" width="52" height="76" fill="{paper}"/>'
             '<rect x="166" y="46" width="52" height="76" fill="{red}"/>'
             '<rect x="38" y="134" width="52" height="76" fill="{paper}"/>'
             '<rect x="102" y="134" width="52" height="76" fill="{paper}"/>'
             '<rect x="166" y="134" width="52" height="76" fill="{paper}"/>'),
    "plate": ("方案七：编号牌",
              '<rect x="16" y="16" width="224" height="224" fill="{ink}"/>'
              '<text x="128" y="112" text-anchor="middle" dominant-baseline="central" font-family="Anaheim" '
              'font-weight="800" font-size="150" letter-spacing="2" fill="{paper}">MD</text>'
              '<rect x="54" y="184" width="148" height="20" fill="{red}"/>'),
    "d": ("方案八：D 与红色 O",
          '<rect x="16" y="16" width="224" height="224" fill="{ink}"/>'
          '<path d="M56 44H136A84 84 0 0 1 136 212H56Z" fill="{paper}"/>'
          '<ellipse cx="140" cy="128" rx="38" ry="46" fill="{red}"/>'),
    "paper": ("方案九：白底红色 O",
              '<rect x="16" y="16" width="224" height="224" fill="{ink}"/>'
              '<rect x="36" y="36" width="184" height="184" fill="{paper}"/>'
              '<text x="128" y="133" text-anchor="middle" dominant-baseline="central" font-family="Chivo" font-weight="900" '
              'font-size="168" fill="{red}">O</text>'),
}
DESIGN = "stack"     # the user chose option five, 2026-10-02


def svg(name, size):
    body = DESIGNS[name][1].format(ink=INK, paper=PAPER, red=RED, on_block=ON_BLOCK, lit=LIT, mid=MID)
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="%d" height="%d">%s</svg>' % (size, size, body)


def font_face():
    rules = []
    for family, path, weights in FONTS:
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
        rules.append('@font-face{font-family:"%s";src:url(data:font/woff2;base64,%s) format("woff2");font-weight:%s}'
                     % (family, data, weights))
    return "".join(rules)


def find_browser():
    named = os.environ.get("MODEL_DEPOT_BROWSER")
    if named and os.path.isfile(named):
        return named
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
        for rel in (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe"):
            if base and os.path.isfile(os.path.join(base, rel)):
                return os.path.join(base, rel)
    sys.exit("No Chrome or Edge found; name one in MODEL_DEPOT_BROWSER.")


def shoot(html, width, height, out):
    """A screenshot of the page at 1:1, on a transparent ground."""
    with tempfile.TemporaryDirectory() as tmp:
        page = os.path.join(tmp, "page.html")
        with open(page, "w", encoding="utf-8") as f:
            f.write(html)
        subprocess.run([find_browser(), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                        "--no-default-browser-check", "--force-device-scale-factor=1", "--default-background-color=00000000",
                        "--virtual-time-budget=3000", "--user-data-dir=" + os.path.join(tmp, "profile"),
                        "--window-size=%d,%d" % (width, height), "--screenshot=" + out,
                        "file:///" + page.replace("\\", "/")],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)


def make_icon(name, out):
    # every size on one page, side by side, cut apart afterwards
    x, places, parts = 0, [], []
    for size in SIZES:
        places.append((size, x))
        parts.append('<div style="position:absolute;left:%dpx;top:0">%s</div>' % (x, svg(name, size)))
        x += size + 8
    html = "<!doctype html><html><head><style>%s html,body{margin:0;background:transparent}</style></head><body>%s</body></html>" % (
        font_face(), "".join(parts))
    with tempfile.TemporaryDirectory() as tmp:
        png = os.path.join(tmp, "all.png")
        shoot(html, x, 256, png)
        sheet = Image.open(png).convert("RGBA")
        frames = [sheet.crop((left, 0, left + size, size)) for size, left in places]
    frames[0].save(out, format="ICO", sizes=[(s, s) for s in SIZES], append_images=frames[1:])
    print("icon written:", out)


SHEET_COLUMNS = 3


def make_sheet(out, names=None):
    # each design large, then at taskbar sizes on the paper ground and on a dark taskbar, three to a row
    names = names or list(DESIGNS)
    cols = []
    for name in names:
        label = DESIGNS[name][0] + ("（当前使用）" if name == DESIGN else "")
        smalls = "".join('<span style="display:inline-block;margin-right:14px;vertical-align:middle">%s</span>' % svg(name, s)
                         for s in (64, 48, 32, 24, 16))
        cols.append('<div class="col"><div class="big">%s</div><p>%s</p>'
                    '<div class="row light">%s</div><div class="row dark">%s</div></div>' % (svg(name, 192), label, smalls, smalls))
    css = (font_face() + "html,body{margin:0;background:%s}body{display:grid;grid-template-columns:repeat(%d,420px);gap:36px 28px;"
           "padding:28px;font:600 18px 'Microsoft YaHei UI','Segoe UI',sans-serif;color:%s}.big{background:#e7e8e7;padding:24px 0;"
           "text-align:center}p{margin:14px 0 10px}.row{padding:14px 16px;line-height:0}.light{background:#e7e8e7}"
           ".dark{background:#1f1f1f;margin-top:8px}") % (PAPER, SHEET_COLUMNS, INK)
    html = "<!doctype html><html><head><style>%s</style></head><body>%s</body></html>" % (css, "".join(cols))
    columns = min(SHEET_COLUMNS, len(names))
    rows = -(-len(names) // SHEET_COLUMNS)
    shoot(html, 28 * 2 + 420 * columns + 28 * (columns - 1), 28 * 2 + 490 * rows + 36 * (rows - 1), out)
    print("sheet written:", out)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--sheet":
        make_sheet(os.path.abspath(sys.argv[2]), sys.argv[3].split(",") if len(sys.argv) > 3 else None)
    else:
        make_icon(DESIGN, ICO)
