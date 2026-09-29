import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { hasCycle } from "./api";
import type { Graph } from "./api";
import { ErrorBox, Empty, Button } from "./ui";
const graph = (edges: Graph["edges"]): Graph => ({
  project_id: "p",
  revision: 1,
  nodes: [],
  branches: [],
  edges,
});
describe("research dependency validation", () => {
  it("rejects an indirect cycle through execution dependencies", () => {
    expect(
      hasCycle(
        graph([
          { id: "1", source: "a", target: "b", relation: "depends_on" },
          { id: "2", source: "b", target: "c", relation: "consumes" },
        ]),
        "c",
        "a",
      ),
    ).toBe(true);
  });
  it("ignores reference and history edges when checking execution cycles", () => {
    expect(
      hasCycle(
        graph([
          { id: "1", source: "a", target: "b", relation: "history" },
          { id: "2", source: "b", target: "c", relation: "evidence" },
        ]),
        "c",
        "a",
      ),
    ).toBe(false);
  });
  it("rejects a self-dependency and terminates on an existing cycle", () => {
    expect(hasCycle(graph([]), "a", "a")).toBe(true);
    expect(
      hasCycle(
        graph([
          { id: "1", source: "a", target: "b", relation: "depends_on" },
          { id: "2", source: "b", target: "a", relation: "consumes" },
        ]),
        "z",
        "a",
      ),
    ).toBe(false);
  });
});
describe("empty and failed workspace components", () => {
  it("renders recovery control and actual error text", () => {
    const html = renderToStaticMarkup(
      <ErrorBox error="Worker unavailable" retry={() => {}} />,
    );
    expect(html).toContain("Worker unavailable");
    expect(html).toContain("Retry");
    expect(html).toContain("<button");
  });
  it("renders empty state without numerical evidence", () => {
    const html = renderToStaticMarkup(
      <Empty
        title="No measured results"
        description="Execute an experiment to collect data."
      />,
    );
    expect(html).toContain("No measured results");
    expect(html).toContain("Execute an experiment");
    expect(html).not.toContain("100%");
  });
});

describe("submission controls", () => {
  it("blocks another submission while busy even when the ordinary disabled predicate is false", () => {
    expect(
      renderToStaticMarkup(
        <Button busy disabled={false}>
          Run
        </Button>,
      ),
    ).toContain('disabled=""');
  });
});
