"""Numerical core: the exact gradient must match finite differences, and the
optimiser must satisfy the constrained-REML first-order conditions without any
constant being added to the covariance."""
from __future__ import annotations

import math

import numpy as np
import pytest

from metrology.variance import (
    _Objective,
    _reml_crit,
    _reml_parts,
    build_design,
    fit_variance_components,
    clean_rows,
)
from metrology.ingest import PRESETS, _normalized_preset, simulate


def _augmented(design_rows, terms, seed=3):
    rows = clean_rows(design_rows)
    return rows, build_design(rows, terms)


def test_gradient_matches_finite_differences_nested():
    rows, _ = simulate(_normalized_preset("nested_clean"), 11)
    rows, design = _augmented(rows, ["lab", "device"])
    obj = _Objective(design)
    theta = np.array([5.0, 0.8])
    x = np.concatenate([theta, [math.log(0.4)]])
    _, grad = obj.fg(x)

    h = 1e-6
    fd = np.zeros_like(grad)
    for i in range(len(x)):
        up, dn = x.copy(), x.copy()
        up[i] += h
        dn[i] -= h
        fd[i] = (obj.fg(up)[0] - obj.fg(dn)[0]) / (2 * h)
    assert np.allclose(grad, fd, rtol=1e-4, atol=1e-4)


def test_gradient_matches_finite_differences_crossed_at_boundary():
    rows, _ = simulate(_normalized_preset("crossed_lab_date"), 7)
    rows, design = _augmented(rows, ["lab", "date", "lab:date"])
    obj = _Objective(design)
    # Include a zero component: the criterion is still differentiable in theta
    # from the right, which is exactly what the KKT check uses.
    x = np.array([2.0, 0.0, 0.3, math.log(0.3)])
    _, grad = obj.fg(x)

    h = 1e-6
    fd = np.zeros_like(grad)
    for i in range(len(x)):
        if i == 1:
            fd[i] = (obj.fg(x + np.array([0, h, 0, 0]))[0] - obj.fg(x)[0]) / h
        else:
            up, dn = x.copy(), x.copy()
            up[i] += h
            dn[i] -= h
            fd[i] = (obj.fg(up)[0] - obj.fg(dn)[0]) / (2 * h)
    assert np.allclose(grad, fd, rtol=1e-4, atol=1e-4)


def test_covariance_is_strictly_positive_definite_by_construction():
    """V is parameterised so it is PD; no jitter may be added to make it so."""
    rows, _ = simulate(_normalized_preset("crossed_lab_date"), 5)
    rows, design = _augmented(rows, ["lab", "date", "lab:date"])
    theta = np.array([1.0, 0.0, 0.0])
    crit, (L, *_rest) = _reml_parts(theta, math.log(0.25), design)
    assert math.isfinite(crit)
    assert np.all(np.diag(L) > 0)


def test_fit_vertices_are_non_negative_and_kkt_valid():
    rows, _ = simulate(_normalized_preset("crossed_lab_date"), 20261125)
    rows = clean_rows(rows)
    terms = ["lab", "date", "lab:date"]
    fit = fit_variance_components(rows, terms)
    for term in fit.terms:
        assert fit.sigma2[term] >= 0.0
    assert fit.residual > 0.0

    # Stationarity test that does not depend on gradient scale: no small
    # feasible step may lower the criterion by more than round-off.
    design = build_design(rows, terms)
    theta = np.array([fit.sigma2[t] for t in terms])
    log_s = float(fit.x[-1])
    base = fit.crit
    tol = 1e-7 * max(1.0, abs(base))
    for k, term in enumerate(terms):
        for direction in (+1, -1):
            delta = direction * 0.01 * max(theta[k], 1e-3)
            trial = theta.copy()
            trial[k] = max(trial[k] + delta, 0.0)
            if np.array_equal(trial, theta):
                continue
            assert _reml_crit(trial, log_s, design) >= base - tol, term
    for direction in (+1, -1):
        assert _reml_crit(theta, log_s + direction * 0.01, design) >= base - tol

    total = fit.total
    for term, d in fit.kkt.items():
        if fit.sigma2[term] <= 1e-12 * max(total, 1e-12):
            # A boundary optimum may not point downhill into the interior.
            assert d >= -1e-4, (term, d)


def test_objective_returns_finite_penalty_on_singular_matrix(monkeypatch):
    """A degenerate trial point must be rejected, not crash the optimiser."""
    rows, _ = simulate(_normalized_preset("nested_clean"), 2)
    rows, design = _augmented(rows, ["lab", "device"])
    obj = _Objective(design)

    real_inv = np.linalg.inv

    def boom(a):
        raise np.linalg.LinAlgError("singular")

    monkeypatch.setattr(np.linalg, "inv", boom)
    crit, grad = obj.fg(np.array([1.0, 0.1, math.log(0.3)]))
    assert crit == 1e12
    assert np.all(grad == 0.0)
    monkeypatch.setattr(np.linalg, "inv", real_inv)


def test_theta_search_box_does_not_change_the_optimum():
    """The search-space bound is far above the data scale, so it is inactive."""
    rows, _ = simulate(_normalized_preset("nested_clean"), 4)
    rows = clean_rows(rows)
    fit = fit_variance_components(rows, ["lab", "device"])
    upper = 1e4 * max(float(np.var(build_design(rows, fit.terms).ybar)), 1e-8)
    for term in fit.terms:
        assert fit.sigma2[term] < 0.5 * upper


def test_reml_crit_matches_parts():
    rows, _ = simulate(_normalized_preset("nested_clean"), 9)
    rows, design = _augmented(rows, ["lab", "device"])
    x = np.array([3.0, 0.5, math.log(0.4)])
    assert _reml_crit(x[:2], x[2], design) == pytest.approx(_reml_parts(x[:2], x[2], design)[0])
