// Thin client for the orchestrator's HTTP API — see docs/ORCHESTRATOR.md.
const ORCHESTRATOR_URL = "http://127.0.0.1:8100";

export type JobStatus =
  | "queued"
  | "generating_image"
  | "meshing"
  | "cleaning"
  | "rigging"
  | "animating"
  | "done"
  | "failed";

export interface JobParams {
  seed?: number;
  width?: number;
  height?: number;
  steps?: number;
  resolution?: number;
  threshold?: number;
  target_tris?: number;
  scale?: number;
  origin?: "base" | "center";
  rig?: boolean;
  smooth_iterations?: number;
  smooth_factor?: number;
  retopology?: boolean;
  bake_texture?: boolean;
  bake_normal?: boolean;
  bake_size?: number;
  roughness_factor?: number;
  metallic_factor?: number;
  animate?: boolean;
  skeleton_template?: string;
}

export interface Job {
  id: string;
  status: JobStatus;
  prompt: string;
  params: JobParams;
  recipe: Record<string, Record<string, unknown>>;
  outputs: Record<string, string>;
  urls: Record<string, string>;
  error: string | null;
  retries: number;
  created_at: string;
  updated_at: string;
}

export interface WorkerHealth {
  status: string;
  worker?: string;
  model?: string;
  device?: string;
}

export interface HealthResponse {
  status: string;
  workers: { imggen: WorkerHealth; meshgen: WorkerHealth; blender: WorkerHealth; rig: WorkerHealth };
}

function assetUrl(path: string): string {
  return `${ORCHESTRATOR_URL}${path}`;
}

export async function createJob(prompt: string, params: JobParams): Promise<Job> {
  const res = await fetch(`${ORCHESTRATOR_URL}/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, params }),
  });
  if (!res.ok) throw new Error((await res.json()).error ?? `HTTP ${res.status}`);
  return withAbsoluteUrls(await res.json());
}

export async function getJob(id: string): Promise<Job> {
  const res = await fetch(`${ORCHESTRATOR_URL}/jobs/${id}`);
  if (!res.ok) throw new Error((await res.json()).error ?? `HTTP ${res.status}`);
  return withAbsoluteUrls(await res.json());
}

export async function listJobs(): Promise<Job[]> {
  const res = await fetch(`${ORCHESTRATOR_URL}/jobs`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const jobs: Job[] = await res.json();
  return jobs.map(withAbsoluteUrls);
}

export async function getHealth(): Promise<HealthResponse> {
  const res = await fetch(`${ORCHESTRATOR_URL}/health`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function withAbsoluteUrls(job: Job): Job {
  return { ...job, urls: Object.fromEntries(Object.entries(job.urls).map(([k, v]) => [k, assetUrl(v)])) };
}

export const STAGE_LABELS: Record<JobStatus, string> = {
  queued: "Queued",
  generating_image: "Generating concept image",
  meshing: "Reconstructing mesh",
  cleaning: "Cleaning up and exporting",
  rigging: "Auto-rigging",
  animating: "Baking animations",
  done: "Done",
  failed: "Failed",
};
