# Decisions

Tracking §11 of the master plan. Settled decisions carry a date and rationale.

## Settled 2026-08-22

### GPU / VRAM — settled by measurement
RTX 2070, 8 GB, compute capability 7.5 (Turing). See [hardware.md](./hardware.md).
Bottom tier of the plan's model table. TRELLIS 2 and Hunyuan3D are out; InstantMesh /
TripoSR are in. The Turing compute-capability floor is the binding constraint, not the
VRAM number — FlashAttention-2 requires SM 8.0+.

### Phase 1 approach — ComfyUI de-risk first
Take §10's hybrid path. Validate the full chain in ComfyUI to learn which models actually
run on Turing, *then* build venv + FastAPI wrappers around only the survivors. The plan
offers this as a de-risk for exactly our situation; on this hardware it is the entry route,
not the fallback.

### OS — install WSL2 with CUDA passthrough
Native Windows costs days on flash-attention and CUDA-extension builds. WSL2 needs its own
CUDA toolkit inside the distro. VRAM stays shared with the Windows desktop either way.

### Orchestrator language — TypeScript
Plan's own recommendation, and the UI is the part that gets iterated most. Reinforced by
Harrison's day job being React/TypeScript. Fastify + SQLite, single worker.

### Rigging — cut to v2
Makes Phase 2 ~40% shorter. Props, crops, walls, and terrain — most of what Homestead
Defense needs — don't need skeletons. Also defers the thorniest licensing review (several
rigging models release code permissively but weights research-only), which matters because
these assets go into games that get sold.

### Target engine — both, Godot 4 defaults first
Export GLB + FBX + STL from one Blender pass as the plan calls for, but default tri budget
and texture resolution to Godot 4 low-poly. Matches the existing `asset-pack` pipeline and
Homestead Defense, and is the kindest ask of an 8 GB card. Unreal preset added later for
Homestead & Market / Elixir of Life.

### Image model — Z-Image-Turbo, validated 2026-08-30
Chose Z-Image-Turbo over Qwen-Image 2.0 for the lighter footprint and speed (Harrison's call
— safer bet for an 8 GB card over Qwen's likely-higher quality ceiling). Validated in ComfyUI:
`z_image_turbo_int8_convrot.safetensors` (int8 diffusion, 5.8 GiB) + `qwen_3_4b_fp8_mixed.safetensors`
(fp8 text encoder, 5.3 GiB) + `ae.safetensors` VAE. 1024x1024, 8 steps, `res_multistep`/`simple`,
cfg 1, `ModelSamplingAuraFlow` shift 3 — the model's own turbo-distilled defaults. First
generation (cold model load + sampling): ~38s, no OOM, 7.0 GB VRAM free to start. Output was
clean and sharp, on a plain background — the master plan's "clean alpha for the mesh
generator" requirement. Skipped the nvfp4 diffusion-model variant (smaller, 4.5 GiB) since
NVFP4 targets Blackwell tensor cores and is unproven on Turing. See
[COMFYUI_SETUP.md](./COMFYUI_SETUP.md) for the full setup. Qwen-Image 2.0 was not tested —
Z-Image-Turbo's result was good enough to not need the comparison for v1.

## Still open

- Whether InstantMesh or TripoSR wins on quality-per-VRAM for low-poly targets.
