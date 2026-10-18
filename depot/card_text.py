"""Reading the author's notes for the settings they recommend: the notes of the version, and the model page's notes.

The notes are free text in HTML. What is read here is what authors write out plainly, in English or Chinese:
"Sampler: Euler a", "Steps: 20-30", "CFG 5~7", "Clip skip 2", "weight 0.6-0.8", "Positive prompt: ...",
"Negative prompt: ...". A range is taken at its middle. Prose ("lower the CFG for close-ups") is not understood; the
lines that speak of settings are kept as an excerpt for a person to read.

The model page's notes often speak of several versions at once. A short line that names a version (a heading such as
"v2.0:" or "<h3>V3</h3>") starts a part about that version; parts about other versions are left out, a part about
this version counts like the version's own notes, and text before any such line counts for every version.
"""
import html
import re

# where a value was read, strongest first
SCOPE_VERSION, SCOPE_THIS, SCOPE_GENERAL = "version", "this", "general"
_RANK = {SCOPE_VERSION: 0, SCOPE_THIS: 1, SCOPE_GENERAL: 2}

_BLOCK_TAGS = re.compile(r"<\s*(?:br|/p|/div|/li|/h[1-6]|/tr|/blockquote|/pre|hr)\b[^>]*>|<\s*(?:li|h[1-6]|p|div|tr)\b[^>]*>",
                         re.I)
_TAG = re.compile(r"<[^>]+>")
MAX_EXCERPT_LINES = 24          # the page shows the lines in its language when the notes have two
MAX_LINE = 300


def html_to_lines(text):
    """The text of an HTML fragment, one line per paragraph, list item or heading."""
    raw = _BLOCK_TAGS.sub("\n", str(text or ""))
    raw = html.unescape(_TAG.sub("", raw)).replace(" ", " ")
    lines = []
    for line in raw.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return lines


def _norm(text):
    """A name or a heading as compared: lower case, punctuation as spaces, "v1.0" as "v1"."""
    t = re.sub(r"[^0-9a-z一-鿿]+", " ", str(text or "").lower())
    t = re.sub(r"(\d) 0\b", r"\1", t)
    return " ".join(t.split())


_LEADING_VERSION = re.compile(r"^(v\d+(?: \d+)?)\b")


def _names(version_name, other_names):
    """The forms a heading may call this version and the other versions by. A version is called by its whole name
    ("Pony (Alpha)" for "Pony Alpha", "V17" for "v17.0"); a name that starts with a version number is also called by
    that number ("V6" for "V6 (start with this one)"), unless another version is called exactly that."""
    mine_name = _norm(version_name)
    whole = {mine_name} | {_norm(n) for n in other_names}
    mine = {mine_name} if len(mine_name) >= 2 else set()
    others = {_norm(n) for n in other_names if len(_norm(n)) >= 2 and _norm(n) != mine_name}
    for name, bucket in [(mine_name, mine)] + [(_norm(n), others) for n in other_names if _norm(n) != mine_name]:
        m = _LEADING_VERSION.match(name)
        if m and m.group(1) != name and m.group(1) not in whole:
            bucket.add(m.group(1))
    return mine, others - mine


def _calls(heading, names):
    padded = " " + _norm(heading) + " "
    return any(" " + name + " " in padded for name in names)


def scoped_lines(version_html, model_html, version_name, other_names):
    """[(line, scope)] from both notes, the parts about other versions left out."""
    out = [(line, SCOPE_VERSION) for line in html_to_lines(version_html)]
    mine, others = _names(version_name, other_names)
    scope = SCOPE_GENERAL
    for line in html_to_lines(model_html):
        if len(line) <= 80 and (others or mine):
            names_me, names_other = _calls(line, mine), _calls(line, others)
            if names_me or names_other:
                scope = SCOPE_THIS if names_me else None
                if scope is None:
                    continue
        if scope is not None:
            out.append((line, scope))
    return out


# ---- the patterns -------------------------------------------------------------------------------------------------
_SEP = r"\s*(?:[:：=]|\bis\b|\bof\b|\bat\b)?\s*"
_RANGE = r"(?:\s*(?:-|~|–|—|to|至|到|/)\s*({num}))?"
_INT = r"\d{1,3}"
_DEC = r"\d{1,2}(?:[.,]\d{1,2})?(?!\d)"      # "2,5" is two and a half, as many authors write it
_WEIGHT_NUM = r"-?(?:\d?[.,]\d{1,2}(?!\d)|\d)"

