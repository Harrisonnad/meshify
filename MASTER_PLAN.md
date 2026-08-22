# Local 3D Asset Forge — Master Plan

A self-hosted text/image → rigged, game-ready 3D asset pipeline. Replaces Shapecast with an
open-weight stack you own, extend, and swap models into as better ones ship.

---

## 0. Definition of done

You type a prompt (or drop an image), and 3–10 minutes later you have:

- A watertight mesh at real-world scale
- 2K–4K PBR textures (albedo / normal / roughness / metallic)
- An optional skeleton + skin weights for anything character-shaped
- Exports in `.glb`, `.fbx`, and `.stl`
- A saved "recipe" (prompt, seed, model versions, settings) so any asset is reproducible
- All of it triggered from a browser tab, running entirely on your machine

Explicit non-goals for v1: multi-object scenes, retopology to hand-authored quad density,
face blendshapes, cloud/multi-user. Ship the spine first.

---

## 1. The architecture decision that makes or breaks this

**Do not put all the models in one Python environment.** TRELLIS, Hunyuan3D, UniRig, and the
image models each pin different CUDA/PyTorch/xformers combinations. A single shared venv is
where this project goes to die.

Instead:

```
┌─────────────────────────────────────────────────┐
│  Web UI (React + TS + Vite)                     │  ← your home turf
└────────────────────┬────────────────────────────┘
                     │ REST + SSE (progress stream)
┌────────────────────▼────────────────────────────┐
│  Orchestrator (Node/TS or FastAPI)              │
│  - job queue (SQLite-backed, single worker)     │
│  - recipe store, artifact registry              │
│  - stage sequencing + retry                     │
└──┬──────────┬──────────┬──────────┬─────────────┘
   │          │          │          │  HTTP, localhost only
┌──▼───┐  ┌───▼────┐  ┌──▼─────┐  ┌─▼──────────┐
│ img  │  │ mesh   │  │ rig    │  │ blender    │
│ gen  │  │ gen    │  │ worker │  │ headless   │
│ venv │  │ venv   │  │ venv   │  │ (its own   │
│ :8101│  │ :8102  │  │ :8103  │  │  python)   │
└──────┘  └────────┘  └────────┘  └────────────┘
```

**Rules for the worker contract:**

- Every worker is a ~100-line FastAPI app exposing `POST /run` and `GET /health`.
- Payloads are **file paths on a shared scratch dir**, never base64 blobs. A 200MB mesh over
  JSON will ruin your day.
- Every worker gets its own `venv` (or conda env) and its own `requirements.lock`.
- Workers are dumb. All sequencing, retry, and state lives in the orchestrator.

This means a model upgrade is "rebuild one venv," not "rebuild everything." It also means you
can develop the UI against stubbed workers that return a fixed cube.

**Language call:** write the orchestrator in TypeScript (Fastify or Express) if you want the
whole app-layer in one language. Write it in Python/FastAPI if you'd rather share Pydantic
models with the workers. Either is defensible — pick TS, since the UI is the part you'll
iterate on most and you'll move faster with one mental model.

---

## 2. Phase 0 — Hardware & environment gate (half a day)

Do this before writing any code. It determines which models are even on the table.

**Check your VRAM.** `nvidia-smi` on Linux/Windows.

| VRAM | What's realistic |
|---|---|
| 24GB+ | TRELLIS 2 at full quality, Hunyuan3D, everything below. Target tier. |
| 12–16GB | TRELLIS 2 with offloading (slow but works), Hunyuan3D at reduced settings, InstantMesh comfortably |
| 8–10GB | TripoSR / InstantMesh only. Quality drop is real but usable for stylized/low-poly work |
| AMD / Apple Silicon | Significant friction. Most of these repos assume CUDA. Budget extra time or plan a rented GPU fallback |

