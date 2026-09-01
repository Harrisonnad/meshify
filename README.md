# Local 3D Asset Forge

Self-hosted text/image → game-ready 3D asset pipeline. Open-weight stack, runs entirely on
this machine.

Built from `MASTER_PLAN.md`, adjusted for measured hardware — see
[docs/hardware.md](docs/hardware.md) and [docs/DECISIONS.md](docs/DECISIONS.md).

## v1 scope

Prompt or image in; watertight-at-generation mesh, `.glb` / `.fbx` / `.stl` out, with a saved
recipe (prompt, seed, model versions, settings) so any asset is reproducible. Auto-rigging is
built and available as an opt-in stage (`params.rig`) — see [docs/RIG.md](docs/RIG.md).

Color is vertex-color only, not baked PBR texture maps (albedo/normal/roughness/metallic) —
the master plan's original texture target was never built; see
[docs/MESH_GEN.md](docs/MESH_GEN.md) for what TripoSR actually outputs.

**Still cut:** multi-object scenes, retopology to hand-authored quad density, face
blendshapes, cloud/multi-user, a 4-candidate concept picker (backend supports one image per
job only).

## The hardware situation, up front

RTX 2070 — 8 GB, **compute capability 7.5 (Turing)**. Two constraints, and the second is the
binding one: FlashAttention-2 requires SM 8.0+, so TRELLIS 2 and Hunyuan3D are effectively out
regardless of offloading. InstantMesh / TripoSR are the realistic mesh generators here.

That risk paid off as predicted: the master plan's §10 hybrid (validate the chain in ComfyUI
first, wrap only the survivors) is exactly how `imggen` and `rig` ended up built. WSL2 +
CUDA passthrough was a real prerequisite, not a nicety — see
[docs/WSL2_SETUP.md](docs/WSL2_SETUP.md).

## Architecture

One venv per worker where that's achievable — the models pin mutually incompatible
CUDA/PyTorch/xformers combinations, and a shared environment is where this dies. (`rig` is
the one exception: it imports ComfyUI's own modules directly, so it shares `imggen`'s venv
rather than getting an isolated one — see [docs/RIG.md](docs/RIG.md) for why.) See
[docs/WORKER_CONTRACT.md](docs/WORKER_CONTRACT.md).

```
UI (React + TS + Vite)                              — native Windows
      │ REST, polled (no SSE yet)
Orchestrator (TypeScript / Fastify, flat JSON job store, single worker) — native Windows
      │ HTTP, localhost only, file paths not blobs
      ├── :8101 imggen    (WSL2, own venv, wraps ComfyUI)
      ├── :8102 meshgen   (WSL2, own venv, imports TripoSR directly)
      ├── :8103 rig       (WSL2, shares imggen's venv, opt-in — see docs/RIG.md)
      └── :8104 blender   (native Windows, Blender 5.2 headless subprocess per job)
```