_NUM_START = r"(?<![\d.])"
_PATTERNS = {
    "steps": [re.compile(r"(?<!hires )(?<!hires\. )(?<!hires fix )(?:sampling )?(?:steps?|步数|迭代步数|采样步数)" + _SEP +
                         _NUM_START + "(" + _INT + r")(?!\d)" + _RANGE.format(num=_INT), re.I),
              re.compile(_NUM_START + "(" + _INT + ")" + _RANGE.format(num=_INT) + r"\s*(?:sampling\s*)?steps\b", re.I)],
    "cfg": [re.compile(r"(?:cfg(?:\s*scale)?|guidance(?:\s*scale)?|提示词引导系数|引导系数)" + _SEP + _NUM_START + "(" + _DEC + ")" +
                       _RANGE.format(num=_DEC), re.I),
            re.compile(_NUM_START + "(" + _DEC + ")" + _RANGE.format(num=_DEC) + r"\s*cfg\b", re.I)],
    "clip_skip": [re.compile(r"clip\s*-?\s*skip" + _SEP + r"(\d{1,2})", re.I),
                  re.compile(r"(?:停止在\s*clip\s*层|clip\s*跳过(?:层)?)" + _SEP + r"-?(\d{1,2})", re.I)],
    "weight": [re.compile(r"(?:(?:lora|model|recommended|suggested|best|推荐|建议)\s*)?(?:weights?|strength|权重|强度)" +
                          _SEP + _NUM_START + "(" + _WEIGHT_NUM + ")" + _RANGE.format(num=_WEIGHT_NUM), re.I),
               re.compile(r"<lora:[^:>]+:(" + _WEIGHT_NUM + r")>", re.I)],
}
# A number counts as a recommendation when it is written as a setting ("Weight: 0.8", "CFG=4", a line that starts with
# the setting) or when the sentence recommends ("suggested weight 0.7", "works best at 0.6"). Prose that merely
# mentions one ("it burns at weight 1", "strength 0-1 over three seeds") does not count.
_CUE = re.compile(r"recommend|suggest|\bbest\b|works?\s+(?:well|fine|great|best)|optimal|ideal|usage|\buse\b|"
                  r"\bsettings?\b|推荐|建议|最佳|适合|用法|使用|设置", re.I)
_BULLET = " \t-–•*·>#)(.0123456789"
_SAMPLER_LINE = re.compile(r"(?:sampl(?:er|ing method)s?|采样器|采样方法)" + _SEP + r"(.+)", re.I)
_SCHEDULER_LINE = re.compile(r"(?:scheduler|schedule type|调度器)" + _SEP + r"([A-Za-z_ ]+)", re.I)
_SIZE_LINE = re.compile(r"(?:resolution|image size|size|width|dimensions?|分辨率|尺寸)", re.I)
_SIZE = re.compile(r"(\d{3,4})\s*[x×*]\s*(\d{3,4})")
NOT_SIZE = re.compile(r"(?:larger|bigger|greater|higher|more|above|over|at least|超过|大于|以上)\s*(?:than)?\s*$", re.I)
_PROMPT_HEAD = r"^\s*(?:recommended\s+|suggested\s+|推荐(?:的)?\s*)?"
_POSITIVE = re.compile(_PROMPT_HEAD + r"(?:positive(?:\s+prompts?)?|quality\s+(?:tags?|prompts?)|prompt\s+prefix|正向提示词|"
                       r"正面提示词|质量词|正向)\s*(?:[:：]\s*(.*))?$", re.I)
_NEGATIVE = re.compile(_PROMPT_HEAD + r"(?:negative(?:\s+prompts?)?|neg(?:ative)?\s+tags?|负面提示词|反向提示词|负向提示词|"
                       r"负面词|负面)\s*(?:[:：]\s*(.*))?$", re.I)
_NOT_WEIGHT = re.compile(r"cfg|denois|hires|guidance|steps?\b|采样|步数", re.I)
_KEYWORDS = re.compile(r"sampl|\bsteps?\b|cfg|clip\s*skip|resolution|\bsize\b|width|weight|strength|negative prompt|"
                       r"positive prompt|trigger|hires|upscal|denois|\bvae\b|scheduler|采样|步数|权重|强度|分辨率|尺寸|提示词|触发词|"
                       r"放大|重绘", re.I)


def _float(text):
    return float(str(text).replace(",", "."))


def _mid(low, high, as_int):
    if high is None:
        value = _float(low)
    else:
        value = (_float(low) + _float(high)) / 2
    return int(round(value)) if as_int else round(value, 2)


def tidy_tags(text):
    """A prompt as the author wrote it, with the commas tidied: ",masterpiece,best quality," becomes
    "masterpiece, best quality"."""
    return ", ".join(t.strip() for t in str(text).strip().strip("\"'“”").split(",") if t.strip())


def _span(m):
    low = m.group(1)
    high = m.group(2) if m.lastindex and m.lastindex >= 2 else None
    return low, high


