"""The download queue: one file at a time from civitai into a model folder, resumed where it stopped, checked against
civitai's fingerprint, then handed to the library.

- A download is written to "<name>.part" in its destination folder (ComfyUI and the scan ignore that name). It is
  renamed to its real name only after its SHA-256 matches the one civitai lists; a file that does not match is not
  kept. The fingerprint is computed while the file is written, so checking a big file costs nothing extra.
- Pausing, a broken connection or stopping the program leaves the .part file; the next try asks civitai for a fresh
  address and continues from the end of the .part file (a byte-range request).
- A broken connection, a busy or failing server, or an address that ran out is tried again after 10 s, 30 s, 90 s,
  5 min and 10 min. A login the creator requires, a refused key, early access, a missing file, a full disk or a
  fingerprint that does not match stop the download until someone retries it.
- Downloads run one after another: civitai is not asked for several files at once, and one file finishes sooner.
- Only folders from the settings are written to (folders.check_destination), and an existing file is never replaced.
- Deleting is limited to this module's own .part files (a cancelled download, or one whose fingerprint did not match).
  Model files are never deleted here.
"""
import asyncio
import concurrent.futures
import hashlib
import json
import logging
import os
import re
import shutil
import time

import aiohttp

from . import families, folders, hfhub, intake as intake_mod, kinds, trash, version_info
from .civitai import CivitaiError

log = logging.getLogger("depot.downloads")

READ_CHUNK = 1 << 20            # read from the network in pieces of this size
WRITE_BLOCK = 8 << 20           # hand to the writing thread in blocks of this size
SPACE_MARGIN = 256 << 20        # free space left on the disk after a download
RETRY_DELAYS = (10.0, 30.0, 90.0, 300.0, 600.0)
SAVE_EVERY = 2.0                # seconds between progress notes in the index
EMIT_EVERY = 0.5                # seconds between progress events

_TRANSIENT = {"network", "server", "rate_limited", "link_expired", "incomplete", "range_mismatch"}
_CONTENT_RANGE_RE = re.compile(r"bytes\s+(?:(\d+)-(\d+)|\*)/(\d+|\*)", re.I)


class Failed(Exception):
    def __init__(self, code, detail="", transient=None):
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.transient = code in _TRANSIENT if transient is None else transient


