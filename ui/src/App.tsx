import { useEffect, useRef, useState } from "react";
import "./App.css";
import { createJob, getHealth, getJob, listJobs, STAGE_LABELS, type HealthResponse, type Job, type JobParams } from "./api";

const TERMINAL_STATUSES = new Set(["done", "failed"]);

function HealthBadge() {
  const [health, setHealth] = useState<HealthResponse | null>(null);

  useEffect(() => {
    const check = () => getHealth().then(setHealth).catch(() => setHealth(null));
    check();
    const id = setInterval(check, 10_000);
    return () => clearInterval(id);
  }, []);

  if (!health) return <span className="badge badge-error">orchestrator unreachable</span>;

  const workers = Object.entries(health.workers);
  const allOk = workers.every(([, w]) => w.status === "ok");
  return (
    <span className={`badge ${allOk ? "badge-ok" : "badge-warn"}`}>
      {workers.map(([name, w]) => `${name}: ${w.status}`).join(" · ")}
    </span>
  );
}

function StageProgress({ job }: { job: Job }) {
  // "rigging"/"animating" only ever happen when the job actually asked for them — leaving
  // them in the list for jobs that didn't would show a stage that's permanently skipped,
  // which reads as stuck.
  const stages: Job["status"][] = job.params.rig
    ? [
        "queued",
        "generating_image",
        "meshing",
        "cleaning",
        "rigging",
        ...(job.params.animate ? (["animating"] as const) : []),
        "done",
      ]
    : ["queued", "generating_image", "meshing", "cleaning", "done"];
  const currentIndex = stages.indexOf(job.status);

  return (
    <div className="stage-progress">
      {stages.map((stage, i) => (
        <div
          key={stage}
          className={`stage ${job.status === "failed" ? "stage-failed" : i <= currentIndex ? "stage-active" : ""}`}
        >
          {STAGE_LABELS[stage]}
        </div>
      ))}
    </div>
  );
}

function JobResult({ job, onRerun, rerunning }: { job: Job; onRerun: (job: Job) => void; rerunning: boolean }) {
  if (job.status === "failed") {
    return (
      <div>
        <p className="error-text">
          Failed{job.retries > 0 ? ` (auto-retried ${job.retries}x)` : ""}: {job.error}
        </p>
        <button type="button" onClick={() => onRerun(job)} disabled={rerunning}>
          {rerunning ? "Re-running…" : "Retry"}
        </button>
      </div>
    );
  }
  if (job.status !== "done") return null;

  const meshgenMeta = job.recipe.meshgen as { tris?: number; watertight?: boolean } | undefined;
  const blenderMeta = job.recipe.blender as
    | {
        tris_before?: number;
        tris_after?: number;
        smoothed?: boolean;
        retopologized?: boolean;
        baked_texture?: boolean;
        baked_normal?: boolean;
        roughness_factor?: number;
        metallic_factor?: number;
      }
    | undefined;
  const rigMeta = job.recipe.rig as { tris?: number; backend?: string } | undefined;
  const animateMeta = job.recipe.animate as { animated?: boolean; clips?: string[] } | undefined;

  // Prefer the most complete asset available in the viewer.
  const viewerSrc = job.urls.animated ?? job.urls.rigged ?? job.urls.glb;

  return (
    <div className="job-result">
      {viewerSrc && (
        <model-viewer
          src={viewerSrc}
          alt={job.prompt}
          camera-controls
          auto-rotate
          shadow-intensity="1"
          style={{ width: "100%", height: "400px", background: "#f3f3f3", borderRadius: "8px" }}
        />
      )}
      <dl className="stats">
        <dt>Raw mesh</dt>
        <dd>
          {meshgenMeta?.tris?.toLocaleString() ?? "?"} tris, {meshgenMeta?.watertight ? "watertight" : "not watertight"}
        </dd>
        <dt>Cleaned mesh</dt>
        <dd>
          {blenderMeta?.tris_after?.toLocaleString() ?? "?"} tris
          {blenderMeta?.smoothed ? ", smoothed" : ""}
          {blenderMeta?.retopologized ? ", retopologized" : ""}
          {blenderMeta?.baked_texture ? ", baked texture" : ""}
          {blenderMeta?.baked_normal ? ", baked normal map" : ""}
          {blenderMeta?.roughness_factor !== undefined
            ? `, roughness ${blenderMeta.roughness_factor}/metallic ${blenderMeta.metallic_factor ?? 0}`
            : ""}
        </dd>
        {rigMeta && (
          <>
            <dt>Rigged</dt>
            <dd>via {rigMeta.backend ?? "skintokens"}</dd>
          </>
        )}
        {animateMeta && (
          <>
            <dt>Animations</dt>
            <dd>
              {animateMeta.animated
                ? `baked: ${animateMeta.clips?.join(", ") ?? "?"}`
                : "skipped — skeleton didn't map cleanly onto the animation template (see docs/ANIMATION.md)"}
            </dd>
          </>
        )}
      </dl>
      <div className="downloads">
        {(["glb", "fbx", "stl", "rigged", "animated", "texture", "ao", "normal", "image"] as const).map(
          (key) =>
            job.urls[key] && (
              <a key={key} href={job.urls[key]} target="_blank" rel="noreferrer">
                {key === "rigged"
                  ? "RIGGED GLB"
                  : key === "animated"
                    ? "ANIMATED GLB"
                    : key === "texture"
                      ? "TEXTURE"
                      : key === "ao"
                        ? "AO MAP"
                        : key === "normal"
                          ? "NORMAL MAP"
                          : key.toUpperCase()}
              </a>
            )
        )}
        <button
          type="button"
          className="rerun-button"
          onClick={() => onRerun(job)}
          disabled={rerunning}
          title="Re-run with the same prompt and settings, including the exact seed that produced this result"
        >
          {rerunning ? "Re-running…" : "Re-run (same seed)"}
        </button>
      </div>
    </div>
  );
}

