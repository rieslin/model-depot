"""Where a download goes: the model folders from the settings and their subfolders, a suggestion among them, and the
checks that keep every write inside those folders.

The suggestion is counted on this computer, from the library itself (no AI model, nothing asked of a server):

  1. the folder chosen the last time a model of this type and base model was downloaded, if it is still there;
  2. the folder that already holds the most models of this type and base model;
  3. a subfolder named after the base model ("PONY", "Illustrious", "ILXL");
  4. the folder of this type that holds the most models of the type;
  5. the first folder of this type in the settings.
"""
import json
import os
import re
import uuid

from . import kinds

# civitai's model types and the ComfyUI folder type their files go to
_TYPE_BY_MODEL = {
    "checkpoint": "checkpoints", "lora": "loras", "locon": "loras", "dora": "loras", "lycoris": "loras",
    "textualinversion": "embeddings", "vae": "vae", "upscaler": "upscale_models", "controlnet": "controlnet",
    "hypernetwork": "hypernetworks",
}
# A file that is a part, not the model: where that part goes whatever page offers it
_TYPE_BY_FILE = {"vae": "vae", "text encoder": "text_encoders", "textencoder": "text_encoders",
                 "diffusion model": "diffusion_models", "unet": "diffusion_models", "negative": "embeddings"}

_RESERVED = {"con", "prn", "aux", "nul"} | {"com%d" % i for i in range(1, 10)} | {"lpt%d" % i for i in range(1, 10)}
_BAD_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_NAME = 180
_MAX_DEPTH = 4
_MAX_FOLDERS = 600


def target_type(model_type, file_type=None):
    """The ComfyUI folder type for a civitai file, or None when the program does not know where it belongs."""
    by_file = _TYPE_BY_FILE.get(str(file_type or "").strip().lower())
    if by_file:
        return by_file
    return _TYPE_BY_MODEL.get(str(model_type or "").strip().lower())


def safe_name(name, fallback="model.safetensors"):
    """A file or folder name that is valid on Windows, macOS and Linux and cannot point outside its folder."""
    name = os.path.basename(str(name or "").replace("\\", "/"))
    name = _BAD_CHARS_RE.sub("_", name).strip().rstrip(". ")
    stem, ext = os.path.splitext(name)
    if stem.lower() in _RESERVED or not stem or name in (".", ".."):
        name = "_" + name if name and name not in (".", "..") else fallback
        stem, ext = os.path.splitext(name)
    if len(name) > _MAX_NAME:
        name = stem[:_MAX_NAME - len(ext)] + ext
    return name


def safe_folder_name(name):
    """A new subfolder's name, or None when nothing usable is left."""
    name = _BAD_CHARS_RE.sub("", str(name or "")).strip().rstrip(". ")
    if not name or name in (".", "..") or name.lower() in _RESERVED:
        return None
    return name[:120]


def _real(path):
    return os.path.normcase(os.path.realpath(path))


def _inside(path, root):
    try:
        return os.path.commonpath([_real(path), _real(root)]) == _real(root)
    except ValueError:      # another drive
        return False


def roots(cfg, comfy_type=None):
    """The model folders from the settings that exist, for one ComfyUI type or for all, in settings order and each
    once: [{"path", "comfy_types"}]."""
    wanted = kinds.canonical_type(comfy_type) if comfy_type else None
    out, index = [], {}
    for folder in cfg.folders():
        ctype = kinds.canonical_type(folder["comfy_type"])
        key = os.path.normcase(os.path.normpath(folder["path"]))
        if key in index:
            if ctype not in out[index[key]]["comfy_types"]:
                out[index[key]]["comfy_types"].append(ctype)
            continue
        index[key] = len(out)
        out.append({"path": os.path.normpath(folder["path"]), "comfy_types": [ctype]})
    return [r for r in out if os.path.isdir(r["path"]) and (wanted is None or wanted in r["comfy_types"])]


def owning_root(cfg, path):
    """The settings folder that contains `path`, or None. The deepest one wins when folders nest."""
    best = None
    for root in roots(cfg):
        if _inside(path, root["path"]) and (best is None or len(root["path"]) > len(best["path"])):
            best = root
    return best


def check_destination(cfg, path):
    """The folder a download may be written to: an existing folder inside one of the settings folders. Returns
    the normalized path; raises ValueError("outside" | "missing") otherwise."""
    path = os.path.normpath(os.path.expanduser(str(path or "").strip().strip('"')))
    if not path or not os.path.isabs(path):
        raise ValueError("outside")
    if owning_root(cfg, path) is None:
        raise ValueError("outside")
    if not os.path.isdir(path):
        raise ValueError("missing")
    return path


