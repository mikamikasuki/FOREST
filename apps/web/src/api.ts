export type Json = Record<string, any>;
export type Project = {
  id: string;
  name: string;
  description: string;
  goal: string;
  current_direction: string;
  revision: number;
  archived: boolean;
  mode: string;
  budget: Json;
  config: Json;
  updated_at: string;
};
export type ResearchNode = {
  id: string;
  project_id: string;
  branch_id: string;
  type: string;
  title: string;
  instructions: string;
  revision: number;
  config: Json;
  position: { x: number; y: number };
  execution_status: string;
  research_status: string;
  deliverable_status: string;
  archived: boolean;
  inputs: any[];
  outputs: any[];
  comments: any[];
  context_overrides: Json;
};
export type Graph = {
  project_id: string;
  revision: number;
  nodes: ResearchNode[];
  edges: { id: string; source: string; target: string; relation: string }[];
  branches: Json[];
};
export type Run = {
  id: string;
  node_id: string;
  project_id: string;
  branch_id: string;
  kind: string;
  status: string;
  config: Json;
  node_revision: number;
  created_at: string;
  started_at: string;
  finished_at: string;
  exit_code: number;
  pid: number;
  error: string;
  metrics: Json;
  output_path: string;
};
export type RecordItem = {
  id: string;
  project_id: string;
  title: string;
  revision: number;
  status: string;
  data: Json;
  created_at: string;
  updated_at: string;
};
export class ApiError extends Error {
  code: string;
  status: number;
  suggestion: string;
  constructor(status: number, detail: any) {
    super(
      typeof detail === "string"
        ? detail
        : detail?.message || JSON.stringify(detail) || `HTTP ${status}`,
    );
    this.status = status;
    this.code = detail?.code || "request_failed";
    this.suggestion = detail?.suggestion || "";
  }
}
export async function api<T = any>(
  path: string,
  method = "GET",
  body?: any,
): Promise<T> {
  const r = await fetch(path.startsWith("/api") ? path : `/api${path}`, {
    method,
    credentials: "same-origin",
    headers:
      body instanceof FormData ? {} : { "Content-Type": "application/json" },
    body:
      body === undefined
        ? undefined
        : body instanceof FormData
          ? body
          : JSON.stringify(body),
  });
  if (!r.ok) {
    if (r.status === 401 && typeof window !== "undefined")
      window.dispatchEvent(new Event("forest-auth-required"));
    const error = await r.json().catch(() => ({ detail: r.statusText }));
    throw new ApiError(r.status, error.detail ?? error);
  }
  if (r.status === 204) return undefined as T;
  return r.json();
}
export const uid = () => crypto.randomUUID();
export async function download(path: string, body?: any, name?: string) {
  const r = await fetch(`/api${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({ detail: r.statusText }));
    throw new ApiError(r.status, e.detail);
  }
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download =
    name ||
    r.headers.get("content-disposition")?.match(/filename="?([^";]+)/)?.[1] ||
    "forest-export.zip";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function formatDate(value?: string) {
  return value
    ? new Date(value).toLocaleString("en", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";
}
export function parseJson(value: string) {
  try {
    return JSON.parse(value);
  } catch {
    throw new Error("Invalid JSON");
  }
}
export function hasCycle(graph: Graph, source: string, target: string) {
  const stack = [target],
    visited = new Set<string>();
  while (stack.length) {
    const id = stack.pop()!;
    if (id === source) return true;
    if (visited.has(id)) continue;
    visited.add(id);
    graph.edges
      .filter(
        (e) =>
          e.source === id && ["depends_on", "consumes"].includes(e.relation),
      )
      .forEach((e) => stack.push(e.target));
  }
  return false;
}
