"""Explicit class-weight calibration example; never a general research fallback."""
from __future__ import annotations

import json
import io
from pathlib import Path
import re
import shutil
import subprocess
import zipfile
import httpx

from research.figures.render import render_figure, LABELS
from research.paper.manuscript import apply_template

REFERENCES = r"""@inproceedings{menon2013,
 title={On the Statistical Consistency of Algorithms for Binary Classification under Class Imbalance},
 author={Menon, Aditya and Narasimhan, Harikrishna and Agarwal, Shivani and Chawla, Sanjay},
 booktitle={Proceedings of ICML}, year={2013}, pages={603--611},
 url={https://proceedings.mlr.press/v28/menon13a.html}}
@inproceedings{guo2017,
 title={On Calibration of Modern Neural Networks},
 author={Guo, Chuan and Pleiss, Geoff and Sun, Yu and Weinberger, Kilian Q.},
 booktitle={Proceedings of ICML}, year={2017}, pages={1321--1330},
 url={https://proceedings.mlr.press/v70/guo17a.html}}
@inproceedings{loffredo2024,
 title={Restoring balance: principled under/oversampling of data for optimal classification},
 author={Loffredo, Emanuele and Pastore, Mauro and Cocco, Simona and Monasson, Remi},
 booktitle={Proceedings of ICML}, year={2024}, pages={32643--32670},
 url={https://proceedings.mlr.press/v235/loffredo24a.html}}
@misc{liu2026,
 title={The Hidden Cost of Resampling: How Imbalance Correction Degrades Probability Calibration in Tree Ensembles},
 author={Liu, Zewen}, year={2026}, eprint={2606.29720}, archivePrefix={arXiv},
 url={https://arxiv.org/abs/2606.29720}}
@misc{adult,
 title={Adult}, author={Becker, Barry and Kohavi, Ronny}, year={1996},
 publisher={UCI Machine Learning Repository}, doi={10.24432/C5XW20},
 url={https://archive.ics.uci.edu/dataset/2/adult}}
@misc{bank,
 title={Bank Marketing}, author={Moro, Sergio and Rita, Paulo and Cortez, Paulo}, year={2014},
 publisher={UCI Machine Learning Repository}, doi={10.24432/C5K306},
 url={https://archive.ics.uci.edu/dataset/222/bank+marketing}}
"""


def tex(value):
    mapping = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(mapping.get(c, c) for c in str(value))


