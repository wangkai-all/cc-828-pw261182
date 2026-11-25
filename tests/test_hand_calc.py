"""Balanced-design hand calculation must agree with REML on the reference layouts.

For a balanced layout the ANOVA moment estimates and the REML estimates are not
algebraically identical in general, but they agree to sampling-level precision
here because the layouts are balanced and the components are interior.  The
test asserts both the closed-form EMS arithmetic and the agreement with REML;
it also checks that a boundary component is reported as zero by REML while the
hand calculation is allowed to go slightly negative.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from metrology.ingest import _normalized_preset, simulate
from metrology.variance import balanced_anova, clean_rows, fit_variance_components


def test_nested_anova_ems_arithmetic_is_self_consistent():
    rows, meta = simulate(_normalized_preset("nested_clean"), 20261125)
    rows = clean_rows(rows)
    hand = balanced_anova(rows, ["lab", "device"])
    assert hand["balanced"] and hand["layout"] == "nested"

    table = {r["source"]: r for r in hand["anova"]}
    n = hand["n_reps"]
    labs, devs = 4, 2
    ms_lab = table["实验室"]["ms"]
    ms_dev = table["设备(实验室)"]["ms"]
    ms_e = table["重复测量"]["ms"]
    est = hand["estimates"]
    assert est["residual"] == pytest.approx(ms_e)
    assert est["device"] == pytest.approx((ms_dev - ms_e) / n)
    assert est["lab"] == pytest.approx((ms_lab - ms_dev) / (n * devs))
    # Degrees of freedom add up to N - 1 for the cell means plus the residual.
    assert table["实验室"]["df"] == labs - 1
    assert table["设备(实验室)"]["df"] == labs * (devs - 1)
    total_df = sum(r["df"] for r in hand["anova"])
    assert total_df == len(rows) - 1
    # Means of squares are SS/df, by construction.
    for r in hand["anova"]:
        assert r["ms"] == pytest.approx(r["ss"] / r["df"])


def test_nested_reml_agrees_with_hand_calculation():
    rows, _ = simulate(_normalized_preset("nested_clean"), 20261125)
    rows = clean_rows(rows)
    hand = balanced_anova(rows, ["lab", "device"])
    fit = fit_variance_components(rows, ["lab", "device"])
    for term in ("lab", "device"):
        assert fit.sigma2[term] == pytest.approx(hand["estimates"][term], rel=2e-3, abs=1e-4)
    assert fit.residual == pytest.approx(hand["estimates"]["residual"], rel=2e-3, abs=1e-4)


def test_crossed_anova_ems_arithmetic():
    rows, _ = simulate(_normalized_preset("crossed_lab_date"), 20261125)
    rows = clean_rows(rows)
    hand = balanced_anova(rows, ["lab", "date", "lab:date"])
    assert hand["balanced"] and hand["layout"] == "crossed"

    table = {r["source"]: r for r in hand["anova"]}
    n_eff = hand["n_reps"]  # observations per lab x date
    assert n_eff == 3
    a, b = 6, 4
    ms_lab = table["实验室"]["ms"]
    ms_date = table["日期"]["ms"]
    ms_int = table["实验室×日期"]["ms"]
    ms_e = table["重复测量"]["ms"]
    est = hand["estimates"]
    assert est["residual"] == pytest.approx(ms_e)
    assert est["lab:date"] == pytest.approx((ms_int - ms_e) / n_eff)
    assert est["lab"] == pytest.approx((ms_lab - ms_int) / (n_eff * b))
    assert est["date"] == pytest.approx((ms_date - ms_int) / (n_eff * a))


def test_crossed_reml_agrees_with_hand_calculation():
    rows, _ = simulate(_normalized_preset("crossed_lab_date"), 20261125)
    rows = clean_rows(rows)
    hand = balanced_anova(rows, ["lab", "date", "lab:date"])
    fit = fit_variance_components(rows, ["lab", "date", "lab:date"])
    for term in ("lab", "date", "lab:date"):
        assert fit.sigma2[term] == pytest.approx(hand["estimates"][term], rel=5e-3, abs=1e-4)
    assert fit.residual == pytest.approx(hand["estimates"]["residual"], rel=5e-3, abs=1e-4)


def test_boundary_component_stops_at_zero_while_anova_may_go_negative():
    rows, _ = simulate(_normalized_preset("boundary_zero"), 20261125)
    rows = clean_rows(rows)
    hand = balanced_anova(rows, ["lab", "date", "lab:date"])
    assert hand["balanced"]
    assert hand["negative_allowed"] is True
    # The truth has date variance exactly zero; the moment estimate is noise
    # around zero and may be negative, while REML must respect the boundary.
    assert abs(hand["estimates"]["date"]) < 0.05
    fit = fit_variance_components(rows, ["lab", "date", "lab:date"])
    assert fit.sigma2["date"] == 0.0
    # The other components still line up with the hand calculation.
    assert fit.sigma2["lab"] == pytest.approx(hand["estimates"]["lab"], rel=1e-2, abs=1e-3)
    assert fit.residual == pytest.approx(hand["estimates"]["residual"], rel=1e-6)


def test_unbalanced_layout_refuses_to_hand_calculate():
    rows, _ = simulate(_normalized_preset("nested_clean"), 3)
    rows = clean_rows(rows)
    # Drop one replicate from a single cell: the layout is no longer balanced.
    drop = next(i for i, r in enumerate(rows) if i < 20)
    rows = rows[:drop] + rows[drop + 1 :]
    hand = balanced_anova(rows, ["lab", "device"])
    assert hand["balanced"] is False
    assert "重复次数不一致" in hand["reason"]
