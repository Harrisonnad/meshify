# ComfyUI Setup — Phase 1 De-risk

Status as of 2026-08-30: **First working text-to-image generation on Turing.** Z-Image-Turbo
(int8 + fp8) produces clean 1024x1024 output in ~38s.

## Why ComfyUI first

Per [DECISIONS.md](./DECISIONS.md), we're taking the master plan's §10 hybrid: validate the
full image→mesh chain in ComfyUI to learn what actually runs on this Turing card before
building venv + FastAPI wrappers around any survivor.

## Where it lives

`~/comfyui` inside the WSL2 Ubuntu distro (**not** under `/mnt/c/` — model weights are
multi-GB and cross-OS file access in WSL2 is dramatically slower). Not tracked in this git
repo; it's a throwaway validation harness, not a project dependency.

## Python: uv, not apt

Ubuntu 26.04's own repos only ship Python 3.14 and no `pip` — too new for the current
PyTorch/ComfyUI/mesh-gen ecosystem, which commonly pins 3.10–3.12. Getting an older
interpreter via `apt`/deadsnakes would need a PPA + `sudo`, and interactive `sudo` password
prompts can't be driven non-interactively.

Used [uv](https://astral.sh/uv) instead — installs to `~/.local/bin` with no sudo, and
fetches portable standalone Python builds:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env
uv python install 3.11
```

Keeps the "one venv per worker, right interpreter per worker" rule from
[WORKER_CONTRACT.md](./WORKER_CONTRACT.md) — `uv` will manage separate interpreters/venvs per
worker without touching the system Python.

## Install

```bash
cd ~ && git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git comfyui
cd ~/comfyui
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
uv pip install -r requirements.txt
```

CUDA 12.6 chosen to match the toolkit installed in [WSL2_SETUP.md](./WSL2_SETUP.md) — see
that doc for why 12.6 over the newer 12.8/12.9/13.x options.

### Gotcha: torchaudio CUDA mismatch

`requirements.txt` pulls an unpinned `torchaudio`, which resolved to a build compiled against
CUDA 13 — incompatible with our CUDA 12.6 `torch`. Symptom: `OSError: libcudart.so.13: cannot
open shared object file`, crashing on ComfyUI startup (it unconditionally imports torchaudio
via `comfy/ldm/lightricks/vae/audio_vae.py`). Fix — force a matching build from the same
index:

```bash
uv pip install --reinstall --index-url https://download.pytorch.org/whl/cu126 torchaudio
```

`uv pip install torchaudio==<X>` without `--reinstall` looks like it works but is a no-op if
some version is already installed and satisfies the unpinned requirement — it won't swap the
CUDA build. `--reinstall` is what actually forces the correct wheel.

## Verified working

```bash
cd ~/comfyui && source .venv/bin/activate
python main.py --listen 127.0.0.1 --port 8188
```

Log confirms: `Device: cuda:0 NVIDIA GeForce RTX 2070`, `pytorch version: 2.13.0+cu126`,
`Using pytorch attention`, server starts at `http://127.0.0.1:8188`. Windows 11 auto-forwards
WSL2 localhost ports, so `http://localhost:8188` reaches it from a Windows browser too.

`comfy_kitchen`'s fused CUDA/triton backends log a warning wanting cu130+ — that's their
newer optimized kernel path, disabled here, **not** the FlashAttention-2/SM-8.0 blocker from
[hardware.md](./hardware.md). ComfyUI falls back to the `eager`/pytorch-attention backend and
runs fine on Turing; this is a performance ceiling, not a functionality one.

## Image model: Z-Image-Turbo — working

Chose Z-Image-Turbo over Qwen-Image 2.0 (lighter/faster, safer for 8 GB — see
[DECISIONS.md](./DECISIONS.md)). Files, all from `Comfy-Org/z_image_turbo` on Hugging Face
(`split_files/`):

| File | Folder | Size |
|---|---|---|
| `z_image_turbo_int8_convrot.safetensors` | `diffusion_models/` | 5.8 GiB |
| `qwen_3_4b_fp8_mixed.safetensors` | `text_encoders/` | 5.3 GiB |
| `ae.safetensors` | `vae/` | 320 MiB |

Skipped the `nvfp4` diffusion variant (4.5 GiB, smaller) — that quantization format targets
Blackwell-generation tensor cores and is unproven on Turing; `int8` is the well-trodden path.

The exact workflow graph ships in the `comfyui-workflow-templates` pip package (already
installed as a ComfyUI dependency) at
`.venv/lib/python3.11/site-packages/comfyui_workflow_templates_json/templates/image_z_image_turbo_int8.json`
— useful reference for node wiring any time this needs rebuilding. Key pieces: `UNETLoader` +
`CLIPLoader` (type `lumina2`) + `VAELoader`, `CLIPTextEncode` → `ConditioningZeroOut` for the
negative (turbo/distilled models don't need a real negative prompt), `ModelSamplingAuraFlow`
shift `3`, `KSampler` at `steps=8, cfg=1, sampler_name=res_multistep, scheduler=simple`.

**Verified:** ran via the `/prompt` HTTP API (not just the browser GUI) with a 1024x1024
prompt — completed in ~38s cold (model load + sampling), no OOM, output was sharp and clean
on a plain background.

## Still open (see DECISIONS.md)

- Which mesh generator: InstantMesh vs TripoSR (quality-per-VRAM, needs testing) — next step,
  feeding this image model's output into a background-removal step and then the mesh
  generator.
