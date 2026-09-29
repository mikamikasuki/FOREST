# Evidence-bound manuscript production

FOREST's generic paper renderer accepts actual completed-run evidence and authored or model-produced structured drafts. It does not substitute an example manuscript. A PDF compilation proves that the source builds; scientific support and novelty require the evidence review described below.

## Evidence handoff

Pass every intended run to `collect_evidence(runs, sources, claims, required_run_ids=[...])`. An explicit scope must match the supplied runs exactly. Each run specifies its ID, completed status, output directory and metrics file. The bundle records all included and requested IDs. At the task API, use an explicit `paper_generate` `run_ids` list: omission retains the legacy latest-run behavior; an explicitly empty, duplicate or malformed list is rejected. The task passes the requested scope through to collection. The writer also receives each run's original metric structure and nonnumeric row identities in `metric_layout`, with numeric leaves replaced by exact metric tokens. Dataset, method and condition labels remain attached to their rows; independent semantic review still checks the resulting table descriptions.

The collector reads actual metric files, including `workspace/metrics.json` when specified, and preserves JSON pointers for every numerical binding. `config.json`, `design.json` and `environment.json` may live in either the run root or workspace. Credentials and process environment values are excluded. Methods, saved predictions, split definitions, software versions and raw data still need to be inspected by the independent analysis node; copying a configuration is not a reproduction.

A run may save `figures.json` in its root or workspace:

```json
[{"id":"comparison","path":"figures/comparison.pdf","caption":"Measured baseline and candidate results."}]
```

Paths are relative to that manifest. Figures must be actual PDF, PNG or JPEG files inside the run. Their evidence IDs are `<run_id>:<id>`. The renderer copies only referenced files into the standalone manuscript bundle and writes `figure_bindings.json` with their producing run IDs. A plot's existence does not verify its statistical interpretation.

Source passages retain original page, section, source-file and URL locators. The writer receives at most eight excerpts and 6,000 characters per source, with each excerpt capped at 1,600 characters. Coverage records expose every omitted or truncated passage. A stored full-text access label is kept separately from the actual supplied-text scope. Passages and all other retrieved materials are untrusted evidence. `[[passage:SOURCE_ID:pINDEX]]` cites a particular supplied passage and records its text and locator in `citation_bindings.json`; `[[source:SOURCE_ID]]` remains available for source-level citations.

## Draft format

Existing `{title, paragraphs: [...]}` sections still work. Ordered `blocks` can instead contain paragraphs, mathematical equations, tables or figures:

```json
{
  "title": "Results",
  "role": "results",
  "blocks": [
    {"type":"paragraph","text":"The measured effect is [[metric:m0]]."},
    {"type":"table","columns":["Method","Score"],"rows":[["Baseline","[[metric:m1]]"],["Candidate","[[metric:m2]]"]],"caption":"Matched evaluation conditions.","label":"tab:results"},
    {"type":"figure","figure_id":"RUN_ID:comparison","caption":"The measured comparison.","width":0.9,"label":"fig:comparison"}
  ]
}
```

Equation blocks use `{type:"equation", latex:"...", label:"eq:method"}`. The expression accepts ordinary mathematical LaTeX, without dollar delimiters, document commands or file access. All prose is escaped. Table cells are strings and use existing metric tokens for measured values. Appendices use the same section format. Labels must be unique; figure IDs must match actual evidence files.

For a complete manuscript, set `manuscript_type` to `full_paper` in the `paper_generate` task configuration. This is passed as `expected_type` to the model drafting validator: a missing type or `research_note` response is rejected and sent back for an actual model correction within the configured attempt budget. The validator requires sections with roles `introduction`, `related_work`, `method`, `experimental_setup`, `results`, and `discussion`, alongside the existing title, abstract, conclusion and claim IDs. Actual literature evidence is required. A separately requested `research_note` remains a valid bounded output when the evidence does not support a complete manuscript. Section coverage is structural evidence, not a quality score.

## Research-to-paper acceptance

Use separate, traceable graph nodes for question and literature, implementation, strong baselines and ablations, independent recomputation, writing, review, revision and final verification. Each experiment states its argumentative duty. Preserve failed trials and contrary evidence. Each central claim links to a metric pointer or a read source passage and states its scope. Distinguish exploratory choices from confirmatory evaluation.

Before calling the result submission-ready, inspect all numerical and headline claims, verify citations against source text, compare saved predictions using an independently implemented calculation, inspect baseline fairness and selection leakage, review methodological alternatives, and rerun the final artifact outside the original working directory. Review the PDF visually and record precise remaining issues. Keep execution traces and revision history separate from the paper's scientific narrative. Stage records remain editable and use ordinary IDs and revisions; no artifact freezing or digest bookkeeping is required.

