"""Walk the model folders from the settings and describe every model file in them.

The names produced here are the ones ComfyUI lists: the path relative to the folder, in the operating system's own
spelling. ComfyUI's listing is followed closely (links are followed, `.git` is skipped, a folder can stand for
several model types) because other programs put these names straight into ComfyUI workflows.
"""
import os
import stat

from . import kinds, sidecar, sniff

_CONTENT_TYPE = {"checkpoint": "checkpoints", "diffusion_model": "diffusion_models", "lora": "loras", "vae": "vae",
                 "text_encoder": "text_encoders", "embedding": "embeddings", "controlnet": "controlnet"}


def _real_key(path):
    try:
        return os.path.normcase(os.path.realpath(path))
    except OSError:
        return os.path.normcase(path)


def walk(folder, stop_at=()):
    """Yield (directory, [file names]) for `folder` and everything under it.

    Links to directories are followed, as ComfyUI does. A link that leads back to one of its own parents is not
    entered, so a loop cannot run forever. Directories named in `stop_at` (normcased paths) are left out: they are
    model folders of their own.
    """
    stack = [(folder, frozenset((_real_key(folder),)))]
    while stack:
        current, ancestors = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError:
            continue
        files, dirs = [], []
        for entry in entries:
            try:
                if entry.is_dir():
                    dirs.append(entry.name)
                else:
                    files.append(entry.name)
            except OSError:
                continue
        yield current, files
        for name in sorted(dirs, reverse=True):       # reverse: the stack pops them back in name order
            if name == ".git":
                continue
            path = os.path.join(current, name)
            if os.path.normcase(path) in stop_at:
                continue
            key = _real_key(path)
            if key in ancestors:
                continue
            stack.append((path, ancestors | {key}))


def count_models(folder):
    """(files, bytes) of the model files under a folder; used by the setup wizard."""
    files = size = 0
    for current, names in walk(folder):
        for name in names:
            if os.path.splitext(name)[1].lower() in kinds.MODEL_EXTENSIONS:
                try:
                    size += os.path.getsize(os.path.join(current, name))
                    files += 1
                except OSError:
                    pass
    return files, size


def primary_type(types, header):
    """The ComfyUI type a file is shown under. A folder registered under several types (a WebUI "Stable-diffusion"
    folder is both checkpoints and diffusion_models) is sorted out by what each file's header says it is."""
    if len(types) == 1:
        return types[0]
    prefer = _CONTENT_TYPE.get((header or {}).get("content"))
    return prefer if prefer in types else types[0]


def _header_outdated(header):
    """Was this safetensors header kept by an older sniff.py, which kept less? Such a file's header is read again."""
    return isinstance(header, dict) and header.get("format") == "safetensors" and (header.get("v") or 1) < sniff.HEADER_VERSION


def scan(folders, known=None, should_stop=None):
    """Find the model files.

    folders: [{"path", "comfy_type"}] in settings order (the order within a type is ComfyUI's search order).
    known:   {normcased path: {"bytes", "mtime_ns", "sidecar_sig", "header", "header_done", "sidecar"}} from the
             index, so that unchanged files are not opened again.
    Returns {"files": [...], "folders": [...], "stopped": bool}.
    """
    known = known or {}
    roots, order, counters = {}, {}, {}
    for folder in folders:
        path = os.path.normpath(folder["path"])
        key = os.path.normcase(path)
        ctype = kinds.canonical_type(folder["comfy_type"])
        root = roots.setdefault(key, {"path": path, "types": []})
        if ctype not in root["types"]:
            root["types"].append(ctype)
            order[(key, ctype)] = counters.get(ctype, 0)
            counters[ctype] = counters.get(ctype, 0) + 1

    files, reports, stopped = [], [], False
    for key, root in roots.items():
        report = {"path": root["path"], "comfy_types": list(root["types"]), "exists": os.path.isdir(root["path"]),
                  "files": 0, "bytes": 0}
        reports.append(report)
        if not report["exists"]:
            continue
        others = frozenset(k for k in roots if k != key)
        label = os.path.basename(root["path"].rstrip("\\/"))
        for current, names in walk(root["path"], stop_at=others):
            if should_stop and should_stop():
                stopped = True
                break
            names_lower = None
            for name in names:
                ext = os.path.splitext(name)[1].lower()
                if ext not in kinds.MODEL_EXTENSIONS:
                    continue
                path = os.path.join(current, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                if names_lower is None:
                    names_lower = {n.lower(): n for n in names}
                rel = os.path.relpath(path, root["path"])
                sidecars = sidecar.find(names_lower, name)
                sig = sidecar.signature(current, sidecars)
                before = known.get(os.path.normcase(path))
                same = bool(before and before.get("bytes") == st.st_size and before.get("mtime_ns") == st.st_mtime_ns)
                kept = before.get("header") if before else None
                if same and before.get("header_done") and not _header_outdated(kept):
                    header = kept
                else:
                    header = sniff.sniff(path, os.path.join(label, rel))
                if same and before.get("sidecar_sig") == sig:
                    side = before.get("sidecar")
                else:
                    side = sidecar.summarize(current, sidecars)
                ctype = primary_type(root["types"], header)
                files.append({
                    "path": path, "folder": root["path"], "rel": rel,
                    "comfy_types": list(root["types"]), "comfy_type": ctype, "kind": kinds.kind_of(ctype),
                    "ext": ext, "bytes": st.st_size, "mtime_ns": st.st_mtime_ns,
                    "header": header, "sidecar": side, "sidecar_sig": sig,
                    "same": same, "shadowed": False,
                })
                report["files"] += 1
                report["bytes"] += st.st_size
        if stopped:
            break

    # Two files with the same ComfyUI name under one type: ComfyUI loads the one in the folder it searches first.
    by_name = {}
    for f in files:
        by_name.setdefault((f["comfy_type"], os.path.normcase(f["rel"])), []).append(f)
    for group in by_name.values():
        if len(group) > 1:
            group.sort(key=lambda f: order.get((os.path.normcase(f["folder"]), f["comfy_type"]), 1 << 30))
            for f in group[1:]:
                f["shadowed"] = True
    return {"files": files, "folders": reports, "stopped": stopped}
