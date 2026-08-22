# Hardware Gate — Phase 0 Deliverable

Measured 2026-08-22. This file is the reference for every "which CUDA / which model" question
that comes up in the repos we'll be cloning. Update it when the driver or OS situation changes.

## Measured

| Property | Value |
|---|---|
| GPU | NVIDIA GeForce RTX 2070 |
| VRAM | 8192 MiB (8 GB) |
| VRAM free at idle | ~6.7 GB (~1.4 GB held by desktop/WDDM) |
| Compute capability | **7.5 (Turing)** |
| Driver | 610.88 |
| CUDA (UMD) | 13.3 |
| Driver model | WDDM (display attached to this GPU) |
| System RAM | 32 GB |
| Free disk (C:) | 104 GB of 475 GB |
| OS | Windows 11 Home 10.0.26200 |
| WSL2 | **Not installed** |
| Node | v20.10.0 / npm 10.2.3 |
| Python | 3.12.0 (system) |
| Blender | 5.2 |
| Git | 2.43.0.windows.1 |

## What this rules in and out

The master plan's VRAM table puts 8 GB in the bottom tier: **TripoSR / InstantMesh only**.
Two independent constraints land us there, and the second one is the harsher of the two.

**1. VRAM (8 GB, ~6.7 GB usable).** TRELLIS 2 at full quality is out. Hunyuan3D is out at
default settings. InstantMesh fits. TripoSR fits comfortably.

**2. Compute capability 7.5 — this is the real blocker.** Turing predates Ampere, and
FlashAttention-2 requires SM 8.0+. Several of the mesh generators in the plan (TRELLIS
especially) lean on flash-attn or Ampere-era kernels in their default install path. On Turing
you are on the xformers / SDPA fallback path, which is the less-tested route in these repos.
Expect this to be the source of most Phase 1 friction. **No amount of offloading fixes a
compute-capability floor** — this is a harder limit than the VRAM number.

Practically: the "TRELLIS 2 with offloading (slow but works)" consolation in the 12–16 GB row
is not available to us, because our problem isn't only capacity.

**3. WSL2 is not installed.** The plan is unambiguous that native Windows Python will cost
days on CUDA-extension builds. Installing WSL2 with CUDA passthrough is a prerequisite, not
an optional nicety. Note it will need its own CUDA toolkit inside the distro, and the 8 GB is
shared with the Windows desktop either way.

**4. Python 3.12 is newer than most of these repos expect.** The ML repos in question
commonly pin 3.10 or 3.11. Per-worker venvs (the architecture's core rule) make this a
non-issue as long as we install the right interpreter per worker rather than reaching for
the system 3.12.

**5. Disk: 104 GB free is workable but not roomy.** Model weights are the bulk — a couple of
image models plus a mesh generator plus their caches will run 40–60 GB. Watch it.

## Consequences for the plan

- **Phase 1 risk is high, not moderate.** The plan says Phase 1 "is either a Saturday
  afternoon or a two-week slog depending entirely on your GPU and driver situation." Our GPU
  and driver situation is the slog-shaped one. The plan's own advice applies: if it stalls
  past a weekend, that is real signal.
- **The §10 hybrid path is now the recommended entry, not the fallback.** Validating the
  chain in ComfyUI first is exactly the de-risk this hardware calls for — it tells us which
  models actually run on Turing before we invest in venv + FastAPI wrappers around them.
- **Phase 2c (Blender cleanup) is our strongest position.** See below.

## Existing asset we can reuse

`C:\Users\blitz\projects\asset-pack` is already a working headless-Blender pipeline (Blender
5.2, `blender/build_all.py`, ~20+ `gen_*.py` mesh scripts, shared trim sheet + day/night
`.gdshader`). Phase 2c of the master plan — decimate, normals, real-world scale, smart UV
unwrap, normal-map bake, origin placement — is the same class of work already being done
there, and it is **CPU-side, so entirely unaffected by the 8 GB / Turing constraints**.

That makes mesh cleanup the lowest-risk stage in the whole pipeline for us, and worth building
early to bank a win while Phase 1 is being fought.
