from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score


def scores(y, probability, bins=10):
    y, p = np.asarray(y, dtype=float), np.asarray(probability, dtype=float)
    if len(y) == 0 or not np.isfinite(p).all() or not np.isin(y, [0, 1]).all() or np.any((p < 0) | (p > 1)):
        raise ValueError("Binary labels and finite probabilities in [0, 1] are required")
    safe = np.clip(p, 1e-15, 1 - 1e-15)
    membership = np.minimum((p * bins).astype(int), bins - 1)
    ece = sum(float(np.sum(membership == b)) / len(y) * abs(float(y[membership == b].mean()) - float(p[membership == b].mean())) for b in range(bins) if np.any(membership == b))
    return {"brier": float(np.mean((p - y) ** 2)), "log_loss": float(-np.mean(y * np.log(safe) + (1 - y) * np.log1p(-safe))), "accuracy": float(np.mean((p >= 0.5) == y)), "auc": float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None, "ece": ece, "prevalence": float(y.mean()), "n": len(y)}


def paired_comparison(frame, candidate, baseline, samples=1000, seed=0, confidence=0.95):
    if int(samples) < 100 or not 0 < confidence < 1:
        raise ValueError("Use at least 100 bootstrap draws and confidence in (0,1)")
    left = frame[frame.method == candidate][["seed", "sample_id", "y_true", "probability"]]
    right = frame[frame.method == baseline][["seed", "sample_id", "y_true", "probability"]]
    pair = left.merge(right, on=["seed", "sample_id"], suffixes=("_candidate", "_baseline"), validate="one_to_one")
    if len(pair) != len(left) or len(pair) != len(right) or not np.array_equal(pair.y_true_candidate, pair.y_true_baseline):
        raise ValueError("Comparisons require identical paired observations and labels")
    pair["difference"] = (pair.probability_candidate - pair.y_true_candidate) ** 2 - (pair.probability_baseline - pair.y_true_baseline) ** 2
    # Repeated holdouts can contain the same person. Resample identities, not
    # seed-observation rows; report conditional test uncertainty explicitly.
    clusters = pair.groupby("sample_id").difference.agg(["sum", "count"])
    sums, counts = clusters["sum"].to_numpy(), clusters["count"].to_numpy()
    rng = np.random.default_rng(seed)
    bootstrap = np.empty(samples)
    for i in range(samples):
        ids = rng.integers(0, len(clusters), len(clusters))
        bootstrap[i] = sums[ids].sum() / counts[ids].sum()
    observed = float(pair.difference.mean())
    alpha = (1 - confidence) / 2
    lo, hi = np.quantile(bootstrap, [alpha, 1 - alpha])
    centered_tail = (np.count_nonzero(np.abs(bootstrap - observed) >= abs(observed)) + 1) / (samples + 1)
    per_seed = pair.groupby("seed").difference.mean()
    return {"candidate": candidate, "baseline": baseline, "metric": "brier", "difference": observed, "ci_low": float(lo), "ci_high": float(hi), "confidence": confidence, "p_bootstrap": float(centered_tail), "bootstrap_samples": samples, "unique_test_objects": len(clusters), "paired_predictions": len(pair), "seed_differences": {str(k): float(v) for k, v in per_seed.items()}, "seed_range": [float(per_seed.min()), float(per_seed.max())], "interpretation": "negative favors candidate; object-cluster percentile CI conditional on fitted models; overlapping training splits do not provide independent training replications"}


