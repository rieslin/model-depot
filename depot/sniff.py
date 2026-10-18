"""Tell what a model file is from its header, without loading it.

A .safetensors file starts with a JSON header naming every tensor and its shape. The names and shapes show the
architecture (SD1.5, SDXL, Flux, Z-Image ...) and whether the file is a whole checkpoint, a bare diffusion model, a
LoRA, a VAE or a text encoder. Training tools also leave notes in the header's metadata; those tell Pony from
Illustrious, which the tensors cannot.

.ckpt/.pt/.pth files are pickles: they are never opened here (unpickling runs code), so nothing is known about them
until civitai recognises their fingerprint.
"""
import json
import os
import struct

from . import training
from .families import UNKNOWN

MAX_HEADER_BYTES = 100 * 1024 * 1024    # the safetensors format's own limit
GGUF_PEEK_BYTES = 1024 * 1024
# Goes up when what is kept from a header changes: a file whose kept header is older is read again at the next scan
# (2: the training facts and the trigger words worked out from the training tags, 2026-10-03).
HEADER_VERSION = 3

# Metadata worth keeping in the index (short values only).
KEPT_METADATA = ("ss_base_model_version", "ss_sd_model_name", "ss_output_name", "ss_network_module", "ss_network_dim",
                 "ss_network_alpha", "ss_v2", "ss_resolution", "ss_epoch", "ss_num_epochs", "ss_num_train_images", "ss_steps",
                 "training_info", "software",       # ai-toolkit's: {"step", "epoch"} and {"name", "version"}
                 "modelspec.architecture", "modelspec.title", "modelspec.resolution", "modelspec.prediction_type")

_ADAPTER_MARKS = ("lora_down", "lora_up", "lora_A", "lora_B", "hada_w1", "hada_w2", "lokr_w1", "lokr_w2",
                  "dora_scale", "oft_blocks", "oft_diag")
_DOWN_MARKS = ("lora_down", "lora_A", "hada_w1_b", "hada_w2_b")
_CONTEXT_FAMILY = {768: "sd15", 1024: "sd2", 2048: "sdxl", 1280: "sdxl"}    # width of the text conditioning
_CHECKPOINT_PARTS = ("first_stage_model.", "vae.", "cond_stage_model.", "conditioner.", "text_encoders.")
_DIFFUSION_PREFIXES = ("model.diffusion_model.", "diffusion_model.", "net.", "")

_GGUF_FAMILY = {"flux": "flux", "sd1": "sd15", "sdxl": "sdxl", "sd3": "sd3", "lumina2": "lumina2",
                "qwen_image": "qwen", "wan": "wan", "hyvid": "hunyuan_video", "hidream": "hidream",
                "cosmos": "cosmos", "aura": "auraflow", "ltxv": "ltxv"}
_GGUF_TEXT = ("t5", "t5encoder", "llama", "clip", "qwen2", "qwen2vl", "qwen3", "gemma", "gemma2", "gemma3", "umt5")


class SniffError(Exception):
    pass


def read_safetensors(path):
    """(metadata, tensors) from a .safetensors file. tensors maps each name to {"dtype", "shape", ...}."""
    with open(path, "rb") as f:
        raw = f.read(8)
        if len(raw) != 8:
            raise SniffError("file too short")
        length = struct.unpack("<Q", raw)[0]
        if length < 2 or length > MAX_HEADER_BYTES or length + 8 > os.fstat(f.fileno()).st_size:
            raise SniffError("not a safetensors header")
        data = f.read(length)
    try:
        head = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise SniffError("header is not JSON") from e
    if not isinstance(head, dict):
        raise SniffError("header is not an object")
    metadata = head.pop("__metadata__", None)
    return (metadata if isinstance(metadata, dict) else {}), head


def _shape(tensors, key):
    entry = tensors.get(key)
    shape = entry.get("shape") if isinstance(entry, dict) else None
    return shape if isinstance(shape, list) else []


def _name_hint(text):
    """pony / illustrious from a name. Only used between families that share the SDXL architecture."""
    low = str(text or "").lower()
    if "pony" in low or "pdxl" in low:
        return "pony"
    if "illustrious" in low or "noob" in low or "ilxl" in low:
        return "illustrious"
    return None


