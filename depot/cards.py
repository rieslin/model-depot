"""The card: what to set when generating with a model (docs/接口约定.md, "资料卡"), as Model Depot suggests it and as
the user corrects it.

A card is kept in the model's record (store.py) as layers, each a dict of field values:

    training      for a model civitai does not know: the trigger words worked out from its training tags (training.py)
    civitai       what civitai states outright: the version's trigger words
    samples       counted from the settings the author's example images were made with (card_samples.py)
    description   read from the author's notes about the version (card_text.py)
    imported      from the old WebUI's card beside the file
    user          what the user set

A field's value comes from the highest layer that has it, in the order above. The first five are drafted by the
program, and drafted again whenever what they are drafted from changes; the user layer changes only when the user
(or a program acting for the user, through PUT /api/v1/models/{sha256}/card) changes it.

    record["card"] = {"drafter": DRAFTER, "drafted_at", "layers": {...}, "evidence": {...},
                      "user_at", "confirmed_at"}
"""
import math
import re
import time

from . import card_samples, card_text, version_info

LAYERS = ("training", "civitai", "samples", "description", "imported", "user")
DRAFTED = LAYERS[:-1]
DRAFTER = 5     # goes up when the drafting changes; cards drafted by an older one are drafted again when next read
_HTML = re.compile(r"<\s*/?\s*[a-z][^>]*>", re.I)

CARD_KIND = {"checkpoint": "checkpoint", "diffusion_model": "checkpoint", "lora": "lora", "embedding": "embedding"}
FIELDS = {
    "checkpoint": ("sampler", "scheduler", "steps", "cfg", "clip_skip", "width", "height", "prompt_prefix",
                   "negative_prefix", "notes"),
    "lora": ("weight", "weight_min", "weight_max", "trigger_words", "base_models", "notes"),
    "embedding": ("use", "trigger", "notes"),
}
MAX_TEXT = 8000


class CardError(ValueError):
    """A value the card cannot take: .field names it, .code says why (unknown_field | bad_value)."""

    def __init__(self, field, code):
        super().__init__("%s: %s" % (field, code))
        self.field = field
        self.code = code


def card_kind(kind):
    return CARD_KIND.get(kind)


