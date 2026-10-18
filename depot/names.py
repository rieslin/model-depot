"""The name a model goes by in the library (the user's choice, 2026-10-14).

civitai titles carry slogans, release numbers, lists of the base models a model is made for, and emoji, and authors
rewrite them with each release ("Moody Krea 2 Mix (uncensored) V8 FREE! V8免费! Halloween SE is here!"). The name is
settled once, when the model first comes into the library, and does not follow later renames; the user can change it.

The local model cuts the name out of the title (SYSTEM, SCHEMA); an answer that adds a word the title does not have is
not used (faithful). Without a local model, or until it has answered, `by_rules` does the few cuts that are safe: on
296 real titles (2026-10-14) every rule that cut more also cut names ("BFS - Best Face Swap" to "BFS").

    names/<model id>.json   {"model_id", "from": the title it was made from, "name", "en", "zh", "by": "ai" | "rules", "at"}
"""
import re

EMOJI = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️‍✨⭐]")
_BASES = (r"krea[\s-]?2|krea|anima(?:\s?base\s?v?\d*)?|flux(?:\.\d)?(?:\s?d)?|f1|pony(?:\s?xl)?|illustrious(?:\s?xl)?|ill|il|"
          r"noob(?:ai)?|sdxl|sd\s?1\.5|sd|xl|z-?image(?:\s?turbo)?|zimageturbo|zit|zib|zid|chroma|qwen(?:[\s-]?image)?(?:[\s-]?2\.1)?|"
          r"qw21|hidream|ideogram\s?4|minimax\s?h3|h3|hunyuan|wan")
_BASE_LIST = re.compile(r"^\s*(?:ver\.?\s*)?(?:(?:%s)\s*(?:[/+&,]|\band\b)?\s*)+$" % _BASES, re.I)
_BRACKETS = re.compile(r"[\(\[（【]([^\)\]）】]*)[\)\]）】]")


def _only_bases(text):
    return bool(text.strip()) and bool(_BASE_LIST.match(text))


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


SYSTEM = """You give AI image-generation models (checkpoints, LoRAs and the like) the name a user knows them by, for a local model library. You get the title the author gives the model on civitai. Authors pack titles with things that are not the name and change them with each release. Answer with the model's own name, cut out of the title.

Cut out:
- promotion and announcements: "FREE", "is here", "is now free", "Goes Ancient", "SALE", "NEW", "Updated", "Pending", "免费", "免费下载", "来了", "放出", "首发", exclamations;
- release numbers of one version (V8, v2.0, V8SE, "V1 | V2") when the model is also known without them;
- the base models it is made for, wherever they stand: in brackets, after "|", after " - ", after "for", or as a list joined by "/", "+", "&" or ",": Flux, Pony, PonyXL, SDXL, XL, IL, ILL, Illustrious, IllustriousXL, NoobAI, Noob, Anima, AnimaBaseV1, Krea 2, Krea2, Krea-2, Qwen Image 2.1, QW21, Z-Image, ZImageTurbo, ZIT, ZIB, ZID, Zimage, Chroma, SD, SD1.5, Ideogram 4, MiniMax H3, Hunyuan;
- a leading "Civitai"; emoji and decorative symbols (✨ 【】); the words "LoRA", "Lora", "LORA", "Checkpoint", "Concept", "Concept >" when the name stands without them;
- file-format notes in brackets: fp8, bf16, [2.9B/2B].

Keep, exactly as written (same spelling, capitals and punctuation), everything else, including:
- words that are part of a product's name: "WAI-illustrious-SDXL", "Pony Diffusion V6 XL", "Animagine XL V3.1", "CyberRealistic Z-Image Turbo", "Qwen Image 2.1 VAE", "Krea 2 Turbo" keep their numbers and base-model words, because that is what the product is called;
- a creator's name that is part of the name ("Muse By Stable Yogi", "Joschek's Film Grain");
- descriptive words that tell models apart ("Detail Slider", "(HD)", "(soft light)").
If unsure, keep the word. Never translate, never add a word, never reorder words.

The title may give the name in several languages (English, Chinese, Japanese), often joined by "/" or "|". "name" holds every language that remains, in the title's order, joined by " / ". "en" is the English (Latin-letter) part alone and "zh" the Chinese part alone, only when "name" has both; otherwise null. Japanese is not Chinese: text with kana (ひらがな, カタカナ) goes in neither "en" nor "zh"."""

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
    """How version names write a base model: civitai's "Krea 2" is also "Krea2", "K2", "KR2"."""
    b = str(base_model or "").lower()
    table = (("krea", ["Krea 2", "Krea2", "Krea-2", "KR2", "K2"]), ("anima", ["Anima"]), ("illustrious", ["Illustrious", "IllustriousXL", "IL", "ILL"]),
             ("pony", ["Pony", "PonyXL"]), ("noob", ["NoobAI", "Noob"]), ("flux", ["Flux", "F1"]), ("sdxl", ["SDXL", "XL"]),
             ("sd 1.5", ["SD1.5", "SD 1.5", "SD15"]), ("zimage", ["ZIT", "Z-Image", "ZImage", "ZImageTurbo", "Zimage Turbo"]),
             ("qwen", ["Qwen", "Qwen 2.1", "Qwen2.1", "QW21"]), ("chroma", ["Chroma"]))
    key = b.replace(" ", "").replace("-", "")
    for marker, words in table:
        if marker.replace(" ", "") in key:
            return words
    return []
