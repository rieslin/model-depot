"""Where things live. Nothing here names a particular computer: the data folder is the only root, and it comes from
the environment, from what the setup wrote next to the program, or sits next to the program."""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT, "web")
ENV_DATA = "MODEL_DEPOT_DATA"
# Written by the Windows setup next to run.py: where it put the data folder, ComfyUI, the model library, the local
# model's runtime and the inbox folders (see install.py). A copy run from the repository has none.
INSTALL_FILE = os.path.join(ROOT, "install.json")


def install_info(path=None):
    """What the setup wrote (install.json), or {} when the program was not installed by it."""
    try:
        with open(path or INSTALL_FILE, encoding="utf-8-sig") as f:
            info = json.load(f)
    except (OSError, ValueError):
        return {}
    return info if isinstance(info, dict) else {}


def data_dir(override=None):
    """The data folder: the --data option, then MODEL_DEPOT_DATA, then the one the setup chose, then data/ inside the
    project."""
    p = override or os.environ.get(ENV_DATA) or str(install_info().get("data_dir") or "") or os.path.join(ROOT, "data")
    return os.path.abspath(os.path.expanduser(os.path.expandvars(p)))


class DataPaths:
    """Paths inside one data folder."""

    def __init__(self, root):
        self.root = root
        self.config = os.path.join(root, "config.json")
        self.index = os.path.join(root, "index.sqlite")
        self.logs = os.path.join(root, "logs")
        self.server_info = os.path.join(root, "server.json")   # pid and port of the running server, for the stop script
        self.default_library = os.path.join(root, "library")   # per-model records and covers, unless config moves them
        self.cache = os.path.join(root, "cache")               # what can be fetched again (example images); never synced
        self.download_folders = os.path.join(root, "download-folders.json")    # the folder chosen last, per type

    def ensure(self):
        os.makedirs(self.root, exist_ok=True)
        os.makedirs(self.logs, exist_ok=True)
