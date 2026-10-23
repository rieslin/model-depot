"""The library: keeps the index in step with the model folders, and answers what is known about each file.

Six pieces of background work, each of which can be interrupted at any time and picks up where it left off:

  scan      walk the folders, read headers and sidecar files                        (seconds)
  hash      SHA-256 of every file that has none yet, small and useful files first   (minutes)
  civitai   ask civitai which versions the new fingerprints are, 100 at a time      (a few requests)
  covers    a thumbnail per model: the local preview if there is one, else civitai's cover
  cards     a card per model (cards.py), drafted from what is already here           (no requests)
  prewarm   only with the setting on: model pages and first example images ahead of time (a request per item)

A model's page on civitai (the author's notes for the whole model, its tags, its other versions) is read when the model
is downloaded, or when it is opened in the library and has not been read before. For the whole library at once only
when the user has turned warming up on (off by default), and then at the background work's pace. Checking for new
versions (updates.py) reads every page again, only when the user asks for it (the user's word, 2026-10-14).

A civitai model's name is settled once, when it first comes into the library (names.py): by the safe rules at once,
then by the local model when there is one. Later renames on civitai are not followed.

Everything that touches the index runs on the event loop; only file reading and hashing run in threads.
"""
import asyncio
import concurrent.futures
import json
import logging
import os
import time
import uuid

from . import cards, families, folders, hashing, image_meta, kinds, llm_install, reader as reader_mod, scan, sidecar
from . import names as names_mod, thumbs, training, trash, updates, version_info
from .civitai import BATCH_SIZE, CivitaiClient, CivitaiError
from .store import Store, is_sha256, write_atomic

log = logging.getLogger("depot.library")

_CIVITAI_BACKOFF = (30.0, 120.0, 600.0)     # waits after a failed round before trying again
NO_CHECK_KINDS = ("upscaler", "text_encoder", "other")     # unknown to civitai: "no check needed", not "unidentified"


