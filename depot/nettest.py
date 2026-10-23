"""The settings page's "test connection": can civitai be reached by the route the page shows, and if not, at which
step does it fail? Asked of each civitai site at once, with short time limits, without the API key.

The steps, in order:
  resolve  the name of the site (or, through a proxy, of the proxy) is turned into an address
  connect  a connection to that address is opened
  tls      the encrypted connection to civitai is set up (through a proxy: the proxy opens the way, then the handshake)
  answer   civitai answers a small request with data

A step that fails names a code the page turns into a sentence; the steps after it are "skipped".
"""
import asyncio
import json
import socket
import time
from urllib.parse import urlsplit

import aiohttp

from . import __version__

STEPS = ("resolve", "connect", "tls", "answer")
_STEP_SECONDS = 6
_TIMEOUT = aiohttp.ClientTimeout(total=15, sock_connect=6, sock_read=10)


def _ms(start):
    return int((time.monotonic() - start) * 1000)


class _Report:
    def __init__(self, site):
        self.site = site
        self.steps = []
        self.code = None
        self.status = None
        self.started = time.monotonic()

    def ok(self, step, start):
        self.steps.append({"step": step, "state": "ok", "ms": _ms(start)})

    def skip(self, step):
        self.steps.append({"step": step, "state": "skipped", "ms": None})

    def fail(self, step, code, start, status=None):
        self.steps.append({"step": step, "state": "failed", "ms": _ms(start)})
        self.code, self.status = code, status
        for later in STEPS[STEPS.index(step) + 1:]:
            self.skip(later)
        return self.done()

    def done(self):
        return {"site": urlsplit(self.site).netloc, "ok": self.code is None, "code": self.code, "status": self.status,
                "ms": _ms(self.started), "steps": self.steps}


async def check_site(site, proxy=None):
    """The report for one site: {"site", "ok", "code", "status", "ms", "steps": [{"step", "state", "ms"}]}."""
    report = _Report(site)
    parts = urlsplit(site)
    secure = parts.scheme == "https"
    if proxy:
        p = urlsplit(proxy)
        host, port = p.hostname or "", p.port or (443 if p.scheme == "https" else 80)
    else:
        host, port = parts.hostname or "", parts.port or (443 if secure else 80)
    loop = asyncio.get_running_loop()

    start = time.monotonic()
    try:
        await asyncio.wait_for(loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), _STEP_SECONDS)
    except (OSError, asyncio.TimeoutError):
        return report.fail("resolve", "proxy_name" if proxy else "name", start)
    report.ok("resolve", start)

    start = time.monotonic()
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), _STEP_SECONDS)
        writer.close()
    except asyncio.TimeoutError:
        return report.fail("connect", "proxy_connect_timeout" if proxy else "connect_timeout", start)
    except OSError:
        return report.fail("connect", "proxy_refused" if proxy else "connect_refused", start)
    report.ok("connect", start)

    # the encrypted connection and the answer, as the program's own requests make them
    connected = {}

    async def on_connected(_session, _ctx, _params):
        connected["at"] = time.monotonic()

    trace = aiohttp.TraceConfig()
    trace.on_connection_create_end.append(on_connected)
    start = time.monotonic()
    try:
        async with aiohttp.ClientSession(trust_env=False, timeout=_TIMEOUT, trace_configs=[trace],
                                         headers={"User-Agent": "ModelDepot/%s" % __version__}) as session:
            async with session.get(site.rstrip("/") + "/api/v1/models?limit=1", proxy=proxy,
                                   headers={"Accept": "application/json"}, allow_redirects=False) as resp:
                body = await resp.content.read(256 * 1024)
                status, ctype = resp.status, (resp.headers.get("Content-Type") or "").lower()
    except aiohttp.ClientHttpProxyError as e:          # the proxy itself said no to opening the way
        return report.fail("tls", "proxy_said_no", start, getattr(e, "status", None))
    except (aiohttp.ClientConnectorCertificateError, aiohttp.ClientSSLError):
        return report.fail("tls", "tls", start)
    except (asyncio.TimeoutError, aiohttp.ServerTimeoutError):
        if "at" not in connected:
            return report.fail("tls", "tls_timeout", start)
        return report.fail("answer", "proxy_no_answer" if proxy else "no_answer", connected["at"])
    except aiohttp.ClientError:
        if "at" not in connected:
            return report.fail("tls", "tls", start)
        return report.fail("answer", "broken", connected["at"])
    tls_end = connected.get("at", time.monotonic())
    if secure:
        report.steps.append({"step": "tls", "state": "ok", "ms": int((tls_end - start) * 1000)})
    else:
        report.skip("tls")
    answer_start = tls_end
    if status in (401, 403):
        return report.fail("answer", "refused", answer_start, status)
    if status == 429:
        return report.fail("answer", "busy", answer_start, status)
    if status >= 500:
        return report.fail("answer", "server", answer_start, status)
    data = None
    if "json" in ctype:
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            data = None
    if status != 200 or not isinstance(data, dict):
        return report.fail("answer", "not_data", answer_start, status)
    report.ok("answer", answer_start)
    return report.done()


async def check(sites, proxy=None):
    """Reports for every site, asked at the same time."""
    return list(await asyncio.gather(*(check_site(s, proxy) for s in sites)))
