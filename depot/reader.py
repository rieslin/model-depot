"""Reading the authors' notes with a local language model, when one is set up; by the rules (card_text.py) otherwise.

The user's choice (2026-10-02): the notes are read by a local, uncensored Qwen model, picked by the graphics card's
memory (TIERS below). Below the lowest tier, or with no model set up, the rules read them and the dossier says so.

Two ways to reach a model, both speaking the OpenAI chat API:

  managed    Model Depot runs llama.cpp's llama-server with a GGUF file itself: started when there are notes to read,
             stopped after an idle minute, at the user's word, or as soon as another program starts taking the
             graphics card, so that the card is free for making images
  endpoint   a server the user runs (llama-swap, Ollama, LM Studio, another llama-server), known by its address and
             model name; it is neither started nor stopped here

The model is asked to copy what the author wrote (a range as "20-30", a sampler as "Euler a", a prompt as written),
not to compute anything. Its answer is then checked against the notes: every value must be found there (a trigger word
may also be one of the version's own), and is turned into the card's form here. A value that is not in the notes is
dropped: a small local model can make one up.
"""
import asyncio
import contextlib
import ctypes
import hashlib
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time

import aiohttp

from . import card_samples, card_text, cards
from . import names as names_mod

log = logging.getLogger("depot.reader")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ---- the models, by the graphics card's memory -----------------------------------------------------------------------
# Measured or taken from the files' pages on 2026-10-02 (see docs/设计草案.md, 3.5). "vram" is the least memory the tier
# is meant for, "ram" what the computer needs besides: the part of the model that does not fit on the card runs from
# memory. Each file was checked to exist at its address; sizes are the files' own.
MODELS = {
    "qwen3.6-35b-a3b": {
        "label": "Qwen3.6 35B-A3B Uncensored (Huihui, i1-Q4_K_M)", "short": "Qwen3.6 35B-A3B",
        "repo": "mradermacher/Huihui-Qwen3.6-35B-A3B-abliterated-i1-GGUF",
        "file": "Huihui-Qwen3.6-35B-A3B-abliterated.i1-Q4_K_M.gguf",
        "gb": 21.3, "vram": 8, "ram": 32,
    },
    "qwen3.8-27b": {
        "label": "Qwen3.8 27B Uncensored (orcarouter, IQ4_XS)", "short": "Qwen3.8 27B",
        "repo": "bartowski/orcarouter_Qwen3.8-27B-Uncensored-GGUF",
        "file": "orcarouter_Qwen3.8-27B-Uncensored-IQ4_XS.gguf",
        "gb": 15.6, "vram": 12, "ram": 24,
    },
    "qwen3.8-27b-q6": {
        "label": "Qwen3.8 27B Uncensored (orcarouter, Q6_K)", "short": "Qwen3.8 27B",
        "repo": "bartowski/orcarouter_Qwen3.8-27B-Uncensored-GGUF",
        "file": "orcarouter_Qwen3.8-27B-Uncensored-Q6_K.gguf",
        "gb": 23.5, "vram": 24, "ram": 16,
    },
}


def display_name(label):
    """The name the dossier shows for the model that read a model's notes: the short one ("Qwen3.8 27B") when its
    file is one of MODELS (the user's choice, 2026-10-02); any other file or server model as it is named."""
    label = str(label or "")
    for model in MODELS.values():
        if label == os.path.splitext(model["file"])[0]:
            return model["short"]
    return label
TIERS = ((24, "qwen3.8-27b-q6"), (12, "qwen3.8-27b"), (8, "qwen3.6-35b-a3b"))     # (least card memory in GB, model)
MIN_VRAM_GB = TIERS[-1][0]
# A managed server is not started while less than this share of the card is free: something else (ComfyUI making
# an image) holds it, and the model would mostly run from memory, slowly, and squeeze the other. The desktop alone
# takes 1 to 3 GB (an 8 GB card then has about 6 GB free, measured with a 12 GB card held down to that, 2026-10-02).
START_FREE_SHARE = 0.6


def start_free_mb(hardware):
    vram = max((g["vram_mb"] for g in (hardware or {}).get("gpus") or []), default=0)
    return max(4096, int(vram * START_FREE_SHARE))


def _ram_mb():
    if sys.platform == "win32":
        class _Status(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        status = _Status()
        status.dwLength = ctypes.sizeof(_Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys // (1024 * 1024))
        return None
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024))
    except (ValueError, OSError, AttributeError):
        return None


def detect_hardware():
    """{"gpus": [{"name", "vram_mb", "free_mb", "driver"}], "cuda": "13.4" or None, "ram_mb"}. Only NVIDIA cards are
    seen (through nvidia-smi); the managed server is built for them."""
    out = {"gpus": [], "cuda": None, "ram_mb": _ram_mb()}
    try:
        res = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=15,
                             creationflags=_NO_WINDOW)
        for line in res.stdout.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 4 and parts[1].isdigit():
                out["gpus"].append({"name": parts[0], "vram_mb": int(parts[1]),
                                    "free_mb": int(parts[2]) if parts[2].isdigit() else None, "driver": parts[3]})
        head = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW)
        m = re.search(r"CUDA (?:UMD )?Version:\s*([\d.]+)", head.stdout)
        out["cuda"] = m.group(1) if m else None
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return out


