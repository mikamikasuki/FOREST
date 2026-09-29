import type { Json } from "./api";
import { Button, Field, JsonView } from "./ui";

export type PaperTemplate = "article" | "iclr2027";
export function paperTemplate(data: Json = {}): PaperTemplate {
  if (data.template === "iclr2027") return "iclr2027";
  if (data.template === "article") return "article";
  const source = String(data.source || "").replace(/^\s*%.*$/gm, "");
  return /\\usepackage(?:\[[^\]]*\])?\{[^}]*\biclr2027_conference\b[^}]*\}/.test(
    source,
  )
    ? "iclr2027"
    : "article";
}
export type PaperLayoutConfig = {
  columns: "single" | "double";
  significant_digits: number;
  scientific_notation: "auto" | "always" | "never";
  figure_span: "auto" | "column" | "page";
  table_span: "auto" | "column" | "page";
  float_placement: "auto" | "top" | "bottom" | "page" | "here";
  table_font_pt: number;
  min_font_pt: number;
  max_table_rows: number;
};

export function paperLayoutDefaults(data: Json = {}): PaperLayoutConfig {
  const layout = data.layout || {};
  return {
    columns:
      paperTemplate(data) === "iclr2027"
        ? "single"
        : (layout.columns ?? "single"),
    significant_digits: layout.significant_digits ?? 5,
    scientific_notation: layout.scientific_notation ?? "auto",
    figure_span: layout.figure_span ?? "auto",
    table_span: layout.table_span ?? "auto",
    float_placement: layout.float_placement ?? "auto",
    table_font_pt: layout.table_font_pt ?? 9,
    min_font_pt: layout.min_font_pt ?? 8,
    max_table_rows: layout.max_table_rows ?? 18,
  };
}

export function PaperLayoutSettings({
  template,
  layout,
  onTemplateChange,
  onChange,
  onApply,
  busy,
  disabled,
}: {
  template: PaperTemplate;
  layout: PaperLayoutConfig;
  onTemplateChange: (value: PaperTemplate) => void;
  onChange: (value: PaperLayoutConfig) => void;
  onApply: () => void;
  busy: boolean;
  disabled?: boolean;
}) {
  const set = <K extends keyof PaperLayoutConfig>(
    key: K,
    value: PaperLayoutConfig[K],
  ) => onChange({ ...layout, [key]: value });
  return (
    <details className="paper-layout">
      <summary>Layout and numbers</summary>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onApply();
        }}
      >
        <fieldset disabled={busy || disabled}>
          <div className="paper-layout-grid">
            <Field label="Template">
              <select
                aria-label="Paper template"
                value={template}
                onChange={(e) =>
                  onTemplateChange(e.target.value as PaperTemplate)
                }
              >
                <option value="article">Article</option>
                <option value="iclr2027">ICLR 2027</option>
              </select>
            </Field>
            <Field
              label="Columns"
              hint={
                template === "iclr2027"
                  ? "ICLR uses one column. Select Article for two columns."
                  : undefined
              }
            >
              <select
                aria-label="Paper columns"
                value={layout.columns}
                onChange={(e) =>
                  set("columns", e.target.value as PaperLayoutConfig["columns"])
                }
              >
                <option value="single">One column</option>
                <option value="double" disabled={template === "iclr2027"}>
                  Two columns
                </option>
              </select>
            </Field>
            <Field label="Figure width">
              <select
                aria-label="Figure width"
                value={layout.figure_span}
                onChange={(e) =>
                  set(
                    "figure_span",
                    e.target.value as PaperLayoutConfig["figure_span"],
                  )
                }
              >
                <option value="auto">Automatic</option>
                <option value="column">Column width</option>
                <option value="page">Page width</option>
              </select>
            </Field>
            <Field label="Table width">
              <select
                aria-label="Table width"
                value={layout.table_span}
                onChange={(e) =>
                  set(
                    "table_span",
                    e.target.value as PaperLayoutConfig["table_span"],
                  )
                }
              >
                <option value="auto">Automatic</option>
                <option value="column">Column width</option>
                <option value="page">Page width</option>
              </select>
            </Field>
            <Field label="Figure and table placement">
              <select
                aria-label="Figure and table placement"
                value={layout.float_placement}
                onChange={(e) =>
                  set(
                    "float_placement",
                    e.target.value as PaperLayoutConfig["float_placement"],
                  )
                }
              >
                <option value="auto">Automatic</option>
                <option value="top">Top of page</option>
                <option value="bottom">Bottom of page</option>
                <option value="page">Separate page</option>
                <option value="here">Near its text</option>
              </select>
            </Field>
            <Field label="Significant digits">
              <input
                aria-label="Significant digits"
                type="number"
                min="2"
                max="10"
                step="1"
                required
                value={layout.significant_digits}
                onChange={(e) =>
                  set("significant_digits", Number(e.target.value))
                }
              />
            </Field>
            <Field label="Scientific notation">
              <select
                aria-label="Scientific notation"
                value={layout.scientific_notation}
                onChange={(e) =>
                  set(
                    "scientific_notation",
                    e.target.value as PaperLayoutConfig["scientific_notation"],
                  )
                }
              >
                <option value="auto">Automatic</option>
                <option value="always">Always</option>
                <option value="never">Decimal</option>
              </select>
            </Field>
          </div>
          <details className="paper-table-settings">
            <summary>Table sizing</summary>
            <div className="paper-layout-grid">
              <Field label="Table text size (pt)">
                <input
                  aria-label="Table text size"
                  type="number"
                  min={layout.min_font_pt}
                  max="12"
                  step="0.5"
                  required
                  value={layout.table_font_pt}
                  onChange={(e) => set("table_font_pt", Number(e.target.value))}
                />
              </Field>
              <Field label="Minimum text size (pt)">
                <input
                  aria-label="Minimum table text size"
                  type="number"
                  min="7"
                  max={layout.table_font_pt}
                  step="0.5"
                  required
                  value={layout.min_font_pt}
                  onChange={(e) => set("min_font_pt", Number(e.target.value))}
                />
              </Field>
              <Field label="Rows per table section">
                <input
                  aria-label="Rows per table section"
                  type="number"
                  min="4"
                  max="60"
                  step="1"
                  required
                  value={layout.max_table_rows}
                  onChange={(e) =>
                    set("max_table_rows", Number(e.target.value))
                  }
                />
              </Field>
            </div>
          </details>
          <div className="paper-layout-footer">
            <span className="muted">
              Number formatting changes display precision; recorded measurements
              stay unchanged.
            </span>
            <Button type="submit" busy={busy}>
              Apply layout and compile
            </Button>
          </div>
        </fieldset>
      </form>
    </details>
  );
}

