"""Base-model families: one short lower-case name per family of models whose LoRAs go together.

Two sources say which family a file belongs to. The file's own header tells the architecture for certain, but it
cannot tell Pony from Illustrious (both are SDXL inside). civitai's "baseModel" can, but it is typed in by the
uploader and is sometimes wrong or just "Other". resolve() combines them: the header decides the architecture,
civitai refines it within that architecture.
"""
import re

from . import catalog

UNKNOWN = "unknown"


def _families():
    """{family: {"code", "name", "arch"?, "folder_words"?}} from the families catalog."""
    return catalog.load("families")["families"]


def table():
    """{family: {"code", "name", "arch"}} for the page; arch is the family whose architecture it shares, or None when
    it is its own."""
    out = {}
    for key, entry in _families().items():
        shared = arch(key)
        out[key] = {"code": entry.get("code"), "name": entry.get("name"), "arch": shared if shared != key else None}
    return out


def from_base_model(base_model):
    """Family for a civitai baseModel string ("Illustrious", "SD 1.5", "Flux.1 D"), or "unknown". Matched on the name
    with spaces, dots, dashes and underscores removed, lower case, by prefix, first hit in the catalog's order wins."""
    key = re.sub(r"[\s.\-_]+", "", str(base_model or "")).lower()
    if not key:
        return UNKNOWN
    for prefix, family in catalog.load("families")["civitai_base_models"]:
        if key.startswith(prefix):
            return family
    return UNKNOWN


_WEBUI_SD_VERSION = {"sd1": "sd15", "sd2": "sd2", "sdxl": "sdxl", "sd3": "sd3", "flux": "flux"}


def from_webui_sd_version(value):
    """Family for the "sd version" field of an old WebUI card ("SD1", "SDXL"), or "unknown"."""
    return _WEBUI_SD_VERSION.get(str(value or "").strip().lower(), UNKNOWN)


def arch(family):
    """The family whose architecture `family` shares (a LoRA of one loads in the other, weaker); itself when none."""
    entry = _families().get(family) if isinstance(family, str) else None
    if isinstance(entry, dict) and entry.get("arch"):
        return entry["arch"]
    return family


def folder_words(family):
    """Folder names that would mean this family ("PONY", "Illustrious", "z-image"); compared in lower case."""
    if not family or family == UNKNOWN:
        return ()
    entry = _families().get(family)
    words = entry.get("folder_words") if isinstance(entry, dict) else None
    return tuple(dict.fromkeys((family,) + tuple(words or ())))


def resolve(header_family, stated_family, hint=None):
    """One family from what the file's header shows and what civitai (or an old card) states.

    `hint` is what only the file's name suggests (see sniff.classify); it counts when nothing else speaks.
    """
    header = header_family or UNKNOWN
    stated = stated_family or UNKNOWN
    if stated == UNKNOWN:
        if hint and header == arch(hint):
            return hint
        return header
    if header == UNKNOWN:
        return stated
    if arch(header) != arch(stated):
        return header            # the label is wrong: the tensors do not lie
    if stated == arch(stated) and header != arch(header):
        return header            # label says plain "sdxl", the training metadata says pony or illustrious
    return stated
