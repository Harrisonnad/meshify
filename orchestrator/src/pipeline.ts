// Single-worker sequential pipeline: queued -> generating_image -> meshing -> cleaning -> done.
//
// This is not just an efficiency choice. docs/PHASE2_WORKERS.md documents an empirical
// finding: running imggen and meshgen with models loaded in VRAM at the same time doesn't
// fail cleanly, it WEDGES the 8GB card (100% util, no progress, no OOM error) until a process
// is killed by hand. The `processing` lock below is the thing standing between "one GPU" and
// that failure mode — do not make this concurrent.
import { getJob, getNextQueuedJob, updateJob, type JobParams } from "./db.js";
import { runBlender, runImggen, runMeshgen } from "./workers.js";

let processing = false;

export function kickQueue(): void {
  if (processing) return;
  processing = true;
  void runNext().finally(() => {
    processing = false;
  });
}

async function runNext(): Promise<void> {
  const job = getNextQueuedJob();
  if (!job) return;

  const params: JobParams = JSON.parse(job.params);

  try {
    updateJob(job.id, { status: "generating_image" });
    const img = await runImggen(job.id, job.prompt, params);
    updateJob(job.id, {
      outputsPatch: { image: img.outputs.image },
      recipePatch: { imggen: img.meta },
    });

    updateJob(job.id, { status: "meshing" });
    const mesh = await runMeshgen(job.id, img.outputs.image, params);
    updateJob(job.id, {
      outputsPatch: { mesh: mesh.outputs.mesh },
      recipePatch: { meshgen: mesh.meta },
    });

    updateJob(job.id, { status: "cleaning" });
    const clean = await runBlender(job.id, mesh.outputs.mesh, params);
    updateJob(job.id, {
      outputsPatch: { glb: clean.outputs.glb, fbx: clean.outputs.fbx, stl: clean.outputs.stl },
      recipePatch: { blender: clean.meta },
    });

    updateJob(job.id, { status: "done" });
  } catch (err) {
    updateJob(job.id, { status: "failed", error: err instanceof Error ? err.message : String(err) });
  }

  // A job finished (or failed) — see if another is waiting. Recursing here (rather than
  // looping) keeps each job's try/catch scoped to itself.
  await runNext();
}

export { getJob };