def free_vram_mb():
    """The free memory of the card with the most of it, in MB; None when it cannot be told. One light query: it is
    asked every few seconds while the managed server runs."""
    try:
        res = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW)
        frees = [int(line.strip()) for line in res.stdout.splitlines() if line.strip().isdigit()]
        return max(frees) if frees else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def gpu_load_pct():
    """How busy the graphics card is computing, in percent (the busiest card); None when it cannot be told."""
    try:
        res = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW)
        loads = [int(line.strip()) for line in res.stdout.splitlines() if line.strip().isdigit()]
        return max(loads) if loads else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def recommend(hardware):
    """The model for this computer (a key of MODELS), or None when its graphics card is below the lowest tier."""
    vram = max((g["vram_mb"] for g in hardware.get("gpus") or []), default=0) / 1024
    for least, key in TIERS:
        if vram >= least - 0.5:         # a "12 GB" card reports a little under 12 * 1024 MB
            return key
    return None


# ---- what is asked ---------------------------------------------------------------------------------------------------
SYSTEM = (
    "You read the notes the author of an image-generation model wrote for it on civitai, and pick out the settings the "
    "author recommends for one particular version of that model. Answer with JSON only, as the schema says.\n"
    "Rules:\n"
    "- Use only what the notes say. Never guess, never fill in from general knowledge.\n"
    "- The notes may speak of several versions. Use what is said for the version named in the request, or for every "
    "version; ignore what is said only about other versions.\n"
    "- Copy values exactly as the notes write them: a range as written (\"20-30\", \"5~7\"), a sampler or scheduler by "
    "the name the notes use, prompts word for word.\n"
    "- A setting the notes do not give for this version is null.\n"
    "- prompt_prefix is the quality or style text the author says to put at the start of the prompt; negative_prefix "
    "is the negative prompt the author recommends. Not the prompt of one example picture.\n"
    "- For a LoRA, weight is the strength the author recommends; trigger_words are the words the author says to use, "
    "each marked required when the author says it is always needed (the main trigger), not required when it is for "
    "an option (an outfit, a pose, a variant).\n"
    "- For an embedding, use says whether it goes into the prompt (positive) or the negative prompt (negative).")

_STR = {"anyOf": [{"type": "string"}, {"type": "null"}]}
SCHEMAS = {
    "checkpoint": {"type": "object", "additionalProperties": False,
                   "properties": {k: _STR for k in ("sampler", "scheduler", "steps", "cfg", "clip_skip", "size",
                                                    "prompt_prefix", "negative_prefix")},
                   "required": ["sampler", "scheduler", "steps", "cfg", "clip_skip", "size", "prompt_prefix",
                                "negative_prefix"]},
    "lora": {"type": "object", "additionalProperties": False,
             "properties": {"weight": _STR,
                            "trigger_words": {"anyOf": [{"type": "null"}, {"type": "array", "items": {
                                "type": "object", "additionalProperties": False,
                                "properties": {"text": {"type": "string"}, "required": {"type": "boolean"}},
                                "required": ["text", "required"]}}]}},
             "required": ["weight", "trigger_words"]},
    "embedding": {"type": "object", "additionalProperties": False,
                  "properties": {"use": {"anyOf": [{"enum": ["positive", "negative"]}, {"type": "null"}]},
                                 "trigger": _STR},
                  "required": ["use", "trigger"]},
}
MAX_ANSWER_TOKENS = 4096        # the longest answer asked for
MAX_VERSION_TEXT = 6000         # characters of the version's notes given to the model
MAX_MODEL_TEXT = 14000          # and of the model page's notes
_KIND_WORDS = {"checkpoint": "checkpoint (a full model)", "lora": "LoRA", "embedding": "embedding (textual inversion)"}


def _plain(html, limit):
    text = "\n".join(card_text.html_to_lines(html))
    return text if len(text) <= limit else text[:limit] + "\n[...]"


def notes_input(kind, version_name, version_html, model_html, other_names, words):
    """What the model is given, and its key: a reading is good for as long as the key stays the same."""
    ckind = cards.card_kind(kind)
    version_text = _plain(version_html, MAX_VERSION_TEXT)
    model_text = _plain(model_html, MAX_MODEL_TEXT)
    others = [n for n in other_names if n and n != version_name]
    message = (
        "Version in use: %s\nOther versions of this model: %s\nKind of model: %s\nTrigger words civitai lists for this "
        "version: %s\n\n=== Notes for this version ===\n%s\n\n=== Notes for the whole model ===\n%s" % (
            version_name or "(no name)", ", ".join(others) or "none", _KIND_WORDS.get(ckind, ckind),
            ", ".join(words) or "none", version_text or "(none)", model_text or "(none)"))
    key = hashlib.sha1(json.dumps([ckind, message, SYSTEM], ensure_ascii=False).encode("utf-8")).hexdigest()
    return {"kind": ckind, "message": message, "key": key, "has_notes": bool(version_text or model_text),
            "notes": version_text + "\n" + model_text}


