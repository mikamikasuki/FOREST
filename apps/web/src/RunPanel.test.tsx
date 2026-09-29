import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { RunPanel } from "./Workspace";
import { UIContext } from "./ui";
import type { Run } from "./api";

function renderRun(status: string) {
  const run = { id: "run-123", status, kind: "command", created_at: "2026-09-29T00:00:00Z" } as Run;
  return renderToStaticMarkup(
    <UIContext.Provider value={{ lang: "en", theme: "light", t: (_zh, en) => en, notify: () => {}, action: async (f) => f() }}>
      <RunPanel runs={[run]} reload={async () => {}} />
    </UIContext.Provider>,
  );
}

describe("run controls reflect allowed transitions", () => {
  it.each(["completed", "failed", "cancelled", "interrupted", "skipped"])("disables process controls for %s while retaining retry", (status) => {
    const html = renderRun(status);
    for (const action of ["pause", "resume", "cancel"]) {
      expect(html.match(new RegExp(`<button[^>]*aria-label="${action}"[^>]*>`))?.[0]).toContain('disabled=""');
    }
    expect(html.match(/<button[^>]*aria-label="retry"[^>]*>/)?.[0]).not.toContain('disabled=""');
    expect(html).toContain("No output");
    expect(html).not.toContain("Waiting for executor output");
  });

  it.each([
    ["queued", ["pause", "cancel"]],
    ["running", ["pause", "cancel"]],
    ["pausing", ["cancel"]],
    ["paused", ["resume", "cancel"]],
    ["waiting_input", ["resume", "cancel"]],
    ["waiting", ["pause", "resume", "cancel"]],
    ["budget_exhausted", ["pause", "resume", "cancel"]],
  ])("keeps valid controls available for %s", (status, enabled) => {
    const html = renderRun(status as string);
    for (const action of ["pause", "resume", "cancel", "retry"]) {
      const button = html.match(new RegExp(`<button[^>]*aria-label="${action}"[^>]*>`))?.[0];
      expect(button).toBeDefined();
      expect(button?.includes('disabled=""')).toBe(!enabled.includes(action));
    }
    expect(html).toContain("Waiting for executor output");
  });
});
