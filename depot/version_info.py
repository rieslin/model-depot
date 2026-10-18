"""Reading a civitai model-version object: the JSON that the API returns for a version, which is also what the old
WebUI plugin saved next to each model as <name>.civitai.info."""
import re

from . import families

# civitai's image ratings. Newer data has nsfwLevel (a bit value); older data has nsfw as a word or a yes/no.
LEVEL_PG, LEVEL_PG13, LEVEL_R, LEVEL_X, LEVEL_XXX = 1, 2, 4, 8, 16
_OLD_NSFW_WORDS = {"none": LEVEL_PG, "soft": LEVEL_PG13, "mature": LEVEL_R, "x": LEVEL_X, "blocked": 32}

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_TRANSFORM_RE = re.compile(r"/(?:original=true|width=\d+)(?:,[^/]*)?/")


def _int(value):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def clean_words(words):
    """trainedWords as a list of non-empty strings, order kept, duplicates dropped."""
    out, seen = [], set()
    for w in words if isinstance(words, list) else []:
        s = str(w).strip().strip(",").strip() if w is not None else ""
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def version_hashes(version):
    """Lower-case SHA-256 of every file the version lists."""
    out = []
    for f in (version or {}).get("files") or []:
        h = ((f or {}).get("hashes") or {}).get("SHA256") if isinstance(f, dict) else None
        if isinstance(h, str) and len(h) == 64:
            out.append(h.lower())
    return out


def version_fields(version):
    """The few fields the index keeps about a version, or None when the object does not name a version
    (the old plugin wrote an empty or placeholder file for models it could not find)."""
    if not isinstance(version, dict):
        return None
    version_id, model_id = _int(version.get("id")), _int(version.get("modelId"))
    if not version_id or not model_id:
        return None
    model = version.get("model") if isinstance(version.get("model"), dict) else {}
    base_model = version.get("baseModel")
    base_model = str(base_model) if base_model else None
    return {
        "model_id": model_id,
        "version_id": version_id,
        "title": str(model.get("name") or "").strip() or None,
        "version_name": str(version.get("name") or "").strip() or None,
        "model_type": str(model.get("type") or "").strip() or None,
        "base_model": base_model,
        "family": families.from_base_model(base_model),
        "trigger_words": clean_words(version.get("trainedWords")),
    }


# civitai model types that fit each kind of file here. A VAE that sits in a vae folder is better named by a page of
# type "VAE" than by a checkpoint page that also offers it.
_TYPES_FOR_KIND = {
    "checkpoint": ("checkpoint",), "diffusion_model": ("checkpoint",), "lora": ("lora", "locon", "dora"),
    "embedding": ("textualinversion",), "vae": ("vae",), "text_encoder": ("textencoder",),
    "upscaler": ("upscaler",), "controlnet": ("controlnet",),
}
_MODEL_FILE_TYPES = ("model", "pruned model")


def file_entry(version, sha256):
    """The entry of the version's file list that has this fingerprint, or None."""
    for f in (version or {}).get("files") or []:
        if isinstance(f, dict) and str(((f.get("hashes") or {}).get("SHA256")) or "").lower() == sha256:
            return f
    return None


def names_the_file(version, entry):
    """Is the file what this version is (its main file, or one of its model variants)? The alternative is a file
    the uploader merely added for convenience: the VAE or the text encoder that a checkpoint needs. civitai's batch
    lookup returns every version that lists a fingerprint, and popular VAEs are listed by hundreds of them."""
    file_type = str(entry.get("type") or "").lower()
    if file_type in _MODEL_FILE_TYPES:
        return True
    model_type = str(((version.get("model") or {}).get("type")) or "").lower()
    if file_type == "negative":
        return model_type == "textualinversion"     # the negative half of an embedding
    if file_type == "diffusion model" and model_type == "checkpoint":
        return True     # a checkpoint page that offers the bare diffusion model, often in several precisions
    if not entry.get("primary"):
        return False
    # A main file marked as a part ("VAE", "Text Encoder", "Diffusion Model") names the version only on a page of
    # that very type; a LoRA page whose "main" file is a VAE is a mis-filed upload.
    return not file_type or file_type.replace(" ", "") == model_type


