# Auto-Rigging — SkinTokens

Status as of 2026-09-01: **Built and validated end to end**, including through the real
orchestrator pipeline. Originally cut to v2 in [DECISIONS.md](./DECISIONS.md) — turned out
viable after all once the right integration path was found.

## The path here: two dead ends, then a working one

**UniRig** (master plan §2d's first choice) needs `flash-attn`, which requires SM 8.0+
(Ampere). We're Turing, SM 7.5 — one generation short, the same hardware floor that already
ruled out TRELLIS 2 and Hunyuan3D in Phase 0. Also needs `spconv`/`torch_scatter`/
`torch_cluster` (prebuilt-wheel libraries likely to need a source build against our
much-newer torch) and 60GB+ GPU memory for training configs. Not attempted — too many
independent red flags.

**The official `VAST-AI-Research/SkinTokens` repo** — same story: its README explicitly
requires `flash-attn` and 14GB VRAM. Blocked for the same reason as UniRig.

**`ComfyUI-SkinToken`** (a community wrapper, `Rizzlord/ComfyUI-SkinToken`) turned out
different. Its `requirements.txt` has no `flash-attn`, `spconv`, `torch_scatter`,
`torch_cluster`, or `nvdiffrast` — and its README claims **~4GB VRAM**. Read the actual
vendored source before trusting that (a lesson from the InstantMesh investigation, where a
buried dependency wasn't in the README either):

- `tsr/models/isosurface.py`-style dead code: `tokenrig.py` and `skin_vae_model.py` both
  define a `flash_attn_func` fallback that ultimately does `raise RuntimeError("flash-attn is
  not available")` — but neither file ever actually *calls* it. Confirmed via a cross-file
  grep. Vestigial, not load-bearing.
- `michelangelo/transformer_blocks.py` and `miche_transformer_blocks.py` gate the real
  external `flash_attn_interface` import behind a `use_flash3.is_use` flag; the fallback path
  uses PyTorch's own `torch.backends.cuda.sdp_kernel` (built-in SDPA dispatch) — the exact
  same mechanism ComfyUI itself already uses successfully for Z-Image-Turbo on this card
  (`docs/COMFYUI_SETUP.md`). Not a crash path — a real, working fallback.

Confirmed empirically too: running it produced a `torch.backends.cuda.sdp_kernel()
deprecated` warning, not a `flash-attn` import error — proof the fallback path was actually
exercised, not just theoretically present.

## Integration: direct Python import, not the ComfyUI node graph

`sktn_nodes.py` imports `comfy.model_management` and `folder_paths` directly — unlike
`meshgen`'s `tsr` package, this isn't cleanly decoupled from ComfyUI, so giving it its own
isolated venv (the usual rule) isn't achievable without reimplementing those modules. Runs in
the **existing** ComfyUI venv instead (same one `imggen` uses) — checked for a `transformers`
conflict first (the exact thing that broke the original `ComfyUI-Flowty-TripoSR` attempt) and
found none; `transformers==5.16.1` already satisfies `>=4.57.0` and nothing broke.

But the node graph itself is bypassed entirely — `workers/rig/server.py` imports
`SkinTokenRigTrimesh` directly and calls it in-process, matching `meshgen`'s pattern rather
than `imggen`'s (which drives ComfyUI's actual HTTP API). Two things needed to make that
work:

1. **A relative-import workaround.** `sktn_nodes.py` does `from .vendor.skintokens...` — a
   relative import that needs a real parent package context. A plain `sys.path` import
   doesn't provide one and fails with `ImportError: attempted relative import with no known
   parent package`. Fixed by registering a dummy package via `importlib.util` pointing at the
   custom node's own directory before loading the module (see `workers/rig/server.py`).
2. **A native Linux Blender inside WSL.** The export step shells out to Blender via pickle
   files passed by Linux path. Pointing it at the *Windows* Blender the `blender` worker
   already uses fails, because a Windows-hosted `Blender.exe` can't resolve WSL's `/home/...`
   paths without a `\\wsl$\...` translation the vendored code doesn't do — the mirror image of
   the `wslToWindowsPath` gotcha in `docs/ORCHESTRATOR.md`, in the opposite direction. Rather
   than patch vendored third-party code, downloaded a native Linux Blender 5.2.0 build
   directly into WSL (`~/blender-5.2.0-linux-x64/`, matching the Windows install's version)
   and pointed `SKINTOKEN_BLENDER_BIN` at it. This means **two separate Blender installs**
   exist now (Windows 5.2.0 for `blender`, WSL Linux 5.2.0 for `rig`) — minor duplication,
   cheap given Blender is free and portable.

## Validation results

**Plumbing test** (tractor mesh, not a character): survived the full rig+skin+export
round-trip completely intact — 110,578 output vertices, no corruption, no exploded topology.
Proved the pipeline doesn't break inputs it wasn't designed for, before testing quality on a
real subject.

**Quality test** (a generated humanoid robot, T-pose): produced a genuinely correct biped
skeleton — 34 bones in exactly the topology you'd want: a 4-bone spine branching into a head,
two mirrored arm chains that each further branch into finger-like sub-chains, and two separate
leg chains. Not something a degenerate or broken model produces by chance.

**Full orchestrator run** (`queued → generating_image → meshing → cleaning → rigging → done`,
job `01M1D4YEVKH2JZQBWBVY1YBP46`): completed in ~2m13s end to end, recipe populated for all
four stages, all five output files (`image`, `glb`/`fbx`/`stl`, `rigged`) present on disk.

## The VRAM question, answered

[PHASE2_WORKERS.md](./PHASE2_WORKERS.md) found that two GPU workers with models
**concurrently active** wedge the 8GB card instead of failing cleanly. Adding `rig` raises a
related but different question: by the time a job reaches the rigging stage, THREE GPU
workers may have models **resident** at once (`imggen`'s Z-Image-Turbo, `meshgen`'s TripoSR,
`rig`'s SkinTokens), since none of them unload after a request — only one is ever *actively
computing*, but all three can be holding VRAM simultaneously.

**Answer: it worked fine.** The full end-to-end run above completed with no wedge, no OOM, no
manual intervention — sequential-but-resident is a different situation from concurrent-active,
and this card handles it. Worth re-checking if a future worker's model is large enough to
change that math, but for the current three, it's a non-issue.

## Design: opt-in, not automatic

`params.rig` defaults to `false`. Per the original v2 rationale in `DECISIONS.md`, most assets
this project cares about — props, walls, terrain, crops — don't need a skeleton, and rigging
adds real time (model load + beam-search generation + a second Blender export pass) that
would be wasted on them. Set `params.rig: true` on `POST /jobs` to opt in.

## Still open

- No test yet with an even heavier subject (e.g. a quadruped or a very high-poly character) —
  the VRAM finding above is specific to what was actually tested.

## Skeleton naming templates: Mixamo/UE5 exist, but don't trust them blindly

`SkinTokenRigTrimesh`'s `skeleton_template` param (`"Keep model names"` default, or
`"Mixamo"`/`"Unreal Engine 5"`) is passed through via `params.skeleton_template` if a caller
wants it. **This renamer does not do real anatomical matching** — traced through the vendored
source: it tries a geometric heuristic first, but silently falls back to overlaying a fixed
template name list onto bones in their existing index order when that heuristic can't
confidently match. Verified empirically on both real rigged outputs on hand: the first ~10
bones came out correctly named, everything past that didn't (e.g.
`mixamorig:RightShoulder`'s renamed parent came back as `mixamorig:LeftHand`). Anything built
on top of Mixamo/UE5 naming needs its own hierarchy validation before trusting it.

The preset animation library ([docs/ANIMATION.md](./ANIMATION.md)) originally depended on
this renamer and inherited its ~0% real-world hit rate as a result. It no longer requests
`skeleton_template` at all — `workers/blender/animate.py`'s `classify_skeleton()` identifies
bone roles directly from the predicted skeleton's topology instead, which was independently
validated as reliably correct even when the naming isn't.
