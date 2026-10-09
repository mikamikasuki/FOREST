import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { Tabs, tabTargetForKey } from "./ui";

const items = [
  { id: "providers", label: "Model providers" },
  { id: "hosts", label: "Compute hosts" },
  { id: "agents", label: "Agent roles" },
];

describe("accessible tabs", () => {
  it("keeps one tab in the tab order and connects tabs to their panel", () => {
    const html = renderToStaticMarkup(
      <>
        <Tabs
          panelId="settings-tabs"
          items={items}
          value="providers"
          onChange={() => {}}
        />
        <div
          id="settings-tabs-panel"
          role="tabpanel"
          aria-labelledby="settings-tabs-tab-providers"
        />
      </>,
    );

    expect(html).toContain('role="tablist"');
    expect(html).toContain('id="settings-tabs-tab-providers"');
    expect(html).toContain('aria-controls="settings-tabs-panel"');
    expect(html).toContain('aria-selected="true" tabindex="0"');
    expect(html).toContain('aria-selected="false" tabindex="-1"');
    expect(html).toContain(
      'role="tabpanel" aria-labelledby="settings-tabs-tab-providers"',
    );
  });

  it("moves between tabs with arrow keys and supports Home and End", () => {
    expect(tabTargetForKey(items, "providers", "ArrowLeft")).toBe("agents");
    expect(tabTargetForKey(items, "agents", "ArrowRight")).toBe("providers");
    expect(tabTargetForKey(items, "hosts", "Home")).toBe("providers");
    expect(tabTargetForKey(items, "hosts", "End")).toBe("agents");
    expect(tabTargetForKey(items, "hosts", "Enter")).toBeNull();
  });
});
