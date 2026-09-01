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

### Rigging — cut to v2 (settled 2026-08-22), reversed 2026-09-01
Original reasoning: makes Phase 2 ~40% shorter; props, crops, walls, and terrain — most of
what Homestead Defense needs — don't need skeletons; defers the thorniest licensing review
(several rigging models release code permissively but weights research-only), which matters
because these assets go into games that get sold.

**Update 2026-09-01: built and available after all.** UniRig (the master plan's pick) and the
official SkinTokens repo are both genuinely blocked on this hardware (`flash-attn` needs
SM 8.0+, we're Turing SM 7.5) — but the community `ComfyUI-SkinToken` wrapper gates flash-attn
behind a working PyTorch SDPA fallback and runs fine. See [RIG.md](./RIG.md) for the full
story, validation results (a correct 34-bone biped topology on a test character), and the
VRAM finding (three sequentially-resident GPU workers coexist fine on this card). Kept
**opt-in** (`params.rig`, default `false`) rather than made automatic — the original rationale
that most assets don't need a skeleton still holds, this just removes "can't be done at all"
as a reason to skip it when one does. The licensing review this decision originally deferred
is still deferred — not yet done for SkinTokens' weights, same as everything else in
[MASTER_PLAN.md](../MASTER_PLAN.md) §8.

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

### Mesh generator — TripoSR, validated 2026-08-30
TripoSR meets Phase 1's exit criteria: watertight mesh (confirmed via `trimesh`), fast
(<10s actual compute), comfortable VRAM headroom. Had to give it its own venv (`~/triposr`,
separate from ComfyUI's) after hitting a `transformers` version conflict trying to reuse the
ComfyUI venv via `ComfyUI-Flowty-TripoSR` — see [MESH_GEN.md](./MESH_GEN.md) for the full
story, including a `torchmcubes` CUDA-extension build that turned out to be entirely
unbuildable on this system's GCC 15 (patched around it with a `scikit-image` CPU fallback,
which was not slow). InstantMesh not tried — TripoSR already clears the bar for v1; a
quality-per-VRAM comparison can happen later if TripoSR's output proves insufficient.

### Surface noise on complex subjects — accepted v1 limitation, 2026-08-31
The watering-can test asset came out rough (rippled surface, deformed spout) unlike the clean
tractor case. Tried tuning around it — higher marching-cubes resolution (384 vs 256) and a
much gentler decimation target (15,000 vs 6,000 tris), reusing the identical source image to
isolate variables. No improvement at any setting tested; see [MESH_GEN.md](./MESH_GEN.md).
The roughness is baked into TripoSR's reconstruction for thin/disconnected geometry, not an
artifact of mesh extraction or decimation. Considered switching to InstantMesh (untried, see
below) but its `nvdiffrast` dependency compiles a CUDA extension the same way `torchmcubes`
did, and that failure mode was confirmed **unfixable** on this system's GCC 15 — not worth the
setup time speculatively. **Accepted as a known v1 quality ceiling** for geometrically
complex, thin-featured subjects. A Blender smoothing pass is the cheaper fix if this becomes
a real blocker later.

## Still open

- InstantMesh — deprioritized, not just deferred: its `nvdiffrast` dependency needs the same
  class of CUDA extension build that's already confirmed unbuildable on this machine
  (see above). Revisit only if the GCC/toolchain situation changes (e.g. a newer CUDA
  toolkit release that supports GCC 15) or the surface-noise limitation becomes a real
  blocker rather than an accepted one.
