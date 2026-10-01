import { describe, expect, it } from "vitest";
import { executionLinks, hasCycle } from "./api";
import type { Graph, ResearchNode } from "./api";

const node = (id: string, fields: Partial<ResearchNode> = {}): ResearchNode => ({
  id, project_id: "p", branch_id: "b", type: "experiment", title: id,
  instructions: "", revision: 1, config: {}, position: { x: 0, y: 0 },
  execution_status: "not_started", research_status: "proposed", deliverable_status: "draft",
  archived: false, inputs: [], outputs: [], comments: [], context_overrides: {}, ...fields,
});

const graph = (): Graph => ({ project_id: "p", revision: 1, branches: [], edges: [], nodes: [
  node("source"),
  node("check", { config: { kind: "verification", verification: { producer_node_id: "source" } } }),
  node("analysis", { config: { required_verification: ["check"] }, inputs: [
    { node_id: "source", path: "metrics.json", verification_node_id: "check" },
  ] }),
] });

describe("editable evidence verification routes", () => {
  it("shows all source and verification bindings without duplicate arrows", () => {
    const route = graph();
    const links = executionLinks(route);
    expect(links.map(({ source, target }) => `${source}:${target}`).sort()).toEqual([
      "check:analysis", "source:analysis", "source:check",
    ]);
    route.edges = [{ id: "visible", source: "source", target: "check", relation: "depends_on" }];
    expect(executionLinks(route)).toHaveLength(3);
  });
  it("checks cycles through verification and source-file bindings", () => {
    expect(hasCycle(graph(), "analysis", "source")).toBe(true);
    expect(hasCycle(graph(), "check", "source")).toBe(true);
    expect(hasCycle(graph(), "source", "analysis")).toBe(false);
  });
});
