import { useEffect, useState } from "react";
import { Download, GitBranch, RefreshCw } from "lucide-react";
import { api, formatDate } from "./api";
import type { Json } from "./api";
import { Badge, Button } from "./ui";
import "./run-evidence.css";

type EvidenceFile = { path: string; bytes: number };
type EvidenceRun = {
  id: string; missing?: boolean; node_id?: string; node_title?: string;
  status?: string; node_revision?: number; current_node_revision?: number;
  current?: boolean; dependencies?: string[]; resource?: Json; config?: Json;
  files?: EvidenceFile[]; source_freshness?: Json[];
};
type Lineage = { run_id: string; project_id: string; runs: EvidenceRun[] };
type SessionState = { run_id: string; status: string; session: Json | null };
function bytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export function RunEvidence({ runId }: { runId: string }) {
  const [open, setOpen] = useState(false);
  const [lineage, setLineage] = useState<Lineage | null>(null);
  const [session, setSession] = useState<SessionState | null>(null);
  const [selected, setSelected] = useState(runId);
  const [error, setError] = useState("");
  const [sessionError, setSessionError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [filter, setFilter] = useState("");
  const [visibleFiles, setVisibleFiles] = useState(15);
  useEffect(() => {
    setSelected(runId); setLineage(null); setSession(null); setFilter(""); setVisibleFiles(15);
  }, [runId]);
  useEffect(() => {
    if (!open) return;
    let active = true, busy = false, live = true;
    const load = async () => {
      if (busy) return;
      busy = true;
      const result = await Promise.allSettled([
        api<Lineage>(`/runs/${runId}/lineage`),
        api<SessionState>(`/runs/${selected}/session?summary=true`),
      ]);
      if (active) {
        if (result[0].status === "fulfilled") {
          setLineage(result[0].value); setError("");
          live = result[0].value.runs.some((run) => ["queued", "running", "waiting", "pausing"].includes(run.status || ""));
        } else setError(String(result[0].reason?.message || result[0].reason));
        if (result[1].status === "fulfilled") { setSession(result[1].value); setSessionError(""); }
        else { setSession(null); setSessionError(String(result[1].reason?.message || result[1].reason)); }
      }
      busy = false;
    };
    void load();
    const timer = setInterval(() => { if (live) void load(); }, 10000);
    return () => { active = false; clearInterval(timer); };
  }, [open, runId, selected, refresh]);
  const run = lineage?.runs.find((item) => item.id === selected);
  const attempts: Json[] = [...(run?.resource?.attempts || [])];
  const currentAttempt = run?.config?.execution_attempt;
  if (currentAttempt?.id && !attempts.some((attempt) => attempt.id === currentAttempt.id)) attempts.push(currentAttempt);
  const files = (run?.files || []).filter((file) => file.path.toLowerCase().includes(filter.toLowerCase()));
  const saved = session?.session;
  const steps = saved?.transcript_count ?? saved?.steps ?? saved?.transcript?.length;
  const select = (id: string) => { setSelected(id); setSession(null); setVisibleFiles(15); setFilter(""); };
  return (
    <details className="run-evidence" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary><GitBranch size={14} /> Evidence and recovery</summary>
      {open && <div className="run-evidence-content">
        <div className="run-evidence-toolbar">
          <span>{lineage ? `${lineage.runs.length} linked run${lineage.runs.length === 1 ? "" : "s"}` : "Loading evidence…"}</span>
          <Button onClick={() => setRefresh((value) => value + 1)} aria-label="Refresh run evidence"><RefreshCw size={12} /> Refresh</Button>
        </div>
        {error && <p role="alert" className="run-evidence-error">{error}</p>}
        {lineage && <>
          <label className="run-evidence-select">Inspect run
            <select value={selected} onChange={(event) => select(event.target.value)}>
              {lineage.runs.map((item) => <option key={item.id} value={item.id}>
                {item.node_title || item.id.slice(0, 8)}{item.missing ? " · missing" : ` · ${item.status}`}
              </option>)}
            </select>
          </label>
          {run?.missing ? <p role="status">The referenced run is no longer available.</p> : run && <>
            <div className="run-evidence-status">
              <Badge status={run.status || "unknown"} />
              <span>{!run.node_id ? "Run record" : run.current ? "Matches current node" : "Node has changed"}</span>
              {run.node_id && <span>Used revision {run.node_revision}; current {run.current_node_revision ?? "unavailable"}</span>}
            </div>
            {!!run.dependencies?.length && <div className="run-evidence-dependencies"><span>Depends on</span>
              {run.dependencies.map((id) => <button type="button" key={id} onClick={() => select(id)}>
                {lineage.runs.find((item) => item.id === id)?.node_title || id.slice(0, 8)}
              </button>)}
            </div>}
            {!!run.source_freshness?.length && <ul className="run-evidence-freshness">
              {run.source_freshness.map((source, index) => <li key={source.path || index}>
                <code>{source.path}</code><span>{source.status || (source.current ? "Current" : "Changed")}</span>
              </li>)}
            </ul>}
            {sessionError && <p role="alert" className="run-evidence-error">Session: {sessionError}</p>}
            {saved && <div className="run-evidence-session"><strong>Research session</strong><span>{saved.status}</span>
              {steps !== undefined && <span>{steps} model turns</span>}
              {saved.active_seconds !== undefined && <span>{Math.round(saved.active_seconds)} seconds of active model work</span>}
              {saved.wait_for?.process_id && <span>Waiting for process {saved.wait_for.process_id.slice(0, 8)}</span>}
              {saved.budget_reason && <p>{saved.budget_reason}</p>}
            </div>}
            <details className="run-evidence-attempts"><summary>Execution attempts ({attempts.length})</summary>
              {attempts.length ? <ol>{attempts.map((attempt, index) => <li key={attempt.id || index}>
                <strong>Attempt {attempt.number ?? index + 1}</strong>
                <span>{attempt.status || (attempt.id === currentAttempt?.id ? run.status : "Recorded")}</span>
                <span>{formatDate(attempt.started_at || attempt.finished_at)}</span>
                {attempt.exit_code !== undefined && <span>Exit {attempt.exit_code}</span>}
                {attempt.resume_mode && <span>{attempt.resume_mode}</span>}
                {attempt.error && <p>{attempt.error}</p>}
              </li>)}</ol> : <p>No attempt details were recorded for this run.</p>}
            </details>
            <details className="run-evidence-files"><summary>Output files ({run.files?.length || 0})</summary>
              <input aria-label="Filter evidence files" placeholder="Filter files" value={filter} onChange={(event) => { setFilter(event.target.value); setVisibleFiles(15); }} />
              <ul>{files.slice(0, visibleFiles).map((file) => <li key={file.path}>
                <a href={`/api/projects/${lineage.project_id}/download?path=${encodeURIComponent(file.path)}`} target="_blank" rel="noreferrer"><Download size={12} /><span>{file.path}</span></a>
                <small>{bytes(file.bytes)}</small>
              </li>)}</ul>
              {!files.length && <p>No matching output files.</p>}
              {files.length > visibleFiles && <Button onClick={() => setVisibleFiles((count) => count + 30)}>Show more files</Button>}
            </details>
          </>}
        </>}
      </div>}
    </details>
  );
}
