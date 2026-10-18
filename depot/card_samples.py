"""What the author's example images were made with, counted: the settings a card suggests from them.

civitai keeps the generation settings of most example images ("meta"): WebUI images carry them in WebUI's words
("DPM++ 2M Karras", "Clip skip: 2", "Size: 832x1216", <lora:name:0.8> in the prompt), ComfyUI images in ComfyUI's
("dpmpp_2m" with a separate scheduler). Both are read here and given in ComfyUI's words, which is what the card holds.

A value is suggested only where the examples agree well enough (see the thresholds below); what they do not agree on
is left out rather than guessed.
"""
import re
import statistics
from collections import Counter

# ---- sampler and scheduler names: WebUI's words to ComfyUI's ------------------------------------------------------
COMFY_SAMPLERS = (
    "euler", "euler_cfg_pp", "euler_ancestral", "euler_ancestral_cfg_pp", "heun", "heunpp2", "dpm_2", "dpm_2_ancestral",
    "lms", "dpm_fast", "dpm_adaptive", "dpmpp_2s_ancestral", "dpmpp_2s_ancestral_cfg_pp", "dpmpp_sde", "dpmpp_sde_gpu",
    "dpmpp_2m", "dpmpp_2m_cfg_pp", "dpmpp_2m_sde", "dpmpp_2m_sde_gpu", "dpmpp_3m_sde", "dpmpp_3m_sde_gpu", "ddpm", "lcm",
    "ipndm", "ipndm_v", "deis", "res_multistep", "res_multistep_cfg_pp", "res_multistep_ancestral", "gradient_estimation",
    "er_sde", "seeds_2", "seeds_3", "sa_solver", "ddim", "uni_pc", "uni_pc_bh2")
COMFY_SCHEDULERS = ("simple", "sgm_uniform", "karras", "exponential", "ddim_uniform", "beta", "normal", "linear_quadratic",
                    "kl_optimal")

# WebUI's sampler names, longest first so that "DPM++ 2M SDE" is not read as "DPM++ 2M".
_WEBUI_SAMPLERS = (
    ("dpm++ 2m sde heun", "dpmpp_2m_sde"), ("dpm++ 3m sde", "dpmpp_3m_sde"), ("dpm++ 2m sde", "dpmpp_2m_sde"),
    ("dpm++ 2s a", "dpmpp_2s_ancestral"), ("dpm++ 2m", "dpmpp_2m"), ("dpm++ sde", "dpmpp_sde"),
    ("dpm2 a", "dpm_2_ancestral"), ("dpm2", "dpm_2"), ("dpm fast", "dpm_fast"), ("dpm adaptive", "dpm_adaptive"),
    ("euler a", "euler_ancestral"), ("euler_a", "euler_ancestral"), ("euler", "euler"), ("heun", "heun"),
    ("lms", "lms"), ("ddim", "ddim"), ("ddpm", "ddpm"), ("unipc", "uni_pc"), ("uni_pc", "uni_pc"), ("lcm", "lcm"),
    ("deis", "deis"), ("ipndm", "ipndm"), ("restart", None), ("plms", None),
)
_WEBUI_SCHEDULERS = (
    ("karras", "karras"), ("exponential", "exponential"), ("sgm uniform", "sgm_uniform"), ("sgm_uniform", "sgm_uniform"),
    ("simple", "simple"), ("normal", "normal"), ("beta", "beta"), ("ddim", "ddim_uniform"), ("kl optimal", "kl_optimal"),
    ("kl_optimal", "kl_optimal"), ("linear quadratic", "linear_quadratic"), ("uniform", "normal"),
)


def _words(text):
    return " ".join(str(text or "").lower().split())


def comfy_sampler(name):
    """(sampler, scheduler) in ComfyUI's words for a sampler name in either program's words. The scheduler is the one
    the WebUI name carries ("DPM++ 2M Karras"), or None when it names none. (None, None) when it is not a sampler
    ComfyUI has."""
    text = _words(name)
    if not text:
        return None, None
    if text in COMFY_SAMPLERS:
        return text, None
    sampler = None
    for webui, comfy in _WEBUI_SAMPLERS:
        if text.startswith(webui):
            sampler = comfy
            rest = text[len(webui):].strip()
            break
    else:
        return None, None
    if sampler is None:
        return None, None
    return sampler, comfy_scheduler(rest) if rest else None


def comfy_scheduler(name):
    text = _words(name)
    if not text or text in ("automatic", "auto", "default"):
        return None
    if text in COMFY_SCHEDULERS:
        return text
    for webui, comfy in _WEBUI_SCHEDULERS:
        if text.startswith(webui):
            return comfy
    return None


