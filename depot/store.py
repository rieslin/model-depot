"""Records: what Model Depot knows about each model, one small JSON file per fingerprint, plus a cover thumbnail.

This folder is the part worth keeping. The index can be rebuilt from it and from the model files. It holds no paths
of this computer, so it can sit in a folder that two computers sync; for that reason every write goes to a .tmp
file first and is then renamed (sync tools pick up half-written files otherwise, and skip .tmp).

    models/<sha256>.json    {"sha256", "civitai": {"site", "fetched_at", "version"}, "sidecar": {...}, "cover": {...},
                             "card": {...} (cards.py), "user": {"title", "base_model"}, "images": [...]}
    covers/<sha256>.jpg
    pages/<model id>.json   {"model_id", "site", "fetched_at", "model"}: civitai's model page (the author's notes,
                             tags, creator, every version), read when the model is downloaded or opened
    images/<id>.jpg         a picture the user added to a model's examples (record["images"] says which, with its
                             generation data); kept here, not in the cache, because nothing could fetch it again
    names/<model id>.json   the name a civitai model goes by here, settled once (names.py)
"""
import json
import os
import re
import uuid

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
RECORD_SCHEMA = 1


def is_sha256(text):
    return isinstance(text, str) and bool(_SHA_RE.match(text))


def write_atomic(path, data):
    """Write bytes to `path` through a uniquely named .tmp file. If another writer put the same file in place
    first (two requests producing the same thumbnail), theirs is kept."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%s.tmp" % (path, uuid.uuid4().hex[:8])
    with open(tmp, "wb") as f:
        f.write(data)
    try:
        os.replace(tmp, path)
    except OSError:
        # Windows refuses to replace a file that is being read. If the target is there, someone else made it.
        try:
            os.remove(tmp)
        except OSError:
            pass
        if not os.path.exists(path):
            raise


class Store:
    def __init__(self, root):
        self.root = root
        self.models_dir = os.path.join(root, "models")
        self.covers_dir = os.path.join(root, "covers")
        self.pages_dir = os.path.join(root, "pages")
        self.images_dir = os.path.join(root, "images")
        self.names_dir = os.path.join(root, "names")

    def record_path(self, sha256):
        if not is_sha256(sha256):
            raise ValueError("not a sha256")
        return os.path.join(self.models_dir, sha256 + ".json")

    def cover_path(self, sha256):
        if not is_sha256(sha256):
            raise ValueError("not a sha256")
        return os.path.join(self.covers_dir, sha256 + ".jpg")

    def load(self, sha256):
        """The record, or a fresh empty one."""
        try:
            with open(self.record_path(sha256), encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("sha256", sha256)
                return data
        except (OSError, ValueError):
            pass
        return {"schema": RECORD_SCHEMA, "sha256": sha256}

    def exists(self, sha256):
        return os.path.isfile(self.record_path(sha256))

    def save(self, sha256, record):
        record["schema"] = RECORD_SCHEMA
        record["sha256"] = sha256
        write_atomic(self.record_path(sha256), json.dumps(record, ensure_ascii=False, indent=1).encode("utf-8"))

    def has_cover(self, sha256):
        return os.path.isfile(self.cover_path(sha256))

    def save_cover(self, sha256, jpeg_bytes):
        write_atomic(self.cover_path(sha256), jpeg_bytes)

    def all_sha256(self):
        """Fingerprints that have a record, for rebuilding the index."""
        try:
            names = os.listdir(self.models_dir)
        except OSError:
            return []
        return sorted(n[:-5] for n in names if n.endswith(".json") and is_sha256(n[:-5]))

    # ---- pictures the user added ------------------------------------------------------------------------------------
    def image_path(self, image_id):
        if not isinstance(image_id, str) or not re.match(r"^[0-9a-f]{16,32}$", image_id):
            raise ValueError("not an image id")
        return os.path.join(self.images_dir, image_id + ".jpg")

    def save_image(self, image_id, jpeg_bytes):
        write_atomic(self.image_path(image_id), jpeg_bytes)

    # ---- model pages -----------------------------------------------------------------------------------------------
    def page_path(self, model_id):
        model_id = int(model_id)
        if model_id <= 0:
            raise ValueError("not a model id")
        return os.path.join(self.pages_dir, "%d.json" % model_id)

    def load_page(self, model_id):
        """{"model_id", "site", "fetched_at", "model"}, or None when the page has not been read. A page civitai no
        longer has is {"model": None, "error": "not_found", ...}."""
        try:
            with open(self.page_path(model_id), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError, TypeError):
            return None
        if isinstance(data, dict) and (isinstance(data.get("model"), dict) or data.get("error")):
            return data
        return None

    # ---- the names models go by -------------------------------------------------------------------------------------
    def load_names(self):
        """{model id: entry} of every name settled so far."""
        out = {}
        try:
            files = os.listdir(self.names_dir)
        except OSError:
            return out
        for n in files:
            if not re.match(r"^\d+\.json$", n):
                continue
            try:
                with open(os.path.join(self.names_dir, n), encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and isinstance(data.get("name"), str) and data["name"]:
                out[int(n[:-5])] = data
        return out

    def save_name(self, model_id, entry):
        model_id = int(model_id)
        if model_id <= 0:
            raise ValueError("not a model id")
        entry = dict(entry, schema=RECORD_SCHEMA, model_id=model_id)
        write_atomic(os.path.join(self.names_dir, "%d.json" % model_id), json.dumps(entry, ensure_ascii=False).encode("utf-8"))
        return entry

    def save_page(self, model_id, site, model, fetched_at, error=None):
        data = {"schema": RECORD_SCHEMA, "model_id": int(model_id), "site": site, "fetched_at": fetched_at, "model": model}
        if error:
            data["error"] = error
        write_atomic(self.page_path(model_id), json.dumps(data, ensure_ascii=False).encode("utf-8"))
        return data
