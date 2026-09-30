# Paper authoring

FOREST separates research decisions, manuscript prose and revision proposals. Scientific projects default to the editable `full_submission` profile and `full_paper` manuscript type. Literature, experiment, analysis, visual, writing and review agents share the evidence-led anti-defensive writing contract. User-authored role instructions remain editable and are preserved during upgrades.

Research analysis states the best estimate, probability range, confidence, supporting and opposing evidence, decisive unknown and cheapest discriminating experiment. Its base-case conclusion remains a research artifact. Manuscripts organize the final argument around the strongest supported contribution: problem, gap, approach and decisive evidence. They state the condition, mechanism and practical value of an advantage. Material contrary evidence remains available and is included where it changes the central claim; development diaries and repeated self-negating verdicts do not become the paper's narrative.

The shared contract excludes defensive framing and unsolicited AI-writing statements from manuscript prose. Research forecasts, execution logs and review receipts remain inspectable project artifacts. This writing policy does not remove results that change the scientific conclusion.

Revision proposals identify the exact original span, minimal replacement and reason. Repeated text needs an explicit character offset. The validator rejects overlapping edits, invented original text, edits spanning multiple paragraphs and replacement of an entire multi-sentence paragraph. The local phrase checker is a targeted detector; the model reviewer also inspects framing beyond literal phrase matches. Neither a style suggestion nor a successful compilation certifies scientific novelty.

## Full-submission scope and review

The default plan starts with fifteen closely related officially accepted papers, three real datasets, five strong baselines, five independent seeds or replicates and four component ablations. These are editable design targets; the relevant accepted literature determines the substantive workload and statistical units. Main comparisons, mechanism tests, scenario studies and alternative-explanation tests have distinct scientific duties. A preliminary run helps select the next experiment and leaves the full submission outstanding.

The bundled [reference corpus](PUBLICATION_REFERENCE_CORPUS.md) records sixteen official accepted full-text papers, with actual figure/table pages, experimental coverage and transferable review criteria. Each project must still import and read sources relevant to its own contribution. Search results, preprints and guessed peer counts do not establish accepted/full-text coverage. Agents compare actual observations, baseline fairness, uncertainty, manuscript coverage and printed visuals against those sources, record precise gaps and schedule repairs. The [delivery audit](PUBLICATION_DELIVERY.md) retains unfinished work when execution or spending limits stop the loop. Passing the audit does not predict a venue's acceptance decision.

## Figures, image candidates and manuscript positions

In **Figure Studio**, create a measured plot from completed runs, an editable method graph from actual components, or a conceptual image describing the actual mechanism. Empirical plots and tables use saved measurements and declared statistical units. Conceptual illustrations cannot supply invented curves, observations or experiment photographs.

Architecture figures default to a grounded problem, dominant mechanism zoom and evidenced consequence or explicitly labeled testable expectation. Actual inputs, outputs and boundaries remain visible; generic module catalogs fail the scientific review. An explicit simple method-only request retains its supplied topology. The [scientific visual narrative contract](SCIENTIFIC_VISUAL_NARRATIVE.md) describes the accepted-paper precedents, evidence bindings and rendering workflow.

Rendering with a connected provider creates alternative presentations and submits the actual candidate pixels to three separate reviewers: **Evidence Reviewer**, **Figure Critic** and **Visual Editor**. They check fidelity and statistical clarity, print readability and comparison coverage, and argumentative purpose. A candidate is eligible only when all three accept it; the eligible candidate with the highest aggregate score is selected. Failed reviews retain the candidates, responses and concrete repair requests for another actual rendering pass. A render without these reviews remains `needs_review`.

After drafting, **Argument Reviewer**, **Layout Reviewer** and **Visual Editor** independently select existing paragraph IDs for every figure and table within its section. Majority agreement selects the anchor; the Visual Editor resolves a three-way tie. The renderer orders the blocks after those paragraphs and inserts local numbered references. Compilation then audits actual figure/table pages against paragraph pages. This separates a model's proposed location from the location that LaTeX actually produced; final PDF reading remains part of review.

Studio figures are independent project assets. Paper generation can select their `figure_ids` alongside explicit completed `run_ids`, without requiring the image to live in an experiment's output folder. Selected scientific figures must use runs within that evidence scope. The standalone paper bundle copies each referenced image and its available editable source, data, style, report and selection artifacts, and records their bindings.

For an existing manuscript, open **Paper → Insert a reviewed figure in the manuscript**. Select a rendered, reviewed Studio figure and paste a unique paragraph from the current source. The action saves pending edits, inserts the figure and numbered reference after that paragraph, and copies the image and available companions into the paper assets. Compile again to inspect the resulting PDF. The [manual insertion API](MANUSCRIPT_PIPELINE.md#insert-a-studio-figure-into-current-manuscript-prose) offers the same revision-checked operation.

## Image-provider configuration

In **Settings**, explicitly configure the provider's `image_generation.model` and positive `image_generation.max_request_usd`. The image model is independent of the text model; the workflow does not select a replacement model. Default conceptual-image generation makes three separate requests, with their candidate prompts and safe metadata retained. PNG base64 responses are required; returned image URLs are never downloaded.

Each request reserves its saved maximum in the shared provider, project and run spending ledger. Image token usage is not priced using text rates; an unverified image charge retains its reserved upper bound. Budget also needs to cover independent model reviews and any repair iterations. See [image transport configuration](IMAGE_GENERATION_TRANSPORT.md) for compatible endpoint options and accounting details.

## Layout and numbers

In **Paper → Layout and numbers**, select the template and column count, figure/table width, placement, significant digits and scientific notation. Advanced table settings control the minimum font size and row grouping. The ICLR 2027 template remains single-column; the Article template supports one or two columns. Structured drafts can also supply per-block span, placement, table strategy and figure panels.

Numeric bindings retain their exact original values. Precision changes alter displayed macros without changing measurements. Structured tables use aligned numeric columns, wrapped text, repeated headers and split panels or multi-page tables. A table is not silently made illegible by shrinking its font to fit. Figures retain editable data/style/source and PDF/SVG output, with dimensions specified at the intended print width. Statistical units and uncertainty definitions must be supplied; duplicated measurements require an explicit aggregation rule. A generic method diagram requires actual supplied nodes and edges.

Layout jobs capture the current source, bibliography and assets when requested, preserve author-edited prose, and compile a separate output bundle. The result becomes current only if the source revision still matches. A newer edit is retained and the compiled result becomes an inspectable proposal. Source edits invalidate previous layout checks.

`layout_plan.json` records the chosen arrangement. `layout_preflight.json` records actual compilation, page count, missing assets/glyphs, undefined references, overflow, numeric bindings and available visual-placement observations. `placement_report.json` records actual LaTeX auxiliary page positions relative to argumentative paragraph anchors. Unsupported custom LaTeX is preserved and reported for review. Compiler checks and visual reading are separate: a readable PDF can still contain an unsuitable comparison or unsupported claim.

## Long context

FOREST treats legacy character budgets as working-set hints. Complete tasks and histories remain in editable files; automatic packing retains complete native tool exchanges or replaces an entire exchange with a retrievable public record. Required original task pages are read before task actions proceed. Provider window rejections trigger smaller requests under the same spending controls. Planner and manuscript/revision requests use the same retained-context approach.

The model service still has a finite per-request window. The application handles work larger than that window through persistence and retrieval; it does not advertise an infinite provider window or remove the owner's spending limit. Historical failed attempts and their original errors remain recorded.
