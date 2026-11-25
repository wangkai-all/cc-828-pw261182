"""Shared fixtures: every test runs against a throwaway project directory."""
from __future__ import annotations

import os
import tempfile

# Tests must never write into the real project store.  The root is resolved at
# import time, so this has to happen before any ``metrology`` import.
os.environ["METROLOGY_DATA"] = tempfile.mkdtemp(prefix="metrology-tests-")

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def data_root() -> str:
    return os.environ["METROLOGY_DATA"]


@pytest.fixture()
def client():
    from metrology.app import app

    app.config.update(TESTING=True)
    with app.test_client() as c:
        yield c


@pytest.fixture()
def project(client):
    resp = client.post("/projects", data={"name": "pytest"})
    pid = resp.headers["Location"].rsplit("/", 1)[-1].split("#")[0]
    return pid
