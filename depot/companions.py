"""The companions list (companions.json): the files each base model family needs beside its model, with the official
address, ComfyUI folder and fingerprint. Some go in a sub-folder of their type's folder: the prompt enhancers in
text_encoders\\pe (the folder norm agreed on 2026-10-24), so that ComfyUI's lists tell them from the text encoders.
A file is placed by its fingerprint, or by its exact size before it has one, never by its name."""
import json
import os

from . import folders

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "companions.json")
_table = None


def table():
    global _table
    if _table is None:
        with open(_PATH, encoding="utf-8") as f:
            _table = json.load(f)
    return _table


def known(sha256):
    """True for the fingerprint of a file on the list (an official file, whatever civitai knows of it)."""
    return bool(sha256) and any(row["sha256"] == sha256 for row in table()["files"])


def subfolder(comfy_type, sha256=None, size=None):
    """The sub-folder a file of this ComfyUI type goes in, by its fingerprint or its exact size; "" when none."""
    for row in table()["files"]:
        if not row.get("subfolder") or row["directory"] != comfy_type:
            continue
        if (sha256 and row["sha256"] == sha256) or (size and row["bytes"] == size):
            return row["subfolder"]
    return ""


def norm_folder(cfg, path):
    """True for a norm sub-folder directly in a settings folder (text_encoders\\pe), which may be made on demand."""
    path = os.path.normpath(str(path or ""))
    parent = os.path.dirname(path)
    root = folders.owning_root(cfg, parent)
    names = {row["subfolder"].lower() for row in table()["files"] if row.get("subfolder")}
    return bool(root) and os.path.normcase(root["path"]) == os.path.normcase(parent) and os.path.basename(path).lower() in names
