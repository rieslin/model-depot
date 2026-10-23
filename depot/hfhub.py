"""Hugging Face: downloading one file that a link names.

Text encoders, VAEs, SeedVR2 and the official ComfyUI files are published there, not on civitai (the review of
2026-10-20). Accepted links name one file:

    https://huggingface.co/<owner>/<repo>/blob/<revision>/<path>      the page of a file
    https://huggingface.co/<owner>/<repo>/resolve/<revision>/<path>   its download address (as ComfyUI's templates have it)

Its size and SHA-256 come from Hugging Face's answer to a HEAD request (X-Linked-Size, X-Linked-Etag: a file kept in
Git LFS, as model files are); the download is checked against that fingerprint, as a civitai download is. No key is
sent: a file behind a login or a licence to accept (a "gated" repository) is reported, not fetched.

The preview has the shape of a civitai preview (intake.describe), so the download drawer shows it with its own parts:
the file as a model with one version and one file, no example images.
"""
import asyncio
import os
import re
import zlib
from urllib.parse import quote, unquote, urljoin, urlsplit

import aiohttp

from . import __version__, families, folders, kinds

HOSTS = ("huggingface.co", "hf.co", "www.huggingface.co")
SITE = "https://huggingface.co"
_TIMEOUT = aiohttp.ClientTimeout(total=20, sock_connect=8, sock_read=15)
# ComfyUI's model folders, as they appear in a repository's paths (Comfy-Org's split_files/<type>/...)
_PATH_TYPES = ("checkpoints", "diffusion_models", "text_encoders", "vae", "loras", "upscale_models", "embeddings",
               "controlnet", "clip_vision", "model_patches", "latent_upscale_models", "style_models", "unet", "clip")
_LEGACY = {"unet": "diffusion_models", "clip": "text_encoders"}


class HFError(Exception):
    """code: not_found | login | network | server | not_file"""

    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code = code
        self.detail = detail


def is_hf(host):
    return (host or "").lower().rstrip(".") in HOSTS


def parse(url):
    """{"hf": download address, "page", "repo", "revision", "path", "name"} for a link to one file, or {"error"}."""
    try:
        parts = urlsplit(url if re.match(r"^https?://", url, re.I) else "https://" + url)
    except ValueError:
        return {"error": "unrecognized"}
    segs = [unquote(s) for s in parts.path.split("/") if s]
    if segs and segs[0] in ("datasets", "spaces"):
        return {"error": "hf_not_file"}
    if len(segs) < 5 or segs[2] not in ("blob", "resolve"):
        return {"error": "hf_not_file"}
    repo, revision, path = segs[0] + "/" + segs[1], segs[3], "/".join(segs[4:])
    def address(kind):      # joined, not %-formatted: a quoted path carries "%20" and the like
        return "/".join((SITE, repo, kind, quote(revision, safe=""), quote(path, safe="/")))
    return {"hf": address("resolve"), "page": address("blob"), "repo": repo, "revision": revision, "path": path,
            "name": os.path.basename(path), "error": None}


def file_id(url):
    """A stable number for the download queue's file column (civitai's are positive; these are negative)."""
    return -(zlib.crc32(url.encode("utf-8")) & 0x7FFFFFFF) - 1


def guess_type(path):
    """The ComfyUI folder a file's path in its repository names (split_files/text_encoders/x.safetensors), or by
    its name; None when neither says."""
    parts = [p.lower() for p in path.split("/")[:-1]]
    for p in reversed(parts):
        if p in _PATH_TYPES:
            return _LEGACY.get(p, p)
    name = os.path.basename(path).lower()
    if "vae" in name:
        return "vae"
    if "lora" in name:
        return "loras"
    if re.search(r"text_?encoder|clip_|t5xxl|umt5|qwen\d|qwen_\d|gemma|mistral", name):
        return "text_encoders"
    if re.search(r"(^|[^a-z0-9])\dx[-_]|esrgan|upscal", name):
        return "upscale_models"
    return None


def session():
    return aiohttp.ClientSession(trust_env=False, timeout=_TIMEOUT, headers={"User-Agent": "ModelDepot/%s" % __version__})


async def head(url, proxy=None):
    """{"bytes", "sha256"} of the file at a download address. The first answer is read (Hugging Face redirects a Git
    LFS file to its storage, with the size and fingerprint in the headers)."""
    try:
        async with session() as s:
            for _ in range(5):
                async with s.head(url, proxy=proxy, allow_redirects=False) as resp:
                    status, headers = resp.status, resp.headers
                location = headers.get("Location") or ""
                # a renamed repository answers with a redirect to its new name on Hugging Face itself first (seen
                # 2026-10-23: z_image_turbo -> Z-Image-Turbo); the storage's redirect carries the file's details
                if status in (301, 302, 307, 308) and not headers.get("X-Linked-Size") and \
                        (location.startswith("/") or is_hf(urlsplit(location).hostname)):
                    url = urljoin(url, location)
                    continue
                break
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise HFError("network", type(e).__name__) from e
    if status in (401, 403):
        raise HFError("login", str(status))
    if status == 404:
        raise HFError("not_found")
    if status >= 500:
        raise HFError("server", str(status))
    if status not in (200, 301, 302, 303, 307, 308):
        raise HFError("server", str(status))
    etag = (headers.get("X-Linked-Etag") or "").strip('"').lower()
    size = headers.get("X-Linked-Size") or (headers.get("Content-Length") if status == 200 else None)
    return {"bytes": int(size) if size and size.isdigit() else None,
            "sha256": etag if re.fullmatch(r"[0-9a-f]{64}", etag) else None}


def describe(link, info, present, queued, suggest):
    """A preview in the shape of a civitai preview: one "version", one file."""
    ctype = guess_type(link["path"])
    family = families.UNKNOWN
    fid = file_id(link["hf"])
    sha = info.get("sha256")
    name = link["name"]
    entry = {"id": fid, "name": name, "type": "Model", "bytes": info.get("bytes"), "format": None, "fp": None,
             "size": None, "sha256": sha, "primary": True, "comfy_type": ctype,
             "model_file": os.path.splitext(name)[1].lower() in kinds.MODEL_EXTENSIONS,
             "in_library": present.get(sha) if sha else None, "queued": fid in queued}
    version = {"id": 0, "name": link["repo"], "base_model": None, "family": family, "published_at": None,
               "availability": "Public", "early_access": False, "downloads": None, "trigger_words": [], "images": [],
               "cover": None, "files": [entry], "chosen": [fid], "comfy_type": ctype, "suggestion": suggest(ctype, family)}
    owner = link["repo"].split("/")[0]
    return {"site": SITE, "source": "hf", "hf": link["hf"],
            "model": {"id": 0, "name": name, "type": None, "kind": kinds.kind_of(ctype) if ctype else "other",
                      "comfy_type": ctype, "creator": owner, "nsfw": False, "availability": "Public", "mode": None,
                      "url": link["page"]},
            "linked_version": None, "selected": 0, "versions": [version], "has_token": False}


def type_of_folder(cfg, path):
    """The ComfyUI type of the settings folder a destination is in, for a file whose type the link did not tell."""
    root = folders.owning_root(cfg, path)
    return root["comfy_types"][0] if root and root.get("comfy_types") else None