# ---- what comes back, checked ----------------------------------------------------------------------------------------
def _squash(text):
    return " ".join(str(text or "").lower().replace("，", ",").split())


def _found(value, notes):
    """Is this text in the notes (spacing and case aside)?"""
    v = _squash(value)
    return bool(v) and v in _squash(notes)


_NUMBER = re.compile(r"(?<![\d.])-?\d+(?:[.,]\d+)?(?![\d])")


def _numbers(text):
    out = []
    for n in _NUMBER.findall(str(text or "")):
        try:
            out.append(float(n.replace(",", ".")))
        except ValueError:
            pass
    return out


def _range(text, low, high):
    """(low, high) of "20-30", "5~7", "0.6 to 0.8", "2,5/3" or a single number; None when it is not one."""
    values = _numbers(text)[:2]
    if not values or any(not low <= v <= high for v in values):
        return None
    return (values[0], values[-1])


def _numbers_found(text, notes):
    """Is every number of this value written in the notes ("20-30" against "Steps: 20 ~ 30")?"""
    wanted = _numbers(text)[:2]
    present = _numbers(notes)
    return bool(wanted) and all(any(abs(w - p) < 1e-6 or abs(abs(w) - p) < 1e-6 for p in present) for w in wanted)


def _samplers_in(notes):
    """The ComfyUI names of every sampler the notes mention."""
    low = " ".join(str(notes or "").lower().split())
    found = set()
    for webui, comfy in card_samples._WEBUI_SAMPLERS:        # noqa: SLF001 - the one table of sampler names
        if comfy and re.search(r"(?<![\w+])" + re.escape(webui) + r"(?![\w+])", low):
            found.add(comfy)
    for comfy in card_samples.COMFY_SAMPLERS:
        if re.search(r"(?<![\w])" + re.escape(comfy) + r"(?![\w])", low):
            found.add(comfy)
    return found


def _schedulers_in(notes):
    low = " ".join(str(notes or "").lower().replace("_", " ").split())
    found = set()
    for word, comfy in card_samples._WEBUI_SCHEDULERS:       # noqa: SLF001
        if re.search(r"(?<![\w])" + re.escape(word.replace("_", " ")) + r"(?![\w])", low):
            found.add(comfy)
    return found


# The blanks of an author's prompt template ("score_9, ..., just describe what you want, tag1, tag2"): a model may
# copy them along with the prefix (Qwen3.6 did, 2026-10-02); they are not part of it.
_PLACEHOLDER = re.compile(r"^(?:tags?\s*\d*|\.{2,}|…|etc\.?|prompt|your prompt|rest of (?:the|your) prompt|.*describe what you want.*|"
                          r"\{[^}]*\}|\[[^\]]*\]|<[^>:]*>|subject|your subject|character|your character)$", re.I)


def without_placeholders(text):
    return ", ".join(t for t in card_samples.split_tags(text) if not _PLACEHOLDER.match(t.strip()))


def _tags_found(text, notes):
    """Most of a prompt's tags must be in the notes."""
    tags = [t for t in card_samples.split_tags(text) if card_samples.tag_key(t)]
    if not tags:
        return False
    hay = _squash(notes)
    found = sum(1 for t in tags if card_samples.tag_key(t) in hay or _squash(t) in hay)
    return found >= max(1, round(len(tags) * 0.8))