class _Stopped(Exception):
    """Pause or cancel, asked for while the file was being transferred."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class AddError(Exception):
    """A download that cannot be added. code: civitai | not_found | bad_request | folder_outside | folder_missing."""

    def __init__(self, code, **params):
        super().__init__(code)
        self.code = code
        self.params = params


def _content_range(value):
    """(first byte, total) from a Content-Range header; either may be None."""
    m = _CONTENT_RANGE_RE.match(value or "")
    if not m:
        return None, None
    first = int(m.group(1)) if m.group(1) is not None else None
    total = int(m.group(3)) if m.group(3) not in (None, "*") else None
    return first, total


def _json_list(text):
    try:
        value = json.loads(text) if text else []
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def _json_obj(text):
    try:
        value = json.loads(text) if text else None
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


class _Sink:
    """The .part file and the fingerprint of what is in it. Its methods run in a worker thread."""

    def __init__(self, path):
        self.path = path
        self.hasher = hashlib.sha256()
        self.size = 0

    def load(self):
        """Take in what an earlier try left behind."""
        if not os.path.isfile(self.path):
            return 0
        with open(self.path, "rb") as f:
            while True:
                block = f.read(WRITE_BLOCK)
                if not block:
                    break
                self.hasher.update(block)
                self.size += len(block)
        return self.size

    def append(self, block):
        with open(self.path, "ab") as f:
            f.write(block)
        self.hasher.update(block)
        self.size += len(block)

    def restart(self):
        _remove(self.path)
        self.hasher = hashlib.sha256()
        self.size = 0


class Downloads:
    def __init__(self, cfg, index, library, intake, retry_delays=RETRY_DELAYS):
        self.cfg = cfg
        self.db = index
        self.library = library
        self.intake = intake
        intake.queued_files = self.db.dl_active_files
        self._retry_delays = tuple(retry_delays)
        self._wake = None
        self._task = None
        self._pool = None
        self._stopping = False
        self._live = {}                 # id -> progress of the download being transferred
        self._labels = {}               # destination folder -> (settings folder name, path inside it), for the list
        self.listeners = []             # called with an event dict on every change

    # ---- life cycle ------------------------------------------------------------------------------------------------
    async def start(self):
        self._wake = asyncio.Event()
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="depot-dl")
        n = self.db.dl_reset_interrupted()
        if n:
            log.info("%d interrupted downloads back in the queue", n)
        self._task = asyncio.ensure_future(self._loop())

    async def stop(self):
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._pool is not None:
            self._pool.shutdown(wait=True)

    def _kick(self):
        if self._wake is not None:
            self._wake.set()

    def _emit(self, event):
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception:  # noqa: BLE001
                log.exception("download listener failed")

    def _changed(self, download_id):
        row = self.db.dl_get(download_id)
        if row is None:
            self._emit({"type": "download", "id": download_id, "state": "removed"})
            return
        item = self._item(row)
        self._emit({"type": "download", "id": download_id, "state": item["state"], "bytes_done": item["bytes_done"],
                    "bytes_total": item["bytes_total"], "speed": item["speed"], "error": item["error"],
                    "sha256": item["sha256"]})

    def _set(self, download_id, **fields):
        self.db.dl_update(download_id, **fields)
        self._changed(download_id)

    # ---- adding ----------------------------------------------------------------------------------------------------
    async def add(self, model_id, version_id, file_ids, folder, retire=None):
        """Queue files of one version. Returns {"added": [ids], "skipped": [{"file_id", "reason", "name"}]}.

        `retire`: fingerprints of other versions of the same model in the library, to go to the recycle bin once the
        version's main file is downloaded and checked. Any that is not another version of this model is ignored."""
        try:
            site, model = await self.intake.model(model_id, version_id)
        except CivitaiError as e:
            raise AddError("civitai", code=e.code) from e
        version = next((v for v in model.get("modelVersions") or []
                        if isinstance(v, dict) and str(v.get("id")) == str(version_id)), None)
        if version is None:
            raise AddError("not_found")
        try:
            dest_dir = folders.check_destination(self.cfg, folder)
        except ValueError as e:
            raise AddError("folder_" + str(e)) from e
        wanted = [int(f) for f in file_ids or [] if str(f).isdigit()]
        entries = [f for fid in wanted for f in version.get("files") or [] if isinstance(f, dict) and f.get("id") == fid]
        if not entries:
            raise AddError("bad_request")
        record_json = json.dumps(intake_mod.version_record(model, version), ensure_ascii=False)
        family = families.from_base_model(version.get("baseModel"))
        images = [img for img in version.get("images") or [] if isinstance(img, dict) and img.get("url")]
        cover = intake_mod.first_still(images)
        present = self.library.present_by_hash()
        taken = self.db.dl_active_paths()
        replaced = []
        for sha in retire or []:
            known = self.db.model(str(sha).lower()) if str(sha).lower() in present else None
            if known is not None and known["model_id"] == int(model["id"]) and known["version_id"] != int(version["id"]):
                replaced.append(known["sha256"])
        main = next((e for e in entries if e.get("primary")), entries[0])
        added, skipped = [], []
        for entry in entries:
            sha = str(((entry.get("hashes") or {}).get("SHA256")) or "").lower() or None
            name = folders.safe_name(entry.get("name"), "%s_%s.safetensors" % (version.get("id"), entry.get("id")))
            if sha and sha in present:
                skipped.append({"file_id": entry["id"], "reason": "in_library", "name": present[sha]})
                continue
            if self.db.dl_active_for_file(entry["id"]):
                skipped.append({"file_id": entry["id"], "reason": "queued", "name": name})
                continue
            path = folders.unique_path(dest_dir, name, taken)
            taken.add(os.path.normcase(path))
            download_id = self.db.dl_insert({
                "state": "queued", "site": site, "model_id": int(model["id"]), "version_id": int(version["id"]),
                "file_id": int(entry["id"]), "title": str(model.get("name") or "").strip() or None,
                "version_name": str(version.get("name") or "").strip() or None, "model_type": model.get("type"),
                "comfy_type": folders.target_type(model.get("type"), entry.get("type")), "family": family,
                "base_model": version.get("baseModel"), "file_name": str(entry.get("name") or name),
                "dest_dir": dest_dir, "dest_name": os.path.basename(path),
                "expected_sha256": sha if sha and len(sha) == 64 else None,
                "expected_bytes": intake_mod.file_bytes(entry) or None,
                "cover_index": cover,
                "cover_level": version_info.image_level(images[cover]) if cover is not None else None,
                "version_json": record_json,
                "retire": json.dumps(replaced) if replaced and entry is main else None,
            })
            added.append(download_id)
            self._changed(download_id)
        if added:
            ctype = folders.target_type(model.get("type"), entries[0].get("type"))
            if ctype:
                self.intake.remembered.put(ctype, family, dest_dir)
            # The model page just read (the author's notes, the other versions) is kept: the card of the downloaded
            # version is drafted from it, and nothing more needs to be asked of civitai for that.
            self.library.keep_page(int(model["id"]), site, model)
            self._kick()
        return {"added": added, "skipped": skipped}

    async def add_url(self, url, folder, comfy_type=None):
        """Queue one Hugging Face file. Returns {"added": [ids], "skipped": [...]}, as add() does."""
        link = hfhub.parse(str(url or ""))
        if link.get("error"):
            raise AddError("bad_request")
        try:
            info = await hfhub.head(link["hf"], proxy=self.cfg.proxy())
        except hfhub.HFError as e:
            raise AddError("hf", code=e.code) from e
        try:
            dest_dir = folders.check_destination(self.cfg, folder)
        except ValueError as e:
            raise AddError("folder_" + str(e)) from e
        fid = hfhub.file_id(link["hf"])
        name = folders.safe_name(link["name"], "download.safetensors")
        sha = info.get("sha256")
        present = self.library.present_by_hash()
        if sha and sha in present:
            return {"added": [], "skipped": [{"file_id": fid, "reason": "in_library", "name": present[sha]}]}
        if self.db.dl_active_for_file(fid):
            return {"added": [], "skipped": [{"file_id": fid, "reason": "queued", "name": name}]}
        ctype = comfy_type or hfhub.guess_type(link["path"]) or hfhub.type_of_folder(self.cfg, dest_dir)
        path = folders.unique_path(dest_dir, name, self.db.dl_active_paths())
        download_id = self.db.dl_insert({
            "state": "queued", "site": hfhub.SITE, "model_id": 0, "version_id": 0, "file_id": fid,
            "title": name, "version_name": link["repo"], "model_type": None, "comfy_type": ctype,
            "family": families.UNKNOWN, "base_model": None, "file_name": link["name"],
            "dest_dir": dest_dir, "dest_name": os.path.basename(path),
            "expected_sha256": sha, "expected_bytes": info.get("bytes"), "cover_index": None, "cover_level": None,
            "version_json": "{}", "url": link["hf"],
        })
        self._changed(download_id)
        self._kick()
        return {"added": [download_id], "skipped": []}

    # ---- what someone can do ---------------------------------------------------------------------------------------
    def pause(self, download_id):
        row = self._need(download_id)
        if download_id in self._live:
            self._live[download_id]["stop"] = "pause"
        elif row["state"] in ("queued", "retrying"):
            self._set(download_id, state="paused", next_try_at=None)

    def resume(self, download_id):
        row = self._need(download_id)
        if row["state"] in ("paused", "retrying"):
            self._set(download_id, state="queued", next_try_at=None)
            self._kick()

    def retry(self, download_id):
        row = self._need(download_id)
        if row["state"] in ("failed", "retrying", "paused"):
            self._set(download_id, state="queued", attempts=0, next_try_at=None, error=None, error_detail=None)
            self._kick()

    def remove(self, download_id):
        """Take a download off the list. One that is not finished is cancelled and its .part file deleted."""
        row = self._need(download_id)
        if download_id in self._live:
            self._live[download_id]["stop"] = "cancel"
            return
        if row["state"] != "done":
            _remove(self._part(row))
        self.db.dl_delete(download_id)
        self._emit({"type": "download", "id": download_id, "state": "removed"})

    def clear_finished(self):
        n = 0
        for row in self.db.dl_list():
            if row["state"] == "done":
                self.db.dl_delete(row["id"])
                self._emit({"type": "download", "id": row["id"], "state": "removed"})
                n += 1
        return n

    def _need(self, download_id):
        row = self.db.dl_get(download_id)
        if row is None:
            raise LookupError(download_id)
        return row

    # ---- reading ---------------------------------------------------------------------------------------------------
    @staticmethod
    def _part(row):
        return os.path.join(row["dest_dir"], row["dest_name"]) + ".part"

    def _label(self, dest_dir):
        """(the settings folder's name, the path inside it) for showing a destination; asked of the disk once."""
        key = (dest_dir, tuple(f["path"] for f in self.cfg.folders()))
        if key not in self._labels:
            root = folders.owning_root(self.cfg, dest_dir)
            rel = os.path.relpath(dest_dir, root["path"]) if root else dest_dir
            self._labels[key] = (os.path.basename(root["path"].rstrip("\\/")) if root else "", "" if rel == "." else rel)
        return self._labels[key]

    def _item(self, row):
        live = self._live.get(row["id"])
        done = live["done"] if live else row["bytes_done"]
        total = (live or {}).get("total") or row["bytes_total"] or row["expected_bytes"]
        speed = self._speed(live) if live else 0.0
        root_label, rel = self._label(row["dest_dir"])
        return {
            "id": row["id"], "state": row["state"], "created_at": row["created_at"], "finished_at": row["finished_at"],
            "model_id": row["model_id"], "version_id": row["version_id"], "file_id": row["file_id"],
            "title": row["title"], "version_name": row["version_name"], "model_type": row["model_type"],
            "kind": kinds.kind_of(row["comfy_type"]) if row["comfy_type"] else "other", "comfy_type": row["comfy_type"],
            "family": row["family"], "base_model": row["base_model"], "file_name": row["file_name"],
            "dest_dir": row["dest_dir"], "dest_name": row["dest_name"], "dest_root_label": root_label, "dest_rel": rel,
            "bytes_done": done, "bytes_total": total, "speed": speed,
            "eta": (total - done) / speed if speed > 0 and total and total > done else None,
            "attempts": row["attempts"], "next_try_at": row["next_try_at"],
            "error": row["error"], "error_detail": row["error_detail"],
            "sha256": row["sha256"], "final_path": row["final_path"], "checked": bool(row["expected_sha256"]),
            "source": "hf" if row["url"] else "civitai",
            "civitai_url": row["url"].replace("/resolve/", "/blob/", 1) if row["url"] else
                           "%s/models/%s?modelVersionId=%s" % (row["site"], row["model_id"], row["version_id"]),
            "cover": "/api/ui/intake/versions/%d/images/%d" % (row["version_id"], row["cover_index"])
                     if row["cover_index"] is not None else None,
            "cover_level": row["cover_level"],
            "replaces": len(_json_list(row["retire"])),
            "retired": _json_obj(row["retired"]),
        }

    def items(self):
        return [self._item(row) for row in self.db.dl_list()]

    def version_object(self, version_id):
        """The version JSON a download keeps, for its example image after the preview is gone."""
        for row in self.db.dl_list():
            if row["version_id"] == version_id and not row["url"]:
                try:
                    return json.loads(row["version_json"])
                except ValueError:
                    return None
        return None

    @staticmethod
    def _speed(live):
        samples = live["samples"]
        if len(samples) < 2 or samples[-1][0] <= samples[0][0]:
            return 0.0
        return max(0.0, (samples[-1][1] - samples[0][1]) / (samples[-1][0] - samples[0][0]))

    # ---- the worker ------------------------------------------------------------------------------------------------
    async def _loop(self):
        while not self._stopping:
            self._wake.clear()
            row = self.db.dl_next(time.time())
            if row is None:
                wait = self.db.dl_next_wait(time.time())
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=wait if wait is not None else 3600)
                except asyncio.TimeoutError:
                    pass
                continue
            try:
                await self._run(row)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - one bad download must not end the queue
                log.exception("download %s failed unexpectedly", row["id"])
                self._set(row["id"], state="failed", error="internal", error_detail=type(e).__name__)

    async def _run(self, row):
        download_id = row["id"]
        live = {"done": row["bytes_done"], "total": row["bytes_total"], "samples": [], "stop": None,
                "saved": time.monotonic(), "emitted": 0.0}
        self._live[download_id] = live
        self._set(download_id, state="running", error=None, error_detail=None)
        try:
            final, digest = await self._transfer(row, live)
        except _Stopped as stop:
            self._live.pop(download_id, None)
            if stop.reason == "cancel":
                _remove(self._part(row))
                self.db.dl_delete(download_id)
                self._emit({"type": "download", "id": download_id, "state": "removed"})
            else:
                self._set(download_id, state="paused", bytes_done=live["done"])
            return
        except Failed as e:
            self._live.pop(download_id, None)
            self._fail(row, live, e)
            return
        except asyncio.CancelledError:
            self._live.pop(download_id, None)
            self.db.dl_update(download_id, bytes_done=live["done"])     # the program is stopping; resumed at start
            raise
        self._live.pop(download_id, None)
        self._set(download_id, state="done", bytes_done=live["done"], bytes_total=live["done"], final_path=final,
                  sha256=digest, finished_at=time.time(), attempts=0, next_try_at=None, error=None, error_detail=None)
        log.info("download %s done: %s", download_id, final)
        await self._retire(row)

    async def _retire(self, row):
        """The versions this download replaces go to the recycle bin, now that the new one is in the library and its
        fingerprint checked, each copy with the old WebUI's files beside it. What became of them is kept with the
        download, for its row in the queue."""
        shas = _json_list(row["retire"])
        if not shas:
            return
        moved, error = [], None
        for sha in shas:
            paths = [p["path"] for p in self.library.trash_plan(sha) or []]
            if not paths:
                continue            # gone already
            try:
                moved += [os.path.basename(p) for p in await self.library.trash_files(sha, paths)]
            except (ValueError, trash.TrashError) as e:
                error = getattr(e, "code", None) or str(e)
                log.warning("download %s: the version it replaces stays where it is: %s", row["id"], e)
        self._set(row["id"], retired=json.dumps({"moved": moved, "error": error}, ensure_ascii=False))

    def _fail(self, row, live, e):
        attempts = row["attempts"] + 1
        detail = str(e.detail or "")[:300]
        if e.transient and attempts <= len(self._retry_delays):
            delay = self._retry_delays[attempts - 1]
            log.info("download %s interrupted (%s); trying again in %.0f s", row["id"], e.code, delay)
            self._set(row["id"], state="retrying", attempts=attempts, next_try_at=time.time() + delay, error=e.code,
                      error_detail=detail, bytes_done=live["done"])
        else:
            log.info("download %s failed: %s %s", row["id"], e.code, detail)
            self._set(row["id"], state="failed", attempts=attempts, next_try_at=None, error=e.code,
                      error_detail=detail, bytes_done=live["done"])

    def _progress(self, download_id, live, force=False):
        now = time.monotonic()
        samples = live["samples"]
        if not samples or now - samples[-1][0] >= 0.2 or force:
            samples.append((now, live["done"]))
            while len(samples) > 2 and now - samples[0][0] > 6:
                samples.pop(0)
        if force or now - live["saved"] >= SAVE_EVERY:
            live["saved"] = now
            self.db.dl_update(download_id, bytes_done=live["done"])
        if force or now - live["emitted"] >= EMIT_EVERY:
            live["emitted"] = now
            self._emit({"type": "download", "id": download_id, "state": "running", "bytes_done": live["done"],
                        "bytes_total": live["total"], "speed": self._speed(live)})

    async def _transfer(self, row, live):
        """Bring the .part file to its full size and check it. Returns (final path, fingerprint)."""
        loop = asyncio.get_running_loop()
        download_id = row["id"]
        try:
            dest_dir = folders.check_destination(self.cfg, row["dest_dir"])
        except ValueError as e:
            raise Failed("folder_" + str(e), row["dest_dir"], transient=False) from e
        sink = _Sink(self._part(row))
        try:
            offset = await loop.run_in_executor(self._pool, sink.load)
        except OSError as e:
            raise Failed("disk", str(e), transient=False) from e
        live["done"] = offset
        try:
            if row["url"]:
                async with self._open_direct(row["url"], offset) as resp:
                    total = await self._begin(row, resp, sink, live)
                    if total is None or sink.size < total:
                        await self._receive(download_id, resp, sink, live, total, dest_dir)
            else:
                url = "%s/api/download/models/%d?fileId=%d" % (row["site"], row["version_id"], row["file_id"])
                async with self.library.client().open_download(url, start=offset) as resp:
                    total = await self._begin(row, resp, sink, live)
                    if total is None or sink.size < total:
                        await self._receive(download_id, resp, sink, live, total, dest_dir)
        except CivitaiError as e:
            raise Failed(e.code, e.detail) from e
        except aiohttp.ClientError as e:
            raise Failed("network", type(e).__name__) from e
        except asyncio.TimeoutError as e:
            raise Failed("network", "timeout") from e
        except OSError as e:
            raise Failed("disk", str(e), transient=False) from e
        total = live["total"] if live["total"] is not None else sink.size
        if sink.size != total:
            raise Failed("incomplete", "%s of %s bytes" % (sink.size, total))
        live["done"] = live["total"] = sink.size
        self._progress(download_id, live, force=True)
        self._set(download_id, state="verifying")
        digest = sink.hasher.hexdigest()
        expected = row["expected_sha256"]
        if expected and digest != expected:
            _remove(sink.path)
            raise Failed("hash_mismatch", "%s != %s" % (digest[:12], expected[:12]), transient=False)
        dest = os.path.join(dest_dir, row["dest_name"])
        final = dest if not os.path.exists(dest) else folders.unique_path(dest_dir, row["dest_name"],
                                                                          self.db.dl_active_paths())
        try:
            os.replace(sink.path, final)
            st = os.stat(final)
        except OSError as e:
            raise Failed("disk", str(e), transient=False) from e
        try:
            record = json.loads(row["version_json"])
        except ValueError:
            record = None
        if isinstance(record, dict) and record:
            record = await self._with_example_settings(row, record)
            self.library.adopt_download(final, digest, record, row["site"], st.st_size, st.st_mtime_ns)
        else:
            self.library.request_scan()
        return final, digest

    def _open_direct(self, url, start):
        """A direct address (a Hugging Face file), from byte `start`, through the route the settings name. Hugging Face
        redirects to its storage, which serves byte ranges; no key is sent anywhere."""
        outer = self

        class _Open:
            async def __aenter__(self):
                self.session = aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(
                    total=None, sock_connect=20, sock_read=60), headers={"User-Agent": "ModelDepot"})
                headers = {"Range": "bytes=%d-" % start} if start > 0 else {}
                try:
                    self.resp = await self.session.get(url, headers=headers, proxy=outer.cfg.proxy())
                except BaseException:
                    await self.session.close()
                    raise
                status = self.resp.status
                if status in (200, 206, 416):
                    return self.resp
                self.resp.release()
                await self.session.close()
                if status in (401, 403):
                    raise Failed("hf_login", str(status), transient=False)
                if status == 404:
                    raise Failed("not_found", url, transient=False)
                if status == 429:
                    raise Failed("rate_limited", "429")
                raise Failed("server" if status >= 500 else "bad_request", str(status), transient=status >= 500)

            async def __aexit__(self, *exc):
                self.resp.release()
                await self.session.close()
                return False

        return _Open()

    async def _with_example_settings(self, row, record):
        """The version as civitai's version API gives it, which keeps the settings each example image was made with
        (the model page the download was queued from leaves them out; checked 2026-10-02). The card's counted values
        come from them. One request per finished download; when it fails, the model page's copy is kept."""
        try:
            _site, version = await self.library.client().get_version(row["version_id"])
        except CivitaiError as e:
            log.info("version %s could not be read again (%s); its example settings are missing", row["version_id"], e.code)
            return record
        if not isinstance(version, dict) or version.get("id") != row["version_id"] \
                or version_info.version_fields(version) is None:
            return record
        merged = dict(version)
        model = dict(version.get("model") or {}) if isinstance(version.get("model"), dict) else {}
        model.setdefault("description", (record.get("model") or {}).get("description"))
        merged["model"] = model
        return merged

    async def _begin(self, row, resp, sink, live):
        """Make sense of the answer to a (range) request: returns the file's total size, None when the server does
        not say. Starts the .part file over when the server sends the whole file instead of the rest."""
        loop = asyncio.get_running_loop()
        if resp.status == 416:
            _first, total = _content_range(resp.headers.get("Content-Range"))
            if total is not None and total == sink.size:
                live["total"] = total
                return total
            await loop.run_in_executor(self._pool, sink.restart)
            raise Failed("range_mismatch", "416 at %d of %s" % (live["done"], total))
        if resp.status == 206:
            first, total = _content_range(resp.headers.get("Content-Range"))
            if first != sink.size:
                await loop.run_in_executor(self._pool, sink.restart)
                raise Failed("range_mismatch", "asked from %d, got %s" % (live["done"], first))
        else:
            length = resp.headers.get("Content-Length")
            total = int(length) if length and length.isdigit() else None
            if sink.size:
                # The whole file came back instead of the rest: start the .part file over.
                await loop.run_in_executor(self._pool, sink.restart)
                live["done"] = 0
        live["total"] = total
        self.db.dl_update(row["id"], bytes_total=total if total is not None else row["expected_bytes"])
        return total

    async def _receive(self, download_id, resp, sink, live, total, dest_dir):
        loop = asyncio.get_running_loop()
        need = total - sink.size if total is not None else None
        try:
            free = shutil.disk_usage(dest_dir).free
        except OSError:
            free = None
        if free is not None and need is not None and free < need + SPACE_MARGIN:
            raise Failed("disk_full", "%d %d" % (need, free), transient=False)
        block = bytearray()
        broken = None
        try:
            async for chunk in resp.content.iter_chunked(READ_CHUNK):
                block += chunk
                live["done"] = sink.size + len(block)
                if total is not None and live["done"] > total:
                    raise Failed("incomplete", "more bytes than announced")
                if len(block) >= WRITE_BLOCK:
                    await loop.run_in_executor(self._pool, sink.append, bytes(block))
                    block = bytearray()
                self._progress(download_id, live)
                if live["stop"]:
                    break
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            broken = e          # keep what arrived, then report the break
        except asyncio.CancelledError:
            # The program is stopping: keep what arrived, so that the next start continues from there.
            if block:
                await loop.run_in_executor(self._pool, sink.append, bytes(block))
                live["done"] = sink.size
            raise
        if block:
            await loop.run_in_executor(self._pool, sink.append, bytes(block))
        live["done"] = sink.size
        if broken is not None:
            raise broken
        if live["stop"]:
            raise _Stopped(live["stop"])
