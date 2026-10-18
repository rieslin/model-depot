"""Base-model families: one short lower-case name per family of models whose LoRAs go together.

Two sources say which family a file belongs to. The file's own header tells the architecture for certain, but it
cannot tell Pony from Illustrious (both are SDXL inside). civitai's "baseModel" can, but it is typed in by the
uploader and is sometimes wrong or just "Other". resolve() combines them: the header decides the architecture,
civitai refines it within that architecture.
"""
import re

UNKNOWN = "unknown"

# Families that share an architecture: a LoRA of one loads in the others (weaker).
ARCH = {
    "sdxl": "sdxl", "pony": "sdxl", "illustrious": "sdxl",
}

# civitai baseModel -> family. Matched on the name with spaces, dots and dashes removed, lower case, by prefix,
# first hit wins, so the longer names come first.
_BASE_MODEL_RULES = (
    ("ponyv7", "pony7"),            # Pony V7 is AuraFlow underneath, not SDXL
    ("pony", "pony"),
    ("illustrious", "illustrious"),
    ("noobai", "illustrious"),      # NoobAI is trained from Illustrious; their LoRAs are shared in practice
    ("sdxl", "sdxl"),
    ("sd14", "sd15"), ("sd15", "sd15"), ("sd1", "sd15"),
    ("sd2", "sd2"),
    ("sd35", "sd3"), ("sd3", "sd3"),
    ("flux2", "flux2"),
    ("flux1", "flux"), ("flux", "flux"),
    ("chroma", "chroma"),
    ("zimage", "zimage"),
    ("qwen", "qwen"),
    ("krea2", "krea2"), ("krea", "krea2"),
    ("anima", "anima"),
    ("lumina", "lumina2"),
    ("hunyuanvideo", "hunyuan_video"), ("hunyuan", "hunyuan_dit"),
    ("wan", "wan"),
    ("hidream", "hidream"),
    ("auraflow", "auraflow"),
    ("pixart", "pixart"),
    ("kolors", "kolors"),
    ("ltxv", "ltxv"), ("ltx", "ltxv"),
    ("cogvideo", "cogvideox"),
    ("mochi", "mochi"),
    ("stablecascade", "cascade"),
    ("svd", "svd"),
    ("playground", "playground"),
    ("cosmos", "cosmos"),
)

_WEBUI_SD_VERSION = {"sd1": "sd15", "sd2": "sd2", "sdxl": "sdxl", "sd3": "sd3", "flux": "flux"}


def from_base_model(base_model):
    """Family for a civitai baseModel string ("Illustrious", "SD 1.5", "Flux.1 D"), or "unknown"."""
    key = re.sub(r"[\s.\-_]+", "", str(base_model or "")).lower()
    if not key:
        return UNKNOWN
    for prefix, family in _BASE_MODEL_RULES:
        if key.startswith(prefix):
            return family
    return UNKNOWN


def from_webui_sd_version(value):
    """Family for the "sd version" field of an old WebUI card ("SD1", "SDXL"), or "unknown"."""
    return _WEBUI_SD_VERSION.get(str(value or "").strip().lower(), UNKNOWN)


def arch(family):
    return ARCH.get(family, family)


# Names a person may give a folder for a family's models ("PONY", "Illustrious", "z-image"); compared in lower case.
_FOLDER_WORDS = {
    "sd15": ("sd15", "sd1.5", "sd 1.5", "sd1", "sd_1.5"), "sd2": ("sd2", "sd2.1"), "sdxl": ("sdxl", "xl"),
    "pony": ("pony", "ponyxl", "pdxl"), "illustrious": ("illustrious", "ilxl", "il", "noob", "noobai"),
    "flux": ("flux", "flux1", "flux.1"), "flux2": ("flux2", "flux.2"), "zimage": ("zimage", "z-image", "zimg", "z_image"),
    "qwen": ("qwen", "qwen-image", "qwenimage"), "krea2": ("krea2", "krea"), "anima": ("anima",), "wan": ("wan",),
    "chroma": ("chroma",), "sd3": ("sd3", "sd3.5"), "lumina2": ("lumina", "lumina2"), "hidream": ("hidream",),
}


def folder_words(family):
    """Folder names that would mean this family."""
    if not family or family == UNKNOWN:
        return ()
    return tuple(dict.fromkeys((family,) + _FOLDER_WORDS.get(family, ())))


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