def check_answer(kind, answer, notes, words):
    """The model's answer in the card's form, each value checked against the notes. Returns (values, dropped): dropped
    lists the fields whose value was not found in the notes or could not be read."""
    ckind = cards.card_kind(kind)
    answer = answer if isinstance(answer, dict) else {}
    values, dropped = {}, []

    def text_of(name):
        v = answer.get(name)
        return v.strip() if isinstance(v, str) and v.strip() else None

    if ckind == "checkpoint":
        sampler = text_of("sampler")
        if sampler:
            name, sched = card_samples.find_sampler(sampler)
            if name and name in _samplers_in(notes):
                values["sampler"] = name
                if sched and sched in _schedulers_in(notes):
                    values["scheduler"] = sched
            else:
                dropped.append("sampler")
        scheduler = text_of("scheduler")
        if scheduler:
            name = card_samples.comfy_scheduler(scheduler)
            if name and name in _schedulers_in(notes):
                values["scheduler"] = name
            else:
                dropped.append("scheduler")
        for field, low, high, as_int in (("steps", 1, 300, True), ("cfg", 0, 30, False)):
            raw = text_of(field)
            if not raw:
                continue
            span = _range(raw, low, high)
            if span and _numbers_found(raw, notes):
                mid = (span[0] + span[1]) / 2
                values[field] = int(round(mid)) if as_int else round(mid, 2)
            else:
                dropped.append(field)
        clip = text_of("clip_skip")
        if clip:
            span = _range(clip, -24, 24)
            if span and span[0] and _numbers_found(clip, notes):
                values["clip_skip"] = -abs(int(span[0]))
            else:
                dropped.append("clip_skip")
        size = text_of("size")
        if size:
            m = re.search(r"(\d{3,4})\s*[x×*]\s*(\d{3,4})", size)
            # written in the notes, and not only as a floor ("use a size larger than 1024x1024": a model read that
            # as the size, 2026-10-02)
            places = [p for p in re.finditer(r"(?<!\d)%s\s*[x×*]\s*%s(?!\d)" % (m.group(1), m.group(2)), str(notes))] if m else []
            if places and any(not card_text.NOT_SIZE.search(str(notes)[:p.start()]) for p in places):
                values["width"], values["height"] = int(m.group(1)), int(m.group(2))
            else:
                dropped.append("size")
        for field in ("prompt_prefix", "negative_prefix"):
            raw = text_of(field)
            if not raw:
                continue
            tidy = without_placeholders(card_text.tidy_tags(raw))
            if tidy and _tags_found(tidy, notes):
                values[field] = tidy[:cards.MAX_TEXT]
            else:
                dropped.append(field)
    elif ckind == "lora":
        raw = text_of("weight")
        if raw:
            span = _range(raw, -5, 5)
            if span and _numbers_found(raw, notes):
                values["weight"] = round((span[0] + span[1]) / 2, 2)
                if span[0] != span[1]:
                    values["weight_min"], values["weight_max"] = round(min(span), 2), round(max(span), 2)
            else:
                dropped.append("weight")
        given = answer.get("trigger_words")
        if isinstance(given, list):
            known = {w.lower() for w in words}
            out, seen = [], set()
            for item in given:
                if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                    continue
                text = item["text"].strip().strip(",").strip()
                if not text or text.lower() in seen:
                    continue
                if text.lower() in known or _found(text, notes):
                    seen.add(text.lower())
                    out.append({"text": text[:500], "required": bool(item.get("required"))})
                else:
                    dropped.append("trigger_words")
            if out:
                values["trigger_words"] = out
    elif ckind == "embedding":
        if answer.get("use") in ("positive", "negative"):
            values["use"] = answer["use"]
        trigger = text_of("trigger")
        if trigger:
            if trigger.lower() in {w.lower() for w in words} or _found(trigger, notes):
                values["trigger"] = trigger[:500]
            else:
                dropped.append("trigger")
    return values, sorted(set(dropped))


