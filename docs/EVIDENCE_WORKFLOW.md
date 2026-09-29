# Evidence-driven research and manuscript workflow

FOREST uses completed run artifacts for general paper drafting. `examples/class_weight_paper.py` is a standalone example; general drafting uses the selected project's goal and evidence.

## Research judgments and experiment duties

`research.validation.protocol.validate_idea` checks the current best estimate, a nonzero-width probability range, confidence, supporting and contrary evidence, decisive unknown, and cheapest discriminating experiment. It also checks all nine requested base-case conclusions. Forecasts must be labeled `ESTIMATED`, and the output distinguishes them from measured frequencies and conference acceptance predictions. Validation does not manufacture probabilities or independently calibrate a model.

`validate_experiment` checks the research question, hypothesis, baseline, candidate, statistical unit, metric, meaningful effect, data split and selection rules, decision rule, and argumentative duty. Duties cover effectiveness, mechanism, scenario value, or an alternative explanation. A confirmatory protocol must explicitly state that evaluation observations were not used for selection. A protocol remains editable; changing it requires an accurate description of which evidence used which conditions.

## Actual evidence to manuscript

1. `collect_evidence` receives explicit completed run records and reads their actual metric files. Missing, unfinished and nonfinite measurements raise an error. A caller can specify `metrics_file`, including a path below `workspace/`.
2. Every numeric leaf gets a bundle-local ID, exact JSON pointer and original run ID. Task configuration and saved method files supply method context. Provider configuration, process environment and credential-like fields are excluded.
3. `manuscript_prompt` supplies the current goal, method context, observed measurements, source reading scope, and optional structured claims to the actual connected model. There is no predefined model response. Authored prose can also be supplied directly to the renderer.
4. The draft references measured values with `[[metric:m0]]` and sources with `[[source:ID]]`. The renderer rejects absent IDs, omitted supplied claims, and omitted contrary references attached to a central claim. These are reference checks; semantic entailment and novelty are not mechanically certified.
5. The renderer creates ordinary editable LaTeX, bibliography, metric macros, an evidence bundle, and a validation report. All metric copies are reread before rendering to detect changes during drafting. Concurrent edits produce a refresh instruction, not a restriction on future editing.
6. Compilation runs the installed TeX toolchain. A failed compile removes stale PDF output. Publication uses normal manuscript revisions, preserving authored edits as an editable proposal rather than silently replacing them.

The generic manuscript contains no invented graph, curve, citation or number. Numbers written directly in prose are listed for review because a date or mathematical constant is not necessarily an experimental result. A numerical binding establishes where a value came from; it does not establish scientific validity.

The worker uses `draft_with_model` for actual provider calls. `paper_draft_attempts` configures the maximum number of replies, with a default of three. JSON or reference errors are sent back with exact token syntax and the previous actual response. Every reply, elapsed time, usage and validation error is retained in `model_responses/` and `generation_attempts.json`; exhausting attempts fails the task without substituting prose. The budget can be changed for a later retry.

## Independent analysis and reproducibility

`paired_csv` reads real observed baseline and candidate scores from a CSV. It averages within declared units and resamples whole units, so repeated observations of the same unit are not counted as independent experiments. It reports the measured effect, percentile interval, practical threshold, pairing fields, seed, and bootstrap count. Its inference is conditional on evaluated fitted models. It does not report a bootstrap fraction as a posterior probability of a hypothesis.

`compare_result_files` directly compares selected numeric JSON pointers in two existing result files. Tolerances are explicit and default to exact equality. It reports `NUMERIC_MATCH` or `NUMERIC_DIFFERENCE`; a match alone is not presented as proof that a second experiment was executed. Reproducibility review also needs distinct run receipts, commands, data and environment information. Timing fields should be excluded from deterministic scientific comparisons using an explicit pointer selection.

The methodological review catalog covers eleven statistical pitfalls, following the [ARS experiment workflow](https://github.com/Imbad0202/academic-research-skills-codex/tree/main/plugins/ars-codex/skills/academic-research-suite/ars/experiment-agent). An evidence-aware reviewer must mark each as supported, an issue, not applicable, or not assessed. The output distinguishes catalog coverage from actual assessed coverage. Unknown information never becomes an automatic pass.

## Minimal wording review

`review_defensive_writing` reports exact offsets and minimal suggestions for recognizable defensive phrases. Redundant phrases can receive literal reductions. Ambiguous phrases receive a request for the controlling condition, measured effect or decisive unknown, rather than an invented scientific judgment. It states that its coverage is literal matching, not exhaustive semantic review.

`validate_revision_proposal` checks that proposed originals actually occur, that repeated spans have explicit offsets, and that proposals do not overlap or replace multiple paragraphs. Unaffected text remains unchanged.

## Verification

The tests execute actual numerical integration in a subprocess, read its saved error and work measurements, render an authored draft against those measurements, compile real LaTeX when the toolchain is installed, detect later edits to metrics and macros, and compare independent numerical analyses. Additional tests reject missing evidence, unsupported citations, omitted contrary references, incomplete research judgments and mislabeled confirmatory data.

These tests establish the implemented artifact and validation behavior. They do not establish autonomous discovery, successful baseline reproduction for an unspecified new topic, or acceptance at a scientific venue.
