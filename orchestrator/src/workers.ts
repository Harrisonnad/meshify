// Thin HTTP clients for the three workers, speaking the contract in docs/WORKER_CONTRACT.md.
// All three are reachable at 127.0.0.1 regardless of whether they run in WSL2 (imggen,
// meshgen) or native Windows (blender) — Windows 11 auto-forwards WSL2 localhost ports.

const WORKER_URLS = {
  imggen: process.env.IMGGEN_URL ?? "http://127.0.0.1:8101",
  meshgen: process.env.MESHGEN_URL ?? "http://127.0.0.1:8102",
  blender: process.env.BLENDER_URL ?? "http://127.0.0.1:8104",
  rig: process.env.RIG_URL ?? "http://127.0.0.1:8103",
} as const;

export type WorkerName = keyof typeof WORKER_URLS;

export interface WorkerResponse {
  job_id: string;
  outputs: Record<string, string>;
  meta: Record<string, unknown>;
}

export interface WorkerHealth {
  status: string;
  worker: string;
  model: string;
  commit: string;
  device: string;
}

async function callWorker(
  worker: WorkerName,
  jobId: string,
  inputs: Record<string, unknown>,
  params: Record<string, unknown>
): Promise<WorkerResponse> {
  const res = await fetch(`${WORKER_URLS[worker]}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_id: jobId, inputs, params }),
  });
  const body = await res.json();
  if (!res.ok) {
    const message = typeof body === "object" && body && "error" in body ? (body as any).error : JSON.stringify(body);
    throw new Error(`${worker} worker failed: ${JSON.stringify(message)}`);
  }
  return body as WorkerResponse;
}

export async function checkHealth(worker: WorkerName): Promise<WorkerHealth | { status: "unreachable" }> {
  try {
    const res = await fetch(`${WORKER_URLS[worker]}/health`, { signal: AbortSignal.timeout(3000) });
    return (await res.json()) as WorkerHealth;
  } catch {
    return { status: "unreachable" };
  }
}

export function runImggen(jobId: string, prompt: string, params: Record<string, unknown>) {
  return callWorker("imggen", jobId, { prompt }, { prompt, ...params });
}

export function runMeshgen(jobId: string, imagePath: string, params: Record<string, unknown>) {
  return callWorker("meshgen", jobId, { image: imagePath }, params);
}

// meshgen and imggen run inside WSL2 and hand back paths in WSL's view of the filesystem
// (/mnt/c/...); blender runs on native Windows and needs the Windows-drive form. This is the
// one place a path crosses that boundary in the pipeline (imggen -> meshgen both stay on the
// WSL side, so no translation needed there).
function wslToWindowsPath(p: string): string {
  const match = p.match(/^\/mnt\/([a-z])\/(.*)$/i);
  if (!match) return p;
  const [, drive, rest] = match;
  return `${drive.toUpperCase()}:/${rest}`;
}

export function runBlender(jobId: string, meshPath: string, params: Record<string, unknown>) {
  return callWorker("blender", jobId, { mesh: wslToWindowsPath(meshPath) }, { name: "asset", ...params });
}

// The mirror image of wslToWindowsPath: rig runs inside WSL2 but its input (the cleaned mesh)
// comes from blender, which hands back a Windows-drive path. Same boundary, opposite direction.
function windowsToWslPath(p: string): string {
  const match = p.match(/^([a-z]):[\\/](.*)$/i);
  if (!match) return p;
  const [, drive, rest] = match;
  return `/mnt/${drive.toLowerCase()}/${rest.replace(/\\/g, "/")}`;
}

export function runRig(jobId: string, meshPath: string, params: Record<string, unknown>) {
  return callWorker("rig", jobId, { mesh: windowsToWslPath(meshPath) }, params);
}
