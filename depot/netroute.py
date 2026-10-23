"""The proxy Windows is set to use (Settings > Network > Proxy), which browsers follow and many VPN programs set.

Python's own HTTP clients are told to ignore it everywhere in this program (trust_env off), so that the route is the
one the settings name. With the network setting "system" (the default since 2026-10-23), this module reads that proxy
at the time of each request, so that a VPN switched on or off while the program runs is followed.

A fixed address is followed as it is. An automatic configuration script (PAC, "Use setup script" in Windows) is not
run; when it is served from this computer (as VPN programs serve theirs), the first "PROXY host:port" it names is
taken, which is the VPN's own proxy. A script from elsewhere, or one naming no HTTP proxy, is reported so the page can
say so. A SOCKS-only setting cannot be used by the HTTP client either.
"""
import os
import re
import sys
import time
import urllib.request
from urllib.parse import urlsplit

_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
_TTL = 10.0                 # seconds a reading is kept: requests come in bursts, the setting changes rarely
_cache = {"at": 0.0, "info": None}
_PAC_TTL = 300.0            # a script is fetched again after this long
_pac_cache = {"at": 0.0, "url": None, "proxy": None}
_PAC_PROXY = re.compile(r"\bPROXY\s+([A-Za-z0-9.\-\[\]:]+:\d{1,5})")


def _pac_proxy(url):
    """The first HTTP proxy a PAC script served from this computer names, or None."""
    now = time.monotonic()
    if _pac_cache["url"] == url and now - _pac_cache["at"] < _PAC_TTL:
        return _pac_cache["proxy"]
    proxy = None
    parts = urlsplit(url)
    if parts.scheme in ("http", "https") and (parts.hostname or "").lower() in ("127.0.0.1", "localhost", "::1"):
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            text = opener.open(url, timeout=2).read(512 * 1024).decode("utf-8", "replace")
            found = _PAC_PROXY.search(text)
            proxy = "http://" + found.group(1) if found else None
        except (OSError, ValueError):
            proxy = None
    _pac_cache.update(at=now, url=url, proxy=proxy)
    return proxy


def _parse(server):
    """{"http": url, "https": url, "socks": url} from Windows' ProxyServer text: "host:port" for every scheme, or
    "http=host:port;https=host:port;socks=host:port"."""
    out = {}
    server = (server or "").strip()
    if not server:
        return out
    if "=" not in server and ";" not in server:
        server = "http=%s;https=%s" % (server, server)
    for part in server.split(";"):
        scheme, sep, address = part.partition("=")
        scheme, address = scheme.strip().lower(), address.strip()
        if not sep or not address:
            continue
        if "://" not in address:
            address = ("socks5://" if scheme == "socks" else "http://") + address
        out[scheme] = address
    return out


def _read_windows():
    import winreg
    values = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY) as key:
            for name in ("ProxyEnable", "ProxyServer", "AutoConfigURL"):
                try:
                    values[name] = winreg.QueryValueEx(key, name)[0]
                except OSError:
                    pass
    except OSError:
        pass
    return values


def read(force=False):
    """What the system says, kept for a few seconds:
    {"proxy": the http(s) proxy to use or None, "server": Windows' text or "", "pac": an automatic configuration
     script is set, "socks_only": only a SOCKS proxy is set}."""
    now = time.monotonic()
    if not force and _cache["info"] is not None and now - _cache["at"] < _TTL:
        return _cache["info"]
    info = {"proxy": None, "server": "", "pac": False, "socks_only": False, "from_pac": False}
    pac_url = ""
    if sys.platform == "win32":
        values = _read_windows()
        pac_url = str(values.get("AutoConfigURL") or "").strip()
        info["pac"] = bool(pac_url)
        if values.get("ProxyEnable"):
            info["server"] = str(values.get("ProxyServer") or "").strip()
    else:
        info["server"] = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or \
            os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or ""
    found = _parse(info["server"]) if sys.platform == "win32" else ({"https": info["server"]} if info["server"] else {})
    proxy = found.get("https") or found.get("http")
    if proxy and proxy.lower().startswith(("http://", "https://")):
        info["proxy"] = proxy.rstrip("/")
    elif found.get("socks") or proxy:
        info["socks_only"] = True
    if info["proxy"] is None and pac_url:
        info["proxy"] = _pac_proxy(pac_url)
        info["from_pac"] = info["proxy"] is not None
    _cache.update(at=now, info=info)
    return info