def _sdxl_flavour(metadata):
    """Which SDXL family the training notes point at, or plain "sdxl"."""
    hint = _name_hint(metadata.get("ss_sd_model_name"))     # the checkpoint the LoRA was trained on
    if hint:
        return hint
    if "score_9" in str(metadata.get("ss_tag_frequency") or "")[:200000]:
        return "pony"       # Pony's quality tags in the training captions
    return "sdxl"


def _diffusion_family(stripped, tensors, prefix):
    """Family of a diffusion model from its tensor names (given without `prefix`), or None."""
    heads = set()
    context = None
    cosmos_like = False
    for k in stripped:
        parts = k.split(".", 2)
        heads.add(parts[0])
        if len(parts) > 1:
            heads.add(parts[0] + "." + parts[1])
        if context is None and k.endswith("attn2.to_k.weight"):
            shape = _shape(tensors, prefix + k)
            if len(shape) == 2:
                context = shape[1]
        if not cosmos_like and "adaln_modulation_cross_attn" in k:
            cosmos_like = True

    unet = ("input_blocks" in heads and ("output_blocks" in heads or "middle_block" in heads)) or \
           ("down_blocks" in heads and "up_blocks" in heads and "conv_in" in heads)
    if unet:
        return _CONTEXT_FAMILY.get(context, UNKNOWN)
    if "double_blocks" in heads or "single_blocks" in heads:
        if "double_stream_modulation_img" in heads:
            return "flux2"
        if "txt_in.individual_token_refiner" in heads:
            return "hunyuan_video"
        if "distilled_guidance_layer" in heads:
            return "chroma"
        return "flux"
    if "noise_refiner" in heads and "context_refiner" in heads:
        return "zimage" if ("x_pad_token" in heads or "cap_pad_token" in heads) else "lumina2"
    if "txtfusion" in heads:
        return "krea2"
    if cosmos_like:
        return "anima" if "llm_adapter" in heads else "cosmos"
    if "transformer_blocks" in heads and "img_in" in heads and "txt_in" in heads:
        return "qwen"
    if "joint_blocks" in heads:
        return "sd3"
    if "vid_in" in heads and "vid_out" in heads:
        return "seedvr2"
    if "head.modulation" in heads and "text_embedding" in heads:
        return "wan"
    if "double_stream_blocks" in heads and "caption_projection" in heads:
        return "hidream"
    if "double_layers" in heads and "single_layers" in heads:
        return "auraflow"
    if "t_block" in heads and "y_embedder" in heads:
        return "pixart"
    if "mlp_t5" in heads:
        return "hunyuan_dit"
    return None


