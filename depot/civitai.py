"""Talking to civitai, politely.

- Fingerprints are looked up in batches (up to 100 per request), first on the first site in the settings
  (civitai.red: it carries everything), then, for those it did not know, on the next one (civitai.com).
- Requests are spaced out and never run in parallel.
- The API key travels only in a header, only over https, and only to civitai's own two sites. Redirects are followed
  by hand so the key is never carried along to another host. It is never logged.
- aiohttp's own reading of proxy variables is off (trust_env): the route is the one the settings name, direct, through
  a proxy, or through the proxy Windows is set to use (read at each request, netroute.py), also after a redirect.
- Someone waiting (a model's page, its example images, a pasted link) is not kept waiting by a network that hangs:
  those requests give up after a few seconds and are not tried again, and a host that failed is not asked again for a
  minute (the answer is "unreachable" at once). On a friend's computer on 2026-10-18 every click waited minutes.
  Once civitai is known to be unreachable (the library probes it at start and when a request fails), the program's
  own requests (opening a model, example images, background work) are answered "unreachable" at once, with no further
  attempts of its own: only what the user asks for by hand (Retry, a pasted link, a connection test) is tried (force),
  and the next start or a change of the settings probes again (the user's rule, 2026-10-23: "once it is confirmed
  unreachable, wait for a manual try or the next start").

Downloads (measured 2026-10-02): <site>/api/download/models/<version>?fileId=<file> answers a request without the
key, for a creator who requires a login, with 401 and a JSON message; with the key, with a 307 to a signed address
on civitai's storage (b2.civitai.com, valid an hour, or a Cloudflare R2 host, valid a day), which serves byte ranges.
Some older answers send the visitor to the login page instead (a redirect to /login): that is a login too.
"""
import asyncio
import contextlib
import json
import logging
import re
import time
from urllib.parse import urljoin, urlsplit

import aiohttp

from . import __version__, version_info

log = logging.getLogger("depot.civitai")

CIVITAI_HOSTS = ("civitai.com", "civitai.red")
BATCH_SIZE = 100
MAX_JSON_BYTES = 128 * 1024 * 1024
MAX_IMAGE_BYTES = 32 * 1024 * 1024
_RETRY_DELAYS = (3.0, 10.0)
_MAX_REDIRECTS = 5
# A download may take hours: no limit on the whole, but a stalled connection is given up after a minute.
_STREAM_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=20, sock_read=60)
# Someone is waiting: a model page is about 200 KB and civitai answers within a second or two when it can be reached.
_QUICK_TIMEOUT = aiohttp.ClientTimeout(total=12, sock_connect=5, sock_read=6)
# After a host could not be reached, requests someone waits for are answered "unreachable" at once for this long;
# when two hosts are down together, the route itself is (a proxy that died, a VPN switched off), and all of them are.
COOLDOWN = 60.0
_CIVITAI_DOMAINS = ("civitai.com", "civitai.red", "civitai.green")


class CivitaiError(Exception):
    """code: network | rate_limited | server | auth | not_json | too_large | bad_request | not_found

    and for downloads: login_required (the creator requires a login and no key is set) | token_rejected (a key was
    sent and refused) | key_not_sent (a key is set, but the address is not one it may go to, so it stayed home) |
    forbidden (early access, paid, or not allowed) | link_expired (the storage address ran out: ask civitai for a new
    one) | not_a_file (a web page came back instead of the file)
    """

    def __init__(self, code, detail="", status=None, keyed=False):
        super().__init__("%s %s" % (code, detail))
        self.code = code
        self.detail = detail
        self.status = status
        self.keyed = keyed          # "auth": the API key went along (else civitai, or a gateway, refused the visit)


class Throttle:
    """Lets one caller through every `interval` seconds."""

    def __init__(self, interval):
        self.interval = max(0.0, float(interval))
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self):
        async with self._lock:
            delay = self._last + self.interval - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()


def _host(url):
    return (urlsplit(url).hostname or "").lower()


def _netloc(url):
    return (urlsplit(url).netloc or "").lower()


def _is_civitai_host(url):
    host = _host(url)
    return any(host == d or host.endswith("." + d) for d in _CIVITAI_DOMAINS)


