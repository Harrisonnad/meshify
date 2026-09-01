import cors from "@fastify/cors";
import staticFiles from "@fastify/static";
import Fastify from "fastify";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { ulid } from "ulid";
import { getJob, insertJob, listJobs, type JobParams, type JobRow } from "./db.js";
import { kickQueue } from "./pipeline.js";
import { checkHealth } from "./workers.js";

const REPO_ROOT = path.resolve(fileURLToPath(import.meta.url), "../../../");
const SCRATCH_DIR = path.join(REPO_ROOT, "scratch");

const app = Fastify({ logger: true });
await app.register(cors, { origin: true });
// Workers hand back file paths (some WSL-style, some Windows-style — see
// docs/ORCHESTRATOR.md's path-translation gotcha), not URLs. Serving scratch/ statically and
// computing browser-friendly URLs from those paths (see toFileUrl below) is what lets the UI
// actually load a generated .glb without caring which filesystem produced it.
await app.register(staticFiles, { root: SCRATCH_DIR, prefix: "/files/" });

// Any output path is somewhere under .../scratch/... — WSL-style (/mnt/c/.../scratch/x) or
// Windows-style (C:\...\scratch\x). Normalizing slashes and slicing after "scratch/" handles
// both without needing to know which worker produced the path.
function toFileUrl(absolutePath: string): string {
  const normalized = absolutePath.replace(/\\/g, "/");
  const marker = "/scratch/";
  const idx = normalized.toLowerCase().indexOf(marker);
  if (idx === -1) return absolutePath;
  const relative = normalized.slice(idx + marker.length);
  return `/files/${relative}`;
}

function serializeJob(row: JobRow) {
  const outputs = JSON.parse(row.outputs) as Record<string, string>;
  const urls = Object.fromEntries(Object.entries(outputs).map(([k, v]) => [k, toFileUrl(v)]));
  return {
    id: row.id,
    status: row.status,
    prompt: row.prompt,
    params: JSON.parse(row.params),
    recipe: JSON.parse(row.recipe),
    outputs,
    urls,
    error: row.error,
    retries: row.retries,
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

app.get("/health", async () => {
  const [imggen, meshgen, blender, rig] = await Promise.all([
    checkHealth("imggen"),
    checkHealth("meshgen"),
    checkHealth("blender"),
    checkHealth("rig"),
  ]);
  return { status: "ok", workers: { imggen, meshgen, blender, rig } };
});

app.post<{ Body: { prompt: string; params?: JobParams } }>("/jobs", async (req, reply) => {
  const { prompt, params } = req.body ?? ({} as any);
  if (!prompt || typeof prompt !== "string") {
    reply.code(400);
    return { error: "body.prompt (string) is required" };
  }
  const id = ulid();
  const job = insertJob(id, prompt, params ?? {});
  kickQueue();
  reply.code(201);
  return serializeJob(job);
});

app.get<{ Params: { id: string } }>("/jobs/:id", async (req, reply) => {
  const job = getJob(req.params.id);
  if (!job) {
    reply.code(404);
    return { error: "no such job" };
  }
  return serializeJob(job);
});

app.get("/jobs", async () => {
  return listJobs().map(serializeJob);
});

export function startServer(port: number) {
  // Pick up any jobs left "queued" from a previous run (e.g. the orchestrator restarted
  // mid-pipeline) — everything past "queued" is left as-is rather than silently resumed,
  // since resuming mid-pipeline needs care about what a worker already wrote to scratch/.
  kickQueue();
  return app.listen({ port, host: "127.0.0.1" });
}
