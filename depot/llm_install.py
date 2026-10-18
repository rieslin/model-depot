"""Downloading and setting up a local model for reading the authors' notes, for a computer that has none (the user's
request, 2026-10-02): llama.cpp's server for Windows from its releases on GitHub, and the recommended GGUF file from
Hugging Face, into one folder; each file checked against the fingerprint its site publishes. A download that stops
(cancelled, the program closed, the connection lost) carries on from where it stopped the next time.

    <folder>/llama.cpp/llama-server.exe      and the rest of the release, with NVIDIA's runtime beside it
    <folder>/models/<file>.gguf

When both are in place the settings are switched to them (reader.mode "managed").
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import time
import zipfile

import aiohttp

from . import reader

log = logging.getLogger("depot.llm_install")

GITHUB_RELEASES = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10"
HF_BASE = "https://huggingface.co"
CHUNK = 1 << 20
SPACE_MARGIN = 1 << 30          # left free on the disk after everything is in place


class InstallError(Exception):
    """code: busy | unsupported | no_release | not_found | network | disk_full | checksum | cancelled | settings"""

    def __init__(self, code, detail=""):
        super().__init__("%s %s" % (code, detail))
        self.code = code
        self.detail = detail


def cuda_flavour(cuda_version):
    """The llama.cpp build for the driver: CUDA 13 needs a driver that reports 13 or more."""
    try:
        major = int(str(cuda_version or "").split(".")[0])
    except ValueError:
        major = 0
    return "13.4" if major >= 13 else "12.4"


def pick_release(releases, flavour):
    """(tag, server asset, runtime asset) from the newest release that has both Windows CUDA archives."""
    for release in releases if isinstance(releases, list) else []:
        assets = {a.get("name"): a for a in release.get("assets") or [] if isinstance(a, dict)}
        tag = release.get("tag_name") or ""
        server = assets.get("llama-%s-bin-win-cuda-%s-x64.zip" % (tag, flavour))
        runtime = assets.get("cudart-llama-bin-win-cuda-%s-x64.zip" % flavour)
        if server and runtime:
            return tag, server, runtime
    return None


def _digest(asset):
    """GitHub gives assets a "digest" ("sha256:<hex>"); older ones have none."""
    m = re.match(r"sha256:([0-9a-f]{64})$", str(asset.get("digest") or ""))
    return m.group(1) if m else None


class Installer:
    def __init__(self, cfg, on_done, github=GITHUB_RELEASES, hf=HF_BASE):
        self.cfg = cfg
        self.on_done = on_done          # called once the settings point at what was installed
        self.github = github
        self.hf = hf.rstrip("/")
        self.state = {"state": "idle"}
        self.proxy = None
        self._task = None
        self._cancel = False

    @property
    def busy(self):
        return self._task is not None and not self._task.done()

    def start(self, model_key, folder):
        if self.busy:
            raise InstallError("busy")
        if model_key not in reader.MODELS:
            raise InstallError("not_found", model_key)
        self._cancel = False
        self.proxy = self.cfg.proxy()
        self.state = {"state": "starting", "model": model_key, "folder": folder, "step": None, "bytes_done": 0,
                      "bytes_total": None, "speed": 0, "error": None, "detail": None, "started_at": time.time()}
        self._task = asyncio.ensure_future(self._run(model_key, folder))

    def cancel(self):
        self._cancel = True

    async def stop(self):
        self._cancel = True
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    # ---- the steps ---------------------------------------------------------------------------------------------------
    async def _run(self, model_key, folder):
        try:
            async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=None, sock_connect=30,
                                                                                            sock_read=120),
                                             headers={"User-Agent": "ModelDepot"}) as session:
                server = await self._runtime(session, folder)
                model_file = await self._model(session, folder, reader.MODELS[model_key])
            self.state.update(state="configuring", step=None)
            problems = self.cfg.update({"reader": {"mode": "managed",
                                                   "managed": {"server": server, "model_file": model_file}}})
            if problems:
                raise InstallError("settings", ", ".join(code for _f, code in problems))
            self.cfg.save()
            self.on_done()
            self.state.update(state="done", finished_at=time.time())
            log.info("local model installed: %s, %s", server, model_file)
        except InstallError as e:
            self.state.update(state="cancelled" if e.code == "cancelled" else "failed", error=e.code, detail=e.detail)
            log.info("local model install stopped: %s %s", e.code, e.detail)
        except asyncio.CancelledError:
            self.state.update(state="cancelled", error="cancelled")
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            self.state.update(state="failed", error="network", detail=type(e).__name__)
        except OSError as e:
            self.state.update(state="failed", error="disk", detail=str(e))

    async def _runtime(self, session, folder):
        """llama.cpp's server, downloaded once. Returns the path of llama-server.exe."""
        target = os.path.join(folder, "llama.cpp")
        server = os.path.join(target, "llama-server.exe")
        if os.path.isfile(server):
            return server
        if sys.platform != "win32":
            raise InstallError("unsupported", "llama.cpp is downloaded for Windows only; set its path by hand")
        self.state.update(state="downloading", step="runtime")
        flavour = cuda_flavour(reader.detect_hardware().get("cuda"))
        async with session.get(self.github, headers={"Accept": "application/vnd.github+json"}, proxy=self.proxy) as resp:
            if resp.status != 200:
                raise InstallError("no_release", "GitHub answered %s" % resp.status)
            releases = json.loads(await resp.text())
        picked = pick_release(releases, flavour)
        if picked is None:
            raise InstallError("no_release", "no Windows CUDA %s build in the newest releases" % flavour)
        tag, server_asset, runtime_asset = picked
        os.makedirs(target, exist_ok=True)
        for asset in (server_asset, runtime_asset):
            archive = os.path.join(folder, "downloads", asset["name"])
            await self._download(session, asset["browser_download_url"], archive, asset.get("size"), _digest(asset))
            self.state.update(state="extracting")
            await asyncio.get_running_loop().run_in_executor(None, _extract, archive, target)
            self.state.update(state="downloading")
        if not os.path.isfile(server):
            raise InstallError("no_release", "llama-server.exe is not in %s" % tag)
        with open(os.path.join(target, "release.txt"), "w", encoding="utf-8") as f:
            f.write("%s cuda-%s\n" % (tag, flavour))
        shutil.rmtree(os.path.join(folder, "downloads"), ignore_errors=True)
        return server

    async def _model(self, session, folder, model):
        """The GGUF file, downloaded once and checked. Returns its path."""
        target = os.path.join(folder, "models", model["file"])
        if os.path.isfile(target):
            return target
        self.state.update(state="downloading", step="model")
        info_url = "%s/api/models/%s/tree/main" % (self.hf, model["repo"])
        async with session.get(info_url, proxy=self.proxy) as resp:
            if resp.status != 200:
                raise InstallError("not_found", "Hugging Face answered %s for %s" % (resp.status, model["repo"]))
            listing = json.loads(await resp.text())
        entry = next((e for e in listing if isinstance(e, dict) and e.get("path") == model["file"]), None)
        if entry is None:
            raise InstallError("not_found", model["file"])
        lfs = entry.get("lfs") if isinstance(entry.get("lfs"), dict) else {}
        size = lfs.get("size") or entry.get("size")
        sha = lfs.get("oid") if re.match(r"^[0-9a-f]{64}$", str(lfs.get("oid") or "")) else None
        url = "%s/%s/resolve/main/%s" % (self.hf, model["repo"], model["file"])
        await self._download(session, url, target, size, sha)
        return target

    async def _download(self, session, url, path, size, sha256):
        """One file into `path`, through path + ".part", resumed from the part already there; checked against the
        fingerprint when there is one. Raises InstallError."""
        part = path + ".part"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if size:
            free = shutil.disk_usage(os.path.dirname(path)).free
            if free < size - have + SPACE_MARGIN:
                raise InstallError("disk_full", "%d needed, %d free" % (size - have + SPACE_MARGIN, free))
        self.state.update(file=os.path.basename(path), bytes_done=have, bytes_total=size, speed=0)
        headers = {"Range": "bytes=%d-" % have} if have else {}
        async with session.get(url, headers=headers, proxy=self.proxy) as resp:
            if resp.status == 416 and size and have >= size:
                pass                            # everything is there already
            elif resp.status not in (200, 206):
                raise InstallError("network", "%s answered %s" % (url.split("/")[2], resp.status))
            else:
                if resp.status == 200 and have:
                    have = 0                    # the server sends the whole file: start over
                mode = "ab" if have else "wb"
                total = size or (have + int(resp.headers.get("Content-Length") or 0)) or None
                self.state.update(bytes_done=have, bytes_total=total)
                started, moved = time.monotonic(), 0
                with open(part, mode) as f:
                    async for chunk in resp.content.iter_chunked(CHUNK):
                        if self._cancel:
                            raise InstallError("cancelled")
                        f.write(chunk)
                        have += len(chunk)
                        moved += len(chunk)
                        elapsed = time.monotonic() - started
                        self.state.update(bytes_done=have, speed=int(moved / elapsed) if elapsed > 0.5 else 0)
        if size and os.path.getsize(part) != size:
            raise InstallError("network", "%s: %d of %d bytes" % (os.path.basename(path), os.path.getsize(part), size))
        if sha256:
            self.state.update(state="verifying")
            digest = await asyncio.get_running_loop().run_in_executor(None, _sha256, part)
            if digest != sha256:
                os.remove(part)
                raise InstallError("checksum", "%s: %s, expected %s" % (os.path.basename(path), digest[:12], sha256[:12]))
            self.state.update(state="downloading")
        os.replace(part, path)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _extract(archive, target):
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            name = member.filename.replace("\\", "/")
            if name.endswith("/") or name.startswith("/") or ".." in name.split("/"):
                continue
            # the archives keep their files at the top, or in one folder: either way they land in `target`
            dest = os.path.join(target, os.path.basename(name)) if "/" in name.strip("/") else os.path.join(target, name)
            with z.open(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
