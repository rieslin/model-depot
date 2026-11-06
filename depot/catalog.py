"""Catalogs: the lists that follow the outside world rather than the program (the recommended local models, base-model
families and their spellings, ComfyUI's types and samplers, civitai's type names) and the local model's instructions.
They ship in the catalog folder beside this file. A computer may replace any of them by a file of the same name in
<data folder>\\catalog: a .json file there replaces the shipped file's top-level keys it has, a .txt file the whole text.
"""
import copy
import json
import logging
import os

log = logging.getLogger("depot.catalog")

SHIPPED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog")

_folder = None      # this computer's catalog folder (<data>\catalog), or None
_cache = {}


def use_folder(path):
    """Where this computer's own catalog files are looked for; called once at start."""
    global _folder
    _folder = path or None
    _cache.clear()


def _read_json(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.warning("catalog %s not read: %s", path, e)
        return None
    return data if isinstance(data, dict) else None


def load(name):
    """The catalog `name` (without .json) as a dict. Callers must not change it."""
    key = name + ".json"
    if key not in _cache:
        data = _read_json(os.path.join(SHIPPED, key))
        if data is None:
            raise FileNotFoundError(os.path.join(SHIPPED, key))
        own = _read_json(os.path.join(_folder, key)) if _folder else None
        if own:
            data = dict(data)
            data.update({k: v for k, v in own.items() if not k.startswith("_")})
        _cache[key] = data
    return _cache[key]


def copy_of(name):
    return copy.deepcopy(load(name))


def text(name):
    """The text catalog `name` (without .txt): this computer's own file if it has one, else the shipped one."""
    key = name + ".txt"
    if key not in _cache:
        value = None
        for folder in ([_folder] if _folder else []) + [SHIPPED]:
            try:
                with open(os.path.join(folder, key), encoding="utf-8-sig") as f:
                    value = f.read().strip("\n")
                break
            except FileNotFoundError:
                continue
        if value is None:
            raise FileNotFoundError(os.path.join(SHIPPED, key))
        _cache[key] = value
    return _cache[key]
