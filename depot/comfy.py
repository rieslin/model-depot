"""Work out a ComfyUI install's model folders by reading its files. ComfyUI is never started or imported.

ComfyUI builds its folder list from three places, and the same three are read here:
  1. its built-in list (folder_paths.py): one or two sub-folders of the models folder per model type;
  2. the models folder itself, which the launch options --models-directory / --base-directory can move. Those
     options live in whatever script starts ComfyUI, so they can only be guessed from the launch scripts found next
     to it; the wizard shows the guess and lets the user type the folder in;
  3. extra_model_paths.yaml, which adds folders (an old WebUI's, for instance) to the types.
Custom nodes can register more types while ComfyUI runs; those cannot be known from files. Sub-folders of the
models folder that nothing claims are therefore offered too, as type "other".
"""
import os
import re

from . import kinds, scan, yamlish

# ComfyUI's built-in model folders (type, sub-folders of the models folder), in its own order. Used when the
# install's folder_paths.py cannot be read; read_builtin_types() prefers the install's own list.
BUILTIN_TYPES = (
    ("checkpoints", ("checkpoints",)),
    ("loras", ("loras",)),
    ("vae", ("vae",)),
    ("text_encoders", ("text_encoders", "clip")),
    ("diffusion_models", ("unet", "diffusion_models")),
    ("clip_vision", ("clip_vision",)),
    ("style_models", ("style_models",)),
    ("embeddings", ("embeddings",)),
    ("vae_approx", ("vae_approx",)),
    ("controlnet", ("controlnet", "t2i_adapter")),
    ("gligen", ("gligen",)),
    ("upscale_models", ("upscale_models",)),
    ("latent_upscale_models", ("latent_upscale_models",)),
    ("hypernetworks", ("hypernetworks",)),
    ("photomaker", ("photomaker",)),
    ("model_patches", ("model_patches",)),
    ("audio_encoders", ("audio_encoders",)),
)
# Types that are not lists of model files (ComfyUI lists .yaml files or whole folders for them).
NOT_MODEL_TYPES = frozenset({"configs", "custom_nodes", "datasets", "classifiers", "diffusers"})

_REGISTRATION_RE = re.compile(r"""^folder_names_and_paths\[["'](?P<type>[^"']+)["']\]\s*=\s*\(\[(?P<paths>.*?)\]\s*,\s*(?P<exts>.+)\)\s*$""")
_MODELS_SUB_RE = re.compile(r"""os\.path\.join\(\s*models_dir\s*,\s*["']([^"']+)["']\s*\)""")
_OPTION_RE = re.compile(r"""--(?P<name>models-directory|base-directory|output-directory|extra-model-paths-config)(?:=|\s+)(?P<rest>.*)""")
# Models saved from inside ComfyUI land in its output folder; ComfyUI adds these sub-folders to the types at start-up
# (main.py), after everything else.
OUTPUT_MODEL_FOLDERS = (("checkpoints", "checkpoints"), ("clip", "clip"), ("vae", "vae"),
                        ("diffusion_models", "diffusion_models"), ("loras", "loras"))
_VALUE_RE = re.compile(r"""\s*(?:"([^"]*)"|'([^']*)'|([^\s"']+))""")
_LAUNCHER_EXTENSIONS = (".bat", ".cmd", ".sh", ".ps1")
_MAX_LAUNCHER_BYTES = 256 * 1024


def locate(path):
    """What `path` is: {"layout", "app_dir", "models_dir", "launcher_dirs"} or None.

    layout: "portable" (the folder holding ComfyUI\\ and the run_*.bat files), "app" (the ComfyUI folder itself),
    "base" (a folder with models\\ in it, as the desktop app keeps), "models" (a models folder).
    """
    path = os.path.normpath(os.path.expanduser(os.path.expandvars(str(path or "").strip().strip('"'))))
    if not path or not os.path.isdir(path):
        return None
    inner = os.path.join(path, "ComfyUI")
    if os.path.isfile(os.path.join(inner, "folder_paths.py")):
        return {"layout": "portable", "app_dir": inner, "models_dir": os.path.join(inner, "models"),
                "launcher_dirs": [path, inner]}
    if os.path.isfile(os.path.join(path, "folder_paths.py")):
        return {"layout": "app", "app_dir": path, "models_dir": os.path.join(path, "models"),
                "launcher_dirs": [os.path.dirname(path), path]}
    if os.path.isdir(os.path.join(path, "models")):
        return {"layout": "base", "app_dir": None, "models_dir": os.path.join(path, "models"), "launcher_dirs": [path]}
    if any(os.path.isdir(os.path.join(path, sub)) for sub in ("checkpoints", "loras", "diffusion_models", "vae")):
        return {"layout": "models", "app_dir": None, "models_dir": path, "launcher_dirs": []}
    return None