def parse_answer(content):
    """The JSON object a model answered, also when it wrapped it in a code fence or put words around it."""
    text = str(content or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ReaderError("bad_answer", text[:200])
    try:
        data = json.loads(m.group(0))
    except ValueError as e:
        raise ReaderError("bad_answer", text[:200]) from e
    if not isinstance(data, dict):
        raise ReaderError("bad_answer", text[:200])
    return data


class ReaderError(Exception):
    """code: not_set_up | vram_low | files_missing | unreachable | start_failed | gpu_busy | timeout | bad_answer |
    too_long | server_error"""

    def __init__(self, code, detail=""):
        super().__init__("%s %s" % (code, detail))
        self.code = code
        self.detail = detail


# ---- talking to a model ------------------------------------------------------------------------------------------------
async def ask(session, base_url, model, kind, message, api_key="", timeout=600):
    """One reading: the model's answer as a dict, the seconds it took, and the token counts when the server says."""
    # an author may list a whole prompt as trigger words (154 of them, 2026-10-06): 800 cut such an answer short
    return await ask_json(session, base_url, model, SYSTEM, message, SCHEMAS[kind], MAX_ANSWER_TOKENS, api_key, timeout)


async def ask_json(session, base_url, model, system, message, schema, max_tokens, api_key="", timeout=600):
    """Ask for one JSON object of `schema`: (answer, seconds, token counts). Raises ReaderError."""
    body = {
        "model": model or "local",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": message}],
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}},
        "chat_template_kwargs": {"enable_thinking": False},      # Qwen: answer straight away
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    started = time.monotonic()
    try:
        async with session.post(base_url.rstrip("/") + "/chat/completions", data=json.dumps(body), headers=headers,
                                timeout=aiohttp.ClientTimeout(total=timeout, sock_connect=5)) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise ReaderError("server_error", "%s %s" % (resp.status, text[:200]))
    except asyncio.TimeoutError as e:
        raise ReaderError("timeout", "no answer in %d s" % timeout) from e
    except aiohttp.ClientError as e:
        raise ReaderError("unreachable", type(e).__name__) from e
    try:
        data = json.loads(text)
        content = data["choices"][0]["message"]["content"]
        finish = data["choices"][0].get("finish_reason")
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
        raise ReaderError("bad_answer", text[:200]) from e
    if finish == "length":
        raise ReaderError("too_long", "the answer reached %d tokens" % max_tokens)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return parse_answer(content), time.monotonic() - started, usage


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ManagedServer:
    """llama-server run by Model Depot: one at a time, on a free local port, with no window."""

    def __init__(self, server, model_file, context=16384, args="", log_path=None):
        self.server = server
        self.model_file = model_file
        self.context = int(context)
        self.args = str(args or "").split()
        self.log_path = log_path
        self.proc = None
        self.base_url = None
        self._log = None

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    async def start(self, session, wait=600):
        """Start the server and wait until the model is loaded. Raises ReaderError."""
        if self.running:
            return self.base_url
        if not os.path.isfile(self.server) or not os.path.isfile(self.model_file):
            raise ReaderError("files_missing", "%s | %s" % (self.server, self.model_file))
        port = _free_port()
        cmd = [self.server, "-m", self.model_file, "--host", "127.0.0.1", "--port", str(port), "-c", str(self.context),
               "--jinja", "-np", "1"] + self.args
        if self.log_path:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            self._log = open(self.log_path, "ab")      # noqa: SIM115 - kept open while the server runs
        log.info("starting %s with %s on port %d", os.path.basename(self.server), os.path.basename(self.model_file), port)
        try:
            self.proc = subprocess.Popen(cmd, stdout=self._log or subprocess.DEVNULL, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, cwd=os.path.dirname(self.server),
                                         creationflags=_NO_WINDOW)
        except OSError as e:
            self._close_log()
            raise ReaderError("start_failed", str(e)) from e
        self.base_url = "http://127.0.0.1:%d/v1" % port
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            proc = self.proc
            if proc is None:                    # stopped while it was loading (the user asked for the card back)
                raise ReaderError("start_failed", "stopped while starting")
            if proc.poll() is not None:
                self.proc = None
                self._close_log()
                raise ReaderError("start_failed", "llama-server ended with %s" % proc.returncode)
            with contextlib.suppress(aiohttp.ClientError, asyncio.TimeoutError):
                async with session.get("http://127.0.0.1:%d/health" % port,
                                       timeout=aiohttp.ClientTimeout(total=5, sock_connect=2)) as resp:
                    if resp.status == 200:
                        return self.base_url
            await asyncio.sleep(1.0)
        await self.stop()
        raise ReaderError("start_failed", "not ready after %d s" % wait)

    async def stop(self):
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            log.info("stopping llama-server")
            if sys.platform == "win32":
                # the whole process tree: a server started through a wrapper script leaves no child behind
                def kill_tree(pid=proc.pid):
                    with contextlib.suppress(OSError, subprocess.SubprocessError):
                        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, timeout=20,
                                       creationflags=_NO_WINDOW)
                await asyncio.get_running_loop().run_in_executor(None, kill_tree)
            else:
                proc.terminate()
            for _ in range(50):
                if proc.poll() is not None:
                    break
                await asyncio.sleep(0.1)
            if proc.poll() is None:
                proc.kill()
        self._close_log()

    def _close_log(self):
        if self._log is not None:
            with contextlib.suppress(OSError):
                self._log.close()
            self._log = None


# ---- the queue ---------------------------------------------------------------------------------------------------------
ANSWER_FAILURES = ("bad_answer", "too_long")    # the model answered, unusably: asking again gets the same answer
NAME = "name:"                  # a queued key naming a model rather than reading a file's notes
GPU_WAIT_RETRY = 60.0          # seconds between looks at a graphics card that something else is using
GPU_WAIT_LIMIT = 1800.0         # after this long, a reading waiting for the card is given up (until asked again)
GUARD_INTERVAL = 2.0            # seconds between looks at the card while the managed server runs
# Two signs that another program (ComfyUI making an image, a game) wants the card, and the managed server is stopped
# for it; a stop for nothing costs only a reload later.
# - The card computes while the local model is between readings: OTHERS_LOAD_PCT or more on two looks in a row (the
#   desktop alone stays under about 20 %). Also looked at before the server starts. This is the sign that works on a
#   full card: on Windows, a program that takes memory then does not lower the card's free memory, because Windows
#   moves the idle server's memory into the computer's to make room (measured 2026-10-02: 4 GB taken, free memory
#   down by 0.2 GB only, and 3.8 GB of the server's memory out of the card).
# - The card's free memory falls GIVE_WAY_MB below what was free once the model had loaded (seen on a card with room
#   to spare). llama.cpp holds its own memory steady while it reads; the desktop alone moves by up to 0.4 GB.
OTHERS_LOAD_PCT = 30
GIVE_WAY_MB = 512


