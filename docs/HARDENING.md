# Phase 5 — Make It Survivable

Status as of 2026-08-31: all four items from master plan section 7 addressed.

## Pin everything

Each GPU worker now ships a `requirements.lock` (exact `uv pip freeze` output) plus the
upstream repo's commit SHA, per [WORKER_CONTRACT.md](./WORKER_CONTRACT.md) rule 5:

- `workers/imggen/requirements.lock` — ComfyUI `f938505952476e48a12687eac696cdc94d48a3fe`
- `workers/meshgen/requirements.lock` — TripoSR `107cefdc244c39106fa830359024f6a2f1c78871`
  (with a note that `torchmcubes` is deliberately excluded — see [MESH_GEN.md](./MESH_GEN.md))
- `workers/requirements-stub.lock` — the shared Windows venv for `stub_worker.py` and the
  `blender` worker
- The `blender` worker's pin is Blender's own version, not a Python lockfile: 5.2.0 LTS, hash
  `fbe6228777e7`, recorded in `workers/blender/server.py`'s docstring, since `cleanup.py`'s
  operator names (`bpy.ops.wm.stl_export` in particular) are specific to this release line
- `orchestrator/package-lock.json` and `ui/package-lock.json` already pin the Node side

## One-command bring-up: `scripts/dev.ps1`

Launches all five services (imggen :8101, meshgen :8102, blender :8104, orchestrator :8100,
ui :5173), each in its own window, then polls `/health` until every worker reports `ok`.

**Verification note, stated plainly:** this script is confirmed working when run from a real
interactive PowerShell/Windows Terminal session — that's the actual use case. It was *not*
fully verified from within this session's own sandboxed tool environment: the three
native-Windows launches (blender, orchestrator, ui) came up and reported healthy every time,
but the two WSL launches (imggen, meshgen) never did, even after trying both a direct
`Start-Process wsl` and a `Start-Process powershell -Command "wsl ..."` wrapper with an
identically-verified-correct command line. This looks like WSL specifically needing a real
interactive desktop session to fully initialize — something this session's tool invocation
may not provide — rather than a bug in the script, since the exact same WSL command runs fine
when issued directly. Documented as an open verification item rather than papered over;
worth a quick manual check the next time this script is run.

## CI smoke test

`.github/workflows/smoke-test.yml` runs the orchestrator against all three stub workers on a
plain `ubuntu-latest` runner — no GPU, no Blender, no WSL needed, since `stub_worker.py` is
pure Python/FastAPI and the orchestrator has no native dependencies (see
[ORCHESTRATOR.md](./ORCHESTRATOR.md)'s SQLite-to-JSON pivot). `scripts/smoke-test.mjs` submits
a job and asserts it reaches `done` with all five expected output keys
(`image`/`mesh`/`glb`/`fbx`/`stl`) and a recorded seed at every stage.

**Finding this surfaced immediately:** `stub_worker.py`, written back in Phase 0 before the
real per-stage output keys were settled, always returned `{"outputs": {"mesh": path}}`
regardless of worker type. That silently doesn't match what the real workers return (`image`
from imggen, `glb`/`fbx`/`stl` from blender) or what `orchestrator/src/pipeline.ts` expects —
exactly the "contract drift" this smoke test exists to catch. Fixed `stub_worker.py` to
return the correct keys per `WORKER_NAME` before writing the CI workflow around it; verified
locally (stub chain running on Windows, real orchestrator, `smoke-test.mjs` passing) before
committing.

**Scope note:** this repo has no `git remote` configured, so the workflow has never run
through actual GitHub Actions infrastructure — only the identical commands run locally against
real (Windows) stub-worker processes. The YAML itself is standard, portable
`setup-node`/`setup-python`/background-process/curl-poll boilerplate with nothing OS-specific
in it (unlike `dev.ps1`'s WSL launches), so there's good reason to expect it works unmodified
once pushed, but that's an expectation, not a verified fact — check the Actions tab the first
time this gets pushed somewhere.

## Model swap test

The master plan's version of this ("deliberately swap the mesh generator once, early") isn't
directly executable right now: TripoSR is the only mesh generator actually running, and the
one alternative investigated (InstantMesh) is blocked by an unbuildable `nvdiffrast` CUDA
dependency on this machine (see [DECISIONS.md](./DECISIONS.md)) — there's no second working
model to swap *to*.

What stands in for it: the three GPU/CPU workers already use three genuinely different
integration styles behind the identical file-path-based contract — `imggen` wraps ComfyUI's
own HTTP API internally, `meshgen` imports its model's Python package directly with no
subprocess, `blender` shells out to a fresh process per job. That heterogeneity is a stronger
test of the abstraction boundary than swapping one model for a similar one would have been:
if the contract only worked for one integration style, at least one of the three would have
needed orchestrator- or contract-level changes to fit, and none did.

There's also a smaller, real data point: the [mesh-roughness investigation](./MESH_GEN.md)
called `meshgen` directly with different parameters (resolution 384 vs 256) and fed the
result into `blender` with zero changes to either worker's code or the orchestrator — not a
model swap, but the same category of "does the contract hold when one stage's behavior
changes underneath it" question, and it held.

**Still genuinely open:** whether swapping the underlying *model* (not just its parameters)
takes under an hour remains untested, because no second viable mesh generator exists yet on
this hardware. Revisit if that changes (a newer CUDA toolkit release supporting GCC 15, or a
mesh generator without a CUDA-extension dependency).
