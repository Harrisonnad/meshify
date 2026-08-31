# Mesh Generation — Phase 1 De-risk

Status as of 2026-08-30: **TripoSR validated end-to-end.** Watertight mesh from a single
image in ~9s of actual compute (plus one-time model downloads).

## Why standalone TripoSR, not the ComfyUI node

Tried `ComfyUI-Flowty-TripoSR` first, reusing the ComfyUI venv from
[COMFYUI_SETUP.md](./COMFYUI_SETUP.md). It failed loading the checkpoint:

```
Missing key(s) in state_dict: "image_tokenizer.model.layers.0.attention.q_proj.weight", ...
Unexpected key(s) in state_dict: "image_tokenizer.model.encoder.layer.0.attention.attention.query.weight", ...
```

Root cause: the checkpoint uses the original HF ViT attention-layer naming
(`query`/`key`/`value`), but ComfyUI's venv has `transformers==5.16.1` (needed for
Z-Image-Turbo's Qwen text encoder), whose ViT implementation was refactored to
`q_proj`/`k_proj`/`v_proj`/`o_proj` naming. TripoSR's vendored `tsr` code calls
`load_state_dict` directly — a hard mismatch, not something `strict=False` papers over
cleanly.

This is exactly the failure mode [WORKER_CONTRACT.md](./WORKER_CONTRACT.md)'s one-venv-per-worker
rule exists to prevent, hit organically. Fix: gave mesh-gen its own venv entirely, at
`~/triposr`, separate from `~/comfyui` — and ran TripoSR's own standalone `run.py`, per the
master plan's original Phase 1 approach (run the model's own demo script), rather than forcing
it through ComfyUI.

## Setup

```bash
cd ~ && git clone --depth 1 https://github.com/VAST-AI-Research/TripoSR.git triposr
cd ~/triposr
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
uv pip install -r requirements.txt   # see torchmcubes gotcha below — this line alone fails
uv pip install onnxruntime           # rembg needs this; not pulled in automatically
```

### Gotcha: torchmcubes can't build on this system, at all

`requirements.txt` includes `git+https://github.com/tatsy/torchmcubes.git`, a CUDA extension
for GPU-accelerated marching cubes. It fails to build here, and — unlike every other build
issue this project has hit — **there is no flag that fixes it**:

1. First failure: CMake can't find Torch (uv builds git dependencies in an isolated env by
   default, which doesn't have our venv's installed `torch`). Fix: `--no-build-isolation`,
   plus manually pre-installing `scikit-build-core pybind11 cmake ninja` (normally auto-provided
   by build isolation).
2. Second failure: `nvcc` not found by CMake even with `CUDA_HOME` set — CMake's CUDA-language
   support needs `nvcc` on `PATH`, not just `CUDA_HOME`. Fix:
   `export PATH="/usr/local/cuda-12.6/bin:$PATH"`.
3. Third failure, the real one: **nvcc 12.6 rejects the system's GCC 15** (`gcc versions later
   than 13 are not supported`). Ubuntu 26.04 ships GCC 15 by default — same story as Python
   3.14 being too new for the ML ecosystem (see [COMFYUI_SETUP.md](./COMFYUI_SETUP.md)), now
   hitting the C++ toolchain instead of Python.
4. Tried nvcc's own escape hatch: `NVCC_APPEND_FLAGS="-allow-unsupported-compiler"` (note:
   `CMAKE_CUDA_FLAGS` does *not* reach CMake's compiler-ID probe step — the env var does).
   This bypasses the version *check*, but then hits a **harder wall**: GCC 15's actual
   `libstdc++` headers use C++ syntax nvcc 12.6's `cudafe++` preprocessor cannot parse at all
   (`0.0bf16` user-defined literals, new `__is_array` builtin usage). Not a warning — a real
   parser incompatibility.
5. No `sudo` access to install an older GCC via apt/PPA to fix this properly.

**Resolution: patched around the need for a CUDA build entirely.** `torchmcubes` is used in
exactly one place, `tsr/models/isosurface.py`, behind a single `from torchmcubes import
marching_cubes`. Patched it to fall back to `skimage.measure.marching_cubes` (pure Python/CPU,
already a dependency elsewhere in the stack) when `torchmcubes` isn't installed:

```python
try:
    from torchmcubes import marching_cubes
except ImportError:
    from skimage.measure import marching_cubes as _sk_marching_cubes

    def marching_cubes(vol, thresh: float):
        vol_np = vol.detach().cpu().numpy().astype("float32")
        verts, faces, _, _ = _sk_marching_cubes(vol_np, level=thresh)
        return torch.from_numpy(verts.copy()).float(), torch.from_numpy(faces.copy()).long()
```

Simply skip installing `torchmcubes` (drop it from `requirements.txt` before installing) and
add `uv pip install scikit-image` instead. **The CPU fallback was not slow** — mesh extraction
at resolution 256 took 2.4s, a non-issue next to the ~4s model forward pass.

**Lesson for future workers on this machine:** Ubuntu 26.04's toolchain (Python 3.14, GCC 15)
is consistently ahead of what the ML/CUDA ecosystem supports. Before reaching for a CUDA
source build, check whether a pure-Python/CPU fallback exists for that one step — it's often
faster to patch around than to fight the toolchain, especially with no `sudo` for installing
older compilers.

## Verified working

```bash
cd ~/triposr && source .venv/bin/activate
python run.py /home/blitz/comfyui/output/phase1-z-image-turbo-test_00001_.png --output-dir output/
```

Auto-downloads its own checkpoint from `stabilityai/TripoSR` (1.68 GiB) and rembg's `u2net.onnx`
(176 MiB) on first run — separate caches from the ComfyUI venv's copies, since venvs are
isolated. Output: `output/0/mesh.obj`.

Result on the Z-Image-Turbo tractor test image: **103,113 vertices, 206,234 faces, confirmed
watertight** (`trimesh.load(...).is_watertight == True`), meeting the master plan's Phase 1
exit criteria. Recognizable as a tractor when rendered (two rear wheels with tread detail,
smaller front wheels, cab/grille) — the expected single-view-inference blobbiness on
unseen/inferred surfaces, but a solid base mesh. Actual compute time (excluding one-time
downloads): background removal ~2.8s, model forward pass ~3.9s, mesh extraction ~2.4s, export
~0.3s — under 10 seconds total.

This is deliberately dense/raw output (marching cubes at resolution 256) — Phase 2c's Blender
cleanup pass (decimate, normals, UV unwrap) is what turns this into a game-ready asset, per
[README.md](../README.md)'s "Head start: Blender" section.

## Still open (see DECISIONS.md)

- InstantMesh not yet tried — TripoSR already meets the Phase 1 bar (watertight, fast, fits
  comfortably in VRAM). Worth a comparison later for quality-per-VRAM, but not blocking.
