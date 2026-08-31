"""Stub worker — the reference implementation of the contract in docs/WORKER_CONTRACT.md.

Runs any of the pipeline stages in fake mode, returning the fixed cube fixture after a
short delay. Master plan §1: "you can develop the UI against stubbed workers that return a
fixed cube." Real workers replace the body of `run_stage` and nothing else.

  python stub_worker.py --worker meshgen --port 8102

Deliberately dependency-light (fastapi + uvicorn only) so it runs in a throwaway venv on any
machine, including one with no CUDA at all.
"""
import argparse
import asyncio
import random
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_CUBE = REPO_ROOT / "workers" / "blender" / "fixtures" / "cube.glb"
SCRATCH = REPO_ROOT / "scratch"

# Fake latency per stage, roughly proportional to the real thing, so UI progress
# pacing looks believable before any model exists.
STAGE_DELAY_SECONDS = {"imggen": 3.0, "meshgen": 8.0, "rig": 5.0, "blender": 2.0}

app = FastAPI()
WORKER_NAME = "stub"


class RunRequest(BaseModel):
    job_id: str
    inputs: dict = {}
    params: dict = {}


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "worker": WORKER_NAME,
        "model": "stub",
        "commit": "stub",
        "device": "cpu",
        "stub": True,
    }


@app.post("/run")
async def run(req: RunRequest) -> dict:
    if not FIXTURE_CUBE.exists():
        raise HTTPException(
            status_code=500,
            detail=f"fixture missing: {FIXTURE_CUBE}. Regenerate with "
                   "blender --background --python workers/blender/make_fixture_cube.py "
                   "-- --output fixtures/cube.glb",
        )

    await asyncio.sleep(STAGE_DELAY_SECONDS.get(WORKER_NAME, 2.0))

    # Rule 3: echo the seed actually used, even when one was supplied.
    seed = req.params.get("seed")
    if seed is None:
        seed = random.randint(0, 2**31 - 1)

    job_dir = SCRATCH / req.job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # Output keys match the real workers' contracts exactly (workers/imggen, workers/meshgen,
    # workers/blender) so the orchestrator's pipeline.ts can run against stubs unmodified —
    # this is what makes the CI smoke test meaningful rather than trivially passing.
    if WORKER_NAME == "imggen":
        out = job_dir / "image.png"
        out.write_bytes(FIXTURE_CUBE.read_bytes())
        outputs = {"image": str(out)}
    elif WORKER_NAME == "blender":
        clean_dir = job_dir / "clean"
        clean_dir.mkdir(exist_ok=True)
        outputs = {}
        for fmt in ("glb", "fbx", "stl"):
            out = clean_dir / f"asset.{fmt}"
            out.write_bytes(FIXTURE_CUBE.read_bytes())
            outputs[fmt] = str(out)
    else:  # meshgen (mesh), rig (v2, not wired into the orchestrator yet)
        out = job_dir / "raw.glb"
        out.write_bytes(FIXTURE_CUBE.read_bytes())
        outputs = {"mesh": str(out)}

    # Rule 1: hand back a path, never the bytes.
    return {
        "job_id": req.job_id,
        "outputs": outputs,
        "meta": {"seed": seed, "tris": 12, "stub": True},
    }


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", default="stub", choices=["imggen", "meshgen", "rig", "blender"])
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    WORKER_NAME = args.worker
    # Rule 2: localhost only.
    uvicorn.run(app, host="127.0.0.1", port=args.port)
