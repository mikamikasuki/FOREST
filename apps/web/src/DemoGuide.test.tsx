import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { DemoRunSummary, demoLink, partitionDemoRuns } from "./DemoGuide";
import { UIContext } from "./ui";
import type { Run } from "./api";

const enteredAt = Date.parse("2026-09-28T06:00:00Z");
const run = (id: string, status: string, finished_at = ""): Run =>
  ({ id, status, finished_at, created_at: "2026-09-28T05:00:00Z" }) as Run;
describe("real demonstration evidence timing", () => {
  it("keeps prior measured completions separate from current tasks and excludes failures from completed results", () => {
    const data = partitionDemoRuns(
      [
        run("previous", "completed", "2026-09-28T05:59:00Z"),
        run("new", "completed", "2026-09-28T06:01:00Z"),
        run("failure", "failed", "2026-09-28T05:59:00Z"),
        run("live", "running"),
        run("waiting", "queued"),
        run("pause", "paused"),
      ],
      enteredAt,
    );
    expect(data.prior.map((r) => r.id)).toEqual(["previous"]);
    expect(data.completedNow.map((r) => r.id)).toEqual(["new"]);
    expect(data.running.map((r) => r.id)).toEqual(["live"]);
    expect(data.queued.map((r) => r.id)).toEqual(["waiting"]);
    expect(data.paused.map((r) => r.id)).toEqual(["pause"]);
  });
  it("does not display fabricated zero counts before the real status request succeeds", () => {
    const html = renderToStaticMarkup(
      <UIContext.Provider
        value={{
          lang: "en",
          theme: "light",
          t: (_zh, en) => en,
          notify: () => {},
          action: async (f) => f(),
        }}
      >
        <DemoRunSummary runs={[]} enteredAt={enteredAt} available={false} />
      </UIContext.Provider>,
    );
    expect(html).toContain("Reading actual execution status");
    expect(html).not.toContain("Completed before entry");
    expect(html).not.toContain("<strong>0</strong>");
  });
  it("preserves ordinary project routes and their query values without changing external or non-project destinations", () => {
    expect(
      demoLink("/projects/p/figures?metric=brier", "?demo=1&demo_step=C"),
    ).toBe("/projects/p/figures?metric=brier&demo=1");
    expect(demoLink("/settings", "?demo=1")).toBe("/settings");
    expect(demoLink("/projects/p/paper", "")).toBe("/projects/p/paper");
  });
});
