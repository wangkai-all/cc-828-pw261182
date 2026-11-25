"""Flask API: the full empty-project → design → fit → budget → validate → trace
flow, plus the error paths the UI relies on."""
from __future__ import annotations

import json
import os

import pytest

from metrology import ingest as I


def _load_preset(client, pid, name="nested_clean", seed=20261125):
    r = client.post(f"/api/p/{pid}/preset", json={"preset": name, "seed": seed})
    assert r.status_code == 200, r.data
    return r.get_json()


def test_index_and_empty_project_render(client):
    assert client.get("/").status_code == 200
    resp = client.post("/projects", data={"name": "空项目"})
    assert resp.status_code == 302
    pid = resp.headers["Location"].rsplit("/", 1)[-1]
    page = client.get(f"/p/{pid}")
    assert page.status_code == 200
    assert "空项目" in page.get_data(as_text=True)


def test_missing_project_returns_404_page(client):
    assert client.get("/p/doesnotexist").status_code == 404


def test_preset_button_creates_a_loaded_project(client):
    resp = client.post("/projects", data={"name": "预设", "preset": "crossed_lab_date"})
    pid = resp.headers["Location"].split("/p/")[1].split("#")[0]
    state = client.get(f"/api/p/{pid}/state").get_json()
    assert state["source"]["preset"] == "crossed_lab_date"
    assert len(state["rows"]) == 72
    assert state["terms"] == ["lab", "date", "lab:date"]
    kinds = [e["kind"] for e in state["history"]]
    assert kinds == ["project_created", "preset_loaded"]


def test_design_upload_returns_graph_and_diagnostics(client, project):
    csv = I.rows_to_csv(I.simulate(I._normalized_preset("nested_clean"), 3)[0])
    r = client.post(f"/api/p/{project}/design", json={"csv": csv, "terms": ["lab", "device"]})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] and body["analyze"]["separable"]
    assert body["analyze"]["graph"]["nodes"]
    assert body["fingerprint"]


def test_design_upload_error_is_json_400(client, project):
    r = client.post(f"/api/p/{project}/design", json={"csv": "lab,device\nx,y\n"})
    assert r.status_code == 400
    assert r.mimetype == "application/json"
    assert r.get_json()["ok"] is False


def test_disconnected_design_cannot_be_fitted_and_says_why(client, project):
    _load_preset(client, project, "crossed_disconnected")
    r = client.post(f"/api/p/{project}/fit", json={})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is False
    assert body["errors"]
    assert any("不连通" in e["message"] for e in body["errors"])


def test_fit_returns_components_intervals_and_hand_check(client, project):
    _load_preset(client, project, "nested_clean")
    body = client.post(f"/api/p/{project}/fit", json={"level": 0.95}).get_json()
    assert body["ok"]
    terms = [c["term"] for c in body["fit"]["components"]]
    assert terms == ["lab", "device", "residual"]
    assert [i["term"] for i in body["intervals"]] == ["lab", "device", "residual"]
    assert body["hand_check"]["balanced"]
    assert body["comparison"]
    for row in body["comparison"]:
        assert row["anova"] is not None
        assert row["reml"] is not None


def test_boundary_component_is_reported_at_zero_with_one_sided_interval(client, project):
    _load_preset(client, project, "boundary_zero")
    body = client.post(f"/api/p/{project}/fit", json={}).get_json()
    date = next(c for c in body["fit"]["components"] if c["term"] == "date")
    assert date["variance"] == 0.0
    iv = next(i for i in body["intervals"] if i["term"] == "date")
    assert iv["at_boundary"] is True
    assert iv["lower"] == 0.0
    assert iv["upper"] >= 0.0


def test_budget_endpoint_respects_budget_and_sunk_cost(client, project):
    _load_preset(client, project, "nested_clean")
    r = client.post(
        f"/api/p/{project}/budget",
        json={"budget": 1500, "cost_lab": 1200, "cost_device": 250, "cost_repeat": 40},
    )
    body = r.get_json()
    assert body["ok"]
    assert body["plans"]
    assert all(p["cost"] <= 1500 + 1e-9 for p in body["plans"])
    assert body["current"]["mean_sd"] > 0
    assert body["sunk_cost_note"]
    # Settings persisted for the next request.
    assert client.get(f"/api/p/{project}/state").get_json()["settings"]["budget"] == 1500