def _message(body):
    """The sentence an error answer carries: civitai's JSON "message" or "error", or the start of the text."""
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        data = None
    if isinstance(data, dict):
        for key in ("message", "error"):
            if isinstance(data.get(key), str) and data[key].strip():
                return data[key].strip()[:300]
    text = re.sub(r"<[^>]+>", " ", body.decode("utf-8", "replace"))
    return " ".join(text.split())[:200]


class CivitaiClient:
    def __init__(self, sites, token="", proxy=None, request_interval=2.0, image_interval=1.0, token_hosts=None,
                 retry_delays=_RETRY_DELAYS, sample_interval=0.25, preview_interval=1.0, on_down=None, on_up=None):
        self.sites = [s.rstrip("/") for s in sites]
        self._token = token or ""
        self._proxy_source = proxy      # an address, or a function giving the one to use now (the system's)
        self._api_throttle = Throttle(request_interval)
        self._image_throttle = Throttle(image_interval)
        # Example images are fetched while someone looks at the page, as civitai's own page would load them: closer
        # together than the covers made in the background, and with one quick retry instead of the long ones.
        self._sample_throttle = Throttle(sample_interval)
        # Reading a pasted link is the same: someone is waiting for the preview.
        self._preview_throttle = Throttle(preview_interval)
        self._retry_delays = tuple(retry_delays)
        # Tests point the client at a stand-in server and name its host (or host:port) here. Nothing in the settings
        # can.
        self._extra_token_hosts = frozenset(h.lower() for h in (token_hosts or ()))
        self._site_netlocs = frozenset(urlsplit(s).netloc.lower() for s in self.sites)
        self._session = None
        self.requests_made = 0
        self._down = {}                 # host[:port] -> time.monotonic() until which it counts as unreachable
        self._preferred = None          # the site that answered last: asked first for a page or a link
        self._last_ok = None            # time.time() of the last answer from civitai
        self._last_seen = None          # time.time() of the last answer or failure
        self._probe_throttle = Throttle(0)
        self._on_down = on_down         # told when a host stops answering (the library then probes)
        self._on_up = on_up             # told when civitai answers while it counted as unreachable
        self.offline = False            # known unreachable: nothing is asked unless forced
        self._recovering = False        # was offline; the user asked to try again

    @property
    def proxy(self):
        source = self._proxy_source
        return (source() if callable(source) else source) or None

    # ---- who can be reached ----------------------------------------------------------------------------------------
    def _cooling(self, host):
        if self.offline:
            return True
        now = time.monotonic()
        down = [h for h, until in self._down.items() if until > now]
        return host in down or len(down) >= 2

    def _note_down(self, host):
        self._down[host] = time.monotonic() + COOLDOWN
        self._last_seen = time.time()
        if self._on_down is not None and not self.offline:
            self._on_down()

    def _note_up(self, url):
        where = _netloc(url)
        self._down.pop(where, None)
        self._last_ok = self._last_seen = time.time()
        if self.offline or self._recovering:
            self.offline = self._recovering = False
            self._down.clear()
            if self._on_up is not None:
                self._on_up()
        for site in self.sites:
            if _netloc(site) == where:
                self._preferred = site

    def reachability(self):
        """{"state": "ok" | "down" | "unknown", "checked_at": time.time() of the last answer or failure}: down when it is
        known unreachable, or every site failed within the last minute."""
        now = time.monotonic()
        down = self.offline or all(self._down.get(_netloc(s), 0) > now for s in self.sites)
        state = "down" if down else ("ok" if self._last_ok else "unknown")
        return {"state": state, "checked_at": self._last_seen}

    def clear_down(self):
        """Forget the failures (the route works again, as a connection test just showed)."""
        self._down.clear()
        if self.offline:
            self._recovering = True
        self.offline = False

    async def probe(self):
        """Ask each site for one small answer (the model list, one entry), at once, whatever the cooldown says. True when
        any site answered; a site that failed counts as down for another minute."""
        async def one(site):
            try:
                status, _ctype, _payload = await self._call("GET", site + "/api/v1/models?limit=1",
                                                            throttle=self._probe_throttle, retry_delays=(), quick=True,
                                                            force=True)
            except CivitaiError as e:
                return e.code in ("auth", "bad_request", "rate_limited")    # it answered: the route works
            return status < 500
        results = await asyncio.gather(*(one(site) for site in self.sites))
        return any(results)

    def unreachable(self):
        """The hosts that failed within the last minute, with the seconds left before they are asked again."""
        now = time.monotonic()
        return {h: round(until - now) for h, until in self._down.items() if until > now}

    def _is_api(self, url):
        """Is this civitai itself (one of its domains, or a site from the settings), rather than its storage?"""
        return _is_civitai_host(url) or urlsplit(url).netloc.lower() in self._site_netlocs

    @property
    def has_token(self):
        return bool(self._token)

    def __repr__(self):
        return "<CivitaiClient sites=%r token=%s>" % (self.sites, "set" if self._token else "none")

    # ---- plumbing --------------------------------------------------------------------------------------------------
    def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                trust_env=False,
                headers={"User-Agent": "ModelDepot/%s" % __version__},
                timeout=aiohttp.ClientTimeout(total=120, connect=20))
        return self._session

    async def close(self):
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    def _may_carry_token(self, url):
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if host in self._extra_token_hosts or (parts.netloc or "").lower() in self._extra_token_hosts:
            return True
        return parts.scheme == "https" and host in CIVITAI_HOSTS

    def _login_error(self, carried):
        """The code for a download that asked for a login: the key that went along was refused, there is no key, or
        there is one but this address may not have it."""
        if carried:
            return "token_rejected"
        return "key_not_sent" if self._token else "login_required"

    async def _fetch(self, method, url, body=None, auth=True, limit=MAX_JSON_BYTES, timeout=None):
        """One request, redirects followed by hand. Returns (status, content type, bytes, final url, key sent)."""
        data = json.dumps(body).encode("utf-8") if body is not None else None
        proxy = self.proxy
        for _ in range(_MAX_REDIRECTS + 1):
            headers = {"Accept": "application/json" if auth else "image/*"}
            if data is not None:
                headers["Content-Type"] = "application/json"
            carried = bool(auth and self._token and self._may_carry_token(url))
            if carried:
                headers["Authorization"] = "Bearer " + self._token
            self.requests_made += 1
            extra = {"timeout": timeout} if timeout is not None else {}
            async with self._get_session().request(method, url, data=data, headers=headers, proxy=proxy,
                                                   allow_redirects=False, **extra) as resp:
                if resp.status in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
                    target = urljoin(url, resp.headers["Location"])
                    if resp.status == 303 or (resp.status in (301, 302) and method != "GET"):
                        method, data = "GET", None
                    log.info("civitai %s -> %s redirect to %s", _host(url), resp.status, _host(target))
                    url = target
                    continue
                chunks, size = [], 0
                async for chunk in resp.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > limit:
                        raise CivitaiError("too_large", _host(url), resp.status)
                    chunks.append(chunk)
                return resp.status, resp.headers.get("Content-Type", ""), b"".join(chunks), url, carried
        raise CivitaiError("network", "too many redirects")

    async def _call(self, method, url, body=None, auth=True, limit=MAX_JSON_BYTES, throttle=None, retry_delays=None,
                    quick=False, force=False):
        """_fetch with the waiting and the retries: spaced out, tried again after a hiccup, never hammering.

        A host that failed within the last minute is not asked (CivitaiError "network" at once) unless `force` (the user
        asked by hand). quick: someone is waiting; the request gives up after a few seconds, and a network failure is
        not tried again."""
        throttle = throttle or self._api_throttle
        delays = self._retry_delays if retry_delays is None else tuple(retry_delays)
        attempts = len(delays) + 1
        host, where = _host(url), _netloc(url)
        if not force and self._cooling(where):
            raise CivitaiError("network", "%s did not answer a moment ago" % host)
        last = None
        for attempt in range(attempts):
            await throttle.wait()
            try:
                status, ctype, payload, final, carried = await self._fetch(
                    method, url, body, auth, limit, timeout=_QUICK_TIMEOUT if quick else None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last = CivitaiError("network", type(e).__name__)
                self._note_down(where)
                log.warning("civitai %s %s: %s", method, host, type(e).__name__)
                if quick:
                    break
            else:
                if status < 400 or status == 404:
                    self._note_up(url)
                path = urlsplit(final).path
                log.info("civitai %s %s%s -> %s (%d bytes)", method, _host(final), path, status, len(payload))
                if status == 429:
                    last = CivitaiError("rate_limited", _host(final), status)
                elif status >= 500:
                    last = CivitaiError("server", _host(final), status)
                elif status in (401, 403):
                    # civitai refuses a key with JSON; a page in HTML is a gateway's (or a proxy's) block page
                    page = "html" in ctype.lower() or payload.lstrip()[:1] == b"<"
                    raise CivitaiError("auth", _host(final), status, keyed=carried and not page)
                elif status == 400:
                    raise CivitaiError("bad_request", _host(final), status)
                else:
                    return status, ctype, payload
            if attempt + 1 < attempts:
                await asyncio.sleep(delays[attempt])
        raise last

    # ---- what the program asks -------------------------------------------------------------------------------------
    async def versions_by_hash(self, sha256s):
        """Which civitai versions list these files.

        Returns (found, complete). found maps each lower-case fingerprint civitai knows to (site, [version objects]):
        every version on that site that lists a file with the fingerprint. A LoRA is usually listed by one version;
        a common VAE or text encoder by hundreds, because uploaders attach it to their checkpoints
        (version_info.pick_version chooses among them). complete is False when a site could not be asked: then
        "not in found" does not mean "not on civitai".
        """
        remaining = list(dict.fromkeys(h.lower() for h in sha256s))
        found, complete = {}, True
        for site in self.sites:
            if not remaining:
                break
            for start in range(0, len(remaining), BATCH_SIZE):
                chunk = remaining[start:start + BATCH_SIZE]
                try:
                    status, ctype, payload = await self._call(
                        "POST", site + "/api/v1/model-versions/by-hash", [h.upper() for h in chunk])
                except CivitaiError as e:
                    if e.code in ("auth", "bad_request"):
                        raise
                    complete = False
                    continue
                if status != 200:
                    complete = False
                    continue
                try:
                    versions = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    versions = None
                if not isinstance(versions, list):
                    # A login page or an error page instead of data.
                    complete = False
                    log.warning("civitai %s answered with something that is not a list (%s)", _host(site), ctype[:40])
                    continue
                wanted = set(chunk)
                for version in versions:
                    if version_info.version_fields(version) is None:
                        continue
                    for h in set(version_info.version_hashes(version)):
                        if h in wanted:
                            found.setdefault(h, (site, []))[1].append(version)
            remaining = [h for h in remaining if h not in found]
        return found, complete

    async def fetch_image(self, url, interactive=False, force=False):
        """The bytes of an image: a cover made in the background, or (interactive) an example image someone is
        waiting for. The API key is never sent with image requests."""
        if interactive:
            status, ctype, payload = await self._call("GET", url, auth=False, limit=MAX_IMAGE_BYTES,
                                                      throttle=self._sample_throttle, retry_delays=(1.0,), quick=True,
                                                      force=force)
        else:
            status, ctype, payload = await self._call("GET", url, auth=False, limit=MAX_IMAGE_BYTES,
                                                      throttle=self._image_throttle)
        if status == 404:
            raise CivitaiError("not_found", _host(url), status)
        if status != 200 or not payload:
            raise CivitaiError("server", _host(url), status)
        if ctype and not ctype.lower().startswith(("image/", "application/octet-stream", "binary/")):
            raise CivitaiError("not_json", "not an image: " + ctype[:40], status)
        return payload

    # ---- reading a pasted link -------------------------------------------------------------------------------------
    async def _get_object(self, path, force=False):
        """(site, JSON object) from the first site in the settings that knows `path`; a site that cannot be reached
        is passed over for the next. Someone is waiting, so the requests are spaced as for a page and tried again
        once, soon."""
        soon = tuple(min(1.0, d) for d in self._retry_delays[:1])
        trouble = missing = None
        sites = sorted(self.sites, key=lambda s: s != self._preferred)      # the one that answered last, first
        for site in sites:
            try:
                status, ctype, payload = await self._call("GET", site + path, throttle=self._preview_throttle,
                                                          retry_delays=soon, quick=True, force=force)
            except CivitaiError as e:
                if e.code == "bad_request":
                    raise
                trouble = e if e.code == "auth" or trouble is None else trouble     # a refusal says most
                continue
            if status == 404:
                missing = CivitaiError("not_found", path, status)
                continue
            if status != 200:
                trouble = trouble or CivitaiError("server", _host(site), status)
                continue
            try:
                data = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                data = None
            if not isinstance(data, dict):
                trouble = trouble or CivitaiError("not_json", "%s (%s)" % (_host(site), ctype[:40]), status)
                continue
            return site, data
        raise trouble or missing or CivitaiError("not_found", path)

    async def get_model(self, model_id, force=False):
        """(site, model object with every version, its files and its example images)."""
        return await self._get_object("/api/v1/models/%d" % int(model_id), force)

    async def get_version(self, version_id, force=False):
        """(site, model-version object): used to find the model a version link belongs to."""
        return await self._get_object("/api/v1/model-versions/%d" % int(version_id), force)

    # ---- downloading -----------------------------------------------------------------------------------------------
    @contextlib.asynccontextmanager
    async def open_download(self, url, start=0):
        """The file behind a civitai download address, as an open response (200, 206 for a range, or 416 when
        `start` is at or past the end). Redirects are followed by hand: the key goes to civitai's own sites only,
        never to its storage. Raises CivitaiError (see the class) when no file comes back."""
        session = self._get_session()
        throttled = False
        for _ in range(_MAX_REDIRECTS + 1):
            if not throttled and self._is_api(url):
                await self._api_throttle.wait()       # civitai's own API; its storage is not throttled
                throttled = True
            headers = {"Accept": "*/*"}
            if start > 0:
                headers["Range"] = "bytes=%d-" % start
            carried = bool(self._token) and self._may_carry_token(url)
            if carried:
                headers["Authorization"] = "Bearer " + self._token
            self.requests_made += 1
            try:
                resp = await session.get(url, headers=headers, proxy=self.proxy, allow_redirects=False,
                                         timeout=_STREAM_TIMEOUT)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                raise CivitaiError("network", type(e).__name__) from e
            status = resp.status
            if status in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
                target = urljoin(url, resp.headers["Location"])
                resp.release()
                log.info("download %s%s -> %s redirect to %s", _host(url), urlsplit(url).path, status, _host(target))
                if self._is_api(target) and "/login" in urlsplit(target).path.lower():
                    raise CivitaiError(self._login_error(carried), _host(url), status)
                url = target
                continue
            try:
                if status in (200, 206, 416):
                    ctype = (resp.headers.get("Content-Type") or "").lower()
                    if status != 416 and ctype.startswith(("text/html", "application/json")):
                        body = await resp.content.read(8192)
                        asks_login = b"log in" in body.lower() or b"login" in body.lower()
                        code = self._login_error(carried) if asks_login else "not_a_file"
                        raise CivitaiError(code, _message(body), status)
                    log.info("download %s%s -> %s (%s bytes)", _host(url), urlsplit(url).path, status,
                             resp.headers.get("Content-Length", "?"))
                    yield resp
                    return
                body = await resp.content.read(8192)
                message = _message(body)
                log.info("download %s%s -> %s: %s", _host(url), urlsplit(url).path, status, message[:120])
                if status in (401, 403) and not self._is_api(url):
                    raise CivitaiError("link_expired", _host(url), status)
                if status == 401:
                    raise CivitaiError(self._login_error(carried), message, status)
                if status == 403:
                    raise CivitaiError("forbidden", message, status)
                if status == 404:
                    raise CivitaiError("not_found", message, status)
                if status == 429:
                    raise CivitaiError("rate_limited", _host(url), status)
                if status >= 500:
                    raise CivitaiError("server", _host(url), status)
                raise CivitaiError("bad_request", message, status)
            finally:
                resp.release()
        raise CivitaiError("network", "too many redirects")
