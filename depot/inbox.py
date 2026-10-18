"""Inbox folders: model files dropped into them are sorted into the model library.

The Windows setup sets two: the `models` folder next to the setup itself, sorted once, and a `models` folder next to
the installed program, kept for files added later. The page lists what is in them and where each file would go; only
what the user confirms is moved.

Where a file goes, from its header alone (sniff.py; nothing is asked of civitai):
  - its ComfyUI type (checkpoints, loras, vae, ...) picks the model folder from the settings: the one named like the
    type ("diffusion_models" before ComfyUI's older "unet"), else the first;
  - its base model picks the sub-folder, by the same rules as a download's suggested folder (folders.suggest): where
    the library already keeps that base model, else a folder named after it, else a new one named after it;
  - a language model (a GGUF the local model reads the authors' notes with) goes to the local model's folder, and is
    set up for reading when none is;
  - the old WebUI's files next to a model (its preview, .civitai.info and .json) go with it;
  - a file whose type cannot be told, and any other file, stays where it is.
A file is moved by renaming it when the folders share a drive; across drives it is copied (as <name>.part, renamed
once complete) and the original removed afterwards. An existing file is never replaced: the name gets " (2)".
"""
import asyncio
import logging
import os
import shutil
import time

from . import families, folders, kinds, reader, scan, sidecar, sniff

log = logging.getLogger("depot.inbox")

CHUNK = 8 << 20
# A GGUF whose architecture is a language model's is a text encoder (Z-Image's Qwen3 4B, for instance) below this
# size, and a model to read with above it: the smallest recommended reading model is 15.6 GB.
LLM_MIN_BYTES = 6 << 30
_LLM_ARCHES = {"llama", "qwen2", "qwen3", "qwen3moe", "qwen35", "qwen35moe", "qwen2moe", "gemma", "gemma2", "gemma3",
               "mistral", "mixtral", "phi3", "glm4", "deepseek2", "command-r", "gpt-oss"}


class InboxError(Exception):
    """code: busy | nothing"""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _sidecars(folder, name, names_lower):
    found = sidecar.find(names_lower, name)
    return [found[k] for k in ("preview", "info", "card") if k in found]


