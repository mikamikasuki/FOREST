import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { focusTrapTarget, Modal } from "./ui";

describe("modal keyboard focus", () => {
  it("keeps modal semantics and allows the dialog to receive fallback focus", () => {
    const html = renderToStaticMarkup(
      <Modal title="New research project" onClose={() => {}}>
        <button type="button">Cancel</button>
      </Modal>,
    );
    expect(html).toContain('role="dialog"');
    expect(html).toContain('aria-modal="true"');
    expect(html).toContain('aria-label="New research project"');
    expect(html).toContain('tabindex="-1"');
  });

  it("cycles focus at the dialog boundaries and contains escaped focus", () => {
    const first = {} as HTMLElement;
    const middle = {} as HTMLElement;
    const last = {} as HTMLElement;
    const outside = {} as Element;
    const focusable = [first, middle, last];

    expect(focusTrapTarget(focusable, first, true)).toBe(last);
    expect(focusTrapTarget(focusable, last, false)).toBe(first);
    expect(focusTrapTarget(focusable, middle, false)).toBeNull();
    expect(focusTrapTarget(focusable, outside, false)).toBe(first);
    expect(focusTrapTarget(focusable, outside, true)).toBe(last);
    expect(focusTrapTarget([], outside, false)).toBeNull();
  });
});