export function PaperPreflight({
  report,
  plan,
}: {
  report?: Json;
  plan?: Json;
}) {
  if (!report)
    return (
      <p className="muted">
        No layout check has been recorded for this version.
      </p>
    );
  const issues = Array.isArray(report.issues) ? report.issues : [];
  return (
    <section className="paper-preflight" aria-label="Layout checks">
      <div className="paper-preflight-summary">
        <strong>Layout checks</strong>
        {report.status && (
          <span>{String(report.status).replaceAll("_", " ")}</span>
        )}
        {typeof report.page_count === "number" && (
          <span>{report.page_count} pages</span>
        )}
      </div>
      {issues.length > 0 && (
        <ul>
          {issues.map((issue, index) => (
            <li key={index}>
              {typeof issue === "string" ? (
                issue
              ) : (
                <>
                  {issue.severity && <strong>{issue.severity}: </strong>}
                  {issue.message || issue.detail || JSON.stringify(issue)}
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      <details>
        <summary>Check details</summary>
        <JsonView value={report} />
      </details>
      {plan && (
        <details>
          <summary>Figure and table plan</summary>
          {Array.isArray(plan.blocks) && plan.blocks.length > 0 && (
            <div className="paper-plan-table">
              <table>
                <thead>
                  <tr>
                    <th>Item</th>
                    <th>Width</th>
                    <th>Placement</th>
                    <th>Text size</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.blocks.map((block: Json, index: number) => (
                    <tr key={`${block.id}-${index}`}>
                      <td>{block.id || block.type || `Item ${index + 1}`}</td>
                      <td>
                        {block.span || "—"}
                        {typeof block.width_in === "number"
                          ? ` · ${block.width_in.toFixed(2)} in`
                          : ""}
                      </td>
                      <td>{block.placement || "—"}</td>
                      <td>
                        {typeof block.font_pt === "number"
                          ? `${block.font_pt} pt`
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <details>
            <summary>Plan details</summary>
            <JsonView value={plan} />
          </details>
        </details>
      )}
    </section>
  );
}