class ReaderService:
    """The notes waiting to be read, read one at a time. The library says what a model's notes are (inputs_for) and
    keeps the result (on_result); on_change is called whenever a model's reading state changes."""

    def __init__(self, cfg, log_dir, inputs_for, on_result, on_change, hardware=detect_hardware, free_mb=free_vram_mb,
                 gpu_load=gpu_load_pct, on_failed=None, name_for=None, on_name=None):
        self.cfg = cfg
        # A model's name (names.py) is settled through the same queue, as "name:<model id>": name_for(model id) gives
        # {"title"} or None, on_name(model id, answer or None) keeps the outcome (None: the answer could not be used).
        self.name_for = name_for
        self.on_name = on_name
        self.on_failed = on_failed      # (sha, {"key", "error", "at"}) when the model answered and the answer was unusable
        self.log_dir = log_dir
        self.inputs_for = inputs_for
        self.on_result = on_result
        self.on_change = on_change
        self._hardware_fn = hardware
        self._free_mb = free_mb
        self._gpu_load = gpu_load
        self._hardware = None
        self.queue = []
        self.states = {}                # sha -> {"state": queued|reading|waiting_gpu|failed, "error", "since"}
        self.server = None              # ManagedServer while one runs
        self._session = None
        self._wake = asyncio.Event()
        self._task = None
        self._stopping = False
        self._last_read = 0.0
        self._current = None            # the model whose notes are being read
        self._interrupted = None        # (sha, "stopped" | "gave_way"): the server was stopped under that reading
        self._guard_task = None         # watches the card while the managed server runs
        self._gave_way_at = None        # time.monotonic() when the server was last stopped for another program

    # ---- what this computer can do ---------------------------------------------------------------------------------
    def hardware(self, fresh=False):
        if self._hardware is None or fresh:
            self._hardware = self._hardware_fn()
        return self._hardware

    def availability(self):
        """(ready, reason, model label). reason: not_set_up | vram_low | files_missing | None."""
        reader = self.cfg.data["reader"]
        mode = reader["mode"]
        if mode == "endpoint":
            return True, None, reader["endpoint"]["model"] or reader["endpoint"]["url"]
        if mode == "managed":
            m = reader["managed"]
            label = os.path.splitext(os.path.basename(m["model_file"]))[0]
            if not (os.path.isfile(m["server"]) and os.path.isfile(m["model_file"])):
                return False, "files_missing", label
            if recommend(self.hardware()) is None:
                return False, "vram_low", label        # the user's rule: below the lowest tier, the rules only
            return True, None, label
        return False, ("vram_low" if recommend(self.hardware()) is None else "not_set_up"), None

    def status(self):
        ready, reason, label = self.availability()
        hw = self.hardware()
        return {"mode": self.cfg.data["reader"]["mode"], "ready": ready, "reason": reason, "model": label,
                "server_running": bool(self.server and self.server.running), "queued": len(self.queue),
                "idle_stop": self.cfg.data["reader"]["idle_stop"],
                "hardware": hw, "recommended": recommend(hw), "min_vram_gb": MIN_VRAM_GB}

    def state_of(self, sha):
        return self.states.get(sha)

    # ---- asking for a reading ----------------------------------------------------------------------------------------
    def request(self, sha):
        """Put a model's notes in the queue (once). False when no model can read them now."""
        ready, _reason, _label = self.availability()
        if not ready:
            return False
        current = self.states.get(sha)
        if current and current["state"] in ("queued", "reading", "waiting_gpu"):
            return True
        self.states[sha] = {"state": "queued", "error": None, "since": time.time()}
        self.queue.append(sha)
        self._wake.set()
        self.on_change()
        return True

    def pending(self):
        """How many readings of notes are still to come: queued, under way, or waiting for the graphics card."""
        return sum(1 for key, st in self.states.items()
                   if not str(key).startswith(NAME) and st.get("state") in ("queued", "reading", "waiting_gpu"))

    def drop_queued(self, shas):
        """Take these models' readings back out (filling in was stopped); one under way finishes."""
        shas = set(shas)
        for sha in shas:
            if (self.states.get(sha) or {}).get("state") in ("queued", "waiting_gpu"):
                self.states.pop(sha, None)          # a waiting one: _requeue finds it gone and leaves it
        self.queue[:] = [sha for sha in self.queue if sha not in shas]
        self.on_change()

    # ---- life cycle ----------------------------------------------------------------------------------------------------
    async def start(self):
        self._task = asyncio.ensure_future(self._run())

    async def stop(self):
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self.stop_server()
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def stop_server(self):
        guard, self._guard_task = self._guard_task, None
        if guard is not None and guard is not asyncio.current_task():
            guard.cancel()
            await asyncio.gather(guard, return_exceptions=True)
        server = self.server
        if server is not None:
            await server.stop()
            # a reading may have started a new server while this one was stopping: that one is left alone
            if self.server is server:
                self.server = None

    async def stop_now(self):
        """The user wants the graphics card back (the settings' "stop now"): the readings waiting are dropped, the one
        under way is abandoned, and the server is stopped. None of them counts as failed: each model asks again the
        next time it is opened."""
        for sha in self.queue:
            self.states.pop(sha, None)
        self.queue.clear()
        for sha in [s for s, st in self.states.items() if st.get("state") == "waiting_gpu"]:
            self.states.pop(sha, None)          # _requeue finds it gone and leaves it
        if self._current is not None:
            self._interrupted = (self._current, "stopped")
        await self.stop_server()
        self.on_change()

    def _reading_now(self):
        return self._current is not None and (self.states.get(self._current) or {}).get("state") == "reading"

    async def _guard(self, server):
        """While the managed server runs: when another program starts using the graphics card (see OTHERS_LOAD_PCT
        and GIVE_WAY_MB), the server is stopped and the card is left to it. A reading under way then waits for the
        card, as one that finds the card busy does."""
        loop = asyncio.get_running_loop()
        most_free = None
        busy_looks = 0
        while self.server is server and server.running:
            why = None
            free = await loop.run_in_executor(None, self._free_mb)
            if free is not None:
                if most_free is None or free > most_free:
                    most_free = free
                elif most_free - free >= GIVE_WAY_MB:
                    why = "%d MB free, %d before" % (free, most_free)
            # while the local model reads, the card's load is its own
            load = None if self._reading_now() else await loop.run_in_executor(None, self._gpu_load)
            busy_looks = busy_looks + 1 if load is not None and load >= OTHERS_LOAD_PCT else 0
            if why is None and busy_looks >= 2:
                why = "the card %d%% busy while the local model is idle" % load
            if why is not None and self.server is server:
                log.info("another program is using the graphics card (%s): stopping the local model", why)
                self._gave_way_at = time.monotonic()
                if self._current is not None:
                    self._interrupted = (self._current, "gave_way")
                await self.stop_server()
                self.on_change()
                return
            await asyncio.sleep(GUARD_INTERVAL)

    def reconfigure(self):
        """The settings changed: a running server may be the wrong one now."""
        self._hardware = None
        if self.server is not None:
            asyncio.ensure_future(self.stop_server())

    def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(trust_env=False)
        return self._session

    async def _run(self):
        while not self._stopping:
            idle = self.cfg.data["reader"]["idle_stop"]
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=idle if self.server is not None else None)
            except asyncio.TimeoutError:
                if self.server is not None and time.monotonic() - self._last_read >= idle:
                    log.info("no notes to read for %d s: stopping the local model", idle)
                    await self.stop_server()
                continue
            self._wake.clear()
            while self.queue and not self._stopping:
                sha = self.queue.pop(0)
                try:
                    retry = await self._read(sha)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - one bad reading must not stop the queue
                    log.exception("reading the notes of %s failed", sha[:12])
                    self.states[sha] = {"state": "failed", "error": "internal", "since": time.time()}
                    retry = False
                self.on_change()
                if retry:
                    asyncio.get_running_loop().call_later(GPU_WAIT_RETRY, self._requeue, sha)

    def _requeue(self, sha):
        if not self._stopping and self.states.get(sha, {}).get("state") == "waiting_gpu":
            self.queue.append(sha)
            self._wake.set()

    async def _base_url(self):
        """The address to ask, starting the managed server when needed. Raises ReaderError."""
        reader = self.cfg.data["reader"]
        if reader["mode"] == "endpoint":
            return reader["endpoint"]["url"], reader["endpoint"]["model"], reader["endpoint"]["key"]
        if reader["mode"] != "managed":
            raise ReaderError("not_set_up")
        m = reader["managed"]
        if self.server is None or not self.server.running or self.server.model_file != m["model_file"] \
                or self.server.server != m["server"]:
            await self.stop_server()
            # just after giving the card away, the card looks free for a moment (the server's memory is back, the
            # other program may be between steps): the readings left in the queue wait as the interrupted one does
            if self._gave_way_at is not None and time.monotonic() - self._gave_way_at < GPU_WAIT_RETRY:
                raise ReaderError("gpu_busy", "gave the card away %d s ago" % (time.monotonic() - self._gave_way_at))
            free = self._free_mb()
            if free is not None and free < start_free_mb(self.hardware()):
                raise ReaderError("gpu_busy", "%d MB free" % free)
            load = self._gpu_load()
            if load is not None and load >= OTHERS_LOAD_PCT:
                raise ReaderError("gpu_busy", "the card is %d%% busy" % load)
            self.server = ManagedServer(m["server"], m["model_file"], m["context"], m["args"],
                                        os.path.join(self.log_dir, "llama-server.log"))
            await self.server.start(self._get_session())
            self._guard_task = asyncio.ensure_future(self._guard(self.server))
        return self.server.base_url, "local", ""

    async def _read(self, sha):
        """Read one model's notes. Returns True when it is to be tried again later (the card is busy)."""
        self._current, self._interrupted = sha, None
        try:
            return await self._read_one(sha)
        finally:
            self._current = self._interrupted = None

    def _after_interruption(self, sha):
        """What becomes of a reading whose server was stopped under it (None when it was not): dropped when the user
        stopped it, as if never asked (the model asks again when opened); waiting for the card when another program
        took it. Neither counts as failed."""
        if not self._interrupted or self._interrupted[0] != sha:
            return None
        if self._interrupted[1] == "gave_way":
            now = time.time()
            self.states[sha] = {"state": "waiting_gpu", "error": None, "since": now, "waiting_since": now}
            log.info("reading the notes of %s waits: another program took the graphics card", sha[:12])
            return True
        self.states.pop(sha, None)
        log.info("reading the notes of %s dropped: the local model was stopped", sha[:12])
        return False

    async def _name_one(self, key):
        """Settle one model's name (names.py). Returns True when it is to be tried again later (the card is busy). An
        answer that came is handed on, used or not; one that could not come is asked for again the next time the names
        are looked at."""
        model_id = int(key[len(NAME):])
        inputs = self.name_for(model_id) if self.name_for else None
        ready, _reason, label = self.availability()
        if not inputs or not ready:
            self.states.pop(key, None)
            return False
        waiting_since = (self.states.get(key) or {}).get("waiting_since")
        try:
            base, model, api_key = await self._base_url()
        except ReaderError as e:
            outcome = self._after_interruption(key)
            if outcome is not None:
                return outcome
            since = waiting_since or time.time()
            if e.code == "gpu_busy" and time.time() - since < GPU_WAIT_LIMIT:
                self.states[key] = {"state": "waiting_gpu", "error": None, "since": time.time(), "waiting_since": since}
                return True
            self.states.pop(key, None)
            return False
        self.states[key] = {"state": "reading", "error": None, "since": time.time()}
        try:
            answer, seconds, _usage = await ask_json(self._get_session(), base, model, names_mod.SYSTEM,
                                                     "Title: " + inputs["title"], names_mod.SCHEMA,
                                                     names_mod.MAX_ANSWER_TOKENS, api_key, timeout=180)
        except ReaderError as e:
            outcome = self._after_interruption(key)
            if outcome is not None:
                return outcome
            self.states.pop(key, None)
            log.info("the local model could not name model %d: %s %s", model_id, e.code, e.detail)
            if e.code in ANSWER_FAILURES and self.on_name is not None:
                self.on_name(model_id, None)
            if e.code in ("unreachable", "timeout") and self.server is not None:
                await self.stop_server()
            return False
        finally:
            self._last_read = time.monotonic()
        log.info("model %d named by %s in %.1f s: %s", model_id, label, seconds, answer.get("name"))
        self.states.pop(key, None)
        if self.on_name is not None:
            self.on_name(model_id, answer)
        return False

    async def _read_one(self, sha):
        if str(sha).startswith(NAME):
            return await self._name_one(sha)
        inputs = self.inputs_for(sha)
        if inputs is None or not inputs.get("has_notes"):
            self.states.pop(sha, None)
            return False
        ready, reason, label = self.availability()
        if not ready:
            self.states[sha] = {"state": "failed", "error": reason, "since": time.time()}
            return False
        waiting_since = (self.states.get(sha) or {}).get("waiting_since")
        try:
            base, model, key = await self._base_url()
        except ReaderError as e:
            outcome = self._after_interruption(sha)
            if outcome is not None:
                return outcome
            if e.code == "gpu_busy":
                since = waiting_since or time.time()
                if time.time() - since < GPU_WAIT_LIMIT:
                    self.states[sha] = {"state": "waiting_gpu", "error": None, "since": time.time(),
                                        "waiting_since": since}
                    return True
            log.info("local model not available for %s: %s %s", sha[:12], e.code, e.detail)
            self.states[sha] = {"state": "failed", "error": e.code, "since": time.time()}
            return False
        self.states[sha] = {"state": "reading", "error": None, "since": time.time()}
        self.on_change()
        try:
            answer, seconds, usage = await ask(self._get_session(), base, model, inputs["kind"], inputs["message"],
                                               api_key=key)
        except ReaderError as e:
            outcome = self._after_interruption(sha)
            if outcome is not None:
                return outcome
            log.info("the local model could not read the notes of %s: %s %s", sha[:12], e.code, e.detail)
            self.states[sha] = {"state": "failed", "error": e.code, "since": time.time()}
            # the same notes would be answered the same way (temperature 0): kept, so that they are not asked again
            # until they change or the user asks
            if e.code in ANSWER_FAILURES and self.on_failed is not None:
                self.on_failed(sha, {"key": inputs["key"], "error": e.code, "at": time.time()})
            if e.code in ("unreachable", "timeout") and self.server is not None:
                await self.stop_server()
            return False
        finally:
            self._last_read = time.monotonic()
        values, dropped = check_answer(inputs["kind"], answer, inputs["notes"], inputs["words"])
        log.info("notes of %s read by %s in %.1f s: %s%s", sha[:12], label, seconds, sorted(values),
                 (" (not in the notes, dropped: %s)" % ", ".join(dropped)) if dropped else "")
        self.on_result(sha, {"values": values, "dropped": dropped, "answer": answer, "model": label,
                             "key": inputs["key"], "seconds": round(seconds, 1), "usage": usage,
                             "read_at": time.time()})
        self.states.pop(sha, None)
        return False