def pick_version(versions, sha256, kinds=()):
    """Choose, among the versions civitai returned for a fingerprint, the one the file should be known as.

    Returns (version or None, bundled). `bundled` lists, most downloaded first, the versions that only carry the
    file along; when no version names the file, that list is all civitai knows about it.

    The choice: a version whose main file it is, before one where it is a variant; a page of the kind the file is
    stored as (see _TYPES_FOR_KIND), before others; then the most downloaded; then the oldest.
    """
    wanted_types = {t for k in kinds for t in _TYPES_FOR_KIND.get(k, ())}
    naming, bundled = [], []
    for version in versions:
        if version_fields(version) is None:
            continue
        entry = file_entry(version, sha256)
        if entry is None:
            continue
        downloads = _int((version.get("stats") or {}).get("downloadCount")) or 0
        if names_the_file(version, entry):
            model_type = str(((version.get("model") or {}).get("type")) or "").lower()
            rank = (0 if entry.get("primary") else 1, 0 if (not wanted_types or model_type in wanted_types) else 1,
                    -downloads, version.get("id"))
            naming.append((rank, version))
        else:
            bundled.append((-downloads, version.get("id"), version))
    naming.sort(key=lambda item: item[0])
    bundled.sort(key=lambda item: item[:2])
    return (naming[0][1] if naming else None), [item[2] for item in bundled]


def bundle_summary(versions, limit=10):
    """A short list of the versions that carry a file along, for the record."""
    out = []
    for version in versions[:limit]:
        fields = version_fields(version)
        out.append({k: fields[k] for k in ("model_id", "version_id", "title", "version_name", "model_type", "base_model")})
    return out


def image_level(image):
    """civitai's rating of one image as its nsfwLevel number, or None when the data does not say."""
    if not isinstance(image, dict):
        return None
    level = image.get("nsfwLevel")
    if isinstance(level, bool):
        level = None
    if isinstance(level, int) and level > 0:
        return level
    old = image.get("nsfw")
    if isinstance(old, bool):
        return LEVEL_R if old else LEVEL_PG     # the oldest data only says "nsfw or not"
    if isinstance(old, str):
        return _OLD_NSFW_WORDS.get(old.strip().lower())
    return None


def image_uuid(url):
    m = _UUID_RE.search(str(url or ""))
    return m.group(0).lower() if m else None


def is_video(image):
    if not isinstance(image, dict):
        return False
    if str(image.get("type") or "").lower() == "video":
        return True
    return str(image.get("url") or "").lower().split("?")[0].endswith((".mp4", ".webm", ".mov"))


def pick_cover_image(images):
    """The first still image of a version: what its civitai page shows as the cover."""
    for image in images if isinstance(images, list) else []:
        if isinstance(image, dict) and image.get("url") and not is_video(image):
            return image
    return None


def sized_image_url(url, width):
    """The same civitai image at a smaller width, when the address has the usual size segment."""
    url = str(url or "")
    if _TRANSFORM_RE.search(url):
        return _TRANSFORM_RE.sub("/width=%d/" % width, url, count=1)
    return url


def samples(version):
    """The author's example images with their generation settings, for the detail API."""
    out = []
    for image in (version or {}).get("images") or []:
        if not isinstance(image, dict) or not image.get("url"):
            continue
        out.append({
            "url": image.get("url"),
            "type": "video" if is_video(image) else "image",
            "width": image.get("width"),
            "height": image.get("height"),
            "level": image_level(image),
            "params": image.get("meta") if isinstance(image.get("meta"), dict) else None,
        })
    return out
