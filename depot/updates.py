"""Which of the versions on a model's civitai page is a newer version of a file here (the user's rule, 2026-10-14).

Authors put several lines on one page: a main line (V7, V8), special editions (V7SE, V8SE), seasonal and premium
editions, and the same version in other precisions (BF16, FP8, INT8). Some put different models on one page (one LoRA
per character and base model). So a version counts as newer only when it has the same base model, is of the same line
(the same edition mark, and the same naming words apart from numbers and precision) and has a higher version number
and a later date. Versions of the same number and line are the same version in another precision: offered together as
its files. Every other version published after the one here and not here is a new entry of the page, not a new version.
"""
import functools
import re

from . import catalog

_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️‍]")
# a version number: v8, V8.0, 2.1, ZIT-V1; "r1" right after it is a revision of that version (V6R1 comes after V6);
# "se" marks a special edition (V8SE)
_NUMBER = re.compile(r"(?<![a-z0-9.])v?(\d+(?:\.\d+)*)(?:r(\d+))?(se)?(?![0-9])", re.I)
_ignored = [None, None, None]


def _ignored_words():
    """Words that tell nothing about the line: precision and format, training method, filler (civitai's
    version_noise_words), and base models written into version names ("Ultramarines Krea2", "v1.0 anima"; families'
    version_name_words), which the base model says already."""
    noise = catalog.load("civitai")["version_noise_words"]
    bases = catalog.load("families")["version_name_words"]
    if _ignored[0] is not noise or _ignored[1] is not bases:
        _ignored[:] = [noise, bases, frozenset(str(w).lower() for w in list(noise) + list(bases))]
    return _ignored[2]


@functools.lru_cache(maxsize=4)
def _compiled(pattern):
    return re.compile(pattern, re.I)


def _precision_re():
    """How version names write a precision or format (civitai's version_precision_pattern)."""
    return _compiled(catalog.load("civitai")["version_precision_pattern"])


def parse(name):
    """(number as a tuple of ints or None, special edition, the line's naming words)."""
    text = _EMOJI.sub(" ", str(name or "").lower())
    text = re.sub(r"[^a-z0-9.぀-ヿ一-鿿]+", " ", text)
    m = _NUMBER.search(text)
    number = None
    if m:
        number = tuple(int(x) for x in m.group(1).split(".") if x)
        if m.group(2):
            number = number + (0,) * (3 - len(number)) + (int(m.group(2)),)    # V6R1: after V6 and V6.0.0
    special = bool(m and m.group(3))
    rest = _NUMBER.sub(" ", text)
    ignored = _ignored_words()
    words = {w for w in rest.split() if re.fullmatch(r"[a-z]{2,}", w) and w not in ignored}
    return number, special, frozenset(words)


def precision(name):
    """The precisions a version name states, lower case, sorted: ["fp8"], ["bf16", "fp8"], or []."""
    return sorted({p.lower() for p in _precision_re().findall(str(name or ""))})


def short_label(name):
    """A version's number as a card shows it: "V7", "V8SE", "V1.1"; None when the name has no number."""
    m = _NUMBER.search(_EMOJI.sub(" ", str(name or "")))
    if not m:
        return None
    return "V" + m.group(1) + ("R" + m.group(2) if m.group(2) else "") + ("SE" if m.group(3) else "")


def _date(v):
    return str(v.get("publishedAt") or v.get("createdAt") or "")


def _same_line(a, b):
    (n1, se1, w1), (n2, se2, w2) = a, b
    return n1 is not None and n2 is not None and se1 == se2 and (w1 <= w2 or w2 <= w1)


def compare(versions, mine_id, here_ids, ignored=()):
    """What the page holds that is newer than the version `mine_id`, for one file here.

    versions: the page's modelVersions. here_ids: version ids that are in the library. ignored: version ids the user
    chose to ignore. Returns {"newer": [version, ...] the newest newer version and its other precisions, newest number
    first, "entries": [version, ...] other versions published later and not here, newest first, "variants": [version]
    the version here in other precisions} or None when `mine_id` is not on the page."""
    by_id = {v.get("id"): v for v in versions if isinstance(v, dict)}
    mine = by_id.get(mine_id)
    if mine is None:
        return None
    key = parse(mine.get("name"))
    base = mine.get("baseModel")
    groups, entries, variants = {}, [], []
    for v in versions:
        if not isinstance(v, dict) or v.get("id") == mine_id or v.get("id") in here_ids:
            continue
        other = parse(v.get("name"))
        same = v.get("baseModel") == base and _same_line(key, other)
        if same and other[0] == key[0]:
            variants.append(v)
        elif same and other[0] > key[0] and _date(v) > _date(mine) and v.get("id") not in ignored:
            groups.setdefault(other[0], []).append(v)
        elif _date(v) > _date(mine) and not (same and other[0] > key[0]):
            entries.append(v)
    newest = max(groups) if groups else None
    newer = sorted(groups[newest], key=_date) if newest else []
    entries.sort(key=_date, reverse=True)
    return {"newer": newer, "entries": entries, "variants": variants}


def default_file(newer, mine_name):
    """Of the newer version's precisions, the one whose precision matches the file here (a full-precision file here:
    the one that states none)."""
    want = precision(mine_name)
    for v in newer:
        if precision(v.get("name")) == want:
            return v.get("id")
    return newer[0].get("id") if newer else None
