"""Reading a pasted link into a preview: which model it is, its versions and their files, which of them the library
already has, and where a download would go.

A model's data comes from civitai's model page API (one request, every version with its files and example images)
and is kept for ten minutes, so pasting the same link again, opening another version, or adding it to the queue asks
nothing more of civitai. A version link needs one request more, to learn its model.
"""
import asyncio
import logging
import os
import time

from . import families, folders, kinds, thumbs, version_info
from .civitai import CivitaiError
from .store import write_atomic

log = logging.getLogger("depot.intake")

CACHE_SECONDS = 600
PREVIEW_IMAGES = 8          # example images offered per version in a preview


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def file_bytes(entry):
    """civitai gives sizes in kilobytes, as a float that is exact to the byte."""
    try:
        return int(round(float(entry.get("sizeKB") or 0) * 1024))
    except (TypeError, ValueError):
        return 0


def version_record(model, version):
    """The version shaped as civitai's version API and its fingerprint lookup return it (with "modelId" and a "model"
    block): the form the library keeps in a model's record. The model page's description rides along in the model
    block, where the detail view reads it."""
    out = dict(version)
    out["modelId"] = model.get("id")
    out["model"] = {"name": model.get("name"), "type": model.get("type"), "nsfw": model.get("nsfw"),
                    "poi": model.get("poi"), "description": model.get("description")}
    return out


def first_still(images):
    for i, image in enumerate(images or []):
        if isinstance(image, dict) and image.get("url") and not version_info.is_video(image):
            return i
    return None


