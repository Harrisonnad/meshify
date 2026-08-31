"""meshgen worker — image to watertight mesh via TripoSR.

Proven in Phase 1 (docs/MESH_GEN.md). Imports the `tsr` package directly rather than
shelling out to run.py — same model calls, no subprocess. Runs in TripoSR's own venv
(~/triposr/.venv in WSL); note the CUDA marching-cubes extension (torchmcubes) isn't used
here — see docs/MESH_GEN.md for why, and the isosurface.py patch that falls back to
scikit-image's CPU marching_cubes (fast enough in practice: ~2.4s at resolution 256).

    python server.py --port 8102

See docs/WORKER_CONTRACT.md for the endpoint contract this implements.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import rembg
import torch
from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel

TRIPOSR_DIR = Path.home() / "triposr"
sys.path.insert(0, str(TRIPOSR_DIR))
from tsr.system import TSR  # noqa: E402
from tsr.utils import remove_background, resize_foreground  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKER_NAME = "meshgen"
MODEL_NAME = "triposr"
DEVICE = "cuda:0" if torch.cuda.is_available() else "cpu"

app = FastAPI()
_model = None
_rembg_session = None


def _get_model() -> TSR:
    global _model
    if _model is None:
        _model = TSR.from_pretrained(
            "stabilityai/TripoSR", config_name="config.yaml", weight_name="model.ckpt"
        )
        _model.renderer.set_chunk_size(8192)
        _model.to(DEVICE)
    return _model


def _get_rembg_session():
    global _rembg_session
    if _rembg_session is None:
        _rembg_session = rembg.new_session()
    return _rembg_session


def _preprocess(image_path: str, foreground_ratio: float) -> Image.Image:
    # Mirrors run.py's default (--remove-bg) path exactly: rembg -> resize foreground ->
    # flatten alpha onto a mid-gray background, since the model was trained on that.
    image = remove_background(Image.open(image_path), _get_rembg_session())
    image = resize_foreground(image, foreground_ratio)
    image = np.array(image).astype(np.float32) / 255.0
    image = image[:, :, :3] * image[:, :, 3:4] + (1 - image[:, :, 3:4]) * 0.5
    return Image.fromarray((image * 255.0).astype(np.uint8))


class RunRequest(BaseModel):
    job_id: str
    inputs: dict = {}
    params: dict = {}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "worker": WORKER_NAME, "model": MODEL_NAME, "commit": "n/a", "device": DEVICE}


@app.post("/run")
def run(req: RunRequest) -> dict:
    image_path = req.inputs.get("image")
    if not image_path:
        raise HTTPException(400, {"error": "inputs.image is required"})
    if not Path(image_path).exists():
        raise HTTPException(400, {"error": f"no such file: {image_path}"})

    resolution = req.params.get("resolution", 256)
    threshold = req.params.get("threshold", 25.0)
    foreground_ratio = req.params.get("foreground_ratio", 0.85)

    model = _get_model()
    image = _preprocess(image_path, foreground_ratio)

    with torch.no_grad():
        scene_codes = model([image], device=DEVICE)
    meshes = model.extract_mesh(scene_codes, has_vertex_color=True, resolution=resolution, threshold=threshold)
    mesh = meshes[0]

    job_dir = REPO_ROOT / "scratch" / req.job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    out = job_dir / "raw.glb"
    mesh.export(str(out))

    return {
        "job_id": req.job_id,
        "outputs": {"mesh": str(out.resolve())},
        "meta": {
            "resolution": resolution,
            "threshold": threshold,
            "tris": int(len(mesh.faces)),
            "watertight": bool(mesh.is_watertight),
            "model": MODEL_NAME,
        },
    }


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8102)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port)