def _macro_key(*parts):
    # TeX command names contain letters only, including numerical indices.
    digits = dict(zip("0123456789", ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"]))
    value = "".join(str(p).title().replace("_", "") for p in parts)
    return "Result" + "".join(digits.get(c, c) for c in value if c.isalnum())


def generate_example_paper(research_dir, output_dir, title=None, template="article"):
    research = Path(research_dir)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    metrics = json.loads((research / "metrics.json").read_text())
    metadata = json.loads((research / "datasets.json").read_text())
    config = json.loads((research / "config.json").read_text())
    analysis_config = json.loads((research / "analysis_config.json").read_text()) if (research / "analysis_config.json").exists() else config
    if config.get("protocol_version", 1) != 2:
        raise ValueError("This manuscript describes protocol_version 2. Preserve earlier manuscript outputs or regenerate a new protocol 2 experiment.")
    if analysis_config.get("candidate", "prior_corrected") != "prior_corrected":
        raise ValueError("Unsupported manuscript template: this case-study template requires candidate=prior_corrected; generic analysis supports other candidates")
    if any(not any(c["dataset"] == dataset and c["candidate"] == "prior_corrected" and c["baseline"] == "balanced_raw" for c in metrics["comparisons"]) for dataset in metrics["datasets"]):
        raise ValueError("Unsupported manuscript template: every dataset requires the prior_corrected versus balanced_raw comparison")
    training = json.loads((research / "training.json").read_text())
    environment = json.loads((research / "environment.json").read_text())
    lookup = {(r["dataset"], r["method"]): r for r in metrics["summary"]}
    required = {"balanced_raw", "prior_corrected", "sigmoid", "unweighted_lr", "hgb", "intercept_only"}
    for dataset in metrics["datasets"]:
        missing = required - {m for d, m in lookup if d == dataset}
        if missing:
            raise ValueError(f"Manuscript needs completed methods for {dataset}: {sorted(missing)}")
    figures = render_figure(output / "figures" / "comparison", metrics, {"title": "Measured effects of class-weight probability correction"})
    render_figure(output / "figures" / "method", {}, {"title": "Known-weight correction of a fitted balanced classifier", "example": "class_weight_calibration"}, "method")
    import pandas as pd
    predictions = pd.read_csv(research / "predictions.csv")
    first_dataset = metrics["datasets"][0]
    render_figure(output / "figures" / "calibration", predictions[(predictions.dataset == first_dataset) & (predictions.seed == config["seeds"][0])].to_dict("records"), {"title": f"{first_dataset.title()}: held-out reliability, seed {config['seeds'][0]}"}, "calibration")
    bindings, macros = [], []
    for index, row in enumerate(metrics["summary"]):
        for metric in ["brier", "brier_std", "log_loss", "auc", "ece"]:
            key = _macro_key(row["dataset"], row["method"], metric)
            macros.append("\\newcommand{\\" + key + "}{" + f"{row[metric]:.5f}" + "}")
            bindings.append({"macro": key, "file": "metrics.json", "pointer": f"/summary/{index}/{metric}", "value": row[metric], "display": f"{row[metric]:.5f}"})
    for i, comparison in enumerate(metrics["comparisons"]):
        for metric in ["difference", "ci_low", "ci_high", "p_holm"]:
            key = _macro_key(comparison["dataset"], comparison["baseline"], metric)
            macros.append("\\newcommand{\\" + key + "}{" + f"{comparison[metric]:.5f}" + "}")
            bindings.append({"macro": key, "file": "metrics.json", "pointer": f"/comparisons/{i}/{metric}", "value": comparison[metric], "display": f"{comparison[metric]:.5f}"})
    def m(dataset, method, metric="brier"):
        return "\\" + _macro_key(dataset, method, metric) + "{}"
    result_sentences = []
    recovery_sentences = []
    for dataset in metrics["datasets"]:
        row = next(r for r in metrics["comparisons"] if r["dataset"] == dataset and r["baseline"] == "balanced_raw")
        result_sentences.append(f"On {dataset.title()}, Brier score changes from {m(dataset, 'balanced_raw')} to {m(dataset, 'prior_corrected')} (paired difference {m(dataset, 'balanced_raw', 'difference')}, conditional {100*row['confidence']:g}\\% CI [{m(dataset, 'balanced_raw', 'ci_low')}, {m(dataset, 'balanced_raw', 'ci_high')}]).")
        recovery = next(r for r in metrics["mechanism_recovery"] if r["dataset"] == dataset)
        if recovery["fraction_of_sigmoid_gain"] is not None:
            recovery_sentences.append(f"{100*recovery['fraction_of_sigmoid_gain']:.1f}\\% on {dataset.title()}")
        else:
            recovery_sentences.append(f"undefined on {dataset.title()} because sigmoid calibration has no positive Brier gain")
    supported = bool(metrics["mechanism_recovery"]) and all(r["supported_descriptively"] for r in metrics["mechanism_recovery"])
    improves_all = all(lookup[(dataset,"prior_corrected")]["brier"] < lookup[(dataset,"balanced_raw")]["brier"] for dataset in metrics["datasets"])
    if supported and improves_all:
        mechanism_abstract = "The configured tasks meet the descriptive 80\\% recovery criterion; offset and strength ablations quantify the contribution of known prior displacement."
        measured_conclusion = "The configured tasks support the descriptive recovery criterion: the known offset captures most of sigmoid calibration's score repair without additional calibration labels."
    else:
        mechanism_abstract = "The configured tasks do not uniformly meet the descriptive 80\\% recovery criterion. Offset and strength ablations separate known prior displacement from the residual fitted-model error."
        measured_conclusion = "The configured tasks do not uniformly support the recovery criterion. The offset controls quantify the repair explained by known weights, and the learned-calibration controls quantify the remaining correction."
    table_rows = []
    for dataset in metrics["datasets"]:
        for method in ["balanced_raw", "prior_corrected", "intercept_only", "sigmoid", "unweighted_lr", "hgb"]:
            table_rows.append(f"{dataset.title()} & {tex(LABELS.get(method, method))} & {m(dataset, method)} $\\pm$ {m(dataset, method, 'brier_std')} & {m(dataset, method, 'log_loss')} & {m(dataset, method, 'auc')} \\\\")
        table_rows.append(r"\midrule")
    dataset_rows = [f"{name.title()} & {info['rows']:,} & {info['features']} & {info['prevalence']:.4f} & {info['exact_duplicates_removed']} \\\\" for name, info in metadata.items()]
    ablation_rows = []
    for dataset in metrics["datasets"]:
        for method in metrics["methods"]:
            if method.startswith(("prior_alpha", "sigmoid_n")):
                ablation_rows.append(f"{dataset.title()} & {tex(method)} & {m(dataset, method)} & {m(dataset, method, 'ece')} \\\\")
    runtime_rows = []
    for method in ["unweighted_lr", "balanced_raw", "prior_corrected", "sigmoid", "intercept_only", "hgb"]:
        seconds = [t["fit_seconds"][method] for t in training]
        runtime_rows.append(f"{tex(LABELS.get(method, method))} & {sum(seconds)/len(seconds):.3f} & {min(seconds):.3f}--{max(seconds):.3f} \\\\")
    paired_rows = []
    for row in metrics["comparisons"]:
        d, b = row["dataset"], row["baseline"]
        paired_rows.append(f"{d.title()} & {tex(LABELS.get(b,b))} & {m(d,b,'difference')} & [{m(d,b,'ci_low')}, {m(d,b,'ci_high')}] & {m(d,b,'p_holm')} \\\\")
    scope_sentences = []
    for dataset in metrics["datasets"]:
        scope_sentences.append(f"For {dataset.title()}, unweighted logistic regression attains {m(dataset, 'unweighted_lr')} and gradient boosting attains {m(dataset, 'hgb')}, compared with {m(dataset, 'prior_corrected')} for correction.")
    unweighted_differences = [lookup[(d,"prior_corrected")]["brier"]-lookup[(d,"unweighted_lr")]["brier"] for d in metrics["datasets"]]
    if all(value >= 0 for value in unweighted_differences):
        alternative_result = "The unweighted logistic reference has no higher mean Brier score on every configured task. The correction's measured use case is repair of an already required weighted classifier."
    elif all(value <= 0 for value in unweighted_differences):
        alternative_result = "Correction has no higher observed mean Brier score than the unweighted reference on every configured task; the paired intervals quantify the confidence and magnitude of that comparison."
    else:
        alternative_result = "The ordering of correction and unweighted logistic regression varies across the configured tasks; the paired comparisons retain that task-specific result."
    source = r"""\documentclass[11pt]{article}
\usepackage[margin=1in]{geometry}
\usepackage{amsmath,amssymb,booktabs,graphicx,hyperref,natbib,microtype}
\usepackage[section]{placeins}
\hypersetup{colorlinks=true,linkcolor=black,citecolor=black,urlcolor=blue}
\setlength{\parskip}{0.35em}
\input{results_macros.tex}
\title{@@TITLE@@}
\author{FOREST Research Case Study\\Reproducible empirical draft}
\date{\today}
\begin{document}
\maketitle
\begin{abstract}
Probabilities from class-balanced training can encode a modified class prior, distorting decisions that require natural-prevalence risks. We evaluate a known analytic log-odds correction as a repair of an already fitted balanced logistic classifier that requires no additional calibration labels. On @@DATASETS@@, using @@SEEDS@@ training/calibration partitions and one untouched test set, the measured recovery of sigmoid calibration's Brier-score gain is @@RECOVERY@@. @@RESULTS@@ @@MECHANISM_ABSTRACT@@ Unweighted and nonlinear references define the performance available when the original classifier can be replaced.
\end{abstract}

\section{Introduction}
Class weights change which errors a classifier prioritizes. A probability consumed downstream, however, must describe the event prevalence relevant to that decision. Treating a class-balanced posterior as a natural-prevalence posterior conflates these two objectives. The distinction matters even when the classifier's ordering of examples is useful.

Weighted risk minimization and threshold adjustment already have established theoretical connections \citep{menon2013}. The unresolved practical quantity in this case study is how much of the calibration repair needs new labels once the weights are known. We test a falsifiable criterion: analytic correction should recover at least 80\% of the held-out sigmoid calibrator's Brier improvement over an uncorrected balanced logistic model. This mechanism replication quantifies the calibration-label requirement through explicit controls.

The study contributes a controlled decomposition on public tasks. A shared fitted model isolates the effect of the offset; a learned intercept and learned slope test residual misspecification; limited-label calibrators expose estimation variance; and unweighted logistic regression plus gradient boosting establish alternatives. Every reported value is generated from saved test probabilities.

\section{Related work and comparison frame}
\citet{menon2013} analyze class-imbalance algorithms under balanced performance criteria, establishing that the evaluation objective determines the appropriate weighting or threshold. Post-hoc calibration separates model fitting from a probability transformation; \citet{guo2017} provide a prominent neural-network example. \citet{loffredo2024} study principled sampling for classification. Those objectives and model families differ from the present controlled logistic study.

The recent resampling study of \citet{liu2026} reports calibration damage in tree ensembles and examines prior-shift correction. Its public abstract already describes the distinction between prior changes and synthetic changes to class-conditional distributions. Our comparison fixes the fitted weighted logistic model and measures whether analytic information substitutes for calibration labels. The preprint comparison uses the reported claims in its public abstract.

\section{Problem and correction}
Let $Y\in\{0,1\}$ and $p(x)=P(Y=1\mid X=x)$. Training minimizes weighted binary log loss with positive weights $w_1,w_0$:
\begin{equation}
\mathcal L(q;x)=-w_1p(x)\log q-w_0(1-p(x))\log(1-q).
\end{equation}
Differentiating in $q$ and setting the derivative to zero gives
\begin{equation}
q^*(x)=\frac{w_1p(x)}{w_1p(x)+w_0(1-p(x))},\qquad
\operatorname{logit}p(x)=\operatorname{logit}q^*(x)-\log\frac{w_1}{w_0}.
\end{equation}
For inverse-frequency balancing and training prevalence $\pi$, $w_1/w_0=(1-\pi)/\pi$. The implemented transformation is
\begin{equation}
\hat p_\alpha(x)=\sigma\left(z(x)+\alpha\log\frac{\pi}{1-\pi}\right),\quad \alpha=1,
\end{equation}
where $z$ is the fitted balanced model's decision function. This identity is exact for the pointwise population optimum. In a regularized finite-dimensional logistic model, weighting can also change slopes; the experiments measure that residual rather than assuming the identity fully describes the fitted model. The transformation is strictly increasing and thus preserves ranking and ROC AUC up to numerical ties. It changes thresholded predictions when the numerical threshold is held fixed.

\section{Experimental design}
\paragraph{Data and leakage controls.} We use the full public Adult and Bank Marketing sources where configured \citep{adult,bank}. Exact duplicate records are removed before splitting. Adult's original train and test files are combined before creating a new fixed test set; these are not scores on the original fixed UCI split. Bank's call-duration feature is excluded because it is unavailable before a call concludes. Missing numerical values use training medians, categorical values use training modes, categorical levels are one-hot encoded, and numerical features are standardized. Every fitted transformation sees training rows only; inner-validation preprocessing is fitted exclusively on the inner-training partition.
\begin{table}[ht]
\centering\caption{Prepared data. The positive-class prevalence remains natural in evaluation.}
\begin{tabular}{lrrrr}\toprule
Dataset & Rows & Features & Prevalence & Duplicates removed\\\midrule
@@DATASET_ROWS@@
\bottomrule\end{tabular}
\end{table}

\paragraph{Shared splits and baselines.} Protocol version 2 reserves one stratified test set using seed @@FIXED_TEST_SEED@@. Every repetition retains that same untouched test set and partitions the remaining observations into @@TRAIN@@\% training and @@CAL@@\% calibration relative to the full dataset. No test identity appears in any repetition's training or calibration data. The logistic regularization parameter is selected from $C\in\{@@CGRID@@\}$ using a stratified @@TUNING_PERCENT@@\% inner validation partition drawn only from that repetition's training data. After selection, both logistic models are refitted on their full training partition with the same selected $C$ and preprocessing. Calibration labels are reserved exclusively for fitting calibrators. Analytic correction requires training labels to fit its base classifier, but no additional calibration labels; the calibration-budget curves count labels used by the correction stage.

The nonlinear reference is histogram gradient boosting with @@HGB@@ iterations, learning rate 0.08, 31 leaves, and L2 regularization 1. This fixed reference measures the probability-quality headroom available from nonlinear modeling. The sigmoid baseline learns both slope and intercept on the calibration partition, whereas the intercept-only baseline learns one offset. The correction-strength ablation changes $\alpha$ while preserving fitted weights and test observations. Calibration-label subsets are stratified within the same calibration partition.

\paragraph{Outcomes and uncertainty.} Brier score is the primary proper scoring rule; log loss and ROC AUC distinguish probability quality from ordering. Equal-width @@ECEBINS@@-bin expected calibration error is descriptive because binning choices affect it. Values are means across @@SEEDS@@ seeds, with descriptive seed standard deviations. Paired score differences resample unique test-object IDs as clusters, retaining repeated predictions of the same object together, using @@BOOTSTRAP@@ draws. The intervals condition on the fitted models; shared training sets are not independent model replications. All displayed paired tests receive Holm adjustment. A Brier change of @@MEANINGFUL@@ is the predefined practically meaningful threshold for this case study.

\section{Results}
\paragraph{Measuring the contribution of known weights.} @@RESULTS@@ The recovery fractions are @@RECOVERY@@. These descriptive ratios test the stated 80\% criterion, while score-difference intervals quantify the paired prediction effect. The correction uses the already known class weights and adds no calibration fit.

\begin{table}[ht]
\centering\small
\caption{All principal methods on the same held-out observations. Lower Brier/log loss and higher AUC are better. The nonlinear reference exposes model-class headroom.}
\begin{tabular}{llrrr}\toprule
Dataset & Method & Brier $\pm$ seed SD & Log loss & AUC\\\midrule
@@TABLE_ROWS@@
\bottomrule\end{tabular}
\end{table}
\begin{figure}[ht]\centering
\includegraphics[width=\linewidth]{figures/comparison/figure.pdf}
\caption{Probability-quality comparison. Bars represent means and whiskers show seed SD, not confidence intervals.}
\end{figure}

\paragraph{The mechanism is an offset, not better ranking.} The raw and analytically corrected models share all learned parameters. Equation (3) predicts unchanged ranking; the saved AUC values test that consequence directly. The learned-intercept baseline measures whether the empirical optimum differs from the known prior offset. Full sigmoid calibration additionally estimates slope. Correction-strength results and restricted calibration budgets appear in Appendix A; they retain every configured ablation.

\paragraph{Alternatives define the use case.} @@SCOPE@@ @@ALTERNATIVE_RESULT@@ The additional-label count describes the probability-repair stage. Choosing a predictive model also requires the absolute score comparisons retained in the table.

\begin{figure}[ht]\centering
\includegraphics[width=0.85\linewidth]{figures/calibration/figure.pdf}
\caption{One explicitly identified held-out split. Each point summarizes an occupied probability bin; the diagonal indicates agreement between predicted and observed frequencies. This diagnostic localizes probability-bin deviations alongside the proper-score comparison.}
\end{figure}

\section{Scope, costs, and reproducibility}
The evidence covers the configured public tabular tasks, repeats, and stated feature set. Adult and Bank observations encode historical social and marketing contexts; aggregate predictive scores do not establish suitability for consequential decisions. The evaluation concerns aggregate probability scores; it does not measure protected-group fairness or future distribution shift. Unknown target prevalence, changes to class-conditional distributions, and substantial slope misspecification require a different calibration analysis.

Training and inference run on CPU. The source package records dataset URLs and licenses, split indices, all per-observation probabilities, model configuration, package versions, and actual wall-clock timings. Analysis recomputes Brier and log loss directly from the prediction CSV using independent NumPy expressions. Rerunning analysis changes no trained model; a full training rerun is a separate reproducibility test. Timing excludes network download and records the observed host workload.

\section{Conclusion}
Known class weights provide a direct population-level relation between a balanced posterior and natural-prevalence probabilities. @@MEASURED_CONCLUSION@@ The learned-intercept, sigmoid, unweighted, and nonlinear controls make the resulting decision explicit: select the repair using its measured probability scores and additional-label requirement, and compare available model replacements separately.

\section*{Data, code, and AI-use statement}
Both datasets are public UCI resources distributed under CC BY 4.0. No new human-participant data were collected. This draft, implementation, experiment execution, and figure generation were produced with AI assistance through FOREST/Codex. The numerical results come from executed Python experiments. Document status: reproducible empirical draft.
\bibliographystyle{plainnat}
\bibliography{references}

\appendix
\section{Mechanism and calibration-budget ablations}
The ablations have distinct argumentative duties. Half and one-and-a-half strength perturb the same known log-odds correction. Sigmoid calibration with small held-out subsets measures whether estimating two calibration parameters is stable when labels are restricted. No ablation is selected for inclusion using its score.
\begin{table}[ht]\centering\small
\begin{tabular}{llrr}\toprule Dataset & Variant & Brier & ECE\\\midrule
@@ABLATION_ROWS@@
\bottomrule\end{tabular}\caption{All configured mechanism and label-budget variants.}\end{table}
\section{Measured computation}
\begin{table}[ht]\centering
\begin{tabular}{lrr}\toprule Method & Mean seconds & Min--max seconds\\\midrule
@@RUNTIME_ROWS@@
\bottomrule\end{tabular}\caption{Wall-clock fitting costs across dataset/seed runs. Every logistic-based method includes the same shared internal $C$-selection cost plus its base fit; learned calibrators also include their calibration fit. The analytic transform requires no additional fitting. Internal tuning includes its preprocessing; final full-training preprocessing and prediction are excluded from per-method fit times.}\end{table}
The execution used Python @@PYTHON@@ and scikit-learn @@SKLEARN@@, with @@THREADS@@ computational threads. Maximum logistic iterations were @@MAXITER@@. Seeds: @@SEEDLIST@@. The full configuration is supplied as \texttt{config.json}; individual convergence indicators and learned offsets/slopes are in \texttt{training.json}.
\section{Recomputable statistical contract}
\begin{table}[ht]\centering\scriptsize
\begin{tabular}{llrrr}\toprule Dataset & Comparator & Difference & Conditional interval & Holm $p$\\\midrule
@@PAIRED_ROWS@@
\bottomrule\end{tabular}\caption{Analytic correction minus comparator Brier score. Negative values favor analytic correction. Intervals are percentile bootstrap intervals conditional on fitted models; the bootstrap $p$ values use centered, two-sided resampling.}\end{table}
Each prediction row contains dataset, split seed, source-row identity, method, true binary outcome, and predicted probability. Pairing requires identical identities and labels; a missing method row or duplicated observation raises an error. The bootstrap resamples within-dataset object clusters and recomputes mean paired losses, keeping repeated evaluations together. Reported intervals condition on these fitted models; \texttt{comparisons.csv} reports seed ranges. Exact saved probabilities, excluding timings, define deterministic reproduction.
\end{document}
"""
    replacements = {"TITLE": tex(title or "Repairing Class-Weighted Probabilities Without Calibration Labels"), "DATASETS": " and ".join(d.title() for d in metrics["datasets"]), "SEEDS": str(len(config["seeds"])), "RECOVERY": " and ".join(recovery_sentences), "RESULTS": " ".join(result_sentences), "DATASET_ROWS": "\n".join(dataset_rows), "TRAIN": f"{100*config['train_fraction']:g}", "CAL": f"{100*config['calibration_fraction']:g}", "CGRID": ",".join(str(c) for c in config["c_grid"]), "HGB": str(config["hgb_iterations"]), "BOOTSTRAP": str(config["bootstrap_samples"]), "MEANINGFUL": str(config["meaningful_brier_gain"]), "TABLE_ROWS": "\n".join(table_rows[:-1]), "SCOPE": " ".join(scope_sentences), "ABLATION_ROWS": "\n".join(ablation_rows), "RUNTIME_ROWS": "\n".join(runtime_rows), "PYTHON": tex(environment["python"]), "SKLEARN": tex(environment["scikit_learn"]), "THREADS": str(config["n_threads"]), "MAXITER": str(config["max_iter"]), "SEEDLIST": ", ".join(str(s) for s in config["seeds"])}
    replacements["BOOTSTRAP"] = str(analysis_config.get("bootstrap_samples", 1000))
    replacements.update({"FIXED_TEST_SEED":str(config["fixed_test_seed"]), "TUNING_PERCENT":f"{100*config['tuning_fraction']:g}", "MECHANISM_ABSTRACT":mechanism_abstract, "MEASURED_CONCLUSION":measured_conclusion, "ALTERNATIVE_RESULT":alternative_result})
    for key, value in replacements.items():
        source = source.replace("@@" + key + "@@", value)
    source = source.replace("@@ECEBINS@@", str(analysis_config.get("ece_bins", 10))).replace("@@PAIRED_ROWS@@", "\n".join(paired_rows))
    (output / "paper.tex").write_text(source)
    (output / "results_macros.tex").write_text("% Generated from metrics.json; regenerate after changing results.\n" + "\n".join(macros) + "\n")
    (output / "references.bib").write_text(REFERENCES)
    (output / "bindings.json").write_text(json.dumps(bindings, indent=2))
    for filename in ["metrics.json", "config.json", "datasets.json", "training.json", "environment.json", "metrics_per_seed.csv", "metrics_summary.csv", "comparisons.csv", "design.json"]:
        shutil.copyfile(research / filename, output / filename)
    template_info = apply_template(output, template)
    return {"source": str(output / "paper.tex"), "bibtex": str(output / "references.bib"), "bindings": bindings, "figures": figures, "template": template_info, "status": "draft_generated"}
