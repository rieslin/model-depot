"""Inbox folders: model files dropped into them are sorted into the model library.

The Windows setup sets two: the `models` folder next to the setup itself, sorted once, and a `models` folder next to
the installed program, kept for files added later. A folder that holds other things too, such as the browser's
downloads (where ComfyUI's "download missing model" saves to), can be watched instead: only the model files directly
in it are listed, and the rest of it is neither listed nor walked. The page lists what is in them and where each file
would go; only what the user confirms is moved.

Where a file goes, from its header alone (sniff.py; nothing is asked of civitai):
  - its ComfyUI type (checkpoints, loras, vae, ...) picks the model folder from the settings: the one named like the
    type ("diffusion_models" before ComfyUI's older "unet"), else the first;
  - its base model picks the sub-folder, by the same rules as a download's suggested folder (folders.suggest): where
    the library already keeps that base model, else a folder named after it; else the type's folder itself. No new
    sub-folder is made: workflows name files relative to the type's folder, and a file moved into a new "krea2\" was
    no longer the name the bundled workflows asked for (review of 2026-10-20);
  - a language model the program recommends for reading the authors' notes (by its file name) goes to the local
    model's folder, and is set up for reading when none is; every other GGUF is a model for ComfyUI (a text encoder
    such as Flux.2's 24B one, or a diffusion model), however large;
  - the old WebUI's files next to a model (its preview, .civitai.info and .json) go with it;
  - a file whose type cannot be told, and any other file, stays where it is.
A file is moved by renaming it when the folders share a drive; across drives it is copied (as <name>.part, renamed
once complete) and the original removed afterwards, except on a removable drive (a USB stick the models came on),
where the original stays. An existing file is never replaced, nor is the new one renamed: a file of the same name
and size is taken to be there already, one of the same name and another size is left for the user to sort by hand.
"""
import asyncio
import ctypes
import logging
import os
import re
import shutil
import sys
import time

from . import companions, families, folders, kinds, reader, scan, sidecar, sniff

log = logging.getLogger("depot.inbox")

CHUNK = 8 << 20


_UPSCALER_NAME = re.compile(r"(^|[^a-z0-9])\d{1}x[-_ ]|esrgan|realesr|upscal|swinir|\bdat\b|span|compact", re.I)


