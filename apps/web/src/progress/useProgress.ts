import { useEffect, useState } from "react";
import { api } from "../api";
import { subscribeProject } from "./events";
import type { Snapshot } from "./types";

export function useProgress(projectId: string) {
  const [state, setState] = useState<{ project: string; snapshot?: Snapshot; error: string }>({ project: projectId, error: "" });
  useEffect(() => {
    let live = true, running = false, pending = false;
    const load = async () => {
      if (running) { pending = true; return; }
      running = true;
      try {
        const snapshot = await api<Snapshot>(`/projects/${projectId}/progress`);
        if (live && snapshot.project_id === projectId) setState((previous) => {
          if (previous.project === projectId && previous.snapshot?.epoch === snapshot.epoch && previous.snapshot.generation > snapshot.generation) return previous;
          return { project: projectId, snapshot, error: "" };
        });
      } catch (error) {
        if (live) setState((old) => ({ ...(old.project === projectId ? old : {}), project: projectId, error: (error as Error).message }));
      } finally {
        running = false;
        if (pending && live) { pending = false; void load(); }
      }
    };
    // Subscribe before loading; onopen resnapshots, closing the startup gap.
    const unsubscribe = subscribeProject(projectId, () => void load());
    void load();
    const timer = setInterval(() => void load(), 2000);
    return () => { live = false; unsubscribe(); clearInterval(timer); };
  }, [projectId]);
  return state.project === projectId ? state : { project: projectId, error: "" };
}
