"""Validation rounds: a simulated new round must reproduce the GLS prediction,
and a bad draw must be skipped rather than crash the request."""
from __future__ import annotations

import math

import numpy as np
import pytest

from metrology import design as D
from metrology.ingest import _normalized_preset, simulate
from metrology.variance import DesignError, clean_rows, fit_variance_components
from metrology.validate import simulate_values, validation_round


@pytest.fixture(scope="module")
def fitted():
    rows, _ = simulate(_normalized_preset("nested_clean"), 20261125)
    rows = clean_rows(rows)
    return rows, fit_variance_components(rows, ["lab", "device"])


def test_simulate_values_reproduces_the_requested_components(fitted):
    rows, fit = fitted
    rng = np.random.default_rng(3)
    sim = simulate_values(rows, fit.terms, fit.sigma2, fit.residual, fit.mu, rng)
    assert [r["value"] for r in sim] != [r["value"] for r in rows]
    assert {(r["lab"], r["device"], r["date"]) for r in sim} == {
        (r["lab"], r["device"], r["date"]) for r in rows
    }
    grand = float(np.mean([r["value"] for r in sim]))
    total_sd = math.sqrt(sum(fit.sigma2.values()) + fit.residual)
    assert grand == pytest.approx(fit.mu, abs=6 * total_sd / math.sqrt(len(sim)))


def test_validation_needs_at_least_one_new_measurement(fitted):
    rows, fit = fitted
    with pytest.raises(DesignError):
        validation_round(rows, fit, D.ScenarioPlan(), n_sim=2)


def test_validation_reports_predicted_and_observed_precision(fitted):
    rows, fit = fitted
    out = validation_round(rows, fit, D.ScenarioPlan(add_reps=1), n_sim=16, seed=11)
    assert out["simulated"] == 16
    assert out["predicted_sd"] > 0
    assert out["observed_sd"] > 0
    assert out["ratio"] == pytest.approx(out["observed_sd"] / out["predicted_sd"])
    n_cells = len({(r["lab"], r["device"], r["date"]) for r in rows})
    assert out["new_measurements"] == n_cells  # one extra per existing cell
    labels = [c["term"] for c in out["components"]]
    assert labels == ["lab", "device", "residual"]
    for c in out["components"]:
        assert 0.0 <= c["coverage"] <= 1.0
        assert 0.0 <= c["boundary_rate"] <= 1.0
        assert math.isfinite(c["mean"])


def test_predicted_sd_matches_a_direct_gls_calculation(fitted):
    rows, fit = fitted
    plan = D.ScenarioPlan(add_labs=1)
    out = validation_round(rows, fit, plan, n_sim=4, seed=5)
    assert out["predicted_sd"] == pytest.approx(math.sqrt(D.scenario_variance(rows, fit, plan)))


def test_validation_example_contains_only_new_rows(fitted):
    rows, fit = fitted
    out = validation_round(rows, fit, D.ScenarioPlan(add_labs=1), n_sim=3, seed=8)
    assert out["example"]
    existing = {r["lab"] for r in rows}
    assert all(r["new"] is True for r in out["example"])
    assert all(r["lab"] not in existing for r in out["example"])


def test_validation_skips_bad_rounds_instead_of_aborting(fitted, monkeypatch):
    rows, fit = fitted
    calls = {"n": 0}
    real = fit_variance_components

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] % 3 == 0:
            raise np.linalg.LinAlgError("singular")
        return real(*a, **k)

    import metrology.validate as VAL

    monkeypatch.setattr(VAL, "fit_variance_components", flaky)
    out = validation_round(rows, fit, D.ScenarioPlan(add_reps=1), n_sim=6, seed=2)
    assert out["simulated"] == 4
    assert out["requested"] == 6
    assert out["warnings"]


def test_validation_raises_when_nothing_fits(fitted, monkeypatch):
    rows, fit = fitted

    def always_fail(*a, **k):
        raise np.linalg.LinAlgError("singular")

    import metrology.validate as VAL

    monkeypatch.setattr(VAL, "fit_variance_components", always_fail)
    with pytest.raises(DesignError):
        validation_round(rows, fit, D.ScenarioPlan(add_reps=1), n_sim=3)


def test_coverage_counts_the_generating_truth_not_the_round_estimate(fitted, monkeypatch):
    """An interval built around the round's own estimate would trivially cover;
    coverage must be measured against the plug-in truth that generated the data."""
    rows, fit = fitted
    import metrology.validate as VAL

    def wide(sim_rows, f2, terms):
        return {t: {"se": 0.0, "lower": 1e-9, "upper": 1e9} for t in list(terms) + ["residual"]}

    monkeypatch.setattr(VAL, "_wald_intervals", wide)
    out = validation_round(rows, fit, D.ScenarioPlan(add_reps=1), n_sim=4, seed=3)
    assert all(c["coverage"] == 1.0 for c in out["components"])

    def narrow(sim_rows, f2, terms):
        # An interval that contains only the *estimate* and not the truth.
        out = {}
        for t in list(terms) + ["residual"]:
            v = float(f2.sigma2[t]) if t != "residual" else float(f2.residual)
            out[t] = {"se": 1e-9, "lower": v, "upper": v}
        return out

    monkeypatch.setattr(VAL, "_wald_intervals", narrow)
    discrete = all(
        abs(fit.sigma2[t]) > 0 for t in fit.terms
    )
    assert discrete  # so the degenerate interval cannot contain the truth
    out2 = validation_round(rows, fit, D.ScenarioPlan(add_reps=1), n_sim=4, seed=3)
    assert all(c["coverage"] <= 0.5 for c in out2["components"])
