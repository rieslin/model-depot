"""Settings. One JSON file, config.json, in the data folder; nothing about a particular computer lives in the code.

The file is written by the setup wizard and the settings page. Secrets in it (the civitai key when the user typed
it in, the remote-access key) never leave through the API: public() strips them.
"""
import copy
import json
import os
import re
import uuid

SCHEMA = 1
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8194
CIVITAI_SITES = ("https://civitai.red", "https://civitai.com")   # asked in this order: .red carries everything
LANGS = ("zh", "en")
TOKEN_SOURCES = ("none", "value", "env_file")
NETWORK_MODES = ("direct", "proxy")
READER_MODES = ("rules", "managed", "endpoint")

_TYPE_RE = re.compile(r'^[^\\/:*?"<>|\x00-\x1f]{1,64}$')   # a ComfyUI type name, or a folder's own name
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_URL_RE = re.compile(r"^https?://[^\s/]+", re.I)


def defaults():
    return {
        "schema": SCHEMA,
        "setup_done": False,
        "language": "",
        "server": {"host": DEFAULT_HOST, "port": DEFAULT_PORT},
        "remote_access": {"enabled": False, "key": ""},
        # What the wizard was told about ComfyUI, kept so "read the folders again" needs no retyping.
        "comfyui": {"dir": "", "models_dir": ""},
        # One entry per (folder, ComfyUI model type). A folder registered under two types appears twice, as in
        # ComfyUI itself. Order matters within a type: it is ComfyUI's search order.
        "folders": [],
        "civitai": {
            "token_source": "none", "token": "", "env_file": "", "env_var": "CIVITAI_TOKEN",
            "sites": list(CIVITAI_SITES),
            "request_interval": 2.0,   # seconds between API requests
            "image_interval": 1.0,     # seconds between cover downloads
        },
        "network": {"mode": "direct", "proxy": ""},
        "library_dir": "",             # records and covers; empty means <data>/library
        # Files fingerprinted at the same time. Two is a safe middle: SSDs gain (measured on an NVMe drive whose
        # old files read slowly: 1.7 times the throughput), and a hard disk loses little.
        "hashing": {"workers": 2},
        # Reading the authors' notes (reader.py): by the rules, or by a local language model that Model Depot runs
        # itself (managed: llama-server and a GGUF file) or that the user runs (endpoint: an OpenAI-style address).
        "reader": {
            "mode": "rules",
            "managed": {"server": "", "model_file": "", "context": 16384, "args": ""},
            "endpoint": {"url": "", "model": "", "key": ""},
            "install_dir": "",          # where "download and install" puts llama.cpp and the model; empty: <data>/llm
            # Seconds after the last reading before a managed server is stopped. Short: llama.cpp fills the graphics card
            # (10 of 12 GB, measured 2026-10-02) for as long as it runs, and making images needs the card; a reading
            # that comes later waits 20 to 30 seconds for the model to load again, and nobody waits on a reading.
            "idle_stop": 60,
        },
        # Warming up (library.py): after each scan, the civitai pages the library has none of yet and the first
        # `samples` example images of each model are fetched ahead of time, at the background work's pace, so that a
        # model opens at once the first time it is opened. Off until the user turns it on (2026-10-03): it asks civitai
        # about every model in the library.
        "prewarm": {"enabled": False, "samples": 6},
        # Inbox folders (inbox.py): model files dropped into them are sorted into the library once the user confirms.
        # `dirs` are kept; `once` (the setup's own models folder) are dropped when nothing is left to sort in them.
        "inbox": {"dirs": [], "once": []},
    }


def parse_env_text(text):
    """KEY=VALUE lines of a .env file. Quotes around the value are dropped; `export KEY=...` is accepted."""
    out = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.lower().startswith("export "):
            s = s[7:].lstrip()
        if "=" not in s:
            continue
        key, value = s.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        if key:
            out[key] = value
    return out


def read_env_var(path, name):
    """(value, problem). problem is None, "file_missing" or "var_missing". The value is never logged by callers."""
    try:
        with open(path, encoding="utf-8-sig") as f:   # utf-8-sig: PowerShell 5.1 writes a BOM
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return "", "file_missing"
    value = parse_env_text(text).get(name, "")
    return (value, None) if value else ("", "var_missing")


def _merge(base, extra):
    """Copy of base with extra laid over it; dicts merge, everything else is replaced."""
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _abs_path(value):
    p = os.path.expanduser(os.path.expandvars(str(value or "").strip().strip('"')))
    return os.path.normpath(p) if p else ""


