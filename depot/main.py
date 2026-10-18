"""Starting and stopping the server."""
import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import sys
import time
import urllib.error
import urllib.request

from . import APP_NAME, __version__
from .config import Config
from .paths import DataPaths, data_dir, install_info

log = logging.getLogger("depot")

EXIT_OK, EXIT_ERROR, EXIT_PORT_BUSY, EXIT_MISSING_PACKAGES = 0, 1, 3, 4


def setup_logging(paths, verbose=False):
    paths.ensure()
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    to_file = logging.handlers.RotatingFileHandler(os.path.join(paths.logs, "depot.log"), maxBytes=2 * 1024 * 1024,
                                                   backupCount=3, encoding="utf-8")
    to_file.setFormatter(fmt)
    root.addHandler(to_file)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


async def serve(paths, cfg, host, port, client_factory=None, ready=None):
    """Run the server until it is asked to stop. `ready`, if given, is called with the Server once it listens."""
    from aiohttp import web     # noqa: PLC0415 - so that --check can report a missing package instead of crashing

    from .db import Index       # noqa: PLC0415
    from .downloads import Downloads    # noqa: PLC0415
    from .folders import Remembered     # noqa: PLC0415
    from .inbox import Inbox    # noqa: PLC0415
    from .intake import Intake  # noqa: PLC0415
    from .library import Library    # noqa: PLC0415
    from .server import Server, clear_server_info, write_server_info    # noqa: PLC0415

    index = Index(paths.index)
    library = Library(cfg, index, client_factory)
    intake = Intake(cfg, library, Remembered(paths.download_folders))
    downloads = Downloads(cfg, index, library, intake)
    inbox = Inbox(cfg, library)
    server = Server(paths, cfg, library, intake=intake, downloads=downloads, inbox=inbox)
    runner = web.AppRunner(server.build_app(), access_log=None)
    await runner.setup()
    try:
        site = web.TCPSite(runner, host, port)
        await site.start()
        server.bound = (host, port)
        write_server_info(paths, host, port)
        await library.start()
        await downloads.start()
        log.info("%s %s listening on http://%s:%s/ (data: %s)", APP_NAME, __version__, host, port, paths.root)
        if ready is not None:
            ready(server)
        await server.shutdown_event.wait()
        log.info("stopping")
    finally:
        server.close_events()
        await inbox.close()
        await downloads.stop()
        await library.stop()
        await runner.cleanup()
        index.close()
        clear_server_info(paths)


def _local_url(host, port):
    if host in ("0.0.0.0", "", "::"):
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = "[%s]" % host
    return "http://%s:%s" % (host, port)


def _direct_opener():
    """Windows hands Python the system proxy; calls to this computer must not go through it."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def running_server(paths):
    """(url, pid) of the server that wrote server.json, if it still answers; otherwise (None, None)."""
    try:
        with open(paths.server_info, encoding="utf-8") as f:
            info = json.load(f)
        url = _local_url(info["host"], info["port"])
    except (OSError, ValueError, KeyError):
        return None, None
    try:
        with _direct_opener().open(url + "/api/v1/status", timeout=2) as resp:
            if json.loads(resp.read().decode("utf-8")).get("app") == APP_NAME:
                return url, info.get("pid")
    except (OSError, ValueError):
        pass
    return None, None


def stop(paths):
    """Ask the running server to stop and wait until it has. Returns True when one was running."""
    url, _pid = running_server(paths)
    if not url:
        return False
    request = urllib.request.Request(url + "/api/admin/shutdown", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        _direct_opener().open(request, timeout=5).close()
    except OSError:
        pass
    for _ in range(60):
        time.sleep(0.5)
        if running_server(paths)[0] is None:
            break
    return True


def check():
    """Say whether this Python can run the program. Used by the start script when it picks a Python."""
    problems = []
    if sys.version_info < (3, 10):
        problems.append("Python 3.10 or newer is needed; this is %s" % sys.version.split()[0])
    for module, package in (("aiohttp", "aiohttp"), ("PIL", "Pillow")):
        try:
            __import__(module)
        except ImportError:
            problems.append("missing package: %s" % package)
    for line in problems:
        print(line)
    if not problems:
        print("ok: Python %s" % sys.version.split()[0])
    return EXIT_OK if not problems else EXIT_MISSING_PACKAGES


def main(argv=None):
    parser = argparse.ArgumentParser(prog="run.py", description="%s %s" % (APP_NAME, __version__))
    parser.add_argument("--data", help="data folder (default: MODEL_DEPOT_DATA, or data/ in the project)")
    parser.add_argument("--host", help="listen address, overriding the settings")
    parser.add_argument("--port", type=int, help="listen port, overriding the settings")
    parser.add_argument("--stop", action="store_true", help="stop the running server and exit")
    parser.add_argument("--check", action="store_true", help="check that this Python can run the program and exit")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    if args.check:
        return check()
    paths = DataPaths(data_dir(args.data))
    if args.stop:
        print("stopped" if stop(paths) else "not running")
        return EXIT_OK

    code = check()
    if code != EXIT_OK:
        return code
    setup_logging(paths, args.verbose)
    cfg = Config.load(paths)
    if cfg.load_problem:
        log.warning("settings file could not be read (%s); starting with the setup wizard", cfg.load_problem)
    elif not cfg.exists:
        from . import install       # noqa: PLC0415 - after check(): it needs the packages
        install.seed(cfg, install_info())       # a copy the Windows setup installed starts from what it chose
    host = args.host or cfg.host
    port = args.port or cfg.port
    try:
        asyncio.run(serve(paths, cfg, host, port))
    except KeyboardInterrupt:
        pass
    except OSError as e:
        if getattr(e, "errno", None) in (98, 10048, 48) or "address already in use" in str(e).lower() \
                or getattr(e, "winerror", None) == 10048:
            log.error("port %s is already in use: is %s already running?", port, APP_NAME)
            return EXIT_PORT_BUSY
        raise
    return EXIT_OK
