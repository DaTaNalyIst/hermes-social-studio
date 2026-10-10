# The Kaggle GPU studio

Everything in this folder runs inside the Kaggle notebook (`kaggle/hermes_social_studio.ipynb`):

| File | Job |
|---|---|
| `setup_studio.py` | installs and starts Ollama on GPU 0 (the brain), ComfyUI on GPU 1 (the renderer) and Hermes Agent; writes Hermes' config; installs every skill in `../skills/` |
| `studio.py` | the render command Hermes uses: `image`, `video`, `wait`, `review`, `rerun`, `film`, `deliver`, `status` |
| `serve.py` | runs Hermes' Telegram gateway, stops after N idle minutes, saves Hermes' memory |
| `studiolib/` | presets and ComfyUI workflows, on-demand weight downloads, ffmpeg tools, vision review, Chatterbox voice, film cutter |
| `voice_worker.py` | Chatterbox TTS in its own venv |
| `tests/` | `check_studio_e2e.py` (every command against fake ComfyUI/Ollama with real ffmpeg) and `check_workflows_static.py` (workflow nodes against a ComfyUI checkout) |

| Preset | Model | Use |
|---|---|---|
| `klein4b` | FLUX.2 klein 4B, 4 steps | fast image drafts |
| `zimage` | Z-Image-Turbo 6B, 8 steps | photoreal finals, readable text |
| `wan14b` | Wan 2.2 T2V A14B GGUF Q4_K_M + Lightning LoRA | default video, 832x480, 5 s |
| `wan14b_hq` | same model, 20 steps, CFG 3.5 | final video renders |
| `wan5b` | Wan 2.2 TI2V 5B fp16 | quick motion drafts |
| voice | Chatterbox Turbo / Multilingual V3 | narration, English + 22 more languages incl. Swahili |

Outputs land in `/kaggle/working/outputs/`; the `social` command turns them into posts.