`imggen`/`meshgen`/`rig` run inside WSL2 (CUDA); `blender` and the orchestrator run on native
Windows. Windows 11 auto-forwards WSL2 localhost ports, so everything talks to everything
over plain `127.0.0.1` regardless of which side it's actually on — except file *paths*, which
need explicit translation at the two points a path crosses that boundary (see
`orchestrator/src/workers.ts`'s `wslToWindowsPath`/`windowsToWslPath`).

Single-worker queue: one GPU means concurrency buys nothing but OOM errors — confirmed the
hard way in [docs/PHASE2_WORKERS.md](docs/PHASE2_WORKERS.md) (two *concurrently active* GPU
workers wedge the card instead of failing cleanly). Three GPU workers *resident* at once
(as `rig` requires) turned out to be fine, per [docs/RIG.md](docs/RIG.md) — it's concurrent
compute that's dangerous, not concurrent memory residency.

## Status

- [x] **Phase 0** — hardware gate, repo scaffold, decisions recorded
- [x] Blender headless GLB export verified end to end (the cube fixture is real output)
- [x] Worker contract defined; stub worker returning the fixed cube
- [x] WSL2 installed, GPU passthrough verified (`nvidia-smi` sees the RTX 2070 in WSL)
- [x] CUDA Toolkit 12.6 installed inside the distro (`nvcc` confirms release 12.6.85)
- [x] ComfyUI installed and boots on Turing (`~/comfyui` in WSL, `uv`-managed Python 3.11 venv,
      torch 2.13.0+cu126) — GPU detected, server starts clean on `pytorch`/`eager` attention
- [x] Image model validated: Z-Image-Turbo (int8+fp8) generates clean 1024x1024 images in
      ~38s via the ComfyUI API — see [docs/COMFYUI_SETUP.md](docs/COMFYUI_SETUP.md)
- [x] Mesh generator validated: TripoSR produces a watertight mesh (103K verts) from the
      tractor test image in <10s compute — see [docs/MESH_GEN.md](docs/MESH_GEN.md)
- [x] **Phase 1 complete** — image→mesh chain proven end to end on this hardware
- [x] `imggen` and `meshgen` real workers built, each validated against the actual
      `/health` + `/run` HTTP contract (not stubs) — see
      [docs/PHASE2_WORKERS.md](docs/PHASE2_WORKERS.md), including a confirmed finding that
      running both workers' GPU models loaded at once wedges the card instead of failing
      cleanly (reinforces the single-worker-queue design)
- [x] `blender` cleanup worker built and validated (decimate/normals/UV/origin + GLB+FBX+STL
      export), each of the three real workers now proven individually — see
      [docs/PHASE2_WORKERS.md](docs/PHASE2_WORKERS.md) for the normal-map-baking deferral and
      a known decimation-breaks-watertightness tradeoff (matters for STL/printing, not GLB/FBX)
- [x] **Phase 2 workers complete** (rig stays cut to v2 per DECISIONS.md)
- [x] **Phase 3 — orchestrator built and validated**: Fastify + a flat JSON job store
      (`better-sqlite3` couldn't build on this machine's toolchain — see
      [docs/ORCHESTRATOR.md](docs/ORCHESTRATOR.md)), single-worker sequential dispatch,
      full recipe tracking. One `POST /jobs` call now chains imggen → meshgen → blender
      automatically and produces real `.glb`/`.fbx`/`.stl` files end to end.
- [x] **Phase 4 — UI built and verified in a real browser**: React + TS + Vite,
      `<model-viewer>` for interactive 3D, prompt form → live stage progress → result panel
      → job library, all driven end to end with Playwright against the live orchestrator and
      real generated assets — see [docs/UI.md](docs/UI.md) (also: pinned to Vite 5 after
      Vite 8's default Rolldown bundler hit a native-binding wall, the fourth native-toolchain
      issue this project has hit on this machine)
- [x] **Phase 5 — hardening**: lockfiles + upstream commit SHAs for every worker,
      one-command bring-up (`scripts/dev.ps1`), a CI smoke test running the orchestrator
      against stubbed workers on a plain Linux runner (`.github/workflows/smoke-test.yml` +
      `scripts/smoke-test.mjs` — which immediately caught `stub_worker.py`'s output keys
      having drifted from the real workers' contract), and a review of the model-swap-test
      question — see [docs/HARDENING.md](docs/HARDENING.md) for what's fully verified vs.
      still open (notably: `dev.ps1`'s WSL launches and the CI workflow's actual GitHub
      Actions run are both unverified from within this session, for different reasons)
- [x] **Auto-rigging (originally cut to v2) built and validated after all**: SkinTokens via
      the `ComfyUI-SkinToken` community wrapper, opt-in via `params.rig` — UniRig and the
      official SkinTokens repo are hardware-blocked (need `flash-attn`, SM 8.0+; we're
      Turing SM 7.5), but this wrapper's graceful SDPA fallback works. Produced a correct
      34-bone biped skeleton on a test character, and the full orchestrator pipeline
      (image → mesh → clean → rig) completes in ~2m13s with three sequentially-resident GPU
      workers coexisting fine — see [docs/RIG.md](docs/RIG.md).
- [ ] Master plan fully implemented — remaining stretch goals: 4-candidate concept picker,
      normal-map baking, InstantMesh (blocked on this hardware's toolchain), UI support for
      the rig option, license audit before shipping anything commercially (§8, not started)

## Head start: Blender

`../asset-pack` is already a working headless-Blender 5.2 pipeline (20+ `gen_*.py` scripts,
shared trim sheet, day/night `.gdshader`). Phase 2c — decimate, recalc normals, real-world
scale, smart UV unwrap, normal-map bake, origin placement — is the same class of work, and
it is **CPU-side, so untouched by the 8 GB / Turing limits.** Lowest-risk stage in the
pipeline and worth building early.

## Running the stubs

The UI and orchestrator can be built and demoed before any GPU model is installed:

```bash
python -m venv .venv-stub && .venv-stub/Scripts/pip install -r workers/requirements-stub.txt
.venv-stub/Scripts/python workers/stub_worker.py --worker meshgen --port 8102
```

Regenerate the fixture:

```bash
"/c/Program Files/Blender Foundation/Blender 5.2/blender.exe" --background \
  --python workers/blender/make_fixture_cube.py -- --output fixtures/cube.glb
```

## Licensing

Assets from this pipeline go into games that get sold, so licensing is a real task, not a
footnote. Before anything ships commercially: audit **code** and **weights** licenses
separately per model (currently Z-Image-Turbo, TripoSR, and SkinTokens/Qwen3-0.6B — all
installed and in active use), check commercial-use terms, territory/scale restrictions, and
downstream output terms. Record per-model results in `LICENSES.md`. Also check current
AI-disclosure policy for each storefront. **Not started** — all four phases plus rigging
are built and validated, but no license review has happened yet; do this before shipping
anything made with this pipeline commercially.
