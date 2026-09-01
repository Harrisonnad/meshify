"""rig worker — auto-rigging via SkinTokens, port 8103.

Wraps the ComfyUI-SkinToken package (https://github.com/Rizzlord/ComfyUI-SkinToken) around
VAST-AI's SkinTokens model. Runs in the ComfyUI venv (~/comfyui/.venv) because the vendored
node code imports comfy.model_management and folder_paths directly — unlike meshgen's tsr
package, this isn't cleanly decoupled from ComfyUI, so isolating it into its own venv (the
usual rule) isn't achievable without reimplementing those modules. Bypasses ComfyUI's node
graph/HTTP server entirely though: imports the node class directly and calls it in-process,
same pattern as meshgen.

See docs/RIG.md for the full story: UniRig and the official SkinTokens repo are both blocked
by a flash-attn/SM-8.0+ hardware requirement (we're Turing, SM 7.5); this community wrapper
gates flash-attn behind a working PyTorch SDPA fallback instead. Also needs a *native Linux*
Blender inside WSL for its export step (separate from the Windows Blender the `blender`
worker uses) — the vendored code passes temp files by Linux path to whatever
SKINTOKEN_BLENDER_BIN points at, and a Windows-hosted Blender.exe can't resolve those.

    python server.py --port 8103

See docs/WORKER_CONTRACT.md for the endpoint contract this implements.
"""
import argparse
import importlib.util
import os
import shutil
import sys
import types
from pathlib import Path

os.environ.setdefault("SKINTOKEN_BLENDER_BIN", str(Path.home() / "blender-5.2.0-linux-x64" / "blender"))

COMFYUI_DIR = Path.home() / "comfyui"
sys.path.insert(0, str(COMFYUI_DIR))

# sktn_nodes.py does `from .vendor.skintokens...` (a relative import), which needs a real
# parent package context that a plain sys.path import doesn't provide. Register a dummy
# package pointing at the custom node's own directory so the relative import resolves.
CUSTOM_NODE_DIR = COMFYUI_DIR / "custom_nodes" / "ComfyUI-SkinToken"
_PKG_NAME = "skintoken_pkg"
_pkg = types.ModuleType(_PKG_NAME)
_pkg.__path__ = [str(CUSTOM_NODE_DIR)]
sys.modules[_PKG_NAME] = _pkg
_spec = importlib.util.spec_from_file_location(
    f"{_PKG_NAME}.sktn_nodes", str(CUSTOM_NODE_DIR / "sktn_nodes.py"), submodule_search_locations=[str(CUSTOM_NODE_DIR)]
)
sktn_nodes = importlib.util.module_from_spec(_spec)
sys.modules[f"{_PKG_NAME}.sktn_nodes"] = sktn_nodes
_spec.loader.exec_module(sktn_nodes)

import trimesh as Trimesh
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKER_NAME = "rig"
MODEL_NAME = "skintokens"

app = FastAPI()
_rig_node = None


def _get_rig_node():
    global _rig_node
    if _rig_node is None:
        _rig_node = sktn_nodes.SkinTokenRigTrimesh()
    return _rig_node


def _load_mesh(path: str) -> "Trimesh.Trimesh":
    loaded = Trimesh.load(path, process=False)
    if isinstance(loaded, Trimesh.Scene):
        return Trimesh.util.concatenate(list(loaded.geometry.values()))
    return loaded


class RunRequest(BaseModel):
    job_id: str
    inputs: dict = {}
    params: dict = {}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "worker": WORKER_NAME, "model": MODEL_NAME, "commit": "n/a", "device": "cuda:0"}


@app.post("/run")
def run(req: RunRequest) -> dict:
    mesh_path = req.inputs.get("mesh")
    if not mesh_path:
        raise HTTPException(400, {"error": "inputs.mesh is required"})
    if not Path(mesh_path).exists():
        raise HTTPException(400, {"error": f"no such file: {mesh_path}"})

    file_format = req.params.get("file_format", "glb")
    mesh = _load_mesh(mesh_path)

    node = _get_rig_node()
    output_mesh, rigged_path, _asset, backend = node.rig(
        trimesh=mesh,
        ckpt_name=sktn_nodes.DEFAULT_TOKENRIG_CKPT,
        device="auto",
        save_file=True,
        filename_prefix=f"rig_{req.job_id}_",
        file_format=file_format,
        use_transfer=req.params.get("use_transfer", False),
        use_postprocess=req.params.get("use_postprocess", False),
        group_per_vertex=req.params.get("group_per_vertex", 4),
        # Origin is already placed by the blender worker's cleanup step, which runs before
        # this stage in the pipeline - re-centering here would be redundant.
        bottom_center_origin=req.params.get("bottom_center_origin", False),
        smooth_angle=req.params.get("smooth_angle", 55.0),
        skeleton_template=req.params.get("skeleton_template", "Keep model names"),
        top_k=req.params.get("top_k", 5),
        top_p=req.params.get("top_p", 0.95),
        temperature=req.params.get("temperature", 1.0),
        repetition_penalty=req.params.get("repetition_penalty", 2.0),
        num_beams=req.params.get("num_beams", 10),
    )

    job_dir = REPO_ROOT / "scratch" / req.job_id / "rig"
    job_dir.mkdir(parents=True, exist_ok=True)
    dest = job_dir / f"rigged.{file_format}"
    shutil.copyfile(rigged_path, dest)

    return {
        "job_id": req.job_id,
        "outputs": {"rigged": str(dest.resolve())},
        "meta": {
            "tris": int(len(output_mesh.faces)) if hasattr(output_mesh, "faces") else None,
            "backend": backend,
            "model": MODEL_NAME,
        },
    }


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8103)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port)
