"""A picture the user adds to a model's examples: a copy small enough to keep, and the settings it was made with, read
from what the generating program wrote into the file.

The settings come back in the same shape as civitai's records of its example images (the dossier shows both the same
way): {"prompt", "negativePrompt", "steps", "sampler", "scheduler", "cfgScale", "seed", "Size", "Model"}, each only
when the file says it. The old WebUI (and Forge) writes one text, "parameters", into a PNG, or into a JPEG's or WebP's
EXIF user comment; ComfyUI writes the graph it ran ("prompt"), whose sampler node is followed back to its prompts.
"""
import io
import json
import re

from PIL import Image, ImageOps

MAX_SIDE = 2048          # the copy kept: large enough for the larger view on a 4K screen
_BACKDROP = (32, 32, 32)     # behind a transparent picture, as the thumbnails have it (thumbs.py)
_LANCZOS = getattr(Image, "Resampling", Image).LANCZOS


class ImageError(ValueError):
    """Not a picture this program can read."""


def keep(data):
    """(jpeg bytes, width, height, settings or None) for the bytes of an added picture: the size is the picture's own,
    upright. Raises ImageError."""
    try:
        image = Image.open(io.BytesIO(data))
        image.seek(0)                   # the first frame of an animated picture
        image.load()
        params = read_settings(image)
        image = ImageOps.exif_transpose(image) or image
        if image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info):
            rgba = image.convert("RGBA")
            image = Image.new("RGB", rgba.size, _BACKDROP)
            image.paste(rgba, mask=rgba.getchannel("A"))
        else:
            image = image.convert("RGB")
        width, height = image.size
        if max(width, height) > MAX_SIDE:
            image.thumbnail((MAX_SIDE, MAX_SIDE), _LANCZOS)
        out = io.BytesIO()
        image.save(out, "JPEG", quality=90, optimize=True)
    except Exception as e:  # noqa: BLE001 - Pillow raises several kinds for a file it cannot read
        raise ImageError(str(e)) from e
    return out.getvalue(), width, height, params


def read_settings(image):
    """The settings a picture records, or None."""
    info = image.info or {}
    text = info.get("parameters")
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    if not text:
        text = _exif_comment(image)
    if text and "Steps:" in text:
        return webui_settings(text)
    graph = info.get("prompt")
    if isinstance(graph, (bytes, str)):
        try:
            graph = json.loads(graph)
        except ValueError:
            graph = None
    if isinstance(graph, dict):
        return comfy_settings(graph)
    return None


def _exif_comment(image):
    try:
        exif = image.getexif()
        raw = exif.get_ifd(0x8769).get(0x9286)      # Exif IFD, UserComment
    except Exception:  # noqa: BLE001 - a broken EXIF block is no reason to refuse the picture
        return None
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, bytes) or len(raw) < 8:
        return None
    head, body = raw[:8], raw[8:]
    if head.startswith(b"UNICODE"):
        for codec in ("utf-16-be", "utf-16-le"):
            try:
                text = body.decode(codec)
            except UnicodeDecodeError:
                continue
            if "Steps:" in text:
                return text
        return None
    return body.decode("utf-8", "replace") if head.startswith(b"ASCII") else None


_PAIR = re.compile(r'\s*([A-Za-z][A-Za-z0-9 _/()\-]*?):\s*("(?:[^"\\]|\\.)*"|[^,]*)(?:,|$)')


def webui_settings(text):
    """The old WebUI's "parameters" text: the prompt, "Negative prompt: …", then "Steps: 20, Sampler: …, …"."""
    lines = text.strip().split("\n")
    last = len(lines) - 1
    while last > 0 and not lines[last].lstrip().startswith("Steps:"):
        last -= 1
    head, settings_line = lines[:last], lines[last] if lines[last].lstrip().startswith("Steps:") else ""
    prompt, negative, in_negative = [], [], False
    for line in head:
        if line.startswith("Negative prompt:"):
            in_negative = True
            line = line[len("Negative prompt:"):].strip()
        (negative if in_negative else prompt).append(line)
    out = {}
    if "\n".join(prompt).strip():
        out["prompt"] = "\n".join(prompt).strip()
    if "\n".join(negative).strip():
        out["negativePrompt"] = "\n".join(negative).strip()
    pairs = {m.group(1).strip(): m.group(2).strip().strip('"') for m in _PAIR.finditer(settings_line)}
    for field, key, kind in (("Steps", "steps", int), ("Sampler", "sampler", str), ("Schedule type", "scheduler", str),
                             ("CFG scale", "cfgScale", float), ("Seed", "seed", int), ("Size", "Size", str),
                             ("Model", "Model", str), ("Clip skip", "Clip skip", int)):
        value = pairs.get(field)
        if value in (None, ""):
            continue
        try:
            out[key] = kind(value) if kind is not str else value
        except ValueError:
            continue
    return out or None


_SAMPLERS = ("KSampler", "KSamplerAdvanced", "SamplerCustom", "SamplerCustomAdvanced")


def comfy_settings(graph):
    """ComfyUI's graph ("prompt"): the settings of its sampler node, its prompts followed back through the graph."""
    nodes = {str(k): v for k, v in graph.items() if isinstance(v, dict)}

    def node(ref):
        return nodes.get(str(ref[0])) if isinstance(ref, list) and ref else None

    def text_of(ref, depth=0):
        """The prompt text a conditioning input comes from: a text encoder's text, followed through a few nodes."""
        n = node(ref)
        if n is None or depth > 6:
            return None
        inputs = n.get("inputs") or {}
        for key in ("text", "text_g", "prompt", "string", "value"):
            value = inputs.get(key)
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, list):
                found = text_of(value, depth + 1)
                if found:
                    return found
        for key in ("conditioning", "conditioning_1", "positive", "base_positive", "clip"):
            if isinstance(inputs.get(key), list):
                found = text_of(inputs[key], depth + 1)
                if found:
                    return found
        return None

    def number(value):
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    sampler = next((n for n in nodes.values() if n.get("class_type") in _SAMPLERS), None)
    if sampler is None:
        return None
    inputs = sampler.get("inputs") or {}
    out = {}
    if isinstance(inputs.get("sampler_name"), str):
        out["sampler"] = inputs["sampler_name"]
    if isinstance(inputs.get("scheduler"), str):
        out["scheduler"] = inputs["scheduler"]
    for key, field in (("steps", "steps"), ("cfg", "cfgScale"), ("seed", "seed"), ("noise_seed", "seed")):
        value = number(inputs.get(key))
        if value is not None:
            out[field] = value
    positive = text_of(inputs.get("positive"))
    negative = text_of(inputs.get("negative"))
    if positive:
        out["prompt"] = positive
    if negative:
        out["negativePrompt"] = negative
    latent = node(inputs.get("latent_image"))
    if latent is not None:
        width, height = number((latent.get("inputs") or {}).get("width")), number((latent.get("inputs") or {}).get("height"))
        if width and height:
            out["Size"] = "%dx%d" % (width, height)
    for n in nodes.values():
        loader_inputs = n.get("inputs") or {}
        for key in ("ckpt_name", "unet_name"):
            if isinstance(loader_inputs.get(key), str):
                out.setdefault("Model", re.split(r"[\\/]", loader_inputs[key])[-1].rsplit(".", 1)[0])
    return out or None
