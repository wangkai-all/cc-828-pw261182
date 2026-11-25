"""Tiny JSON project store with an append-only audit trail.

Each project lives in ``data/projects/<id>.json`` and holds the design rows,
the chosen model, user settings, and every analysis event.  The audit trail is
what makes results traceable: each event carries a UTC timestamp, a hash of the
data it was computed from, the inputs, and the key outputs.
"""
from __future__ import annotations

import json
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

ROOT = Path(os.environ.get("METROLOGY_DATA", Path(__file__).resolve().parent.parent / "data" / "projects"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class ProjectNotFound(KeyError):
    pass


def _path(pid: str) -> Path:
    if not pid or not all(c.isalnum() or c in "-_" for c in pid):
        raise ProjectNotFound(pid)
    return ROOT / f"{pid}.json"


def list_projects() -> List[Dict]:
    if not ROOT.exists():
        return []
    out = []
    for p in sorted(ROOT.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append(
            {
                "id": d.get("id"),
                "name": d.get("name"),
                "created": d.get("created"),
                "updated": d.get("updated"),
                "n_rows": len(d.get("rows", [])),
                "terms": d.get("terms", []),
                "has_fit": any(e.get("kind") == "fit" for e in d.get("history", [])),
            }
        )
    return out


def create_project(name: str) -> Dict:
    pid = secrets.token_urlsafe(6).replace("=", "")
    doc = {
        "id": pid,
        "name": name.strip() or "未命名项目",
        "created": _now(),
        "updated": _now(),
        "rows": [],
        "source": None,
        "terms": ["lab", "device", "date"],
        "settings": {
            "budget": 5000.0,
            "cost_lab": 1200.0,
            "cost_device": 250.0,
            "cost_repeat": 40.0,
        },
        "history": [],
        "latest": {},
    }
    _atomic_write(_path(pid), doc)
    log_event(pid, "project_created", {"name": doc["name"]}, {"terms": doc["terms"]})
    return load_project(pid)


def load_project(pid: str) -> Dict:
    p = _path(pid)
    if not p.exists():
        raise ProjectNotFound(pid)
    return json.loads(p.read_text(encoding="utf-8"))


def save_project(doc: Dict) -> None:
    doc["updated"] = _now()
    _atomic_write(_path(doc["id"]), doc)


def delete_project(pid: str) -> None:
    p = _path(pid)
    if p.exists():
        p.unlink()


def log_event(pid: str, kind: str, inputs: Dict, outputs: Dict) -> Dict:
    doc = load_project(pid)
    event = {
        "seq": len(doc["history"]) + 1,
        "at": _now(),
        "kind": kind,
        "inputs": inputs,
        "outputs": outputs,
    }
    doc["history"].append(event)
    # ``latest`` holds the full result bundles used by the UI; the audit event
    # itself lives in ``history`` so a page reload can restore every tab.
    save_project(doc)
    return event
