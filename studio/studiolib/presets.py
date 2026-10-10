"""Model presets and ComfyUI (API-format) workflow builders.

Every preset lists the exact Hugging Face files it needs and a builder that
returns a ComfyUI API prompt for one item. All repos are ungated, so no token
is needed. Node and input names were checked against ComfyUI v0.38.2 and
ComfyUI-GGUF (see tests/test_workflows_static.py).
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# Model files.  dest = ComfyUI models sub-folder.
# ---------------------------------------------------------------------------
HF = "https://huggingface.co"

FILES = {
    # FLUX.2 klein 4B (Apache 2.0)
    "klein_unet": ("black-forest-labs/FLUX.2-klein-4b-fp8", "flux-2-klein-4b-fp8.safetensors",
                   "diffusion_models", 4_070_624_520),
    "qwen3_4b_te": ("Comfy-Org/z_image_turbo", "split_files/text_encoders/qwen_3_4b.safetensors",
                    "text_encoders", 8_044_982_048),
    "flux2_vae": ("Comfy-Org/vae-text-encorder-for-flux-klein-4b", "split_files/vae/flux2-vae.safetensors",
                  "vae", 336_211_292),
    # Z-Image-Turbo 6B (Apache 2.0)
    "zimage_unet": ("Comfy-Org/z_image_turbo", "split_files/diffusion_models/z_image_turbo_bf16.safetensors",
                    "diffusion_models", 12_309_866_400),
    "zimage_vae": ("Comfy-Org/z_image_turbo", "split_files/vae/ae.safetensors", "vae", 335_304_388),
    # Wan 2.2 (Apache 2.0)
    "umt5": ("Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
             "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", "text_encoders", 6_735_906_897),
    "wan21_vae": ("Comfy-Org/Wan_2.2_ComfyUI_Repackaged", "split_files/vae/wan_2.1_vae.safetensors",
                  "vae", 253_815_318),
    "wan22_vae": ("Comfy-Org/Wan_2.2_ComfyUI_Repackaged", "split_files/vae/wan2.2_vae.safetensors",
                  "vae", 1_409_400_960),
    "wan5b_unet": ("Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
                   "split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors", "diffusion_models", 9_999_658_848),
    "light_high": ("lightx2v/Wan2.2-Lightning",
                   "Wan2.2-T2V-A14B-4steps-lora-rank64-Seko-V2.0/high_noise_model.safetensors", "loras", 1_226_977_424),
    "light_low": ("lightx2v/Wan2.2-Lightning",
                  "Wan2.2-T2V-A14B-4steps-lora-rank64-Seko-V2.0/low_noise_model.safetensors", "loras", 1_226_977_424),
}

WAN14_QUANT_SIZES = {"Q3_K_M": 7_174_468_096, "Q4_K_M": 9_650_090_496, "Q5_K_M": 10_790_416_896}


def wan14_files(quant: str) -> dict:
    if quant not in WAN14_QUANT_SIZES:
        raise ValueError(f"quant must be one of {sorted(WAN14_QUANT_SIZES)}")
    size = WAN14_QUANT_SIZES[quant]
    return {
        f"wan14_high_{quant}": ("QuantStack/Wan2.2-T2V-A14B-GGUF",
                                f"HighNoise/Wan2.2-T2V-A14B-HighNoise-{quant}.gguf", "unet", size),
        f"wan14_low_{quant}": ("QuantStack/Wan2.2-T2V-A14B-GGUF",
                               f"LowNoise/Wan2.2-T2V-A14B-LowNoise-{quant}.gguf", "unet", size),
    }


def local_name(spec) -> str:
    """File name as ComfyUI sees it (basename inside its models sub-folder)."""
    return spec[1].rsplit("/", 1)[-1]


def url_for(spec) -> str:
    repo, path = spec[0], spec[1]
    return f"{HF}/{repo}/resolve/main/{path}"


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------
PRESETS = {
    "klein4b": {
        "kind": "image",
        "about": "FLUX.2 klein 4B distilled, 4 steps. Fast drafts and many variations.",
        "files": ["klein_unet", "qwen3_4b_te", "flux2_vae"],
        "defaults": {"width": 1024, "height": 1024, "steps": 4, "cfg": 1.0},
        "est_seconds": 45,
    },
    "zimage": {
        "kind": "image",
        "about": "Z-Image-Turbo 6B, 8 steps. Best photorealism, people, products, food, readable text.",
        "files": ["zimage_unet", "qwen3_4b_te", "zimage_vae"],
        "defaults": {"width": 1024, "height": 1024, "steps": 8, "cfg": 1.0, "shift": 3.0},
        "est_seconds": 150,
    },
    "wan14b": {
        "kind": "video",
        "about": "Wan 2.2 T2V A14B (GGUF) + Lightning 4-step LoRA. Default video, 5 s at 16 fps.",
        "files": ["umt5", "wan21_vae", "light_high", "light_low"],
        "gguf": True,
        "defaults": {"width": 832, "height": 480, "length": 81, "fps": 16, "steps": 4, "cfg": 1.0,
                     "shift": 5.0, "quant": "Q4_K_M"},
        "max_frames": 81,
        "est_seconds": 840,
    },
    "wan14b_hq": {
        "kind": "video",
        "about": "Same Wan 2.2 A14B without the LoRA: 20 steps, CFG 3.5. Final renders only (~5x slower).",
        "files": ["umt5", "wan21_vae"],
        "gguf": True,
        "defaults": {"width": 832, "height": 480, "length": 81, "fps": 16, "steps": 20, "cfg": 3.5,
                     "shift": 8.0, "quant": "Q4_K_M"},
        "max_frames": 81,
        "est_seconds": 4200,
    },
    "wan5b": {
        "kind": "video",
        "about": "Wan 2.2 TI2V 5B fp16. Quick motion drafts, 24 fps.",
        "files": ["umt5", "wan22_vae", "wan5b_unet"],
        "defaults": {"width": 1280, "height": 704, "length": 49, "fps": 24, "steps": 20, "cfg": 5.0, "shift": 8.0},
        "max_frames": 121,
        "est_seconds": 520,
    },
}

WAN_NEGATIVE = ("blurry, low quality, jpeg artifacts, distorted hands, extra fingers, deformed face, "
                "flicker, static frame, watermark, subtitles, text overlay")


def preset_files(preset: str, quant: str | None = None) -> dict:
    p = PRESETS[preset]
    out = {k: FILES[k] for k in p["files"]}
    if p.get("gguf"):
        out.update(wan14_files(quant or p["defaults"]["quant"]))
    return out


# ---------------------------------------------------------------------------
# Item normalisation
# ---------------------------------------------------------------------------
def _snap(v: int, m: int, lo: int, hi: int) -> int:
    v = int(round(float(v) / m) * m)
    return max(lo, min(hi, v))


def normalise_item(preset: str, item: dict, defaults: dict | None = None) -> dict:
    """Merge preset defaults < job defaults < item; snap sizes to what the model accepts."""
    if preset not in PRESETS:
        raise ValueError(f"unknown preset '{preset}'. Choose one of: {', '.join(PRESETS)}")
    p = PRESETS[preset]
    it = dict(p["defaults"])
    it.update(defaults or {})
    it.update(item)
    if not str(it.get("prompt", "")).strip():
        raise ValueError(f"item {item.get('id')!r} has no prompt")
    it["seed"] = int(it.get("seed", 1)) % (2 ** 32)
    mult = 32 if preset == "wan5b" else 16
    it["width"] = _snap(it["width"], mult, 256, 2048)
    it["height"] = _snap(it["height"], mult, 256, 2048)
    if p["kind"] == "video":
        n = max(5, min(int(it["length"]), p["max_frames"]))  # longer clips do not fit a T4
        it["length"] = 4 * math.ceil((n - 1) / 4) + 1  # Wan needs 4n+1 frames
        it["fps"] = float(it.get("fps", p["defaults"]["fps"]))
    it["steps"] = int(it["steps"])
    it["cfg"] = float(it["cfg"])
    return it


# ---------------------------------------------------------------------------
# Workflow builders.  Each returns (prompt_dict, save_prefix).
# ---------------------------------------------------------------------------
class G:
    """Tiny helper to build an API-format graph."""

    def __init__(self):
        self.nodes: dict = {}
        self.n = 0

    def add(self, class_type: str, **inputs) -> str:
        self.n += 1
        nid = str(self.n)
        self.nodes[nid] = {"class_type": class_type, "inputs": inputs}
        return nid


def out(nid: str, idx: int = 0):
    return [nid, idx]


def build_klein4b(it: dict, prefix: str) -> dict:
    g = G()
    unet = g.add("UNETLoader", unet_name=local_name(FILES["klein_unet"]), weight_dtype="default")
    clip = g.add("CLIPLoader", clip_name=local_name(FILES["qwen3_4b_te"]), type="flux2", device="default")
    vae = g.add("VAELoader", vae_name=local_name(FILES["flux2_vae"]))
    pos = g.add("CLIPTextEncode", text=it["prompt"], clip=out(clip))
    neg = g.add("ConditioningZeroOut", conditioning=out(pos))
    guider = g.add("CFGGuider", model=out(unet), positive=out(pos), negative=out(neg), cfg=it["cfg"])
    noise = g.add("RandomNoise", noise_seed=it["seed"])
    sampler = g.add("KSamplerSelect", sampler_name="euler")
    sigmas = g.add("Flux2Scheduler", steps=it["steps"], width=it["width"], height=it["height"])
    latent = g.add("EmptyFlux2LatentImage", width=it["width"], height=it["height"], batch_size=1)
    sampled = g.add("SamplerCustomAdvanced", noise=out(noise), guider=out(guider), sampler=out(sampler),
                    sigmas=out(sigmas), latent_image=out(latent))
    img = g.add("VAEDecode", samples=out(sampled), vae=out(vae))
    g.add("SaveImage", images=out(img), filename_prefix=prefix)
    return g.nodes


def build_zimage(it: dict, prefix: str) -> dict:
    g = G()
    unet = g.add("UNETLoader", unet_name=local_name(FILES["zimage_unet"]), weight_dtype="default")
    shifted = g.add("ModelSamplingAuraFlow", model=out(unet), shift=float(it.get("shift", 3.0)))
    clip = g.add("CLIPLoader", clip_name=local_name(FILES["qwen3_4b_te"]), type="lumina2", device="default")
    vae = g.add("VAELoader", vae_name=local_name(FILES["zimage_vae"]))
    pos = g.add("CLIPTextEncode", text=it["prompt"], clip=out(clip))
    neg = g.add("ConditioningZeroOut", conditioning=out(pos))
    latent = g.add("EmptySD3LatentImage", width=it["width"], height=it["height"], batch_size=1)
    sampled = g.add("KSampler", model=out(shifted), seed=it["seed"], steps=it["steps"], cfg=it["cfg"],
                    sampler_name="res_multistep", scheduler="simple", positive=out(pos), negative=out(neg),
                    latent_image=out(latent), denoise=1.0)
    img = g.add("VAEDecode", samples=out(sampled), vae=out(vae))
    g.add("SaveImage", images=out(img), filename_prefix=prefix)
    return g.nodes


def _build_wan14(it: dict, prefix: str, lora: bool) -> dict:
    quant = it.get("quant", "Q4_K_M")
    f = wan14_files(quant)
    g = G()
    hi = g.add("UnetLoaderGGUF", unet_name=local_name(f[f"wan14_high_{quant}"]))
    lo = g.add("UnetLoaderGGUF", unet_name=local_name(f[f"wan14_low_{quant}"]))
    if lora:
        hi = g.add("LoraLoaderModelOnly", model=out(hi), lora_name=local_name(FILES["light_high"]),
                   strength_model=float(it.get("lora_strength", 1.0)))
        lo = g.add("LoraLoaderModelOnly", model=out(lo), lora_name=local_name(FILES["light_low"]),
                   strength_model=float(it.get("lora_strength", 1.0)))
    shift = float(it.get("shift", 5.0 if lora else 8.0))
    hi = g.add("ModelSamplingSD3", model=out(hi), shift=shift)
    lo = g.add("ModelSamplingSD3", model=out(lo), shift=shift)
    clip = g.add("CLIPLoader", clip_name=local_name(FILES["umt5"]), type="wan", device="default")
    vae = g.add("VAELoader", vae_name=local_name(FILES["wan21_vae"]))
    pos = g.add("CLIPTextEncode", text=it["prompt"], clip=out(clip))
    neg = g.add("CLIPTextEncode", text=it.get("negative") or WAN_NEGATIVE, clip=out(clip))
    latent = g.add("EmptyHunyuanLatentVideo", width=it["width"], height=it["height"], length=it["length"],
                   batch_size=1)
    steps = it["steps"]
    split = max(1, steps // 2)
    s1 = g.add("KSamplerAdvanced", model=out(hi), add_noise="enable", noise_seed=it["seed"], steps=steps,
               cfg=it["cfg"], sampler_name="euler", scheduler="simple", positive=out(pos), negative=out(neg),
               latent_image=out(latent), start_at_step=0, end_at_step=split,
               return_with_leftover_noise="enable")
    s2 = g.add("KSamplerAdvanced", model=out(lo), add_noise="disable", noise_seed=it["seed"], steps=steps,
               cfg=it["cfg"], sampler_name="euler", scheduler="simple", positive=out(pos), negative=out(neg),
               latent_image=out(s1), start_at_step=split, end_at_step=10000,
               return_with_leftover_noise="disable")
    img = g.add("VAEDecode", samples=out(s2), vae=out(vae))
    g.add("SaveImage", images=out(img), filename_prefix=prefix)
    return g.nodes


def build_wan14b(it, prefix):
    return _build_wan14(it, prefix, lora=True)


def build_wan14b_hq(it, prefix):
    return _build_wan14(it, prefix, lora=False)


def build_wan5b(it: dict, prefix: str) -> dict:
    g = G()
    unet = g.add("UNETLoader", unet_name=local_name(FILES["wan5b_unet"]), weight_dtype="default")
    shifted = g.add("ModelSamplingSD3", model=out(unet), shift=float(it.get("shift", 8.0)))
    clip = g.add("CLIPLoader", clip_name=local_name(FILES["umt5"]), type="wan", device="default")
    vae = g.add("VAELoader", vae_name=local_name(FILES["wan22_vae"]))
    pos = g.add("CLIPTextEncode", text=it["prompt"], clip=out(clip))
    neg = g.add("CLIPTextEncode", text=it.get("negative") or WAN_NEGATIVE, clip=out(clip))
    latent = g.add("Wan22ImageToVideoLatent", vae=out(vae), width=it["width"], height=it["height"],
                   length=it["length"], batch_size=1)
    sampled = g.add("KSampler", model=out(shifted), seed=it["seed"], steps=it["steps"], cfg=it["cfg"],
                    sampler_name="uni_pc", scheduler="simple", positive=out(pos), negative=out(neg),
                    latent_image=out(latent), denoise=1.0)
    img = g.add("VAEDecode", samples=out(sampled), vae=out(vae))
    g.add("SaveImage", images=out(img), filename_prefix=prefix)
    return g.nodes


BUILDERS = {
    "klein4b": build_klein4b,
    "zimage": build_zimage,
    "wan14b": build_wan14b,
    "wan14b_hq": build_wan14b_hq,
    "wan5b": build_wan5b,
}


def build(preset: str, it: dict, prefix: str) -> dict:
    return BUILDERS[preset](it, prefix)
