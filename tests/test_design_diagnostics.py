"""Design diagnostics: disconnected lab-date graphs and structurally confounded
components must be reported instead of being silently estimated."""
from __future__ import annotations

import pytest

from metrology import design as D
from metrology.ingest import _normalized_preset, simulate
from metrology.variance import (
    DesignError,
    clean_rows,
    fit_variance_components,
    separability,
)


def _rows(preset, seed=1):
    rows, _ = simulate(_normalized_preset(preset), seed)
    return clean_rows(rows)


def test_connected_design_has_no_separability_errors():
    rows = _rows("crossed_lab_date")
    report = separability(rows, ["lab", "date", "lab:date"])
    assert report["ok"]
    assert report["errors"] == []
    assert D.design_graph(rows)["components"].__len__() == 1


def test_disconnected_design_blocks_fit_and_names_the_components():
    rows = _rows("crossed_disconnected")
    report = separability(rows, ["lab", "date", "lab:date"])
    assert not report["ok"]
    codes = {e["code"] for e in report["errors"]}
    assert "disconnected" in codes
    assert "confounded" in codes
    assert "lab:date" in report["confounded"]

    graph = D.design_graph(rows)
    assert len(graph["components"]) > 1
    # The reported groups must cover every lab exactly once.
    grouped_labs = sorted(l for g in graph["components"] for l in g["labs"])
    assert grouped_labs == sorted({r["lab"] for r in rows})

    with pytest.raises(DesignError) as exc:
        fit_variance_components(rows, ["lab", "date", "lab:date"])
    assert "不连通" in str(exc.value)


def test_single_device_per_lab_confounds_lab_and_device():
    """devices nested in labs with one device each: lab and device absorb the
    same level, so they cannot be separated."""
    rows = _rows("crossed_lab_date")  # one device per lab
    report = separability(rows, ["lab", "device", "date"])
    assert not report["ok"]
    assert any("lab" in e["detail"].get("members", []) and "device" in e["detail"].get("members", [])
               for e in report["errors"])


def test_two_devices_per_lab_separates_lab_and_device():
    rows = _rows("nested_clean")
    report = separability(rows, ["lab", "device"])
    assert report["ok"]
    assert report["errors"] == []


def test_no_replication_is_a_warning_not_an_error():
    rows, _ = simulate(_normalized_preset("nested_clean"), 2)
    rows = clean_rows(rows)
    # Keep one observation per lab/device/date cell.
    seen = set()
    thinned = []
    for r in rows:
        key = (r["lab"], r["device"], r["date"])
        if key in seen:
            continue
        seen.add(key)
        thinned.append(r)
    report = separability(thinned, ["lab", "device"])
    codes = {w["code"] for w in report["warnings"]}
    assert "no_replication" in codes
    assert report["ok"]  # residual is still identifiable through replication-


def test_gram_dependency_reports_hard_vs_soft():
    """With within-cell replication the residual/random-term trade-off is a
    non-fatal note, not an error."""
    rows = _rows("nested_clean")
    report = separability(rows, ["lab", "device"])
    assert report["ok"]
    assert all(n["code"] in {"residual_tradeoff"} for n in report["notes"])


def test_single_lab_is_flagged_as_a_warning():
    """One lab is estimable in principle but rests on a single draw; the app
    must warn rather than present it as a well-supported component."""
    rows = _rows("nested_clean")
    single = [r for r in rows if r["lab"] == "L01"]
    report = separability(single, ["lab", "device"])
    assert report["ok"]
    assert any(w["code"] == "partial" and w["term"] == "lab" for w in report["warnings"])


def test_design_graph_edges_count_measurements_and_devices():
    rows = _rows("nested_clean")
    graph = D.design_graph(rows)
    per_lab = {}
    for e in graph["edges"]:
        per_lab[e["source"]] = per_lab.get(e["source"], 0) + e["measurements"]
    assert all(v > 0 for v in per_lab.values())
    assert sum(e["measurements"] for e in graph["edges"]) == len(rows)
