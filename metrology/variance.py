"""Variance-component engine for a repeated metrology design.

The model is

    y_bi = mu + sum_k u_k[level_k(b)] + e_bi,
    b = one (lab, device, date) cell, i = replicate within the cell,

with independent random effects u_k ~ N(0, theta_k) and e ~ N(0, sigma2).
``device`` is always nested in ``lab``; ``date`` is crossed with ``lab``;
``lab:date`` and ``device:date`` are the optional interactions.

Estimation is REML on an exact reduction of the likelihood: every random
effect is constant inside a measurement cell, so the cell means and the
within-cell residual sum of squares are sufficient.  For a cell b with m_b
replicates,

    ybar_b = mu + sum_k u_k[level_k(b)] + ebar_b,   Var(ebar_b) = sigma2 / m_b.

The cell-mean covariance is  V = Z diag(theta) Z' + diag(sigma2 / m_b).
The within-cell part contributes (N-B) log sigma2 + SSw / sigma2.

Non-negativity is part of the parameter space, not a numerical trick:
variance components are optimised under the bound theta_k >= 0 with a
bound-constrained quasi-Newton search, and the KKT conditions are reported
for components that settle on zero.  sigma2 is parametrised as exp(log
sigma2), so every Cholesky factor is of a strictly positive-definite matrix.
No constant is ever added to a covariance matrix to make it invertible.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import minimize, brentq
from scipy.stats import chi2

TERM_ORDER = ["lab", "device", "date", "lab:date", "device:date"]

TERM_LABELS = {
    "lab": "实验室",
    "device": "设备（嵌套于实验室）",
    "date": "日期",
    "lab:date": "实验室×日期",
    "device:date": "设备×日期",
    "residual": "重复测量（残差）",
}
TERM_SHORT = {
    "lab": "lab",
    "device": "device",
    "date": "date",
    "lab:date": "lab:date",
    "device:date": "device:date",
    "residual": "residual",
}
# Highest-order term first; used when a term must absorb the residual.
SEP = "␟"  # unit separator for composite level keys


class DesignError(ValueError):
    """The supplied design cannot support the requested model."""


# ---------------------------------------------------------------------------
# Row parsing / validation
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = ("lab", "device", "date", "value")


def clean_rows(raw: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    """Validate tabular rows and return typed rows.

    Accepts keys ``lab``, ``device``, ``date``, ``value`` (aliases in Chinese are
    handled by :func:`metrology.ingest.parse_csv`).  Raises DesignError with the
    offending row number for anything that cannot be parsed.
    """
    if not raw:
        raise DesignError("设计表为空：至少需要一条测量记录。")
    rows: List[Dict[str, object]] = []
    for i, r in enumerate(raw, start=2):  # header is row 1
        missing = [k for k in REQUIRED_FIELDS if r.get(k) in (None, "")]
        if missing:
            raise DesignError(f"第 {i} 行缺少字段: {', '.join(missing)}")
        try:
            val = float(r["value"])
        except (TypeError, ValueError):
            raise DesignError(f"第 {i} 行的测量值不是数字: {r['value']!r}")
        if not math.isfinite(val):
            raise DesignError(f"第 {i} 行的测量值不是有限数: {r['value']!r}")
        rows.append(
            {
                "lab": str(r["lab"]).strip(),
                "device": str(r["device"]).strip(),
                "date": str(r["date"]).strip(),
                "value": val,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Design reduction
# ---------------------------------------------------------------------------


@dataclass
class Design:
    rows: List[Dict[str, object]]
    terms: List[str]
    block_id: np.ndarray            # cell index per observation
    counts: np.ndarray              # m_b
    ybar: np.ndarray                # cell means
    y: np.ndarray
    Zb: Dict[str, np.ndarray]       # block-level 0/1 incidence, one 1 per row
    levels: Dict[str, List[str]]
    ss_within: float
    df_within: int
    n: int

    @property
    def B(self) -> int:
        return len(self.counts)


def _cell_keys(rows: Sequence[Dict[str, object]]) -> List[Tuple[str, str, str]]:
    return [(r["lab"], r["device"], r["date"]) for r in rows]


def build_design(rows: Sequence[Dict[str, object]], terms: Sequence[str]) -> Design:
    if not rows:
        raise DesignError("没有测量记录。")
    terms = [t for t in TERM_ORDER if t in terms]
    if not terms:
        raise DesignError("没有选择随机项。")
    y = np.array([float(r["value"]) for r in rows], dtype=float)
    keys = _cell_keys(rows)
    uniq = list(dict.fromkeys(keys))
    index = {k: i for i, k in enumerate(uniq)}
    block_id = np.array([index[k] for k in keys], dtype=int)
    B = len(uniq)
    counts = np.bincount(block_id, minlength=B).astype(float)
    sums = np.bincount(block_id, weights=y, minlength=B)
    ybar = sums / counts
    ss_within = float(np.sum((y - ybar[block_id]) ** 2))
    df_within = int(len(y) - B)

    Zb: Dict[str, np.ndarray] = {}
    levels: Dict[str, List[str]] = {}
    for term in terms:
        if term == "lab":
            lev = [r["lab"] for r in rows]
        elif term == "device":
            lev = [r["lab"] + SEP + r["device"] for r in rows]
        elif term == "date":
            lev = [r["date"] for r in rows]
        elif term == "lab:date":
            lev = [r["lab"] + SEP + r["date"] for r in rows]
        elif term == "device:date":
            lev = [r["lab"] + SEP + r["device"] + SEP + r["date"] for r in rows]
        else:
            raise DesignError(f"未知随机项: {term}")
        level_list = list(dict.fromkeys(lev))
        pos = {v: j for j, v in enumerate(level_list)}
        # Every cell has one level; use the cell's representative observation.
        first = np.zeros(B, dtype=int)
        seen = np.zeros(B, dtype=bool)
        for i, b in enumerate(block_id):
            if not seen[b]:
                seen[b] = True
                first[b] = i
        Z = np.zeros((B, len(level_list)), dtype=float)
        Z[np.arange(B), [pos[lev[i]] for i in first]] = 1.0
        Zb[term] = Z
        levels[term] = level_list
    return Design(
        rows=list(rows),
        terms=terms,
        block_id=block_id,
        counts=counts,
        ybar=ybar,
        y=y,
        Zb=Zb,
        levels=levels,
        ss_within=ss_within,
        df_within=df_within,
        n=len(y),
    )


# ---------------------------------------------------------------------------
# Design connectivity / identifiability
# ---------------------------------------------------------------------------


@dataclass
class DesignIssue:
    term: str
    code: str  # connected | confounded | partial | boundary | warning
    severity: str  # "error" | "warning" | "info"
    message: str
    detail: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "term": self.term,
            "term_label": TERM_LABELS.get(self.term, self.term),
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "detail": self.detail,
        }


def _bipartite_components(rows, a: str, b: str) -> Tuple[List[int], List[Dict[str, List[str]]]]:
    """Connected components of the a-b factor graph; component id per row."""
    parent: Dict[Tuple[str, str], Tuple[str, str]] = {}

    def find(x):
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    for r in rows:
        ka, kb = (a, r[a]), (b, r[b])
        parent.setdefault(ka, ka)
        parent.setdefault(kb, kb)
        union(ka, kb)
    roots: Dict[Tuple[str, str], int] = {}
    comp = []
    for r in rows:
        root = find((a, r[a]))
        comp.append(roots.setdefault(root, len(roots)))
    groups = []
    for c in range(max(comp) + 1):
        labs = sorted({rows[i][a] for i in range(len(rows)) if comp[i] == c})
        dates = sorted({rows[i][b] for i in range(len(rows)) if comp[i] == c})
        groups.append({"a": labs, "b": dates})
    return comp, groups


def _gram_dependency(design: Design) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    """Find linear dependencies among covariance-structure basis matrices.

    The covariance of the cell means is  V = sum_k theta_k M_k + sigma2 D,
    with M_k = Z_k Z_k' and D = diag(1/m_b).  Random terms are separately
    identifiable when {vec(M_k)} are linearly independent.  The residual is
    additionally identified by within-cell replication (df_within > 0), which
    appears in the likelihood but not in V; only when there is no replication
    must D enter the independence test.

    Returns ``(hard, soft)``: dependencies among random terms (fatal) and
    dependencies that only involve the residual basis (non-fatal when
    replication identifies sigma2; reported as a note).
    """
    mats = [design.Zb[t] @ design.Zb[t].T for t in design.terms]
    D = np.diag(1.0 / design.counts)
    replicated = design.df_within > 0

    def nullspace(bases, names):
        K = len(bases)
        G = np.zeros((K, K))
        for i in range(K):
            for j in range(i, K):
                G[i, j] = G[j, i] = float(np.sum(bases[i] * bases[j]))
        scale = max(float(np.trace(G)) / K, 1e-300)
        w, V = np.linalg.eigh(G)
        groups = []
        for idx in range(K):
            if w[idx] < 1e-11 * scale * K:
                vec = V[:, idx]
                load = np.abs(vec)
                if load.max() <= 0:
                    continue
                members = [names[i] for i in range(K) if load[i] > 1e-6 * load.max()]
                groups.append(
                    {
                        "members": members,
                        "coefficients": {
                            names[i]: float(vec[i])
                            for i in range(K)
                            if load[i] > 1e-6 * load.max()
                        },
                        "eigenvalue": float(w[idx]),
                    }
                )
        return groups

    hard = nullspace(mats, list(design.terms))
    soft: List[Dict[str, object]] = []
    if replicated:
        all_groups = nullspace(mats + [D], list(design.terms) + ["residual"])
        for g in all_groups:
            # A group that only ties a random term to D (and no other random
            # term) is not a hard conflict: replication fixes sigma2, and the
            # random term is then identified too.  Report it for transparency.
            rand = [m for m in g["members"] if m != "residual"]
            if "residual" in g["members"] and len(rand) == 1:
                soft.append(g)
    return hard, soft


def _lab_device_warnings(rows, terms) -> List[DesignIssue]:
    out: List[DesignIssue] = []
    if "lab" in terms:
        labs = {r["lab"] for r in rows}
        if len(labs) < 2:
            out.append(
                DesignIssue(
                    "lab",
                    "partial",
                    "warning",
                    f"只有 {len(labs)} 家实验室：实验室方差只由这一个（组）实验室的"
                    "整体偏移支撑，可以估计，但实质上是单一随机抽取，解释时须谨慎。",
                    {"labs": sorted(labs)},
                )
            )
    if "date" in terms:
        dates = {r["date"] for r in rows}
        if len(dates) < 2:
            out.append(
                DesignIssue(
                    "date",
                    "partial",
                    "warning",
                    f"只有 {len(dates)} 个日期：日期方差只由单一日期水平支撑，"
                    "可以估计，但实质上无法与整体偏移区分。",
                    {"dates": sorted(dates)},
                )
            )
    if "device" in terms:
        devs: Dict[str, set] = {}
        for r in rows:
            devs.setdefault(r["lab"], set()).add(r["device"])
        single = sorted([lab for lab, ds in devs.items() if len(ds) < 2])
        if single:
            out.append(
                DesignIssue(
                    "device",
                    "partial",
                    "warning",
                    f"实验室 {', '.join(single)} 只有 1 台设备：这些实验室内的"
                    "实验室效应与设备效应无法逐个分离（整体分量仍可估计）。",
                    {"labs": single},
                )
            )
    if "lab:date" in terms:
        dates: Dict[str, set] = {}
        labs: Dict[str, set] = {}
        for r in rows:
            dates.setdefault(r["lab"], set()).add(r["date"])
            labs.setdefault(r["date"], set()).add(r["lab"])
        single_d = sorted([lab for lab, ds in dates.items() if len(ds) < 2])
        single_l = sorted([d for d, ls in labs.items() if len(ls) < 2])
        if single_d:
            out.append(
                DesignIssue(
                    "lab:date",
                    "partial",
                    "warning",
                    f"实验室 {', '.join(single_d)} 只在一个日期测量：这些实验室的"
                    "实验室效应与实验室×日期交互无法逐个分离。",
                    {"labs": single_d},
                )
            )
        if single_l:
            out.append(
                DesignIssue(
                    "lab:date",
                    "partial",
                    "warning",
                    f"日期 {', '.join(single_l)} 只有一个实验室测量：这些日期的"
                    "日期效应与实验室×日期交互无法逐个分离。",
                    {"dates": single_l},
                )
            )
    if "device:date" in terms:
        dev_dates: Dict[str, set] = {}
        for r in rows:
            dev_dates.setdefault(r["lab"] + SEP + r["device"], set()).add(r["date"])
        single = sorted(d for d, ds in dev_dates.items() if len(ds) < 2)
        if single:
            readable = [d.replace(SEP, "/") for d in single[:8]]
            out.append(
                DesignIssue(
                    "device:date",
                    "partial",
                    "warning",
                    f"{len(single)} 台设备只出现在一个日期："
                    f"{', '.join(readable)}{' 等' if len(single) > 8 else ''}。"
                    "这些设备的设备效应与设备×日期交互无法逐个分离。",
                    {"devices": readable, "count": len(single)},
                )
            )
    return out


def diagnose(rows: Sequence[Dict[str, object]], terms: Sequence[str]) -> List[DesignIssue]:
    """Separability diagnostics for the requested model.

    Errors stop a fit (a component is not identifiable at all).  Warnings flag
    cells whose effects are locally confounded while the component remains
    globally estimable.
    """
    if not rows:
        return [DesignIssue("", "empty", "error", "没有测量记录。")]
    terms = [t for t in TERM_ORDER if t in terms]
    issues: List[DesignIssue] = []
    design = build_design(rows, terms)

    # Connectivity of the lab-date graph matters for lab:date.
    if "lab:date" in terms:
        comp, groups = _bipartite_components(rows, "lab", "date")
        if len(groups) > 1:
            detail = [
                {
                    "labs": g["a"],
                    "dates": g["b"],
                    "n": int(sum(1 for c in comp if c == i)),
                }
                for i, g in enumerate(groups)
            ]
            issues.append(
                DesignIssue(
                    "lab:date",
                    "disconnected",
                    "error",
                    f"实验室–日期连接图分成 {len(groups)} 个互不连通的组："
                    "组间没有共享的实验室或日期，实验室×日期交互与实验室/日期主效应"
                    "不能分离。需要至少一条连接两组的新测量。",
                    {"groups": detail},
                )
            )

    groups, soft = _gram_dependency(design)
    merged: List[List[str]] = []
    for g in groups:
        s = set(g["members"])
        hit = [i for i, m in enumerate(merged) if s & set(m)]
        if hit:
            first = hit[0]
            merged[first] = sorted(set(merged[first]) | s, key=lambda t: TERM_ORDER.index(t) if t in TERM_ORDER else 99)
            for i in reversed(hit[1:]):
                merged[first] = sorted(set(merged[first]) | set(merged.pop(i)), key=lambda t: TERM_ORDER.index(t) if t in TERM_ORDER else 99)
        else:
            merged.append(sorted(s, key=lambda t: TERM_ORDER.index(t) if t in TERM_ORDER else 99))
    for g, members in zip(groups, merged):
        names = [TERM_LABELS.get(m, m) for m in members]
        msg = "分量 " + "、".join(names) + " 的协方差结构线性相关，不能分别估计。"
        if set(members) >= {"lab", "device"}:
            msg += "每个实验室至少需要 2 台设备（且设备不在实验室间共用同一编号）才能分开实验室与设备方差。"
        if set(members) >= {"lab", "date"}:
            msg += "需要让实验室与日期通过共享测量连接起来。"
        if "device:date" in members and "device" in members:
            msg += "每台设备至少需要在 2 个日期测量。"
        issues.append(
            DesignIssue(
                members[0],
                "confounded",
                "error",
                msg,
                {"members": members, "coefficients": g["coefficients"]},
            )
        )

    for g in soft:
        rand = [m for m in g["members"] if m != "residual"]
        issues.append(
            DesignIssue(
                rand[0] if rand else "residual",
                "residual_tradeoff",
                "info",
                "该设计的单元内重复与 "
                + "、".join(TERM_LABELS.get(m, m) for m in rand)
                + " 在总变异上存在一个可互相抵消的方向（逐单元均值不变）；"
                "因为单元内有重复测量，残差方差仍由单元内平方和单独估计，两个分量都可识别。",
                {"members": g["members"], "coefficients": g["coefficients"]},
            )
        )

    issues.extend(_lab_device_warnings(rows, terms))

    # Informational: cells with one replicate.
    if design.df_within == 0:
        issues.append(
            DesignIssue(
                "residual",
                "no_replication",
                "warning",
                "没有同一单元内的重复测量：残差方差只能由模型结构（如最高阶交互）"
                "间接承担，解释时须谨慎。",
                {"cells": int(design.B)},
            )
        )
    return issues


def separability(rows: Sequence[Dict[str, object]], terms: Sequence[str]) -> Dict[str, object]:
    """Public separability report used by the UI and by the fit guard."""
    issues = diagnose(rows, terms)
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    notes = [i for i in issues if i.severity == "info"]
    return {
        "terms": [t for t in TERM_ORDER if t in terms],
        "ok": not errors,
        "errors": [i.to_dict() for i in errors],
        "warnings": [i.to_dict() for i in warnings],
        "notes": [i.to_dict() for i in notes],
        "confounded": sorted(
            {
                m
                for i in issues
                if i.code == "confounded"
                for m in i.detail.get("members", [])
                if m in TERM_ORDER
            },
            key=TERM_ORDER.index,
        ),
    }


# ---------------------------------------------------------------------------
# REML objective: cell-mean reduction with analytic derivatives
# ---------------------------------------------------------------------------

# The criterion is assembled on the cell means.  Every random term changes only
# through its block-level incidence Z_b (B x q_k), and the residual through
# D = diag(1/m_b).  For a trial variance vector the covariance is
#     V = sum_k theta_k Z_k Z_k' + sigma2 D.
# We never form V^{-1} by adding a constant: sigma2 is parametrised as
# exp(log sigma2), so V is strictly positive definite for every admissible
# theta, and the Cholesky factor is the only inverse-like object used.


def _reml_parts(
    theta: np.ndarray, log_sigma2: float, design: Design
) -> Tuple[float, Tuple[np.ndarray, ...]]:
    """-2 REML criterion and the pieces needed for its gradient.

    Returns ``(crit, (L, mu, r, w))`` where L is the Cholesky factor of V,
    mu the GLS mean, r the cell-mean residual and w = L^{-1} 1_B.
    """
    sigma2 = math.exp(log_sigma2)
    B = design.B
    V = np.diag(sigma2 / design.counts)
    for th, term in zip(theta, design.terms):
        if th > 0.0:
            Z = design.Zb[term]
            V += th * (Z @ Z.T)
    L = np.linalg.cholesky(V)
    logdet = 2.0 * float(np.sum(np.log(np.diag(L))))
    ones = np.ones(B)
    w = np.linalg.solve(L, ones)
    wy = np.linalg.solve(L, design.ybar)
    denom = float(w @ w)
    mu = float(w @ wy) / denom
    r = design.ybar - mu
    wr = np.linalg.solve(L, r)
    quad = float(wr @ wr)
    crit = (
        logdet
        + (design.n - 1) * math.log(2.0 * math.pi)
        + float(design.df_within) * log_sigma2
        + design.ss_within / sigma2
        + math.log(denom)
        + quad
    )
    return crit, (L, mu, r, w)


def _reml_crit(theta: np.ndarray, log_sigma2: float, design: Design) -> float:
    """-2 REML log-likelihood up to a constant, for the cell-mean reduction."""
    try:
        return _reml_parts(theta, log_sigma2, design)[0]
    except np.linalg.LinAlgError:
        return math.inf


@dataclass
class FitResult:
    terms: List[str]
    sigma2: Dict[str, float]
    residual: float
    mu: float
    crit: float
    x: np.ndarray
    n_obs: int
    n_cells: int
    cell_sizes: List[int]
    kkt: Dict[str, float]
    config: Dict[str, object] = field(default_factory=dict)

    @property
    def total(self) -> float:
        return float(sum(self.sigma2.values()) + self.residual)

    def to_dict(self) -> Dict[str, object]:
        tot = self.total
        comps = []
        for t in self.terms:
            v = float(self.sigma2[t])
            comps.append(
                {
                    "term": t,
                    "label": TERM_LABELS.get(t, t),
                    "short": TERM_SHORT.get(t, t),
                    "variance": v,
                    "sd": math.sqrt(max(v, 0.0)),
                    "share": v / tot if tot > 0 else 0.0,
                    "at_boundary": v <= 1e-12 * max(tot, 1e-12),
                    "kkt": self.kkt.get(t),
                }
            )
        comps.append(
            {
                "term": "residual",
                "label": TERM_LABELS["residual"],
                "short": "residual",
                "variance": float(self.residual),
                "sd": math.sqrt(max(self.residual, 0.0)),
                "share": float(self.residual) / tot if tot > 0 else 0.0,
                "at_boundary": False,
                "kkt": 0.0,
            }
        )
        return {
            "terms": self.terms,
            "components": comps,
            "mu": self.mu,
            "total_variance": tot,
            "total_sd": math.sqrt(max(tot, 0.0)),
            "reml_crit": self.crit,
            "n_obs": self.n_obs,
            "n_cells": self.n_cells,
            "cell_sizes": self.cell_sizes,
            "kkt": self.kkt,
            "config": self.config,
        }


def _fit_start(design: Design) -> Tuple[np.ndarray, float]:
    """Moment-based starting values (non-negative), residual from within cells."""
    scale = float(np.var(design.ybar)) if design.B > 1 else 1.0
    scale = max(scale, 1e-8)
    K = len(design.terms)
    theta = np.full(K, 0.15 * scale)
    if design.df_within > 0:
        sigma2 = max(design.ss_within / design.df_within, 1e-10 * scale)
    else:
        sigma2 = 0.25 * scale
    return theta, sigma2


class _Objective:
    """Criterion and exact gradient for L-BFGS-B.

    Variables: theta_1..theta_K (bounded >= 0) and log sigma2 (free).
    With V = sum_k theta_k M_k + sigma2 D and P = V^{-1} - V^{-1}1(1'V^{-1}1)^{-1}1'V^{-1}
    the -2 REML gradient is, for theta_k,  tr(V^{-1}M_k) - r' P M_k P r,
    and for log sigma2,  2[sigma2 tr(V^{-1}D) - df_within - SSw/sigma2
                            - sigma2 r' P D P r].
    The middle two terms in the brackets are the derivative of
    (N-B) log sigma2 + SSw/sigma2.  All traces are of the small cell-level V.
    """

    def __init__(self, design: Design):
        self.design = design
        self.K = len(design.terms)

    def parts(self, x: np.ndarray):
        theta = np.asarray(x[: self.K], dtype=float)
        log_s = float(x[self.K])
        sigma2 = math.exp(log_s)
        B = self.design.B
        V = np.diag(sigma2 / self.design.counts)
        for th, term in zip(theta, self.design.terms):
            if th > 0.0:
                Z = self.design.Zb[term]
                V += th * (Z @ Z.T)
        L = np.linalg.cholesky(V)
        logdet = 2.0 * float(np.sum(np.log(np.diag(L))))
        ones = np.ones(B)
        w = np.linalg.solve(L, ones)
        wy = np.linalg.solve(L, self.design.ybar)
        denom = float(w @ w)
        mu = float(w @ wy) / denom
        r = self.design.ybar - mu
        wr = np.linalg.solve(L, r)
        quad = float(wr @ wr)
        crit = (
            logdet
            + (self.design.n - 1) * math.log(2.0 * math.pi)
            + float(self.design.df_within) * log_s
            + self.design.ss_within / sigma2
            + math.log(denom)
            + quad
        )
        return crit, theta, log_s, sigma2, V, L, r

    def fg(self, x: np.ndarray) -> Tuple[float, np.ndarray]:
        try:
            crit, theta, log_s, sigma2, V, L, r = self.parts(x)
            if not math.isfinite(crit):
                return 1e12, np.zeros(self.K + 1)
            B = self.design.B
            ones = np.ones(B)
            # Reuse the Cholesky factor: solve rather than invert, and only
            # form the explicit inverse where a trace is actually needed.
            Vinv = np.linalg.inv(V)
        except np.linalg.LinAlgError:
            return 1e12, np.zeros(self.K + 1)
        c = Vinv @ ones
        denom = float(ones @ c)
        Vir = Vinv @ r
        grad = np.empty(self.K + 1)
        for k, term in enumerate(self.design.terms):
            Z = self.design.Zb[term]
            M = Z @ Z.T
            tr = float(np.sum(Vinv * M.T))
            trace_fix = float(c @ (M @ c)) / denom
            quad_k = float(Vir @ (M @ Vir))
            grad[k] = tr - trace_fix - quad_k
        Vd = np.diag(sigma2 / self.design.counts)
        tr_d = float(np.sum(Vinv * Vd.T))
        grad[self.K] = (
            tr_d
            - float(c @ (Vd @ c)) / denom
            - float(Vir @ (Vd @ Vir))
            + float(self.design.df_within)
            - self.design.ss_within / sigma2
        )
        return crit, grad


def _kkt(design: Design, theta: np.ndarray, log_sigma2: float) -> Dict[str, float]:
    """Normalised directional derivative of the criterion at each component.

    At an interior solution the value is ~0.  At the boundary (theta=0) a valid
    constrained optimum requires derivative >= 0; a clearly negative value means
    the optimiser stopped short and the component belongs in the model.
    """
    base = _reml_crit(theta, log_sigma2, design)
    out: Dict[str, float] = {}
    for k, term in enumerate(design.terms):
        step = max(theta[k], 1e-6) * 1e-3
        t = theta.copy()
        t[k] = theta[k] + step
        out[term] = float((_reml_crit(t, log_sigma2, design) - base) / step)
    return out


def fit_variance_components(
    rows: Sequence[Dict[str, object]],
    terms: Sequence[str] = ("lab", "device", "date"),
    *,
    max_iter: int = 500,
    max_cells: int = 1500,
) -> FitResult:
    """REML fit with non-negativity constraints; raises DesignError if confounded."""
    terms = [t for t in TERM_ORDER if t in terms]
    report = separability(rows, terms)
    if not report["ok"]:
        raise DesignError("；".join(e["message"] for e in report["errors"]))
    design = build_design(rows, terms)
    if design.B > max_cells:
        raise DesignError(
            f"测量单元数为 {design.B}，超过交互式拟合上限 {max_cells}。"
            "请先按日期或设备聚合，或使用脚本接口分批拟合。"
        )
    theta0, sigma20 = _fit_start(design)
    K = len(design.terms)
    log_s0 = math.log(max(sigma20, 1e-10))
    # Upper bound is a *search box*, sized from the data scale.  It keeps
    # L-BFGS-B out of regions where V is numerically singular; no constant is
    # ever added to the covariance itself.
    data_scale = max(float(np.var(np.asarray(design.ybar, dtype=float))), 1e-8)
    theta_hi = 1e4 * data_scale
    bounds = [(0.0, theta_hi)] * K + [(log_s0 - 30.0, log_s0 + 30.0)]
    obj = _Objective(design)

    solutions = []
    starts = [
        np.concatenate([theta0, [log_s0]]),
        np.concatenate([np.full(K, 1e-4 * max(float(np.var(design.ybar)), 1e-10)), [log_s0]]),
    ]
    for x0 in starts:
        res = minimize(
            obj.fg,
            x0,
            jac=True,
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": max_iter, "ftol": 1e-14, "gtol": 1e-10, "maxls": 50},
        )
        solutions.append(res)
    res = min(solutions, key=lambda r: r.fun)

    theta = np.maximum(np.asarray(res.x[:K], dtype=float), 0.0)
    log_s = float(res.x[K])
    scale = max(float(np.sum(theta)) + math.exp(log_s), 1e-12)
    theta[theta < 1e-11 * scale] = 0.0

    # Re-enter any boundary component with a clearly negative directional
    # derivative; the constrained optimum is not on the boundary in that case.
    kkt = _kkt(design, theta, log_s)
    bad = [k for k, t in enumerate(design.terms) if theta[k] == 0.0 and kkt.get(t, 0.0) < -1e-6]
    if bad:
        x2 = np.concatenate([theta, [log_s]])
        for k in bad:
            x2[k] = 1e-3 * scale
        res2 = minimize(
            obj.fg,
            x2,
            jac=True,
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": max_iter, "ftol": 1e-14, "gtol": 1e-10, "maxls": 50},
        )
        if res2.fun < res.fun - 1e-9:
            res = res2
            theta = np.maximum(np.asarray(res.x[:K], dtype=float), 0.0)
            log_s = float(res.x[K])
            theta[theta < 1e-11 * scale] = 0.0
            kkt = _kkt(design, theta, log_s)

    sigma2 = math.exp(log_s)
    crit, (L, mu, r, w) = _reml_parts(theta, log_s, design)
    grad = obj.fg(np.concatenate([theta, [log_s]]))[1]
    proj = grad.copy()
    for k in range(K):
        if theta[k] == 0.0:
            proj[k] = min(grad[k], 0.0)
    return FitResult(
        terms=list(design.terms),
        sigma2={t: float(v) for t, v in zip(design.terms, theta)},
        residual=float(sigma2),
        mu=float(mu),
        crit=float(crit),
        x=np.concatenate([theta, [log_s]]),
        n_obs=design.n,
        n_cells=design.B,
        cell_sizes=sorted(set(int(c) for c in design.counts)),
        kkt={k: float(v) for k, v in kkt.items()},
        config={
            "optimizer": "L-BFGS-B, jac=True (theta >= 0 bounded, log sigma2 free)",
            "iterations": int(res.nit),
            "converged": bool(res.success),
            "message": str(res.message),
            "projected_gradient": float(np.max(np.abs(proj))),
            "reml": "cell-mean reduction",
            "kkt_rule": "gradient of -2REML; boundary components flagged when d>0",
        },
    )


# ---------------------------------------------------------------------------
# Profile-likelihood intervals
# ---------------------------------------------------------------------------


def _profile_crit(design: Design, fitted: FitResult, k: int, value: float, start_x: np.ndarray) -> float:
    """Minimise the criterion with component k fixed at ``value``.

    The remaining components are bounded below by zero and the residual is
    log-parametrised, so the inner problem is the same constrained REML fit in
    one dimension fewer.  The gradient re-uses the masked version of the exact
    score (the fixed component simply contributes no free coordinate).
    """
    K = len(design.terms)
    idx = [i for i in range(K) if i != k]

    def expand(z: np.ndarray) -> np.ndarray:
        x = np.empty(K + 1)
        x[k] = value
        x[idx] = z[: K - 1]
        x[K] = z[K - 1]
        return x

    obj = _Objective(design)

    def fg(z: np.ndarray) -> Tuple[float, np.ndarray]:
        x = expand(z)
        crit, grad = obj.fg(x)
        if crit >= 1e11:
            return crit, np.zeros(len(z))
        return crit, grad[idx + [K]]

    z0 = np.concatenate([start_x[idx], [start_x[K]]])
    bounds = [(0.0, None)] * (K - 1) + [(start_x[K] - 30.0, start_x[K] + 30.0)]
    r = minimize(
        fg,
        z0,
        jac=True,
        method="L-BFGS-B",
        bounds=bounds,
        options={"maxiter": 200, "ftol": 1e-12, "gtol": 1e-8, "maxls": 40},
    )
    best = float(r.fun)
    # If the warm start did not converge, a second start from a small common
    # value guards against a flat ridge.
    if not r.success or best >= 1e11:
        z1 = np.concatenate(
            [np.full(K - 1, 1e-3 * max(float(np.var(design.ybar)), 1e-10)), [start_x[K] + 0.5]]
        )
        r1 = minimize(
            fg,
            z1,
            jac=True,
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 200, "ftol": 1e-12, "gtol": 1e-8, "maxls": 40},
        )
        best = min(best, float(r1.fun))
    return best


def profile_ci(
    rows: Sequence[Dict[str, object]],
    fitted: FitResult,
    term: str,
    *,
    level: float = 0.95,
) -> Dict[str, object]:
    """Profile-likelihood interval for one variance component.

    Interior estimates use the chi-square(1) cutoff.  A component at the zero
    boundary gets a one-sided interval with the 0.5*chi2(0)+0.5*chi2(1) cutoff;
    the lower end of any interval is clipped at zero, never negative.
    """
    design = build_design(rows, fitted.terms)
    K = len(design.terms)
    k = fitted.terms.index(term)
    theta = np.array([fitted.sigma2[t] for t in fitted.terms])
    start_x = fitted.x.copy()
    base = fitted.crit
    at_zero = theta[k] <= 1e-11 * max(fitted.total, 1e-12)
    crit = float(chi2.ppf(2 * level - 1, 1)) if at_zero else float(chi2.ppf(level, 1))

    def gap(v: float) -> float:
        return _profile_crit(design, fitted, k, v, start_x) - base - crit

    if at_zero:
        upper = 0.0
        if gap(0.0) > 0:
            hi = 0.05 * max(fitted.total, 1e-12)
            ok = False
            for _ in range(60):
                if gap(hi) > 0:
                    ok = True
                    break
                hi *= 1.7
            if ok:
                try:
                    upper = float(brentq(gap, 0.0, hi, xtol=1e-12 * max(1.0, hi), rtol=1e-10))
                except ValueError:
                    upper = hi
        return {
            "term": term,
            "estimate": 0.0,
            "lower": 0.0,
            "upper": float(upper),
            "level": level,
            "at_boundary": True,
            "lower_clipped": True,
            "method": "one-sided profile likelihood (0.5χ²₀ + 0.5χ²₁ cutoff)",
            "crit": crit,
        }

    upper = float("nan")
    hi = max(theta[k], 1e-12)
    step = max(0.1 * theta[k], 0.02 * max(fitted.total, 1e-12))
    for _ in range(60):
        if gap(hi) > 0:
            break
        hi += step
        step *= 1.7
    if gap(hi) > 0:
        try:
            upper = float(brentq(gap, theta[k], hi, xtol=1e-12 * max(1.0, hi), rtol=1e-10))
        except ValueError:
            upper = hi

    lower = 0.0
    lo = theta[k]
    step = max(0.2 * theta[k], 0.02 * max(fitted.total, 1e-12))
    found = False
    for _ in range(60):
        lo = max(lo - step, 0.0)
        if gap(lo) > 0:
            found = True
            break
        if lo == 0.0:
            break
        step *= 1.5
    if found:
        try:
            lower = float(brentq(gap, lo, theta[k], xtol=1e-12 * max(1.0, theta[k]), rtol=1e-10))
        except ValueError:
            lower = lo
    return {
        "term": term,
        "estimate": float(theta[k]),
        "lower": float(lower),
        "upper": float(upper),
        "level": level,
        "at_boundary": False,
        "lower_clipped": bool(lower == 0.0),
        "method": "profile likelihood (χ²₁ cutoff)",
        "crit": crit,
    }


def fit_with_intervals(
    rows: Sequence[Dict[str, object]],
    terms: Sequence[str],
    *,
    level: float = 0.95,
    include_residual: bool = True,
) -> Dict[str, object]:
    fit = fit_variance_components(rows, terms)
    intervals = []
    targets = list(fit.terms) + (["residual"] if include_residual else [])
    for t in targets:
        if t == "residual":
            intervals.append(_residual_interval(rows, fit, level=level))
        else:
            intervals.append(profile_ci(rows, fit, t, level=level))
    return {"fit": fit.to_dict(), "intervals": intervals}


def _residual_interval(rows, fit: FitResult, *, level: float = 0.95) -> Dict[str, object]:
    """Boundary-aware interval for the residual variance.

    With replicates the residual is identified by the within-cell sum of
    squares, whose exact scaled chi-square interval is used.  Without
    replicates the profile of the criterion is inverted numerically and the
    result is asymptotic.
    """
    design = build_design(rows, fit.terms)
    alpha = 1.0 - level
    if design.df_within > 0:
        lo = design.ss_within / float(chi2.ppf(1 - alpha / 2, design.df_within))
        hi = design.ss_within / float(chi2.ppf(alpha / 2, design.df_within))
        return {
            "term": "residual",
            "estimate": float(fit.residual),
            "lower": float(lo),
            "upper": float(hi) if design.df_within > 1 else float("nan"),
            "level": level,
            "at_boundary": False,
            "lower_clipped": True,
            "kind": "exact",
            "method": f"单元内平方和的卡方区间（df={design.df_within}）",
        }
    log_s = float(fit.x[-1])
    base = fit.crit
    crit = float(chi2.ppf(level, 1))

    def f(log_sv: float) -> float:
        return _reml_crit(np.array([fit.sigma2[t] for t in fit.terms]), log_sv, design) - base - crit

    hi = log_s + 1.0
    for _ in range(60):
        if f(hi) > 0:
            break
        hi += 0.7
    lo = log_s - 1.0
    for _ in range(60):
        if f(lo) > 0:
            break
        lo -= 0.7
    try:
        upper = math.exp(brentq(f, log_s, hi))
        lower = math.exp(brentq(f, lo, log_s))
    except ValueError:
        upper = float("nan")
        lower = 0.0
    return {
        "term": "residual",
        "estimate": float(fit.residual),
        "lower": float(lower),
        "upper": float(upper),
        "level": level,
        "at_boundary": False,
        "lower_clipped": True,
        "kind": "asymptotic",
        "method": "剖面似然（无重复测量，渐近）",
    }


# ---------------------------------------------------------------------------
# Prediction / precision of a design given variance components
# ---------------------------------------------------------------------------


def _gls_total_variance(design: Design, theta: np.ndarray, sigma2: float) -> float:
    """Var of the GLS grand mean for a given variance vector."""
    B = design.B
    V = np.diag(sigma2 / design.counts)
    for th, term in zip(theta, design.terms):
        if th > 0:
            Z = design.Zb[term]
            V += th * (Z @ Z.T)
    L = np.linalg.cholesky(V)
    w = np.linalg.solve(L, np.ones(B))
    return 1.0 / float(w @ w)


# ---------------------------------------------------------------------------
# Balanced-design hand check (ANOVA moments)
# ---------------------------------------------------------------------------


def balanced_anova(rows: Sequence[Dict[str, object]], terms: Sequence[str]) -> Dict[str, object]:
    """Closed-form ANOVA estimates for the two reference balanced layouts.

    Supported layouts (both require an equal number ``n`` of replicates in every
    measurement cell):

    * **nested** - ``lab > device``, with every device measured on exactly one
      date.  Date is a pure replicate label here; the model has lab and
      device(lab) effects only.
    * **crossed** - ``lab x date`` with every lab x date combination present and
      one device per combination.  The model has lab, date and (optionally)
      lab:date effects; devices act as replicate labels.

    These are the layouts whose EMS are unambiguous; anything else returns
    ``balanced: False`` with a reason instead of a misleading number.
    """
    terms = [t for t in TERM_ORDER if t in terms]
    if not rows:
        return {"balanced": False, "reason": "没有记录"}
    reps: Dict[Tuple[str, str, str], int] = {}
    for r in rows:
        k = (r["lab"], r["device"], r["date"])
        reps[k] = reps.get(k, 0) + 1
    if len(set(reps.values())) != 1:
        return {"balanced": False, "reason": "各测量单元的重复次数不一致"}
    n = next(iter(reps.values()))
    cells = list(reps.keys())
    labs = sorted({c[0] for c in cells})
    dates = sorted({c[2] for c in cells})
    devs_by_lab = {l: sorted({c[1] for c in cells if c[0] == l}) for l in labs}
    if len({len(d) for d in devs_by_lab.values()}) != 1:
        return {"balanced": False, "reason": "每个实验室的设备数不一致"}
    b_dev = len(next(iter(devs_by_lab.values())))

    vals: Dict[Tuple[str, str, str], List[float]] = {}
    for r in rows:
        vals.setdefault((r["lab"], r["device"], r["date"]), []).append(float(r["value"]))
    mean = {c: float(np.mean(v)) for c, v in vals.items()}
    y = np.array([float(r["value"]) for r in rows], dtype=float)
    ss_within = float(sum(np.sum((np.array(v) - np.mean(v)) ** 2) for v in vals.values()))
    df_e = len(y) - len(cells)
    ms_e = ss_within / df_e if df_e > 0 else float("nan")

    # ---- nested: lab > device, one date per device -----------------------
    if set(terms) <= {"lab", "device"} and "device" in terms:
        device_dates = {}
        for c in cells:
            device_dates.setdefault((c[0], c[1]), set()).add(c[2])
        if any(len(v) > 1 for v in device_dates.values()):
            return {
                "balanced": False,
                "reason": "设备跨多个日期测量：这是交叉结构，不能用纯嵌套的 EMS 手算",
            }
        a = len(labs)
        dev_mean = {(l, d): float(np.mean([mean[(l, d, dt)] for dt in dates if (l, d, dt) in mean])) for l in labs for d in devs_by_lab[l]}
        grand = float(np.mean([dev_mean[(l, d)] for l in labs for d in devs_by_lab[l]]))
        lab_mean = {l: float(np.mean([dev_mean[(l, d)] for d in devs_by_lab[l]])) for l in labs}
        ss_lab = n * b_dev * sum((lab_mean[l] - grand) ** 2 for l in labs)
        ss_dev = n * sum((dev_mean[(l, d)] - lab_mean[l]) ** 2 for l in labs for d in devs_by_lab[l])
        df_lab = a - 1
        df_dev = a * (b_dev - 1)
        ms_lab = ss_lab / df_lab
        ms_dev = ss_dev / df_dev
        est = {
            "residual": ms_e,
            "device": (ms_dev - ms_e) / n,
            "lab": (ms_lab - ms_dev) / (n * b_dev),
        }
        return {
            "balanced": True,
            "layout": "nested",
            "n_reps": n,
            "device_dates": "每台设备只在一个日期测量",
            "anova": [
                {"source": "实验室", "df": df_lab, "ss": ss_lab, "ms": ms_lab,
                 "ems": "σ²e + n·B·σ²device + n·B·D·σ²lab"},
                {"source": "设备(实验室)", "df": df_dev, "ss": ss_dev, "ms": ms_dev,
                 "ems": "σ²e + n·σ²device"},
                {"source": "重复测量", "df": df_e, "ss": ss_within, "ms": ms_e, "ems": "σ²e"},
            ],
            "estimates": {k: float(v) for k, v in est.items()},
            "negative_allowed": True,
        }

    # ---- crossed: lab x date (optionally with interaction) ---------------
    if set(terms) <= {"lab", "date", "lab:date"} and "date" in terms:
        for l in labs:
            for d in dates:
                if not all((l, dd, d) in mean for dd in devs_by_lab[l]):
                    return {"balanced": False, "reason": "实验室×日期组合不完整（每台设备都要在全部日期测量）"}
        a, b = len(labs), len(dates)
        n_eff = n * b_dev  # observations per lab x date (devices act as reps)
        lab_date = {
            (l, d): float(np.mean([mean[(l, dd, d)] for dd in devs_by_lab[l]]))
            for l in labs
            for d in dates
        }
        grand = float(np.mean([lab_date[(l, d)] for l in labs for d in dates]))
        lab_m = {l: float(np.mean([lab_date[(l, d)] for d in dates])) for l in labs}
        date_m = {d: float(np.mean([lab_date[(l, d)] for l in labs])) for d in dates}
        ss_lab = n_eff * b * sum((lab_m[l] - grand) ** 2 for l in labs)
        ss_date = n_eff * a * sum((date_m[d] - grand) ** 2 for d in dates)
        ss_int = n_eff * sum(
            (lab_date[(l, d)] - lab_m[l] - date_m[d] + grand) ** 2
            for l in labs
            for d in dates
        )
        ss_e = ss_within
        df_lab, df_date = a - 1, b - 1
        df_int = (a - 1) * (b - 1)
        ms_lab = ss_lab / df_lab
        ms_date = ss_date / df_date
        ms_int = ss_int / df_int
        if "lab:date" in terms:
            est = {
                "residual": ms_e,
                "lab:date": (ms_int - ms_e) / n_eff,
                "lab": (ms_lab - ms_int) / (n_eff * b),
                "date": (ms_date - ms_int) / (n_eff * a),
            }
            rows_out = [
                {"source": "实验室", "df": df_lab, "ss": ss_lab, "ms": ms_lab,
                 "ems": "σ²e + n·B·σ²lab:date + n·B·σ²date + n·D·B·σ²lab"},
                {"source": "日期", "df": df_date, "ss": ss_date, "ms": ms_date,
                 "ems": "σ²e + n·A·σ²lab:date + n·A·σ²date"},
                {"source": "实验室×日期", "df": df_int, "ss": ss_int, "ms": ms_int,
                 "ems": "σ²e + n·σ²lab:date"},
                {"source": "重复测量", "df": df_e, "ss": ss_e, "ms": ms_e, "ems": "σ²e"},
            ]
        elif "date" in terms:
            est = {
                "residual": ms_e,
                "date": (ms_date - ms_e) / (n_eff * a),
                "lab": (ms_lab - ms_e) / (n_eff * b),
            }
            rows_out = [
                {"source": "实验室", "df": df_lab, "ss": ss_lab, "ms": ms_lab,
                 "ems": "σ²e + n·B·σ²date + n·D·B·σ²lab"},
                {"source": "日期", "df": df_date, "ss": ss_date, "ms": ms_date,
                 "ems": "σ²e + n·A·σ²date"},
                {"source": "重复测量", "df": df_e, "ss": ss_e, "ms": ms_e, "ems": "σ²e"},
            ]
        else:
            return {"balanced": False, "reason": "交叉结构需要日期项"}
        return {
            "balanced": True,
            "layout": "crossed",
            "n_reps": n_eff,
            "anova": rows_out,
            "estimates": {k: float(v) for k, v in est.items()},
            "negative_allowed": True,
        }

    return {"balanced": False, "reason": "该随机项组合没有内置的手算公式"}
