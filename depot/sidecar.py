"""The files an old WebUI install left next to each model. They are only ever read: never changed, never deleted.

    <name>.civitai.info    what civitai knew about the version when the Civitai Helper plugin looked it up
    <name>.json            the WebUI's own card: preferred weight, activation text, notes
    <name>.preview.png     the cover (also .jpg/.jpeg/.webp, and <name>.png)
"""
import json
import os

from . import families, version_info

INFO_SUFFIX = ".civitai.info"
CARD_SUFFIX = ".json"
PREVIEW_SUFFIXES = (".preview.png", ".preview.jpg", ".preview.jpeg", ".preview.webp", ".png", ".jpg", ".jpeg", ".webp")
CARD_KEYS = ("description", "sd version", "activation text", "preferred weight", "negative text", "notes")
MAX_SIDECAR_BYTES = 32 * 1024 * 1024


def find(names_lower, model_name):
    """The sidecar file names for a model. names_lower maps lower-cased file names of the model's folder to the real
    names. Returns {"info": name, "card": name, "preview": name}, only the ones that exist."""
    stem = os.path.splitext(model_name)[0].lower()
    out = {}
    name = names_lower.get(stem + INFO_SUFFIX)
    if name:
        out["info"] = name
    name = names_lower.get(stem + CARD_SUFFIX)
    if name:
        out["card"] = name
    for suffix in PREVIEW_SUFFIXES:
        name = names_lower.get(stem + suffix)
        if name:
            out["preview"] = name
            break
    return out


def signature(folder, found):
    """A string that changes when any of the sidecar files changes."""
    parts = []
    for role in ("info", "card", "preview"):
        name = found.get(role)
        if not name:
            continue
        try:
            st = os.stat(os.path.join(folder, name))
            parts.append("%s:%s:%d:%d" % (role, name, st.st_size, st.st_mtime_ns))
        except OSError:
            parts.append("%s:%s:?" % (role, name))
    return "|".join(parts)


def _read_json(path):
    try:
        if os.path.getsize(path) > MAX_SIDECAR_BYTES:
            return None
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def read_info(path):
    """The .civitai.info content as a dict ({} when the plugin recorded "not on civitai"), or None if unreadable."""
    data = _read_json(path)
    return data if isinstance(data, dict) else None


def read_card(path):
    """The WebUI card as a dict, or None when the .json next to the model is something else."""
    data = _read_json(path)
    if isinstance(data, dict) and any(k in data for k in CARD_KEYS):
        return {k: data[k] for k in CARD_KEYS if k in data}
    return None


def summarize(folder, found):
    """What the index keeps about a model's sidecars: enough to show the model as recognised before its fingerprint
    is computed. The full content is read again when it is imported into the model's record."""
    out = {}
    if found.get("info"):
        info = read_info(os.path.join(folder, found["info"]))
        entry = {"file": found["info"]}
        fields = version_info.version_fields(info)
        if fields:
            entry.update(fields)
            entry["sha256s"] = version_info.version_hashes(info)
        else:
            entry["empty"] = True       # looked up back then and not found, or unreadable
        out["info"] = entry
    if found.get("card"):
        card = read_card(os.path.join(folder, found["card"]))
        if card is not None:
            out["card"] = {"file": found["card"],
                           "family": families.from_webui_sd_version(card.get("sd version"))}
    if found.get("preview"):
        out["preview"] = {"file": found["preview"]}
    return out or None


def preview_level(info, preview_size, fresh_version=None):
    """civitai's rating of the image a local preview was saved from, or None when it cannot be told.

    The plugin saved the preview from one of the images listed in the .civitai.info of that moment, at full size, so
    the image with the same pixel size is the one. If civitai still lists that image, its current rating is used.
    """
    images = [i for i in (info or {}).get("images") or [] if isinstance(i, dict) and not version_info.is_video(i)]
    if not images:
        return None
    chosen = None
    if preview_size:
        width, height = preview_size
        for image in images:
            if image.get("width") == width and image.get("height") == height:
                chosen = image
                break
        if chosen is None and width and height:
            for image in images:
                w, h = image.get("width"), image.get("height")
                if w and h and abs(w / h - width / height) < 0.01:
                    chosen = image
                    break
    if chosen is None:
        chosen = images[0]
    uuid = version_info.image_uuid(chosen.get("url"))
    if uuid and isinstance(fresh_version, dict):
        for image in fresh_version.get("images") or []:
            if isinstance(image, dict) and version_info.image_uuid(image.get("url")) == uuid:
                level = version_info.image_level(image)
                if level is not None:
                    return level
    return version_info.image_level(chosen)