def read_builtin_types(app_dir):
    """ComfyUI's built-in (type, sub-folders) list, read from the install's own folder_paths.py so that it follows
    whatever version is installed. Falls back to BUILTIN_TYPES."""
    found = []
    try:
        with open(os.path.join(app_dir or "", "folder_paths.py"), encoding="utf-8") as f:
            for line in f:
                m = _REGISTRATION_RE.match(line.strip())
                if not m or "supported_pt_extensions" not in m.group("exts"):
                    continue
                subs = tuple(_MODELS_SUB_RE.findall(m.group("paths")))
                if subs and m.group("type") not in NOT_MODEL_TYPES:
                    found.append((m.group("type"), subs))
    except OSError:
        found = []
    return tuple(found) if len(found) >= 8 else BUILTIN_TYPES


def _option_values(rest, many):
    values = []
    pos = 0
    while True:
        m = _VALUE_RE.match(rest, pos)
        if not m:
            break
        value = next(g for g in m.groups() if g is not None)
        if value.startswith("--") or value in ("%*", "$@", '"$@"'):
            break
        values.append(value)
        pos = m.end()
        if not many:
            break
    return values


def _expand(value, script_dir):
    """A path written in a launch script, made absolute. None when it still holds a variable we cannot resolve."""
    value = re.sub(r"%~dp0\\?", lambda _m: script_dir + os.sep, value, flags=re.I)
    value = value.replace("$PSScriptRoot", script_dir)
    value = os.path.expandvars(value)
    if "%" in value or "$" in value:
        return None
    if not os.path.isabs(value):
        value = os.path.join(script_dir, value)
    return os.path.normpath(value)


def launcher_hints(dirs):
    """Launch options found in the scripts next to ComfyUI: [{"file", "option", "value"}].

    Scripts usually `cd` to their own folder first, so relative paths are taken from the script's folder.
    """
    hints, seen = [], set()
    for folder in dirs:
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            if not name.lower().endswith(_LAUNCHER_EXTENSIONS):
                continue
            path = os.path.join(folder, name)
            try:
                if os.path.getsize(path) > _MAX_LAUNCHER_BYTES:
                    continue
                with open(path, encoding="utf-8", errors="replace") as f:
                    lines = f.read().splitlines()
            except OSError:
                continue
            for line in lines:
                stripped = line.strip()
                if stripped.lower().startswith(("rem ", "::", "#", "echo ")):
                    continue
                for m in _OPTION_RE.finditer(stripped):
                    option = m.group("name")
                    for value in _option_values(m.group("rest"), many=option == "extra-model-paths-config"):
                        full = _expand(value, folder)
                        if full and (path, option, full) not in seen:
                            seen.add((path, option, full))
                            hints.append({"file": path, "option": "--" + option, "value": full})
    return hints


def read_extra_paths(yaml_path):
    """The folders an extra_model_paths.yaml adds: ([(type, folder, is_default)], error). Follows ComfyUI's reading:
    paths are relative to base_path when the section has one, otherwise to the yaml file's folder."""
    try:
        with open(yaml_path, encoding="utf-8-sig") as f:
            data = yamlish.loads(f.read())
    except OSError as e:
        return [], str(e)
    except yamlish.YamlishError as e:
        return [], str(e)
    if data is None:
        return [], None
    if not isinstance(data, dict):
        return [], "unexpected content"
    yaml_dir = os.path.dirname(os.path.abspath(yaml_path))
    out = []
    for section in data.values():
        if not isinstance(section, dict):
            continue
        section = dict(section)
        base = section.pop("base_path", None)
        if base:
            base = os.path.expandvars(os.path.expanduser(str(base)))
            if not os.path.isabs(base):
                base = os.path.abspath(os.path.join(yaml_dir, base))
        is_default = section.pop("is_default", False) is True
        for ctype, value in section.items():
            if not isinstance(value, str):
                continue
            for line in value.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if base:
                    full = os.path.join(base, line)
                elif os.path.isabs(line):
                    full = line
                else:
                    full = os.path.abspath(os.path.join(yaml_dir, line))
                out.append((str(ctype), os.path.normpath(full), is_default))
    return out, None


