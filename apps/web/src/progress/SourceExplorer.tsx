import { useEffect, useState } from "react";
import { useSearchParams, NavLink } from "react-router-dom";
import { api } from "../api";
import { Badge, Button, ErrorBox, useLoad } from "../ui";
import { RunEvidence } from "../RunEvidence";
import { PDFViewer } from "../editors";
import { sourceLink } from "./ProgressPanel";
import type { Source, SourceView, ScopePage, FilePage } from "./types";

export function SourceExplorer({ projectId }: { projectId: string }) {
  const [params] = useSearchParams();
  const encoded = params.get("source");
  const [scope, setScope] = useState("");
  const [scopeCursor, setScopeCursor] = useState("");
  const [scopeKind, setScopeKind] = useState("project");
  const [scopeOwner, setScopeOwner] = useState("");
  const [fileCursor, setFileCursor] = useState("");
  const scopes = useLoad<ScopePage>(`/projects/${projectId}/progress/scopes?cursor=${encodeURIComponent(scopeCursor)}&kind=${encodeURIComponent(scopeKind)}&object_id=${encodeURIComponent(scopeOwner)}`, { items: [], next_cursor: null });
  const files = useLoad<FilePage | null>(scope ? `/projects/${projectId}/progress/sources?scope_id=${encodeURIComponent(scope)}&cursor=${encodeURIComponent(fileCursor)}` : null, null);
  const [source, setSource] = useState<Source | null>(null);
  const [view, setView] = useState<SourceView | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const timer = setInterval(() => { void scopes.reload(); if (scope) void files.reload(); }, 2000);
    return () => clearInterval(timer);
  }, [scope, scopes.reload, files.reload]);
  useEffect(() => { setScope(""); setScopeCursor(""); setFileCursor(""); }, [projectId, scopeKind, scopeOwner]);
  const changeScopePage = (cursor: string) => {
    setScope("");
    setFileCursor("");
    setScopeCursor(cursor);
  };
  useEffect(() => {
    let live = true; setView(null); setSource(null); setError("");
    if (!encoded) return;
    try {
      const ref = JSON.parse(encoded) as Source;
      if (ref.project_id !== projectId) throw new Error("Source belongs to another project");
      setSource(ref);
      let pending = false;
      const load = async () => {
        if (pending) return; pending = true;
        try { const value = await api<SourceView>(`/projects/${projectId}/progress/source`, "POST", ref); if (live) { setView(value); setError(""); } }
        catch (e) { if (live) setError((e as Error).message); }
        finally { pending = false; }
      };
      void load();
      const timer = setInterval(() => { void load(); }, 2000);
      return () => { live = false; clearInterval(timer); };
    } catch { setError("Invalid source reference"); }
    return () => { live = false; };
  }, [encoded, projectId]);
  return <section className="surface source-explorer" aria-label="Scoped source explorer"><h2>Source explorer</h2>
    <p className="muted">Branch files and proposed run files have separate identities. Source inspection preserves editor drafts below.</p>
    <ErrorBox error={error || scopes.error || files.error} />
    <div className="inline-actions"><select aria-label="Scope kind" value={scopeKind} onChange={(e) => setScopeKind(e.target.value)}>
      {['project','branch_workspace','run_output','run_workspace','remote_workspace'].map((kind) => <option key={kind} value={kind}>{kind}</option>)}</select>
      <input aria-label="Scope owner ID" value={scopeOwner} maxLength={64} placeholder="Optional branch/run ID" onChange={(e) => setScopeOwner(e.target.value)} />
      <select aria-label="Source scope" value={scope} onChange={(e) => { setScope(e.target.value); setFileCursor(""); }}>
      <option value="">Choose scope</option>{scopes.data.items.map((item) => <option key={item.id} value={item.id}>{item.kind} · {item.object_id.slice(0,8)} · {item.coverage} · {item.total} files</option>)}</select>
      <Button disabled={!scopes.data.next_cursor} onClick={() => changeScopePage(scopes.data.next_cursor!)}>Next scopes</Button>
      {scopeCursor && <Button onClick={() => changeScopePage("")}>First scopes</Button>}
      <Button onClick={() => { void scopes.reload(); void files.reload(); }}>Recheck inventory</Button></div>
    {files.data && <><p>Inventory: {files.data.coverage.coverage} · scan {files.data.coverage.scan_generation} · observed {files.data.coverage.observed_at || "not yet"}. {files.data.coverage.error}</p>
      <ul>{files.data.items.map((file) => <li key={file.id}><NavLink to={sourceLink(file.source)}>{file.path}</NavLink> · {file.state} · generation {file.generation} · {file.parse_state} · {file.attribution}</li>)}</ul>
      <Button disabled={!files.data.next_cursor} onClick={() => setFileCursor(files.data!.next_cursor!)}>Next files</Button>
      {fileCursor && <Button onClick={() => setFileCursor("")}>First files</Button>}</>}
    {source && <div className="source-view"><h3>{source.kind} · {source.path || source.object_id}</h3>
      {!view && !error && <p>Resolving exact source…</p>}
      {view && <><Badge status={view.availability} /><p>{view.note}</p><small>{view.parse_state} · {view.observed_at}</small>
        <div className="source-segments">{(view.segments || []).map((segment) => <NavLink key={segment.id} to={sourceLink({ ...source, segment_id: segment.id })}>{segment.name} · lines {segment.start_line}–{segment.end_line}</NavLink>)}</div>
        {view.content !== null && <pre tabIndex={0}>{view.content}</pre>}
        {Object.keys(view.metadata || {}).length > 0 && <pre tabIndex={0} aria-label="Observed artifact metadata">{JSON.stringify(view.metadata, null, 2)}</pre>}
        {view.artifact_url && view.project_path && /\.(png|jpe?g|gif|webp)$/i.test(view.project_path) && <img alt="Observed source preview" style={{maxWidth:"100%"}} src={view.artifact_url} />}
        {view.artifact_url && view.project_path && /\.pdf$/i.test(view.project_path) && <>
          {typeof view.metadata?.pages === "number" && <div className="source-segments">{Array.from({length: Math.min(500, view.metadata.pages)}, (_, i) => <NavLink key={i} to={sourceLink({...source, segment_id: `pdf_page:${i + 1}`})}>Page {i + 1}</NavLink>)}</div>}
          <PDFViewer url={view.artifact_url} initialPage={source.segment_id?.startsWith('pdf_page:') ? Number(source.segment_id.split(':')[1]) : 1} />
        </>}
        {view.artifact_url && <a href={view.artifact_url} target="_blank" rel="noreferrer">Open observed artifact</a>}
        {view.project_path && <p>Project-relative path: <code>{view.project_path}</code></p>}
        {source.kind === "run" && <RunEvidence runId={source.object_id} />}</>}
    </div>}
  </section>;
}
