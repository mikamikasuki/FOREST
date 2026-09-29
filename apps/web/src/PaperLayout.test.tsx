import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import {
  PaperLayoutSettings,
  PaperPreflight,
  paperLayoutDefaults,
  paperTemplate,
} from "./PaperLayout";

describe("paper layout controls", () => {
  it("recognizes an existing ICLR source before layout metadata has been recorded", () => {
    expect(
      paperTemplate({
        source:
          "\\documentclass{article}\n\\usepackage{iclr2027_conference,times}",
      }),
    ).toBe("iclr2027");
    expect(
      paperTemplate({
        source: "% \\usepackage{iclr2027_conference}\n\\documentclass{article}",
      }),
    ).toBe("article");
  });
  it("keeps an ICLR template in one column even when an old config asks for two", () => {
    const layout = paperLayoutDefaults({
      template: "iclr2027",
      layout: { columns: "double" },
    });
    expect(layout.columns).toBe("single");
    const html = renderToStaticMarkup(
      <PaperLayoutSettings
        template="iclr2027"
        layout={layout}
        onTemplateChange={() => {}}
        onChange={() => {}}
        onApply={() => {}}
        busy={false}
      />,
    );
    expect(html).toMatch(/<option value="double" disabled="">Two columns/);
    expect(html).toContain("Select Article for two columns");
  });

  it("retains a paper's saved precision and width settings instead of resetting to defaults", () => {
    expect(
      paperLayoutDefaults({
        template: "article",
        layout: {
          columns: "double",
          significant_digits: 3,
          scientific_notation: "always",
          figure_span: "page",
          table_span: "column",
          table_font_pt: 10,
        },
      }),
    ).toMatchObject({
      columns: "double",
      significant_digits: 3,
      scientific_notation: "always",
      figure_span: "page",
      table_span: "column",
      table_font_pt: 10,
    });
  });

  it("does not claim a layout passed before it has been checked", () => {
    const html = renderToStaticMarkup(<PaperPreflight />);
    expect(html).toContain("No layout check has been recorded");
    expect(html).not.toContain("passed");
  });

  it("shows a real failed check and its artifact information without turning it into a pass", () => {
    const html = renderToStaticMarkup(
      <PaperPreflight
        report={{
          status: "failed",
          actual_compilation: true,
          page_count: 11,
          issues: [
            {
              code: "figure_missing",
              severity: "error",
              message: "Figure results.pdf is missing.",
            },
          ],
          assets: [{ path: "results.pdf", exists: false, kind: "figure" }],
        }}
        plan={{ mode: "existing_source_reflow", blocks: [] }}
      />,
    );
    expect(html).toContain("failed");
    expect(html).toContain("11 pages");
    expect(html).toContain("Figure results.pdf is missing.");
    expect(html).toContain("existing_source_reflow");
  });
});
