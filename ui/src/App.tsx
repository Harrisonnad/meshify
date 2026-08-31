import { useEffect, useRef, useState } from "react";
import "./App.css";
import { createJob, getHealth, getJob, listJobs, STAGE_LABELS, type HealthResponse, type Job } from "./api";

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
  const stages: Job["status"][] = ["queued", "generating_image", "meshing", "cleaning", "done"];
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

function JobResult({ job }: { job: Job }) {
  if (job.status === "failed") {
    return <p className="error-text">Failed: {job.error}</p>;
  }
  if (job.status !== "done") return null;

  const meshgenMeta = job.recipe.meshgen as { tris?: number; watertight?: boolean } | undefined;
  const blenderMeta = job.recipe.blender as { tris_before?: number; tris_after?: number } | undefined;

  return (
    <div className="job-result">
      {job.urls.glb && (
        <model-viewer
          src={job.urls.glb}
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
        <dd>{blenderMeta?.tris_after?.toLocaleString() ?? "?"} tris</dd>
      </dl>
      <div className="downloads">
        {(["glb", "fbx", "stl", "image"] as const).map(
          (key) =>
            job.urls[key] && (
              <a key={key} href={job.urls[key]} target="_blank" rel="noreferrer">
                {key.toUpperCase()}
              </a>
            )
        )}
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
  const [job, setJob] = useState<Job | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
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
      const created = await createJob(prompt.trim(), { target_tris: targetTris });
      setJob(created);
      refreshLibrary();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
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
              <button type="submit" disabled={submitting || !prompt.trim()}>
                {submitting ? "Submitting…" : "Generate"}
              </button>
            </div>
            {submitError && <p className="error-text">{submitError}</p>}
          </form>

          {job && (
            <section className="job-panel">
              <StageProgress job={job} />
              <JobResult job={job} />
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
