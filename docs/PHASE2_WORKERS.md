# Phase 2 — imggen and meshgen Workers

Status as of 2026-08-31: **Both real workers built and validated against the actual HTTP
contract** in [WORKER_CONTRACT.md](./WORKER_CONTRACT.md), not just ad-hoc scripts.

## workers/imggen/server.py — port 8101

Wraps the ComfyUI backend proven in [COMFYUI_SETUP.md](./COMFYUI_SETUP.md). ComfyUI is an
implementation detail here, not a separate always-on service: `_ensure_comfy_running()` checks
`/system_stats` and launches ComfyUI as a subprocess on first `/run` if it isn't already up.
Runs in the ComfyUI venv (`~/comfyui/.venv`), where torch/cu126 and the Z-Image-Turbo weights
already live.

Verified: `POST /run` with `{"job_id": "test-imggen-1", "params": {"prompt": "a small wooden
treasure chest..."}}` produced a real 1024x1024 PNG at `scratch/test-imggen-1/image.png` in
~40s (cold ComfyUI start + generation).

## workers/meshgen/server.py — port 8102

Imports the `tsr` package directly — no subprocess, no ComfyUI. Same model calls as
`run.py` (background removal → resize/flatten onto gray → `TSR.from_pretrained` →
`extract_mesh`), proven in [MESH_GEN.md](./MESH_GEN.md). Runs in TripoSR's own venv
(`~/triposr/.venv`), which has the `scikit-image` CPU marching-cubes fallback patched in.

Verified: `POST /run` with `{"job_id": "test-meshgen-1", "inputs": {"image": "<path>"}}`
produced a watertight 206,234-triangle `.glb` at `scratch/test-meshgen-1/raw.glb`.

## Gotcha: shutil.copy fails across the WSL/Windows filesystem boundary

`imggen`'s `/run` crashed on its first real test — generation succeeded, but the final
`shutil.copy(src, dest)` into `scratch/` (on `/mnt/c`, WSL's view of the Windows disk) raised:

```
PermissionError: [Errno 1] Operation not permitted: '.../scratch/test-imggen-1/image.png'
```

`shutil.copy` copies data *then* `chmod`s the destination to match the source's permission
bits — and DrvFs (the `/mnt/c` mount) rejects that `chmod`. Fix: use `shutil.copyfile` instead,
which only copies data. **Any future worker code that writes into `scratch/` from a WSL
process should use `copyfile`, not `copy`,** if the repo (and therefore `scratch/`) stays on
the Windows side of the filesystem boundary.

## Finding: concurrent GPU workers don't fail cleanly, they wedge

Tested `imggen` a second time while `meshgen`'s TripoSR model was still resident in VRAM from
its own test (both worker processes left running). Result: `nvidia-smi` showed **7.6/8.2 GB
used, 100% GPU util, and the ComfyUI subprocess pegged at 92% CPU with zero progress for 7+
minutes** — no OOM error, no crash, just wedged. Had to `kill -9` the stuck ComfyUI process
directly; it never recovered on its own.

Killing the `meshgen` worker (freeing its VRAM) and starting `imggen` fresh fixed it
immediately — the same request completed normally.

**This is not a bug in either worker — it's exactly the scenario
[README.md](../README.md)'s "single-worker queue: one GPU means concurrency buys nothing but
OOM errors" line is warning about,** now confirmed empirically rather than just asserted. The
failure mode is worse than a clean OOM: on this card/driver combo, contention wedges the job
silently rather than erroring. **Hard requirement for Phase 3's orchestrator: enforce strictly
sequential job dispatch — never let two GPU workers have models loaded at the same time, even
briefly for a "quick" test.** Worth a startup/health check in the orchestrator that refuses to
dispatch a second job while another GPU worker reports resident state, as defense in depth
beyond just queuing.

## workers/blender/server.py — port 8104

The odd one out: Blender's Python API isn't a natural long-running async server, so unlike
`imggen`/`meshgen`, each `/run` shells out to a fresh `blender --background --python
cleanup.py` subprocess — the same one-shot pattern the repo already used for the fixture cube
(`make_fixture_cube.py`). No CUDA, no special venv — runs on plain Windows Python (the
`.venv-stub` from [README.md](../README.md)'s "Running the stubs" section), unaffected by
everything in [hardware.md](./hardware.md).

`workers/blender/cleanup.py` does the master plan's §2c list, minus texture baking (see below):
decimate to a target tri budget (`DECIMATE` modifier, `COLLAPSE` ratio computed from
current/target tris), recalculate normals (`normals_make_consistent` + shade smooth),
smart-UV-unwrap only if the mesh has no UVs already, apply a uniform scale, set the origin at
the base (bounding-box bottom-center) for props — matching the convention already established
in `make_fixture_cube.py` — then export GLB + FBX + STL.

**Not done yet: normal-map baking** (high-poly detail → decimated low-poly), also from §2c.
Needs visual iteration on bake margins/cage distance to get right, which isn't practical to
do blind — a follow-up once there's a way to actually look at intermediate results (e.g. once
the UI's model viewer exists in Phase 4, or by opening results in the Blender GUI directly).

**Known tradeoff: decimation breaks strict watertightness.** Verified on the tractor test
mesh — the raw `meshgen` output was watertight (confirmed in
[MESH_GEN.md](./MESH_GEN.md)), but after Blender's `DECIMATE` modifier the result is not
(`trimesh` reports `is_watertight: False`). This is standard `COLLAPSE`-mode decimation
behavior, not a bug in `cleanup.py`. Doesn't matter for GLB/FBX (game engines don't care), but
matters for the STL export path specifically, since STL/3D-printing wants a manifold mesh. If
print-quality output becomes a real requirement, the fix is a `REMESH` pass (voxel or
quad-based) after decimation, at the cost of losing some shape fidelity — not implemented yet.

Verified end to end via the real HTTP contract: `POST /run` with `{"job_id":
"test-blender-1", "inputs": {"mesh": ".../raw.glb"}, "params": {"target_tris": 6000}}`
decimated the tractor mesh from 206,234 → 6,000 triangles (still clearly recognizable when
rendered) and exported all three formats to `scratch/test-blender-1/clean/`.

## Still open for Phase 2

- Normal-map baking (see above) — deferred, not blocking.
- Rig worker — explicitly cut to v2 per [DECISIONS.md](./DECISIONS.md).
- All three real workers (imggen, meshgen, blender) are now built and individually validated.
  What's left before Phase 3: nothing structural — the orchestrator can now be built against
  real workers instead of stubs.