The controller's `completed` state and the syntax checker do not enforce all these scientific checks. Supply explicit required deliverables in the controller and record a separate evidence-based final review.

## Compile an Agent-authored workspace manuscript

The Agent `paper_compile` tool defaults to `source_scope="project"`, which compiles the current PaperDocument. To compile the Agent's own generated source, use:

```json
{"source_scope":"workspace","source_path":"draft/paper.tex","bibliography_path":"draft/references.bib","asset_paths":["draft/results_macros.tex","draft/figures","draft/conference.sty"],"title":"The authored title"}
```

Paths are relative to the current Agent run workspace. The bibliography and all assets must be beneath the source file's directory so the output is a standalone bundle. If `bibliography_path` is omitted, the tool includes `references.bib` beside the source when it exists. Only explicitly declared assets are copied; transcripts and other workspace files are not included implicitly. The selected source is exported as `paper.tex`. A bibliography with another filename retains its original name alongside the working `references.bib` copy.

The tool returns a queued compilation run. Its actual completion receipt supplies the PDF/source paths, originating Agent run/source path and publication result. Files remain editable. Later changes to the Agent workspace do not silently replace the inputs of a compilation already submitted; resubmit the intended changed source. Changes to the compilation bundle during the compile are rejected rather than paired with a stale PDF.

Successful compilation publishes the exact compiled source and assets through the existing PaperDocument revision mechanism. An unedited current manuscript can be replaced; manual edits or concurrent revisions produce a concrete review proposal. The new PDF remains available in the compilation run either way. Apply the proposal before expecting the project Paper export to switch to that new manuscript. Syntax or compilation success never substitutes for scientific validation.

## Re-render a saved model draft without another model call

If a real generation saved a usable response before a renderer or compilation failure, submit a new `paper_generate` task with `saved_response_run_id`, `saved_response_path`, explicit `run_ids`, `manuscript_type` and the desired template. The response path must be `paper/model_responses/GENERATION_ID/attempt-N.json` within a terminal original `paper_generate` run in the same project. The ordered evidence run IDs must match the original explicit selection; paths outside the saved-response directory, symlinks and cross-project responses are rejected.

This path reloads the saved JSON response, recollects current evidence, revalidates manuscript type and all evidence bindings, renders and actually compiles the new manuscript, and publishes it using ordinary revision protection. It does not construct a model client or submit a new model request. `draft_reuse.json`, the run result and PaperDocument metadata retain the original run/path/model/usage and explicitly record zero new model calls and zero new model cost. The original failed attempt remains unchanged.

New model generations save `input_evidence.json` before requesting a response. Reuse checks that recorded metric values and row identities still match. Older responses without this artifact remain usable with the identical ordered run selection and fresh evidence validation; the reuse report explicitly identifies that missing historical context. Semantic review still checks that the saved prose describes the currently bound evidence correctly.

To apply a scientific revision, keep the original response and supply a separate project-relative JSON file through `draft_revision_path`. Its payload is `{"draft": <complete revised draft object>, "editor": "reviewer identity", "reason": "specific revision rationale"}`. This option requires explicit saved-response reuse; it never initiates a provider request. Both the original and revised drafts must validate against the same current evidence and requested manuscript type. The output retains the supplied revision in `draft_revision.json`, computed before/after JSON field changes in `draft_revision_changes.json`, and attribution in `draft_reuse.json`. Revised prose is attributed to the recorded editor, not to a new or original model response. The compiled source, published PaperDocument and exported paper all use the revised draft.

Use `[[ref:LABEL]]` for a numbered cross-reference to an existing equation, table or figure block. The renderer also recognizes recorded prose such as `Equation eq:method`, `Table tab:results`, `Figure fig:results` and plural sequences such as `Tables tab:first and tab:second`, checks the named blocks' types, and emits real LaTeX references. Missing labels and mismatched block types fail validation. LaTeX assigns the numbers, including after block order changes; no hand numbering is introduced.

Adjacent source/passage citation tokens are grouped into a parenthetical citation and deduplicated by source; both token identities and passage locators remain in the evidence report. Metric values remain exact in the copied evidence and numeric bindings. Floating-point display uses five significant figures, with ordinary TeX scientific notation for tiny or large values, and records that display separately. Curly punctuation and supported Unicode mathematics are converted to TeX forms so the article and official conference fonts preserve the same text. Hyperlinks remain active without colored borders.