def _lora_family(keys, tensors, metadata):
    base = str(metadata.get("ss_base_model_version") or "").lower()
    spec = str(metadata.get("modelspec.architecture") or "").lower()
    notes = base + " " + spec

    context = None
    flux = zimage_like = krea = qwen_like = wan_like = sdxl_names = unet_names = one_te = four_levels = te2 = False
    hidden = None
    for k in keys:
        if "double_blocks" in k or "single_blocks" in k or "single_transformer_blocks" in k:
            flux = True
        elif "txtfusion" in k or ".attn.wq." in k or "_attn_wq." in k:
            krea = True
        elif "noise_refiner" in k or "context_refiner" in k or ".layers." in k or "_layers_" in k:
            zimage_like = True
            if hidden is None and any(m in k for m in _DOWN_MARKS) and "attention" in k:
                shape = _shape(tensors, k)
                if len(shape) >= 2:
                    hidden = shape[1]
        elif "transformer_blocks" in k and ("img_mlp" in k or "txt_mlp" in k or "img_mod" in k or "txt_mod" in k
                                            or "add_q_proj" in k or "add_k_proj" in k):
            qwen_like = True
        elif "cross_attn" in k and ("blocks." in k or "blocks_" in k):
            wan_like = True
        if "lora_te2_" in k:
            te2 = True
        if "lora_unet_input_blocks" in k or "lora_unet_output_blocks" in k or "lora_unet_label_emb" in k:
            sdxl_names = True
        elif "lora_te_text_model" in k:
            one_te = True               # a single text encoder: SD 1.x or 2.x (SDXL's are lora_te1_ and lora_te2_)
        elif "lora_unet_down_blocks" in k or "lora_unet_up_blocks" in k:
            unet_names = True           # this spelling is used for SD 1.x and for SDXL alike
            if "down_blocks_3" in k or "up_blocks_3" in k:
                four_levels = True      # SD 1.x/2.x UNets have four levels, SDXL's has three
        if context is None and ("attn2_to_k" in k or "attn2.to_k" in k) and any(m in k for m in _DOWN_MARKS):
            for size in _shape(tensors, k)[1:]:
                if size in _CONTEXT_FAMILY:
                    context = size
                    break

    if flux:
        return "flux2" if ("flux2" in notes or "flux-2" in notes or "flux.2" in notes) else "flux"
    if krea or "krea" in notes:
        return "krea2"
    if "anima" in notes or "cosmos" in notes:
        return "anima" if "anima" in notes else "cosmos"
    if zimage_like:
        if "zimage" in notes or "z-image" in notes or "z_image" in notes or hidden == 3840:
            return "zimage"
        if "lumina" in notes or hidden == 2304:
            return "lumina2"
    if qwen_like or "qwen" in notes:
        return "qwen"
    if "wan" in notes and wan_like:
        return "wan"

    # SD 1.x, SD 2.x and SDXL. The width of the text conditioning settles it when the LoRA touches cross-attention;
    # LoRAs that leave it alone ("noxattn" sliders) are told apart by the names and the depth of the UNet.
    family = _CONTEXT_FAMILY.get(context)
    if family is None:
        v2 = str(metadata.get("ss_v2") or "").lower() == "true" or "sd_v2" in base or "stable-diffusion-v2" in spec
        if te2 or sdxl_names or "sdxl" in notes or "xl-v1" in notes:
            family = "sdxl"
        elif one_te or four_levels or "sd_v1" in base or "stable-diffusion-v1" in spec or v2:
            family = "sd2" if v2 else "sd15"
        elif unet_names:
            family = "sdxl"
    return family or UNKNOWN


def classify(metadata, tensors, name=""):
    """{"content", "family"} for a safetensors header, plus "hint" when only the file's name suggests a family.

    content: checkpoint | diffusion_model | lora | embedding | vae | text_encoder | controlnet | unknown

    family comes from the tensors and the training notes and can be relied on. Files of the SDXL architecture are
    refined to pony or illustrious when the notes say so; when only the name says so ("PONY\\x.safetensors"), that
    goes into "hint": a name is weak evidence (a My Little Pony LoRA for plain SDXL has "pony" in its name too).
    """
    out = _classify(metadata, tensors)
    if out["family"] == "sdxl":
        out["family"] = _sdxl_flavour(metadata)
        if out["family"] == "sdxl":
            hint = _name_hint(metadata.get("modelspec.title")) or _name_hint(name)
            if hint:
                out["hint"] = hint
    return out