def _stated(line, m):
    """Is the number this match found given as a setting, rather than mentioned in passing?"""
    if any(sep in m.group(0) for sep in (":", "：", "=")):
        return True
    if not line[:m.start()].strip(_BULLET):
        return True
    return bool(_CUE.search(line))


def read(lines, kind, find_sampler, comfy_scheduler):
    """The settings the notes state, and the excerpt. `lines` from scoped_lines; `kind` is checkpoint, lora or
    embedding; the two functions turn sampler and scheduler names into ComfyUI's (card_samples)."""
    found = {}          # field -> (rank, value)

    def put(field, value, scope):
        if value is None:
            return
        rank = _RANK[scope]
        if field not in found or rank < found[field][0]:
            found[field] = (rank, value)

    pending = None      # a "Negative prompt:" line whose text is on the next line
    for line, scope in lines:
        if pending:
            heading_next = _POSITIVE.match(line) or _NEGATIVE.match(line)
            field, pending = pending, None
            if not heading_next:            # "Positive Prompt" right before "Negative Prompt": it had no prompt
                put(field, tidy_tags(line) or None, scope)
                continue
        if kind == "checkpoint":
            for field, pattern in (("prompt_prefix", _POSITIVE), ("negative_prefix", _NEGATIVE)):
                m = pattern.match(line)
                if m:
                    text = tidy_tags(m.group(1) or "")
                    if text:
                        put(field, text, scope)
                    else:
                        pending = field
            m = _SAMPLER_LINE.search(line)
            if m:
                sampler, scheduler = find_sampler(m.group(1))
                put("sampler", sampler, scope)
                put("scheduler", scheduler, scope)
            m = _SCHEDULER_LINE.search(line)
            if m:
                put("scheduler", comfy_scheduler(m.group(1).strip()), scope)
            for field, as_int in (("steps", True), ("cfg", False)):
                for pattern in _PATTERNS[field]:
                    m = pattern.search(line)
                    if m and _stated(line, m):
                        low, high = _span(m)
                        value = _mid(low, high, as_int)
                        if (field == "steps" and 1 <= value <= 300) or (field == "cfg" and 0 < value <= 30):
                            put(field, value, scope)
                        break
            for pattern in _PATTERNS["clip_skip"]:
                m = pattern.search(line)
                if m and 1 <= int(m.group(1)) <= 24:
                    put("clip_skip", -int(m.group(1)), scope)
                    break
            if _SIZE_LINE.search(line):
                m = _SIZE.search(line)
                if m and not NOT_SIZE.search(line[:m.start()]):     # "larger than 1024x1024" is not a size
                    put("width", int(m.group(1)), scope)
                    put("height", int(m.group(2)), scope)
        elif kind == "lora":
            for pattern in _PATTERNS["weight"]:
                m = pattern.search(line)
                if not m:
                    continue
                if pattern is _PATTERNS["weight"][0] and (_NOT_WEIGHT.search(line[max(0, m.start() - 12):m.end()])
                                                          or not _stated(line, m)):
                    continue
                low, high = _span(m)
                try:
                    lo, hi = _float(low), _float(high) if high is not None else None
                except ValueError:
                    continue
                if not -5 <= lo <= 5 or (hi is not None and not -5 <= hi <= 5):
                    continue
                put("weight", _mid(low, high, False), scope)
                if hi is not None and hi != lo:
                    put("weight_min", round(min(lo, hi), 2), scope)
                    put("weight_max", round(max(lo, hi), 2), scope)
                break
        elif kind == "embedding":
            if re.search(r"negative|负面|反向", line, re.I) and re.search(r"\b(?:use|put|add|in)\b|使用|放在|放入|加入", line, re.I):
                put("use", "negative", scope)
    values = {field: value for field, (_rank, value) in found.items()}
    return values, excerpt(lines)


def excerpt(lines):
    """The lines that speak of settings, for a person to read: the version's notes first. A "Negative Prompt" line
    whose prompt is on the next line is given with it."""
    joined = []
    for i, (line, scope) in enumerate(lines):
        heading = _POSITIVE.match(line) or _NEGATIVE.match(line)
        if heading and not (heading.group(1) or "").strip() and i + 1 < len(lines) and lines[i + 1][1] == scope:
            joined.append((line.rstrip(" :：") + ": " + lines[i + 1][0], scope))
        else:
            joined.append((line, scope))
    out, seen = [], set()
    for line, _scope in sorted(joined, key=lambda item: _RANK[item[1]]):
        if not _KEYWORDS.search(line):
            continue
        text = line if len(line) <= MAX_LINE else line[:MAX_LINE - 1] + "…"
        if text.lower() in seen:
            continue
        seen.add(text.lower())
        out.append(text)
        if len(out) >= MAX_EXCERPT_LINES:
            break
    return out