def find_sampler(text):
    """The first sampler named anywhere in a piece of free text, as (sampler, scheduler): "Euler a or DPM++ 2M Karras"
    gives ("euler_ancestral", None)."""
    low = _words(text)
    best = None
    for webui, comfy in _WEBUI_SAMPLERS:
        if comfy is None:
            continue
        for m in re.finditer(r"(?<![\w+])" + re.escape(webui) + r"(?![\w+])", low):
            if best is None or m.start() < best[0] or (m.start() == best[0] and len(webui) > best[1]):
                best = (m.start(), len(webui), comfy)
    for comfy in COMFY_SAMPLERS:
        for m in re.finditer(r"(?<![\w])" + re.escape(comfy) + r"(?![\w])", low):
            if best is None or m.start() < best[0]:
                best = (m.start(), len(comfy), comfy)
    if best is None:
        return None, None
    after = low[best[0] + best[1]:best[0] + best[1] + 24]
    return best[2], comfy_scheduler(after.strip(" ,(/")) if after.strip(" ,(/") else None


# ---- one image's settings -----------------------------------------------------------------------------------------
_LORA_TAG = re.compile(r"<(?:lora|lyco|locon):([^:>]+):(-?\d*\.?\d+)(?::[^>]*)?>", re.I)
_SIZE = re.compile(r"^\s*(\d{2,5})\s*[x×*]\s*(\d{2,5})\s*$")
_LORA_TYPES = ("lora", "lycoris", "locon", "dora")


def _num(value, kind=float):
    try:
        n = kind(float(value))
    except (TypeError, ValueError):
        return None
    return n


def stem(name):
    """A file or tag name without folders and extension, lower-cased: how names are compared."""
    base = re.split(r"[\\/]", str(name or ""))[-1].strip().lower()
    return re.sub(r"\.(safetensors|ckpt|pt|pth|bin|gguf)$", "", base)


def settings_of(image):
    """The settings one example image records, in ComfyUI's words; None when it records none."""
    meta = image.get("meta") if isinstance(image, dict) else None
    if not isinstance(meta, dict) or not meta:
        return None
    sampler, scheduler = comfy_sampler(meta.get("sampler"))
    stated = meta.get("Schedule type") or meta.get("scheduler")
    if stated:
        scheduler = comfy_scheduler(stated) or scheduler        # "Automatic" says nothing: keep what the name says
    elif sampler and scheduler is None and _words(meta.get("sampler")) not in COMFY_SAMPLERS:
        scheduler = "normal"        # an older WebUI name without a scheduler word: WebUI's plain schedule
    width = height = None
    size = _SIZE.match(str(meta.get("Size") or ""))
    if size:
        width, height = int(size.group(1)), int(size.group(2))
    elif meta.get("width") and meta.get("height"):
        width, height = _num(meta.get("width"), int), _num(meta.get("height"), int)
    clip = meta.get("Clip skip", meta.get("clipSkip"))
    clip = _num(clip, int)
    prompt = str(meta.get("prompt") or "")
    loras = []
    for entry in meta.get("resources") or []:
        if isinstance(entry, dict) and str(entry.get("type") or "").lower() in _LORA_TYPES:
            loras.append({"name": stem(entry.get("name")), "weight": _num(entry.get("weight")),
                          "hash": str(entry.get("hash") or "").lower() or None, "version_id": None})
    for entry in meta.get("civitaiResources") or []:
        if isinstance(entry, dict) and str(entry.get("type") or "").lower() in _LORA_TYPES:
            loras.append({"name": None, "weight": _num(entry.get("weight", entry.get("strength"))), "hash": None,
                          "version_id": _num(entry.get("modelVersionId"), int)})
    hashes = meta.get("hashes") if isinstance(meta.get("hashes"), dict) else {}
    named_hashes = {stem(k.split(":", 1)[1]): str(v).lower() for k, v in hashes.items()
                    if isinstance(k, str) and k.lower().startswith(("lora:", "lyco:")) and v}
    tags = [(stem(m.group(1)), _num(m.group(2))) for m in _LORA_TAG.finditer(prompt)]
    for name, weight in tags:
        loras.append({"name": name, "weight": weight, "hash": named_hashes.get(name), "version_id": None, "tag": True})
    return {
        "sampler": sampler, "scheduler": scheduler,
        "steps": _num(meta.get("steps"), int), "cfg": _num(meta.get("cfgScale", meta.get("cfg"))),
        "clip_skip": -abs(clip) if clip else None, "width": width, "height": height,
        "prompt": prompt, "negative": str(meta.get("negativePrompt") or ""),
        "loras": loras, "lora_tags": len(tags), "model": str(meta.get("Model") or "").strip() or None,
    }