def _loads(text, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def resource_keys(params):
    """The keys an example's generation data names models by: "h:<short hash>" (WebUI's resources, "Model hash" and
    "hashes") and "v:<version id>" (civitai's generator)."""
    keys = set()
    if not isinstance(params, dict):
        return keys
    hashes = [r.get("hash") for r in params.get("resources") or [] if isinstance(r, dict)]
    hashes.append(params.get("Model hash"))
    if isinstance(params.get("hashes"), dict):
        hashes.extend(params["hashes"].values())
    keys.update("h:" + str(h).strip().lower() for h in hashes if isinstance(h, str) and h.strip())
    for r in params.get("civitaiResources") or []:
        vid = r.get("modelVersionId") if isinstance(r, dict) else None
        if isinstance(vid, int) or (isinstance(vid, str) and vid.isdigit()):
            keys.add("v:%s" % int(vid))
    return keys


class Library:
    def __init__(self, config, index, client_factory=None, civitai_backoff=_CIVITAI_BACKOFF, reader_options=None):
        self.cfg = config
        self.db = index
        self.store = Store(config.library_dir())
        self.control = hashing.Control()
        self._client_factory = client_factory or self._default_client
        self._client = None
        self._civitai_backoff = tuple(civitai_backoff)
        self._stopping = False
        self._tasks = []
        self._scan_task = None
        self._scan_again = False
        self._pool = None
        self._scan_pool = None          # the scan's own thread, so that stopping can wait for it to let go of files
        self._wake = {}
        self._generation = 0            # goes up when the index is rebuilt: queued work from before is dropped
        self._cover_queue = []          # fingerprints whose cover can be made now
        self._cover_queued = set()
        self._summary = None            # (revision, summary)
        self._folder_stats = None       # (revision, stats)
        self._sample_locks = {}         # one download at a time per example image
        self._page_locks = {}           # one reading of a model page at a time per model
        # Files a download has just written, whose fingerprint is already known: {normcased path: (bytes, mtime, sha)}.
        # The next scan takes the fingerprint from here instead of reading the file again.
        self._hash_hints = {}
        self._short_hashes = {}         # sha -> (record mtime, the short hashes civitai lists for the file)
        self.listeners = []             # called with the new revision after the library changed
        self.move_to_trash = trash.to_recycle_bin     # the tests put a stand-in here: nothing of theirs reaches the bin

        self.scan_state = {"running": False, "started_at": None, "finished_at": None, "folders": [], "error": None,
                           "last": None}
        self.hash_state = {"running": False, "active": {}, "session_bytes": 0, "session_files": 0, "samples": []}
        self.civitai_state = {"running": False, "last_at": None, "last_error": None, "waiting_until": None}
        # can civitai be reached by the route in the settings: "unknown" until the first probe, then "ok" or "down"
        self.reach_state = {"state": "unknown", "checked_at": None}
        self._route_changed = False     # the settings changed since the last probe
        self.cover_state = {"running": False, "made": 0, "failed": 0}
        self.card_state = {"running": False, "drafted": 0}
        self.prewarm_state = {"running": False, "pages": 0, "samples": 0, "failed": 0, "last_error": None}
        # filling in the details at the user's request (fill_needs, start_fill): the last run, or the one under way
        self.fill_state = {"running": False, "started_at": None, "finished_at": None, "total": 0, "done": 0, "pages": 0,
                           "samples": 0, "notes": 0, "failed": 0, "error": None, "stopped": False}
        self._fill_task = None
        self._fill_stop = False
        self._fill_queued = set()       # the readings filling in asked for, taken back out if it is stopped
        self._fill_only = None          # the models a run is limited to (the library's selection), or None for all
        self._sample_failed = set()     # (sha, index) of example images that could not be had in this run of the program
        # The authors' notes read by a local language model (reader.py), one model at a time. The tests hand in
        # stand-ins for what the graphics card says.
        options = dict(reader_options or {})
        install_urls = options.pop("install_urls", {})
        self.reader = reader_mod.ReaderService(config, config.paths.logs, self._reader_inputs, self._reader_result,
                                               lambda: None, on_failed=self._reader_failed, name_for=self._name_input,
                                               on_name=self._name_result, **options)
        self.names = {}                 # model id -> the name it goes by here (store.load_names)
        # checking every model's page for new versions, at the user's request: the last run, or the one under way
        self.update_state = {"running": False, "started_at": None, "finished_at": None, "total": 0, "done": 0,
                             "failed": 0, "error": None, "stopped": False}
        self._update_task = None
        self._update_stop = False
        self._updates = None            # (revision, {sha: what is newer}) for the lists
        self._page_cache = {}           # model id -> (file time, page, its versions)
        # Downloading llama.cpp and the recommended model for a computer that has none; when it is done the reader
        # uses them.
        self.installer = llm_install.Installer(config, self.reader.reconfigure, **install_urls)
        self._notes_wanted = set()      # downloaded models whose notes are to be read once their card is drafted

    # ---- life cycle ------------------------------------------------------------------------------------------------
    async def start(self):
        self._wake = {name: asyncio.Event() for name in ("hash", "civitai", "covers", "cards", "prewarm", "reach")}
        # Threads start only as they are needed; how many files are read at once is decided in _hash_loop.
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="depot-hash")
        self._scan_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="depot-scan")
        self.names = self.store.load_names()
        self._adopt_all_records()
        self._tasks = [asyncio.ensure_future(self._guard(self._hash_loop, "hash")),
                       asyncio.ensure_future(self._guard(self._civitai_loop, "civitai")),
                       asyncio.ensure_future(self._guard(self._cover_loop, "covers")),
                       asyncio.ensure_future(self._guard(self._card_loop, "cards")),
                       asyncio.ensure_future(self._guard(self._prewarm_loop, "prewarm")),
                       asyncio.ensure_future(self._guard(self._reach_loop, "reach"))]
        await self.reader.start()
        if self.cfg.configured:
            self._wake["reach"].set()
            self.request_scan()

    async def stop(self):
        self._stopping = True
        self.control.abort()
        tasks = [t for t in self._tasks + [self._scan_task, self._fill_task, self._update_task] if t is not None]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.installer.stop()         # a download in progress carries on from its .part file next time
        await self.reader.stop()            # and the local model it may have started
        if self._pool is not None:
            self._pool.shutdown(wait=True)
        if self._scan_pool is not None:
            # A scan stops at the next folder once _stopping is set; until then it may hold a file open.
            self._scan_pool.shutdown(wait=True)
        await self._close_client()

    async def _guard(self, loop_fn, name):
        """Keep a background loop alive: one bad file or one bad answer must not end the work for good."""
        while not self._stopping:
            try:
                await loop_fn()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("%s worker failed; it restarts in 30 s", name)
                await asyncio.sleep(30)
                self._wake[name].set()

    def _kick(self, *names):
        for name in names or ("hash", "civitai", "covers", "cards", "prewarm"):
            event = self._wake.get(name)
            if event is not None:
                event.set()

    def _changed(self):
        self._summary = None
        revision = self.db.revision()
        for listener in list(self.listeners):
            try:
                listener(revision)
            except Exception:  # noqa: BLE001
                log.exception("library listener failed")

    @property
    def busy(self):
        """Is background work going on right now. Hashing that is paused does not count."""
        scanning = self._scan_task is not None and not self._scan_task.done()
        hashing_now = self.hash_state["running"] and not self.control.paused
        return bool(scanning or hashing_now or self.civitai_state["running"] or self.cover_state["running"]
                    or self._cover_queue or self.card_state["running"] or self.fill_state["running"]
                    or self.update_state["running"])

    # ---- settings changed ------------------------------------------------------------------------------------------
    async def reconfigure(self):
        """Called after the settings were saved: new folders, new key, new network route, another local model."""
        await self._close_client()
        self.reach_state.update(state="unknown", checked_at=None)
        self._route_changed = True
        if "reach" in self._wake:
            self._wake["reach"].set()       # a new route: is civitai reachable by it?
        self.reader.reconfigure()
        self.store = Store(self.cfg.library_dir())
        self.names = self.store.load_names()
        self._adopt_all_records()
        self.request_scan()

    # ---- civitai client --------------------------------------------------------------------------------------------
    def _default_client(self, cfg):
        token, _problem = cfg.civitai_token()
        civ = cfg.data["civitai"]
        return CivitaiClient(civ["sites"], token=token, proxy=cfg.proxy, request_interval=civ["request_interval"],
                             image_interval=civ["image_interval"])

    # ---- can civitai be reached ------------------------------------------------------------------------------------
    def _civitai_down(self):
        """A civitai host stopped answering: probe, so that the state is known (and kept) while it stays down."""
        if self.reach_state["state"] != "down" and "reach" in self._wake:
            self._wake["reach"].set()

    def _civitai_up(self):
        """civitai answered a request the user made by hand while it counted as unreachable."""
        self.reach_state.update(state="ok", checked_at=time.time())
        log.info("civitai can be reached again")
        self._reachable_again()
        self._changed()

    async def _reach_loop(self):
        """At start, after the settings change, and when a request fails: ask civitai for one small answer. When it
        cannot be reached, the client is set offline: the program's own requests are answered "unreachable" at once and
        nothing more is tried until the user tries by hand, the settings change or the program starts again (the
        user's rule, 2026-10-23). When it answers again, what failed in the meantime is fetched."""
        await self._wake["reach"].wait()
        self._wake["reach"].clear()
        if self._stopping or not self.cfg.configured:
            return
        client = self._get_client()
        ok = await client.probe()
        self._wake["reach"].clear()             # the probe's own failures do not ask for another
        was = self.reach_state["state"]
        client.offline = not ok
        self.reach_state.update(state="ok" if ok else "down", checked_at=time.time())
        if was != self.reach_state["state"]:
            log.info("civitai %s", "can be reached" if ok else
                     "cannot be reached; nothing more is asked until a manual try, a settings change or the next start")
            self._changed()
        if ok and (was == "down" or self._route_changed):
            self._reachable_again()     # back, or reached by a new route: what failed before is asked again
        self._route_changed = False

    def _reachable_again(self):
        """civitai answers again: the fingerprints, covers and example images that failed meanwhile are asked again."""
        self.db.retry_covers()
        for model in self.db.cover_candidates():
            self._queue_cover(model["sha256"])
        self._sample_failed.clear()
        self.civitai_state["last_error"] = None
        self._kick("civitai", "covers", "prewarm")

    def _tried_by_hand(self):
        """The user started work that asks civitai (check again, fill in, check for updates): it is tried, whatever was
        known about civitai; when it still cannot be reached, the first failure finds that out again."""
        if self._client is not None:
            self._client.clear_down()

    def route_works(self):
        """A connection test by the saved route reached civitai."""
        if self._client is not None:
            self._client.clear_down()
        if self.reach_state["state"] == "down":
            self._wake["reach"].set()

    def reachable(self):
        """{"state": "ok" | "down" | "unknown", "checked_at"} for the route in the settings."""
        return dict(self.reach_state)

    def _get_client(self):
        if self._client is None:
            self._client = self._client_factory(self.cfg)
            self._client._on_down = self._civitai_down      # whoever made it, the library hears when civitai stops
            self._client._on_up = self._civitai_up          # answering, and when it answers again
        return self._client

    def client(self):
        """The one civitai client: downloads and previews share its spacing of requests with the background work."""
        return self._get_client()

    async def _close_client(self):
        if self._client is not None:
            client, self._client = self._client, None
            await client.close()

    # ---- scan ------------------------------------------------------------------------------------------------------
    def request_scan(self):
        """Start a scan, or have the running one followed by another."""
        if self._scan_task is not None and not self._scan_task.done():
            self._scan_again = True
            return
        self._scan_task = asyncio.ensure_future(self._scan_runner())

    async def _scan_runner(self):
        while not self._stopping:
            self._scan_again = False
            await self._scan_once()
            if not self._scan_again:
                break

    async def _scan_once(self):
        state = self.scan_state
        state.update(running=True, started_at=time.time(), error=None)
        try:
            known = self.db.known_files()
            folders = self.cfg.folders()
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(self._scan_pool, scan.scan, folders, known, lambda: self._stopping)
            counts = self.db.apply_scan(result["files"], complete=not result["stopped"])
            state["folders"] = result["folders"]
            state["last"] = counts
            self._apply_hash_hints()
            self._import_sidecars()
            for model in self.db.cover_candidates():
                self._queue_cover(model["sha256"])
            log.info("scan: %d files in %d folders (%s)", len(result["files"]), len(result["folders"]), counts)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("scan failed")
            state["error"] = str(e)
        finally:
            state.update(running=False, finished_at=time.time())
        self._changed()
        self._kick()

    # ---- records and sidecars --------------------------------------------------------------------------------------
    def _adopt_all_records(self):
        """Fill the index from the records folder for fingerprints the index does not know yet: after the index was
        rebuilt, or when the records folder is shared with another computer."""
        adopted = 0
        for sha in self.store.all_sha256():
            row = self.db.model(sha)
            if row is None or (row["source"] is None and row["civitai_state"] == "pending"):
                adopted += self._adopt_record(sha)
        if adopted:
            log.info("adopted %d records into the index", adopted)

    def _adopt_record(self, sha):
        """Put what a record says into the index. Returns 1 when the record identified the model."""
        if not self.store.exists(sha):
            return 0
        record = self.store.load(sha)
        self.db.ensure_model(sha)
        done = 0
        civ = record.get("civitai") if isinstance(record.get("civitai"), dict) else None
        info = (record.get("sidecar") or {}).get("civitai_info") if isinstance(record.get("sidecar"), dict) else None
        fields = version_info.version_fields(civ.get("version")) if civ else None
        if fields:
            self.db.set_identity(sha, "civitai", fields, verified=True)
            self.db.set_civitai_state([sha], "found", civ.get("site"), civ.get("fetched_at"))
            done = 1
        else:
            if civ and civ.get("bundled_count"):
                self.db.set_civitai_state([sha], "bundled", civ.get("site"), civ.get("fetched_at"))
            fields = version_info.version_fields(info)
            if fields:
                self.db.set_identity(sha, "sidecar", fields, verified=sha in version_info.version_hashes(info))
                done = 1
        if civ:
            self.db.set_bundled(sha, civ.get("bundled_count") or 0)
        cover = record.get("cover") if isinstance(record.get("cover"), dict) else None
        if cover and self.store.has_cover(sha):
            self.db.set_cover(sha, "ready", cover.get("level"), cover.get("source"))
        card = record.get("card") if isinstance(record.get("card"), dict) else None
        if card and card.get("state"):
            # what the card holds, for the lists; drafted again if the drafter changed since
            self.db.set_card(sha, card["state"], card.get("drafter") or 0)
        user = record.get("user") if isinstance(record.get("user"), dict) else {}
        if user.get("title") or user.get("base_model"):
            self.db.set_user_info(sha, user.get("title"), user.get("base_model"))
        return done

    def _import_sidecars(self):
        for row in self.db.files_to_import():
            self._import_sidecar(row)

    def _import_sidecar(self, row):
        """Copy the old WebUI files' content into the model's record (the originals are left untouched), and use it
        to name the model until civitai has answered, or if civitai no longer knows it."""
        sha = row["sha256"]
        side = _loads(row["sidecar"], {})
        folder = os.path.dirname(row["path"])
        record = self.store.load(sha)
        entry = record["sidecar"] if isinstance(record.get("sidecar"), dict) else {}
        changed = False
        info = None
        if side.get("info") and not side["info"].get("empty"):
            info = sidecar.read_info(os.path.join(folder, side["info"]["file"]))
            if version_info.version_fields(info) and entry.get("civitai_info") != info:
                entry["civitai_info"] = info
                changed = True
        if side.get("card"):
            card = sidecar.read_card(os.path.join(folder, side["card"]["file"]))
            if card and entry.get("webui_card") != card:
                entry["webui_card"] = card
                changed = True
        if changed:
            entry["imported_at"] = time.time()
            record["sidecar"] = entry
            self.store.save(sha, record)
            self.db.redraft_card(sha)
            self._kick("cards")
        model = self.db.model(sha)
        stored = entry.get("civitai_info")
        fields = version_info.version_fields(stored)
        if fields and (model is None or model["source"] != "civitai"):
            self.db.set_identity(sha, "sidecar", fields, verified=sha in version_info.version_hashes(stored))
        self.db.mark_sidecar_imported(row["id"], row["sidecar_sig"])

    # ---- fingerprints ----------------------------------------------------------------------------------------------
    def pause_hashing(self):
        self.control.pause()

    def resume_hashing(self):
        self.control.resume()
        self._kick("hash")

    async def _hash_loop(self):
        await self._wake["hash"].wait()
        self._wake["hash"].clear()
        rows = self.db.pending_hash()
        if not rows:
            return
        state = self.hash_state
        state.update(running=True, session_bytes=0, session_files=0, samples=[])
        queue = list(reversed(rows))
        try:
            workers = self.cfg.data["hashing"]["workers"]
            await asyncio.gather(*[self._hash_worker(queue, self._generation) for _ in range(workers)])
        finally:
            state.update(running=False, active={})
            self._changed()
            self._kick("civitai", "covers")

    async def _hash_worker(self, queue, generation):
        loop = asyncio.get_running_loop()
        state = self.hash_state
        while queue and not self._stopping and generation == self._generation:
            row = queue.pop()
            progress = {"name": os.path.basename(row["path"]), "bytes": row["bytes"], "done": 0}
            state["active"][row["id"]] = progress

            def on_bytes(n, progress=progress):
                progress["done"] += n

            try:
                digest = await loop.run_in_executor(self._pool, hashing.sha256_file, row["path"], row["bytes"],
                                                    row["mtime_ns"], self.control, on_bytes)
            except hashing.Aborted:
                return
            except hashing.Changed:
                self._scan_later()            # replaced or still being written: look again
            except OSError as e:
                self.db.set_hash_error(row["id"], "%s: %s" % (type(e).__name__, e.strerror or e))
                log.warning("cannot read %s: %s", row["path"], e)
            else:
                if generation == self._generation:
                    self.db.set_hash(row["id"], digest)
                    self._after_hash(row["id"], digest)
                    state["session_files"] += 1
            finally:
                state["session_bytes"] += progress["done"]
                state["active"].pop(row["id"], None)
            self._changed()
            self._kick("civitai", "covers")

    def _after_hash(self, file_id, sha):
        row = self.db.file(file_id)
        if row is None:
            return
        model = self.db.model(sha)
        if model is not None and model["source"] is None and model["civitai_state"] == "pending":
            self._adopt_record(sha)
        if row["sidecar"] and row["sidecar_sig"] != row["sidecar_imported"]:
            self._import_sidecar(row)
        self._queue_cover(sha)

    def _scan_later(self, delay=20.0):
        if not self._stopping:
            asyncio.get_running_loop().call_later(delay, lambda: None if self._stopping else self.request_scan())

    def hash_progress(self):
        """Numbers for the progress line, with a speed measured over the last few seconds."""
        state = self.hash_state
        totals = self.db.hash_totals()
        active_done = sum(p["done"] for p in state["active"].values())
        moving = state["session_bytes"] + active_done
        now = time.monotonic()
        samples = state["samples"]
        if state["running"] and not self.control.paused:
            samples.append((now, moving))
        while len(samples) > 2 and now - samples[0][0] > 10:
            samples.pop(0)
        speed = 0.0
        if state["running"] and len(samples) >= 2 and samples[-1][0] > samples[0][0]:
            speed = max(0.0, (samples[-1][1] - samples[0][1]) / (samples[-1][0] - samples[0][0]))
        left = totals["bytes"] - totals["done_bytes"] - active_done
        return {
            "running": state["running"], "paused": self.control.paused,
            "files": totals["files"], "bytes": totals["bytes"],
            "done_files": totals["done_files"], "done_bytes": totals["done_bytes"] + active_done,
            "errors": totals["errors"],
            "current": [dict(p) for p in state["active"].values()],
            "speed": speed, "eta": (left / speed) if speed > 0 and left > 0 else None,
        }

    # ---- civitai ---------------------------------------------------------------------------------------------------
    def recheck(self, include_found=False):
        self._tried_by_hand()
        n = self.db.recheck_civitai(include_found)
        self.db.retry_covers()
        self.civitai_state["last_error"] = None
        for model in self.db.cover_candidates():
            self._queue_cover(model["sha256"])
        self._changed()
        self._kick("civitai")
        return n

    async def _civitai_loop(self):
        await self._wake["civitai"].wait()
        self._wake["civitai"].clear()
        state = self.civitai_state
        failures = 0
        while not self._stopping:
            pending = self.db.count_unchecked()
            if not pending:
                break
            if pending < BATCH_SIZE and self.hash_state["running"]:
                break       # more fingerprints are on their way: wait for a full batch, or for hashing to end
            state["running"] = True
            try:
                complete = await self._check_batch(self.db.unchecked_hashes(BATCH_SIZE))
            finally:
                state["running"] = False
            if complete:
                failures = 0
                continue
            if failures >= len(self._civitai_backoff):
                log.warning("civitai could not be asked; giving up until the next scan or a manual retry")
                break
            delay = self._civitai_backoff[failures]
            failures += 1
            state["waiting_until"] = time.time() + delay
            await asyncio.sleep(delay)
            state["waiting_until"] = None

    async def _check_batch(self, batch):
        """Ask civitai about one batch of fingerprints. True when every site answered."""
        state = self.civitai_state
        now = time.time()
        try:
            found, complete = await self._get_client().versions_by_hash(batch)
        except CivitaiError as e:
            state.update(last_at=now, last_error=e.code)
            log.warning("civitai lookup failed: %s", e.code)
            return False
        kinds_of = self.db.kinds_for(found)
        named = 0
        for sha, (site, versions) in found.items():
            version, bundled = version_info.pick_version(versions, sha, kinds_of.get(sha, ()))
            record = self.store.load(sha)
            before = self._civitai_version(record)
            if version is None and before is not None and before.get("id") not in {v.get("id") for v in versions}:
                # The version this file was known as is gone from civitai, while other models still carry the file
                # along. What was saved about it stays: a model taken down is still that model.
                record["civitai"]["bundled_count"] = len(bundled)
                self.store.save(sha, record)
                self.db.set_civitai_state([sha], "bundled", site, now)
                self.db.set_bundled(sha, len(bundled))
                continue
            cover = record.get("cover") if isinstance(record.get("cover"), dict) else None
            if cover and cover.get("source") == "civitai" and (before or {}).get("id") != (version or {}).get("id"):
                # The cover was taken from a version this file is no longer known as: fetch the right one.
                record.pop("cover", None)
                cover = None
                self.db.set_cover(sha, "pending")
            record["civitai"] = {"site": site, "fetched_at": now, "version": version, "bundled_count": len(bundled)}
            if version is None:
                # civitai knows the file only as an extra that other models carry along: that is not a name for it.
                record["civitai"]["bundled_in"] = version_info.bundle_summary(bundled)
                self.store.save(sha, record)
                self.db.clear_identity(sha, "civitai")
                self.db.redraft_card(sha)
                self.db.set_civitai_state([sha], "bundled", site, now)
                self.db.set_bundled(sha, len(bundled))
                continue
            level = None
            if cover and cover.get("source") == "sidecar":
                # The local preview was rated from old data; civitai's current rating of that same image is better.
                info = self._sidecar_info(record)
                size = (cover.get("width"), cover.get("height")) if cover.get("width") else None
                level = sidecar.preview_level(info, size, version) if info else None
                if level is not None:
                    cover["level"] = level
            self.store.save(sha, record)
            self.db.set_identity(sha, "civitai", version_info.version_fields(version), verified=True)
            if level is not None:
                self.db.set_cover(sha, "ready", level, "sidecar")
            self.db.set_civitai_state([sha], "found", site, now)
            self.db.set_bundled(sha, len(bundled))
            if before != version:
                self.db.redraft_card(sha)       # drafted from what civitai says about the version
            named += 1
        if complete:
            # Not on civitai (any more). A file identified earlier keeps its saved identification.
            self.db.set_civitai_state([h for h in batch if h not in found], "not_found", None, now)
            # Whatever happened above, a fingerprint that was asked about and answered is never asked again in a
            # loop: anything still marked as waiting is closed here.
            stuck = [h for h in batch if (self.db.model(h) or {"civitai_state": ""})["civitai_state"] == "pending"]
            if stuck:
                log.warning("%d fingerprints were left without an outcome; marked as not found", len(stuck))
                self.db.set_civitai_state(stuck, "not_found", None, now)
        state.update(last_at=now, last_error=None if complete else "incomplete")
        log.info("civitai: %d of %d fingerprints named, %d known only as extras%s", named, len(batch), len(found) - named,
                 "" if complete else " (incomplete)")
        for sha in batch:
            if complete or sha in found:
                self._queue_cover(sha)
        self._changed()
        self._kick("cards", "prewarm")
        return complete

    # ---- covers ----------------------------------------------------------------------------------------------------
    def _queue_cover(self, sha):
        """Have the cover worker look at this fingerprint: its file was just hashed, or civitai just answered."""
        if sha not in self._cover_queued:
            self._cover_queued.add(sha)
            self._cover_queue.append(sha)
        self._kick("covers")

    async def _cover_loop(self):
        await self._wake["covers"].wait()
        self._wake["covers"].clear()
        state = self.cover_state
        if not self._cover_queue:
            return
        state["running"] = True
        made = False
        try:
            while self._cover_queue and not self._stopping:
                sha = self._cover_queue.pop(0)
                self._cover_queued.discard(sha)
                model = self.db.model(sha)
                if model is None or model["cover_state"] != "pending":
                    continue
                try:
                    await self._make_cover(model)
                    made = True
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    log.exception("cover for %s failed", sha[:12])
                    self.db.set_cover(sha, "failed")
                    state["failed"] += 1
        finally:
            state["running"] = False
            if made:
                self._changed()

    def _civitai_version(self, record):
        civ = record.get("civitai") if isinstance(record.get("civitai"), dict) else {}
        return civ.get("version") if isinstance(civ.get("version"), dict) else None

    def _sidecar_info(self, record):
        return record["sidecar"].get("civitai_info") if isinstance(record.get("sidecar"), dict) else None

    async def _make_cover(self, model):
        sha = model["sha256"]
        loop = asyncio.get_running_loop()

        # 1. A preview that already sits next to the file: no download needed.
        for f in self.db.files_for(sha):
            preview = (_loads(f["sidecar"], {}) or {}).get("preview")
            if not preview:
                continue
            path = os.path.join(os.path.dirname(f["path"]), preview["file"])
            try:
                jpeg, size = await loop.run_in_executor(None, thumbs.make_cover, path)
            except Exception as e:  # noqa: BLE001 - a broken image is not worth more than a note
                log.info("preview %s unusable: %s", path, e)
                continue
            self._save_cover(sha, jpeg, {"source": "sidecar", "file": preview["file"], "width": size[0], "height": size[1]})
            return

        # 2. civitai's cover for the version.
        if model["civitai_state"] == "pending":
            return          # not asked yet: decide when the answer is in
        record = self.store.load(sha)
        version = self._civitai_version(record)
        image = version_info.pick_cover_image(version.get("images")) if version else None
        if image is None:
            # 3. the first picture the user added to the examples
            for added in self._user_images(record):
                path = self.image_file(sha, added["id"])
                if path is None:
                    continue
                jpeg, size = await loop.run_in_executor(None, thumbs.make_cover, path)
                self._save_cover(sha, jpeg, {"source": "added", "image": added["id"], "width": size[0], "height": size[1]})
                return
            self.db.set_cover(sha, "none")
            return
        try:
            data = await self._get_client().fetch_image(version_info.sized_image_url(image["url"], thumbs.MAX_WIDTH))
            jpeg, _size = await loop.run_in_executor(None, thumbs.make_cover, data)
        except CivitaiError as e:
            log.info("cover download for %s failed: %s", sha[:12], e.code)
            self.db.set_cover(sha, "none" if e.code == "not_found" else "failed")
            self.cover_state["failed"] += 1
            return
        self._save_cover(sha, jpeg, {"source": "civitai", "url": image["url"], "width": image.get("width"),
                                     "height": image.get("height"), "level": version_info.image_level(image)})

    def _save_cover(self, sha, jpeg, cover):
        """Store the thumbnail and note it in the record. The record is read here, after all waiting is over, so
        that what the civitai worker wrote in the meantime is not overwritten with an older copy."""
        record = self.store.load(sha)
        if cover["source"] == "sidecar":
            info = self._sidecar_info(record)
            size = (cover["width"], cover["height"])
            cover["level"] = sidecar.preview_level(info, size, self._civitai_version(record)) if info else None
        self.store.save_cover(sha, jpeg)
        record["cover"] = cover
        self.store.save(sha, record)
        self.db.set_cover(sha, "ready", cover.get("level"), cover.get("source"))
        self.cover_state["made"] += 1

    # ---- the author's example images -------------------------------------------------------------------------------
    def _sample_images(self, sha):
        """The version's example images in civitai's order: the same list the detail API numbers as `samples`."""
        record = self.store.load(sha)
        version = self._civitai_version(record) or self._sidecar_info(record)
        return [image for image in (version or {}).get("images") or [] if isinstance(image, dict) and image.get("url")]

    def sample_cache_path(self, sha, index):
        return os.path.join(self.cfg.paths.cache, "samples", sha, "%d.jpg" % index)

    async def sample_thumbnail(self, sha, index, interactive=True, force=False, cached_only=False):
        """The path of a small JPEG of example image number `index`, fetched from civitai the first time it is asked
        for and kept in the cache folder: at the pace for someone waiting, or (not `interactive`, warming up) at the
        covers' pace. LookupError when there is no such still image; CivitaiError, or the image library's own error,
        when it cannot be had."""
        if not is_sha256(sha) or index < 0:
            raise LookupError(sha)
        path = self.sample_cache_path(sha, index)
        if os.path.isfile(path):
            return path
        if cached_only:
            raise LookupError(sha)
        images = self._sample_images(sha)
        if index >= len(images) or version_info.is_video(images[index]):
            raise LookupError(sha)
        lock = self._sample_locks.setdefault(path, asyncio.Lock())
        try:
            async with lock:
                if not os.path.isfile(path):
                    url = version_info.sized_image_url(images[index]["url"], thumbs.SAMPLE_WIDTH)
                    data = await self._get_client().fetch_image(url, interactive=interactive, force=force)
                    jpeg, _size = await asyncio.get_running_loop().run_in_executor(None, thumbs.make_sample, data)
                    write_atomic(path, jpeg)
        finally:
            if not lock.locked():
                self._sample_locks.pop(path, None)
        return path

    def file_paths(self, sha):
        """Where the copies of a model are on disk."""
        return [row["path"] for row in self.db.files_for(sha)] if is_sha256(sha) else []

    # ---- the models an example names, matched to the files here ----------------------------------------------------
    # WebUI writes each model's short hash (a Checkpoint's AutoV2, the first ten characters of its fingerprint; a LoRA's
    # AutoV3, a hash of its weights alone), civitai's generator the version's number. Both name a file exactly; a name
    # alone does not (files are renamed, and many share one), so names are not matched.
    def _file_short_hashes(self, sha):
        """Every hash civitai lists for this file (AutoV1, AutoV2, AutoV3, CRC32, BLAKE3), lower case, and its own
        AutoV2; kept until the record changes."""
        try:
            stamp = os.stat(self.store.record_path(sha)).st_mtime_ns
        except OSError:
            stamp = None
        cached = self._short_hashes.get(sha)
        if cached and cached[0] == stamp:
            return cached[1]
        out = {sha[:10]}
        if stamp is not None:
            record = self.store.load(sha)
            civ = record.get("civitai") if isinstance(record.get("civitai"), dict) else {}
            sidecar = record.get("sidecar") if isinstance(record.get("sidecar"), dict) else {}
            for version in (civ.get("version"), sidecar.get("civitai_info")):
                if not isinstance(version, dict):
                    continue
                for f in version.get("files") or []:
                    hashes = f.get("hashes") if isinstance(f, dict) and isinstance(f.get("hashes"), dict) else {}
                    hashes = {str(k).lower(): str(v).lower() for k, v in hashes.items() if v}
                    if hashes.get("sha256") == sha:
                        out.update(v for k, v in hashes.items() if k != "sha256")
        self._short_hashes[sha] = (stamp, out)
        return out

    def resource_matches(self, keys):
        """{key: {"sha256", "title", "version_name"}} for the keys ("h:<short hash>", "v:<civitai version id>") that
        name a file here."""
        hashes = {k[2:] for k in keys if k.startswith("h:")}
        versions = {k[2:] for k in keys if k.startswith("v:")}
        out = {}
        if not hashes and not versions:
            return out
        for row in self.db.joined():
            sha = row["sha256"]
            if not sha:
                continue
            found = ["h:" + h for h in hashes & self._file_short_hashes(sha)]
            if versions and row["version_id"] is not None and str(row["version_id"]) in versions:
                found.append("v:%s" % row["version_id"])
            if not found:
                continue
            obj = self.model_object(row)
            for key in found:
                out.setdefault(key, {"sha256": sha, "title": obj["title"], "version_name": obj["version_name"]})
        return out

    # ---- what the user adds and changes: pictures, the cover, the name and the base model -------------------------
    @staticmethod
    def _user_images(record):
        """The pictures the user added to a model's examples, in the order added."""
        return [i for i in record.get("images") or [] if isinstance(i, dict) and isinstance(i.get("id"), str)]

    def _here(self, sha):
        return is_sha256(sha) and bool(self.db.files_for(sha))

    def image_file(self, sha, image_id):
        """The kept copy of a picture the user added to this model, or None."""
        if not is_sha256(sha) or not any(i["id"] == image_id for i in self._user_images(self.store.load(sha))):
            return None
        try:
            path = self.store.image_path(image_id)
        except ValueError:
            return None
        return path if os.path.isfile(path) else None

    async def add_image(self, sha, data, name=None):
        """Add a picture to a model's examples: a copy is kept (image_meta.keep), with the settings the file records.
        The first picture of a model that has no cover becomes its cover. Returns the new entry. Raises LookupError when
        no file here has this fingerprint, image_meta.ImageError when the bytes are not a picture."""
        if not self._here(sha):
            raise LookupError(sha)
        jpeg, width, height, params = await asyncio.get_running_loop().run_in_executor(None, image_meta.keep, data)
        entry = {"id": uuid.uuid4().hex, "width": width, "height": height, "params": params,
                 "name": str(name or "").strip()[:200] or None, "added_at": time.time()}
        self.store.save_image(entry["id"], jpeg)
        record = self.store.load(sha)       # read after the waiting, so that nothing written meanwhile is lost
        record["images"] = self._user_images(record) + [entry]
        self.store.save(sha, record)
        model = self.db.model(sha)
        if model is not None and model["cover_state"] == "none":
            # the first picture of a model with no cover is its cover at once, so that the answer already says so
            cover, size = await asyncio.get_running_loop().run_in_executor(None, thumbs.make_cover,
                                                                           self.store.image_path(entry["id"]))
            self._save_cover(sha, cover, {"source": "added", "image": entry["id"], "width": size[0], "height": size[1]})
        elif model is not None and model["cover_state"] == "failed":
            self.db.set_cover(sha, "pending")       # civitai's could not be had: tried again, else this picture
            self._queue_cover(sha)
        self.db.bump_revision()
        self._changed()
        return entry

    async def delete_image(self, sha, image_id):
        """Take a picture the user added out of the model's examples. The kept copy goes to the recycle bin (the user's
        own file, wherever it is, was never touched); where the data folder has no recycle bin, it stays in the folder.
        When it was the cover, the cover is chosen again. Raises LookupError when the model has no such picture."""
        record = self.store.load(sha) if is_sha256(sha) else {}
        images = self._user_images(record)
        if not any(i["id"] == image_id for i in images):
            raise LookupError(image_id)
        record["images"] = [i for i in images if i["id"] != image_id]
        cover = record.get("cover") if isinstance(record.get("cover"), dict) else {}
        was_cover = cover.get("image") == image_id
        if was_cover:
            record.pop("cover", None)
        self.store.save(sha, record)
        if was_cover:
            self.db.set_cover(sha, "pending")
            self._queue_cover(sha)
        self.db.bump_revision()
        self._changed()
        path = self.store.image_path(image_id)
        if os.path.isfile(path):
            try:
                await asyncio.get_running_loop().run_in_executor(None, self.move_to_trash, [path])
            except trash.TrashError as e:
                log.info("the copy of picture %s stays in the data folder: %s", image_id, e)

    async def set_cover(self, sha, sample=None, image=None):
        """Make an example the model's cover: the author's example number `sample`, or the picture `image` the user
        added. With neither, the cover is chosen again as at first (the picture beside the file, civitai's, the first
        one added). Raises LookupError when there is no such model or example; CivitaiError, OSError or the image
        library's own error when the example cannot be had."""
        if not self._here(sha):
            raise LookupError(sha)
        loop = asyncio.get_running_loop()
        if sample is not None:
            images = self._sample_images(sha)
            if isinstance(sample, bool) or not isinstance(sample, int) or not 0 <= sample < len(images) \
                    or version_info.is_video(images[sample]):
                raise LookupError(sample)
            chosen = images[sample]
            path = await self.sample_thumbnail(sha, sample)
            jpeg, _size = await loop.run_in_executor(None, thumbs.make_cover, path)
            cover = {"source": "user", "sample": sample, "url": chosen["url"], "width": chosen.get("width"),
                     "height": chosen.get("height"), "level": version_info.image_level(chosen)}
        elif image is not None:
            path = self.image_file(sha, image)
            if path is None:
                raise LookupError(image)
            jpeg, size = await loop.run_in_executor(None, thumbs.make_cover, path)
            cover = {"source": "user", "image": image, "width": size[0], "height": size[1]}
        else:
            record = self.store.load(sha)
            record.pop("cover", None)
            self.store.save(sha, record)
            self.db.set_cover(sha, "pending")
            self._queue_cover(sha)
            self._changed()
            return
        self._save_cover(sha, jpeg, cover)
        self._changed()

    def _cover_from(self, record, samples):
        """Which example the cover is: {"sample": index} or {"image": id}, with "chosen" true when the user chose it;
        None when the cover is the picture beside the file, or there is none."""
        cover = record.get("cover") if isinstance(record.get("cover"), dict) else {}
        chosen = cover.get("source") == "user"
        if cover.get("image") and any(i["id"] == cover["image"] for i in self._user_images(record)):
            return {"image": cover["image"], "chosen": chosen}
        if cover.get("source") in ("user", "civitai") and cover.get("url"):
            for i, s in enumerate(samples):
                if s["url"] == cover["url"]:
                    return {"sample": i, "chosen": chosen}
        return None

    def set_info(self, sha, changes):
        """The name and the base model the user gives a model: {"title": …, "base_model": …}, either may be left out;
        None or "" takes the user's value back (civitai's, the training's or the file's shows again). Raises
        LookupError when no file here has this fingerprint, ValueError(field) for a value that cannot be taken."""
        if not self._here(sha):
            raise LookupError(sha)
        record = self.store.load(sha)
        user = dict(record.get("user") or {}) if isinstance(record.get("user"), dict) else {}
        for field in ("title", "base_model"):
            if field not in changes:
                continue
            value = changes[field]
            if value is not None and not isinstance(value, str):
                raise ValueError(field)
            text = " ".join((value or "").split())
            if len(text) > 200:
                raise ValueError(field)
            if text:
                user[field] = text
            else:
                user.pop(field, None)
        user["at"] = time.time()
        record["user"] = user
        self.store.save(sha, record)
        self.db.set_user_info(sha, user.get("title"), user.get("base_model"))
        self._changed()

    # ---- cards -----------------------------------------------------------------------------------------------------
    def _kind_of(self, sha):
        rows = self.db.files_for(sha) if is_sha256(sha) else []
        return rows[0]["kind"] if rows else None

    def _notes_of(self, kind, record):
        """(version, page model, what the local model is given about the notes): the same text both for asking and
        for telling whether a reading kept in the card still answers the notes as they are now."""
        version = self._civitai_version(record) or self._sidecar_info(record)
        fields = version_info.version_fields(version) if version else None
        page = self.store.load_page(fields["model_id"]) if fields else None
        model = (page or {}).get("model") if isinstance((page or {}).get("model"), dict) else None
        if model is not None:
            model_html = model.get("description")
        else:
            block = (version or {}).get("model")
            model_html = block.get("description") if isinstance(block, dict) else None
        others = [str(v.get("name") or "") for v in (model or {}).get("modelVersions") or []
                  if isinstance(v, dict) and v.get("id") != (fields or {}).get("version_id")]
        words = version_info.clean_words((version or {}).get("trainedWords"))
        notes = reader_mod.notes_input(kind, (fields or {}).get("version_name"), (version or {}).get("description"),
                                       model_html, others, words)
        notes["words"] = words
        return version, model, notes

    def _draft_card(self, sha, kind, record=None):
        """Draft a model's card anew from what is here (its record, its model page if that has been read, and what
        the local model read in the notes if that still answers them), keep it, and note it in the index. Nothing is
        awaited in between, so no other step writes the record meanwhile. An empty card is not written into a record
        that has none. Returns the card."""
        record = record if record is not None else self.store.load(sha)
        version, model, notes = self._notes_of(kind, record)
        webui = record["sidecar"].get("webui_card") if isinstance(record.get("sidecar"), dict) else None
        files = self.db.files_for(sha)
        names = [os.path.splitext(os.path.basename(r["path"]))[0] for r in files]
        old = record.get("card") if isinstance(record.get("card"), dict) else {}
        ai = old.get("ai") if isinstance(old.get("ai"), dict) and old["ai"].get("key") == notes["key"] else None
        # A model neither civitai nor the old WebUI's files name: what its header says about its training (the user's
        # own LoRAs, mostly) suggests the trigger words.
        header = _loads(files[0]["header"], {}) if files and version is None else None
        card = cards.drafted(record.get("card"), kind, sha, version, model, webui, names, ai=ai, training=header or None)
        card["state"] = cards.state_of(card, kind)
        if card["state"] != "empty" or isinstance(record.get("card"), dict):
            record["card"] = card
            self.store.save(sha, record)
        row = self.db.model(sha)
        if row is not None and (row["card_state"], row["card_drafted"]) != (card["state"], cards.DRAFTER):
            self.db.set_card(sha, card["state"], cards.DRAFTER)
        self.card_state["drafted"] += 1
        return card

    async def _card_loop(self):
        """Draft the cards that are not drafted yet (all of them once, after an upgrade), from what is already here.
        Asks civitai nothing."""
        await self._wake["cards"].wait()
        self._wake["cards"].clear()
        self._name_pass()
        batch = 500
        todo = self.db.cards_to_draft(cards.DRAFTER, batch)
        if not todo:
            return
        self.card_state["running"] = True
        try:
            for i, (sha, kind) in enumerate(todo):
                if self._stopping:
                    return
                try:
                    self._draft_card(sha, kind)
                except Exception:  # noqa: BLE001 - one odd record must not stop the others, nor be tried for ever
                    log.exception("card for %s could not be drafted", sha[:12])
                    self.db.set_card(sha, "empty", cards.DRAFTER)
                if sha in self._notes_wanted:       # a model just downloaded: its notes go to the local model
                    self._notes_wanted.discard(sha)
                    self.reader.request(sha)
                if i % 50 == 49:
                    self.db.bump_revision()
                    self._changed()
                await asyncio.sleep(0)          # requests are answered in between
        finally:
            self.card_state["running"] = False
            self.db.bump_revision()
            self._changed()
        if len(todo) == batch:
            self._kick("cards")

    def card(self, sha, kind=None):
        """A model's card as the API gives it (cards.effective), drafted first when the drafter changed since. None
        when no file here has this fingerprint."""
        kind = kind or self._kind_of(sha)
        if kind is None:
            return None
        record = self.store.load(sha)
        card = record.get("card")
        if cards.card_kind(kind) and cards.needs_draft(card):
            card = self._draft_card(sha, kind, record)
        out = cards.effective(card, kind)
        out["reader"] = self._reader_view(sha, out)
        return out

    def _reader_view(self, sha, card):
        """How the notes behind the card were read, for the dossier's line under it: by the local model (which), or
        by the rules and why (no model set up, a card below the lowest tier, the model missing or failing), or being
        read now."""
        if not card.get("kind"):
            return None
        ev = (card.get("evidence") or {}).get("description") or {}
        by = (ev.get("reader") or {}).get("by") or "rules"
        ready, reason, _label = self.reader.availability()
        state = self.reader.state_of(sha) or {}
        busy = state.get("state") in ("queued", "reading", "waiting_gpu")
        model = reader_mod.display_name((ev.get("reader") or {}).get("model")) if by == "ai" else None
        inputs = None if by == "ai" else self._reader_inputs(sha)
        # notes the local model would be given text of: an empty "<p> </p>" counts for the rules' record, and the
        # model was never handed it, so it stayed "to be read" for ever (2026-10-06)
        has_notes = bool(ev.get("read")) and (by == "ai" or bool(inputs and inputs.get("has_notes")))
        # the model answered these very notes unusably before: not asked again unless the user asks (or they change)
        stuck = None if busy or not inputs else self._failed_reading(sha, inputs)
        if stuck and not state:
            state = {"state": "failed", "error": stuck.get("error")}
        can = bool(ready and has_notes and by != "ai" and not busy)
        return {"by": by, "model": model,
                "state": state.get("state"), "error": state.get("error"), "ready": ready, "reason": reason,
                "has_notes": has_notes, "min_vram_gb": reader_mod.MIN_VRAM_GB,
                # another failed reading (the card busy, the server down) is tried again when the model is opened
                "can_read": can and not stuck, "stuck": bool(stuck), "can_retry": can}

    def _failed_reading(self, sha, inputs):
        """The failed reading kept for this model's notes as they are now (`inputs`, _reader_inputs), or None."""
        failed = (self.store.load(sha).get("card") or {}).get("ai_failed")
        return failed if isinstance(failed, dict) and failed.get("key") == inputs["key"] else None

    def request_notes(self, sha, retry=False):
        """Have the local model read a model's notes, when it can and has not yet (with `retry`, also notes whose
        reading failed). True when queued or already in hand."""
        kind = self._kind_of(sha)
        if not cards.card_kind(kind):
            return False
        view = (self.card(sha, kind) or {}).get("reader") or {}
        if view.get("state") in ("queued", "reading", "waiting_gpu"):
            return True
        if not view.get("can_read") and not (retry and view.get("can_retry")):
            return False
        return self.reader.request(sha)

    def _reader_failed(self, sha, failure):
        """The local model answered these notes unusably: kept in the card, so that filling in does not hand them to it
        again and again."""
        record = self.store.load(sha)
        if not isinstance(record.get("card"), dict):
            return
        record["card"] = dict(record["card"], ai_failed=failure)
        self.store.save(sha, record)
        self.db.bump_revision()
        self._changed()

    def _reader_inputs(self, sha):
        kind = self._kind_of(sha)
        if not cards.card_kind(kind):
            return None
        _version, _model, notes = self._notes_of(kind, self.store.load(sha))
        return notes

    def _reader_result(self, sha, result):
        """What the local model read: kept in the card, which is drafted again around it. The record is read here,
        after the waiting, so that nothing written meanwhile is lost."""
        kind = self._kind_of(sha)
        if not cards.card_kind(kind):
            return
        record = self.store.load(sha)
        card = dict(record.get("card")) if isinstance(record.get("card"), dict) else {}
        card["ai"] = result
        card.pop("ai_failed", None)
        record["card"] = card
        self._draft_card(sha, kind, record)
        self.db.bump_revision()
        self._changed()

    def put_card(self, sha, values, confirmed=None):
        """The user's changes to a card (cards.with_user_values). Returns the card; None when no file here has this
        fingerprint. Raises cards.CardError for a value the card cannot take."""
        kind = self._kind_of(sha)
        if kind is None:
            return None
        if not cards.card_kind(kind):
            raise cards.CardError("kind", "no_card")
        record = self.store.load(sha)
        card = record.get("card")
        if cards.needs_draft(card):
            card = self._draft_card(sha, kind, record)
        card = cards.with_user_values(card, kind, values, confirmed)
        card["state"] = cards.state_of(card, kind)
        record["card"] = card
        self.store.save(sha, record)
        self.db.set_card(sha, card["state"], card.get("drafter") or 0)
        self.db.bump_revision()
        self._changed()
        out = cards.effective(card, kind)
        out["reader"] = self._reader_view(sha, out)
        return out

    # ---- model pages -----------------------------------------------------------------------------------------------
    async def read_page(self, sha, refresh=False, notes=True, force=False):
        """Read the civitai page of the model this file is a version of, keep it, and draft again the cards of its
        versions that are here (the author's notes for the whole model are on that page). A page read before is not
        read again unless `refresh`. The notes then go to the local model, if there is one, unless not `notes` (warming
        up reads pages for models nobody has opened). Returns the kept page; None when the file has no civitai page.
        Raises CivitaiError when civitai cannot be asked. A model civitai no longer has is kept as such."""
        row = self.db.model(sha) if is_sha256(sha) else None
        model_id = row["model_id"] if row is not None and row["source"] else None
        if not model_id:
            return None
        if not refresh:
            page = self.store.load_page(model_id)
            if page is not None:
                return page
        lock = self._page_locks.setdefault(model_id, asyncio.Lock())
        try:
            async with lock:
                page = None if refresh else self.store.load_page(model_id)
                if page is None:
                    try:
                        site, model = await self._get_client().get_model(model_id, force=force)
                    except CivitaiError as e:
                        if e.code != "not_found":
                            raise
                        page = self.store.save_page(model_id, None, None, time.time(), error="not_found")
                    else:
                        page = self.store.save_page(model_id, site, model, time.time())
        finally:
            if not lock.locked():
                self._page_locks.pop(model_id, None)
        for other, kind in self.db.shas_of_model(model_id):
            self._draft_card(other, kind)
        self.db.bump_revision()
        self._changed()
        if notes:
            self.request_notes(sha)     # the notes of the model opened go to the local model, if there is one
        return page

    # ---- warming up ------------------------------------------------------------------------------------------------
    async def _prewarm_loop(self):
        """With the setting on (it is off unless the user turns it on): after a scan, or once civitai has named new
        fingerprints, read the civitai pages the library has none of yet and fetch the first example images of each
        model, so that a model opens at once the first time. Asks civitai at the background work's pace, one item at a
        time; the local model is asked to read nothing. A pass stops when civitai does not answer well; the next scan
        starts another, which goes quickly over what is already here."""
        await self._wake["prewarm"].wait()
        self._wake["prewarm"].clear()
        if not self.cfg.data["prewarm"]["enabled"] or not self.cfg.configured:
            return
        state = self.prewarm_state
        state.update(running=True, last_error=None)
        try:
            for sha, model_id in self.db.warm_candidates():
                warm = self.cfg.data["prewarm"]
                if self._stopping or not warm["enabled"]:
                    return
                try:
                    if self.store.load_page(model_id) is None:
                        await self.read_page(sha, notes=False)
                        state["pages"] += 1
                    images = self._sample_images(sha)
                    stills = [i for i, image in enumerate(images) if not version_info.is_video(image)][:warm["samples"]]
                    for index in stills:
                        if self._stopping:
                            return
                        if not os.path.isfile(self.sample_cache_path(sha, index)):
                            await self.sample_thumbnail(sha, index, interactive=False)
                            state["samples"] += 1
                except CivitaiError as e:
                    state["failed"] += 1
                    state["last_error"] = e.code
                    if e.code in ("network", "rate_limited", "server", "auth"):
                        log.info("warming up stopped: civitai answered %s; the next scan carries on", e.code)
                        return
                except (LookupError, OSError, ValueError) as e:    # one odd model or picture: the next one
                    state["failed"] += 1
                    log.info("warming up skipped %s: %s", sha[:12], e)
                await asyncio.sleep(0)
        finally:
            state["running"] = False

    # ---- filling in the details: every model's page, examples and notes, at the user's request --------------------
    # Without it, a model's civitai page, its example images and the local model's reading of its notes come only when
    # the model is opened (the user found that, 2026-10-05, after making a new library). The page comes first: the notes
    # for the whole model are on it, and the local model reads them with the version's own.
    _CIVITAI_DOWN = ("network", "rate_limited", "server", "auth")

    def _missing_samples(self, sha):
        """The still example images of a model not fetched yet (and not found missing on civitai in this run)."""
        return [i for i, image in enumerate(self._sample_images(sha))
                if not version_info.is_video(image) and (sha, i) not in self._sample_failed
                and not os.path.isfile(self.sample_cache_path(sha, i))]

    def _notes_to_read(self, sha, kind, page_missing):
        """Would filling in hand this model's notes to the local model? Not when it has read them as they are now; a
        model whose page is still to be read has notes to read once it is."""
        if not cards.card_kind(kind):
            return False
        view = (self.card(sha, kind) or {}).get("reader") or {}
        if not view.get("ready") or view.get("by") == "ai" or view.get("state") in ("queued", "reading", "waiting_gpu") \
                or view.get("stuck"):
            return False
        return bool(view.get("has_notes") or page_missing)

    def fill_needs(self):
        """What filling in would do now: {"models", "pages", "samples", "notes", "reader_ready"}: civitai pages not read,
        example images not fetched, notes the local model has yet to read (when one is set up)."""
        out = {"models": 0, "pages": 0, "samples": 0, "notes": 0, "reader_ready": self.reader.availability()[0]}
        for sha, model_id, kind in self.db.named_models():
            out["models"] += 1
            page_missing = bool(model_id) and self.store.load_page(model_id) is None
            out["pages"] += page_missing
            out["samples"] += len(self._missing_samples(sha))
            out["notes"] += self._notes_to_read(sha, kind, page_missing)
        return out

    def fill_progress(self):
        return dict(self.fill_state, reading=self.reader.pending())

    def start_fill(self, shas=None):
        """Start filling in, for every model or only those of `shas`; False when it is already under way."""
        self._tried_by_hand()
        if self.fill_state["running"]:
            return False
        self._fill_only = set(shas) if shas is not None else None
        self._fill_stop = False
        self.fill_state.update(running=True, started_at=time.time(), finished_at=None, total=0, done=0, pages=0, samples=0,
                               notes=0, failed=0, error=None, stopped=False)
        self._fill_task = asyncio.ensure_future(self._fill_run())
        return True

    def stop_fill(self):
        """Stop filling in: nothing more is fetched, and the readings it queued are taken back (one under way ends)."""
        self._fill_stop = True
        self.reader.drop_queued(self._fill_queued)
        self._fill_queued.clear()

    async def _fill_run(self):
        """Each model civitai or the old WebUI's files name, oldest first: its page when it has none, then every still
        example image not fetched yet, at the background work's pace; then its notes go to the local model's queue
        (which reads them one by one, giving the graphics card way to other programs). Stops when civitai does not
        answer well: what is done stays done, and a new start goes quickly over it."""
        state = self.fill_state
        try:
            todo = self.db.named_models()
            if self._fill_only is not None:
                todo = [row for row in todo if row[0] in self._fill_only]
            state["total"] = len(todo)
            for sha, model_id, kind in todo:
                if self._fill_stop or self._stopping:
                    state["stopped"] = True
                    return
                try:
                    page_missing = bool(model_id) and self.store.load_page(model_id) is None
                    if page_missing:
                        await self.read_page(sha, notes=False)
                        state["pages"] += 1
                    for index in self._missing_samples(sha):
                        if self._fill_stop or self._stopping:
                            break
                        try:
                            await self.sample_thumbnail(sha, index, interactive=False)
                            state["samples"] += 1
                        except CivitaiError as e:
                            if e.code in self._CIVITAI_DOWN:
                                raise
                            self._sample_failed.add((sha, index))
                            state["failed"] += 1
                        except (LookupError, OSError, ValueError):     # a picture civitai no longer has, or a broken one
                            self._sample_failed.add((sha, index))
                            state["failed"] += 1
                    if self._notes_to_read(sha, kind, False) and self.reader.request(sha):
                        self._fill_queued.add(sha)
                        state["notes"] += 1
                except CivitaiError as e:
                    if e.code in self._CIVITAI_DOWN:
                        state["error"] = e.code
                        log.info("filling in stopped: civitai answered %s", e.code)
                        return
                    state["failed"] += 1
                except (LookupError, OSError, ValueError) as e:
                    state["failed"] += 1
                    log.info("filling in skipped %s: %s", sha[:12], e)
                state["done"] += 1
                await asyncio.sleep(0)
        finally:
            state.update(running=False, finished_at=time.time())
            self.db.bump_revision()
            self._changed()

    def keep_page(self, model_id, site, model):
        """A model page read for another reason (a download): kept as if read for itself."""
        if model_id and isinstance(model, dict):
            self.store.save_page(model_id, site, model, time.time())

    def _page_summary(self, civitai, page):
        """The `page` block of the detail API: whether the model page has been read, and what it adds."""
        if not civitai:
            return {"state": "none"}
        if page is None:
            return {"state": "missing"}
        if not isinstance(page.get("model"), dict):
            return {"state": page.get("error") or "missing", "fetched_at": page.get("fetched_at")}
        model = page["model"]
        present = self.db.present_hashes()
        versions = []
        for v in model.get("modelVersions") or []:
            if not isinstance(v, dict):
                continue
            here = next((h for h in version_info.version_hashes(v) if h in present), None)
            versions.append({"id": v.get("id"), "name": v.get("name"), "base_model": v.get("baseModel"),
                             "published_at": v.get("publishedAt") or v.get("createdAt"), "in_library": here})
        creator = model.get("creator") if isinstance(model.get("creator"), dict) else {}
        tags = [t if isinstance(t, str) else (t or {}).get("name") for t in model.get("tags") or []]
        return {"state": "ready", "fetched_at": page.get("fetched_at"), "site": page.get("site"),
                "creator": creator.get("username"), "tags": [t for t in tags if t][:40], "versions": versions}

    # ---- the names models go by (names.py) -------------------------------------------------------------------------
    def _name_pass(self):
        """Give every civitai model here a name, once: by the safe rules at once, then by the local model when there is
        one. A name the local model gave, or answered unusably for, is not asked for again."""
        ready = self.reader.availability()[0]
        added = 0
        for model_id, title in self.db.civitai_titles().items():
            entry = self.names.get(model_id)
            if entry is None:
                entry = self.store.save_name(model_id, {"from": title, "name": names_mod.by_rules(title), "en": None,
                                                        "zh": None, "by": "rules", "at": time.time()})
                self.names[model_id] = entry
                added += 1
            if ready and entry.get("by") == "rules" and not entry.get("ai_failed"):
                self.reader.request(reader_mod.NAME + str(model_id))
        if added:
            self.db.bump_revision()
            self._changed()

    def _name_input(self, model_id):
        entry = self.names.get(model_id)
        return {"title": entry["from"]} if entry and entry.get("by") == "rules" and entry.get("from") else None

    def _name_result(self, model_id, answer):
        """The local model's name for a model: used when it only cuts from the title (names.faithful); otherwise the
        rules' name stays, and the model is not asked again."""
        entry = self.names.get(model_id)
        if entry is None:
            return
        tidy = names_mod.faithful(answer, entry["from"]) if answer else None
        entry = dict(entry, **tidy, by="ai", at=time.time()) if tidy else dict(entry, ai_failed=time.time())
        self.names[model_id] = self.store.save_name(model_id, entry)
        if tidy:
            self.db.bump_revision()
            self._changed()

    def _settled_name(self, row):
        """The name settled for this file's civitai model, or None."""
        if row["source"] != "civitai" or not row["model_id"]:
            return None
        return self.names.get(row["model_id"])

    # ---- new versions (updates.py), looked for when the user asks ----------------------------------------------------
    @staticmethod
    def _update_prefs(record):
        """(muted, ignored version ids) the user set for a file."""
        u = record.get("updates") if isinstance(record.get("updates"), dict) else {}
        return bool(u.get("muted")), [i for i in u.get("ignored") or [] if isinstance(i, int)]

    def _page_versions(self, model_id):
        """(page, its versions) as kept here, read from disk once per change of the file."""
        cache = self._page_cache
        try:
            mtime = os.path.getmtime(self.store.page_path(model_id))
        except (OSError, ValueError, TypeError):
            return None, []
        hit = cache.get(model_id)
        if hit and hit[0] == mtime:
            return hit[1], hit[2]
        page = self.store.load_page(model_id)
        model = page.get("model") if page and isinstance(page.get("model"), dict) else None
        versions = [v for v in (model or {}).get("modelVersions") or [] if isinstance(v, dict)]
        cache[model_id] = (mtime, page if model else None, versions)
        return (page if model else None), versions

    def _all_updates(self):
        """{sha: updates.compare(...) plus "muted", "checked_at", "site", "model_id", "version_id"} for every file here
        civitai names whose page has been read; cached until the library changes."""
        revision = self.db.revision()
        if self._updates and self._updates[0] == revision:
            return self._updates[1]
        rows = self.db.civitai_versions()
        here = {}
        for _sha, model_id, version_id, _title in rows:
            here.setdefault(model_id, set()).add(version_id)
        out = {}
        for sha, model_id, version_id, _title in rows:
            page, versions = self._page_versions(model_id)
            if page is None or not version_id:
                continue
            muted, ignored = self._update_prefs(self.store.load(sha))
            found = updates.compare(versions, version_id, here[model_id], ignored)
            if found is None:
                continue
            found.update(muted=muted, checked_at=page.get("fetched_at"), site=page.get("site"), model_id=model_id,
                         version_id=version_id)
            out[sha] = found
        self._updates = (revision, out)
        return out

    def _update_badge(self, sha):
        """The label of the newer version a card shows ("V7"), or None."""
        u = self._all_updates().get(sha) if sha else None
        if not u or not u["newer"] or u["muted"]:
            return None
        return next((updates.short_label(v.get("name")) for v in u["newer"] if updates.short_label(v.get("name"))),
                    None) or names_mod.version_name(u["newer"][0].get("name"), None)

    @staticmethod
    def _version_bytes(v):
        files = [f for f in v.get("files") or [] if isinstance(f, dict)]
        models = [f for f in files if f.get("type") == "Model"] or files
        kb = max((f.get("sizeKB") or 0 for f in models), default=0)
        return int(kb * 1024) if kb else None

    def _update_view(self, sha, row):
        """The dossier's "new version" section: None when the page was never read."""
        u = self._all_updates().get(sha)
        if u is None:
            return None
        site = u.get("site") or self.cfg.data["civitai"]["sites"][0]

        def link(v):
            return "%s/models/%s?modelVersionId=%s" % (site, u["model_id"], v.get("id"))

        def item(v):
            return {"id": v.get("id"), "name": names_mod.version_name(v.get("name"), None), "base_model": v.get("baseModel"),
                    "published_at": v.get("publishedAt") or v.get("createdAt"), "bytes": self._version_bytes(v),
                    "precision": updates.precision(v.get("name")), "url": link(v)}

        newer = None
        if u["newer"] and not u["muted"]:
            lead = min(u["newer"], key=lambda v: (len(updates.precision(v.get("name"))), str(v.get("publishedAt") or "")))
            newer = {"label": self._update_badge(sha), "name": names_mod.version_name(lead.get("name"), None),
                     "base_model": lead.get("baseModel"), "published_at": lead.get("publishedAt") or lead.get("createdAt"),
                     "versions": [item(v) for v in u["newer"]],
                     "default": updates.default_file(u["newer"], row["version_name"]),
                     "ids": [v.get("id") for v in u["newer"]], "description": lead.get("description")}
        # a new entry in several precisions is one entry
        grouped = []
        for v in [] if u["muted"] else u["entries"]:
            key = (v.get("baseModel"), updates.parse(v.get("name")))
            same = next((g for g in grouped if g["key"] == key), None)
            if same is None:
                grouped.append({"key": key, "lead": v, "more": []})
            else:
                same["more"].append(v)
        entries = []
        for g in grouped:
            lead = min([g["lead"]] + g["more"], key=lambda v: len(updates.precision(v.get("name"))))
            others = [p for v in [g["lead"]] + g["more"] if v is not lead for p in updates.precision(v.get("name"))]
            entries.append(dict(item(lead), also=sorted(set(o.upper() for o in others))))
        return {"checked_at": u["checked_at"], "muted": u["muted"], "newer": newer, "entries": entries[:12],
                "entries_total": len(entries)}

    def set_update_prefs(self, sha, muted=None, ignore=None):
        """The user's word on a file's new versions: stop checking it (`muted`), or leave these versions be (`ignore`,
        version ids). Returns the model's detail; None when no file here has this fingerprint."""
        if not is_sha256(sha) or not self.db.joined_by_sha(sha):
            return None
        record = self.store.load(sha)
        prefs = dict(record.get("updates")) if isinstance(record.get("updates"), dict) else {}
        if muted is not None:
            prefs["muted"] = bool(muted)
        if ignore:
            prefs["ignored"] = sorted(set(self._update_prefs(record)[1]) | {int(i) for i in ignore})
        record["updates"] = prefs
        self.store.save(sha, record)
        self.db.bump_revision()
        self._changed()
        return self.detail(sha)

    def update_counts(self):
        """{"newer": files with a new version, "entries": files whose page has new entries only, "models": pages read}"""
        found = [u for u in self._all_updates().values() if not u["muted"]]
        return {"newer": sum(1 for u in found if u["newer"]), "entries": sum(1 for u in found if u["entries"] and not u["newer"]),
                "models": len({u["model_id"] for u in self._all_updates().values()})}

    def update_progress(self):
        last = self.db.get_meta("updates_checked_at")
        return dict(self.update_state, checked_at=float(last) if last else None, counts=self.update_counts(),
                    pages=len({m for _s, m, _v, _t in self.db.civitai_versions()}))

    def start_update_check(self):
        """Read every civitai page of the models here again, to see what is new. False when already under way."""
        self._tried_by_hand()
        if self.update_state["running"]:
            return False
        self._update_stop = False
        self.update_state.update(running=True, started_at=time.time(), finished_at=None, total=0, done=0, failed=0,
                                 error=None, stopped=False)
        self._update_task = asyncio.ensure_future(self._update_run())
        return True

    def stop_update_check(self):
        self._update_stop = True

    async def _update_run(self):
        """One request per civitai page, at the background work's pace; files the user stopped checking are left out.
        A model civitai no longer has keeps the page read before. Stops when civitai does not answer well."""
        state = self.update_state
        try:
            todo = {}
            for sha, model_id, _version_id, _title in self.db.civitai_versions():
                row = self.db.model(sha)
                if row is None or row["source"] != "civitai" or self._update_prefs(self.store.load(sha))[0]:
                    continue
                todo.setdefault(model_id, sha)
            state["total"] = len(todo)
            for model_id, sha in todo.items():
                if self._update_stop or self._stopping:
                    state["stopped"] = True
                    return
                try:
                    site, model = await self._get_client().get_model(model_id)
                    self.store.save_page(model_id, site, model, time.time())
                    for other, kind in self.db.shas_of_model(model_id):
                        self._draft_card(other, kind)
                except CivitaiError as e:
                    if e.code in self._CIVITAI_DOWN:
                        state["error"] = e.code
                        log.info("checking for new versions stopped: civitai answered %s", e.code)
                        return
                    state["failed"] += 1
                except (LookupError, OSError, ValueError) as e:
                    state["failed"] += 1
                    log.info("checking for new versions skipped model %s: %s", model_id, e)
                state["done"] += 1
                if state["done"] % 20 == 0:
                    self.db.bump_revision()
                    self._changed()
                await asyncio.sleep(0)
            self.db.set_meta("updates_checked_at", str(time.time()))
        finally:
            state.update(running=False, finished_at=time.time())
            self.db.bump_revision()
            self._changed()

    # ---- moving a model to the recycle bin -------------------------------------------------------------------------
    def trash_plan(self, sha):
        """What moving a model to the recycle bin takes: each copy of it, with the old WebUI's files named after it,
        and whether that copy can go (inside a model folder of the settings, on a drive with a recycle bin). None
        when no file has this fingerprint."""
        rows = self.db.joined_by_sha(sha) if is_sha256(sha) else []
        if not rows:
            return None
        return [{"path": r["path"], "comfy_name": r["rel"], "bytes": r["bytes"], "sidecars": self._sidecars_of(r["path"]),
                 "inside": folders.owning_root(self.cfg, r["path"]) is not None,
                 "recycle_bin": trash.has_recycle_bin(r["path"])} for r in rows]

    @staticmethod
    def _sidecars_of(model_path):
        """The old WebUI's files beside a model file (see sidecar.py), every one of them. When another model file in
        the folder has the same name with another extension, the files belong to it too and stay where they are."""
        folder, name = os.path.split(model_path)
        stem = os.path.splitext(name)[0].lower()
        try:
            lower = {n.lower(): n for n in os.listdir(folder)}
        except OSError:
            return []
        for n in lower:
            base, ext = os.path.splitext(n)
            if base == stem and ext in kinds.MODEL_EXTENSIONS and n != name.lower():
                return []
        out = []
        for suffix in (sidecar.INFO_SUFFIX, sidecar.CARD_SUFFIX) + sidecar.PREVIEW_SUFFIXES:
            real = lower.get(stem + suffix)
            if real and os.path.isfile(os.path.join(folder, real)):
                out.append(os.path.join(folder, real))
        return out

    async def trash_files(self, sha, paths):
        """Move these copies of a model, each with the old WebUI's files beside it, to the recycle bin, and take them
        out of the lists. Returns every path moved. Raises ValueError("not_this_model" | "outside") for a path that is
        not a copy of this model or not inside a model folder of the settings, and trash.TrashError when Windows does
        not move them (nothing is moved then). The model's record stays: if the file comes back, so does its data."""
        plan = {os.path.normcase(p["path"]): p for p in (self.trash_plan(sha) or [])}
        wanted = {os.path.normcase(str(p)) for p in paths or []}
        if not wanted or not wanted <= set(plan):
            raise ValueError("not_this_model")
        chosen = [plan[k] for k in sorted(wanted)]
        if not all(item["inside"] for item in chosen):
            raise ValueError("outside")
        if not all(item["recycle_bin"] for item in chosen):
            raise trash.TrashError("no_recycle_bin", "; ".join(i["path"] for i in chosen if not i["recycle_bin"]))
        moving = [path for item in chosen for path in [item["path"]] + item["sidecars"]]
        try:
            await asyncio.get_running_loop().run_in_executor(None, self.move_to_trash, moving)
        except trash.TrashError:
            self.request_scan()         # some may have gone after all: the scan finds out which
            raise
        self.db.mark_absent([item["path"] for item in chosen])
        log.info("moved to the recycle bin: %s", "; ".join(moving))
        self._changed()
        self.request_scan()
        return moving

    # ---- downloads -------------------------------------------------------------------------------------------------
    def present_by_hash(self):
        return self.db.present_hashes()

    def adopt_download(self, path, sha, version, site, size, mtime_ns):
        """A file a download has just written and checked: its record gets civitai's data for the version it was
        downloaded as (so no lookup is needed), and the next scan takes the fingerprint as known."""
        record = self.store.load(sha)
        now = time.time()
        record["civitai"] = {"site": site, "fetched_at": now, "version": version, "bundled_count": 0}
        record["download"] = {"downloaded_at": now, "site": site}
        self.store.save(sha, record)
        self.db.ensure_model(sha)
        self._adopt_record(sha)
        self.db.redraft_card(sha)       # drafted once the scan has listed the file (its kind decides the card)
        self._notes_wanted.add(sha)     # then its notes go to the local model, if there is one
        self._hash_hints[os.path.normcase(path)] = (size, mtime_ns, sha)
        self._changed()
        self.request_scan()

    def _apply_hash_hints(self):
        for key, (size, mtime_ns, sha) in list(self._hash_hints.items()):
            row = self.db.file_by_key(key)
            if row is None:
                continue        # not found by this scan (yet): keep the hint for the next one
            del self._hash_hints[key]
            if row["sha256"] is None and row["bytes"] == size and row["mtime_ns"] == mtime_ns:
                self.db.set_hash(row["id"], sha)
                self._after_hash(row["id"], sha)

    def folder_stats(self):
        """How many models each folder holds, for suggesting where a download goes: by folder, by kind, and by kind
        and base model. Counted from the index, cached until the library changes."""
        revision = self.db.revision()
        if self._folder_stats and self._folder_stats[0] == revision:
            return self._folder_stats[1]
        by_dir, by_kind, by_kind_family = {}, {}, {}
        for row in self.db.joined():
            folder = os.path.dirname(row["path"])
            _source, fields = self._identity(row)
            family = self._family(row, fields)
            by_dir[os.path.normcase(folder)] = by_dir.get(os.path.normcase(folder), 0) + 1
            kind = by_kind.setdefault(row["kind"], {})
            kind[folder] = kind.get(folder, 0) + 1
            same = by_kind_family.setdefault((row["kind"], family), {})
            same[folder] = same.get(folder, 0) + 1
        stats = {"by_dir": by_dir, "by_kind": by_kind, "by_kind_family": by_kind_family}
        self._folder_stats = (revision, stats)
        return stats

    # ---- maintenance -----------------------------------------------------------------------------------------------
    async def rebuild_index(self):
        """Throw the index away and build it again from the model files and the records."""
        if self._scan_task is not None and not self._scan_task.done():
            self._scan_again = False
            await asyncio.gather(self._scan_task, return_exceptions=True)
        self._generation += 1
        self._cover_queue.clear()
        self._cover_queued.clear()
        self.db.clear()
        self._adopt_all_records()
        self._changed()
        self.request_scan()

    # ---- reading ---------------------------------------------------------------------------------------------------
    @staticmethod
    def _identity(row):
        """(source, fields): which civitai version the file is, from the index or, before the fingerprint is
        computed, from the old info file next to it."""
        if row["source"]:
            return row["source"], {
                "model_id": row["model_id"], "version_id": row["version_id"], "title": row["title"],
                "version_name": row["version_name"], "model_type": row["model_type"], "base_model": row["base_model"],
                "family": row["model_family"], "trigger_words": _loads(row["trigger_words"], [])}
        info = (_loads(row["sidecar"], {}) or {}).get("info") or {}
        if info.get("version_id") and row["sha256"] is None:
            return "sidecar", info
        return None, {}

    @staticmethod
    def file_status(row):
        """civitai | local | no_check | unidentified | waiting | error. "local": the details are this computer's own, the old
        WebUI's files or, for a model one trained (the user's word, 2026-10-06), the training facts in its header."""
        source, _fields = Library._identity(row)
        if source == "civitai":
            return "civitai"
        if source == "sidecar":
            return "local"
        if row["sha256"] is None:
            return "error" if row["hash_error"] else "waiting"
        if row["civitai_state"] == "pending":
            return "waiting"
        if Library._training_facts(row):
            return "local"
        # an upscaler, a text encoder or a custom node's model civitai does not know: nothing online would add to it,
        # and HuggingFace cannot be asked by fingerprint (the user's word, 2026-10-06)
        return "no_check" if row["kind"] in NO_CHECK_KINDS else "unidentified"

    @staticmethod
    def _training_facts(row):
        """What the header of a file civitai and the old WebUI do not name says about its training ({} when nothing)."""
        meta = (_loads(row["header"], {}) or {}).get("meta")
        if not isinstance(meta, dict) or not meta:
            return {}
        return training.facts(meta, os.path.splitext(os.path.basename(row["rel"]))[0])

    def _family(self, row, fields):
        user = families.from_base_model(row["user_base_model"]) if row["user_base_model"] else families.UNKNOWN
        if user != families.UNKNOWN:
            return user         # the user's word wins: they know what the model was made for
        header = _loads(row["header"], {}) or {}
        stated = fields.get("family")
        if not stated or stated == families.UNKNOWN:
            card = (_loads(row["sidecar"], {}) or {}).get("card") or {}
            stated = card.get("family")
        return families.resolve(header.get("family"), stated, header.get("hint"))

    @staticmethod
    def _trained(row):
        """(title, version) a training tool's output name gives a file civitai does not name, or (None, None)."""
        meta = (_loads(row["header"], {}) or {}).get("meta") or {}
        return training.title_version(os.path.splitext(os.path.basename(row["rel"]))[0], meta.get("ss_output_name"))

    def _original(self, row):
        """(title, version name, base model) as they are without the user's changes: civitai's (or the old WebUI's);
        for a file neither names, its training output name; else the file's name."""
        source, fields = self._identity(row)
        trained = (None, None) if source else self._trained(row)
        settled = self._settled_name(row)
        title = (settled or {}).get("name") or fields.get("title") or trained[0] or os.path.splitext(os.path.basename(row["rel"]))[0]
        return title, fields.get("version_name") or trained[1], fields.get("base_model")

    def model_object(self, row):
        """One entry of the API's model list (docs/接口约定.md). The name and the base model the user gave a model come
        first, then the name settled for its civitai model (names.py), then the original ones (_original). A version's
        name loses its emoji and the base model the group already says."""
        source, fields = self._identity(row)
        title, version_name, base_model = self._original(row)
        settled = self._settled_name(row)
        if source and version_name:
            version_name = names_mod.version_name(version_name, base_model)
        sha = row["sha256"]
        badge = self._update_badge(sha) if source == "civitai" else None
        civitai = None
        if source and fields.get("model_id") and fields.get("version_id"):
            site = row["civitai_site"] or self.cfg.data["civitai"]["sites"][0]
            civitai = {"model_id": fields["model_id"], "version_id": fields["version_id"],
                       "url": "%s/models/%s?modelVersionId=%s" % (site, fields["model_id"], fields["version_id"])}
        has_cover = bool(sha and row["cover_state"] == "ready")
        return {
            "sha256": sha,
            "hash_pending": sha is None,
            "comfy_type": row["comfy_type"],
            "comfy_name": row["rel"],
            "kind": row["kind"],
            "family": self._family(row, fields),
            "base_model": row["user_base_model"] or base_model,
            "title": row["user_title"] or title,
            # the settled name's English and Chinese parts when it has both, for a page to show in its own language
            "title_parts": None if row["user_title"] or not settled or not settled.get("en")
            else {"en": settled["en"], "zh": settled["zh"]},
            "version_name": version_name,
            "bytes": row["bytes"],
            "civitai": civitai,
            "cover": "/api/v1/models/%s/cover" % sha if has_cover else None,
            "cover_level": row["cover_level"] if has_cover else None,
            "trigger_words": fields.get("trigger_words") or [],
            "has_update": bool(badge),
            "update_label": badge,
            "favorite": False,
            "tags": [],
            "card_state": (row["card_state"] or "empty") if sha and cards.card_kind(row["kind"]) else "empty",
            "usage": {"count": 0, "last_used": None},
            "identified_by": {"civitai": "civitai", "sidecar": "local"}.get(source)
            or ("local" if sha and self.file_status(row) == "local" else None),
            "shadowed": bool(row["shadowed"]),
        }

    def list_models(self, kinds_filter=None, families_filter=None, query=None):
        terms = [t for t in (query or "").lower().split() if t]
        out = []
        for row in self.db.joined():
            if kinds_filter and row["kind"] not in kinds_filter:
                continue
            obj = self.model_object(row)
            if families_filter and obj["family"] not in families_filter:
                continue
            if terms:
                hay = " ".join([obj["title"] or "", obj["comfy_name"], obj["version_name"] or "",
                                " ".join(obj["trigger_words"])]).lower()
                if not all(t in hay for t in terms):
                    continue
            out.append(obj)
        return out

    def lookup(self, comfy_type, comfy_name):
        """The file ComfyUI means by this type and name, or None."""
        wanted = kinds.canonical_type(comfy_type)
        name = str(comfy_name or "").replace("/", os.sep).replace("\\", os.sep)
        rows = [r for r in self.db.joined_by_name(name) if wanted in (_loads(r["comfy_types"], []) or [])]
        if not rows:
            return None
        exact = [r for r in rows if r["rel"] == name] or rows
        primary = [r for r in exact if r["comfy_type"] == wanted] or exact
        return primary[0]

    def detail(self, sha):
        """The model object plus what only the detail view needs, or None."""
        if not is_sha256(sha):
            return None
        rows = self.db.joined_by_sha(sha)
        if not rows:
            return None
        obj = self.model_object(rows[0])
        record = self.store.load(sha)
        civ = record.get("civitai") if isinstance(record.get("civitai"), dict) else {}
        version = civ.get("version") if isinstance(civ.get("version"), dict) else None
        if version is None and isinstance(record.get("sidecar"), dict):
            version = record["sidecar"].get("civitai_info")
        verified = rows[0]["verified"]
        obj["card"] = self.card(sha, rows[0]["kind"])
        obj["card_state"] = obj["card"]["state"]
        obj["files"] = [{
            "path": r["path"], "bytes": r["bytes"], "modified": r["mtime_ns"] / 1e9,
            "comfy_type": r["comfy_type"], "comfy_name": r["rel"], "shadowed": bool(r["shadowed"]),
            "check": "unknown" if verified is None else ("match" if verified else "mismatch"),
        } for r in rows]
        obj["description"] = (version or {}).get("description")
        model_block = (version or {}).get("model") if isinstance((version or {}).get("model"), dict) else {}
        page = self.store.load_page(obj["civitai"]["model_id"]) if obj["civitai"] else None
        if page is not None and isinstance(page.get("model"), dict):
            obj["model_description"] = page["model"].get("description")
        else:
            obj["model_description"] = model_block.get("description")
        obj["page"] = self._page_summary(obj["civitai"], page)
        obj["update"] = self._update_view(sha, rows[0]) if obj["civitai"] else None
        obj["samples"] = version_info.samples(version)
        # the copy kept here of each still example (fetched from civitai the first time it is asked for), for pages
        # that load nothing from the internet
        for i, s in enumerate(obj["samples"]):
            still = s.get("type") != "video"
            s["local_url"] = "/api/v1/models/%s/samples/%d" % (sha, i) if still else None
            s["cached"] = still and os.path.isfile(self.sample_cache_path(sha, i))
        obj["bundled_in"] = {"count": rows[0]["bundled"] or 0, "examples": civ.get("bundled_in") or []}
        # what the user added and changed
        user = record.get("user") if isinstance(record.get("user"), dict) else {}
        title, _version_name, base_model = self._original(rows[0])
        obj["user"] = {"title": user.get("title"), "base_model": user.get("base_model")}
        obj["original"] = {"title": title, "base_model": base_model}
        obj["images"] = [{"id": i["id"], "url": "/api/ui/models/%s/images/%s" % (sha, i["id"]), "width": i.get("width"),
                          "height": i.get("height"), "params": i.get("params"), "name": i.get("name"),
                          "added_at": i.get("added_at")} for i in self._user_images(record)]
        obj["cover_from"] = self._cover_from(record, obj["samples"]) if obj["cover"] else None
        keys = set()
        for item in obj["samples"] + obj["images"]:
            keys.update(resource_keys(item.get("params")))
        obj["resource_matches"] = self.resource_matches(keys)
        # a model neither civitai nor the old WebUI's files name: what its header says about its training
        meta = (_loads(rows[0]["header"], {}) or {}).get("meta") if version is None else None
        stem = os.path.splitext(os.path.basename(rows[0]["rel"]))[0]
        obj["training"] = training.facts(meta, stem) or None if meta else None
        return obj

    def summary(self):
        """Counts for the status API and the inventory page, cached until the library changes."""
        revision = self.db.revision()
        if self._summary and self._summary[0] == revision:
            return self._summary[1]
        total = {"files": 0, "bytes": 0, "civitai": 0, "local": 0, "no_check": 0, "unidentified": 0, "waiting": 0, "error": 0}
        by_kind = {}
        for row in self.db.joined():
            status = self.file_status(row)
            for bucket in (total, by_kind.setdefault(row["kind"], dict.fromkeys(total, 0))):
                bucket["files"] += 1
                bucket["bytes"] += row["bytes"]
                bucket[status] += 1
        out = dict(total, identified=total["civitai"] + total["local"],
                   by_kind={k: dict(v, identified=v["civitai"] + v["local"]) for k, v in by_kind.items()})
        self._summary = (revision, out)
        return out

    def status(self):
        """The `library` block of GET /api/v1/status."""
        s = self.summary()
        scanned_at = self.db.get_meta("scanned_at")
        return {"files": s["files"], "identified": s["identified"], "scanning": self.busy,
                "scanned_at": float(scanned_at) if scanned_at else None, "revision": self.db.revision()}

    def progress(self):
        """What the background work is doing, for the inventory page."""
        models = self.db.model_counts()
        return {
            "busy": self.busy,
            "scan": {k: self.scan_state[k] for k in ("running", "started_at", "finished_at", "error", "folders", "last")},
            "hash": self.hash_progress(),
            "reach": dict(self.reach_state),
            "civitai": {"running": self.civitai_state["running"], "found": models["found"],
                        "not_found": models["not_found"], "bundled": models["bundled"], "pending": models["unchecked"],
                        "last_at": self.civitai_state["last_at"], "last_error": self.civitai_state["last_error"],
                        "waiting_until": self.civitai_state["waiting_until"]},
            "covers": {"running": self.cover_state["running"], "ready": models["covers_ready"],
                       "failed": models["covers_failed"]},
            # warming up: counted since the program started
            "prewarm": dict(self.prewarm_state, enabled=self.cfg.data["prewarm"]["enabled"]),
            "fill": self.fill_progress(),
            "updates": self.update_progress(),
        }

    def inventory(self):
        """Every file with what is known about it: the inventory page's table."""
        out = []
        for row in self.db.joined():
            obj = self.model_object(row)
            out.append({
                "sha256": obj["sha256"], "name": obj["comfy_name"], "comfy_type": obj["comfy_type"], "kind": obj["kind"],
                "family": obj["family"], "base_model": obj["base_model"], "title": obj["title"],
                "version_name": obj["version_name"], "bytes": obj["bytes"], "folder": row["folder"],
                "status": self.file_status(row), "civitai_url": obj["civitai"]["url"] if obj["civitai"] else None,
                "shadowed": obj["shadowed"], "error": row["hash_error"],
                "format": row["ext"].lstrip("."), "cover": obj["cover"],
                # civitai lists the file, but only as an extra attached to this many other models
                "bundled": row["bundled"] if row["civitai_state"] == "bundled" else 0,
            })
        return out