**OS call.** If you're on Windows, do this inside WSL2 with CUDA passthrough, not native
Windows Python. Roughly half the flash-attention and CUDA-extension build steps in these repos
assume Linux, and native Windows will cost you days.

**Deliverable for this phase:** a `hardware.md` in the repo recording GPU, VRAM, driver
version, CUDA version, OS. You will reference this constantly when a repo's README asks
"which CUDA?"

---

## 3. Phase 1 — Prove the core (1 weekend)

**Goal: one image → one mesh, on your machine, from the command line. No UI, no API.**

1. Pick your mesh generator based on the VRAM table above. Start with **TRELLIS 2** if you
   have the headroom (MIT-licensed, best current open quality), **InstantMesh** if you don't.
2. Clone it, build its env, run its own demo/gradio script unmodified until it works.
3. Only then feed it your own image — a clean subject, plain background, 512px+.
4. Open the result in Blender. Check: is it watertight? What's the tri count? Is the scale sane?

**Do not proceed until this works.** This is the single highest-risk step and everything else
is downstream of it. If you stall here for more than a weekend, that's your signal that the
$149 is the better trade.

**Exit criteria:** a `.glb` on disk that opens in Godot without complaint.

---

## 4. Phase 2 — Build out the stages (2–3 weekends)

Add one worker at a time. Each becomes an isolated venv + FastAPI wrapper only *after* you've
run it successfully from the CLI.

### 2a. Text → image (so you get text-to-3D)
- **Qwen-Image 2.0** (Apache 2.0, native 2K, permissive) or **Z-Image-Turbo** (6B, fits in
  16GB, sub-second-ish inference)
- Add background removal (`rembg` or BiRefNet) as a post-step — mesh generators want clean
  alpha, and this single step improves output quality more than almost anything else
- Generate 4 candidates per prompt so the UI can offer a pick-a-concept step

### 2b. Mesh generation
- Wrap whatever you proved in Phase 1
- Expose seed, and record it — this is what makes recipes reproducible

### 2c. Mesh cleanup (Blender headless)
This is unglamorous and it's what separates "AI slop mesh" from "asset I'll actually ship."

`blender --background --python clean.py -- --input x.glb --output y.glb`

The script should: decimate to a target tri budget, recalculate normals, apply real-world
scale, smart-UV-unwrap if UVs are missing, bake a normal map from the high-poly to the
decimated version, and set the origin sensibly (feet on ground for characters, base for props).

### 2d. Auto-rigging
- **UniRig** (VAST-AI + Tsinghua) — skeleton prediction + skinning weights, handles bipeds,
  quadrupeds, and weirder topologies
- Alternatives if UniRig fights you: SkinTokens, Make-It-Animatable, RigAnything
- **Motion clips:** don't build this. Export a humanoid-standard skeleton and use Mixamo's
  free library, or retarget existing clips in Blender. Generating motion from scratch is a
  whole second project.

### 2e. Export
FBX + GLB + STL from one Blender pass. Godot wants GLB. Unreal is happier with FBX. Your
printer wants STL. Generate all three; storage is cheap.

---

## 5. Phase 3 — Orchestrator (1 weekend)

- SQLite for jobs + recipes. No Postgres, no Docker Compose sprawl. One file.
- Job states: `queued → generating_concepts → awaiting_selection → meshing → cleaning → rigging → exporting → done | failed`
- **Single-worker queue.** You have one GPU; concurrency buys you nothing but OOM errors.
- Stream progress over SSE. Each worker reports coarse percentage; that's enough.
- Persist the recipe on every job: prompt, seed, each model's name + commit hash, every
  setting. This is the feature that makes the tool actually valuable six months in.
- Artifacts go to a content-addressed dir (`/assets/<job-id>/`) with a manifest JSON.

---

## 6. Phase 4 — UI (1–2 weekends, and the fun part)

This is where your day job makes you dramatically faster than the average person attempting
this. Scope for v1:

