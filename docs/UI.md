# Phase 4 — UI

Status as of 2026-08-31: **Working v1, verified in a real browser against the live
orchestrator** — prompt → submit → live progress → interactive 3D result → library, actually
driven with Playwright and screenshotted, not just typechecked.

## Stack: React + TypeScript + Vite, `<model-viewer>` for 3D

Matches the architecture in [README.md](../README.md). No React Three Fiber or other 3D
library — `<model-viewer>` (Google's web component, loaded via a CDN `<script>` in
`index.html`) does orbit controls, lighting, and GLB loading out of the box, which is all v1
needs. A `model-viewer.d.ts` ambient declaration teaches JSX about the custom element.

## Scope: matches what the orchestrator actually supports today

The master plan's Phase 4 list includes a 4-candidate concept picker and a full settings
panel. Neither exists yet because the orchestrator doesn't support multi-candidate generation
(each job produces exactly one image) — building picker UI for a backend capability that
doesn't exist would be speculative. What's here:

- **Prompt + target-tris form** — the two things `POST /jobs` actually accepts today
- **Stage progress** — the five real job states from [ORCHESTRATOR.md](./ORCHESTRATOR.md)
  (`queued → generating_image → meshing → cleaning → done`), polled every 2s while a job is
  in flight
- **Result panel** — `<model-viewer>` on the cleaned GLB, tri-count before/after, watertight
  flag, download links for GLB/FBX/STL/source image
- **Library** — every past job, click to view its result
- **Health badge** — live status of all three workers, polled every 10s

Extend toward the full master-plan list once the backend grows the capability the UI would
be exposing (concept candidates, texture-resolution controls, etc.) — building either side
alone is speculative.

## Gotcha: Vite 8 (bleeding-edge) needs a native binding that fails to resolve here

`npm create vite@latest` pulled in Vite 8 by default, which ships Rolldown (a Rust bundler)
as its default, requiring a platform-specific native binding. It failed to load:

```
Error: Cannot find native binding.
npm has a bug related to optional dependencies (https://github.com/npm/cli/issues/4828)
```

A clean reinstall (the fix the error itself suggests) didn't help — this machine's `npm run`
invocations resolve to a different Node runtime (`v26.7.0`) than what `node --version` reports
on `PATH` (`v20.10.0`), the same two-Node-installs confusion node-gyp hit trying to build
`better-sqlite3` for the orchestrator (see [ORCHESTRATOR.md](./ORCHESTRATOR.md)). Whatever
that mismatch is, it's a recurring source of native-binary resolution failures on this
specific machine, not something wrong with any one package.

**Fix: pinned to Vite 5** (`^5.4.10`, classic Rollup/esbuild — no native binding required) and
downgraded `@vitejs/plugin-react` and `typescript` to matching stable versions. Also dropped
`oxlint` (another bleeding-edge tool Vite 8's scaffold pulled in) since it isn't needed for a
small single-page app. This is the fourth time this session a "grab the latest" install hit a
native-toolchain wall on this machine (CUDA/nvcc/gcc for `torchmcubes`, node-gyp for
`better-sqlite3`, now Rolldown for Vite) — **default to a known-stable major version rather
than `@latest` for anything with native bindings on this machine**, and reach for it
immediately rather than debugging the native build first.

## Verified working

```bash
cd orchestrator && npm start   # port 8100
cd ui && npm run dev           # port 5173
```

Driven with a throwaway Playwright script (no `chromium-cli` available in this environment)
against the real running orchestrator and real worker-generated assets:

- Loaded the page, confirmed the health badge shows all three workers `ok` live
- Selected a completed job from the library — `<model-viewer>` rendered the actual
  vertex-colored GLB (the watering-can test asset) with working orbit controls, and the
  stats/download links matched the job's real recipe data exactly
- Submitted a brand-new job through the actual form (not a direct API call) — a real
  end-to-end run (mug, target_tris 5000) appeared in the library immediately and reached
  `done` via the UI's own polling, with a watertight mesh this time
- Zero browser console errors across every check

## Still open

- No concept picker (needs backend support first — see Scope above)
- No SSE — polling every 2s is adequate for job counts this small
- No settings beyond target-tris (seed override, texture resolution, output-format toggles
  are on the master plan's list but not wired into the form yet)
