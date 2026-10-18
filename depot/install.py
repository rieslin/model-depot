"""The first start of a copy the Windows setup installed: the settings start from what the setup chose (install.json,
see paths.install_info), so that the setup wizard opens with ComfyUI's model folders listed, the local model's runtime
in place and the inbox folders set. Nothing is saved here; the wizard saves when the user confirms it.

install.json, written by the setup:
    {"version", "install_dir", "data_dir", "python", "comfyui_dir", "models_dir", "llm_dir",
     "inbox": [folder, ...], "inbox_once": [folder, ...], "language": "zh" | "en"}
"""
import logging
import os

from . import comfy, reader

log = logging.getLogger("depot.install")


def llm_files(llm_dir):
    """The GGUF files in the local model's folder (<llm_dir>/models), largest first."""
    folder = os.path.join(llm_dir, "models")
    try:
        names = [n for n in os.listdir(folder) if n.lower().endswith(".gguf") and not n.lower().startswith("mmproj")]
    except OSError:
        return []
    paths = [os.path.join(folder, n) for n in names if os.path.isfile(os.path.join(folder, n))]
    return sorted(paths, key=os.path.getsize, reverse=True)


def pick_llm(paths):
    """The file to read the authors' notes with: one of the recommended models when it is there, else the largest."""
    known = {m["file"].lower() for m in reader.MODELS.values()}
    for path in paths:
        if os.path.basename(path).lower() in known:
            return path
    return paths[0] if paths else ""


def seed(cfg, info):
    """Lay what the setup chose over the default settings of a copy that has no settings yet. Returns True when
    anything was set."""
    if cfg.exists or not info:
        return False
    changes = {}
    if info.get("language") in ("zh", "en"):
        changes["language"] = info["language"]
    comfy_dir, models_dir = str(info.get("comfyui_dir") or ""), str(info.get("models_dir") or "")
    if comfy_dir:
        found = comfy.detect(comfy_dir, models_dir)
        if found["found"]:
            changes["comfyui"] = {"dir": comfy_dir, "models_dir": models_dir}
            changes["folders"] = [{"path": f["path"], "comfy_type": f["comfy_type"], "enabled": bool(f["exists"])}
                                  for f in found["folders"]]
    llm_dir = str(info.get("llm_dir") or "")
    if llm_dir:
        server = os.path.join(llm_dir, "llama.cpp", "llama-server.exe")
        managed = {"server": server if os.path.isfile(server) else ""}
        model = pick_llm(llm_files(llm_dir))
        rd = {"install_dir": llm_dir, "managed": managed}
        if managed["server"] and model:
            managed["model_file"] = model
            rd["mode"] = "managed"
        changes["reader"] = rd
    changes["inbox"] = {"dirs": [str(p) for p in info.get("inbox") or [] if p],
                        "once": [str(p) for p in info.get("inbox_once") or [] if p]}
    problems = cfg.update(changes)
    if problems:
        log.warning("the setup's choices were not all usable: %s", problems)
        # what can be kept is kept: everything but the parts named in the problems
        bad = {field.split(".")[0] for field, _code in problems}
        problems = cfg.update({k: v for k, v in changes.items() if k not in bad})
    log.info("settings started from the setup's choices (%s)", ", ".join(sorted(changes)))
    return not problems