- **Prompt/upload panel** — text box, image drop zone, style presets
- **Concept picker** — 4 candidate images, click to proceed
- **Job progress** — stage-by-stage, with the intermediate artifact visible at each step
- **Model viewer** — `<model-viewer>` web component or react-three-fiber. Orbit, wireframe
  toggle, tri count, bounding-box dimensions readout
- **Library** — grid of past assets, filter by tag, re-run from recipe, export buttons
- **Settings** — target tri budget, texture resolution, output formats, scale unit

Accessibility note for your own sanity: keyboard-navigable job list and proper focus
management on the concept picker. You'll be tabbing through this thing constantly.

---

## 7. Phase 5 — Make it survivable (ongoing)

- **Pin everything.** Each worker gets a lockfile *and* a recorded upstream commit SHA. These
  repos break constantly.
- **Smoke test in CI.** A GitHub Actions job that runs the orchestrator with stubbed workers
  against a golden fixture — catches contract drift without needing a GPU runner. You've built
  these pipelines before; same YAML shape as your day job.
- **One-command bring-up.** A `make dev` that starts all workers and the UI. You will forget
  the port assignments within two weeks.
- **Model swap test.** Deliberately swap the mesh generator once, early. If it takes more than
  an hour, your abstraction is wrong — fix it while the codebase is small.

---

## 8. License audit — do this before you ship anything commercially

You intend to put these assets in games you sell. That makes licensing a real task, not a
footnote. For each model, verify **separately**:

- The **code** license and the **weights** license (they frequently differ)
- Whether commercial use is permitted, or whether it's research/non-commercial only
- Territory or scale restrictions — some vendor "community" licenses carve out specific
  regions or MAU thresholds
- Whether outputs are covered by any downstream terms

Known shapes to check carefully: TRELLIS's MIT terms are clean; Tencent's Hunyuan community
licenses have historically carried territory and scale conditions; several academic rigging
models release code permissively but weights under research-only terms. Confirm current terms
directly from each repo before committing — don't take my summary as the final word.

Record the result in a `LICENSES.md` per model. Also note that Steam and most platforms
currently ask for AI-usage disclosure at submission — a checkbox, not a blocker, but check the
current policy for wherever you're shipping.

---

## 9. Realistic timeline

| Phase | Effort | Cumulative |
|---|---|---|
| 0 — Hardware gate | 0.5 day | — |
| 1 — Prove the core | 1 weekend | Week 1 |
| 2 — Stage workers | 2–3 weekends | Week 4 |
| 3 — Orchestrator | 1 weekend | Week 5 |
| 4 — UI | 1–2 weekends | Week 7 |
| 5 — Hardening | ongoing | — |

Roughly 6–7 weekends of evening-and-weekend pace to a tool you'd actually use daily. Phase 1
is where the variance lives — it's either a Saturday afternoon or a two-week slog depending
entirely on your GPU and driver situation.

---

## 10. Fast-path alternative

If the goal is *assets for your games* rather than *a tool you built*, there's a shorter road:
**ComfyUI** already has community nodes for most of this stack, including 3D generation and
UniRig rigging. You'd be wiring a graph instead of writing an inference layer — days instead of
weeks, at the cost of a clunkier UX and less control over the pipeline.

A reasonable hybrid: use ComfyUI to validate the whole chain end-to-end and learn which models
actually work on your hardware, then build the custom orchestrator + UI around the models that
survived. That de-risks Phase 1 substantially.

---

## 11. Decisions still open

- GPU and VRAM (drives every model choice above)
- OS / WSL2 vs native Linux
- Orchestrator language: TS vs Python
- Primary target: Godot 4 (GLB, low-poly friendly) vs Unreal (FBX, higher fidelity) — this
  changes your default tri budget and texture resolution
- Whether rigging is v1 or v2 scope. Cutting it makes Phase 2 roughly 40% shorter, and props
  and terrain assets don't need it at all