# ---- counting -----------------------------------------------------------------------------------------------------
MIN_SHARE = 0.5         # a setting is suggested when at least half of the examples that state it agree
PREFIX_SHARE = 0.7      # a tag belongs to the prefix when this share of the prompts has it near the start
PREFIX_WINDOW = 10      # "near the start": among the first this many tags
NEGATIVE_SHARE = 0.7
MIN_PROMPTS = 3         # fewer prompts than this say nothing about a common prefix
# Tags that open many prompts but describe the picture, not the model's way of being prompted.
_SUBJECT_TAGS = frozenset((
    "1girl", "2girls", "3girls", "4girls", "5girls", "6+girls", "multiple girls", "1boy", "2boys", "3boys",
    "multiple boys", "1other", "solo", "duo", "trio", "group", "girl", "boy", "woman", "man", "women", "men", "female",
    "male", "couple", "no humans", "looking at viewer", "smile", "upper body", "full body", "cowboy shot", "portrait",
    "close-up", "outdoors", "indoors", "day", "night", "simple background", "white background", "standing", "sitting",
    "blush", "open mouth", "long hair", "short hair"))


def _mode(values, min_share=MIN_SHARE):
    """The most common value, when it is common enough: (value, count) or (None, 0)."""
    values = [v for v in values if v is not None]
    if not values:
        return None, 0
    value, count = Counter(values).most_common(1)[0]
    if len(values) > 1 and (count < 2 or count / len(values) < min_share):
        return None, 0
    return value, count


def _median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def split_tags(prompt):
    """A prompt's comma-separated tags, LoRA tags and empty pieces left out."""
    text = _LORA_TAG.sub(",", str(prompt or ""))
    out = []
    for piece in re.split(r"[,\n\r]+", text):
        piece = " ".join(piece.split())
        if piece and piece not in ("BREAK",):
            out.append(piece)
    return out


def tag_key(tag):
    """How tags are compared: without emphasis brackets and weights, lower-cased. "(masterpiece:1.2)" is
    "masterpiece"."""
    t = tag.strip().lower()
    t = re.sub(r":\s*-?\d*\.?\d+\s*\)*$", "", t)
    t = t.strip("()[]{} ")
    return " ".join(t.split())


def common_tags(prompts, share, window=None, skip=frozenset(), limit=40, keep=None):
    """The tags that a share of the prompts have (within the first `window` tags, when given), in the order they
    usually come, each written the way the prompts write it most often. `keep(key, share, prompts)` may turn a tag
    down."""
    lists = [split_tags(p) for p in prompts if str(p or "").strip()]
    if len(lists) < MIN_PROMPTS:
        return []
    seen, spelled, places = Counter(), {}, {}
    for tags in lists:
        counted = set()
        for i, tag in enumerate(tags if window is None else tags[:window]):
            key = tag_key(tag)
            if not key or key in counted or key in skip:
                continue
            counted.add(key)
            seen[key] += 1
            spelled.setdefault(key, Counter())[tag] += 1
            places.setdefault(key, []).append(i)
    chosen = [k for k, n in seen.items() if n / len(lists) >= share
              and (keep is None or keep(k, n / len(lists), len(lists)))]
    chosen.sort(key=lambda k: statistics.mean(places[k]))
    return [spelled[k].most_common(1)[0][0] for k in chosen[:limit]]


# The words prompts open with to ask for quality, rather than for a subject: a common tag is taken into the prefix when
# it is one of these, or when nearly every one of many examples starts with it (a style word the model wants).
_QUALITY = re.compile(
    r"^(?:score_\d+(?:_up)?|source_[a-z_]+|rating[_:]\s*[a-z_]+|masterpiece|masterwork|best quality|"
    r"(?:high|highest|amazing|great|good|top|ultra|very high|extremely high) quality|(?:very |best )?aesthetic|"
    r"absurd ?res|high ?res|highres|(?:ultra|super)[- ]?high[- ]?res(?:olution)?|high resolution|"
    r"(?:ultra|extremely|very|super|highly)[- ]?detailed|intricate(?: details?)?|\d+k(?: uhd)?|uhd|hdr|raw photo|"
    r"photo-?realistic|hyper-?realistic|realistic|newest|recent|year \d{4}|official art|anime screencap|very awa|"
    r"sharp focus|safe|sensitive|general|questionable|explicit)$")
PREFIX_STRONG_SHARE = 0.9
PREFIX_STRONG_PROMPTS = 5


