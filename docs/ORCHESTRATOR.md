# Phase 3 — Orchestrator

Status as of 2026-08-31: **First real end-to-end job succeeded.** One API call chains
imggen → meshgen → blender automatically, with full recipe tracking.

## Stack: Fastify + a flat JSON file, not SQLite

Master plan §5 calls for "SQLite for jobs + recipes... no Postgres, no Docker Compose sprawl.
One file." Tried that first — `better-sqlite3`'s native build failed on this machine's
node-gyp/MSBuild toolchain (a version mismatch between the Node on `PATH` and the Node npm's
internal tooling invoked for the build). This is the same *class* of problem as the CUDA/gcc
toolchain fights hit repeatedly on the Python side this session (see
[MESH_GEN.md](./MESH_GEN.md)): this machine's toolchains don't line up cleanly with what
native builds expect.

Rather than debug a third native-toolchain problem, swapped to a flat JSON file
(`orchestrator/jobs.json`, gitignored) as the job store. For a single-user local tool with job
counts in the dozens, this satisfies the "one file, no server" spirit exactly as well as
SQLite would, with zero native dependencies. Revisit only if job volume or concurrent readers
ever make that not true — neither is expected for this project.

## Job state machine

```
queued → generating_image → meshing → cleaning → done
                                                 ↘ failed (from any state)
```

Deliberately simpler than the master plan's full list — no `generating_concepts` /
`awaiting_selection` (the 4-candidate concept picker is a Phase 4 UI feature that doesn't
exist yet) and no rigging states (cut to v2 per [DECISIONS.md](./DECISIONS.md)). Extend the
state machine when those land, not before.

## Single-worker sequential dispatch — load-bearing, not just efficient

`src/pipeline.ts` holds an in-process `processing` boolean lock: only one job's worker calls
are ever in flight at a time, and each stage is `await`ed before the next starts. This isn't
an efficiency nicety — [PHASE2_WORKERS.md](./PHASE2_WORKERS.md) documents an empirical
finding that running two GPU workers' models loaded at once **wedges the 8GB card instead of
failing cleanly** (100% util, no OOM, no progress for 7+ minutes, required a manual `kill -9`
to recover). The lock is what stands between "one GPU" and that failure mode. Do not make job
processing concurrent without addressing that first.

## HTTP API

| Route | Purpose |
|---|---|
| `POST /jobs` | `{prompt, params}` → creates a job, kicks the queue, returns it (status `queued`) |
| `GET /jobs/:id` | Current status, recipe, and output paths for one job |
| `GET /jobs` | Recent jobs, newest first |
| `GET /health` | Orchestrator's own status plus a live health fan-out to all three workers |

`params` passes straight through to the workers (`seed`, `width`, `height`, `steps`,
`resolution`, `threshold`, `target_tris`, `scale`, `origin`) — see
[WORKER_CONTRACT.md](./WORKER_CONTRACT.md) for what each means.

## Gotcha: paths cross the WSL/Windows filesystem boundary mid-pipeline

`imggen` and `meshgen` both run inside WSL2 and hand back paths in WSL's view of the
filesystem (`/mnt/c/Users/...`). `blender` runs on native Windows and needs the Windows-drive
form (`C:/Users/...`). The first real pipeline run failed here:

```
blender worker failed: {"error":"no such file: /mnt/c/Users/.../raw.glb"}
```

— `meshgen`'s output path was handed straight to `blender` unmodified. Fixed with a small
translator in `src/workers.ts` (`wslToWindowsPath`), applied specifically at the
`meshgen` → `blender` handoff, the one place in this pipeline a path crosses that boundary.

**General lesson:** any path passed between a WSL-hosted worker and a Windows-hosted worker
needs this translation. If a future worker introduces another such crossing, it needs its own
translation call — this isn't handled generically, just at the one seam that exists today.

## Verified working

```bash
cd orchestrator && npm install && npm start
```

`POST /jobs` with `{"prompt": "a small red garden watering can on a plain grey background,
studio product photo", "params": {"target_tris": 6000}}` produced, via one API call:

- `scratch/<job-id>/image.png` — the generated concept image
- `scratch/<job-id>/raw.glb` — the raw TripoSR mesh (107,048 tris; not watertight this time —
  the can's thin spout and open handle loop are genuinely hard geometry for single-view
  reconstruction to close cleanly, unlike the tractor test case in
  [MESH_GEN.md](./MESH_GEN.md))
- `scratch/<job-id>/clean/asset.{glb,fbx,stl}` — decimated to 6,000 tris, normals fixed, UVs
  unwrapped, origin at base

Job record's `recipe` field captured real seed/dims/steps from imggen, tris/watertight/
threshold from meshgen, and tris-before/after from blender — the reproducibility data the
master plan calls for.

**Honest quality note:** the cleaned watering-can mesh came out visibly rougher than the
tractor test case (some pinching/faceting near the spout and handle). Aggressive decimation
(107K → 6K, a ~94% reduction) is harder on thin protruding features than on a chunky shape —
a real tuning tradeoff in the default `target_tris`, not a pipeline bug. Worth revisiting
per-asset-class defaults once there's a UI to actually see results before committing to them.

## Still open

- No retry logic — a failed job just sits `failed`; the orchestrator "owns retry" per
  [WORKER_CONTRACT.md](./WORKER_CONTRACT.md) but nothing implements it yet.
- No SSE progress streaming yet (master plan §5) — polling `GET /jobs/:id` works for now.
- Restarting the orchestrator mid-pipeline leaves an in-flight job stuck at whatever
  non-terminal status it was in; nothing resumes it automatically (deliberate — resuming
  needs care about what a worker already wrote to `scratch/`).