def _classify(metadata, tensors):
    keys = list(tensors.keys())
    if not keys:
        return {"content": "unknown", "family": UNKNOWN}

    adapters = sum(1 for k in keys if any(m in k for m in _ADAPTER_MARKS) or k.endswith((".diff", ".diff_b")))
    if adapters * 3 >= len(keys) or keys[0].startswith(("lora_unet_", "lora_te", "lycoris_")):
        return {"content": "lora", "family": _lora_family(keys, tensors, metadata)}

    if len(keys) <= 8:
        heads = {k.split(".", 1)[0] for k in keys}
        if heads <= {"clip_l", "clip_g", "t5xxl", "emb_params", "string_to_param", "string_to_token", "name",
                     "step", "sd_checkpoint", "sd_checkpoint_name"}:
            if "clip_g" in heads:
                family = "sdxl"
            else:
                shape = _shape(tensors, "emb_params") or _shape(tensors, "clip_l")
                family = _CONTEXT_FAMILY.get(shape[-1] if shape else None, UNKNOWN)
            return {"content": "embedding", "family": family}

    for prefix in _DIFFUSION_PREFIXES:
        stripped = [k[len(prefix):] for k in keys if k.startswith(prefix)] if prefix else keys
        if not stripped:
            continue
        family = _diffusion_family(stripped, tensors, prefix)
        if family is None:
            continue
        whole = bool(prefix) and any(k.startswith(_CHECKPOINT_PARTS) for k in keys)
        return {"content": "checkpoint" if whole else "diffusion_model", "family": family}

    heads = set()
    for k in keys:
        parts = k.split(".", 2)
        heads.add(parts[0])
        if len(parts) > 1:
            heads.add(parts[0] + "." + parts[1])
    if heads & {"control_model", "input_hint_block", "controlnet_cond_embedding", "zero_convs"}:
        return {"content": "controlnet", "family": UNKNOWN}
    if heads & {"text_model", "encoder.block", "shared", "model.layers", "model.language_model", "model.embed_tokens",
                "transformer.text_model", "text_projection"}:
        return {"content": "text_encoder", "family": UNKNOWN}
    if "decoder" in heads and heads & {"decoder.conv_in", "decoder.up", "decoder.up_blocks", "decoder.upsamples",
                                       "decoder.conv1", "decoder.mid", "decoder.mid_block", "decoder.middle"}:
        return {"content": "vae", "family": UNKNOWN}
    return {"content": "unknown", "family": UNKNOWN}


def _gguf_architecture(path):
    """general.architecture of a GGUF file, or None."""
    with open(path, "rb") as f:
        buf = f.read(GGUF_PEEK_BYTES)
    if len(buf) < 24 or buf[:4] != b"GGUF":
        return None
    version = struct.unpack_from("<I", buf, 4)[0]
    if version < 2:
        return None
    count = struct.unpack_from("<Q", buf, 16)[0]
    pos = 24
    fixed = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}

    def read_string(at):
        n = struct.unpack_from("<Q", buf, at)[0]
        if n > len(buf):
            raise struct.error("string runs past the peeked bytes")
        return buf[at + 8: at + 8 + n], at + 8 + n

    try:
        for _ in range(min(count, 64)):
            key, pos = read_string(pos)
            vtype = struct.unpack_from("<I", buf, pos)[0]
            pos += 4
            if vtype == 8:
                value, pos = read_string(pos)
                if key == b"general.architecture":
                    return value.decode("utf-8", "replace")
            elif vtype == 9:
                etype, n = struct.unpack_from("<IQ", buf, pos)
                pos += 12
                if etype == 8:
                    for _ in range(n):
                        _, pos = read_string(pos)
                elif etype in fixed:
                    pos += fixed[etype] * n
                else:
                    return None
            elif vtype in fixed:
                pos += fixed[vtype]
            else:
                return None
    except struct.error:
        return None
    return None


def sniff(path, name=""):
    """What the file at `path` is: {"format", "content", "family", "meta"}, or None when its header says nothing
    (pickle files, unreadable files). `name` is the file's name inside its model folder, used only as a last hint."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".safetensors", ".sft"):
            metadata, tensors = read_safetensors(path)
            out = classify(metadata, tensors, name)
            out["format"] = "safetensors"
            out["v"] = HEADER_VERSION
            out["tensors"] = len(tensors)
            out["meta"] = {k: str(metadata[k])[:200] for k in KEPT_METADATA if metadata.get(k) not in (None, "")}
            if metadata.get("ss_tag_frequency"):
                triggers = training.suggest_triggers(metadata["ss_tag_frequency"], metadata.get("ss_output_name"))
                if triggers:
                    out["triggers"] = triggers
            return out
        if ext == ".gguf":
            architecture = _gguf_architecture(path)
            if architecture is None:
                return None
            low = architecture.lower()
            if low in _GGUF_FAMILY:
                content, family = "diffusion_model", _GGUF_FAMILY[low]
            elif low in _GGUF_TEXT:
                content, family = "text_encoder", UNKNOWN
            else:
                content, family = "unknown", UNKNOWN
            return {"format": "gguf", "content": content, "family": family, "meta": {"general.architecture": architecture}}
    except (OSError, SniffError):
        return None
    return None
