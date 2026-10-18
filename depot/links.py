"""Reading what someone pastes into the download field: civitai addresses and bare numbers.

Accepted, on civitai.com, civitai.red and civitai.green (a link's site only names the model; the program always asks
the sites in the settings):

    https://civitai.red/models/590924                       a model; its newest downloadable version is offered
    https://civitai.red/models/590924/some-name?modelVersionId=661435
    https://civitai.com/api/download/models/661435?fileId=…  a version, and the file the link points at
    https://civitai.com/api/v1/models/590924, …/api/v1/model-versions/661435
    590924                                                  a model number
    590924@661435, urn:air:sdxl:lora:civitai:590924@661435   a model and a version

Text around the links is allowed: addresses are found wherever they stand, and the rest is split at white space and
common separators.
"""
import re
from urllib.parse import parse_qs, urlsplit

CIVITAI_DOMAINS = ("civitai.com", "civitai.red", "civitai.green")
MAX_LINKS = 20

_URL_FIND_RE = re.compile(r"(?:https?://|(?<![\w.-])(?:[\w-]+\.)*civitai\.(?:com|red|green)(?=[/?#:]|\b))"
                          r"[^\s<>\"'，；、。）」』》“”]*", re.I)
_SPLIT_RE = re.compile(r"[\s,;，；、]+")
_AIR_RE = re.compile(r"^urn:air:[^:\s]+:[^:\s]+:civitai:(\d+)@(\d+)$", re.I)
_PAIR_RE = re.compile(r"^(\d+)@(\d+)$")
_NUM_RE = re.compile(r"^\d{1,12}$")
_TRIM = "<>()[]{}\"'`“”‘’「」『』《》（）。."


def _int(value):
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if 0 < n < 10 ** 12 else None


def _is_civitai(host):
    host = (host or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in CIVITAI_DOMAINS)


def parse_url(text):
    """{"model_id", "version_id", "file_id", "error"} for one address."""
    url = text if re.match(r"^https?://", text, re.I) else "https://" + text
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return {"error": "unrecognized"}
    if not _is_civitai(host):
        return {"error": "not_civitai"}
    segs = [s for s in parts.path.split("/") if s]
    lower = [s.lower() for s in segs]
    query = parse_qs(parts.query)

    def first(name):
        return _int((query.get(name) or [None])[0])

    out = {"model_id": None, "version_id": None, "file_id": None, "error": None}
    if len(segs) >= 2 and lower[0] == "models":
        out["model_id"] = _int(segs[1])
        out["version_id"] = first("modelVersionId")
    elif len(segs) >= 4 and lower[:3] == ["api", "download", "models"]:
        out["version_id"] = _int(segs[3])
        out["file_id"] = first("fileId")
    elif len(segs) >= 4 and lower[:3] == ["api", "v1", "models"]:
        out["model_id"] = _int(segs[3])
    elif len(segs) >= 4 and lower[:3] == ["api", "v1", "model-versions"]:
        out["version_id"] = _int(segs[3])
    elif len(segs) >= 2 and lower[0] == "model-versions":
        out["version_id"] = _int(segs[1])
    if out["model_id"] is None and out["version_id"] is None:
        return {"error": "not_model"}
    return out


def parse_token(token):
    """One piece of pasted text as {"input", "model_id", "version_id", "file_id", "error"}."""
    s = token.strip().strip(_TRIM)
    out = {"input": s, "model_id": None, "version_id": None, "file_id": None, "error": None}
    m = _AIR_RE.match(s) or _PAIR_RE.match(s)
    if m:
        out["model_id"], out["version_id"] = _int(m.group(1)), _int(m.group(2))
    elif _NUM_RE.match(s):
        out["model_id"] = _int(s)
    elif _URL_FIND_RE.match(s):
        out.update(parse_url(s))
    else:
        out["error"] = "unrecognized"
    if out["error"] is None and out["model_id"] is None and out["version_id"] is None:
        out["error"] = "unrecognized"
    return out


def _tokens(text):
    """The pieces of a pasted text in reading order: every address found in it, and the words between them."""
    pos = 0
    for m in _URL_FIND_RE.finditer(text):
        yield from (w for w in _SPLIT_RE.split(text[pos:m.start()]) if w.strip(_TRIM + " "))
        yield m.group(0)
        pos = m.end()
    yield from (w for w in _SPLIT_RE.split(text[pos:]) if w.strip(_TRIM + " "))


def parse(text):
    """Every link in a pasted text, in order, each model and version once. Pieces that are not links come back
    with an error code (not_civitai, not_model, unrecognized), so the page can say what was skipped. At most
    MAX_LINKS links are read at a time; the rest come back as "too_many"."""
    out, seen, count = [], set(), 0
    for token in _tokens(str(text or "")):
        item = parse_token(token)
        if item["error"] is None:
            key = (item["model_id"], item["version_id"])
            if key in seen:
                continue
            seen.add(key)
            count += 1
            if count > MAX_LINKS:
                item = dict(item, error="too_many")
        out.append(item)
    return out
