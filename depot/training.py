"""What a training tool wrote into a model's header (kohya's sd-scripts and the tools that follow its "ss_" names), for
a model civitai does not know: the user's own LoRAs, mostly.

sniff.py keeps the short fields in the index and works out the trigger words here while it still has the whole
header (the tag counts can run to megabytes); the library builds the default dossier from them: a name and version,
the facts of the training, and the trigger words as a suggestion the user can change.
"""
import json
import re

# Tags that describe almost any picture of a person, never what a LoRA was trained to call up.
_GENERIC = frozenset("""
1girl 1boy 2girls 2boys 3girls multiple_girls multiple_boys solo solo_focus male_focus female_focus girl boy woman man
female male person people human realistic photorealistic photo real looking_at_viewer smile long_hair short_hair
medium_hair black_hair brown_hair blonde_hair white_hair grey_hair red_hair blue_hair closed_eyes open_mouth closed_mouth
simple_background white_background grey_background upper_body full_body cowboy_shot portrait close-up standing sitting
lying underwear nude outdoors indoors jewelry earrings necklace lips teeth breasts small_breasts medium_breasts
large_breasts shirt dress jacket skirt pants shorts black_eyes brown_eyes blue_eyes day night sky tree depth_of_field
blurry blurry_background from_side from_behind from_above from_below hand_up holding bangs asian chinese japanese
korean looking_at_another looking_away parted_lips blush makeup
""".split())

# The facts shown in the dossier's "training" section, from the fields sniff.py keeps.
FACT_FIELDS = (("base_checkpoint", "ss_sd_model_name"), ("resolution", "ss_resolution"), ("epoch", "ss_epoch"),
               ("epochs", "ss_num_epochs"), ("steps", "ss_steps"), ("images", "ss_num_train_images"),
               ("network_dim", "ss_network_dim"), ("network_alpha", "ss_network_alpha"))


def _norm(tag):
    return re.sub(r"\s+", " ", str(tag or "").replace("_", " ")).strip().lower()


def _key(text):
    return re.sub(r"[^0-9a-z]", "", str(text or "").lower())


def _first_word(tag):
    words = re.split(r"[\s_,]+", str(tag or "").strip())
    return words[0] if words else ""


