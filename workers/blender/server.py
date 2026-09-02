"""blender worker — mesh cleanup + animation baking, port 8104.

Thin FastAPI wrapper around cleanup.py and animate.py. Unlike imggen/meshgen, Blender's
Python API isn't a natural long-running async server, so each /run shells out to a fresh
`blender --background --python <script>` subprocess — the same pattern already used for
the fixture cube (make_fixture_cube.py). This worker needs no CUDA and no special venv; it
runs on plain Windows Python, same as the stub worker (see docs/WORKER_CONTRACT.md).

Per docs/WORKER_CONTRACT.md, a worker exposes exactly `/health` + `/run` — animation baking
is a second *capability* of this worker, not a second endpoint. `params.task` ("clean",
the default, or "animate") picks which script actually runs.

Pin recorded here per master plan §7 ("pin everything... these repos break constantly" applies
to Blender's own releases too, not just Python packages): verified against
Blender 5.2.0 LTS, hash fbe6228777e7, built 2026-07-14. cleanup.py's operator names
(bpy.ops.wm.stl_export in particular, renamed from export_mesh.stl in earlier Blender
versions) are specific to this release line.

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
ANIMATE_SCRIPT = Path(__file__).resolve().parent / "animate.py"
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

    task = req.params.get("task", "clean")
    if task == "animate":
        return _run_animate(req, mesh_in)
    return _run_clean(req, mesh_in)


def _run_animate(req: RunRequest, mesh_in: str) -> dict:
    job_dir = REPO_ROOT / "scratch" / req.job_id / "rig"
    name = req.params.get("name", "asset")

    cmd = [
        BLENDER_EXE, "--background", "--python", str(ANIMATE_SCRIPT), "--",
        "--input", mesh_in,
        "--output-dir", str(job_dir),
        "--name", name,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise HTTPException(502, {"error": f"blender exited {proc.returncode}: {proc.stderr[-2000:]}"})

    match = re.search(r"^RESULT_JSON:(.+)$", proc.stdout, re.MULTILINE)
    if not match:
        raise HTTPException(502, {"error": "blender ran but produced no RESULT_JSON", "stdout": proc.stdout[-2000:]})
    result = json.loads(match.group(1))

    outputs = {}
    if result.get("animated_glb"):
        outputs["animated"] = result["animated_glb"]

    return {
        "job_id": req.job_id,
        "outputs": outputs,
        "meta": {
            "animated": result.get("animated", False),
            "clips": result.get("clips", []),
            "model": MODEL_NAME,
        },
    }


def _run_clean(req: RunRequest, mesh_in: str) -> dict:
    target_tris = req.params.get("target_tris", 8000)
    scale = req.params.get("scale", 1.0)
    origin = req.params.get("origin", "base")
    smooth_iterations = req.params.get("smooth_iterations", 1)
    smooth_factor = req.params.get("smooth_factor", 2.0)
    retopology = bool(req.params.get("retopology", False))
    bake_texture = bool(req.params.get("bake_texture", False))
    bake_normal = bool(req.params.get("bake_normal", False))
    bake_size = req.params.get("bake_size", 2048)
    roughness_factor = req.params.get("roughness_factor", 0.6)
    metallic_factor = req.params.get("metallic_factor", 0.0)

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
        "--smooth-iterations", str(smooth_iterations),
        "--smooth-factor", str(smooth_factor),
        "--bake-size", str(bake_size),
        "--roughness-factor", str(roughness_factor),
        "--metallic-factor", str(metallic_factor),
    ]
    if retopology:
        cmd.append("--retopology")
    if bake_texture:
        cmd.append("--bake-texture")
    if bake_normal:
        cmd.append("--bake-normal")
    # QuadriFlow remeshing and/or the Cycles bake passes run on top of everything else in
    # cleanup.py, so any of them needs more headroom than the geometry-only 300s budget.
    # Normal baking is the slowest (selected-to-active ray casting against the full-res
    # high-poly source), hence the larger budget when it's on.
    timeout = 900 if bake_normal else (600 if (bake_texture or retopology) else 300)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise HTTPException(502, {"error": f"blender exited {proc.returncode}: {proc.stderr[-2000:]}"})

    match = re.search(r"^RESULT_JSON:(.+)$", proc.stdout, re.MULTILINE)
    if not match:
        raise HTTPException(502, {"error": "blender ran but produced no RESULT_JSON", "stdout": proc.stdout[-2000:]})
    result = json.loads(match.group(1))

    outputs = {"glb": result["glb"], "fbx": result["fbx"], "stl": result["stl"]}
    if result.get("texture"):
        outputs["texture"] = result["texture"]
    if result.get("ao"):
        outputs["ao"] = result["ao"]
    if result.get("normal"):
        outputs["normal"] = result["normal"]

    return {
        "job_id": req.job_id,
        "outputs": outputs,
        "meta": {
            "tris_before": result["tris_before"],
            "tris_after": result["tris_after"],
            "target_tris": target_tris,
            "smoothed": result.get("smoothed", False),
            "retopologized": result.get("retopologized", False),
            "baked_texture": bool(result.get("texture")),
            "baked_normal": bool(result.get("normal")),
            "roughness_factor": result.get("roughness_factor", roughness_factor),
            "metallic_factor": result.get("metallic_factor", metallic_factor),
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