function JobLibrary({ jobs, onSelect }: { jobs: Job[]; onSelect: (job: Job) => void }) {
  if (jobs.length === 0) return <p className="muted">No jobs yet.</p>;
  return (
    <ul className="library">
      {jobs.map((job) => (
        <li key={job.id} onClick={() => onSelect(job)}>
          <span className={`status-dot status-${job.status}`} />
          <span className="library-prompt">{job.prompt}</span>
          <span className="muted">{STAGE_LABELS[job.status]}</span>
        </li>
      ))}
    </ul>
  );
}

export default function App() {
  const [prompt, setPrompt] = useState("");
  const [targetTris, setTargetTris] = useState(8000);
  const [rig, setRig] = useState(false);
  const [animate, setAnimate] = useState(false);
  const [retopology, setRetopology] = useState(false);
  const [bakeTexture, setBakeTexture] = useState(false);
  const [bakeNormal, setBakeNormal] = useState(false);
  const [roughnessFactor, setRoughnessFactor] = useState(0.6);
  const [metallicFactor, setMetallicFactor] = useState(0.0);
  const [seed, setSeed] = useState("");
  const [resolution, setResolution] = useState(256);
  const [threshold, setThreshold] = useState(25.0);
  const [smoothIterations, setSmoothIterations] = useState(1);
  const [smoothFactor, setSmoothFactor] = useState(2);
  const [job, setJob] = useState<Job | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [rerunning, setRerunning] = useState(false);
  const pollRef = useRef<number | null>(null);

  const refreshLibrary = () => listJobs().then(setJobs).catch(() => {});

  useEffect(() => {
    refreshLibrary();
  }, []);

  useEffect(() => {
    if (!job || TERMINAL_STATUSES.has(job.status)) {
      if (pollRef.current) window.clearInterval(pollRef.current);
      return;
    }
    pollRef.current = window.setInterval(async () => {
      const updated = await getJob(job.id).catch(() => null);
      if (updated) {
        setJob(updated);
        if (TERMINAL_STATUSES.has(updated.status)) refreshLibrary();
      }
    }, 2000);
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.id, job?.status]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!prompt.trim()) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const params: JobParams = {
        target_tris: targetTris,
        rig,
        resolution,
        threshold,
        smooth_iterations: smoothIterations,
        smooth_factor: smoothFactor,
        retopology,
        bake_texture: bakeTexture,
        bake_normal: bakeNormal,
        roughness_factor: roughnessFactor,
        metallic_factor: metallicFactor,
        animate,
      };
      if (seed.trim()) params.seed = Number(seed.trim());
      const created = await createJob(prompt.trim(), params);
      setJob(created);
      refreshLibrary();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  // Re-run uses the exact seed recorded in the source job's recipe (not just its requested
  // params, which may have been left blank for a random roll) — that's what makes this a
  // true reproduction rather than just "try the same prompt again."
  async function handleRerun(source: Job) {
    setRerunning(true);
    setSubmitError(null);
    try {
      const imggenMeta = source.recipe.imggen as { seed?: number } | undefined;
      const params: JobParams = { ...source.params };
      if (imggenMeta?.seed !== undefined) params.seed = imggenMeta.seed;
      const created = await createJob(source.prompt, params);
      setJob(created);
      refreshLibrary();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    } finally {
      setRerunning(false);
    }
  }

  return (
    <div className="app">
      <header>
        <h1>Local 3D Asset Forge</h1>
        <HealthBadge />
      </header>

      <div className="layout">
        <main>
          <form onSubmit={handleSubmit} className="prompt-form">
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="a small red toy tractor on a wooden workbench, plain grey background, studio lighting"
              rows={3}
            />
            <div className="form-row">
              <label>
                Target tris
                <input
                  type="number"
                  min={500}
                  max={50000}
                  step={500}
                  value={targetTris}
                  onChange={(e) => setTargetTris(Number(e.target.value))}
                />
              </label>
              <label className="checkbox-label" title="Predicts a skeleton and skin weights. Only makes sense for character-shaped subjects — props, walls, and terrain don't need one.">
                <input
                  type="checkbox"
                  checked={rig}
                  onChange={(e) => {
                    setRig(e.target.checked);
                    if (!e.target.checked) setAnimate(false); // animate needs a skeleton to animate
                  }}
                />
                Auto-rig (characters only)
              </label>
              <label
                className="checkbox-label"
                title="Bakes a preset Idle + Walk animation library onto the rig. Only works when the predicted skeleton maps cleanly onto a standard bone-naming template — often skipped for now (see docs/ANIMATION.md); the job still succeeds either way."
              >
                <input
                  type="checkbox"
                  checked={animate}
                  onChange={(e) => {
                    setAnimate(e.target.checked);
                    if (e.target.checked) setRig(true); // animate implies rig
                  }}
                />
                Add animations (Idle, Walk)
              </label>
              <label className="checkbox-label" title="Bakes the mesh's per-vertex colors into a UV-mapped image texture instead of leaving them as vertex colors. Adds a Cycles render pass, so it takes longer.">
                <input type="checkbox" checked={bakeTexture} onChange={(e) => setBakeTexture(e.target.checked)} />
                Bake texture
              </label>
              <label className="checkbox-label" title="Bakes a real normal map from the original full-resolution mesh onto the final decimated one, recovering surface detail lost to decimation. Adds a slow selected-to-active Cycles bake.">
                <input type="checkbox" checked={bakeNormal} onChange={(e) => setBakeNormal(e.target.checked)} />
                Bake normal map
              </label>
              <label className="checkbox-label" title="Runs a QuadriFlow remesh for game-artist-style quad edge flow instead of raw triangle decimation. Adds noticeable time (~5-10s) for a real topology-quality improvement.">
                <input type="checkbox" checked={retopology} onChange={(e) => setRetopology(e.target.checked)} />
                Retopology (quads)
              </label>
              <button type="submit" disabled={submitting || !prompt.trim()}>
                {submitting ? "Submitting…" : "Generate"}
              </button>
            </div>

            <details className="advanced">
              <summary>Advanced</summary>
              <div className="advanced-grid">
                <label>
                  Seed
                  <input
                    type="text"
                    inputMode="numeric"
                    placeholder="random"
                    value={seed}
                    onChange={(e) => setSeed(e.target.value.replace(/[^0-9]/g, ""))}
                  />
                </label>
                <label>
                  Mesh resolution
                  <input
                    type="number"
                    min={64}
                    max={512}
                    step={32}
                    value={resolution}
                    onChange={(e) => setResolution(Number(e.target.value))}
                  />
                </label>
                <label>
                  Mesh threshold
                  <input
                    type="number"
                    min={1}
                    max={100}
                    step={1}
                    value={threshold}
                    onChange={(e) => setThreshold(Number(e.target.value))}
                  />
                </label>
                <label title="Laplacian smoothing passes after decimation, to soften marching-cubes noise. 0 disables it.">
                  Smoothing iterations
                  <input
                    type="number"
                    min={0}
                    max={10}
                    step={1}
                    value={smoothIterations}
                    onChange={(e) => setSmoothIterations(Number(e.target.value))}
                  />
                </label>
                <label title="Laplacian smoothing strength per iteration.">
                  Smoothing factor
                  <input
                    type="number"
                    min={0}
                    max={50}
                    step={1}
                    value={smoothFactor}
                    onChange={(e) => setSmoothFactor(Number(e.target.value))}
                  />
                </label>
                <label title="Flat PBR roughness factor (0 = mirror-smooth, 1 = fully matte). Not a baked map — see docs/PBR.md for why real per-pixel roughness needs a material-understanding model this project doesn't have.">
                  Roughness
                  <input
                    type="number"
                    min={0}
                    max={1}
                    step={0.05}
                    value={roughnessFactor}
                    onChange={(e) => setRoughnessFactor(Number(e.target.value))}
                  />
                </label>
                <label title="Flat PBR metallic factor (0 = dielectric, 1 = fully metal). Not a baked map — most generated props should stay at 0.">
                  Metallic
                  <input
                    type="number"
                    min={0}
                    max={1}
                    step={0.05}
                    value={metallicFactor}
                    onChange={(e) => setMetallicFactor(Number(e.target.value))}
                  />
                </label>
              </div>
            </details>

            {submitError && <p className="error-text">{submitError}</p>}
          </form>

          {job && (
            <section className="job-panel">
              <StageProgress job={job} />
              <JobResult job={job} onRerun={handleRerun} rerunning={rerunning} />
            </section>
          )}
        </main>

        <aside>
          <h2>Library</h2>
          <JobLibrary jobs={jobs} onSelect={setJob} />
        </aside>
      </div>
    </div>
  );
}