# ---- drafting -----------------------------------------------------------------------------------------------------
def draft(kind, sha256, version, page=None, webui_card=None, local_names=(), ai=None, training=None):
    """The five drafted layers and what they were drafted from, for a model of this kind (checkpoint, lora or
    embedding). `version`: civitai's data for the file's version (or the old WebUI's copy of it); `page`: the model
    page as civitai gives it, when it has been read; `webui_card`: the old WebUI's card; `local_names`: the file's
    names here, as a prompt might call it; `ai`: what a local language model read in the notes as they are now
    (reader.py), which then is the description layer instead of what the rules read; `training`: for a model civitai
    does not know, what its header says about its training ({"triggers": [...]}, from sniff.py)."""
    ckind = card_kind(kind)
    layers = {name: {} for name in DRAFTED}
    evidence = {"samples": {"images": 0, "with_settings": 0}, "description": {"excerpt": [], "read": []}}
    if ckind is None:
        return layers, evidence
    if ckind == "lora" and isinstance(training, dict) and training.get("triggers"):
        try:
            layers["training"]["trigger_words"] = clean_value("trigger_words", training["triggers"])
        except CardError:
            pass
    version = version if isinstance(version, dict) else {}
    fields = version_info.version_fields(version) or {}
    version_id = fields.get("version_id")
    # The version as the record keeps it (civitai's fingerprint lookup, or the download) is what the dossier shows, and
    # its example images carry their settings. The model page's copy of the version does not: civitai's model API
    # leaves the settings out of the images (20 of 20 without, checked 2026-10-02). The page adds the notes for the
    # whole model and the names of the other versions.
    source = version
    words = version_info.clean_words(source.get("trainedWords"))

    # civitai: what it states outright
    if ckind == "lora" and words:
        layers["civitai"]["trigger_words"] = [{"text": w, "required": i == 0} for i, w in enumerate(words)]
    if ckind == "embedding":
        file_types = {str((f or {}).get("type") or "").lower() for f in source.get("files") or [] if isinstance(f, dict)}
        if words:
            layers["civitai"]["trigger"] = words[0]
        if "negative" in file_types and len(file_types) == 1:
            layers["civitai"]["use"] = "negative"

    # samples: what the examples were made with
    images = [i for i in source.get("images") or [] if isinstance(i, dict)]
    settings = [s for s in (card_samples.settings_of(i) for i in images) if s]
    evidence["samples"] = {"images": len(images), "with_settings": len(settings)}
    if settings:
        if ckind == "checkpoint":
            layers["samples"] = card_samples.checkpoint_values(settings, words)
        elif ckind == "lora":
            names = {card_samples.stem(n) for n in local_names}
            names |= {card_samples.stem((f or {}).get("name")) for f in source.get("files") or [] if isinstance(f, dict)}
            layers["samples"] = card_samples.lora_values(settings, sha256, version_id, names, words)
        else:
            trigger = layers["civitai"].get("trigger") or next(iter(local_names), None)
            layers["samples"] = card_samples.embedding_values(settings, card_samples.stem(trigger) if trigger else None)

    # description: what the author wrote
    model_html = (page or {}).get("description")
    if model_html is None:
        model_html = ((version.get("model") or {}) if isinstance(version.get("model"), dict) else {}).get("description")
    other_names = [str(v.get("name") or "") for v in (page or {}).get("modelVersions") or []
                   if isinstance(v, dict) and v.get("id") != version_id]
    lines = card_text.scoped_lines(source.get("description"), model_html, fields.get("version_name"), other_names)
    values, excerpt = card_text.read(lines, ckind, card_samples.find_sampler, card_samples.comfy_scheduler)
    evidence["description"] = {"excerpt": excerpt,
                               "read": [n for n, text in (("version", source.get("description")), ("model", model_html))
                                        if text]}
    if isinstance(ai, dict):
        # The local model read the notes: its values (each found in the notes, reader.check_answer) are the layer;
        # what the rules read stays beside them for comparison.
        read = {}
        for field, value in (ai.get("values") or {}).items():
            try:
                read[field] = clean_value(field, value)
            except CardError:
                continue
        if ckind == "lora" and read.get("trigger_words") and layers["civitai"].get("trigger_words"):
            read["trigger_words"] = _merge_triggers(layers["civitai"]["trigger_words"], read["trigger_words"])
        layers["description"] = read
        evidence["description"]["reader"] = {"by": "ai", "model": ai.get("model"), "read_at": ai.get("read_at"),
                                             "seconds": ai.get("seconds"), "dropped": ai.get("dropped") or []}
        evidence["description"]["rules"] = values
    else:
        layers["description"] = values
        evidence["description"]["reader"] = {"by": "rules"}

    # imported: the old WebUI's card. Its plugin filled "activation text" with civitai's trigger words of that day
    # (joined, with "||" between groups) and "notes" with the version's notes in HTML; neither is the user's own
    # setting, so civitai's words stand, and only plain-text notes count. Checked on a real library, 2026-10-02.
    card = webui_card if isinstance(webui_card, dict) else {}
    weight = _number(card.get("preferred weight"))
    activation = str(card.get("activation text") or "").strip()
    negative = str(card.get("negative text") or "").strip()
    notes = str(card.get("notes") or "").strip()
    if ckind == "lora":
        if weight:
            layers["imported"]["weight"] = round(weight, 2)
        if activation and not words:
            tags = [t.strip() for t in activation.replace("||", ",").split(",") if t.strip()]
            layers["imported"]["trigger_words"] = [{"text": t, "required": i == 0} for i, t in enumerate(tags)]
    elif ckind == "checkpoint":
        if activation and not words:
            layers["imported"]["prompt_prefix"] = activation
        if negative:
            layers["imported"]["negative_prefix"] = negative
    elif ckind == "embedding" and activation and not words:
        layers["imported"]["trigger"] = activation
    if notes and not _HTML.search(notes):
        layers["imported"]["notes"] = notes[:MAX_TEXT]

    allowed = FIELDS[ckind]
    for name in DRAFTED:
        layers[name] = {k: v for k, v in layers[name].items() if k in allowed}
    return layers, evidence


