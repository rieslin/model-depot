"""Assemble the loading screen the Windows app shows while Model Depot starts (direction C, "index", the user's choice
of four drafts, 2026-10-08) into one self-contained page, desktop/loader.html: the fonts (fonts.css, made by fonts.py),
the shared frame (shell.css, shell.html, shell.js) and the page itself (loader.src.html), without the draft controls
and the comments. The window reads that file when it starts (MainForm.cs, ShowLoader).

    python desktop/loader-src/make.py           writes desktop/loader.html
    python desktop/loader-src/make.py --draft   also writes loader-draft.html here, with the draft controls (ready,
                                                fail, long wait, less motion, language) for looking at it in a browser
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "loader.html"

NOTE = ('<div class="note" data-out role="status"><span class="msg" data-msg></span><span class="msg2" data-msg2></span>'
        '<div class="fail" data-fail></div></div>')


def read(name):
    return (HERE / name).read_text(encoding="utf-8")


def assemble():
    text = read("loader.src.html")
    parts = {
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
    page = assemble()
    lean = strip(page)
    OUT.write_text(lean, encoding="utf-8", newline="\n")
    print(f"{OUT}: {len(lean.encode('utf-8')):,} bytes")
    if "--draft" in sys.argv:
        draft = HERE / "loader-draft.html"
        draft.write_text(page, encoding="utf-8", newline="\n")
        print(f"{draft}: {len(page.encode('utf-8')):,} bytes")


if __name__ == "__main__":
    main()
