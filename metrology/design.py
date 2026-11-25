"""Design connectivity graph and budget scenarios.

The graph is a bipartite laboratory-date projection.  A connected graph means
the crossed terms are at least nominally estimable; the component structure is
what a lab:date interaction cannot bridge.  The budget module answers the
question "next money: more labs, more devices, or more repeats?" using the
precision of the grand mean under the *current* variance components, and it
never counts money already spent as available budget.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .variance import Design, FitResult, _gls_total_variance, build_design

# ---------------------------------------------------------------------------
# Graph data for the design map
# ---------------------------------------------------------------------------


def design_graph(rows: Sequence[Dict[str, object]]) -> Dict[str, object]:
    """Nodes/edges for the lab-date connectivity map, plus device inventory."""
    labs = sorted({r["lab"] for r in rows})
    dates = sorted({r["date"] for r in rows})
    nodes = [{"id": f"lab:{l}", "kind": "lab", "label": l} for l in labs]
    nodes += [{"id": f"date:{d}", "kind": "date", "label": d} for d in dates]
    weight: Dict[Tuple[str, str], int] = {}
    devs: Dict[Tuple[str, str], set] = {}
    for r in rows:
        key = (r["lab"], r["date"])
        weight[key] = weight.get(key, 0) + 1
        devs.setdefault(key, set()).add(r["device"])
    edges = [
        {
            "source": f"lab:{l}",
            "target": f"date:{d}",
            "measurements": int(n),
            "devices": len(devs[(l, d)]),
        }
        for (l, d), n in sorted(weight.items())
    ]
    # Connected components (labs linked when they share a date).
    parent = {f"lab:{l}": f"lab:{l}" for l in labs}
    parent.update({f"date:{d}": f"date:{d}" for d in dates})

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (l, d) in weight:
        a, b = find(f"lab:{l}"), find(f"date:{d}")
        if a != b:
            parent[b] = a
    comp_of_lab = {l: find(f"lab:{l}") for l in labs}
    comps = {}
    for l in labs:
        comps.setdefault(comp_of_lab[l], []).append(l)
    components = [
        {
            "labs": v,
            "dates": sorted(
                {d for (l, d) in weight if comp_of_lab[l] == root}
            ),
        }
        for root, v in comps.items()
    ]
    lab_devices = {l: sorted({r["device"] for r in rows if r["lab"] == l}) for l in labs}
    return {
        "nodes": nodes,
        "edges": edges,
        "components": components,
        "labs": labs,
        "dates": dates,
        "labs_by_device": {
            d: sorted({r["lab"] for r in rows if r["device"] == d and r["lab"] == l})
            for l in labs
            for d in lab_devices[l]
        },
        "lab_devices": lab_devices,
    }


# ---------------------------------------------------------------------------
# Precision model for budget scenarios
# ---------------------------------------------------------------------------


@dataclass
class ScenarioPlan:
    """A hypothetical augmentation of the current design."""

    add_labs: int = 0
    add_devices_per_lab: int = 0
    add_reps: int = 0
    add_dates: int = 0
    new_lab_cost: float = 0.0
    new_device_cost: float = 0.0
    extra_repeat_cost: float = 0.0
    extra_date_cost: float = 0.0


def _mean_variance(design: Design, theta: np.ndarray, sigma2: float) -> float:
    return _gls_total_variance(design, theta, sigma2)


def _scenario_rows(
    rows: Sequence[Dict[str, object]],
    plan: ScenarioPlan,
    *,
    dates_per_new_lab: int = 1,
    used_devices: Optional[Sequence[Dict[str, object]]] = None,
) -> List[Dict[str, object]]:
    """Materialise the hypothetical design as rows (for exact GLS precision).

    Existing rows are never removed and their cost is *not* part of the
    scenario budget - the caller only ever charges money for new measurements.
    """
    labs = sorted({r["lab"] for r in rows})
    dates = sorted({r["date"] for r in rows})
    per_lab_devices: Dict[str, List[str]] = {}
    for r in rows:
        per_lab_devices.setdefault(r["lab"], [])
        if r["device"] not in per_lab_devices[r["lab"]]:
            per_lab_devices[r["lab"]].append(r["device"])
    reps_per_cell: Dict[Tuple[str, str, str], int] = {}
    for r in rows:
        k = (r["lab"], r["device"], r["date"])
        reps_per_cell[k] = reps_per_cell.get(k, 0) + 1
    base_eps = float(np.mean(list(reps_per_cell.values()))) if reps_per_cell else 1.0

    out = [
        {"lab": r["lab"], "device": r["device"], "date": r["date"], "value": 0.0}
        for r in rows
    ]
    # Devices that did not exist yet but were bought in this scenario still
    # cost money; they are added as zero-variance measurements so that the
    # precision model sees them, while the cost function counts them once.
    for r in used_devices or []:
        out.append(
            {"lab": r["lab"], "device": r["device"], "date": r["date"], "value": 0.0}
        )

    # New dates extend every existing lab (crossed date factor).
    new_dates: List[str] = []
    for k in range(plan.add_dates):
        new_dates.append(f"新增日期-{k + 1}")
    if new_dates:
        for l in labs:
            for dev in per_lab_devices[l]:
                for dt in new_dates:
                    for _ in range(max(1, int(round(base_eps + plan.add_reps)))):
                        out.append({"lab": l, "device": dev, "date": dt, "value": 0.0})

    # Repeats apply to existing cells (same lab/device/date).
    if plan.add_reps > 0:
        for (l, dev, dt), m in reps_per_cell.items():
            for _ in range(plan.add_reps):
                out.append({"lab": l, "device": dev, "date": dt, "value": 0.0})

    # Additional devices per lab measure on the lab's existing dates.
    if plan.add_devices_per_lab > 0:
        for i, l in enumerate(labs):
            lab_dates = sorted({r["date"] for r in rows if r["lab"] == l}) or dates[:1]
            for k in range(plan.add_devices_per_lab):
                dev = f"{l}-新增设备{k + 1}"
                for dt in lab_dates:
                    for _ in range(max(1, int(round(base_eps)))):
                        out.append({"lab": l, "device": dev, "date": dt, "value": 0.0})

    # New labs bring their own devices and measure on shared dates, which is
    # what connects them to the rest of the design.
    if plan.add_labs > 0:
        shared = dates if dates_per_new_lab <= 0 else dates[:dates_per_new_lab]
        if not shared:
            shared = ["新增日期-1"]
        for i in range(plan.add_labs):
            lab = f"新增实验室{k_label(i)}"
            dev = f"{lab}-D1"
            for dt in shared:
                for _ in range(max(1, int(round(base_eps)))):
                    out.append({"lab": lab, "device": dev, "date": dt, "value": 0.0})
    return out


def k_label(i: int) -> str:
    return chr(ord("A") + i) if i < 26 else str(i + 1)


def scenario_variance(
    rows: Sequence[Dict[str, object]],
    fit: FitResult,
    plan: ScenarioPlan,
    *,
    dates_per_new_lab: int = 1,
    used_devices: Optional[Sequence[Dict[str, object]]] = None,
) -> float:
    """Var of the GLS grand mean under ``plan``, plugging in fitted components.

    The hypothetical design is materialised and its GLS precision computed, so
    existing and new structure enter exactly rather than through a formula of
    thumb.  A disconnected date graph still has a plug-in variance; the caller
    checks separability separately.
    """
    new_rows = _scenario_rows(
        rows, plan, dates_per_new_lab=dates_per_new_lab, used_devices=used_devices
    )
    design = build_design(new_rows, fit.terms)
    theta = np.array([max(fit.sigma2[t], 0.0) for t in fit.terms])
    return _mean_variance(design, theta, fit.residual)


def plan_units(
    rows: Sequence[Dict[str, object]],
    fit: FitResult,
    plan: ScenarioPlan,
    *,
    dates_per_new_lab: int = 1,
    used_devices: Optional[Sequence[Dict[str, object]]] = None,
) -> Dict[str, float]:
    """Count and price the units a scenario introduces.

    * ``repeats`` - new observations inside a cell that already exists.
    * ``cell_measurements`` - observations on new (device, date) cells.
    * ``device_days`` - the number of new (device, date) cells.  A cell that
      already exists contributes repeats, not new device-days, however many
      observations the scenario adds there.
    * ``measurements`` - all new observations, whether repeats or on new cells.
    * ``new_labs`` and ``new_devices`` - brand-new units.

    The current design's own cost is history: nothing here re-charges it.
    """
    new_rows = _scenario_rows(
        rows, plan, dates_per_new_lab=dates_per_new_lab, used_devices=used_devices
    )
    existing_cells = {(r["lab"], r["device"], r["date"]) for r in rows}
    existing_devices = {(r["lab"], r["device"]) for r in rows}
    used_cells = {(r["lab"], r["device"], r["date"]) for r in (used_devices or [])}
    counts: Dict[Tuple[str, str, str], int] = {}
    for r in new_rows[len(rows):]:
        key = (r["lab"], r["device"], r["date"])
        counts[key] = counts.get(key, 0) + 1
    repeats = 0
    cell_measurements = 0
    new_device_days = 0
    for key, m in counts.items():
        if key in used_cells:
            continue  # already scheduled and paid for
        if key in existing_cells:
            repeats += m
        else:
            new_device_days += 1
            cell_measurements += m
    new_devices = len(
        {
            (r["lab"], r["device"])
            for r in new_rows
            if (r["lab"], r["device"]) not in existing_devices
        }
    )
    return {
        "repeats": float(repeats),
        "cell_measurements": float(cell_measurements),
        "measurements": float(repeats + cell_measurements),
        "device_days": float(new_device_days),
        "new_devices": float(new_devices),
        "new_labs": float(plan.add_labs),
    }


def cost_plan(
    units: Dict[str, float],
    *,
    cost_lab: float,
    cost_device: float,
    cost_repeat: float,
) -> float:
    """Cost of *new* work only.

    Three prices, matching the three levers: a new laboratory (its first device
    included), each additional device, and each new measurement.  Existing
    measurements are sunk: they are never counted toward, or subtracted from,
    an available budget.
    """
    device_units = max(units["new_devices"] - units["new_labs"], 0.0)
    return (
        units["new_labs"] * cost_lab
        + device_units * cost_device
        + units["measurements"] * cost_repeat
    )


# ---------------------------------------------------------------------------
# Search over budget allocations
# ---------------------------------------------------------------------------


def enumerate_plans(
    rows: Sequence[Dict[str, object]],
    fit: FitResult,
    *,
    budget: float,
    cost_lab: float,
    cost_device: float,
    cost_repeat: float,
    cost_date: float = 0.0,
    max_labs: int = 4,
    max_devices: int = 3,
    max_reps: int = 6,
    max_dates: int = 2,
    max_candidates: int = 400,
    limit: int = 60,
) -> Dict[str, object]:
    """Enumerate affordable augmentation plans and rank by predicted precision.

    Cost is computed from the *new* work only.  The current design's cost is
    history: it is reported separately and never subtracted from the budget.
    The enumeration is deterministic; ``limit`` caps how many ranked rows are
    returned and ``max_candidates`` bounds the work for large designs.
    """
    n_labs = len({r["lab"] for r in rows})
    base_plan = ScenarioPlan()
    base_var = scenario_variance(rows, fit, base_plan)
    prices = {
        "lab": float(cost_lab),
        "device": float(cost_device),
        "repeat": float(cost_repeat),
        "date": float(cost_date),
    }
    if budget <= 0:
        # Still report the current design's precision so a zero budget can be
        # compared with a target: "no money left, are we already there?".
        return {
            "plans": [],
            "n_affordable": 0,
            "truncated": False,
            "budget": 0.0,
            "base_cost": 0.0,
            "base_variance": float(base_var),
            "base_sd": float(math.sqrt(base_var)),
            "best": None,
            "prices": prices,
        }

    # Repeat price must exceed zero for repeats to be a lever; likewise for the
    # other dimensions.  A zero price means "not selectable" rather than free.
    labs_range = range(max_labs + 1) if cost_lab > 0 else [0]
    dev_range = range(max_devices + 1) if cost_device > 0 else [0]
    rep_range = range(max_reps + 1) if cost_repeat > 0 else [0]
    date_range = range(max_dates + 1) if cost_date > 0 else [0]

    seen = set()
    candidates: List[Dict[str, object]] = []
    truncated = False
    for add_labs in labs_range:
        for add_dev in dev_range:
            for add_rep in rep_range:
                for add_dates in date_range:
                    if add_labs + add_dev + add_rep + add_dates == 0:
                        continue
                    key = (add_labs, add_dev, add_rep, add_dates)
                    if key in seen:
                        continue
                    seen.add(key)
                    plan = ScenarioPlan(add_labs=add_labs, add_devices_per_lab=add_dev, add_reps=add_rep, add_dates=add_dates)
                    units = plan_units(rows, fit, plan)
                    cost = cost_plan(
                        units, cost_lab=cost_lab, cost_device=cost_device, cost_repeat=cost_repeat
                    ) + add_dates * cost_date * max(1, n_labs)
                    if cost > budget + 1e-9:
                        continue
                    if len(candidates) >= max_candidates:
                        truncated = True
                        break
                    var = scenario_variance(rows, fit, plan)
                    candidates.append(
                        {
                            "add_labs": add_labs,
                            "add_devices_per_lab": add_dev,
                            "add_reps": add_rep,
                            "add_dates": add_dates,
                            "cost": float(cost),
                            "budget": float(budget),
                            "remaining": float(budget - cost),
                            "new_measurements": int(units["measurements"]),
                            "new_device_days": int(units["device_days"]),
                            "new_repeats": int(units["repeats"]),
                            "new_cell_measurements": int(units["cell_measurements"]),
                            "new_devices": int(units["new_devices"]),
                            "mean_variance": float(var),
                            "mean_sd": float(math.sqrt(var)),
                            "reduction": float(1.0 - var / base_var) if base_var > 0 else 0.0,
                        }
                    )
                if truncated:
                    break
            if truncated:
                break
        if truncated:
            break

    candidates.sort(key=lambda c: (c["mean_variance"], c["cost"], c["add_labs"], c["add_reps"]))
    best = candidates[0] if candidates else None
    return {
        "plans": candidates[:limit],
        "n_affordable": len(candidates),
        "truncated": truncated,
        "budget": float(budget),
        "base_variance": float(base_var),
        "base_sd": float(math.sqrt(base_var)),
        "best": best,
        "prices": {
            "lab": float(cost_lab),
            "device": float(cost_device),
            "repeat": float(cost_repeat),
            "date": float(cost_date),
        },
    }


def target_progress(
    out: Dict[str, object],
    target_sd: Optional[float],
) -> Dict[str, object]:
    """Compare the ranked plans with a required precision.

    ``target_sd`` is the largest acceptable standard error of the grand mean.
    The answer is threefold:

    * does the *current* design already meet it (no spend needed)?
    * if not, what is the cheapest plan *within the budget* that does?
    * if none does, how far short does the best affordable plan fall?

    ``plans`` are already budget-limited, so "cheapest" always means cheapest
    among affordable plans; a plan the budget cannot buy is never offered.
    """
    base_sd = float(out.get("base_sd") or 0.0)
    plans = out.get("plans") or []
    if target_sd is None or not math.isfinite(float(target_sd)) or float(target_sd) <= 0:
        return {
            "target_sd": None,
            "current_meets": None,
            "cheapest": None,
            "n_meeting": None,
            "shortfall": None,
        }
    target = float(target_sd)
    current_meets = base_sd <= target + 1e-12
    meeting = [p for p in plans if p["mean_sd"] <= target + 1e-12]
    # Cheapest by price, not by precision rank.
    cheapest = min(meeting, key=lambda p: (p["cost"], p["mean_sd"])) if meeting else None
    best = out.get("best")
    best_sd = float(best["mean_sd"]) if best else base_sd
    return {
        "target_sd": target,
        "current_meets": bool(current_meets),
        "current_sd": base_sd,
        "cheapest": cheapest,
        "n_meeting": len(meeting),
        "n_listed": len(plans),
        "best_affordable_sd": best_sd,
        "shortfall": max(0.0, best_sd - target) if not current_meets else 0.0,
    }


def precision_summary(
    rows: Sequence[Dict[str, object]],
    fit: FitResult,
    *,
    horizon_reps: int = 0,
) -> Dict[str, object]:
    """Current precision and the largest single-lever augmentations."""
    design = build_design(rows, fit.terms)
    theta = np.array([fit.sigma2[t] for t in fit.terms])
    v0 = _mean_variance(design, theta, fit.residual)
    n_labs = len({r["lab"] for r in rows})
    n_dev = len({(r["lab"], r["device"]) for r in rows})
    n_cells = design.B
    return {
        "mean_variance": float(v0),
        "mean_sd": float(math.sqrt(v0)),
        "n_labs": n_labs,
        "n_devices": n_dev,
        "n_cells": n_cells,
        "mean_reps": float(np.mean(design.counts)),
    }
