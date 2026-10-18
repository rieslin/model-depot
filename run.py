"""Start Model Depot.

    python run.py            start the server (settings and data live in the data folder)
    python run.py --stop     stop the running server
    python run.py --check    check that this Python has what the program needs

Any Python 3.10 or newer with aiohttp and Pillow will do; the Python inside a ComfyUI portable build has both.
On Windows, double-click 启动.cmd / Start.cmd instead: it finds a suitable Python and opens the page.
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
# A portable Python (ComfyUI's) does not put the script's folder on sys.path, so it goes in front explicitly.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from depot.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
