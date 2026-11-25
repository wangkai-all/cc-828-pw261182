"""Flask app: metrology variance-component studio."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, List

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from . import analysis as A
from . import ingest as I
from . import store
from .variance import TERM_LABELS, TERM_ORDER, DesignError, clean_rows

BASE = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(BASE / "templates"), static_folder=str(BASE / "static"))
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
app.json.ensure_ascii = False


def _project(pid: str) -> Dict:
    try:
        return store.load_project(pid)
    except store.ProjectNotFound:
        abort(404)


def _terms_from(doc: Dict) -> List[str]:
    return [t for t in TERM_ORDER if t in doc.get("terms", [])]


@app.get("/")
def index():
    return render_template(
        "index.html",
        projects=store.list_projects(),
        presets=I.preset_catalog(),
        terms=TERM_LABELS,
        order=TERM_ORDER,
    )


@app.post("/projects")
def create():
    name = request.form.get("name", "").strip() or "未命名计量研究"
    doc = store.create_project(name)
    preset_name = request.form.get("preset", "").strip()
    if preset_name:
        try:
            preset = I._normalized_preset(preset_name)
            rows, meta = I.simulate(preset, 20261125)
            rows = clean_rows(rows)
        except (I.IngestError, DesignError):
            return redirect(url_for("project", pid=doc["id"]))
        doc["rows"] = rows
        doc["terms"] = [t for t in TERM_ORDER if t in preset["terms"]]
        doc["source"] = {
            "kind": "simulation",
            "preset": preset_name,
            "label": preset["label"],
            "seed": 20261125,
            "truth": meta["truth"],
        }
        store.save_project(doc)
        analyze = A.analyze_design(rows, doc["terms"])
        store.log_event(
            doc["id"],
            "preset_loaded",
            {"preset": preset_name, "seed": 20261125, "terms": doc["terms"], "truth": meta["truth"]},
            {"rows": len(rows), "separable": analyze["separable"], "fingerprint": I.fingerprint(rows)},
        )
        return redirect(url_for("project", pid=doc["id"]) + "#design")
    return redirect(url_for("project", pid=doc["id"]))


@app.get("/p/<pid>")
def project(pid: str):
    doc = _project(pid)
    return render_template(
        "project.html",
        project=doc,
        presets=I.preset_catalog(),
        term_labels=TERM_LABELS,
        term_order=TERM_ORDER,
    )


@app.post("/p/<pid>/delete")
def remove(pid: str):
    _project(pid)
    store.delete_project(pid)
    return redirect(url_for("index"))


@app.post("/api/p/<pid>/design")
def set_design(pid: str):
    doc = _project(pid)
    payload = request.get_json(force=True, silent=True) or {}
    try:
        if "csv" in payload and payload["csv"].strip():
            raw = I.parse_csv(payload["csv"])
        elif "rows" in payload:
            raw = payload["rows"]
        else:
            raise DesignError("没有提供设计数据。")
        rows = clean_rows(raw)
        terms = [t for t in TERM_ORDER if t in payload.get("terms", doc.get("terms", []))]
        if not terms:
            raise DesignError("至少选择一个随机项。")
    except (DesignError, I.IngestError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    doc["rows"] = rows
    doc["terms"] = terms
    doc["source"] = payload.get("source") or doc.get("source")
    doc["latest"].pop("fit", None)
    doc["latest"].pop("budget", None)
    doc["latest"].pop("validation", None)
    store.save_project(doc)
    analyze = A.analyze_design(rows, terms)
    store.log_event(
        pid,
        "design_loaded",
        {"source": doc.get("source"), "terms": terms},
        {
            "rows": len(rows),
            "cells": analyze["cell_count"],
            "labs": analyze["labs"],
            "devices": analyze["devices"],
            "dates": analyze["dates"],
            "separable": analyze["separable"],
            "fingerprint": I.fingerprint(rows),
        },
    )
    return jsonify({"ok": True, "analyze": analyze, "fingerprint": I.fingerprint(rows)})


@app.post("/api/p/<pid>/preset")
def load_preset(pid: str):
    doc = _project(pid)
    payload = request.get_json(force=True, silent=True) or {}
    name = payload.get("preset")
    seed = int(payload.get("seed", 20261125))
    try:
        preset = I._normalized_preset(name)
        rows, meta = I.simulate(preset, seed)
        rows = clean_rows(rows)
    except (I.IngestError, DesignError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    terms = [t for t in TERM_ORDER if t in preset["terms"]]
    doc["rows"] = rows
    doc["terms"] = terms
    doc["source"] = {"kind": "simulation", "preset": name, "label": preset["label"], "seed": seed, "truth": meta["truth"]}
    doc["latest"] = {}
    store.save_project(doc)
    analyze = A.analyze_design(rows, terms)
    store.log_event(
        pid,
        "preset_loaded",
        {"preset": name, "seed": seed, "truth": meta["truth"], "terms": terms},
        {"rows": len(rows), "separable": analyze["separable"], "fingerprint": I.fingerprint(rows)},
    )
    return jsonify({"ok": True, "analyze": analyze, "truth": meta["truth"], "fingerprint": I.fingerprint(rows)})


@app.post("/api/p/<pid>/terms")
def set_terms(pid: str):
    doc = _project(pid)
    payload = request.get_json(force=True, silent=True) or {}
    terms = [t for t in TERM_ORDER if t in payload.get("terms", [])]
    if not terms:
        return jsonify({"ok": False, "error": "至少选择一个随机项。"}), 400
    doc["terms"] = terms
    doc["latest"].pop("fit", None)
    doc["latest"].pop("budget", None)
    doc["latest"].pop("validation", None)
    store.save_project(doc)
    return jsonify({"ok": True, "analyze": A.analyze_design(doc["rows"], terms)})


@app.get("/api/p/<pid>/analyze")
def analyze(pid: str):
    doc = _project(pid)
    return jsonify(A.analyze_design(doc["rows"], _terms_from(doc)))


@app.post("/api/p/<pid>/fit")
def fit(pid: str):
    doc = _project(pid)
    if not doc["rows"]:
        return jsonify({"ok": False, "error": "请先导入或生成设计。"}), 400
    payload = request.get_json(force=True, silent=True) or {}
    level = float(payload.get("level", 0.95))
    try:
        result = A.fit_design(doc["rows"], _terms_from(doc), level=level)
    except DesignError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    doc["latest"]["fit"] = {"ok": result.get("ok"), "result": result}
    store.save_project(doc)
    if result.get("ok"):
        store.log_event(
            pid,
            "fit",
            {"terms": doc["terms"], "level": level, "fingerprint": I.fingerprint(doc["rows"])},
            {
                "components": {c["term"]: round(c["variance"], 8) for c in result["fit"]["components"]},
                "mu": round(result["fit"]["mu"], 8),
                "reml_crit": round(result["fit"]["reml_crit"], 6),
                "intervals": [
                    {
                        "term": i["term"],
                        "lower": None if not math.isfinite(i["lower"]) else round(i["lower"], 8),
                        "upper": None if not math.isfinite(i["upper"]) else round(i["upper"], 8),
                        "at_boundary": i["at_boundary"],
                    }
                    for i in result["intervals"]
                ],
            },
        )
    return jsonify(result)


@app.post("/api/p/<pid>/settings")
def settings(pid: str):
    doc = _project(pid)
    payload = request.get_json(force=True, silent=True) or {}
    keys = ("budget", "cost_lab", "cost_device", "cost_repeat", "target_sd")
    for k in keys:
        if k not in payload:
            continue
        try:
            value = float(payload[k])
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": f"{k} 必须是数字。"}), 400
        # A non-positive target means "no target set".
        doc["settings"][k] = max(0.0, value)
    store.save_project(doc)
    return jsonify({"ok": True, "settings": doc["settings"]})


@app.post("/api/p/<pid>/budget")
def budget(pid: str):
    doc = _project(pid)
    if not doc["rows"]:
        return jsonify({"ok": False, "error": "请先导入或生成设计。"}), 400
    payload = request.get_json(force=True, silent=True) or {}
    s = doc["settings"].copy()
    for k in ("budget", "cost_lab", "cost_device", "cost_repeat", "target_sd"):
        if k in payload:
            try:
                s[k] = max(0.0, float(payload[k]))
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": f"{k} 必须是数字。"}), 400
    doc["settings"] = {**s}
    try:
        result = A.budget_scenarios(doc["rows"], _terms_from(doc), s)
    except DesignError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    doc["latest"]["budget"] = {"ok": True, "result": result}
    store.save_project(doc)
    best = result.get("best")
    store.log_event(
        pid,
        "budget",
        {
            "budget": s["budget"],
            "prices": {k: s[k] for k in ("cost_lab", "cost_device", "cost_repeat")},
            "fingerprint": I.fingerprint(doc["rows"]),
        },
        {
            "n_affordable": result["n_affordable"],
            "best": None
            if not best
            else {
                "add_labs": best["add_labs"],
                "add_devices_per_lab": best["add_devices_per_lab"],
                "add_reps": best["add_reps"],
                "cost": round(best["cost"], 2),
                "mean_sd": round(best["mean_sd"], 6),
            },
        },
    )
    return jsonify(result)


@app.post("/api/p/<pid>/validate")
def validate(pid: str):
    doc = _project(pid)
    if not doc["rows"]:
        return jsonify({"ok": False, "error": "请先导入或生成设计。"}), 400
    payload = request.get_json(force=True, silent=True) or {}
    plan = {
        "add_labs": int(payload.get("add_labs", 0)),
        "add_devices_per_lab": int(payload.get("add_devices_per_lab", 0)),
        "add_reps": int(payload.get("add_reps", 0)),
        "add_dates": int(payload.get("add_dates", 0)),
    }
    n_sim = int(payload.get("n_sim", 24))
    seed = int(payload.get("seed", 20261125))
    try:
        result = A.budget_and_validate(
            doc["rows"], _terms_from(doc), doc["settings"], plan, n_sim=n_sim, seed=seed
        )
    except DesignError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    doc["latest"]["validation"] = {"ok": True, "result": result, "plan": plan, "seed": seed}
    store.save_project(doc)
    store.log_event(
        pid,
        "validation",
        {"plan": plan, "seed": seed, "n_sim": n_sim, "fingerprint": I.fingerprint(doc["rows"])},
        {
            "predicted_sd": round(result["predicted_sd"], 6),
            "observed_sd": None if result["observed_sd"] is None else round(result["observed_sd"], 6),
            "cost": round(result["cost"], 2),
            "within_budget": result["within_budget"],
        },
    )
    return jsonify(result)


@app.get("/api/p/<pid>/export.csv")
def export_csv(pid: str):
    doc = _project(pid)
    csv_text = I.rows_to_csv(doc["rows"])
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{pid}-design.csv"'},
    )


@app.get("/api/p/<pid>/export.json")
def export_json(pid: str):
    doc = _project(pid)
    payload = json.dumps(doc, ensure_ascii=False, indent=2, default=str)
    return Response(
        payload,
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{pid}-project.json"'},
    )


@app.get("/api/p/<pid>/state")
def state(pid: str):
    doc = _project(pid)
    return jsonify(
        {
            "ok": True,
            "rows": doc["rows"],
            "terms": doc["terms"],
            "settings": doc["settings"],
            "source": doc.get("source"),
            "latest": doc.get("latest", {}),
            "history": doc.get("history", []),
            "fingerprint": I.fingerprint(doc["rows"]) if doc["rows"] else None,
        }
    )


@app.post("/api/p/<pid>/demo")
def demo(pid: str):
    """One-click full walkthrough: clean nested simulation, fit, budget, validation."""
    doc = _project(pid)
    preset = I._normalized_preset("nested_clean")
    rows, meta = I.simulate(preset, 20261125)
    rows = clean_rows(rows)
    doc["rows"] = rows
    doc["terms"] = list(preset["terms"])
    doc["source"] = {
        "kind": "simulation",
        "preset": "nested_clean",
        "label": preset["label"],
        "seed": 20261125,
        "truth": meta["truth"],
    }
    doc["settings"] = {"budget": 5000.0, "cost_lab": 1200.0, "cost_device": 250.0, "cost_repeat": 40.0}
    doc["latest"] = {}
    store.save_project(doc)
    store.log_event(
        pid,
        "preset_loaded",
        {"preset": "nested_clean", "seed": 20261125, "truth": meta["truth"]},
        {"rows": len(rows), "fingerprint": I.fingerprint(rows)},
    )
    fit = A.fit_design(rows, doc["terms"])
    # Reload after each log so the in-memory copy never overwrites appended
    # audit events with a stale history list.
    doc = store.load_project(pid)
    doc["latest"]["fit"] = {"ok": True, "result": fit}
    store.save_project(doc)
    store.log_event(
        pid,
        "fit",
        {"terms": doc["terms"], "fingerprint": I.fingerprint(rows)},
        {"components": {c["term"]: round(c["variance"], 6) for c in fit["fit"]["components"]}},
    )
    budget = A.budget_scenarios(rows, doc["terms"], doc["settings"])
    doc = store.load_project(pid)
    doc["latest"]["budget"] = {"ok": True, "result": budget}
    store.save_project(doc)
    best = budget.get("best") or {}
    store.log_event(
        pid,
        "budget",
        {"budget": 5000, "prices": {"cost_lab": 1200, "cost_device": 250, "cost_repeat": 40}},
        {"n_affordable": budget["n_affordable"], "best_mean_sd": best.get("mean_sd")},
    )
    if best:
        plan = {
            "add_labs": best["add_labs"],
            "add_devices_per_lab": best["add_devices_per_lab"],
            "add_reps": best["add_reps"],
            "add_dates": best["add_dates"],
        }
        val = A.budget_and_validate(rows, doc["terms"], doc["settings"], plan, n_sim=24, seed=20261125)
        doc = store.load_project(pid)
        doc["latest"]["validation"] = {"ok": True, "result": val, "plan": plan, "seed": 20261125}
        store.save_project(doc)
        store.log_event(
            pid,
            "validation",
            {"plan": plan, "seed": 20261125, "n_sim": 24},
            {"predicted_sd": round(val["predicted_sd"], 6), "observed_sd": val["observed_sd"]},
        )
    return jsonify({"ok": True})


@app.errorhandler(404)
def not_found(_):
    return render_template("not_found.html"), 404
