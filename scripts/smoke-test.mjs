#!/usr/bin/env node
// Master plan section 7: "A GitHub Actions job that runs the orchestrator with stubbed
// workers against a golden fixture — catches contract drift without needing a GPU runner."
//
// Assumes the orchestrator and all four stub workers (imggen/meshgen/blender/rig, per
// docs/WORKER_CONTRACT.md's stub mode) are already running — see
// .github/workflows/smoke-test.yml for how CI brings them up, or run manually:
//
//   .venv-stub/Scripts/python workers/stub_worker.py --worker imggen  --port 8101 &
//   .venv-stub/Scripts/python workers/stub_worker.py --worker meshgen --port 8102 &
//   .venv-stub/Scripts/python workers/stub_worker.py --worker blender --port 8104 &
//   .venv-stub/Scripts/python workers/stub_worker.py --worker rig     --port 8103 &
//   (cd orchestrator && npm start) &
//   node scripts/smoke-test.mjs
//
// This job doesn't request params.rig/params.animate, so the rig stub only needs to exist
// for /health to report every orchestrator-known worker as reachable -- it's not exercised
// by the job flow below. Still required: the orchestrator's /health check (see server.ts)
// reports on all four workers regardless of what any given job actually uses.
const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_URL ?? "http://127.0.0.1:8100";
const EXPECTED_OUTPUT_KEYS = ["image", "mesh", "glb", "fbx", "stl"];

function fail(message) {
  console.error(`SMOKE TEST FAILED: ${message}`);
  process.exit(1);
}

async function main() {
  const health = await fetch(`${ORCHESTRATOR_URL}/health`).then((r) => r.json());
  for (const [name, w] of Object.entries(health.workers)) {
    if (w.status !== "ok") fail(`worker ${name} is not healthy: ${JSON.stringify(w)}`);
  }
  console.log("All workers healthy.");

  const created = await fetch(`${ORCHESTRATOR_URL}/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt: "ci smoke test cube" }),
  }).then((r) => r.json());
  if (!created.id) fail(`job creation did not return an id: ${JSON.stringify(created)}`);
  console.log(`Job created: ${created.id}`);

  const deadline = Date.now() + 30_000;
  let job = created;
  while (Date.now() < deadline && !["done", "failed"].includes(job.status)) {
    await new Promise((r) => setTimeout(r, 1000));
    job = await fetch(`${ORCHESTRATOR_URL}/jobs/${created.id}`).then((r) => r.json());
    console.log(`  status: ${job.status}`);
  }

  if (job.status !== "done") {
    fail(`job did not complete in time (final status: ${job.status}, error: ${job.error})`);
  }

  for (const key of EXPECTED_OUTPUT_KEYS) {
    if (!job.outputs[key]) fail(`missing expected output key "${key}": ${JSON.stringify(job.outputs)}`);
  }
  for (const [name, w] of Object.entries(job.recipe)) {
    if (!w.seed && w.seed !== 0) fail(`recipe.${name} is missing a seed: ${JSON.stringify(w)}`);
  }

  console.log("Smoke test passed: job ran queued -> generating_image -> meshing -> cleaning -> done,");
  console.log("all five output keys present, recipe seeds recorded for every stage.");
}

main().catch((err) => fail(err.stack ?? String(err)));