def normalize(raw):
    """(settings, problems). Settings always has every key; problems lists (field, code) for what was wrong."""
    cfg = _merge(defaults(), raw if isinstance(raw, dict) else {})
    problems = []
    cfg["schema"] = SCHEMA
    cfg["setup_done"] = bool(cfg.get("setup_done"))

    if cfg.get("language") not in LANGS:
        cfg["language"] = ""

    server = cfg["server"] if isinstance(cfg.get("server"), dict) else {}
    host = str(server.get("host") or DEFAULT_HOST).strip()
    try:
        port = int(server.get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = 0
    if not 1 <= port <= 65535:
        problems.append(("server.port", "bad_port"))
        port = DEFAULT_PORT
    cfg["server"] = {"host": host, "port": port}

    remote = cfg["remote_access"] if isinstance(cfg.get("remote_access"), dict) else {}
    cfg["remote_access"] = {"enabled": bool(remote.get("enabled")), "key": str(remote.get("key") or "")}
    if cfg["remote_access"]["enabled"] and len(cfg["remote_access"]["key"]) < 8:
        problems.append(("remote_access.key", "remote_key_short"))

    comfy = cfg["comfyui"] if isinstance(cfg.get("comfyui"), dict) else {}
    cfg["comfyui"] = {"dir": _abs_path(comfy.get("dir")), "models_dir": _abs_path(comfy.get("models_dir"))}

    folders, seen = [], set()
    for item in cfg.get("folders") if isinstance(cfg.get("folders"), list) else []:
        if not isinstance(item, dict):
            continue
        path = _abs_path(item.get("path"))
        ctype = str(item.get("comfy_type") or "").strip()
        if not path or not os.path.isabs(path):
            problems.append(("folders", "folder_not_absolute"))
            continue
        if not _TYPE_RE.match(ctype):
            problems.append(("folders", "folder_type_bad"))
            continue
        key = (os.path.normcase(path), ctype)
        if key in seen:
            continue
        seen.add(key)
        folders.append({"path": path, "comfy_type": ctype, "enabled": item.get("enabled", True) is not False})
    cfg["folders"] = folders

    civ = cfg["civitai"] if isinstance(cfg.get("civitai"), dict) else {}
    source = civ.get("token_source") if civ.get("token_source") in TOKEN_SOURCES else "none"
    env_var = str(civ.get("env_var") or "CIVITAI_TOKEN").strip()
    if not _ENV_NAME_RE.match(env_var):
        problems.append(("civitai.env_var", "env_var_bad"))
        env_var = "CIVITAI_TOKEN"
    sites = [str(s).strip().rstrip("/") for s in (civ.get("sites") or []) if _URL_RE.match(str(s).strip())]

    def seconds(value, default):
        try:
            return min(60.0, max(0.0, float(value)))
        except (TypeError, ValueError):
            return default

    cfg["civitai"] = {
        "token_source": source,
        "token": str(civ.get("token") or "").strip(),
        "env_file": _abs_path(civ.get("env_file")),
        "env_var": env_var,
        "sites": sites or list(CIVITAI_SITES),
        "request_interval": seconds(civ.get("request_interval"), 2.0),
        "image_interval": seconds(civ.get("image_interval"), 1.0),
    }
    if source == "env_file" and not cfg["civitai"]["env_file"]:
        problems.append(("civitai.env_file", "env_file_missing"))

    net = cfg["network"] if isinstance(cfg.get("network"), dict) else {}
    mode = net.get("mode") if net.get("mode") in NETWORK_MODES else "direct"
    proxy = str(net.get("proxy") or "").strip()
    if mode == "proxy" and not _URL_RE.match(proxy):
        problems.append(("network.proxy", "proxy_bad"))
    cfg["network"] = {"mode": mode, "proxy": proxy}

    lib = _abs_path(cfg.get("library_dir"))
    if lib and not os.path.isabs(lib):
        problems.append(("library_dir", "folder_not_absolute"))
        lib = ""
    cfg["library_dir"] = lib

    hashing = cfg["hashing"] if isinstance(cfg.get("hashing"), dict) else {}
    try:
        workers = int(hashing.get("workers") or 2)
    except (TypeError, ValueError):
        workers = 2
    cfg["hashing"] = {"workers": min(8, max(1, workers))}

    reader = cfg["reader"] if isinstance(cfg.get("reader"), dict) else {}
    mode = reader.get("mode") if reader.get("mode") in READER_MODES else "rules"
    managed = reader.get("managed") if isinstance(reader.get("managed"), dict) else {}
    endpoint = reader.get("endpoint") if isinstance(reader.get("endpoint"), dict) else {}
    try:
        context = min(131072, max(2048, int(managed.get("context") or 16384)))
    except (TypeError, ValueError):
        context = 16384
    try:
        idle_stop = min(3600, max(30, int(reader.get("idle_stop") or 60)))
    except (TypeError, ValueError):
        idle_stop = 60
    cfg["reader"] = {
        "mode": mode,
        "managed": {"server": _abs_path(managed.get("server")), "model_file": _abs_path(managed.get("model_file")),
                    "context": context, "args": str(managed.get("args") or "").strip()[:500]},
        "endpoint": {"url": str(endpoint.get("url") or "").strip().rstrip("/"),
                     "model": str(endpoint.get("model") or "").strip()[:200],
                     "key": str(endpoint.get("key") or "").strip()},
        "install_dir": _abs_path(reader.get("install_dir")),
        "idle_stop": idle_stop,
    }
    if mode == "managed":
        if not cfg["reader"]["managed"]["server"] or not os.path.isfile(cfg["reader"]["managed"]["server"]):
            problems.append(("reader.managed.server", "reader_server_missing"))
        if not cfg["reader"]["managed"]["model_file"] or not os.path.isfile(cfg["reader"]["managed"]["model_file"]):
            problems.append(("reader.managed.model_file", "reader_model_missing"))
    if mode == "endpoint" and not _URL_RE.match(cfg["reader"]["endpoint"]["url"]):
        problems.append(("reader.endpoint.url", "reader_url_bad"))

    warm = cfg["prewarm"] if isinstance(cfg.get("prewarm"), dict) else {}
    try:
        samples = min(20, max(0, int(warm.get("samples") if warm.get("samples") is not None else 6)))
    except (TypeError, ValueError):
        samples = 6
    cfg["prewarm"] = {"enabled": warm.get("enabled") is True, "samples": samples}

    inbox = cfg["inbox"] if isinstance(cfg.get("inbox"), dict) else {}

    def folder_list(value):
        out, seen = [], set()
        for item in value if isinstance(value, list) else []:
            path = _abs_path(item)
            if path and os.path.isabs(path) and os.path.normcase(path) not in seen:
                seen.add(os.path.normcase(path))
                out.append(path)
        return out

    cfg["inbox"] = {"dirs": folder_list(inbox.get("dirs")), "once": folder_list(inbox.get("once"))}
    return cfg, problems


class Config:
    def __init__(self, paths):
        self.paths = paths
        self.data = defaults()
        self.exists = False          # is there a settings file at all
        self.load_problem = None     # set when the file was there but could not be read

    @classmethod
    def load(cls, paths):
        self = cls(paths)
        try:
            with open(paths.config, encoding="utf-8-sig") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return self
        except (OSError, ValueError) as e:
            self.load_problem = str(e)
            return self
        self.exists = True
        self.data, _problems = normalize(raw)
        return self

    def save(self):
        """Write the file whole: a temporary file first, then a rename, so a crash never leaves half a file."""
        os.makedirs(self.paths.root, exist_ok=True)
        tmp = "%s.%s.tmp" % (self.paths.config, uuid.uuid4().hex[:8])
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, self.paths.config)
        self.exists = True

    # ---- reading --------------------------------------------------------------------------------------------------
    @property
    def configured(self):
        return bool(self.exists and self.data.get("setup_done"))

    @property
    def language(self):
        return self.data.get("language") or ""

    @property
    def host(self):
        return self.data["server"]["host"]

    @property
    def port(self):
        return self.data["server"]["port"]

    def folders(self):
        """The folders to scan, in settings order: [{"path", "comfy_type"}]."""
        return [{"path": f["path"], "comfy_type": f["comfy_type"]} for f in self.data["folders"] if f.get("enabled")]

    def library_dir(self):
        return self.data.get("library_dir") or self.paths.default_library

    def civitai_token(self):
        """(token, problem). problem: None, "empty", "file_missing", "var_missing"."""
        civ = self.data["civitai"]
        if civ["token_source"] == "value":
            return (civ["token"], None) if civ["token"] else ("", "empty")
        if civ["token_source"] == "env_file":
            return read_env_var(civ["env_file"], civ["env_var"])
        return "", None

    def proxy(self):
        net = self.data["network"]
        return net["proxy"] if net["mode"] == "proxy" and net["proxy"] else None

    def public(self):
        """The settings as the page may see them: no key values, only whether one is in place."""
        out = copy.deepcopy(self.data)
        token, problem = self.civitai_token()
        out["civitai"].pop("token", None)
        out["civitai"]["has_token"] = bool(token)
        out["civitai"]["token_problem"] = problem
        out["remote_access"] = {"enabled": self.data["remote_access"]["enabled"],
                                "has_key": bool(self.data["remote_access"]["key"])}
        out["reader"]["endpoint"].pop("key", None)
        out["reader"]["endpoint"]["has_key"] = bool(self.data["reader"]["endpoint"]["key"])
        return out

    def reader_install_dir(self):
        return self.data["reader"]["install_dir"] or os.path.join(self.paths.root, "llm")

    # ---- changing -------------------------------------------------------------------------------------------------
    def update(self, changes):
        """Lay `changes` over the current settings. Returns the problems found; nothing is kept if there are any.

        Key fields left empty mean "unchanged": the page never receives the stored keys, so it cannot send them back.
        """
        changes = copy.deepcopy(changes) if isinstance(changes, dict) else {}
        civ = changes.get("civitai")
        if isinstance(civ, dict) and not civ.get("token"):
            civ.pop("token", None)
        remote = changes.get("remote_access")
        if isinstance(remote, dict) and not remote.get("key"):
            remote.pop("key", None)
        reader = changes.get("reader")
        endpoint = reader.get("endpoint") if isinstance(reader, dict) else None
        if isinstance(endpoint, dict) and not endpoint.get("key"):
            endpoint.pop("key", None)
        merged, problems = normalize(_merge(self.data, changes))
        if not problems:
            self.data = merged
        return problems
