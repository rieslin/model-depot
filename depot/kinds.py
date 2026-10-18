"""Model types. ComfyUI's folder-type names ("checkpoints", "loras") are the vocabulary of the settings and of the API's
comfy_type; `kind` is the coarser name the API also gives ("checkpoint", "lora")."""

# The extensions ComfyUI's loaders list (folder_paths.supported_pt_extensions).
COMFY_EXTENSIONS = frozenset({".ckpt", ".pt", ".pt2", ".bin", ".pth", ".safetensors", ".pkl", ".sft"})
# Model files that ComfyUI's own loaders do not list, but that are models all the same.
EXTRA_EXTENSIONS = frozenset({".gguf", ".onnx"})
MODEL_EXTENSIONS = COMFY_EXTENSIONS | EXTRA_EXTENSIONS

LEGACY_TYPES = {"unet": "diffusion_models", "clip": "text_encoders"}   # ComfyUI's own renames

KIND_BY_TYPE = {
    "checkpoints": "checkpoint",
    "diffusion_models": "diffusion_model",
    "loras": "lora",
    "embeddings": "embedding",
    "vae": "vae",
    "text_encoders": "text_encoder",
    "upscale_models": "upscaler",
    "controlnet": "controlnet",
}
KINDS = ("checkpoint", "diffusion_model", "lora", "embedding", "vae", "text_encoder", "upscaler", "controlnet", "other")

# The types the settings page offers by name, in the order shown. Any other ComfyUI type can be typed in.
COMMON_TYPES = ("checkpoints", "diffusion_models", "loras", "embeddings", "vae", "text_encoders", "upscale_models",
                "controlnet", "clip_vision", "style_models", "gligen", "hypernetworks", "photomaker", "vae_approx",
                "latent_upscale_models", "model_patches", "audio_encoders")


def canonical_type(comfy_type):
    t = str(comfy_type or "").strip()
    return LEGACY_TYPES.get(t, t)


def kind_of(comfy_type):
    return KIND_BY_TYPE.get(canonical_type(comfy_type), "other")
