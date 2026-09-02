// Single-worker sequential pipeline: queued -> generating_image -> meshing -> cleaning -> done.
//
// This is not just an efficiency choice. docs/PHASE2_WORKERS.md documents an empirical
// finding: running imggen and meshgen with models loaded in VRAM at the same time doesn't
// fail cleanly, it WEDGES the 8GB card (100% util, no progress, no OOM error) until a process
// is killed by hand. The `processing` lock below is the thing standing between "one GPU" and
// that failure mode — do not make this concurrent.
import { getJob, getNextQueuedJob, updateJob, type JobParams } from "./db.js";
import { runBlender, runImggen, runMeshgen, runRig } from "./workers.js";

// Worker contract rule 4: "orchestrator owns retry, workers don't retry internally."
// Capped low — a job that fails 3 times in a row is very unlikely to succeed on a 4th
// identical attempt, and retrying forever would just mask a real bug as "still queued."
const MAX_RETRIES = 2;

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
    const cleanOutputs: Record<string, string> = { glb: clean.outputs.glb, fbx: clean.outputs.fbx, stl: clean.outputs.stl };
    if (clean.outputs.texture) cleanOutputs.texture = clean.outputs.texture;
    if (clean.outputs.ao) cleanOutputs.ao = clean.outputs.ao;
    updateJob(job.id, {
      outputsPatch: cleanOutputs,
      recipePatch: { blender: clean.meta },
    });

    // Opt-in: most assets (props, walls, terrain) don't need a skeleton, per the original
    // rationale in docs/DECISIONS.md — rigging only runs when a job explicitly asks for it.
    if (params.rig) {
      updateJob(job.id, { status: "rigging" });
      const rig = await runRig(job.id, clean.outputs.glb, params);
      updateJob(job.id, {
        outputsPatch: { rigged: rig.outputs.rigged },
        recipePatch: { rig: rig.meta },
      });
    }

    updateJob(job.id, { status: "done" });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    if (job.retries < MAX_RETRIES) {
      // Back to the queue, not straight to "failed" — getNextQueuedJob will pick this up
      // again on a later pass (behind anything already queued ahead of it).
      updateJob(job.id, { status: "queued", retries: job.retries + 1, error: message });
    } else {
      updateJob(job.id, {
        status: "failed",
        error: `failed after ${job.retries} ${job.retries === 1 ? "retry" : "retries"}: ${message}`,
      });
    }
  }

  // A job finished (or failed) — see if another is waiting. Recursing here (rather than
  // looping) keeps each job's try/catch scoped to itself.
  await runNext();
}

export { getJob };
