import Fastify from "fastify";
import { ulid } from "ulid";
import { getJob, insertJob, listJobs, type JobParams, type JobRow } from "./db.js";
import { kickQueue } from "./pipeline.js";
import { checkHealth } from "./workers.js";

const app = Fastify({ logger: true });

function serializeJob(row: JobRow) {
  return {
    id: row.id,
    status: row.status,
    prompt: row.prompt,
    params: JSON.parse(row.params),
    recipe: JSON.parse(row.recipe),
    outputs: JSON.parse(row.outputs),
    error: row.error,
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

app.get("/health", async () => {
  const [imggen, meshgen, blender] = await Promise.all([
    checkHealth("imggen"),
    checkHealth("meshgen"),
    checkHealth("blender"),
  ]);
  return { status: "ok", workers: { imggen, meshgen, blender } };
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
