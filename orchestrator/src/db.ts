// Job store: a flat JSON file, not SQLite. Master plan §5 calls for "SQLite for jobs +
// recipes... no Postgres, no Docker Compose sprawl. One file" — better-sqlite3's native
// build failed on this machine's toolchain (a node-gyp/MSBuild mismatch, the Node-side
// equivalent of the CUDA/gcc toolchain fights the Python side hit repeatedly this session).
// For a single-user local tool with job counts in the dozens, a JSON file satisfies the same
// "one file, no server" spirit without a native dependency. Revisit if job volume or
// concurrent readers ever make that not true.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = path.resolve(fileURLToPath(import.meta.url), "../../../");
const DB_PATH = path.join(REPO_ROOT, "orchestrator", "jobs.json");

// Job states: queued -> generating_image -> meshing -> cleaning -> done | failed
// (No concept-picker or rigging states yet — those are Phase 4 UI and v2 scope respectively;
// see docs/DECISIONS.md.)
export type JobStatus =
  | "queued"
  | "generating_image"
  | "meshing"
  | "cleaning"
  | "done"
  | "failed";

export interface JobRow {
  id: string;
  status: JobStatus;
  prompt: string;
  params: string; // JSON, kept as a string to mirror the shape a real SQLite column would have
  recipe: string; // JSON
  outputs: string; // JSON
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface JobParams {
  seed?: number;
  width?: number;
  height?: number;
  steps?: number;
  resolution?: number; // marching cubes resolution
  threshold?: number;
  target_tris?: number;
  scale?: number;
  origin?: "base" | "center";
}

function loadAll(): Map<string, JobRow> {
  if (!fs.existsSync(DB_PATH)) return new Map();
  const rows: JobRow[] = JSON.parse(fs.readFileSync(DB_PATH, "utf-8"));
  return new Map(rows.map((r) => [r.id, r]));
}

function saveAll(jobs: Map<string, JobRow>): void {
  const rows = [...jobs.values()].sort((a, b) => a.created_at.localeCompare(b.created_at));
  fs.writeFileSync(DB_PATH, JSON.stringify(rows, null, 2));
}

export function insertJob(id: string, prompt: string, params: JobParams): JobRow {
  const now = new Date().toISOString();
  const row: JobRow = {
    id,
    status: "queued",
    prompt,
    params: JSON.stringify(params),
    recipe: "{}",
    outputs: "{}",
    error: null,
    created_at: now,
    updated_at: now,
  };
  const jobs = loadAll();
  jobs.set(id, row);
  saveAll(jobs);
  return row;
}

export function getJob(id: string): JobRow | undefined {
  return loadAll().get(id);
}

export function listJobs(limit = 50): JobRow[] {
  return [...loadAll().values()]
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
    .slice(0, limit);
}

export function updateJob(
  id: string,
  patch: Partial<Pick<JobRow, "status" | "error">> & {
    recipePatch?: Record<string, unknown>;
    outputsPatch?: Record<string, unknown>;
  }
): void {
  const jobs = loadAll();
  const current = jobs.get(id);
  if (!current) throw new Error(`no such job: ${id}`);

  const updated: JobRow = {
    ...current,
    status: patch.status ?? current.status,
    error: patch.error !== undefined ? patch.error : current.error,
    recipe: patch.recipePatch
      ? JSON.stringify({ ...JSON.parse(current.recipe), ...patch.recipePatch })
      : current.recipe,
    outputs: patch.outputsPatch
      ? JSON.stringify({ ...JSON.parse(current.outputs), ...patch.outputsPatch })
      : current.outputs,
    updated_at: new Date().toISOString(),
  };
  jobs.set(id, updated);
  saveAll(jobs);
}

export function getNextQueuedJob(): JobRow | undefined {
  return [...loadAll().values()]
    .filter((j) => j.status === "queued")
    .sort((a, b) => a.created_at.localeCompare(b.created_at))[0];
}
