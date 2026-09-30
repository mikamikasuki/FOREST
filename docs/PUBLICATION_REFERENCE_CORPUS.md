# Accepted-paper reference corpus

[`accepted_reference_corpus.json`](../research/publication/accepted_reference_corpus.json) contains 16 accepted main-conference papers relevant to research agents, code agents, tool use, and executable evaluation. On 2026-09-29, each official proceedings record returned HTTP 200 and matched its title and venue. The actual official PDFs were read; 32 representative method and results pages were also rendered and inspected. PDF page numbers are one-based.

Counts below cover numbered figures and tables in the main body. A multi-panel figure counts once. References, appendices, supplements, unnumbered code examples, and repeated caption mentions are excluded. The main bodies contain 75 figures and 61 tables in total; medians are 5 figures and 3.5 tables. These are descriptive observations, not acceptance thresholds. DSPy has no numbered main-body figures and uses two dense result tables; a compulsory figure quota would misclassify it.

| Official accepted paper | Venue | Main figures / tables | Verified evaluation scope |
| --- | --- | --- | --- |
| [AgentBench](https://proceedings.iclr.cc/paper_files/paper/2024/hash/e9df36b21ff4ee211a8b71ee8b7e9f57-Abstract-Conference.html) | ICLR 2024 | 3 / 4 | 29 models; 8 environments |
| [SWE-bench](https://proceedings.iclr.cc/paper_files/paper/2024/hash/edac78c3e300629acfe6cbe9ca88fb84-Abstract-Conference.html) | ICLR 2024 | 6 / 8 | 2,294 issues; 12 repositories; 5 model configurations |
| [WebArena](https://proceedings.iclr.cc/paper_files/paper/2024/hash/4410c0711e9154a7a2d26f9b3816d1ef-Abstract-Conference.html) | ICLR 2024 | 5 / 4 | 812 tasks; 170 templates; functional and human evaluation |
| [SWE-agent](https://proceedings.neurips.cc/paper_files/paper/2024/hash/5a7c947568c1b1328ccc5230172e1e7c-Abstract-Conference.html) | NeurIPS 2024 | 8 / 3 | Full and Lite SWE-bench, HumanEvalFix; 6 repeated Lite runs |
| [Toolformer](https://proceedings.neurips.cc/paper_files/paper/2023/hash/d842425e4bf79ba039352da0f658a906-Abstract-Conference.html) | NeurIPS 2023 | 4 / 5 | 5 tools; factual, arithmetic, QA, temporal, multilingual and language-model evaluations |
| [Tree of Thoughts](https://proceedings.neurips.cc/paper_files/paper/2023/hash/271db9922b8d1f4dd7aaef84ed5ac703-Abstract-Conference.html) | NeurIPS 2023 | 6 / 3 | 100 arithmetic puzzles; 100 writing inputs; 20 crosswords |
| [AFlow](https://proceedings.iclr.cc/paper_files/paper/2025/hash/5492ecbce4439401798dcd2c90be94cd-Abstract-Conference.html) | ICLR 2025 | 6 / 2 | 6 benchmarks; 7 baseline methods; 4 executors; 3 test repetitions |
| [AgentSquare](https://proceedings.iclr.cc/paper_files/paper/2025/hash/0ae94013da7cd459402fd77874e09ee3-Abstract-Conference.html) | ICLR 2025 | 6 / 2 | 6 agent tasks; 16 baseline methods; 2 backbones |
| [MetaGPT](https://proceedings.iclr.cc/paper_files/paper/2024/hash/6507b115562bb0a305f1958ccc87355a-Abstract-Conference.html) | ICLR 2024 | 5 / 3 | HumanEval 164; MBPP subset 427; 7 evaluated SoftwareDev tasks from a 70-task inventory |
| [DSPy](https://proceedings.iclr.cc/paper_files/paper/2024/hash/f1cf02ce09757f57c3b93c0db83181e0-Abstract-Conference.html) | ICLR 2024 | 0 / 2 | GSM8K official test 1,319; HotpotQA sample 1,000; separated train/dev samples |
| [Reflexion](https://proceedings.neurips.cc/paper_files/paper/2023/hash/1b44b878bb782e6954cd888628510e90-Abstract-Conference.html) | NeurIPS 2023 | 4 / 3 | 134 ALFWorld tasks; 100 HotpotQA questions; code benchmarks and feedback ablations |
| [WebShop](https://proceedings.neurips.cc/paper_files/paper/2022/hash/82ad13ec01f9fe44c01cb91814fd7b8c-Abstract-Conference.html) | NeurIPS 2022 | 4 / 5 | 1,181,436-product inventory; 12,087 instructions; 3 test trials; 100 real-site transfer instructions |
| [MLAgentBench](https://proceedings.mlr.press/v235/huang24y.html) | ICML 2024 | 6 / 4 | 13 experimentation tasks; 7 backbones; 8 runs per agent |
| [RAG](https://proceedings.neurips.cc/paper_files/paper/2020/hash/6b493230205f780e1bc26945df7481e5-Abstract.html) | NeurIPS 2020 | 3 / 6 | 7 datasets/task settings; 21-million-passage index; human generation assessment |
| [ScienceAgentBench](https://proceedings.iclr.cc/paper_files/paper/2025/hash/f12b4df26344f3be803c06b555252efe-Abstract-Conference.html) | ICLR 2025 | 4 / 3 | 102 tasks from 44 publications; 9 experts; 5 models and 3 frameworks; 3 runs |
| [MLE-bench](https://proceedings.iclr.cc/paper_files/paper/2025/hash/7e3767db483c942b883eb4f8cfb74e31-Abstract-Conference.html) | ICLR 2025 | 5 / 4 | 75 Kaggle competitions; 3 scaffolds; 4 models; most configurations use 3 seeds, with 16/36 for two AIDE configurations |

The JSON preserves reported inventory and evaluated subsets separately, identifies the evidence pages, and leaves unverified fields null. Acceptance is established by the proceedings record; scientific quantities are reported from the PDF, not independently reproduced. The source PDF retained by an official publisher can have a preprint banner, as MetaGPT does; the official proceedings record supplies its acceptance evidence.

## Applying the references to a new project

Use the corpus as an initial set for agent research. For a different research question, retrieve at least 15 accepted papers from the actual target area and compare their full-text evidence. Do not count a workshop, unaccepted preprint, abstract-only result, or inaccessible PDF as a fully inspected accepted peer. Add current papers when a stronger relevant method changes the baseline set.

Build a comparison matrix connecting each proposed claim to task coverage, test units, strong baselines, matched resources, component ablations, sensitivity, independent repetitions, and the necessary analytical visuals. A budget-constrained run can validate implementation or produce preliminary evidence; it does not turn a small evaluated subset into a completed submission-scale study. Retain explicit gaps until the experiments resolve them.

Each visual needs a scientific job and a placement anchor. Place a method overview beside the introduced mechanism, the complete main table beside its primary comparison, and sensitivity, distribution, or ablation plots beside the argument they test. Captions identify data, conditions, aggregation, units, and uncertainty. Substantive claims cite the visual in the adjacent text. A generated conceptual image must not represent fabricated measurements; analytical charts and data tables use observed outputs.

Have independent reviewers check scientific fidelity, quantitative analysis, comparative information value, and rendered layout before selecting a candidate. Preserve their candidate-specific reasons and the selected paragraph anchor. Re-render after insertion and verify assets, first substantive reference, caption, numbering, and cross-references. Matching an accepted paper's figure count or body length does not establish novelty, correctness, or acceptance.

The machine-readable `review_rubric` covers these checks. It also requires accurate best-of-k versus independent-repeat labeling, a distinction between reproduced and literature-reported baseline values, and a concrete unresolved-gap record when an authorized resource limit ends the iteration.