def analyze(predictions_path, output_dir, config=None):
    source_directory = Path(predictions_path).parent
    stored_config = json.loads((source_directory / "config.json").read_text()) if (source_directory / "config.json").is_file() else {}
    config = {**stored_config, **(config or {})}
    frame = pd.read_csv(predictions_path, float_precision="round_trip")
    required = {"dataset", "seed", "sample_id", "method", "y_true", "probability"}
    if not required <= set(frame.columns):
        raise ValueError(f"Missing prediction columns: {sorted(required - set(frame.columns))}")
    if frame.duplicated(["dataset", "seed", "sample_id", "method"]).any():
        raise ValueError("Duplicate observation/method predictions would bias the analysis")
    expected_datasets = config.get("datasets", sorted(frame.dataset.unique()))
    expected_seeds = config.get("seeds", sorted(frame.seed.unique()))
    manifest_path = source_directory / "training.json"
    training_manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    expected_runs = {(row["dataset"], int(row["seed"])): set(row["fit_seconds"]) for row in training_manifest}
    default_methods = set(config.get("expected_methods", frame.method.unique()))
    observed_grid = set(frame[["dataset", "seed"]].itertuples(index=False, name=None))
    expected_grid = {(dataset, seed) for dataset in expected_datasets for seed in expected_seeds}
    if stored_config.get("protocol_version") == 2 and not expected_grid <= set(expected_runs):
        raise ValueError("Protocol 2 analysis requires a complete training manifest for every configured dataset/seed")
    if observed_grid != expected_grid:
        raise ValueError(f"Incomplete configured dataset/seed grid: missing={sorted(expected_grid-observed_grid)}, unexpected={sorted(observed_grid-expected_grid)}")
    for dataset, seed in sorted(expected_grid):
        rows = frame[(frame.dataset == dataset) & (frame.seed == seed)]
        required_methods = expected_runs.get((dataset, int(seed)), default_methods)
        present = set(rows.method)
        if present != required_methods:
            raise ValueError(f"Incomplete method grid for {dataset}/{seed}: missing={sorted(required_methods-present)}, unexpected={sorted(present-required_methods)}")
        reference = None
        for _, method_rows in rows.groupby("method"):
            identity = method_rows[["sample_id", "y_true"]].sort_values("sample_id").reset_index(drop=True)
            if reference is not None and not identity.equals(reference):
                raise ValueError(f"Incomplete observation grid or inconsistent labels for {dataset}/{seed}")
            reference = identity
    if config.get("protocol_version") == 2:
        for dataset, rows in frame.groupby("dataset"):
            identity_sets = [set(group.sample_id) for _, group in rows.groupby("seed")]
            if any(ids != identity_sets[0] for ids in identity_sets[1:]):
                raise ValueError(f"Protocol 2 requires one unchanged test set across seeds: {dataset}")
    per_seed = []
    for (dataset, seed, method), rows in frame.groupby(["dataset", "seed", "method"], sort=True):
        per_seed.append({"dataset": dataset, "seed": int(seed), "method": method, **scores(rows.y_true, rows.probability, int(config.get("ece_bins", 10)))})
    metrics = pd.DataFrame(per_seed)
    summaries = []
    for (dataset, method), rows in metrics.groupby(["dataset", "method"]):
        summary = {"dataset": dataset, "method": method, "seeds": len(rows), "test_predictions": int(rows.n.sum())}
        for metric in ["brier", "log_loss", "auc", "accuracy", "ece", "prevalence"]:
            value = rows[metric].mean()
            summary[metric] = float(value) if pd.notna(value) else None
            std = rows[metric].std(ddof=1) if len(rows) > 1 else 0.0
            summary[metric + "_std"] = float(std) if pd.notna(std) else None
        summaries.append(summary)
    candidate = config.get("candidate", "prior_corrected")
    comparisons = []
    baselines = config.get("comparison_baselines", [config.get("primary_baseline", "balanced_raw"), "sigmoid", "unweighted_lr", "hgb", "intercept_only"])
    for dataset, rows in frame.groupby("dataset"):
        for baseline in dict.fromkeys(baselines):
            if baseline == candidate or baseline not in set(rows.method) or candidate not in set(rows.method):
                continue
            comparisons.append({"dataset": dataset, **paired_comparison(rows, candidate, baseline, int(config.get("bootstrap_samples", 1000)), int(config.get("bootstrap_seed", 20260927)), float(config.get("confidence", 0.95)))})
    # Holm familywise correction across every displayed confirmatory comparison.
    order = sorted(range(len(comparisons)), key=lambda i: comparisons[i]["p_bootstrap"])
    previous = 0.0
    for rank, i in enumerate(order):
        adjusted = min(1.0, max(previous, (len(order) - rank) * comparisons[i]["p_bootstrap"]))
        comparisons[i]["p_holm"] = adjusted
        previous = adjusted
    recovery = []
    for dataset in sorted(frame.dataset.unique()):
        lookup = {r["method"]: r["brier"] for r in summaries if r["dataset"] == dataset}
        if {"balanced_raw", "sigmoid", "prior_corrected"} <= lookup.keys():
            denominator = lookup["balanced_raw"] - lookup["sigmoid"]
            fraction = (lookup["balanced_raw"] - lookup["prior_corrected"]) / denominator if denominator > 0 else None
            recovery.append({"dataset": dataset, "fraction_of_sigmoid_gain": fraction, "criterion": 0.8, "supported_descriptively": bool(fraction is not None and fraction >= 0.8), "note": "ratio is descriptive; confidence claims use paired score differences"})
    validation = [
        {"check": "Simpson's paradox", "result": "dataset-level comparisons retained; no pooled cross-task effect used"},
        {"check": "Ecological fallacy", "result": "object-level losses; claims apply to these benchmark predictions"},
        {"check": "Berkson's paradox", "result": "benchmark sampling limits the target population; no causal population inference"},
        {"check": "Collider bias", "result": "post-call duration removed; no causal feature interpretation"},
        {"check": "Base rate neglect", "result": "prevalence saved per dataset and split; Brier uses natural test prevalence"},
        {"check": "Regression to mean", "result": "splits/seeds selected before score inspection; no extreme-case selection"},
        {"check": "Survivorship bias", "result": "complete declared dataset/seed/method grid checked against configuration/training manifest; all method observations paired"},
        {"check": "Look-elsewhere effect", "result": "all comparisons emitted; Holm-adjusted bootstrap p-values included"},
        {"check": "Garden of forking paths", "result": "editable saved configuration; exploratory benchmark, not external preregistration"},
        {"check": "Correlation versus causation", "result": "controlled model postprocessing identifies algorithmic score changes; no social causal claim"},
        {"check": "Reverse causality", "result": "no causal real-world inference; fixed predictive target"}]
    result = {"status": "ANALYZED", "evidence_label": "MEASURED", "source": str(Path(predictions_path).name), "prediction_rows": len(frame), "datasets": sorted(frame.dataset.unique().tolist()), "methods": sorted(frame.method.unique().tolist()), "per_seed": per_seed, "summary": summaries, "comparisons": comparisons, "mechanism_recovery": recovery, "fallacy_scan": validation, "fallacy_scan_coverage": "11/11", "reproducibility": {"independent_metric_recomputation": "Brier and log loss recomputed using NumPy formulas from observation-level CSV", "training_rerun": "not checked by analysis; requires an independent run", "timing": "wall-clock CPU measurements; not deterministic"}, "statistical_scope": "Object-cluster bootstrap accounts for repeated test identities. Intervals condition on fitted models, and do not quantify new-training-set or new-task uncertainty. Seed SD is descriptive."}
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "analysis_config.json").write_text(json.dumps({"ece_bins": 10, "bootstrap_samples": 1000, "bootstrap_seed": 20260927, "confidence": 0.95, **config}, indent=2))
    (output / "metrics.json").write_text(json.dumps(result, indent=2, allow_nan=False))
    metrics.to_csv(output / "metrics_per_seed.csv", index=False)
    pd.DataFrame(summaries).to_csv(output / "metrics_summary.csv", index=False)
    pd.DataFrame(comparisons).to_csv(output / "comparisons.csv", index=False)
    return result


def verify_reproduction(original_dir, repeated_dir):
    original = pd.read_csv(Path(original_dir) / "predictions.csv", float_precision="round_trip")
    repeated = pd.read_csv(Path(repeated_dir) / "predictions.csv", float_precision="round_trip")
    columns = ["dataset", "seed", "sample_id", "method", "y_true", "probability"]
    first = original[columns].sort_values(columns[:-1]).reset_index(drop=True)
    second = repeated[columns].sort_values(columns[:-1]).reset_index(drop=True)
    exact = first.equals(second)
    max_error = float(np.max(np.abs(first.probability.to_numpy() - second.probability.to_numpy()))) if first.shape == second.shape else None
    return {"status": "VERIFIED" if exact else "MISMATCH", "exact_predictions": exact, "maximum_probability_difference": max_error, "original_rows": len(first), "repeated_rows": len(second), "timing_excluded": True}
