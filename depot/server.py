"""The HTTP server: the API in docs/接口约定.md under /api/v1/, the page's own calls under /api/ui/, and the page.

Who may call:
  - a request is local when it comes from this computer AND is addressed to it by a loopback name. The second half
    stops a web page elsewhere from reaching the server through a made-up domain name that points at 127.0.0.1;
  - anything else is refused unless remote access is switched on in the settings and the request carries the key;
  - a browser page from another origin may not change anything (requests that carry a foreign Origin are refused).
"""
import asyncio
import hmac
import ipaddress
import json
import logging
import mimetypes
import os
import re
import string
import subprocess
import sys
import time
import urllib.parse

import aiohttp
from aiohttp import web

from . import API_VERSION, APP_NAME, __version__, card_samples, comfy, config as config_mod, folders, kinds, links
from . import inbox as inbox_mod
from . import llm_install, paths as paths_mod, reader as reader_mod, scan, sniff
from .cards import CardError
from .civitai import CivitaiError
from .downloads import AddError
from .image_meta import ImageError
from .store import is_sha256
from .strings import LANGS, STRINGS, t
from .trash import TrashError

log = logging.getLogger("depot.server")

_LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})
_MAX_BROWSE_ENTRIES = 2000
_TRANSIENT_ERRORS = {"network", "server", "rate_limited", "link_expired", "incomplete", "range_mismatch"}
_EVENT_KEEPALIVE = 15.0


def _civitai_api_error(e):
    """An ApiError for a civitai failure while reading a link."""
    if e.code == "not_found":
        return ApiError(404, "not_found", "err.civitai_not_found")
    if e.code == "auth":
        return ApiError(502, "civitai_auth", "err.civitai_auth")
    if e.code in ("not_json", "too_large", "bad_request"):
        return ApiError(502, "civitai_bad", "err.civitai_bad")
    return ApiError(502, "civitai_unreachable", "err.civitai_unreachable")


def _size(n):
    if n is None:
        return ""
    for unit, scale in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= scale:
            return "%.1f %s" % (n / scale, unit)
    return "%d B" % n


class ApiError(Exception):
    def __init__(self, status, code, key=None, **params):
        super().__init__(code)
        self.status = status
        self.code = code
        self.key = key or "err." + code
        self.params = params
        self.extra = {}          # more fields for the JSON answer


def _host_name(host_header):
    h = (host_header or "").strip().lower()
    if h.startswith("["):
        return h[1:h.index("]")] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def _is_loopback(address):
    try:
        ip = ipaddress.ip_address(address or "")
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return bool(ip.is_loopback or (mapped is not None and mapped.is_loopback))


def _list_roots():
    """Where folder browsing starts: the drives on Windows, / elsewhere."""
    if sys.platform == "win32":
        import ctypes   # noqa: PLC0415
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        return [letter + ":\\" for i, letter in enumerate(string.ascii_uppercase) if mask & (1 << i)]
    return ["/"]


