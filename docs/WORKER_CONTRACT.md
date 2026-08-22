# Worker Contract

Master plan §1. Every worker is a small FastAPI app in **its own venv**, speaking the same
two endpoints. Workers are dumb: all sequencing, retry, and state live in the orchestrator.

## Why isolation is non-negotiable

TRELLIS, InstantMesh, UniRig, and the image models pin mutually incompatible
CUDA/PyTorch/xformers combinations. A shared venv is where this project dies. One venv per
worker means a model upgrade is "rebuild one venv," not "rebuild everything."

On this machine there's a second reason: system Python is 3.12, but most of these repos pin
3.10/3.11. Each worker installs its own interpreter — never reach for the system one.

## Endpoints

### `GET /health`
```json
{ "status": "ok", "worker": "meshgen", "model": "instantmesh", "commit": "a1b2c3d", "device": "cuda:0" }
```
Orchestrator polls this on bring-up. `model` and `commit` feed the recipe (§5) — that's what
makes an asset reproducible six months later.

### `POST /run`
```json
{ "job_id": "01J...", "inputs": { "image": "/scratch/01J.../concept_2.png" }, "params": { "seed": 42 } }
```
Returns:
```json
{ "job_id": "01J...", "outputs": { "mesh": "/scratch/01J.../raw.glb" }, "meta": { "seed": 42, "tris": 24680 } }
```

## Rules

1. **Paths, never blobs.** Inputs and outputs are file paths inside the shared scratch dir.
   A 200 MB mesh over JSON will ruin your day.
2. **Localhost only.** No worker binds a public interface.
3. **Echo the seed.** Every worker that uses randomness reports the seed it actually used,
   even when one was supplied. Recipes are worthless if the seed is a lie.
4. **Fail loudly.** Non-2xx with `{"error": "..."}`. Don't retry internally — the
   orchestrator owns retry.
5. **Lock everything.** Each worker ships `requirements.lock` *and* records the upstream
   repo commit SHA. These repos break constantly (§7).

## Port assignments

| Port | Worker | Status |
|---|---|---|
| 8101 | imggen — text to image + background removal | stub |
| 8102 | meshgen — image to mesh | stub |
| 8103 | rig — skeleton + skin weights | **v2, not built** |
| 8104 | blender — cleanup, bake, export | stub |

Rigging is cut from v1 (see [DECISIONS.md](./DECISIONS.md)). Port 8103 is reserved so the
sequence doesn't shuffle when it lands.

## Stub mode

Every worker runs with `STUB=1`, returning `workers/blender/fixtures/cube.glb` after a short
fake delay. This is what lets the UI and orchestrator be built and demoed before a single
GPU model is installed — which on this hardware may be a while.