def _prefix_tag(key, share, prompts):
    if len(key) > 60:
        return False
    return bool(_QUALITY.match(key)) or (share >= PREFIX_STRONG_SHARE and prompts >= PREFIX_STRONG_PROMPTS)


def _round(value, step):
    return round(round(value / step) * step, 2)


def checkpoint_values(settings, trigger_words=()):
    """Sampler, scheduler, steps, CFG, CLIP layer, size and the two prefixes, from the examples' settings."""
    out = {}
    pairs = [(s["sampler"], s["scheduler"]) for s in settings if s["sampler"]]
    pair, _n = _mode(pairs)
    if pair:
        out["sampler"] = pair[0]
        if pair[1]:
            out["scheduler"] = pair[1]
    elif pairs:
        sampler, _n = _mode([p[0] for p in pairs])
        if sampler:
            out["sampler"] = sampler
    steps = _median([s["steps"] for s in settings if s["steps"] and 0 < s["steps"] <= 300])
    if steps:
        out["steps"] = int(round(steps))
    cfg = _median([s["cfg"] for s in settings if s["cfg"] is not None and 0 < s["cfg"] <= 50])
    if cfg:
        out["cfg"] = _round(cfg, 0.5)
    stated = [s["clip_skip"] for s in settings if s["clip_skip"]]
    if stated and len(stated) * 2 >= len(settings):
        clip, _n = _mode(stated)
        if clip:
            out["clip_skip"] = clip
    size, _n = _mode([(s["width"], s["height"]) for s in settings if s["width"] and s["height"]])
    if size:
        out["width"], out["height"] = size
    skip = _SUBJECT_TAGS | {tag_key(w) for w in trigger_words}
    prefix = common_tags([s["prompt"] for s in settings], PREFIX_SHARE, PREFIX_WINDOW, skip, limit=16, keep=_prefix_tag)
    if prefix:
        out["prompt_prefix"] = ", ".join(prefix)
    negative = common_tags([s["negative"] for s in settings], NEGATIVE_SHARE, None, frozenset(), limit=60)
    if negative:
        out["negative_prefix"] = ", ".join(negative)
    return out


def lora_weights(settings, sha256, version_id, names):
    """The strength this LoRA was used at in each example that says so. `names`: the lower-cased stems it may be
    called by in a prompt (its file names here and on civitai)."""
    short = (sha256 or "")[:10].lower()
    names = {n for n in names if n}
    out = []
    for s in settings:
        found = None
        for entry in s["loras"]:
            if entry["weight"] is None:
                continue
            if version_id and entry["version_id"] == version_id:
                found = entry["weight"]
                break
            if entry["hash"] and short and (entry["hash"] == short or entry["hash"] == (sha256 or "").lower()):
                found = entry["weight"]
                break
            if entry["name"] and entry["name"] in names:
                found = entry["weight"]
                break
        if found is None and s["lora_tags"] == 1 and len(s["loras"]) >= 1:
            # one LoRA tag in the prompt of this LoRA's own example: it is this one, under another name
            tagged = [e for e in s["loras"] if e.get("tag") and e["weight"] is not None]
            if len(tagged) == 1:
                found = tagged[0]["weight"]
        if found is not None and -10 <= found <= 10:
            out.append(found)
    return out


def lora_values(settings, sha256, version_id, names, trigger_words):
    out = {}
    weights = lora_weights(settings, sha256, version_id, names)
    if weights:
        out["weight"] = round(statistics.median(weights), 2)
        low, high = min(weights), max(weights)
        if low != high:
            out["weight_min"], out["weight_max"] = round(low, 2), round(high, 2)
    models = Counter(s["model"] for s in settings if s["model"])
    if models:
        out["base_models"] = [name for name, _n in models.most_common(3)]
    prompts = [s["prompt"] for s in settings if s["prompt"].strip()]
    if trigger_words and len(prompts) >= MIN_PROMPTS:
        out["trigger_words"] = [{"text": w, "required": sum(1 for p in prompts if mentions(p, w)) / len(prompts) >= 0.8}
                                for w in trigger_words]
    return out


def mentions(text, word):
    """Does the text have the word as a word of its own ("trig" is not in "trigger")?"""
    return re.search(r"(?<![\w])" + re.escape(word.lower()) + r"(?![\w])", str(text or "").lower()) is not None


def embedding_values(settings, trigger):
    """Positive or negative: where the examples put it."""
    if not trigger:
        return {}
    pos = sum(1 for s in settings if mentions(s["prompt"], trigger))
    neg = sum(1 for s in settings if mentions(s["negative"], trigger))
    if pos == neg == 0:
        return {}
    return {"use": "negative" if neg > pos else "positive"}
