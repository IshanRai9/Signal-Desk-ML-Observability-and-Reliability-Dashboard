import pytest
from fastapi.testclient import TestClient

from signaldesk.db import connect, insert
from signaldesk.model import load_model, train
from signaldesk.settings import stamp


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("ALERT_CONFIG", raising=False)
    load_model.cache_clear()
    train()
    with connect() as connection:
        insert(
            connection,
            "deployments",
            {"model_version": "router-v1", "deployed_at": stamp(), "reason": "Test baseline"},
        )
    yield tmp_path
    load_model.cache_clear()


@pytest.fixture
def client(store):
    from signaldesk.api import app

    with TestClient(app) as client:
        yield client
