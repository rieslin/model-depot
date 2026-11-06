"""Assemble the loading screen the Windows app shows while Model Depot starts (direction C, "index", the user's choice
of four drafts, 2026-10-08) into one self-contained page, desktop/loader.html: the fonts (fonts.css, made by fonts.py),
the shared frame (shell.css, shell.html, shell.js) and the page itself (loader.src.html), without the draft controls
and the comments, with the base models it shows before the library was ever shown (from depot/catalog/families.json).
The window reads that file when it starts (MainForm.cs, ShowLoader) and gives it its sentences
(scripts/launcher-strings.json, in LOADER_WORDS).

    python desktop/loader-src/make.py           writes desktop/loader.html
    python desktop/loader-src/make.py --draft   also writes loader-draft.html here, with the draft controls (ready,
                                                fail, long wait, less motion, language), the sentences and a sample
                                                log, for looking at it in a browser
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
OUT = HERE.parent / "loader.html"

NOTE = ('<div class="note" data-out role="status"><span class="msg" data-msg></span><span class="msg2" data-msg2></span>'
        '<div class="fail" data-fail></div></div>')

# The base models shown before the library was ever shown, in this order: families of depot/catalog/families.json,
# with their codes, names and the architecture they share.
DEFAULT_FAMILIES = ("krea2", "anima", "qwen", "zimage", "illustrious", "sdxl", "flux2")

# the draft only: a log tail of the kind the window passes (the message is the program's own, depot/main.py)
DRAFT_LOG = ("2026-10-08 09:14:03,402 INFO depot: settings read from data\\config.json\n"
             "2026-10-08 09:14:03,618 ERROR depot: port 8194 is already in use: is Model Depot already running?")


def read(name):
    return (HERE / name).read_text(encoding="utf-8")


def in_script(value):
    """A value as JavaScript that a <script> element can hold."""
    return json.dumps(value, ensure_ascii=True).replace("</", "<\\/")


def default_families():
    """[[code, name, architecture], ...] of DEFAULT_FAMILIES (architecture only where a family shares another's)."""
    families = json.loads((REPO / "depot" / "catalog" / "families.json").read_text(encoding="utf-8"))["families"]
    rows = []
    for key in DEFAULT_FAMILIES:
        family = families[key]
        row = [family["code"], family["name"]]
        if family.get("arch"):
            row.append(families[family["arch"]]["name"])
        rows.append(row)
    return rows


def draft_words():
    """What the window gives the page, for a browser: the sentences in the language of the address, a sample log."""
    rows = json.loads((REPO / "scripts" / "launcher-strings.json").read_text(encoding="utf-8"))
    return ('<script>(() => { const rows = %s; const en = new URLSearchParams(location.search).get("lang") === "en";'
            ' const words = { lang: en ? "en" : "zh" }; for (const k in rows) words[k] = rows[k][en ? 1 : 0];'
            ' window.LOADER_WORDS = words; window.LOADER_SAMPLE_LOG = %s; })();</script>' % (in_script(rows), in_script(DRAFT_LOG)))


def assemble(draft=False):
    text = read("loader.src.html")
    parts = {
        "<!--DRAFT_WORDS-->": draft_words() if draft else "",
        "<!--DEFAULT_FAMILIES-->": "<script>window.LOADER_DEFAULT_FAMILIES = %s;</script>" % in_script(default_families()),
        "<!--FONTS-->": "<style>" + read("fonts.css").strip() + "</style>",
        "<!--SHELL_CSS-->": "<style>\n" + read("shell.css") + "</style>",
        "<!--SHELL_HTML-->": read("shell.html"),
        "<!--NOTE-->": NOTE,
        "<!--SHELL_JS-->": "<script>\n" + read("shell.js") + "</script>",
    }
    for k, v in parts.items():
        text = text.replace(k, v)
    return text


def strip(text):
    """The page as the app carries it: no draft controls, no comments, no blank lines."""
    text = re.sub(r'<div class="proto".*?</div>\n', "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(?m)^\s*//[^\n]*\n", "", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text


def main():
    lean = strip(assemble())
    OUT.write_text(lean, encoding="utf-8", newline="\n")
    print(f"{OUT}: {len(lean.encode('utf-8')):,} bytes")
    if "--draft" in sys.argv:
        page = assemble(draft=True)
        draft = HERE / "loader-draft.html"
        draft.write_text(page, encoding="utf-8", newline="\n")
        print(f"{draft}: {len(page.encode('utf-8')):,} bytes")


if __name__ == "__main__":
    main()
