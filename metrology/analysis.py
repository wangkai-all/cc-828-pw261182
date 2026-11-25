"""Orchestration: one call from raw rows to a fully traceable analysis bundle."""
from __future__ import annotations

import math
from typing import Dict, Sequence

import numpy as np

from . import design as D
from . import validate as VAL
from .variance import (
    TERM_LABELS,
    TERM_ORDER,
    balanced_anova,
    fit_variance_components,
    fit_with_intervals,
    separability,
)


def _jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if not math.isfinite(v) else v
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, float):
        return None if not math.isfinite(obj) else obj
    if isinstance(obj, np.ndarray):
        return [_jsonable(v) for v in obj.tolist()]
    return obj


def analyze_design(rows: Sequence[Dict[str, object]], terms: Sequence[str]) -> Dict[str, object]:
    terms = [t for t in TERM_ORDER if t in terms]
    report = separability(rows, terms)
    graph = D.design_graph(rows) if rows else {"nodes": [], "edges": [], "components": []}
    counts = {}
    for r in rows:
        counts[(r["lab"], r["device"], r["date"])] = counts.get((r["lab"], r["device"], r["date"]), 0) + 1
    cells = len(counts)
    return _jsonable(
        {
            "terms": terms,
            "cell_count": cells,
            "row_count": len(rows),
            "labs": len({r["lab"] for r in rows}),
            "devices": len({(r["lab"], r["device"]) for r in rows}),
            "dates": len({r["date"] for r in rows}),
            "reps": {
                "min": min(counts.values()) if counts else 0,
                "max": max(counts.values()) if counts else 0,
                "median": float(np.median(list(counts.values()))) if counts else 0,
            },
            "separable": report["ok"],
            "errors": report["errors"],
            "warnings": report["warnings"],
            "notes": report["notes"],
            "confounded": report["confounded"],
            "graph": graph,
        }
    )


def fit_design(
    rows: Sequence[Dict[str, object]],
    terms: Sequence[str],
    *,
    level: float = 0.95,
) -> Dict[str, object]:
    terms = [t for t in TERM_ORDER if t in terms]
    report = separability(rows, terms)
    if not report["ok"]:
        return _jsonable(
            {
                "ok": False,
                "errors": report["errors"],
                "warnings": report["warnings"],
                "notes": report["notes"],
                "confounded": report["confounded"],
            }
        )
    bundle = fit_with_intervals(rows, terms, level=level)
    fit = bundle["fit"]
    hand = balanced_anova(rows, terms)
    comparison = []
    if hand.get("balanced"):
        for t in list(terms) + ["residual"]:
            est = hand["estimates"].get(t)
            reml = fit["components"] and next(
                (c["variance"] for c in fit["components"] if c["term"] == t), None
            )
            comparison.append(
                {
                    "term": t,
                    "label": TERM_LABELS.get(t, t),
                    "anova": est,
                    "reml": reml,
                    "difference": (reml - est) if (reml is not None and est is not None) else None,
                }
            )
    return _jsonable(
        {
            "ok": True,
            "fit": fit,
            "intervals": bundle["intervals"],
            "warnings": report["warnings"],
            "notes": report["notes"],
            "hand_check": hand,
            "comparison": comparison,
            "level": level,
        }
    )


def budget_scenarios(
    rows: Sequence[Dict[str, object]],
    terms: Sequence[str],
    settings: Dict[str, float],
    *,
    level: float = 0.95,
) -> Dict[str, object]:
    fit = fit_variance_components(rows, terms)
    summary = D.precision_summary(rows, fit)
    result = D.enumerate_plans(
        rows,
        fit,
        budget=float(settings.get("budget", 0.0)),
        cost_lab=float(settings.get("cost_lab", 0.0)),
        cost_device=float(settings.get("cost_device", 0.0)),
        cost_repeat=float(settings.get("cost_repeat", 0.0)),
    )
    target = settings.get("target_sd")
    target = float(target) if target not in (None, "") else None
    return _jsonable(
        {
            "ok": True,
            "current": summary,
            "budget": float(settings.get("budget", 0.0)),
            "prices": result["prices"],
            "plans": result["plans"],
            "best": result["best"],
            "n_affordable": result["n_affordable"],
            "base_sd": result["base_sd"],
            "base_variance": result["base_variance"],
            "truncated": result["truncated"],
            "target": D.target_progress(result, target),
            "sunk_cost_note": "已有测量的成本属于已发生支出；情景预算只计算新增实验室/设备/复测。",
        }
    )


def validation(
    rows: Sequence[Dict[str, object]],
    terms: Sequence[str],
    plan_fields: Dict[str, int],
    *,
    n_sim: int = 24,
    seed: int = 20261125,
) -> Dict[str, object]:
    fit = fit_variance_components(rows, terms)
    plan = D.ScenarioPlan(
        add_labs=int(plan_fields.get("add_labs", 0)),
        add_devices_per_lab=int(plan_fields.get("add_devices_per_lab", 0)),
        add_reps=int(plan_fields.get("add_reps", 0)),
        add_dates=int(plan_fields.get("add_dates", 0)),
    )
    result = VAL.validation_round(rows, fit, plan, n_sim=n_sim, seed=seed)
    return _jsonable(result)


def budget_and_validate(
    rows: Sequence[Dict[str, object]],
    terms: Sequence[str],
    settings: Dict[str, float],
    plan_fields: Dict[str, int],
    *,
    n_sim: int = 24,
    seed: int = 20261125,
) -> Dict[str, object]:
    out = validation(rows, terms, plan_fields, n_sim=n_sim, seed=seed)
    fit = fit_variance_components(rows, terms)
    plan = D.ScenarioPlan(
        add_labs=int(plan_fields.get("add_labs", 0)),
        add_devices_per_lab=int(plan_fields.get("add_devices_per_lab", 0)),
        add_reps=int(plan_fields.get("add_reps", 0)),
        add_dates=int(plan_fields.get("add_dates", 0)),
    )
    units = D.plan_units(rows, fit, plan)
    out["cost"] = D.cost_plan(
        units,
        cost_lab=float(settings.get("cost_lab", 0.0)),
        cost_device=float(settings.get("cost_device", 0.0)),
        cost_repeat=float(settings.get("cost_repeat", 0.0)),
    )
    out["units"] = units
    out["budget"] = float(settings.get("budget", 0.0))
    out["within_budget"] = out["cost"] <= out["budget"] + 1e-9
    return _jsonable(out)
