"""blender worker — mesh cleanup, port 8104.

Thin FastAPI wrapper around cleanup.py. Unlike imggen/meshgen, Blender's Python API isn't a
natural long-running async server, so each /run shells out to a fresh
`blender --background --python cleanup.py` subprocess — the same pattern already used for
the fixture cube (make_fixture_cube.py). This worker needs no CUDA and no special venv; it
runs on plain Windows Python, same as the stub worker (see docs/WORKER_CONTRACT.md).

    python server.py --port 8104 [--blender "C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe"]
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CLEANUP_SCRIPT = Path(__file__).resolve().parent / "cleanup.py"
DEFAULT_BLENDER = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
WORKER_NAME = "blender"
MODEL_NAME = "blender-5.2-headless"

app = FastAPI()
BLENDER_EXE = DEFAULT_BLENDER


class RunRequest(BaseModel):
    job_id: str
    inputs: dict = {}
    params: dict = {}


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok" if Path(BLENDER_EXE).exists() else "error: blender.exe not found",
        "worker": WORKER_NAME,
        "model": MODEL_NAME,
        "commit": "n/a",
        "device": "cpu",
    }


@app.post("/run")
def run(req: RunRequest) -> dict:
    mesh_in = req.inputs.get("mesh")
    if not mesh_in:
        raise HTTPException(400, {"error": "inputs.mesh is required"})
    if not Path(mesh_in).exists():
        raise HTTPException(400, {"error": f"no such file: {mesh_in}"})

    target_tris = req.params.get("target_tris", 8000)
    scale = req.params.get("scale", 1.0)
    origin = req.params.get("origin", "base")

    job_dir = REPO_ROOT / "scratch" / req.job_id / "clean"
    name = req.params.get("name", "asset")

    cmd = [
        BLENDER_EXE, "--background", "--python", str(CLEANUP_SCRIPT), "--",
        "--input", mesh_in,
        "--output-dir", str(job_dir),
        "--name", name,
        "--target-tris", str(target_tris),
        "--scale", str(scale),
        "--origin", origin,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise HTTPException(502, {"error": f"blender exited {proc.returncode}: {proc.stderr[-2000:]}"})

    match = re.search(r"^RESULT_JSON:(.+)$", proc.stdout, re.MULTILINE)
    if not match:
        raise HTTPException(502, {"error": "blender ran but produced no RESULT_JSON", "stdout": proc.stdout[-2000:]})
    result = json.loads(match.group(1))

    return {
        "job_id": req.job_id,
        "outputs": {"glb": result["glb"], "fbx": result["fbx"], "stl": result["stl"]},
        "meta": {
            "tris_before": result["tris_before"],
            "tris_after": result["tris_after"],
            "target_tris": target_tris,
            "model": MODEL_NAME,
        },
    }


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8104)
    parser.add_argument("--blender", default=DEFAULT_BLENDER)
    args = parser.parse_args()
    BLENDER_EXE = args.blender
    uvicorn.run(app, host="127.0.0.1", port=args.port)