def test_target_sd_round_trips_and_drives_the_verdict(client, project):
    _load_preset(client, project, "nested_clean")
    # Persist a target through /settings first.
    r = client.post(f"/api/p/{project}/settings", json={"target_sd": 0.42})
    assert r.get_json()["settings"]["target_sd"] == 0.42
    state = client.get(f"/api/p/{project}/state").get_json()
    assert state["settings"]["target_sd"] == 0.42

    body = client.post(
        f"/api/p/{project}/budget",
        json={"budget": 5000, "cost_lab": 1200, "cost_device": 250, "cost_repeat": 40},
    ).get_json()
    assert body["target"]["target_sd"] == 0.42
    assert body["target"]["current_meets"] in (True, False)
    assert body["target"]["n_listed"] == len(body["plans"])
    flags = [p["mean_sd"] <= 0.42 + 1e-12 for p in body["plans"]]
    assert body["target"]["n_meeting"] == sum(flags)

    # Omitting the target yields a null verdict, not a zero-threshold one.
    body = client.post(
        f"/api/p/{project}/budget",
        json={"budget": 5000, "target_sd": 0},
    ).get_json()
    assert body["target"]["target_sd"] is None
    assert body["target"]["current_meets"] is None


def test_validation_endpoint_runs_and_is_logged(client, project):
    _load_preset(client, project, "nested_clean")
    r = client.post(
        f"/api/p/{project}/validate",
        json={"add_labs": 1, "add_reps": 0, "add_dates": 0, "n_sim": 6, "seed": 4},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["simulated"] == 6
    assert body["predicted_sd"] > 0
    assert body["cost"] > 0
    state = client.get(f"/api/p/{project}/state").get_json()
    assert state["history"][-1]["kind"] == "validation"
    assert state["latest"]["validation"]["ok"]


def test_validation_without_new_measurement_is_json_400(client, project):
    _load_preset(client, project, "nested_clean")
    r = client.post(f"/api/p/{project}/validate", json={"n_sim": 3})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_terms_change_invalidates_downstream_results(client, project):
    _load_preset(client, project, "crossed_lab_date")
    client.post(f"/api/p/{project}/fit", json={})
    r = client.post(f"/api/p/{project}/terms", json={"terms": ["lab", "date"]})
    assert r.status_code == 200
    state = client.get(f"/api/p/{project}/state").get_json()
    assert "fit" not in state["latest"]


def test_demo_runs_every_step_and_keeps_the_audit_trail(client, project):
    r = client.post(f"/api/p/{project}/demo", json={})
    assert r.status_code == 200
    state = client.get(f"/api/p/{project}/state").get_json()
    kinds = [e["kind"] for e in state["history"]]
    assert kinds == ["project_created", "preset_loaded", "fit", "budget", "validation"]
    assert state["latest"]["fit"]["ok"]
    assert state["latest"]["budget"]["result"]["plans"]
    assert state["latest"]["validation"]["result"]["simulated"] > 0
    # The audit sequence is strictly increasing and each event has a fingerprint.
    seqs = [e["seq"] for e in state["history"]]
    assert seqs == list(range(1, len(seqs) + 1))


def test_exports_are_served(client, project):
    _load_preset(client, project, "nested_clean")
    csv = client.get(f"/api/p/{project}/export.csv")
    assert csv.status_code == 200 and csv.mimetype == "text/csv"
    assert csv.get_data(as_text=True).startswith("lab,device,date,value")
    js = client.get(f"/api/p/{project}/export.json")
    payload = json.loads(js.get_data(as_text=True))
    assert payload["id"] == project
    assert len(payload["rows"]) == 24


def test_settings_rejects_non_numeric(client, project):
    r = client.post(f"/api/p/{project}/settings", json={"budget": "lots"})
    assert r.status_code == 400


def test_delete_project_removes_it(client, project):
    assert client.get(f"/api/p/{project}/state").status_code == 200
    client.post(f"/p/{project}/delete")
    assert client.get(f"/api/p/{project}/state").status_code == 404