class InboxError(Exception):
    """code: busy | nothing"""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _removable(path):
    """Is this on a removable drive (Windows' DRIVE_REMOVABLE: USB sticks, card readers)?"""
    if sys.platform != "win32":
        return False
    root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
    try:
        return ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)) == 2
    except (AttributeError, OSError):
        return False


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
        """[(path, mode)] of the inbox folders that exist; mode "dirs", "once" or "watch" (the settings list)."""
        box = self.cfg.data["inbox"]
        out = [(p, mode) for mode in ("dirs", "once", "watch") for p in box.get(mode) or []]
        return [(p, mode) for p, mode in out if os.path.isdir(p)]

    def _target(self, header, path, size):
        """(comfy_type, family, kind) for a model file, or (None, ...) when it cannot be told."""
        if not header and _UPSCALER_NAME.search(os.path.basename(path)) and \
                os.path.splitext(path)[1].lower() in (".pth", ".pt", ".bin"):
            # an old upscaler in pickle form, which has no header to read: its name says it ("4x-UltraSharp.pth")
            return "upscale_models", families.UNKNOWN, kinds.kind_of("upscale_models")
        header = header or {}
        content = header.get("content")
        family = header.get("family") or families.UNKNOWN
        if header.get("format") == "gguf" and content in ("text_encoder", "unknown"):
            # by size or architecture a text encoder cannot be told from a chat model (Flux.2's text encoder is a 24B
            # Mistral, 14 GB and more as GGUF): only the reading models the program recommends count as one
            if os.path.basename(path).lower() in {m["file"].lower() for m in reader.MODELS.values()}:
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

    def _destination(self, comfy_type, family, kind, stats, size=None):
        """The folder a file of this type and base model goes to (made when it is moved). A file the companions list
        puts in a sub-folder (a prompt enhancer, by its exact size) goes there."""
        if comfy_type == "llm":
            return os.path.join(self.cfg.reader_install_dir(), "models")
        root = self._type_root(comfy_type)
        if root is None:
            return None
        sub = companions.subfolder(comfy_type, size=size)
        if sub:
            return os.path.join(root, sub)
        same = stats["by_kind_family"].get((kind, family), {}) if family != families.UNKNOWN else {}
        picked = folders.suggest(self.cfg, comfy_type, family, families.folder_words(family), same,
                                 stats["by_kind"].get(kind, {}), None)
        if picked and picked["reason"] in ("library", "name"):
            return picked["path"]
        return root

    def plan(self, stats):
        """What is in the inbox folders: {"folders": [{"path", "once", "watch"}], "items": [...], "skipped": [...],
        "bytes"}. A watched folder adds only its own model files: no sub-folders, nothing to the skipped list but files
        already in the library or blocked by a file of the same name.
        items: files that would be moved, each {"path", "name", "bytes", "comfy_type", "kind", "family", "dest",
        "rel", "sidecars", "keep"}; keep: the original stays (a removable drive). skipped: {"path", "name", "bytes",
        "reason": "not_model" | "unknown" | "no_folder" | "already_there" | "name_taken", "dest"?}.
        stats: library.folder_stats(), taken on the event loop's thread (the index is not shared across threads);
        the rest reads files and runs in a worker thread."""
        boxes = self.folders()
        items, skipped, taken = [], [], set()
        for box, mode in boxes:
            watched = mode == "watch"
            for current, names in scan.walk(box):
                if watched and os.path.normcase(current) != os.path.normcase(box):
                    break           # a watched folder: only the files directly in it (os.walk gives the folder first)
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
                        if not watched:
                            skipped.append({"path": path, "name": name, "bytes": size, "reason": "not_model"})
                        continue
                    header = self._header(path, name)
                    ctype, family, kind = self._target(header, path, size)
                    if ctype is None:
                        if not watched:     # (a downloads folder's other .bin and .onnx files are none of ours)
                            skipped.append({"path": path, "name": name, "bytes": size, "reason": "unknown"})
                        continue
                    dest_dir = self._destination(ctype, family, kind, stats, size)
                    if dest_dir is None:
                        skipped.append({"path": path, "name": name, "bytes": size, "reason": "no_folder",
                                        "comfy_type": ctype})
                        continue
                    dest = os.path.join(dest_dir, folders.safe_name(name))
                    if os.path.normcase(dest) in taken or os.path.exists(dest):
                        try:
                            same = os.path.getsize(dest) == size
                        except OSError:
                            same = False
                        skipped.append({"path": path, "name": name, "bytes": size, "dest": dest,
                                        "reason": "already_there" if same else "name_taken"})
                        continue
                    taken.add(os.path.normcase(dest))
                    root = self.cfg.reader_install_dir() if ctype == "llm" else self._type_root(ctype)
                    rel = os.path.relpath(dest_dir, os.path.dirname(root.rstrip("\\/"))) if root else dest_dir
                    items.append({"path": path, "name": name, "bytes": size, "comfy_type": ctype, "kind": kind,
                                  "family": family, "dest": dest, "rel": rel,
                                  "arch": ((header or {}).get("meta") or {}).get("general.architecture"),
                                  "sidecars": _sidecars(current, name, names_lower), "keep": _removable(path)})
        return {"folders": [{"path": p, "once": mode == "once", "watch": mode == "watch"} for p, mode in boxes],
                "items": items, "skipped": skipped,
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
            raise OSError("%s is already there" % os.path.basename(dest))
        stem = os.path.splitext(os.path.basename(dest))[0]
        own_stem = os.path.splitext(item["name"])[0]
        pairs = [(src, dest)]
        for side in item["sidecars"]:
            side_src = os.path.join(os.path.dirname(src), side)
            side_dest = os.path.join(folder, stem + side[len(own_stem):])
            if os.path.isfile(side_src) and not os.path.exists(side_dest):
                pairs.append((side_src, side_dest))
        for a, b in pairs:
            self._move_file(a, b, counted=(a == src), keep=item.get("keep", False))
        return dest

    def _move_file(self, src, dest, counted, keep=False):
        if not keep:
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
        if not keep:
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
