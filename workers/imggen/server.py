"""imggen worker — text to image via Z-Image-Turbo.

Wraps the ComfyUI backend proven in Phase 1 (docs/COMFYUI_SETUP.md): ComfyUI is an
implementation detail here, not a dependency of the contract. This worker launches it if
not already running and drives it over its own HTTP API. Runs in the ComfyUI venv
(~/comfyui/.venv in WSL), which is where the model weights and torch/cu126 install live.

    python server.py --port 8101

See docs/WORKER_CONTRACT.md for the endpoint contract this implements.
"""
import argparse
import random
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMFYUI_DIR = Path.home() / "comfyui"
COMFYUI_URL = "http://127.0.0.1:8188"
WORKER_NAME = "imggen"
MODEL_NAME = "z-image-turbo-int8"

app = FastAPI()


def _comfy_alive() -> bool:
    try:
        return requests.get(f"{COMFYUI_URL}/system_stats", timeout=2).status_code == 200
    except requests.RequestException:
        return False


def _ensure_comfy_running() -> None:
    if _comfy_alive():
        return
    log = open("/tmp/comfyui_server.log", "a")
    subprocess.Popen(
        ["python", "main.py", "--listen", "127.0.0.1", "--port", "8188"],
        cwd=str(COMFYUI_DIR),
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    for _ in range(90):
        if _comfy_alive():
            return
        time.sleep(1)
    raise RuntimeError("ComfyUI did not become ready in time")


def _build_workflow(prompt: str, seed: int, width: int, height: int, steps: int, filename_prefix: str) -> dict:
    # Graph mirrors comfyui_workflow_templates_json/templates/image_z_image_turbo_int8.json —
    # see docs/COMFYUI_SETUP.md for why each piece is here (ConditioningZeroOut instead of a
    # real negative prompt, ModelSamplingAuraFlow shift, the turbo sampler settings).
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "z_image_turbo_int8_convrot.safetensors", "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_4b_fp8_mixed.safetensors", "type": "lumina2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
        "6": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "7": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3}},
        "8": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["7", 0],
                "positive": ["4", 0],
                "negative": ["5", 0],
                "latent_image": ["6", 0],
                "seed": seed,
                "steps": steps,
                "cfg": 1,
                "sampler_name": "res_multistep",
                "scheduler": "simple",
                "denoise": 1,
            },
        },
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": filename_prefix}},
    }


class RunRequest(BaseModel):
    job_id: str
    inputs: dict = {}
    params: dict = {}


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok" if _comfy_alive() else "starting",
        "worker": WORKER_NAME,
        "model": MODEL_NAME,
        "commit": "n/a",
        "device": "cuda:0",
    }


@app.post("/run")
def run(req: RunRequest) -> dict:
    prompt = req.params.get("prompt") or req.inputs.get("prompt")
    if not prompt:
        raise HTTPException(400, {"error": "params.prompt (or inputs.prompt) is required"})

    seed = req.params.get("seed")
    if seed is None:
        seed = random.randint(0, 2**31 - 1)
    width = req.params.get("width", 1024)
    height = req.params.get("height", 1024)
    steps = req.params.get("steps", 8)

    _ensure_comfy_running()

    filename_prefix = f"imggen_{req.job_id}_{uuid.uuid4().hex[:8]}"
    workflow = _build_workflow(prompt, seed, width, height, steps, filename_prefix)

    resp = requests.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow}, timeout=30)
    if resp.status_code != 200:
        raise HTTPException(502, {"error": f"ComfyUI rejected prompt: {resp.text}"})
    prompt_id = resp.json()["prompt_id"]

    for _ in range(240):  # up to ~4 minutes, generous for a cold model load
        history = requests.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=10).json()
        entry = history.get(prompt_id)
        if entry:
            status = entry["status"]
            if status["status_str"] == "error":
                raise HTTPException(502, {"error": status["messages"]})
            if status.get("completed"):
                images = entry["outputs"]["10"]["images"]
                src = COMFYUI_DIR / "output" / images[0]["filename"]
                job_dir = REPO_ROOT / "scratch" / req.job_id
                job_dir.mkdir(parents=True, exist_ok=True)
                dest = job_dir / "image.png"
                # copyfile, not copy: copy() also chmods the destination to match the
                # source, which /mnt/c (WSL's DrvFs mount) rejects with EPERM.
                shutil.copyfile(src, dest)
                return {
                    "job_id": req.job_id,
                    "outputs": {"image": str(dest.resolve())},
                    "meta": {"seed": seed, "width": width, "height": height, "steps": steps, "model": MODEL_NAME},
                }
        time.sleep(1)
    raise HTTPException(504, {"error": "timed out waiting for ComfyUI to finish the job"})


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8101)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port)