class Server:
    def __init__(self, paths, cfg, library, intake=None, downloads=None, inbox=None):
        self.paths = paths
        self.cfg = cfg
        self.library = library
        self.intake = intake
        self.downloads = downloads
        self.inbox = inbox
        self.shutdown_event = asyncio.Event()
        self.started_at = time.time()
        self.bound = None        # (host, port) actually listening, set by serve()
        self._subscribers = set()           # queues of the open event streams
        self._closing = False
        self._library_note = None           # a pending library_changed event
        library.listeners.append(self._library_changed)
        if downloads is not None:
            downloads.listeners.append(self._broadcast)

    # ---- who is asking ---------------------------------------------------------------------------------------------
    def lang(self, request):
        asked = (request.headers.get("X-Lang") or "").strip().lower()
        if asked in LANGS:
            return asked
        if self.cfg.language:
            return self.cfg.language
        accept = (request.headers.get("Accept-Language") or "").lower()
        return "zh" if accept.startswith("zh") else "en"

    @staticmethod
    def is_local(request):
        return _is_loopback(request.remote) and _host_name(request.headers.get("Host")) in _LOOPBACK_NAMES

    def _check_access(self, request):
        origin = request.headers.get("Origin")
        if origin and request.method not in ("GET", "HEAD", "OPTIONS"):
            scheme_host = "%s://%s" % (request.scheme, request.headers.get("Host", ""))
            if origin.rstrip("/").lower() != scheme_host.lower():
                raise ApiError(403, "forbidden")
        if self.is_local(request):
            return
        remote = self.cfg.data["remote_access"]
        if request.path.startswith("/api/admin/") or not remote["enabled"] or not remote["key"]:
            raise ApiError(403, "forbidden")
        given = request.headers.get("Authorization", "")
        if not given.startswith("Bearer ") or not hmac.compare_digest(given[7:].strip().encode(), remote["key"].encode()):
            raise ApiError(401, "unauthorized")

    # ---- app -------------------------------------------------------------------------------------------------------
    def build_app(self):
        @web.middleware
        async def guard(request, handler):
            is_api = request.path.startswith("/api/")
            try:
                self._check_access(request)
                return await handler(request)
            except ApiError as e:
                message = t(self.lang(request), e.key, **e.params)
                body = {"error": message, "code": e.code}
                body.update(e.extra)
                return web.json_response(body, status=e.status) if is_api or e.status in (401, 403) \
                    else web.Response(status=e.status, text=message)
            except web.HTTPException as e:
                if not is_api or e.status < 400:
                    raise
                code = "not_found" if e.status == 404 else "bad_request" if e.status < 500 else "internal"
                return web.json_response({"error": t(self.lang(request), "err." + code), "code": code}, status=e.status)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("request failed: %s %s", request.method, request.path)
                if not is_api:
                    raise
                return web.json_response({"error": t(self.lang(request), "err.internal"), "code": "internal"}, status=500)

        app = web.Application(middlewares=[guard], client_max_size=4 * 1024 * 1024)
        r = app.router
        r.add_get("/api/v1/status", self.api_status)
        r.add_get("/api/v1/models", self.api_models)
        r.add_get("/api/v1/models/lookup", self.api_lookup)
        r.add_get("/api/v1/models/{sha256}", self.api_model)
        r.add_get("/api/v1/models/{sha256}/cover", self.api_cover)
        r.add_get("/api/v1/models/{sha256}/samples/{index}", self.ui_sample)       # the same copy the dossier shows
        r.add_get("/api/v1/models/{sha256}/card", self.api_card)
        r.add_put("/api/v1/models/{sha256}/card", self.api_put_card)
        r.add_post("/api/v1/scan", self.api_scan)
        r.add_get("/api/v1/events", self.api_events)

        r.add_post("/api/ui/intake/parse", self.ui_intake_parse)
        r.add_post("/api/ui/intake/preview", self.ui_intake_preview)
        r.add_get("/api/ui/intake/folders", self.ui_intake_folders)
        r.add_post("/api/ui/intake/folders", self.ui_intake_new_folder)
        r.add_get("/api/ui/intake/versions/{version_id}/images/{index}", self.ui_intake_image)
        r.add_get("/api/ui/downloads", self.ui_downloads)
        r.add_post("/api/ui/downloads", self.ui_downloads_add)
        r.add_post("/api/ui/downloads/clear", self.ui_downloads_clear)
        r.add_post("/api/ui/downloads/{id}/{action}", self.ui_download_action)

        r.add_get("/api/ui/state", self.ui_state)
        r.add_get("/api/ui/strings", self.ui_strings)
        r.add_get("/api/ui/browse", self.ui_browse)
        r.add_post("/api/ui/detect", self.ui_detect)
        r.add_post("/api/ui/check-folder", self.ui_check_folder)
        r.add_post("/api/ui/check-env", self.ui_check_env)
        r.add_put("/api/ui/config", self.ui_save_config)
        r.add_get("/api/ui/progress", self.ui_progress)
        r.add_get("/api/ui/inventory", self.ui_inventory)
        r.add_post("/api/ui/hashing", self.ui_hashing)
        r.add_post("/api/ui/recheck", self.ui_recheck)
        r.add_post("/api/ui/rebuild", self.ui_rebuild)
        r.add_post("/api/ui/folders/forget", self.ui_forget_folders)
        r.add_get("/api/ui/fill", self.ui_fill)
        r.add_get("/api/ui/inbox", self.ui_inbox)
        r.add_post("/api/ui/inbox", self.ui_inbox_action)
        r.add_post("/api/ui/fill", self.ui_fill_action)
        r.add_get("/api/ui/updates", self.ui_updates)
        r.add_post("/api/ui/updates", self.ui_updates_action)
        r.add_put("/api/ui/models/{sha256}/updates", self.ui_update_prefs)
        r.add_get("/api/ui/models/{sha256}/samples/{index}", self.ui_sample)
        r.add_post("/api/ui/models/{sha256}/page", self.ui_read_page)
        r.add_post("/api/ui/models/{sha256}/notes", self.ui_read_notes)
        r.add_get("/api/ui/reader", self.ui_reader)
        r.add_post("/api/ui/reader/install", self.ui_reader_install)
        r.add_post("/api/ui/reader/install/cancel", self.ui_reader_install_cancel)
        r.add_post("/api/ui/reader/probe", self.ui_reader_probe)
        r.add_post("/api/ui/reader/stop", self.ui_reader_stop)
        r.add_post("/api/ui/open-folder", self.ui_open_folder)
        r.add_get("/api/ui/models/{sha256}/trash", self.ui_trash_plan)
        r.add_post("/api/ui/models/{sha256}/trash", self.ui_trash)
        r.add_post("/api/ui/models/{sha256}/images", self.ui_add_image)
        r.add_get("/api/ui/models/{sha256}/images/{image_id}", self.ui_image)
        r.add_delete("/api/ui/models/{sha256}/images/{image_id}", self.ui_delete_image)
        r.add_post("/api/ui/models/{sha256}/cover", self.ui_set_cover)
        r.add_put("/api/ui/models/{sha256}/info", self.ui_set_info)
        r.add_post("/api/admin/shutdown", self.admin_shutdown)

        r.add_get("/", self.page)
        r.add_get("/web/{path:.*}", self.static)
        return app

    @staticmethod
    async def _json_body(request):
        if not (request.content_type or "").startswith("application/json"):
            raise ApiError(400, "bad_request", "err.json_expected")
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError) as e:
            raise ApiError(400, "bad_request", "err.json_expected") from e
        if not isinstance(body, dict):
            raise ApiError(400, "bad_request", "err.json_expected")
        return body

    # ---- /api/v1 ---------------------------------------------------------------------------------------------------
    async def api_status(self, request):
        return web.json_response({"app": APP_NAME, "version": __version__, "api": API_VERSION,
                                  "configured": self.cfg.configured, "library": self.library.status()})

    async def api_models(self, request):
        def listed(name):
            raw = request.query.get(name, "")
            return {x.strip() for x in raw.split(",") if x.strip()} or None

        models = self.library.list_models(listed("kind"), listed("family"), request.query.get("q"))
        return web.json_response({"models": models, "revision": self.library.db.revision()})

    async def api_lookup(self, request):
        ctype, name = request.query.get("comfy_type", ""), request.query.get("comfy_name", "")
        if not ctype or not name:
            raise ApiError(400, "bad_request", "err.lookup_needs_both")
        row = self.library.lookup(ctype, name)
        if row is None:
            raise ApiError(404, "not_found", "err.model_not_found")
        return web.json_response(self.library.model_object(row))

    async def api_model(self, request):
        detail = self.library.detail(request.match_info["sha256"].lower())
        if detail is None:
            raise ApiError(404, "not_found", "err.model_not_found")
        return web.json_response(detail)

    async def api_cover(self, request):
        sha = request.match_info["sha256"].lower()
        model = self.library.db.model(sha) if is_sha256(sha) else None
        if model is None or model["cover_state"] != "ready" or not self.library.store.has_cover(sha):
            raise ApiError(404, "not_found", "err.cover_not_found")
        return web.FileResponse(self.library.store.cover_path(sha), headers={"Cache-Control": "no-cache"})

    async def api_card(self, request):
        card = self.library.card(request.match_info["sha256"].lower())
        if card is None:
            raise ApiError(404, "not_found", "err.model_not_found")
        return web.json_response(card)

    async def api_put_card(self, request):
        """The user's values for a card: {"values": {field: value or null}, "confirmed": true | false (optional)}."""
        body = await self._json_body(request)
        values = body.get("values", {})
        confirmed = body.get("confirmed")
        if not isinstance(values, dict) or confirmed not in (None, True, False):
            raise ApiError(400, "bad_request", "err.card_body")
        try:
            card = self.library.put_card(request.match_info["sha256"].lower(), values, confirmed)
        except CardError as e:
            key = {"unknown_field": "err.card_unknown_field", "no_card": "err.card_not_for_kind"}.get(e.code,
                                                                                                     "err.card_bad_value")
            err = ApiError(400, "bad_request", key, field=e.field)
            err.extra = {"field": e.field}
            raise err from e
        if card is None:
            raise ApiError(404, "not_found", "err.model_not_found")
        return web.json_response(card)

    async def api_scan(self, request):
        await request.read()        # an unread body would keep the connection lingering for ten seconds
        if not self.cfg.configured:
            raise ApiError(409, "not_configured")
        self.library.request_scan()
        return web.json_response({"ok": True, "message": t(self.lang(request), "msg.scan_started")})

    # ---- /api/v1/events: what changed, as it happens (server-sent events) ------------------------------------------
    def _broadcast(self, event):
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass        # a reader that does not keep up misses progress notes, not the next state

    def _library_changed(self, _revision):
        """The library changes many times a second while a scan runs: one event per half second is enough."""
        if self._library_note is not None or not self._subscribers:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        def send():
            self._library_note = None
            self._broadcast({"type": "library_changed", "revision": self.library.db.revision()})

        self._library_note = loop.call_later(0.5, send)

    def close_events(self):
        """End the open event streams, so that stopping the server does not wait for their readers."""
        self._closing = True
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

    async def api_events(self, request):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache",
                                           "X-Accel-Buffering": "no"})
        await resp.prepare(request)
        queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)

        def frame(event):
            return ("data: %s\n\n" % json.dumps(event, ensure_ascii=False)).encode("utf-8")

        try:
            await resp.write(frame({"type": "hello", "api": API_VERSION, "revision": self.library.db.revision()}))
            while not self._closing:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_EVENT_KEEPALIVE)
                except asyncio.TimeoutError:
                    await resp.write(b": keep-alive\n\n")
                    continue
                if event is None:
                    break
                await resp.write(frame(event))
        except (ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            self._subscribers.discard(queue)
        return resp

    # ---- /api/ui: the page's own calls -----------------------------------------------------------------------------
    async def ui_state(self, request):
        return web.json_response({
            "app": APP_NAME, "version": __version__, "configured": self.cfg.configured,
            "language": self.lang(request), "language_chosen": bool(self.cfg.language), "local": self.is_local(request),
            "config": self.cfg.public(), "config_problem": self.cfg.load_problem,
            "data_dir": self.paths.root, "types": list(kinds.COMMON_TYPES),
            "kind_by_type": dict(kinds.KIND_BY_TYPE), "kinds": list(kinds.KINDS),
            "defaults": {"sites": list(config_mod.CIVITAI_SITES), "env_var": "CIVITAI_TOKEN"},
            # the names a card's sampler and scheduler are chosen from when it is edited (ComfyUI's)
            "samplers": list(card_samples.COMFY_SAMPLERS), "schedulers": list(card_samples.COMFY_SCHEDULERS),
        })

    async def ui_strings(self, request):
        return web.json_response({"langs": list(LANGS), "strings": {k: list(v) for k, v in STRINGS.items()}})

    async def ui_browse(self, request):
        """The sub-folders of a folder, for picking one without typing its path."""
        path = request.query.get("path", "").strip().strip('"')
        with_files = request.query.get("files") == "1"
        roots = _list_roots()
        if not path:
            return web.json_response({"path": "", "parent": None, "roots": roots, "files": [],
                                      "dirs": [{"name": r, "path": r} for r in roots]})
        path = os.path.normpath(os.path.expanduser(os.path.expandvars(path)))
        if not os.path.isabs(path) or not os.path.isdir(path):
            raise ApiError(404, "not_found", "err.folder_not_found", path=path)
        dirs, files = [], []
        try:
            with os.scandir(path) as it:
                for entry in it:
                    try:
                        if entry.is_dir():
                            dirs.append({"name": entry.name, "path": entry.path})
                        elif with_files:
                            files.append({"name": entry.name, "path": entry.path})
                    except OSError:
                        continue
                    if len(dirs) + len(files) >= _MAX_BROWSE_ENTRIES:
                        break
        except OSError as e:
            raise ApiError(403, "forbidden", "err.folder_unreadable", path=path) from e
        dirs.sort(key=lambda d: d["name"].lower())
        files.sort(key=lambda d: d["name"].lower())
        parent = os.path.dirname(path.rstrip("\\/")) if path.rstrip("\\/") else None
        if parent == path or not parent or (len(path) <= 3 and path.endswith(":\\")):
            parent = ""          # above a drive: back to the list of drives
        return web.json_response({"path": path, "parent": parent, "roots": roots, "dirs": dirs, "files": files})

    async def ui_detect(self, request):
        body = await self._json_body(request)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, comfy.detect, str(body.get("comfy_dir") or ""),
                                            str(body.get("models_dir") or ""))
        for folder in result["folders"]:
            folder["kind"] = kinds.kind_of(folder["comfy_type"])
        return web.json_response(result)

    async def ui_check_folder(self, request):
        body = await self._json_body(request)
        path = os.path.normpath(os.path.expanduser(os.path.expandvars(str(body.get("path") or "").strip().strip('"'))))
        if not path or not os.path.isabs(path):
            raise ApiError(400, "bad_request", "err.folder_not_absolute")
        exists = os.path.isdir(path)
        files = size = 0
        suggested = ""
        if exists:
            loop = asyncio.get_running_loop()
            files, size = await loop.run_in_executor(None, scan.count_models, path)
            suggested = await loop.run_in_executor(None, self._suggest_type, path)
        return web.json_response({"path": path, "exists": exists, "files": files, "bytes": size,
                                  "suggested_type": suggested})

    _TYPE_BY_FOLDER_NAME = {
        "lora": "loras", "loras": "loras", "lycoris": "loras", "stable_diffusion": "checkpoints",
        "checkpoints": "checkpoints", "checkpoint": "checkpoints", "unet": "diffusion_models",
        "diffusion_models": "diffusion_models", "vae": "vae", "embeddings": "embeddings", "embedding": "embeddings",
        "text_encoder": "text_encoders", "text_encoders": "text_encoders", "clip": "text_encoders",
        "esrgan": "upscale_models", "upscale_models": "upscale_models", "controlnet": "controlnet",
        "clip_vision": "clip_vision"}

    @classmethod
    def _suggest_type(cls, path):
        """A ComfyUI type to propose for a folder added by hand: from its name ("Lora" -> loras), else from what
        the first few files in it are, else from a parent folder's name, else the folder's own name ("other")."""
        parts = [p for p in os.path.normpath(path).split(os.sep) if p]

        def by_name(name):
            return cls._TYPE_BY_FOLDER_NAME.get(name.lower().replace("-", "_").replace(" ", "_"))

        if parts and by_name(parts[-1]):
            return by_name(parts[-1])
        seen, votes = 0, {}
        for current, names in scan.walk(path):
            for name in names:
                if os.path.splitext(name)[1].lower() not in kinds.MODEL_EXTENSIONS:
                    continue
                seen += 1
                content = (sniff.sniff(os.path.join(current, name), name) or {}).get("content")
                ctype = scan._CONTENT_TYPE.get(content)
                if ctype:
                    votes[ctype] = votes.get(ctype, 0) + 1
                if seen >= 8:
                    break
            if seen >= 8:
                break
        if votes:
            return max(votes, key=votes.get)
        for name in reversed(parts[-3:-1]):
            if by_name(name):
                return by_name(name)
        return parts[-1] if parts else ""

    async def ui_check_env(self, request):
        """Whether a .env file holds the named variable. The value itself is never returned."""
        body = await self._json_body(request)
        path = os.path.normpath(os.path.expanduser(os.path.expandvars(str(body.get("env_file") or "").strip().strip('"'))))
        name = str(body.get("env_var") or "CIVITAI_TOKEN").strip()
        _value, problem = config_mod.read_env_var(path, name)
        return web.json_response({"ok": problem is None, "problem": problem})

    async def ui_save_config(self, request):
        body = await self._json_body(request)
        allowed = ("language", "folders", "comfyui", "civitai", "network", "hashing", "setup_done", "reader")
        changes = {k: body[k] for k in allowed if k in body}
        if isinstance(changes.get("civitai"), dict):
            changes["civitai"] = {k: v for k, v in changes["civitai"].items()
                                  if k in ("token_source", "token", "env_file", "env_var")}
        def working_parts():
            data = self.cfg.data
            return json.dumps([data["setup_done"], data["folders"], data["civitai"], data["network"],
                               data["library_dir"], data["hashing"]], sort_keys=True)

        before = working_parts()
        reader_before = json.dumps(self.cfg.data["reader"], sort_keys=True)
        problems = self.cfg.update(changes)
        if problems:
            lang = self.lang(request)
            err = ApiError(400, "bad_settings", "err.bad_settings")
            err.extra = {"problems": [{"field": f, "code": c, "message": t(lang, "problem." + c)} for f, c in problems]}
            raise err
        self.cfg.save()
        if working_parts() != before:       # a change of language alone starts no scan
            await self.library.reconfigure()
        elif json.dumps(self.cfg.data["reader"], sort_keys=True) != reader_before:
            self.library.reader.reconfigure()      # another local model: the one running, if any, is stopped
        return web.json_response({"ok": True, "configured": self.cfg.configured, "config": self.cfg.public()})

    async def ui_forget_folders(self, request):
        """Take folders that no longer exist out of the settings (the user confirmed they were deleted or moved):
        {"paths": [...]}. A folder that exists is left in, whatever was asked. Returns the paths taken out."""
        body = await self._json_body(request)
        paths = body.get("paths")
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise ApiError(400, "bad_request")
        wanted = {os.path.normcase(os.path.normpath(p)) for p in paths}
        gone = lambda f: os.path.normcase(f["path"]) in wanted and not os.path.isdir(f["path"])   # noqa: E731
        folders = self.cfg.data["folders"]
        removed = sorted({f["path"] for f in folders if gone(f)})
        if removed:
            problems = self.cfg.update({"folders": [f for f in folders if not gone(f)]})
            if problems:
                raise ApiError(400, "bad_settings", "err.bad_settings")
            self.cfg.save()
            log.info("folders taken out of the settings: %s", "; ".join(removed))
            await self.library.reconfigure()
        return web.json_response({"removed": removed, "config": self.cfg.public()})

    async def ui_progress(self, request):
        return web.json_response({"revision": self.library.db.revision(), "configured": self.cfg.configured,
                                  "summary": self.library.summary(), "progress": self.library.progress()})

    async def ui_inventory(self, request):
        return web.json_response({"revision": self.library.db.revision(), "summary": self.library.summary(),
                                  "files": self.library.inventory()})

    async def ui_hashing(self, request):
        body = await self._json_body(request)
        if body.get("paused"):
            self.library.pause_hashing()
        else:
            self.library.resume_hashing()
        return web.json_response({"paused": self.library.control.paused})

    async def ui_recheck(self, request):
        body = await self._json_body(request)
        return web.json_response({"ok": True, "asked_again": self.library.recheck(bool(body.get("all")))})

    async def ui_rebuild(self, request):
        await self._json_body(request)
        await self.library.rebuild_index()
        return web.json_response({"ok": True})

    async def ui_fill(self, request):
        """What filling in the details would do now, and how the last run went."""
        return web.json_response({"needs": self.library.fill_needs(), "progress": self.library.fill_progress()})

    async def ui_fill_action(self, request):
        """Fill in every model's civitai page, example images and the local model's reading of its notes:
        {"action": "start" | "stop"}; with "shas", only those models (the library's selection)."""
        body = await self._json_body(request)
        action = body.get("action")
        if action == "start":
            if not self.cfg.configured:
                raise ApiError(409, "not_configured")
            shas = body.get("shas")
            if shas is not None and (not isinstance(shas, list) or not all(isinstance(s, str) and is_sha256(s.lower()) for s in shas)):
                raise ApiError(400, "bad_request")
            started = self.library.start_fill([s.lower() for s in shas] if shas is not None else None)
            if not started and shas is not None:
                raise ApiError(409, "fill_running", "err.fill_running")
        elif action == "stop":
            self.library.stop_fill()
        else:
            raise ApiError(400, "bad_request")
        return web.json_response({"progress": self.library.fill_progress()})

    async def ui_inbox(self, request):
        """What the inbox folders hold and where each file would go (inbox.py), and how the last sorting went."""
        if self.inbox is None:
            raise ApiError(404, "not_found")
        plan = await self.inbox.make_plan() if self.cfg.configured else {"folders": [], "items": [], "skipped": [], "bytes": 0}
        return web.json_response(dict(plan, progress=self.inbox.progress()))

    async def ui_inbox_action(self, request):
        """Sort the inbox: {"action": "start", "paths": [...]} moves the listed files into the library; {"action":
        "stop"} stops after the file being moved (a copy between drives under way is undone)."""
        if self.inbox is None:
            raise ApiError(404, "not_found")
        body = await self._json_body(request)
        action = body.get("action")
        if action == "start":
            if not self.cfg.configured:
                raise ApiError(409, "not_configured")
            paths = body.get("paths")
            if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
                raise ApiError(400, "bad_request")
            try:
                await self.inbox.start(paths)
            except inbox_mod.InboxError as e:
                key = {"busy": "err.inbox_busy", "nothing": "err.inbox_nothing"}[e.code]
                raise ApiError(409, "inbox_" + e.code, key) from e
        elif action == "stop":
            self.inbox.stop()
        else:
            raise ApiError(400, "bad_request")
        return web.json_response({"progress": self.inbox.progress()})

    async def ui_updates(self, request):
        """How the last check for new versions went, and what it found."""
        return web.json_response({"progress": self.library.update_progress()})

    async def ui_updates_action(self, request):
        """Read every model's civitai page again for new versions: {"action": "start" | "stop"}. Only at the user's
        request (the user's word, 2026-10-14: no checking on its own)."""
        body = await self._json_body(request)
        action = body.get("action")
        if action == "start":
            if not self.cfg.configured:
                raise ApiError(409, "not_configured")
            self.library.start_update_check()
        elif action == "stop":
            self.library.stop_update_check()
        else:
            raise ApiError(400, "bad_request")
        return web.json_response({"progress": self.library.update_progress()})

    async def ui_update_prefs(self, request):
        """{"muted": bool} stops or resumes checking a file for new versions; {"ignore": [version ids]} leaves those
        versions be. Returns the model's detail."""
        body = await self._json_body(request)
        sha = request.match_info["sha256"].lower()
        muted = body.get("muted")
        ignore = body.get("ignore")
        if (muted is not None and not isinstance(muted, bool)) or (ignore is not None and (
                not isinstance(ignore, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ignore))):
            raise ApiError(400, "bad_request")
        detail = self.library.set_update_prefs(sha, muted=muted, ignore=ignore)
        if detail is None:
            raise ApiError(404, "not_found", "err.model_not_found")
        return web.json_response(detail)

    async def ui_sample(self, request):
        """A small copy of one of the author's example images, fetched from civitai the first time."""
        sha = request.match_info["sha256"].lower()
        try:
            index = int(request.match_info["index"])
        except ValueError as e:
            raise ApiError(404, "not_found", "err.sample_not_found") from e
        try:
            path = await self.library.sample_thumbnail(sha, index)
        except LookupError as e:
            raise ApiError(404, "not_found", "err.sample_not_found") from e
        except (CivitaiError, OSError, ValueError) as e:
            log.info("example image %s/%d could not be fetched: %s", sha[:12], index, e)
            raise ApiError(502, "fetch_failed", "err.sample_fetch_failed") from e
        return web.FileResponse(path, headers={"Cache-Control": "private, max-age=86400", "Content-Type": "image/jpeg"})

    async def ui_read_page(self, request):
        """Read the model's civitai page (the author's notes for the whole model) and draft its card again. Without
        "refresh" a page read before is not read again. Returns the model's detail."""
        body = await self._json_body(request)
        sha = request.match_info["sha256"].lower()
        if not self.cfg.configured:
            raise ApiError(409, "not_configured")
        try:
            page = await self.library.read_page(sha, refresh=bool(body.get("refresh")))
        except CivitaiError as e:
            log.info("model page for %s could not be read: %s", sha[:12], e.code)
            raise _civitai_api_error(e) from e
        if page is None:
            raise ApiError(404, "not_found", "err.page_none")
        detail = self.library.detail(sha)
        if detail is None:
            raise ApiError(404, "not_found", "err.model_not_found")
        return web.json_response(detail)

    async def ui_read_notes(self, request):
        """Have the local model read the model's notes (queued; the card says when it is done). {"retry": true} asks
        again for notes whose reading failed."""
        body = await self._json_body(request)
        sha = request.match_info["sha256"].lower()
        if not is_sha256(sha) or self.library.card(sha) is None:
            raise ApiError(404, "not_found", "err.model_not_found")
        retry = isinstance(body, dict) and body.get("retry") is True
        return web.json_response({"requested": self.library.request_notes(sha, retry=retry)})

    async def ui_reader(self, request):
        """The local model for reading notes: what this computer has, what is recommended, what is set up, and how a
        download of the recommended one is going."""
        status = await asyncio.get_running_loop().run_in_executor(None, self.library.reader.status)
        status["models"] = {key: dict(model, key=key) for key, model in reader_mod.MODELS.items()}
        status["tiers"] = [{"vram_gb": gb, "model": key} for gb, key in reader_mod.TIERS]
        status["install"] = self.library.installer.state
        status["install_dir"] = self.cfg.reader_install_dir()
        status["install_dir_default"] = not self.cfg.data["reader"]["install_dir"]     # the llm folder in the data folder
        status["managed"] = self.cfg.data["reader"]["managed"]
        return web.json_response(status)

    async def ui_reader_install(self, request):
        """Download llama.cpp and a model into the install folder, then use them. Only for this computer's user."""
        body = await self._json_body(request)
        if not self.is_local(request):
            raise ApiError(403, "forbidden", "err.open_folder_local_only")
        key = str(body.get("model") or self.library.reader.status()["recommended"] or "")
        folder = os.path.normpath(str(body.get("folder") or self.cfg.reader_install_dir()))
        if not os.path.isabs(folder):
            raise ApiError(400, "bad_request", "err.folder_not_absolute")
        try:
            self.library.installer.start(key, folder)
        except llm_install.InstallError as e:
            if e.code == "busy":
                raise ApiError(409, "busy", "err.install_busy") from e
            raise ApiError(400, "bad_request", "err.install_no_model") from e
        return web.json_response({"install": self.library.installer.state})

    async def ui_reader_stop(self, request):
        """Stop the local model now, for the graphics card (the settings' "stop now"). The readings waiting are dropped;
        each model asks again the next time it is opened."""
        await self._json_body(request)
        await self.library.reader.stop_now()
        return web.json_response(await asyncio.get_running_loop().run_in_executor(None, self.library.reader.status))

    async def ui_reader_install_cancel(self, request):
        await self._json_body(request)
        self.library.installer.cancel()
        return web.json_response({"install": self.library.installer.state})

    async def ui_reader_probe(self, request):
        """The models a running model server offers (its /models list), to check an address before saving it."""
        body = await self._json_body(request)
        url = str(body.get("url") or "").strip().rstrip("/")
        if not re.match(r"^https?://[^\s/]+", url, re.I):
            raise ApiError(400, "bad_request", "problem.reader_url_bad")
        key = str(body.get("key") or "") or self.cfg.data["reader"]["endpoint"]["key"]
        headers = {"Authorization": "Bearer " + key} if key else {}
        try:
            async with aiohttp.ClientSession(trust_env=False) as session:
                async with session.get(url + "/models", headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=10, sock_connect=3)) as resp:
                    data = await resp.json(content_type=None) if resp.status == 200 else None
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            raise ApiError(502, "unreachable", "err.reader_unreachable") from e
        models = [m.get("id") for m in (data or {}).get("data") or [] if isinstance(m, dict) and m.get("id")]
        if data is None:
            raise ApiError(502, "unreachable", "err.reader_unreachable")
        return web.json_response({"models": models})

    async def ui_open_folder(self, request):
        """Show a model file in the computer's file manager. Only for the person sitting at this computer."""
        body = await self._json_body(request)
        if not self.is_local(request):
            raise ApiError(403, "forbidden", "err.open_folder_local_only")
        sha = str(body.get("sha256") or "").lower()
        paths = self.library.file_paths(sha)
        wanted = str(body.get("path") or "")
        path = wanted if wanted in paths else (paths[0] if paths and not wanted else None)
        if path is None:
            raise ApiError(404, "not_found", "err.model_not_found")
        try:
            _reveal(path)
        except OSError as e:
            log.warning("cannot open the folder of %s: %s", path, e)
            raise ApiError(500, "internal", "err.open_folder_failed") from e
        return web.json_response({"ok": True})

    # ---- moving a model to the recycle bin ------------------------------------------------------------------------
    _TRASH_ERRORS = {"not_this_model": (400, "err.trash_not_this_model"), "outside": (400, "err.trash_outside"),
                     "no_recycle_bin": (409, "err.trash_no_recycle_bin"), "unsupported": (409, "err.trash_unsupported"),
                     "failed": (500, "err.trash_failed")}

    async def ui_trash_plan(self, request):
        """What moving a model to the recycle bin would take: the confirmation lists it."""
        plan = self.library.trash_plan(request.match_info["sha256"].lower())
        if plan is None:
            raise ApiError(404, "not_found", "err.model_not_found")
        return web.json_response({"files": plan})

    async def ui_trash(self, request):
        """Move the chosen copies of a model, and the old WebUI's files beside them, to the recycle bin."""
        body = await self._json_body(request)
        paths = body.get("paths")
        if not isinstance(paths, list) or not paths:
            raise ApiError(400, "bad_request", "err.trash_nothing")
        try:
            moved = await self.library.trash_files(request.match_info["sha256"].lower(), [str(p) for p in paths])
        except (ValueError, TrashError) as e:
            code = e.code if isinstance(e, TrashError) else str(e)
            status, key = self._TRASH_ERRORS.get(code, (500, "err.trash_failed"))
            log.warning("could not move to the recycle bin: %s", e)
            raise ApiError(status, "bad_request" if status == 400 else "trash_" + code, key) from e
        return web.json_response({"moved": moved, "revision": self.library.db.revision()})

    # ---- what the user adds and changes in the dossier --------------------------------------------------------------
    _MAX_PICTURE = 80 * 1024 * 1024         # an upscaled PNG of a large picture runs to tens of megabytes

    def _detail_of(self, sha):
        detail = self.library.detail(sha)
        if detail is None:
            raise ApiError(404, "not_found", "err.model_not_found")
        return web.json_response(detail)

    async def ui_add_image(self, request):
        """Add a picture to a model's examples. The body is the picture file itself (PNG, JPEG or WebP), its name
        percent-encoded in X-File-Name. Returns the model's detail."""
        sha = request.match_info["sha256"].lower()
        if not (request.content_type or "").startswith("image/"):
            raise ApiError(400, "bad_request", "err.image_unreadable")
        data = bytearray()
        # read here, not with request.read(): the app's limit on a request is far below a large picture
        async for chunk in request.content.iter_chunked(1 << 16):
            data.extend(chunk)
            if len(data) > self._MAX_PICTURE:
                raise ApiError(413, "too_large", "err.image_too_large", mb=self._MAX_PICTURE // (1024 * 1024))
        name = urllib.parse.unquote(request.headers.get("X-File-Name", ""))
        try:
            await self.library.add_image(sha, bytes(data), name)
        except LookupError as e:
            raise ApiError(404, "not_found", "err.model_not_found") from e
        except ImageError as e:
            log.info("picture for %s not taken: %s", sha[:12], e)
            raise ApiError(400, "bad_request", "err.image_unreadable") from e
        return self._detail_of(sha)

    async def ui_image(self, request):
        path = self.library.image_file(request.match_info["sha256"].lower(), request.match_info["image_id"])
        if path is None:
            raise ApiError(404, "not_found", "err.sample_not_found")
        # an image id is never used for another picture
        return web.FileResponse(path, headers={"Cache-Control": "private, max-age=31536000, immutable",
                                               "Content-Type": "image/jpeg"})

    async def ui_delete_image(self, request):
        await request.read()
        sha = request.match_info["sha256"].lower()
        try:
            await self.library.delete_image(sha, request.match_info["image_id"])
        except LookupError as e:
            raise ApiError(404, "not_found", "err.sample_not_found") from e
        return self._detail_of(sha)

    async def ui_set_cover(self, request):
        """Make an example the cover: {"sample": index} (the author's), {"image": id} (one the user added), or
        {"auto": true} (chosen again as at first)."""
        body = await self._json_body(request)
        sha = request.match_info["sha256"].lower()
        sample, image, auto = body.get("sample"), body.get("image"), body.get("auto") is True
        if (sample is not None) + (image is not None) + auto != 1:
            raise ApiError(400, "bad_request", "err.cover_body")
        try:
            await self.library.set_cover(sha, sample=sample, image=image)
        except LookupError as e:
            raise ApiError(404, "not_found", "err.sample_not_found") from e
        except (CivitaiError, OSError, ValueError) as e:
            log.info("cover for %s not made: %s", sha[:12], e)
            raise ApiError(502, "fetch_failed", "err.sample_fetch_failed") from e
        return self._detail_of(sha)

    async def ui_set_info(self, request):
        """The name and the base model the user gives a model: {"title", "base_model"}, null or "" for the original."""
        body = await self._json_body(request)
        sha = request.match_info["sha256"].lower()
        try:
            self.library.set_info(sha, {k: body[k] for k in ("title", "base_model") if k in body})
        except LookupError as e:
            raise ApiError(404, "not_found", "err.model_not_found") from e
        except ValueError as e:
            err = ApiError(400, "bad_request", "err.info_bad")
            err.extra = {"field": str(e)}
            raise err from e
        return self._detail_of(sha)

    # ---- /api/ui/intake: reading pasted links ----------------------------------------------------------------------
    def _need_downloads(self):
        if self.intake is None or self.downloads is None:
            raise ApiError(404, "not_found")
        if not self.cfg.configured:
            raise ApiError(409, "not_configured")

    async def ui_intake_parse(self, request):
        body = await self._json_body(request)
        lang = self.lang(request)
        out = []
        for item in links.parse(str(body.get("text") or "")):
            if item["error"]:
                item["message"] = t(lang, "link." + item["error"], n=links.MAX_LINKS)
            out.append(item)
        return web.json_response({"links": out, "limit": links.MAX_LINKS})

    def _preview_urls(self, preview):
        for version in preview["versions"]:
            for image in version["images"]:
                image["url"] = "/api/ui/intake/versions/%d/images/%d" % (version["id"], image["index"])
        return preview

    async def ui_intake_preview(self, request):
        self._need_downloads()
        body = await self._json_body(request)
        try:
            preview = await self.intake.preview(body.get("model_id"), body.get("version_id"), body.get("file_id"))
        except CivitaiError as e:
            raise _civitai_api_error(e) from e
        if not preview["versions"]:
            raise ApiError(404, "not_found", "err.civitai_not_found")
        return web.json_response(self._preview_urls(preview))

    async def ui_intake_folders(self, request):
        self._need_downloads()
        comfy_type = kinds.canonical_type(request.query.get("comfy_type", ""))
        return web.json_response({"comfy_type": comfy_type, "folders": self.intake.folder_choices(comfy_type or None)})

    async def ui_intake_new_folder(self, request):
        self._need_downloads()
        body = await self._json_body(request)
        try:
            parent = folders.check_destination(self.cfg, body.get("parent"))
        except ValueError as e:
            raise ApiError(400, "bad_request", "err.folder_outside" if str(e) == "outside" else "err.folder_missing") from e
        name = folders.safe_folder_name(body.get("name"))
        if not name:
            raise ApiError(400, "bad_request", "err.folder_name_bad")
        path = os.path.join(parent, name)
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            raise ApiError(400, "bad_request", "err.folder_name_bad") from e
        log.info("created folder %s", path)
        return web.json_response({"path": path})

    async def ui_intake_image(self, request):
        """A small copy of an example image of a version being previewed or downloaded."""
        self._need_downloads()
        try:
            version_id = int(request.match_info["version_id"])
            index = int(request.match_info["index"])
        except ValueError as e:
            raise ApiError(404, "not_found", "err.sample_not_found") from e
        _model, version = self.intake.cached_version(version_id)
        if version is None:
            version = self.downloads.version_object(version_id)
        try:
            path = await self.intake.image(version_id, index, version=version)
        except LookupError as e:
            raise ApiError(404, "not_found", "err.sample_not_found") from e
        except (CivitaiError, OSError, ValueError) as e:
            log.info("example image %d/%d could not be fetched: %s", version_id, index, e)
            raise ApiError(502, "fetch_failed", "err.sample_fetch_failed") from e
        return web.FileResponse(path, headers={"Cache-Control": "private, max-age=86400", "Content-Type": "image/jpeg"})

    # ---- /api/ui/downloads: the queue ------------------------------------------------------------------------------
    def _download_message(self, lang, item):
        code = item["error"]
        if item["state"] == "retrying":
            wait = max(0, int((item["next_try_at"] or time.time()) - time.time()))
            reason = t(lang, "dl.err." + code) if code and ("dl.err." + code) in STRINGS else ""
            return t(lang, "dl.st_retrying", reason=reason, time="%d:%02d" % divmod(wait, 60), n=item["attempts"])
        if item["state"] != "failed" or not code:
            return None
        params = {"detail": item["error_detail"] or ""}
        if code == "disk_full":
            need, _, free = (item["error_detail"] or "0 0").partition(" ")
            params = {"need": _size(int(need or 0)), "free": _size(int(free or 0))}
        reason = t(lang, "dl.err." + code, **params) if ("dl.err." + code) in STRINGS else t(lang, "dl.err.internal")
        if code in _TRANSIENT_ERRORS:
            return t(lang, "dl.err.gave_up", reason=reason)
        return reason.strip()

    def _download_items(self, lang):
        items = self.downloads.items()
        for item in items:
            item["message"] = self._download_message(lang, item)
        return items

    async def ui_downloads(self, request):
        self._need_downloads()
        return web.json_response({"items": self._download_items(self.lang(request)),
                                  "has_token": self.library.client().has_token})

    async def ui_downloads_add(self, request):
        self._need_downloads()
        body = await self._json_body(request)
        lang = self.lang(request)
        try:
            retire = body.get("retire") if isinstance(body.get("retire"), list) else None
            result = await self.downloads.add(body.get("model_id"), body.get("version_id"), body.get("file_ids"),
                                              body.get("folder"), retire=retire)
        except AddError as e:
            if e.code == "civitai":
                raise _civitai_api_error(CivitaiError(e.params.get("code") or "network")) from e
            if e.code == "not_found":
                raise ApiError(404, "not_found", "err.version_not_found") from e
            if e.code in ("folder_outside", "folder_missing"):
                raise ApiError(400, "bad_request", "err." + e.code) from e
            raise ApiError(400, "bad_request", "err.download_bad") from e
        for skip in result["skipped"]:
            skip["message"] = t(lang, "dl.skip_in_library" if skip["reason"] == "in_library" else "dl.skip_queued",
                                name=skip["name"])
        result["items"] = self._download_items(lang)
        return web.json_response(result)

    async def ui_downloads_clear(self, request):
        self._need_downloads()
        await self._json_body(request)
        self.downloads.clear_finished()
        return web.json_response({"items": self._download_items(self.lang(request))})

    async def ui_download_action(self, request):
        self._need_downloads()
        await self._json_body(request)
        action = request.match_info["action"]
        handler = {"pause": self.downloads.pause, "resume": self.downloads.resume, "retry": self.downloads.retry,
                   "remove": self.downloads.remove}.get(action)
        try:
            download_id = int(request.match_info["id"])
            if handler is None:
                raise LookupError(action)
            handler(download_id)
        except (ValueError, LookupError) as e:
            raise ApiError(404, "not_found", "err.download_not_found") from e
        return web.json_response({"items": self._download_items(self.lang(request))})

    async def admin_shutdown(self, request):
        # Read the body and close the connection: otherwise the server waits for a client that sent
        # "Expect: 100-continue" (PowerShell does) and stopping takes ten seconds.
        await request.read()
        self.shutdown_event.set()
        response = web.json_response({"ok": True})
        response.force_close()
        return response

    # ---- the page --------------------------------------------------------------------------------------------------
    async def page(self, request):
        return web.FileResponse(os.path.join(paths_mod.WEB_DIR, "index.html"), headers={"Cache-Control": "no-cache"})

    async def static(self, request):
        root = os.path.realpath(paths_mod.WEB_DIR)
        target = os.path.realpath(os.path.join(root, request.match_info["path"]))
        try:
            inside = os.path.commonpath([root, target]) == root
        except ValueError:      # another drive
            inside = False
        if not inside or not os.path.isfile(target):
            raise web.HTTPNotFound()
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if target.endswith(".js"):
            ctype = "text/javascript"       # Windows' registry sometimes says text/plain, and modules then refuse to load
        elif target.endswith(".woff2"):
            ctype = "font/woff2"
        return web.FileResponse(target, headers={"Cache-Control": "no-cache", "Content-Type": ctype})


def _reveal(path):
    """Open the folder that holds `path` in the file manager, with the file selected where the system can do that."""
    path = os.path.normpath(path)
    if sys.platform == "win32":
        # Explorer wants /select,"<path>" as one argument; Windows paths cannot contain a double quote.
        subprocess.Popen('explorer /select,"%s"' % path)        # noqa: S603, S607 - a fixed program and a known file
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])                  # noqa: S603, S607
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])   # noqa: S603, S607


def write_server_info(paths, host, port):
    info = {"pid": os.getpid(), "host": host, "port": port, "started_at": time.time()}
    tmp = paths.server_info + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(info, f)
    os.replace(tmp, paths.server_info)


def clear_server_info(paths):
    """Remove server.json if it is this process's. (Read and close first: Windows will not delete an open file.)"""
    try:
        with open(paths.server_info, encoding="utf-8") as f:
            mine = json.load(f).get("pid") == os.getpid()
        if mine:
            os.remove(paths.server_info)
    except (OSError, ValueError):
        pass
