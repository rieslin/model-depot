"""Model types. ComfyUI's folder-type names ("checkpoints", "loras") are the vocabulary of the settings and of the API's
comfy_type; `kind` is the coarser name the API also gives ("checkpoint", "lora").

ComfyUI's own lists (extensions, renamed types, the type each kind stands for, the types the settings page offers) are
in the comfyui catalog. The old constant names (MODEL_EXTENSIONS, KIND_BY_TYPE ...) still read them."""
from . import catalog

KINDS = ("checkpoint", "diffusion_model", "lora", "embedding", "vae", "text_encoder", "upscaler", "controlnet", "other")

_sets = [None, None]    # [the comfyui catalog they were made from, {name: frozenset}]


def _comfyui():
    return catalog.load("comfyui")


def _extensions():
    data = _comfyui()
    if _sets[0] is not data:
        comfy = frozenset(e.lower() for e in data["model_extensions"])
        extra = frozenset(e.lower() for e in data["extra_extensions"])
        _sets[:] = [data, {"comfy": comfy, "extra": extra, "model": comfy | extra}]
    return _sets[1]


def comfy_extensions():
    """The extensions ComfyUI's loaders list (folder_paths.supported_pt_extensions), lower case with the dot."""
    return _extensions()["comfy"]


def extra_extensions():
    """Model files that ComfyUI's own loaders do not list, but that are models all the same."""
    return _extensions()["extra"]


def model_extensions():
    return _extensions()["model"]


def legacy_types():
    """ComfyUI's own renames ({"unet": "diffusion_models"})."""
    return dict(_comfyui()["legacy_types"])


def kind_by_type():
    """{ComfyUI type: kind}; a kind the program does not have (KINDS) is left out, the type counts as "other"."""
    return {t: k for t, k in _comfyui()["kind_by_type"].items() if k in KINDS}


def common_types():
    """The types the settings page offers by name, in the order shown. Any other ComfyUI type can be typed in."""
    return tuple(_comfyui()["common_types"])


def canonical_type(comfy_type):
    t = str(comfy_type or "").strip()
    return _comfyui()["legacy_types"].get(t, t)


def kind_of(comfy_type):
    kind = _comfyui()["kind_by_type"].get(canonical_type(comfy_type))
    return kind if kind in KINDS else "other"


_OLD_NAMES = {"COMFY_EXTENSIONS": comfy_extensions, "EXTRA_EXTENSIONS": extra_extensions,
              "MODEL_EXTENSIONS": model_extensions, "LEGACY_TYPES": legacy_types, "KIND_BY_TYPE": kind_by_type,
              "COMMON_TYPES": common_types}


def __getattr__(name):
    if name in _OLD_NAMES:
        return _OLD_NAMES[name]()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