def subfolders(root, max_depth=_MAX_DEPTH, limit=_MAX_FOLDERS):
    """The folder and the folders under it, parents before children, names in order. Links are not followed."""
    out, stack = [], [(root, 0)]
    while stack and len(out) < limit:
        path, depth = stack.pop()
        out.append(path)
        if depth >= max_depth:
            continue
        try:
            with os.scandir(path) as it:
                names = sorted((e.name for e in it if e.is_dir(follow_symlinks=False) and not e.name.startswith(".")),
                               key=str.lower, reverse=True)
        except OSError:
            continue
        stack.extend((os.path.join(path, n), depth + 1) for n in names)
    return out


def listing(cfg, counts, comfy_type):
    """The choices for the folder picker: the settings folders of this type with their subfolders, then every other
    settings folder. counts: {normcased folder path: model files directly in it}."""
    def entry(path, root, group):
        rel = os.path.relpath(path, root["path"])
        return {"path": path, "root": root["path"], "rel": "" if rel == "." else rel, "group": group,
                "root_label": os.path.basename(root["path"].rstrip("\\/")) or root["path"],
                "comfy_types": root["comfy_types"], "files": counts.get(os.path.normcase(path), 0)}

    own = roots(cfg, comfy_type) if comfy_type else []
    own_keys = {os.path.normcase(r["path"]) for r in own}
    out = [entry(p, r, "type") for r in own for p in subfolders(r["path"])]
    out += [entry(r["path"], r, "other") for r in roots(cfg) if os.path.normcase(r["path"]) not in own_keys]
    return out


def suggest(cfg, comfy_type, family, family_words, dir_counts, kind_counts, remembered):
    """The suggested folder for a download and why: {"path", "reason", "n", "total"} or None.

    dir_counts:  {folder path: models of this kind and base model directly in it}
    kind_counts: {folder path: models of this kind directly in it}
    family_words: names a folder for this base model may have ("pony"; "illustrious", "ilxl")
    remembered:  the folder chosen last time for this type and base model, or None
    """
    own = roots(cfg, comfy_type)
    if not own:
        return None

    def under_own(path):
        return any(_inside(path, r["path"]) for r in own)

    if remembered and os.path.isdir(remembered) and under_own(remembered):
        return {"path": os.path.normpath(remembered), "reason": "last", "n": 0, "total": 0}
    same = {p: n for p, n in dir_counts.items() if n and under_own(p)}
    if same and family and family != "unknown":
        path = max(same, key=lambda p: (same[p], -len(p)))
        return {"path": path, "reason": "library", "n": same[path], "total": sum(same.values())}
    words = {w.lower() for w in family_words if w}
    if words:
        for root in own:
            for path in subfolders(root["path"], max_depth=2):
                if os.path.basename(path).lower() in words and path != root["path"]:
                    return {"path": path, "reason": "name", "n": 0, "total": 0}
    by_root = {r["path"]: sum(n for p, n in kind_counts.items() if _inside(p, r["path"])) for r in own}
    best = max(own, key=lambda r: by_root[r["path"]])
    if by_root[best["path"]]:
        return {"path": best["path"], "reason": "type_root", "n": by_root[best["path"]], "total": 0}
    return {"path": own[0]["path"], "reason": "first", "n": 0, "total": 0}


def unique_path(folder, name, taken=()):
    """folder/name, or folder/"stem (2).ext" and so on when that file is already there or already promised to another
    download (`taken`: normcased paths)."""
    stem, ext = os.path.splitext(name)
    for n in range(1, 1000):
        candidate = os.path.join(folder, name if n == 1 else "%s (%d)%s" % (stem, n, ext))
        key = os.path.normcase(candidate)
        if key not in taken and not os.path.exists(candidate) and not os.path.exists(candidate + ".part"):
            return candidate
    raise ValueError("no free name")


class Remembered:
    """The folder chosen last time, per ComfyUI type and base model. A small file in the data folder: a preference,
    not part of the index, so rebuilding the index keeps it."""

    def __init__(self, path):
        self.path = path
        self._data = None

    def _load(self):
        if self._data is None:
            try:
                with open(self.path, encoding="utf-8") as f:
                    data = json.load(f)
                self._data = data if isinstance(data, dict) else {}
            except (OSError, ValueError):
                self._data = {}
        return self._data

    def get(self, comfy_type, family):
        value = self._load().get("%s|%s" % (comfy_type, family or "unknown"))
        return value if isinstance(value, str) else None

    def put(self, comfy_type, family, folder):
        data = self._load()
        data["%s|%s" % (comfy_type, family or "unknown")] = folder
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = "%s.%s.tmp" % (self.path, uuid.uuid4().hex[:8])
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)