class Intake:
    def __init__(self, cfg, library, remembered):
        self.cfg = cfg
        self.library = library
        self.remembered = remembered
        self.queued_files = lambda: set()           # set by the download queue: civitai file ids it already has
        self._models = {}                           # model id -> (fetched at, site, model)
        self._owner = {}                            # version id -> model id
        self._locks = {}
        self._image_locks = {}

    # ---- civitai's data, kept a while ------------------------------------------------------------------------------
    async def model(self, model_id=None, version_id=None):
        """(site, model) for a model, or for the model a version belongs to."""
        model_id = _int(model_id)
        if model_id is None:
            version_id = _int(version_id)
            if version_id is None:
                raise CivitaiError("not_found", "no model")
            model_id = self._owner.get(version_id)
            if model_id is None:
                _site, version = await self.library.client().get_version(version_id, force=True)
                model_id = _int(version.get("modelId"))
                if model_id is None:
                    raise CivitaiError("not_found", "version without a model")
                self._owner[version_id] = model_id
        cached = self._models.get(model_id)
        if cached and time.time() - cached[0] < CACHE_SECONDS:
            return cached[1], cached[2]
        lock = self._locks.setdefault(model_id, asyncio.Lock())
        async with lock:
            cached = self._models.get(model_id)
            if cached and time.time() - cached[0] < CACHE_SECONDS:
                return cached[1], cached[2]
            site, model = await self.library.client().get_model(model_id, force=True)     # the user pasted the link
            self._models[model_id] = (time.time(), site, model)
            for v in model.get("modelVersions") or []:
                if isinstance(v, dict) and _int(v.get("id")):
                    self._owner[_int(v["id"])] = model_id
        return site, model

    def cached_version(self, version_id):
        """(model, version) from what has been read lately, or (None, None)."""
        model_id = self._owner.get(version_id)
        cached = self._models.get(model_id) if model_id else None
        if not cached:
            return None, None
        for v in cached[2].get("modelVersions") or []:
            if isinstance(v, dict) and _int(v.get("id")) == version_id:
                return cached[2], v
        return None, None

    # ---- the preview -----------------------------------------------------------------------------------------------
    async def preview(self, model_id=None, version_id=None, file_id=None):
        site, model = await self.model(model_id, version_id)
        return self.describe(site, model, _int(version_id), _int(file_id))

    def describe(self, site, model, version_id=None, file_id=None):
        versions = [v for v in model.get("modelVersions") or [] if isinstance(v, dict) and _int(v.get("id"))]
        present = self.library.present_by_hash()
        queued = self.queued_files()
        model_type = str(model.get("type") or "")
        main_type = folders.target_type(model_type)
        out_versions = []
        for v in versions:
            out_versions.append(self._describe_version(model, v, model_type, main_type, present, queued, file_id))
        selected = self._pick(out_versions, version_id, file_id)
        creator = model.get("creator") if isinstance(model.get("creator"), dict) else {}
        return {
            "site": site,
            "model": {
                "id": _int(model.get("id")), "name": str(model.get("name") or "").strip(), "type": model_type,
                "kind": kinds.kind_of(main_type) if main_type else "other", "comfy_type": main_type,
                "creator": creator.get("username"), "nsfw": bool(model.get("nsfw")),
                "availability": model.get("availability"), "mode": model.get("mode"),
                "url": "%s/models/%s" % (site, model.get("id")),
            },
            "linked_version": version_id if any(v["id"] == version_id for v in out_versions) else None,
            "selected": selected,
            "versions": out_versions,
            "has_token": self.library.client().has_token,
        }

    def _describe_version(self, model, v, model_type, main_type, present, queued, wanted_file):
        vid = _int(v["id"])
        family = families.from_base_model(v.get("baseModel"))
        files = []
        for f in v.get("files") or []:
            if not isinstance(f, dict) or not _int(f.get("id")):
                continue
            sha = str(((f.get("hashes") or {}).get("SHA256")) or "").lower() or None
            meta = f.get("metadata") if isinstance(f.get("metadata"), dict) else {}
            ctype = folders.target_type(model_type, f.get("type"))
            name = str(f.get("name") or "")
            files.append({
                "id": _int(f["id"]), "name": name, "type": f.get("type"), "bytes": file_bytes(f),
                "format": meta.get("format"), "fp": meta.get("fp"), "size": meta.get("size"),
                "sha256": sha if sha and len(sha) == 64 else None, "primary": bool(f.get("primary")),
                "comfy_type": ctype, "model_file": os.path.splitext(name)[1].lower() in kinds.MODEL_EXTENSIONS,
                "in_library": present.get(sha) if sha else None, "queued": _int(f["id"]) in queued,
            })
        main = self._main_file(files)
        chosen = [f["id"] for f in files if f["id"] == wanted_file] or ([main["id"]] if main else [])
        ctype = (main or {}).get("comfy_type") or main_type
        images = [img for img in v.get("images") or [] if isinstance(img, dict) and img.get("url")]
        return {
            "id": vid, "name": str(v.get("name") or "").strip(), "base_model": v.get("baseModel"), "family": family,
            "published_at": v.get("publishedAt"), "availability": v.get("availability"),
            "early_access": str(v.get("availability") or "").lower() == "earlyaccess",
            "downloads": _int((v.get("stats") or {}).get("downloadCount")),
            "trigger_words": version_info.clean_words(v.get("trainedWords")),
            "images": [{"index": i, "type": "video" if version_info.is_video(img) else "image",
                        "level": version_info.image_level(img), "width": img.get("width"), "height": img.get("height")}
                       for i, img in enumerate(images[:PREVIEW_IMAGES])],
            "cover": first_still(images),
            "files": files, "chosen": chosen, "comfy_type": ctype,
            "suggestion": self.suggest(ctype, family),
        }

    @staticmethod
    def _main_file(files):
        for test in (lambda f: f["primary"] and f["model_file"], lambda f: f["model_file"], lambda f: True):
            for f in files:
                if test(f):
                    return f
        return None

    @staticmethod
    def _pick(versions, version_id, file_id):
        """The version to show first: the one the link names, else the newest that can be downloaded now."""
        if file_id:
            for v in versions:
                if any(f["id"] == file_id for f in v["files"]):
                    return v["id"]
        if version_id and any(v["id"] == version_id for v in versions):
            return version_id
        for v in versions:
            if not v["early_access"] and v["files"]:
                return v["id"]
        return versions[0]["id"] if versions else None

    # ---- where it goes ---------------------------------------------------------------------------------------------
    def suggest(self, comfy_type, family):
        if not comfy_type:
            return None
        kind = kinds.kind_of(comfy_type)
        stats = self.library.folder_stats()
        same = stats["by_kind_family"].get((kind, family), {}) if family and family != families.UNKNOWN else {}
        out = folders.suggest(self.cfg, comfy_type, family, families.folder_words(family), same,
                              stats["by_kind"].get(kind, {}), self.remembered.get(comfy_type, family))
        if out:
            root = folders.owning_root(self.cfg, out["path"])
            out["root"] = root["path"] if root else None
            out["rel"] = os.path.relpath(out["path"], root["path"]) if root else out["path"]
            out["rel"] = "" if out["rel"] == "." else out["rel"]
            out["root_label"] = os.path.basename(root["path"].rstrip("\\/")) if root else ""
        return out

    def folder_choices(self, comfy_type):
        return folders.listing(self.cfg, self.library.folder_stats()["by_dir"], comfy_type)

    # ---- example images of a version that is not in the library ----------------------------------------------------
    async def image(self, version_id, index, version=None):
        """The path of a small JPEG of one example image of a version: fetched from civitai the first time and kept
        in the cache folder. `version` is the version object when the caller has it (a queued download); otherwise
        it comes from the models read lately. LookupError when there is no such still image."""
        path = os.path.join(self.cfg.paths.cache, "intake", str(int(version_id)), "%d.jpg" % int(index))
        if os.path.isfile(path):
            return path
        if version is None:
            _model, version = self.cached_version(int(version_id))
        images = [img for img in (version or {}).get("images") or [] if isinstance(img, dict) and img.get("url")]
        if not 0 <= index < len(images) or version_info.is_video(images[index]):
            raise LookupError(version_id)
        lock = self._image_locks.setdefault(path, asyncio.Lock())
        try:
            async with lock:
                if not os.path.isfile(path):
                    url = version_info.sized_image_url(images[index]["url"], thumbs.MAX_WIDTH)
                    data = await self.library.client().fetch_image(url, interactive=True)
                    jpeg, _size = await asyncio.get_running_loop().run_in_executor(None, thumbs.make_cover, data)
                    write_atomic(path, jpeg)
        finally:
            if not lock.locked():
                self._image_locks.pop(path, None)
        return path