def _word_key(text):
    return " ".join(str(text or "").lower().split()).strip(" ,")


def _merge_triggers(civitai_words, read_words):
    """civitai's trigger words, in civitai's order and spelling (the user wants civitai's), with the local model's
    judgement of which are always needed; words the notes give that civitai does not list come after them. A model
    may split "outfit,armor" into two words or join two into one: each civitai word takes the judgement made on it or
    on any of its comma-separated parts."""
    flags = {_word_key(item["text"]): item["required"] for item in read_words}
    out, covered = [], set()
    for item in civitai_words:
        keys = [_word_key(item["text"])] + [_word_key(p) for p in item["text"].split(",") if _word_key(p)]
        said = [flags[k] for k in keys if k in flags]
        covered.update(keys)
        out.append({"text": item["text"], "required": any(said) if said else item["required"]})
    out.extend(item for item in read_words if _word_key(item["text"]) not in covered)
    return out


def _number(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) and n != 0 else None


def drafted(record_card, kind, sha256, version, page, webui_card, local_names, now=None, ai=None, training=None):
    """The card with its drafted layers made anew; the user layer, the confirmation and the local model's last
    reading are kept."""
    card = dict(record_card) if isinstance(record_card, dict) else {}
    layers, evidence = draft(kind, sha256, version, page, webui_card, local_names, ai=ai, training=training)
    old = card.get("layers") if isinstance(card.get("layers"), dict) else {}
    layers["user"] = dict(old.get("user") or {})
    card.update({"drafter": DRAFTER, "drafted_at": now or time.time(), "layers": layers, "evidence": evidence})
    return card


def needs_draft(record_card):
    return not isinstance(record_card, dict) or record_card.get("drafter") != DRAFTER


# ---- reading ------------------------------------------------------------------------------------------------------
def effective(record_card, kind):
    """The card as the API gives it (docs/接口约定.md): values, the layer each comes from, and the layers."""
    ckind = card_kind(kind)
    card = record_card if isinstance(record_card, dict) else {}
    layers = card.get("layers") if isinstance(card.get("layers"), dict) else {}
    values, sources = {}, {}
    allowed = FIELDS.get(ckind, ())
    for name in LAYERS:
        for field, value in (layers.get(name) or {}).items():
            if field in allowed and value is not None and value != "" and value != []:
                values[field] = value
                sources[field] = name
    user = {k: v for k, v in (layers.get("user") or {}).items() if k in allowed}
    if user or card.get("confirmed_at"):
        state = "confirmed"
    elif values:
        state = "suggested"
    else:
        state = "empty"
    stamps = [t for t in (card.get("drafted_at"), card.get("user_at")) if t]
    return {
        "values": values, "sources": sources, "updated_at": max(stamps) if stamps else None, "state": state,
        "kind": ckind, "fields": list(allowed),
        "layers": {name: {k: v for k, v in (layers.get(name) or {}).items() if k in allowed} for name in LAYERS},
        "evidence": card.get("evidence") or {"samples": {"images": 0, "with_settings": 0},
                                             "description": {"excerpt": [], "read": []}},
        "confirmed_at": card.get("confirmed_at"),
    }


def state_of(record_card, kind):
    return effective(record_card, kind)["state"] if card_kind(kind) else "empty"


