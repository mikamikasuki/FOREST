# Editable manuscript and scientific-figure layout

The same exact evidence can be rendered at different physical widths. Layout changes do not change measurements, replace current edited prose with an old draft, or imply scientific validation.

## Public interfaces

`research.paper.layout.normalize_layout(layout=None, template='article')` validates the configuration. `generate_paper(..., layout=...)` and `write_manuscript(..., layout=...)` use it for structured manuscripts. A draft may also carry `layout`; an explicit call argument takes precedence.

```json
{
  "columns": "double",
  "significant_digits": 5,
  "scientific_notation": "auto",
  "table_font_pt": 9,
  "min_font_pt": 8,
  "max_table_rows": 18,
  "float_placement": "auto",
  "figure_span": "auto",
  "table_span": "auto"
}
```

- `columns`: `single` or `double`. The official `iclr2027` template requires `single`; a conflicting request fails explicitly. `article` supports both. Double-column article title and abstract span the page.
- `significant_digits`: integer 2–10. `scientific_notation`: `auto`, `always`, `never`. Integer counts remain integer counts. Original values stay exact in bindings and evidence; display text is recorded separately.
- `table_font_pt`, `min_font_pt`: 7–12 pt, with table font at least the minimum. No resize-to-fit or microscopic table font is inserted.
- `max_table_rows`: integer 4–60. Long structured tables use repeated headers and page breaks in single-column documents; double-column tables use repeated panels.
- `float_placement`: `auto`, `top`, `bottom`, `page`, `here`. These are ordinary LaTeX placement requests, not guaranteed coordinates. Result floats are bounded before Discussion, references and appendices.
- `figure_span`, `table_span`: `auto`, `column`, `page`. Auto uses page spans for wide double-column comparisons.

Each table/figure block may override `layout:{span,placement,strategy,max_rows,font_pt,panel_columns}`. Strategies are `auto`, `wrap`, `split`, `longtable`; double-column longtables become split panels. Table `alignment` can give `left`, `right` or `center` for each column; numeric-token columns default to right alignment. Headers and text wrap within measured column widths. Figure `panels:[{figure_id,caption}]` combines existing evidence figures without inventing new files; `panel_columns` is 1–3. Equation wrapping uses existing relation separators, retaining formulas and labels.

## Reflow current edits

`apply_layout(directory, layout, template=None)` reads the current `paper.tex`, assets and numeric bindings. It changes format in that source, preserving edited words and table cells. It can split recognized booktabs tables and convert current generated longtables into full-width panels for double columns. Existing separate panels are not merged into a reconstructed old table. Unknown custom floats are preserved with an explicit advisory. Edited numeric macros that disagree with their recorded bindings are preserved and flagged; they are not silently overwritten.

Template detection checks metadata and current source. Callers should copy the current source/assets to a new run and publish through revision protection; the worker uses this flow for the Paper Studio layout action.

## Plans and actual checks

`layout_plan.json` records normalized settings, each block's selected span/strategy/size, and reasons. `compile_paper` returns `preflight` and `preflight_path`, and writes `layout_preflight.json` after actual compilation:

```json
{
  "status": "passed",
  "actual_compilation": true,
  "page_count": 10,
  "issues": [],
  "assets": [{"path":"figures/figure0.pdf","exists":true,"kind":"figure"}],
  "checks": {"glyphs":"passed","overflow":"passed","references":"passed","assets":"passed","pdf":"passed","bindings":"passed"}
}
```

The report distinguishes `passed`, `needs_review`, `failed`, `unavailable`, and per-check `not_checked`. It observes the final compiler log, readable PDF page count, missing glyphs, overfull boxes, references, local assets and registered numeric bindings. It does not claim a scientific finding or replace page-by-page visual review. A plot with declared original physical width/font metadata is flagged when planned placement would reduce its labels below the manuscript minimum.

## Scientific figures

`render_figure(..., style=...)` accepts `paper_layout`, `paper_template`, `span`, or explicit `layout_width_in`. Width is in inches and font sizes are final points. Default plots use at least 9 pt text, an accessible categorical palette plus marker/hatch distinctions, vector PDF/SVG and 300-DPI PNG. Long titles/labels wrap; legends receive physical space. Data, editable source, style and `figure_report.json` are retained.

All observed methods are included by default. Explicit selections record omitted methods. A metric's unit comes from `style.unit` or the supplied axis label. Missing units are reported; no units are invented. Error bars use a declared `error:{type,column,unit}` or `error:{type,lower,upper,unit}`; types are `sd`, `se`, `ci95`, `interval`. A supplied `metric_std` is identified as SD, without assuming a seed unit. Missing uncertainty is not labeled as mean ± seed SD.

Duplicate bar groups require explicit `aggregation:{method:'mean',unit:'seed',uncertainty:'sd'|'se'|'ci95'|'none'}` and unique observed units. CI95 uses a t interval over those declared units; this computation does not establish their statistical independence. Line charts require already disambiguated method/x pairs. Calibration curves need real binary outcomes and probabilities; pooling multiple datasets/splits requires an explicit `pooling_scope`.

Method diagrams require supplied `nodes:[{id,label}]` and `edges:[{source,target}]`. The old class-weight diagram remains only under the explicitly selected `style.example:'class_weight_calibration'` example.