def detect(comfy_dir, models_dir=""):
    """Everything the wizard shows after the user points at ComfyUI.

    Returns {"found", "layout", "app_dir", "models_dir", "models_dir_source", "hints", "extra_files", "folders"}.
    folders: [{"path", "comfy_type", "source", "exists", "files", "bytes"}] in ComfyUI's search order per type;
    source is "builtin", "extra" (from a yaml), "output" (where ComfyUI saves models made inside it) or "unlisted"
    (a sub-folder of the models folder nothing claims).
    """
    where = locate(comfy_dir)
    manual = os.path.normpath(os.path.expanduser(os.path.expandvars(str(models_dir or "").strip().strip('"')))) \
        if str(models_dir or "").strip() else ""
    result = {"found": where is not None, "layout": where["layout"] if where else None,
              "app_dir": where["app_dir"] if where else None, "models_dir": "", "models_dir_source": None,
              "hints": [], "extra_files": [], "folders": []}
    if where is None and not manual:
        return result

    hints = launcher_hints(where["launcher_dirs"]) if where else []
    result["hints"] = hints
    hinted_models = [h["value"] for h in hints if h["option"] == "--models-directory"]
    hinted_base = [h["value"] for h in hints if h["option"] == "--base-directory"]
    if manual:
        chosen, source = manual, "manual"
    elif hinted_models:
        chosen, source = hinted_models[0], "launcher"
    elif hinted_base:
        chosen, source = os.path.join(hinted_base[0], "models"), "launcher"
    else:
        chosen, source = where["models_dir"], "default"
    result["models_dir"], result["models_dir_source"] = chosen, source

    # ComfyUI's registrations, in its order: built-in folders first, then each yaml in turn.
    registered = {}     # type -> [folder]
    sources = {}        # (type, normcased folder) -> source
    for ctype, subs in read_builtin_types(where["app_dir"] if where else None):
        for sub in subs:
            folder = os.path.normpath(os.path.join(chosen, sub))
            registered.setdefault(ctype, []).append(folder)
            sources[(ctype, os.path.normcase(folder))] = "builtin"

    yaml_files = []
    if where and where["app_dir"]:
        own = os.path.join(where["app_dir"], "extra_model_paths.yaml")
        if os.path.isfile(own):
            yaml_files.append(own)
    for h in hints:
        if h["option"] == "--extra-model-paths-config" and h["value"] not in yaml_files and os.path.isfile(h["value"]):
            yaml_files.append(h["value"])
    for yaml_path in yaml_files:
        entries, error = read_extra_paths(yaml_path)
        result["extra_files"].append({"path": yaml_path, "ok": error is None, "error": error, "entries": len(entries)})
        for raw_type, folder, is_default in entries:
            ctype = kinds.canonical_type(raw_type)
            if ctype in NOT_MODEL_TYPES:
                continue
            paths = registered.setdefault(ctype, [])
            cased = [os.path.normcase(p) for p in paths]
            key = os.path.normcase(folder)
            if key in cased:
                if is_default and cased[0] != key:
                    paths.insert(0, paths.pop(cased.index(key)))
                continue
            sources[(ctype, key)] = "extra"
            if is_default:
                paths.insert(0, folder)
            else:
                paths.append(folder)

    hinted_output = [h["value"] for h in hints if h["option"] == "--output-directory"]
    if hinted_output:
        output_dir = hinted_output[0]
    elif hinted_base:
        output_dir = os.path.join(hinted_base[0], "output")
    elif where and where["app_dir"]:
        output_dir = os.path.join(where["app_dir"], "output")
    else:
        output_dir = None
    if output_dir:
        for raw_type, sub in OUTPUT_MODEL_FOLDERS:
            ctype = kinds.canonical_type(raw_type)
            folder = os.path.normpath(os.path.join(output_dir, sub))
            paths = registered.setdefault(ctype, [])
            if os.path.normcase(folder) not in [os.path.normcase(p) for p in paths]:
                paths.append(folder)
                sources[(ctype, os.path.normcase(folder))] = "output"

    claimed = set()
    counts = {}

    def count(folder):
        key = os.path.normcase(folder)
        if key not in counts:
            counts[key] = scan.count_models(folder) if os.path.isdir(folder) else (0, 0)
        return counts[key]

    for ctype, paths in registered.items():
        for folder in paths:
            claimed.add(os.path.normcase(folder))
            files, size = count(folder)
            result["folders"].append({"path": folder, "comfy_type": ctype,
                                      "source": sources.get((ctype, os.path.normcase(folder)), "builtin"),
                                      "exists": os.path.isdir(folder), "files": files, "bytes": size})

    # Sub-folders of the models folder that no type claims: custom nodes' own folders, an old WebUI's leftovers.
    try:
        subs = sorted(os.listdir(chosen))
    except OSError:
        subs = []
    for sub in subs:
        folder = os.path.join(chosen, sub)
        if not os.path.isdir(folder) or os.path.normcase(folder) in claimed:
            continue
        files, size = count(folder)
        if files:
            result["folders"].append({"path": folder, "comfy_type": sub, "source": "unlisted",
                                      "exists": True, "files": files, "bytes": size})
    return result