# ---- the user's changes -------------------------------------------------------------------------------------------
def _clean_text(value, field):
    if not isinstance(value, str):
        raise CardError(field, "bad_value")
    text = value.strip()
    if len(text) > MAX_TEXT:
        raise CardError(field, "bad_value")
    return text


def _clean_number(value, field, low, high, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CardError(field, "bad_value")
    if integer and value != int(value):
        raise CardError(field, "bad_value")
    if not low <= value <= high:
        raise CardError(field, "bad_value")
    return int(value) if integer else round(float(value), 3)


def _clean_name(value, field, known, translate):
    text = _clean_text(value, field).lower()
    if text in known:
        return text
    converted = translate(text)
    if converted:
        return converted
    if text and len(text) <= 48 and all(c.isalnum() or c == "_" for c in text):
        return text         # a name newer than this program's list
    raise CardError(field, "bad_value")


def clean_value(field, value):
    """A value from a request, checked and put in the card's form. Raises CardError."""
    if field in ("prompt_prefix", "negative_prefix", "notes", "trigger"):
        return _clean_text(value, field)
    if field == "sampler":
        return _clean_name(value, field, card_samples.COMFY_SAMPLERS, lambda t: card_samples.comfy_sampler(t)[0])
    if field == "scheduler":
        return _clean_name(value, field, card_samples.COMFY_SCHEDULERS, card_samples.comfy_scheduler)
    if field == "steps":
        return _clean_number(value, field, 1, 300, integer=True)
    if field == "cfg":
        return _clean_number(value, field, 0, 50)
    if field == "clip_skip":
        n = _clean_number(value, field, -24, 24, integer=True)
        if n == 0:
            raise CardError(field, "bad_value")
        return -abs(n)
    if field in ("width", "height"):
        return _clean_number(value, field, 64, 16384, integer=True)
    if field in ("weight", "weight_min", "weight_max"):
        return _clean_number(value, field, -10, 10)
    if field == "use":
        if value not in ("positive", "negative"):
            raise CardError(field, "bad_value")
        return value
    if field == "trigger_words":
        if not isinstance(value, list) or len(value) > 100:
            raise CardError(field, "bad_value")
        out = []
        for item in value:
            if isinstance(item, str):
                item = {"text": item, "required": True}
            if not isinstance(item, dict) or not isinstance(item.get("required", True), bool):
                raise CardError(field, "bad_value")
            text = _clean_text(item.get("text"), field)
            if not text or len(text) > 500:
                raise CardError(field, "bad_value")
            out.append({"text": text, "required": item.get("required", True)})
        return out
    if field == "base_models":
        if not isinstance(value, list) or len(value) > 20:
            raise CardError(field, "bad_value")
        out = [_clean_text(v, field) for v in value]
        if any(not v or len(v) > 200 for v in out):
            raise CardError(field, "bad_value")
        return out
    raise CardError(field, "unknown_field")


def with_user_values(record_card, kind, changes, confirmed=None, now=None):
    """The card with the user's changes: a value sets the field, None clears the user's value (the layers below show
    again). `confirmed` True marks the card confirmed as it stands, False takes that back. Raises CardError."""
    ckind = card_kind(kind)
    if ckind is None:
        raise CardError("kind", "no_card")
    if not isinstance(changes, dict):
        raise CardError("values", "bad_value")
    card = dict(record_card) if isinstance(record_card, dict) else {}
    layers = dict(card.get("layers") or {})
    user = dict(layers.get("user") or {})
    for field, value in changes.items():
        if field not in FIELDS[ckind]:
            raise CardError(field, "unknown_field")
        if value is None:
            user.pop(field, None)
            continue
        cleaned = clean_value(field, value)
        if cleaned in ("", []):
            user.pop(field, None)
        else:
            user[field] = cleaned
    layers["user"] = user
    card["layers"] = layers
    now = now or time.time()
    if changes:
        card["user_at"] = now
    if confirmed is True:
        card["confirmed_at"] = now
    elif confirmed is False:
        card["confirmed_at"] = None
    return card
