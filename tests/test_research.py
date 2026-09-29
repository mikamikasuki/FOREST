"""Numerical contracts and actual artifact rendering, no network dependency."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy.special import expit, logit

from research.analysis.statistics import analyze, paired_comparison, scores
from research.experiments.case import configuration, split_indices
from research.figures.render import render_figure, revise_style
from research.literature.sources import bibtex, import_bibliography
from research.paper.manuscript import check_paper, compile_paper


def test_prior_correction_inverts_population_weighting():
    natural = np.linspace(0.0001, 0.9999, 300)
    for prevalence in [0.03, 0.12, 0.25, 0.5]:
        ratio = (1 - prevalence) / prevalence
        balanced = ratio * natural / (ratio * natural + 1 - natural)
        restored = expit(logit(balanced) + logit(prevalence))
        np.testing.assert_allclose(restored, natural, atol=1e-13)
        assert np.array_equal(np.argsort(balanced), np.argsort(restored))


def test_scores_independent_formula_and_extreme_probabilities():
    result = scores([0, 1, 1, 0], [0, 1, 0.5, 0.5])
    assert result["brier"] == 0.125
    assert result["log_loss"] == pytest.approx(np.log(2) / 2)
    assert result["auc"] == 0.875
    with pytest.raises(ValueError):
        scores([0, 1], [0.2, float("nan")])
    with pytest.raises(ValueError):
        scores([0, 2], [0.2, 0.8])


def _predictions():
    rows = []
    for seed in [1, 2]:
        for index in range(40):
            y = index % 2
            for method, p in [("baseline", 0.5), ("candidate", 0.8 if y else 0.2)]:
                rows.append({"dataset": "test_fixture", "seed": seed, "sample_id": index, "method": method, "y_true": y, "probability": p})
    return pd.DataFrame(rows)


def test_pairing_clusters_repeated_identity_and_detects_missing_rows():
    frame = _predictions()
    result = paired_comparison(frame, "candidate", "baseline", samples=100)
    assert result["unique_test_objects"] == 40
    assert result["paired_predictions"] == 80
    assert result["difference"] == pytest.approx(-0.21)
    assert result["ci_high"] == pytest.approx(-0.21)
    with pytest.raises(ValueError, match="identical paired"):
        paired_comparison(frame.iloc[1:], "candidate", "baseline", samples=100)


def test_reanalysis_from_predictions_reacts_to_actual_data(tmp_path):
    frame = _predictions()
    source = tmp_path / "predictions.csv"
    frame.to_csv(source, index=False)
    config = {"candidate": "candidate", "primary_baseline": "baseline", "comparison_baselines": ["baseline"], "bootstrap_samples": 100}
    first = analyze(source, tmp_path, config)
    assert first["comparisons"][0]["difference"] == pytest.approx(-0.21)
    frame.loc[frame.method == "candidate", "probability"] = 0.5
    frame.to_csv(source, index=False)
    second = analyze(source, tmp_path, config)
    assert second["comparisons"][0]["difference"] == 0
    assert json.loads((tmp_path / "metrics.json").read_text())["prediction_rows"] == 160


def test_invalid_experiment_configuration():
    with pytest.raises(ValueError):
        configuration({"train_fraction": 0.9, "calibration_fraction": 0.2})
    with pytest.raises(ValueError):
        configuration({"seeds": [7, 7]})


def test_protocol_two_test_set_is_untouched_across_repetitions():
    y = np.tile([0, 0, 0, 1], 250)
    config = configuration()
    splits = [split_indices(y, seed, config) for seed in config["seeds"]]
    fixed_test = set(splits[0]["test"])
    for split in splits:
        train, calibration = set(split["train"]), set(split["calibration"])
        assert set(split["test"]) == fixed_test
        assert not train & calibration
        assert not fixed_test & (train | calibration)
        assert train == set(split["tuning_train"]) | set(split["tuning_validation"])
        assert not set(split["tuning_train"]) & set(split["tuning_validation"])
        assert len(train | calibration | fixed_test) == len(y)


@pytest.mark.parametrize("missing", ["seed", "method", "observation"])
def test_analysis_rejects_missing_declared_experimental_grid(tmp_path, missing):
    frame = _predictions()
    if missing == "seed":
        frame = frame[frame.seed != 2]
    elif missing == "method":
        frame = frame[frame.method != "baseline"]
    else:
        frame = frame.drop(frame.index[-1])
    path = tmp_path / "predictions.csv"
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match="[Ii]ncomplete"):
        analyze(path, tmp_path, {"datasets": ["test_fixture"], "seeds": [1, 2], "expected_methods": ["baseline", "candidate"], "bootstrap_samples": 100})


def test_real_figure_exports_and_style_changes(tmp_path):
    rows = [{"dataset": "observed_fixture", "method": "prior_corrected", "brier": 0.04, "brier_std": 0.001}]
    result = render_figure(tmp_path, rows, {"title": "Numerical test fixture"})
    assert Path(result["pdf"]).read_bytes().startswith(b"%PDF")
    assert Path(result["png"]).read_bytes().startswith(b"\x89PNG")
    original = Path(result["svg"]).read_text()
    changed = revise_style("color #112233 font size 13 width 10")
    render_figure(tmp_path, rows, changed)
    assert Path(result["svg"]).read_text() != original
    assert "#112233" in Path(result["svg"]).read_text()
    assert (tmp_path / "plot.py").is_file()


def test_manuscript_checker_finds_missing_and_stale_bindings(tmp_path):
    (tmp_path / "paper.tex").write_text(r"\cite{absent} \includegraphics{absent.pdf} TODO")
    (tmp_path / "references.bib").write_text("@article{present,title={Example}}")
    (tmp_path / "metrics.json").write_text('{"score": 0.2}')
    (tmp_path / "bindings.json").write_text('[{"macro":"Result", "file":"metrics.json","pointer":"/score", "value":0.1}]')
    codes = {issue["code"] for issue in check_paper(tmp_path)["issues"]}
    assert codes == {"missing_citation", "missing_figure", "placeholder", "stale_metric"}


def test_bibliography_nested_braces_and_ris():
    imported = import_bibliography('@article{k, title={A {nested} title}, author={Smith, Jane}, year={2024}}')
    assert imported[0]["citation_key"] == "k"
    assert "nested" in imported[0]["title"]
    ris = import_bibliography("TY  - JOUR\nTI  - Real metadata\nAU  - Smith, Jane\nER  -", "ris")
    assert ris[0]["title"] == "Real metadata"
    assert "@article" in bibtex(ris)


def test_compile_rejects_path_outside_paper(tmp_path):
    with pytest.raises(ValueError):
        compile_paper(tmp_path, "../outside.tex")
