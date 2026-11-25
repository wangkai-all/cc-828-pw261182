"""Budget scenarios: cost counts only new work, and the existing programme is
never re-charged or subtracted from the spendable budget."""
from __future__ import annotations

import math

import pytest

from metrology import design as D
from metrology.ingest import _normalized_preset, simulate
from metrology.variance import clean_rows, fit_variance_components


@pytest.fixture(scope="module")
def fitted():
    rows, _ = simulate(_normalized_preset("nested_clean"), 20261125)
    rows = clean_rows(rows)
    return rows, fit_variance_components(rows, ["lab", "device"])


def test_plan_units_never_count_existing_measurements(fitted):
    rows, fit = fitted
    zero = D.plan_units(rows, fit, D.ScenarioPlan())
    assert zero["measurements"] == 0.0
    assert zero["device_days"] == 0.0
    assert zero["new_devices"] == 0.0
    assert zero["new_labs"] == 0.0
    assert D.cost_plan(zero, cost_lab=1200, cost_device=250, cost_repeat=40) == 0.0


def test_pure_repeat_plan_costs_only_repeats(fitted):
    rows, fit = fitted
    plan = D.ScenarioPlan(add_reps=2)
    units = D.plan_units(rows, fit, plan)
    n_cells = len({(r["lab"], r["device"], r["date"]) for r in rows})
    assert units["repeats"] == 2 * n_cells
    assert units["measurements"] == 2 * n_cells
    assert units["device_days"] == 0
    cost = D.cost_plan(units, cost_lab=1200, cost_device=250, cost_repeat=40)
    assert cost == pytest.approx(40 * 2 * n_cells)


def test_new_lab_plan_charges_the_lab_price_not_an_extra_device(fitted):
    rows, fit = fitted
    plan = D.ScenarioPlan(add_labs=1)
    units = D.plan_units(rows, fit, plan)
    assert units["new_labs"] == 1
    assert units["new_devices"] == 1  # its first device
    cost = D.cost_plan(units, cost_lab=1200, cost_device=250, cost_repeat=40)
    # The first device is included in the laboratory price; only the actual
    # measurements it takes are charged on top.
    assert cost == pytest.approx(1200 + units["measurements"] * 40)


def test_extra_device_is_charged_per_device_and_per_measurement(fitted):
    rows, fit = fitted
    plan = D.ScenarioPlan(add_devices_per_lab=1)
    units = D.plan_units(rows, fit, plan)
    n_labs = len({r["lab"] for r in rows})
    assert units["new_devices"] == n_labs
    assert units["new_labs"] == 0
    cost = D.cost_plan(units, cost_lab=1200, cost_device=250, cost_repeat=40)
    assert cost == pytest.approx(n_labs * 250 + units["measurements"] * 40)


def test_scenario_variance_matches_a_materialised_gls_computation(fitted):
    rows, fit = fitted
    plan = D.ScenarioPlan(add_labs=1, add_reps=1)
    got = D.scenario_variance(rows, fit, plan)
    aug = D._scenario_rows(rows, plan)
    from metrology.variance import _gls_total_variance, build_design

    design = build_design(aug, fit.terms)
    theta = [fit.sigma2[t] for t in fit.terms]
    assert got == pytest.approx(_gls_total_variance(design, __import__("numpy").array(theta), fit.residual))


def test_more_repeats_never_hurt_predicted_precision(fitted):
    rows, fit = fitted
    previous = D.scenario_variance(rows, fit, D.ScenarioPlan())
    for reps in range(1, 5):
        var = D.scenario_variance(rows, fit, D.ScenarioPlan(add_reps=reps))
        assert var <= previous + 1e-12
        previous = var


def test_budget_enumeration_is_affordable_and_ordered(fitted):
    rows, fit = fitted
    out = D.enumerate_plans(
        rows, fit, budget=1500.0, cost_lab=1200.0, cost_device=250.0, cost_repeat=40.0
    )
    assert out["plans"]
    assert out["n_affordable"] == len([p for p in out["plans"]]) or out["n_affordable"] >= len(out["plans"])
    for p in out["plans"]:
        assert p["cost"] <= 1500.0 + 1e-9
        assert p["mean_sd"] == pytest.approx(math.sqrt(p["mean_variance"]))
        assert p["reduction"] >= -1e-12
    sds = [p["mean_sd"] for p in out["plans"]]
    assert sds == sorted(sds)
    assert out["best"]["mean_variance"] == min(p["mean_variance"] for p in out["plans"])


