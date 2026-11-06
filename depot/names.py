"""The name a model goes by in the library (the user's choice, 2026-10-14).

civitai titles carry slogans, release numbers, lists of the base models a model is made for, and emoji, and authors
rewrite them with each release ("Moody Krea 2 Mix (uncensored) V8 FREE! V8免费! Halloween SE is here!"). The name is
settled once, when the model first comes into the library, and does not follow later renames; the user can change it.

The local model cuts the name out of the title (system(): catalog/prompt-names.txt; SCHEMA); an answer that adds a word the title does not have is
not used (faithful). Without a local model, or until it has answered, `by_rules` does the few cuts that are safe: on
296 real titles (2026-10-14) every rule that cut more also cut names ("BFS - Best Face Swap" to "BFS").

    names/<model id>.json   {"model_id", "from": the title it was made from, "name", "en", "zh", "by": "ai" | "rules", "at"}
"""
import functools
import re

from . import catalog

EMOJI = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️‍✨⭐]")
_BRACKETS = re.compile(r"[\(\[（【]([^\)\]）】]*)[\)\]）】]")


@functools.lru_cache(maxsize=4)
def _base_list(bases):
    """A text that is only a list of base models; `bases` is the families catalog's title_base_pattern."""
    return re.compile(r"^\s*(?:ver\.?\s*)?(?:(?:%s)\s*(?:[/+&,]|\band\b)?\s*)+$" % bases, re.I)


def _only_bases(text):
    return bool(text.strip()) and bool(_base_list(catalog.load("families")["title_base_pattern"]).match(text))


def by_rules(title):
    """The safe cuts: emoji, a leading "Civitai", brackets that hold only base models, and a part after "|" or at the
    end after "/" , " - " or "for" that is only a list of base models. Returns the name, never empty."""
    text = EMOJI.sub(" ", str(title or ""))
    text = re.sub(r"^\s*civitai\s+", "", text, flags=re.I)
    text = _BRACKETS.sub(lambda m: " " if _only_bases(m.group(1)) else m.group(0), text)
    parts = [p for p in re.split(r"\s*[|｜]\s*", text) if p.strip()]
    kept = [p for p in parts if not _only_bases(p)] or parts[:1]
    text = " | ".join(kept)
    m = re.search(r"(\s+-\s+|\s+for\s+)([^-]*)$", text, flags=re.I)        # "Rainy Street - Krea2", "... for Krea 2"
    if m and m.start() and _only_bases(m.group(2)):
        text = text[:m.start()]
    m = re.search(r"\s+(\S+(?:\s*[/+]\s*\S+)+)$", text)                     # "Dark Art Style Krea2/Anima/Flux"
    if m and _only_bases(m.group(1)):
        text = text[:m.start()]
    text = re.sub(r"\s{2,}", " ", text).strip(" -–—|/+&,:")
    return text or str(title or "").strip()


def system():
    """The local model's instructions for naming a model (catalog/prompt-names.txt)."""
    return catalog.text("prompt-names")


def __getattr__(name):
    if name == "SYSTEM":
        return system()
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


_STR_OR_NULL = {"anyOf": [{"type": "string"}, {"type": "null"}]}
SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}, "en": _STR_OR_NULL, "zh": _STR_OR_NULL},
          "required": ["name", "en", "zh"], "additionalProperties": False}
MAX_ANSWER_TOKENS = 160         # a name is short; a model caught repeating itself stops here and is not used

_KANA = re.compile(r"[぀-ヿ]")


def _words(text):
    return re.findall(r"[a-z0-9]+|[぀-ヿ㐀-鿿＀-￯]", str(text or "").lower())


def faithful(answer, title):
    """The local model's answer, tidied, when every word and every CJK character of it is in the title and it is no
    longer than the title; else None. "zh" holding kana is Japanese, not Chinese: dropped."""
    if not isinstance(answer, dict):
        return None
    have = set(_words(title))
    out = {}
    for key in ("name", "en", "zh"):
        value = answer.get(key)
        value = re.sub(r"\s{2,}", " ", value).strip() if isinstance(value, str) else ""
        if not value:
            out[key] = None
            continue
        if len(value) > len(str(title)) + 2 or any(w not in have for w in _words(value)):
            return None
        out[key] = value
    if not out["name"]:
        return None
    if out["zh"] and (_KANA.search(out["zh"]) or not re.search(r"[一-鿿]", out["zh"])):
        out["zh"] = None
    if out["en"] and not re.search(r"[A-Za-z]", out["en"]):
        out["en"] = None
    if not (out["en"] and out["zh"]):
        out["en"] = out["zh"] = None        # the parts matter only when the name has both
    return out


def version_name(name, base_model):
    """A version's name as a card shows it: emoji gone, and the base model the version is for, which the group it
    sits in already says ("Ultramarines Krea2" in Krea 2: "Ultramarines"). Never empty."""
    text = re.sub(r"\s{2,}", " ", EMOJI.sub(" ", str(name or ""))).strip()
    if not base_model:
        return text or str(name or "")
    words = _base_words(base_model)
    if not words:
        return text
    cut = re.sub(r"(?<![A-Za-z0-9])(%s)(?![A-Za-z0-9])" % "|".join(sorted(map(re.escape, words), key=len, reverse=True)),
                 " ", text, flags=re.I)
    cut = re.sub(r"[\s_]*[-_]+[\s_]*$|^[\s_]*[-_]+[\s_]*", "", re.sub(r"\(\s*\)|\[\s*\]", "", cut))
    cut = re.sub(r"\s{2,}", " ", cut).strip(" -_|/,·")
    return cut if re.search(r"[A-Za-z0-9぀-鿿]", cut) else (text or str(name or ""))


def _base_words(base_model):
    """How version names write a base model: civitai's "Krea 2" is also "Krea2", "K2", "KR2" (the families catalog's
    version_name_spellings; the first marker found in the base model wins)."""
    key = str(base_model or "").lower().replace(" ", "").replace("-", "")
    for marker, words in catalog.load("families")["version_name_spellings"]:
        if marker.lower().replace(" ", "") in key:
            return list(words)
    return []
