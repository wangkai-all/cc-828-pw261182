"""Validation rounds: simulate new measurements under the fitted model and compare.

A validation round is a *new* set of measurements on a proposed design.  We
cannot know its values in advance, so the app generates them from the fitted
variance components (parametric simulation), refits the same model, and reports
how the expectations performed:

* predicted vs observed standard error of the grand mean,
* bias of each variance component,
* Wald interval coverage for each component,
* how often a component lands on the non-negativity boundary.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Sequence

import numpy as np

from .design import ScenarioPlan, _scenario_rows, scenario_variance
from .variance import (
    DesignError,
    FitResult,
    build_design,
    fit_variance_components,
    TERM_ORDER,
    TERM_LABELS,
)


def _cell_levels(rows: Sequence[Dict[str, object]], term: str) -> List[tuple]:
    if term == "lab":
        return [("lab", r["lab"]) for r in rows]
    if term == "device":
        return [("device", r["lab"], r["device"]) for r in rows]
    if term == "date":
        return [("date", r["date"]) for r in rows]
    if term == "lab:date":
        return [("lab:date", r["lab"], r["date"]) for r in rows]
    if term == "device:date":
        return [("device:date", r["lab"], r["device"], r["date"]) for r in rows]
    raise DesignError(f"未知随机项: {term}")


def simulate_values(
    rows: Sequence[Dict[str, object]],
    terms: Sequence[str],
    sigma2: Dict[str, float],
    residual: float,
    mu: float,
    rng: np.random.Generator,
) -> List[Dict[str, object]]:
    """Draw one dataset from the model with the given components."""
    effects: Dict[tuple, float] = {}
    for term in TERM_ORDER:
        if term not in terms:
            continue
        sd = math.sqrt(max(float(sigma2.get(term, 0.0)), 0.0))
        keys = _cell_levels(rows, term)
        uniq = list(dict.fromkeys(keys))
        draw = {k: rng.normal(0.0, sd) if sd > 0 else 0.0 for k in uniq}
        for k in uniq:
            effects[k] = draw[k]
    out = []
    # Build per-row effect lookup directly.
    level_keys = {term: _cell_levels(rows, term) for term in TERM_ORDER if term in terms}
    for i, r in enumerate(rows):
        base = mu
        for term in TERM_ORDER:
            if term in terms:
                base += effects[level_keys[term][i]]
        y = base + rng.normal(0.0, math.sqrt(max(residual, 0.0)))
        out.append(
            {
                "lab": r["lab"],
                "device": r["device"],
                "date": r["date"],
                "value": round(float(y), 6),
            }
        )
    return out


def _wald_intervals(design_rows, fit: FitResult, terms: Sequence[str]) -> Dict[str, Dict[str, float]]:
    """Approximate Wald intervals from the observed REML information.

    Used for validation coverage where running profile likelihoods inside a
    simulation loop would be prohibitively slow.  The profile intervals shown
    in the main fit remain the reported ones.
    """
    from .variance import _Objective

    design = build_design(design_rows, terms)
    obj = _Objective(design)
    x = fit.x.copy()
    K = len(terms)
    h = 1e-4
    f0 = obj.fg(x)[0]
    H = np.zeros((K + 1, K + 1))
    for i in range(K + 1):
        for j in range(i, K + 1):
            def f(xi, xj):
                z = x.copy()
                z[i] += xi
                z[j] += xj
                return obj.fg(z)[0]
            hh_i = h * max(1.0, abs(x[i]))
            hh_j = h * max(1.0, abs(x[j]))
            if i == j:
                if i < K and x[i] - hh_i < 0:
                    val = (f(hh_i, 0) - 2 * f0 + f(0, 0)) / (hh_i ** 2)
                else:
                    val = (f(hh_i, 0) - 2 * f0 + f(-hh_i, 0)) / (hh_i ** 2)
            else:
                val = (
                    f(hh_i, hh_j) - f(hh_i, -hh_j) - f(-hh_i, hh_j) + f(-hh_i, -hh_j)
                ) / (4 * hh_i * hh_j)
            H[i, j] = H[j, i] = val
    try:
        cov = np.linalg.inv(H)
    except np.linalg.LinAlgError:
        return {t: {"se": float("nan"), "lower": float("nan"), "upper": float("nan")} for t in list(terms) + ["residual"]}
    diag = np.clip(np.diag(cov), 0, None)
    out = {}
    for k, t in enumerate(terms):
        se = math.sqrt(diag[k])
        est = float(fit.sigma2[t])
        out[t] = {"se": se, "lower": max(0.0, est - 1.96 * se), "upper": est + 1.96 * se}
    se = math.sqrt(diag[K])
    out["residual"] = {
        "se": se,
        "lower": max(0.0, float(fit.residual) - 1.96 * se),
        "upper": float(fit.residual) + 1.96 * se,
    }
    return out


@dataclass
class ValidationResult:
    plan: ScenarioPlan
    predicted_sd: float
    observed_sd: float
    simulated: int
    components: List[Dict[str, object]]
    mu: Dict[str, object]
    boundary: Dict[str, object]
    example: List[Dict[str, object]]
    seed: int
    warnings: List[str]

    def to_dict(self) -> Dict[str, object]:
        return {
            "predicted_sd": self.predicted_sd,
            "observed_sd": self.observed_sd,
            "ratio": self.observed_sd / self.predicted_sd if self.predicted_sd > 0 else None,
            "simulated": self.simulated,
            "components": self.components,
            "mu": self.mu,
            "boundary": self.boundary,
            "example": self.example[:200],
            "seed": self.seed,
            "warnings": self.warnings,
        }


def validation_round(
    rows: Sequence[Dict[str, object]],
    fit: FitResult,
    plan: ScenarioPlan,
    *,
    n_sim: int = 24,
    seed: int = 20261125,
    level: float = 0.95,
) -> Dict[str, object]:
    """Simulate ``n_sim`` validation rounds on the augmented design and refit.

    The first simulated round is returned in full as the *example* round, so the
    user can inspect what one round of new measurements would look like and
    what the refit would say.  Coverage uses Wald intervals (fast); the main
    app's intervals remain profile likelihood.
    """
    n_sim = int(max(1, min(n_sim, 200)))
    aug = _scenario_rows(rows, plan)
    added = aug[len(rows):]
    if not added:
        raise DesignError("验证情景没有新增测量：至少选择一种追加方式。")
    predicted_var = scenario_variance(rows, fit, plan)
    predicted_sd = math.sqrt(max(predicted_var, 0.0))
    rng = np.random.default_rng(seed)
    mu_hats: List[float] = []
    est: Dict[str, List[float]] = {t: [] for t in fit.terms}
    resid: List[float] = []
    cover = {t: 0 for t in list(fit.terms) + ["residual"]}
    boundary = {t: 0 for t in fit.terms}
    fails = 0
    fail_reasons: Dict[str, int] = {}
    warnings: List[str] = []
    example: List[Dict[str, object]] = []
    for s in range(n_sim):
        sim_rows = simulate_values(
            aug, fit.terms, fit.sigma2, fit.residual, fit.mu, rng
        )
        if s == 0:
            example = [
                dict(r, new=True) for r in sim_rows[len(rows):]
            ]
        try:
            f2 = fit_variance_components(sim_rows, fit.terms)
        except (DesignError, np.linalg.LinAlgError, FloatingPointError) as e:
            # A single pathological draw must not abort the whole round; the
            # tainted simulation is skipped and reported, never patched by
            # nudging the covariance.
            fails += 1
            key = type(e).__name__
            fail_reasons[key] = fail_reasons.get(key, 0) + 1
            continue
        mu_hats.append(f2.mu)
        for t in fit.terms:
            est[t].append(f2.sigma2[t])
            if f2.sigma2[t] <= 1e-12 * max(f2.total, 1e-12):
                boundary[t] += 1
        resid.append(f2.residual)
        try:
            wald = _wald_intervals(sim_rows, f2, fit.terms)
        except (np.linalg.LinAlgError, DesignError, ValueError):
            wald = {}
        # Coverage asks whether the interval from this round's refit contains
        # the value that generated the data (the plug-in truth), never the
        # round's own estimate - that would always be covered.
        for t in list(fit.terms) + ["residual"]:
            if t not in wald:
                continue
            truth_t = float(fit.sigma2[t]) if t != "residual" else float(fit.residual)
            if wald[t]["lower"] <= truth_t <= wald[t]["upper"]:
                cover[t] += 1
    if fails:
        detail = "、".join(f"{k}×{v}" for k, v in fail_reasons.items())
        warnings.append(
            f"{fails} 次模拟拟合失败（{detail}），已跳过；覆盖与偏差只按可拟合的 {len(mu_hats)} 轮统计。"
        )
    kept = len(mu_hats)
    if kept == 0:
        raise DesignError("所有验证模拟都未能拟合，请检查追回方案。")
    observed_sd = float(np.std(mu_hats, ddof=1)) if kept > 1 else float("nan")

    comps = []
    for t in fit.terms:
        vals = np.array(est[t])
        comps.append(
            {
                "term": t,
                "label": TERM_LABELS.get(t, t),
                "truth": float(fit.sigma2[t]),
                "mean": float(np.mean(vals)),
                "bias": float(np.mean(vals) - fit.sigma2[t]),
                "sd": float(np.std(vals, ddof=1)) if kept > 1 else float("nan"),
                "coverage": cover[t] / kept,
                "boundary_rate": boundary[t] / kept,
            }
        )
    vals = np.array(resid)
    comps.append(
        {
            "term": "residual",
            "label": TERM_LABELS["residual"],
            "truth": float(fit.residual),
            "mean": float(np.mean(vals)),
            "bias": float(np.mean(vals) - fit.residual),
            "sd": float(np.std(vals, ddof=1)) if kept > 1 else float("nan"),
            "coverage": cover["residual"] / kept,
            "boundary_rate": 0.0,
        }
    )
    return {
        "predicted_sd": float(predicted_sd),
        "observed_sd": observed_sd,
        "ratio": float(observed_sd / predicted_sd) if predicted_sd > 0 and kept > 1 else None,
        "simulated": kept,
        "requested": n_sim,
        "components": comps,
        "mu": {
            "truth": float(fit.mu),
            "mean": float(np.mean(mu_hats)),
            "sd": observed_sd,
            "sd_of_mean": float(np.std(mu_hats, ddof=1) / math.sqrt(kept)) if kept > 1 else float("nan"),
        },
        "boundary": {t: boundary[t] / kept for t in fit.terms},
        "example": example,
        "seed": seed,
        "level": level,
        "warnings": warnings,
        "new_measurements": len(added),
        "predicted_sd_note": "按拟合分量对新设计做 GLS 精度预测",
    }