def test_budget_excludes_sunk_cost_so_a_cheap_lab_is_reachable(fitted):
    """The spendable budget must not be reduced by what the current programme
    already cost: a 1500 budget can buy one new lab even though the existing
    design's implied price is far larger."""
    rows, fit = fitted
    one_lab = D.enumerate_plans(
        rows, fit, budget=1500.0, cost_lab=1200.0, cost_device=250.0, cost_repeat=40.0
    )
    assert any(p["add_labs"] == 1 and p["cost"] <= 1500.0 for p in one_lab["plans"])
    expensive_existing = 1e6
    assert expensive_existing > 1500.0  # the existing design is history, not budget


def test_zero_budget_yields_no_plans_but_still_reports_current_precision(fitted):
    rows, fit = fitted
    out = D.enumerate_plans(rows, fit, budget=0.0, cost_lab=1, cost_device=1, cost_repeat=1)
    assert out["plans"] == []
    assert out["truncated"] is False
    # A zero budget must still answer "is the current design precise enough?".
    assert out["base_sd"] > 0
    assert out["prices"]["lab"] == 1
    t = D.target_progress(out, out["base_sd"] * 2)
    assert t["current_meets"] is True
    assert t["cheapest"] is None


def test_zero_prices_disable_a_lever_instead_of_making_it_free(fitted):
    rows, fit = fitted
    out = D.enumerate_plans(
        rows, fit, budget=1000.0, cost_lab=0.0, cost_device=250.0, cost_repeat=40.0
    )
    assert all(p["add_labs"] == 0 for p in out["plans"])


# ---------------------------------------------------------------------------
# Target precision
# ---------------------------------------------------------------------------


def test_no_target_reports_nothing_rather_than_zero(fitted):
    rows, fit = fitted
    out = D.enumerate_plans(
        rows, fit, budget=1500.0, cost_lab=1200.0, cost_device=250.0, cost_repeat=40.0
    )
    t = D.target_progress(out, None)
    assert t["target_sd"] is None
    assert t["current_meets"] is None
    assert t["cheapest"] is None
    assert t["shortfall"] is None


def test_loose_target_is_already_met_by_the_current_design(fitted):
    rows, fit = fitted
    out = D.enumerate_plans(
        rows, fit, budget=1500.0, cost_lab=1200.0, cost_device=250.0, cost_repeat=40.0
    )
    # Anything at or above the current SD needs no new money.
    t = D.target_progress(out, out["base_sd"] * 1.5)
    assert t["current_meets"] is True
    assert t["shortfall"] == 0.0
    assert t["current_sd"] == pytest.approx(out["base_sd"])


def test_reachable_target_names_the_cheapest_plan(fitted):
    rows, fit = fitted
    out = D.enumerate_plans(
        rows, fit, budget=5000.0, cost_lab=1200.0, cost_device=250.0, cost_repeat=40.0
    )
    reachable = [p["mean_sd"] for p in out["plans"] if p["mean_sd"] < out["base_sd"]]
    assert reachable
    t = D.target_progress(out, max(reachable))
    assert t["current_meets"] is False
    assert t["cheapest"] is not None
    assert t["cheapest"]["mean_sd"] <= t["target_sd"] + 1e-12
    assert t["n_meeting"] >= 1
    # Cheapest by price among meeting plans, not the precision champion.
    price = min(p["cost"] for p in out["plans"] if p["mean_sd"] <= t["target_sd"] + 1e-12)
    assert t["cheapest"]["cost"] == pytest.approx(price)
    assert t["shortfall"] == 0.0


def test_unreachable_target_reports_the_shortfall(fitted):
    rows, fit = fitted
    out = D.enumerate_plans(
        rows, fit, budget=500.0, cost_lab=1200.0, cost_device=250.0, cost_repeat=40.0
    )
    t = D.target_progress(out, out["base_sd"] / 1000.0)
    assert t["current_meets"] is False
    assert t["cheapest"] is None
    assert t["n_meeting"] == 0
    best_sd = out["best"]["mean_sd"] if out.get("best") else out["base_sd"]
    assert t["best_affordable_sd"] == pytest.approx(best_sd)
    assert t["shortfall"] == pytest.approx(max(0.0, best_sd - t["target_sd"]))
    assert t["shortfall"] > 0.0
