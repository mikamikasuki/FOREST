# Editable manuscript and scientific-figure layout

The same exact evidence can be rendered at different physical widths. Layout changes do not change measurements, replace current edited prose with an old draft, or imply scientific validation.

The full-paper workflow compares coverage and visual duties with fifteen relevant officially accepted full-text papers. The bundled [sixteen-paper corpus](PUBLICATION_REFERENCE_CORPUS.md) supplies sourced examples of real main comparisons, mechanism figures, analysis panels and data tables. It informs design and review; it does not set an arbitrary minimum image count or guarantee acceptance. [The delivery workflow](PUBLICATION_DELIVERY.md) retains actual experiment, analysis and layout gaps until repaired.

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

## Argumentative placement

Every structured visual needs a unique numbered label and a scientific duty. Paragraph blocks can supply stable `id` fields; visual blocks can use `anchor:{"after":"PARAGRAPH_ID","section_role":"results","reason":"..."}`. An anchor must identify an existing paragraph in the visual's section. The structure renderer orders the figure/table after that paragraph, keeps its local numbered reference and applies section barriers.

For a model-generated manuscript, **Argument Reviewer**, **Layout Reviewer** and **Visual Editor** independently choose anchors from the actual prose. Majority agreement selects the location; the Visual Editor resolves a three-way tie. `placement_reviews.json` retains the actual review calls and decisions. These anchors describe intended source order. The compiler's page observations establish where floats actually landed.

An existing source can also receive a reviewed Studio image through **Paper → Insert a reviewed figure in the manuscript** or the [revision-checked insertion API](MANUSCRIPT_PIPELINE.md#insert-a-studio-figure-into-current-manuscript-prose). Paste a unique body paragraph as its anchor, then compile again. The action copies the actual image and available companion artifacts, inserts a numbered reference and clears the preceding layout checks.

## Reflow current edits

`apply_layout(directory, layout, template=None)` reads the current `paper.tex`, assets and numeric bindings. It changes format in that source, preserving edited words and table cells. It can split recognized booktabs tables and convert current generated longtables into full-width panels for double columns. Existing separate panels are not merged into a reconstructed old table. Unknown custom floats are preserved with an explicit advisory. Edited numeric macros that disagree with their recorded bindings are preserved and flagged; they are not silently overwritten.

Template detection checks metadata and current source. Callers should copy the current source/assets to a new run and publish through revision protection; the worker uses this flow for the Paper Studio layout action.

## Plans and actual checks

`layout_plan.json` records normalized settings, each block's selected span/strategy/size, intended paragraph anchor and reasons. `compile_paper` returns `preflight` and `preflight_path`, and writes `layout_preflight.json` after actual compilation. A report includes:

```json
{
  "status": "passed",
  "actual_compilation": true,
  "page_count": 10,
  "issues": [],
  "assets": [{"path":"figures/figure0.pdf","exists":true,"kind":"figure"}],
  "checks": {"glyphs":"passed","overflow":"passed","references":"passed","assets":"passed","pdf":"passed","bindings":"passed","visual_placement":"passed"}
}
```

The report distinguishes `passed`, `needs_review`, `failed`, `unavailable`, and per-check `not_checked`. It observes the final compiler log, readable PDF page count, missing glyphs, overfull boxes, references, local assets and registered numeric bindings. It does not claim a scientific finding or replace page-by-page visual review. A plot with declared original physical width/font metadata is flagged when planned placement would reduce its labels below the manuscript minimum.

For structured visuals with recorded anchors, actual compilation reads the current LaTeX auxiliary labels and writes `placement_report.json` with each `visual_page`, `anchor_page` and available `page_distance`. A visual preceding its argumentative paragraph is an error; a visual more than one page after it requires review. Missing substantive local references also require review. Inspect the recorded observations and actual PDF, then adjust span, panel structure or placement and recompile. Custom source without those anchors remains editable; an unobserved location is not an audited location. A settings change or source insertion requires fresh compilation.

## Scientific figures

`render_figure(..., style=...)` accepts `paper_layout`, `paper_template`, `span`, or explicit `layout_width_in`. Width is in inches and font sizes are final points. Default plots use at least 9 pt text, an accessible categorical palette plus marker/hatch distinctions, vector PDF/SVG and 300-DPI PNG. Long titles/labels wrap; legends receive physical space. Data, editable source, style and `figure_report.json` are retained.

With a connected provider, the Figure Studio workflow renders alternative presentations and sends the actual candidate pixels to **Evidence Reviewer**, **Figure Critic** and **Visual Editor** in separate calls. Only a candidate accepted by all three enters the eligible ranking. Review checks cover actual data fidelity, comparison density, uncertainty, caption purpose and final print legibility. Revisions change presentation while preserving measurements and statistical definitions. The selected independent Studio asset and its available source/data/style/report/selection companions are copied into the standalone manuscript; the paper does not depend on the Studio job's original directory.

All observed methods are included by default. Explicit selections record omitted methods. A metric's unit comes from `style.unit` or the supplied axis label. Missing units are reported; no units are invented. Error bars use a declared `error:{type,column,unit}` or `error:{type,lower,upper,unit}`; types are `sd`, `se`, `ci95`, `interval`. A supplied `metric_std` is identified as SD, without assuming a seed unit. Missing uncertainty is not labeled as mean ± seed SD.

Duplicate bar groups require explicit `aggregation:{method:'mean',unit:'seed',uncertainty:'sd'|'se'|'ci95'|'none'}` and unique observed units. CI95 uses a t interval over those declared units; this computation does not establish their statistical independence. Line charts require already disambiguated method/x pairs. Calibration curves need real binary outcomes and probabilities; pooling multiple datasets/splits requires an explicit `pooling_scope`.

Method diagrams require supplied `nodes:[{id,label}]` and `edges:[{source,target}]`. The old class-weight diagram remains only under the explicitly selected `style.example:'class_weight_calibration'` example.

Conceptual image candidates require an explicit saved `image_generation.model` and positive `image_generation.max_request_usd`. Three actual image requests are made by default, followed by independent visual reviews; image and text-review calls use the shared spending ledger. No automatic model substitution or URL download occurs. [Transport configuration](IMAGE_GENERATION_TRANSPORT.md) describes PNG response options and uncertain charge reservations. Generated conceptual imagery cannot replace measured analysis figures or data tables.
