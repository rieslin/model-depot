"""Advanced settings: the technical values the program works with (timeouts, retries, thresholds, polling, limits,
service addresses). Their defaults ship in advanced.json beside this file; each computer may override any of them in
config.json under "advanced", in the same groups, with plain values:

    "advanced": {"network": {"quick_total": 20}, "reader": {"vram_gb": 16}}

Only keys the shipped file has are taken, each with the shipped value's type and within its bounds; anything else is
dropped and reported. The settings page does not show these: they are for the rare computer that needs them.
"""
import copy
import json
import os
from contextlib import contextmanager

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "advanced.json")

_spec = None        # {group: {key: {"value", "min", "max", "about"}}}, as shipped
_current = None     # {group: {key: value}}, the shipped values with this computer's overrides
_forced = []        # tests' overrides, {dotted name: value}, innermost last; they outlive use()


def _load_spec():
    global _spec
    if _spec is None:
        with open(_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        _spec = {g: {k: e for k, e in entries.items() if isinstance(e, dict) and "value" in e}
                 for g, entries in raw.items() if isinstance(entries, dict) and not g.startswith("_")}
    return _spec


def defaults():
    """{group: {key: shipped value}}."""
    return {g: {k: copy.deepcopy(e["value"]) for k, e in entries.items()} for g, entries in _load_spec().items()}


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _fits(value, entry):
    """The value as the shipped entry wants it, or None when it does not fit."""
    default = entry["value"]
    low, high = entry.get("min"), entry.get("max")

    def in_bounds(n):
        return (low is None or n >= low) and (high is None or n <= high)

    if isinstance(default, bool):
        return value if isinstance(value, bool) else None
    if _number(default):
        if not _number(value) or not in_bounds(value):
            return None
        return int(value) if isinstance(default, int) and float(value).is_integer() else value
    if isinstance(default, str):
        return value.strip() if isinstance(value, str) else None
    if isinstance(default, list):
        if not isinstance(value, list):
            return None
        if all(_number(x) for x in default):
            return list(value) if all(_number(x) and in_bounds(x) for x in value) else None
        if all(isinstance(x, str) for x in default):
            return [x.strip() for x in value] if all(isinstance(x, str) for x in value) else None
        return copy.deepcopy(value)
    if isinstance(default, dict):
        if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
            return None
        kinds = {type(v) for v in default.values()}
        if len(kinds) == 1 and kinds <= {str, int, float, bool}:
            want = kinds.pop()
            if not all(isinstance(v, want) and not (want is not bool and isinstance(v, bool)) for v in value.values()):
                return None
        return copy.deepcopy(value)
    return None


def clean(overrides):
    """(kept, problems): the overrides that fit, and (field, "advanced_bad") for each that does not."""
    spec = _load_spec()
    kept, problems = {}, []
    for group, entries in (overrides.items() if isinstance(overrides, dict) else ()):
        if group not in spec or not isinstance(entries, dict):
            problems.append(("advanced.%s" % group, "advanced_bad"))
            continue
        for key, value in entries.items():
            entry = spec[group].get(key)
            fitted = _fits(value, entry) if entry else None
            if fitted is None:
                problems.append(("advanced.%s.%s" % (group, key), "advanced_bad"))
                continue
            kept.setdefault(group, {})[key] = fitted
    return kept, problems


def use(overrides):
    """Take this computer's overrides (config.json "advanced"); the rest stays as shipped."""
    global _current
    current = defaults()
    for name, entries in clean(overrides)[0].items():
        current[name].update(entries)
    _current = current
    return current


def _now():
    return _current if _current is not None else use({})


def get(name):
    """One value by its dotted name, "network.quick_total"."""
    for layer in reversed(_forced):
        if name in layer:
            return layer[name]
    g, key = name.split(".", 1)
    return _now()[g][key]


def group(name):
    """A whole group, {key: value}, as a copy."""
    out = copy.deepcopy(_now()[name])
    for key in out:
        out[key] = get("%s.%s" % (name, key))
    return out


@contextmanager
def override(values):
    """For tests: {"network.quick_total": 0.5, ...} in force inside the block, without validation, whatever settings
    are loaded meanwhile."""
    now = _now()
    for name in values:
        g, k = name.split(".", 1)
        if k not in now.get(g, {}):
            raise KeyError(name)
    layer = dict(values)
    _forced.append(layer)
    try:
        yield
    finally:
        _forced.remove(layer)
