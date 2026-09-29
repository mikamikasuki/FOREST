# Paper authoring

FOREST separates research decisions, manuscript prose and revision proposals. The default Writer and Figure Designer apply the project's own evidence-led Press-Release contract, informed by the ARS-Codex paper, formatter and visualization workflows. User-authored role instructions remain editable and are preserved during upgrades.

Research analysis states the best estimate, probability range, confidence, supporting and opposing evidence, decisive unknown and cheapest discriminating experiment. Its base-case conclusion remains a research artifact. Manuscripts organize the final argument around the strongest supported contribution: problem, gap, approach and decisive evidence. They state the condition, mechanism and practical value of an advantage. Material contrary evidence remains available and is included where it changes the central claim; development diaries and repeated self-negating verdicts do not become the paper's narrative.

Revision proposals identify the exact original span, minimal replacement and reason. Repeated text needs an explicit character offset. The validator rejects overlapping edits, invented original text, edits spanning multiple paragraphs and replacement of an entire multi-sentence paragraph. The local phrase checker is a targeted detector; the model reviewer also inspects framing beyond literal phrase matches. Neither a style suggestion nor a successful compilation certifies scientific novelty.

## Layout and numbers

In **Paper → Layout and numbers**, select the template and column count, figure/table width, placement, significant digits and scientific notation. Advanced table settings control the minimum font size and row grouping. The ICLR 2027 template remains single-column; the Article template supports one or two columns. Structured drafts can also supply per-block span, placement, table strategy and figure panels.

Numeric bindings retain their exact original values. Precision changes alter displayed macros without changing measurements. Structured tables use aligned numeric columns, wrapped text, repeated headers and split panels or multi-page tables. A table is not silently made illegible by shrinking its font to fit. Figures retain editable data/style/source and PDF/SVG output, with dimensions specified at the intended print width. Statistical units and uncertainty definitions must be supplied; duplicated measurements require an explicit aggregation rule. A generic method diagram requires actual supplied nodes and edges.

Layout jobs capture the current source, bibliography and assets when requested, preserve author-edited prose, and compile a separate output bundle. The result becomes current only if the source revision still matches. A newer edit is retained and the compiled result becomes an inspectable proposal. Source edits invalidate previous layout checks.

`layout_plan.json` records the chosen arrangement. `layout_preflight.json` records actual compilation, page count, missing assets/glyphs, undefined references, overflow and numeric bindings. Unsupported custom LaTeX is preserved and reported for review. Compiler checks and visual reading are separate: a readable PDF can still contain an unsuitable comparison or unsupported claim.

## Long context

FOREST treats legacy character budgets as working-set hints. Complete tasks and histories remain in editable files; automatic packing retains complete native tool exchanges or replaces an entire exchange with a retrievable public record. Required original task pages are read before task actions proceed. Provider window rejections trigger smaller requests under the same spending controls. Planner and manuscript/revision requests use the same retained-context approach.

The model service still has a finite per-request window. The application handles work larger than that window through persistence and retrieval; it does not advertise an infinite provider window or remove the owner's spending limit. Historical failed attempts and their original errors remain recorded.