class Inbox:
    def __init__(self, cfg, library):
        self.cfg = cfg
        self.library = library
        self.state = {"running": False, "started_at": None, "finished_at": None, "total": 0, "done": 0,
                      "bytes_total": 0, "bytes_done": 0, "current": None, "moved": 0, "failed": [], "llm": None}
        self._task = None
        self._cancel = False
        self._headers = {}      # path -> ((size, mtime), header): a file is read again only when it changed

    # ---- what is there ----------------------------------------------------------------------------------------------
    def folders(self):
        """[(path, once)] of the inbox folders that exist."""
        box = self.cfg.data["inbox"]
        out = [(p, False) for p in box["dirs"]] + [(p, True) for p in box["once"]]
        return [(p, once) for p, once in out if os.path.isdir(p)]

    def _target(self, header, path, size):
        """(comfy_type, family, kind) for a model file, or (None, ...) when it cannot be told."""
        header = header or {}
        content = header.get("content")
        family = header.get("family") or families.UNKNOWN
        if header.get("format") == "gguf" and content in ("text_encoder", "unknown"):
            arch = str((header.get("meta") or {}).get("general.architecture") or "").lower()
            named = os.path.basename(path).lower() in {m["file"].lower() for m in reader.MODELS.values()}
            if named or (arch in _LLM_ARCHES and size >= LLM_MIN_BYTES):
                return "llm", families.UNKNOWN, "llm"
        ctype = scan._CONTENT_TYPE.get(content)
        if not ctype:
            return None, family, None
        return ctype, family, kinds.kind_of(ctype)

    def _type_root(self, comfy_type):
        own = folders.roots(self.cfg, comfy_type)
        if not own:
            return None
        for r in own:
            if os.path.basename(r["path"].rstrip("\\/")).lower() == comfy_type.lower():
                return r["path"]
        return own[0]["path"]

    def _destination(self, comfy_type, family, kind, stats):
        """The folder a file of this type and base model goes to, and whether it has to be made."""
        if comfy_type == "llm":
            return os.path.join(self.cfg.reader_install_dir(), "models")
        root = self._type_root(comfy_type)
        if root is None:
            return None
        same = stats["by_kind_family"].get((kind, family), {}) if family != families.UNKNOWN else {}
        picked = folders.suggest(self.cfg, comfy_type, family, families.folder_words(family), same,
                                 stats["by_kind"].get(kind, {}), None)
        if picked and picked["reason"] in ("library", "name"):
            return picked["path"]
        if family != families.UNKNOWN:
            return os.path.join(root, family)       # a new sub-folder named after the base model
        return root

    def plan(self, stats):
        """What is in the inbox folders: {"folders": [{"path", "once"}], "items": [...], "skipped": [...], "bytes"}.
        items: files that would be moved, each {"path", "name", "bytes", "comfy_type", "kind", "family", "dest",
        "rel", "sidecars"}; skipped: {"path", "name", "bytes", "reason": "not_model" | "unknown" | "no_folder"}.
        stats: library.folder_stats(), taken on the event loop's thread (the index is not shared across threads);
        the rest reads files and runs in a worker thread."""
        boxes = self.folders()
        items, skipped, taken = [], [], set()
        for box, _once in boxes:
            for current, names in scan.walk(box):
                names_lower = {n.lower(): n for n in names}
                claimed = set()
                models = [n for n in names if os.path.splitext(n)[1].lower() in kinds.MODEL_EXTENSIONS]
                for name in models:
                    claimed.update(s.lower() for s in _sidecars(current, name, names_lower))
                for name in sorted(names, key=str.lower):
                    path = os.path.join(current, name)
                    if name.lower().endswith(".part") or name.lower() in claimed:
                        continue
                    try:
                        size = os.path.getsize(path)
                    except OSError:
                        continue
                    if name not in models:
                        skipped.append({"path": path, "name": name, "bytes": size, "reason": "not_model"})
                        continue
                    header = self._header(path, name)
                    ctype, family, kind = self._target(header, path, size)
                    if ctype is None:
                        skipped.append({"path": path, "name": name, "bytes": size, "reason": "unknown"})
                        continue
                    dest_dir = self._destination(ctype, family, kind, stats)
                    if dest_dir is None:
                        skipped.append({"path": path, "name": name, "bytes": size, "reason": "no_folder",
                                        "comfy_type": ctype})
                        continue
                    dest = folders.unique_path(dest_dir, folders.safe_name(name), taken)
                    taken.add(os.path.normcase(dest))
                    root = self.cfg.reader_install_dir() if ctype == "llm" else self._type_root(ctype)
                    rel = os.path.relpath(dest_dir, os.path.dirname(root.rstrip("\\/"))) if root else dest_dir
                    items.append({"path": path, "name": name, "bytes": size, "comfy_type": ctype, "kind": kind,
                                  "family": family, "dest": dest, "rel": rel,
                                  "arch": ((header or {}).get("meta") or {}).get("general.architecture"),
                                  "sidecars": _sidecars(current, name, names_lower)})
        return {"folders": [{"path": p, "once": once} for p, once in boxes], "items": items, "skipped": skipped,
                "bytes": sum(i["bytes"] for i in items)}

    def _header(self, path, name):
        try:
            st = os.stat(path)
        except OSError:
            return None
        state = (st.st_size, st.st_mtime_ns)
        known = self._headers.get(path)
        if known and known[0] == state:
            return known[1]
        header = sniff.sniff(path, name)
        self._headers[path] = (state, header)
        return header

    def forget_spent(self, plan):
        """Take the setup's one-time folders out of the settings once they are gone or hold nothing left to sort
        (what cannot be sorted stays in them, and is not asked about again)."""
        once = self.cfg.data["inbox"]["once"]
        keys = {os.path.normcase(i["path"]) for i in plan["items"]}
        keep = [p for p in once if os.path.isdir(p)
                and any(k.startswith(os.path.normcase(os.path.join(p, ""))) for k in keys)]
        if len(keep) != len(once):
            self.cfg.update({"inbox": {"once": keep}})
            self.cfg.save()

    # ---- moving -----------------------------------------------------------------------------------------------------
    @property
    def busy(self):
        return self._task is not None and not self._task.done()

    async def make_plan(self):
        stats = self.library.folder_stats()
        plan = await asyncio.get_running_loop().run_in_executor(None, self.plan, stats)
        if not self.busy:
            self.forget_spent(plan)
        return plan

    async def start(self, paths):
        """Move the listed files (paths from plan()["items"]); the plan is made again, so that only files still there
        and still sortable are moved."""
        if self.busy:
            raise InboxError("busy")
        wanted = {os.path.normcase(os.path.normpath(p)) for p in paths if isinstance(p, str)}
        items = [i for i in (await self.make_plan())["items"] if os.path.normcase(i["path"]) in wanted]
        if not items:
            raise InboxError("nothing")
        self._cancel = False
        self.state.update(running=True, started_at=time.time(), finished_at=None, total=len(items), done=0,
                          bytes_total=sum(i["bytes"] for i in items), bytes_done=0, current=None, moved=0, failed=[],
                          llm=None)
        self._task = asyncio.ensure_future(self._run(items))

    def stop(self):
        self._cancel = True

    async def close(self):
        self._cancel = True
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self, items):
        loop = asyncio.get_running_loop()
        try:
            for item in items:
                if self._cancel:
                    break
                self.state["current"] = item["name"]
                try:
                    dest = await loop.run_in_executor(None, self._move_one, item)
                    self.state["moved"] += 1
                    if item["comfy_type"] == "llm":
                        self.state["llm"] = dest
                except (OSError, ValueError) as e:
                    if self._cancel:
                        break
                    log.warning("could not move %s: %s", item["path"], e)
                    self.state["failed"].append({"name": item["name"], "error": str(e)})
                self.state["done"] += 1
            if self.state["llm"]:
                self._use_llm(self.state["llm"], await loop.run_in_executor(None, reader.detect_hardware))
        finally:
            self.state.update(running=False, current=None, finished_at=time.time())
            # the setup's folder is forgotten once nothing is left to sort in it
            self.forget_spent(await loop.run_in_executor(None, self.plan, self.library.folder_stats()))
            if self.state["moved"]:
                self.library.request_scan()

    def _move_one(self, item):
        src, dest = item["path"], item["dest"]
        folder = os.path.dirname(dest)
        os.makedirs(folder, exist_ok=True)
        if item["comfy_type"] != "llm":
            folders.check_destination(self.cfg, folder)         # never outside the model folders of the settings
        if os.path.exists(dest):
            dest = folders.unique_path(folder, os.path.basename(dest))
        stem = os.path.splitext(os.path.basename(dest))[0]
        own_stem = os.path.splitext(item["name"])[0]
        pairs = [(src, dest)]
        for side in item["sidecars"]:
            side_src = os.path.join(os.path.dirname(src), side)
            side_dest = os.path.join(folder, stem + side[len(own_stem):])
            if os.path.isfile(side_src) and not os.path.exists(side_dest):
                pairs.append((side_src, side_dest))
        for a, b in pairs:
            self._move_file(a, b, counted=(a == src))
        return dest

    def _move_file(self, src, dest, counted):
        try:
            os.replace(src, dest)
            if counted:
                self.state["bytes_done"] += os.path.getsize(dest)
            return
        except OSError as e:
            if getattr(e, "winerror", None) != 17 and getattr(e, "errno", None) != 18:   # not "another drive"
                raise
        part = dest + ".part"
        try:
            with open(src, "rb") as fin, open(part, "wb") as fout:
                while True:
                    if self._cancel:
                        raise OSError("cancelled")
                    block = fin.read(CHUNK)
                    if not block:
                        break
                    fout.write(block)
                    if counted:
                        self.state["bytes_done"] += len(block)
            if os.path.getsize(part) != os.path.getsize(src):
                raise OSError("the copy of %s is incomplete" % os.path.basename(src))
            shutil.copystat(src, part)
            os.replace(part, dest)
        except BaseException:
            try:
                os.remove(part)
            except OSError:
                pass
            raise
        os.remove(src)

    def _use_llm(self, path, hw):
        """A language model was moved in: read the authors' notes with it when no local model is set up yet and the
        graphics card is large enough for the smallest recommended model."""
        rd = self.cfg.data["reader"]
        if rd["mode"] == "managed" and os.path.isfile(rd["managed"]["model_file"]):
            return
        server = rd["managed"]["server"]
        if not server or not os.path.isfile(server):
            return
        vram = max((g["vram_mb"] for g in hw.get("gpus") or []), default=0)
        if vram < reader.MIN_VRAM_GB * 1024 * 0.9:
            return
        if not self.cfg.update({"reader": {"mode": "managed", "managed": {"model_file": path}}}):
            self.cfg.save()
            self.library.reader.reconfigure()
            log.info("local model set up for reading: %s", path)

    def progress(self):
        return dict(self.state, failed=list(self.state["failed"]))