def _shared_prefix(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _related(tag, stem):
    """Is the tag's first word the name the model was trained under (or its start: "bbqin" for "bbqiXL")?"""
    first = _key(_first_word(tag))
    if len(first) < 3 or not stem:
        return False
    shared = _shared_prefix(first, stem)
    return shared >= 3 and shared >= 0.6 * len(first)


def tag_counts(tag_frequency):
    """{tag: count} over every training folder, from ss_tag_frequency (a JSON text or a dict). Folder names of the
    "10_name class" form also count: with no captions, the folder name is the prompt every picture was trained with."""
    data = tag_frequency
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return {}
    if not isinstance(data, dict):
        return {}
    counts = {}
    biggest = 0
    for folder, tags in data.items():
        if not isinstance(tags, dict):
            continue
        for tag, count in tags.items():
            text = re.sub(r"\s+", " ", str(tag)).strip(" ,")
            if text and isinstance(count, (int, float)):
                counts[text] = counts.get(text, 0) + int(count)
                biggest = max(biggest, counts[text])
    for folder in data:
        name = re.sub(r"^\d+_", "", str(folder)).strip()
        if name and name != str(folder) and name not in counts:
            counts[name] = biggest or 1
    return counts


def suggest_triggers(tag_frequency, output_name, limit=2):
    """The words a prompt should carry to call up what the model learned: the most frequent training tag whose first
    word is the name the model was trained under (a long caption gives only that word), else a tag on nearly every
    picture that is not a generic one. [{"text", "required"}], the first required; [] when nothing stands out."""
    counts = tag_counts(tag_frequency)
    if not counts:
        return []
    stem = _key(output_name)
    usable = {t: c for t, c in counts.items() if _norm(t).replace(" ", "_") not in _GENERIC}
    ranked = sorted(usable.items(), key=lambda kv: (-kv[1], kv[0]))
    picks = []
    for tag, _count in ranked:
        if not _related(tag, stem):
            continue
        text = tag if len(tag.split()) <= 3 else _first_word(tag)
        if all(_norm(text) != _norm(p) for p in picks):
            picks.append(text)
        if len(picks) >= limit:
            break
    if not picks and ranked:
        top = max(counts.values())
        tag, count = ranked[0]
        if count >= 0.9 * top and len(tag.split()) <= 3:
            picks.append(tag)
    return [{"text": t, "required": i == 0} for i, t in enumerate(picks)]


def title_version(stem, output_name):
    """(title, version) for a file named `stem` that was saved under `output_name`: "mychar2-000200" with the output
    name "mychar2" is ("mychar2", "000200"); a file whose name does not start with the output name keeps its own name
    (an output name like "20260110-202416" says nothing). (None, None) when there is nothing to go on."""
    out = str(output_name or "").strip()
    stem = str(stem or "").strip()
    if not out or not stem:
        return None, None
    if stem.lower() == out.lower():
        return out, None
    if stem.lower().startswith(out.lower()):
        rest = stem[len(out):].lstrip(" -_.")
        if rest and re.match(r"^[0-9][0-9_\-.]*$", rest):
            return out, rest
    return None, None


def _json_field(meta, field):
    try:
        value = json.loads(meta.get(field) or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


_MARKED_STEPS = re.compile(r"(?:^|[^a-z])(?:steps?|stp)[\s_\-=]*(\d+)", re.I)
_TRAILING_NUMBER = re.compile(r"[\s_\-](\d{3,})$")


def steps_from_name(stem, recorded=None):
    """The training steps a file's name states, or None. The user writes the real number of steps into the names of
    their own LoRAs ("mychar z3_12000", "..._stp8000_loss1657"): the training tools record optimizer steps, which a
    batch of 8 makes 8 times fewer (that file records 1500). A number after "step" or "stp" is taken as it is; a number
    ending the name only when it is a whole multiple of the recorded steps, so that an epoch, a loss or a version at
    the end of a name is not read as steps."""
    stem = str(stem or "")
    m = _MARKED_STEPS.search(stem)
    if m:
        return int(m.group(1))
    m = _TRAILING_NUMBER.search(stem)
    if m and not m.group(1).startswith("0") and recorded:
        n = int(m.group(1))
        if n >= recorded and n % recorded == 0:
            return n
    return None


def facts(meta, stem=None):
    """The training facts the dossier shows, from the header fields sniff.py keeps and the file's name `stem`:
    {name: text}. kohya's sd-scripts write them as "ss_" fields; ai-toolkit writes the step and epoch as JSON in
    "training_info", and its own name and version in "software" (its "ss_" fields are only the output name and the base
    model). The steps are those the file's name states when it does (steps_from_name), else those recorded."""
    meta = meta if isinstance(meta, dict) else {}
    meta = dict(meta)
    info, software = _json_field(meta, "training_info"), _json_field(meta, "software")
    if not meta.get("ss_epoch") and isinstance(info.get("epoch"), int) and info["epoch"] > 0:
        meta["ss_epoch"] = str(info["epoch"])
    if not meta.get("ss_steps") and isinstance(info.get("step"), int) and info["step"] > 0:
        meta["ss_steps"] = str(info["step"])
    try:
        recorded = int(float(meta.get("ss_steps") or 0))
    except ValueError:
        recorded = 0
    named = steps_from_name(stem, recorded or None)
    if named:
        meta["ss_steps"] = str(named)
    out = {}
    if software.get("name"):
        out["tool"] = " ".join(str(software[k]) for k in ("name", "version") if software.get(k))
    for name, field in FACT_FIELDS:
        value = str(meta.get(field) or "").strip()
        if not value or value.lower() in ("none", "null"):
            continue
        if name == "resolution":
            sizes = re.findall(r"\d+", value)
            value = " × ".join(sizes[:2]) if len(sizes) >= 2 else value
        elif re.match(r"^\d+\.0+$", value):
            value = value.split(".")[0]          # "16.0": the tool wrote a whole number as a decimal
        out[name] = value
    return out
