import json
from datetime import UTC, datetime, timedelta

from dashboard.data import episodes, load
from signaldesk import queries
from signaldesk.db import connect
from signaldesk.model import load_model
from signaldesk.monitor import run_monitor
from signaldesk.seed import seed
from signaldesk.settings import now, stamp


def test_seed_detects_all_failures_and_queries_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "seed.db"))
    monkeypatch.setenv("MODEL_DIR", str(tmp_path / "models"))
    load_model.cache_clear()
    anchor = datetime(2026, 10, 10, 12, tzinfo=UTC)
    first = anchor - timedelta(hours=72)
    assert seed(anchor)["events"] == 7088
    result = run_monitor(first, anchor)
    assert result["events"] == 7088
    original_count = len(queries.rows("SELECT * FROM incidents"))
    run_monitor(first, anchor)
    assert len(queries.rows("SELECT * FROM incidents")) == original_count
    assert seed(anchor)["status"] == "already-seeded"
    data = load(stamp(first), stamp(anchor))
    assert data["summary"]["request_count"] == 7200
    grouped = episodes(data["incidents"])
    assert {r["category"] for r in grouped} >= {
        "quality",
        "drift",
        "latency",
        "errors",
        "confidence",
    }
    degradation = queries.snapshots(
        stamp(first + timedelta(hours=24)), stamp(first + timedelta(hours=32))
    )
    assert all(r["quality"]["f1"] < 0.5 for r in degradation)
    drift = queries.snapshots(
        stamp(first + timedelta(hours=40)), stamp(first + timedelta(hours=48))
    )
    assert all(r["drift"]["language"]["psi"] > 0.25 for r in drift)
    slow = queries.snapshots(stamp(first + timedelta(hours=56)), stamp(first + timedelta(hours=60)))
    assert all(r["latency_p95_ms"] > 2000 for r in slow)
    latency_incident = next(r for r in data["incidents"] if r["category"] == "latency")
    detail = queries.incident_detail(latency_incident["id"])
    assert detail["requests"][0]["status_code"] == 503
    assert all(e["model_version"] == "router-v1" for e in detail["samples"])
    assert all(r["source"] == "seed" for r in data["requests"])
    assert len(load(stamp(first), stamp(anchor), "router-v2-bad")["events"]) == 792
    load_model.cache_clear()


def test_delayed_label_backfills_historical_quality(client):
    response = client.post("/v1/predict", json={"text": "Please refund my payment"})
    event_id = response.json()["event_id"]
    original = now() - timedelta(days=4)
    with connect() as connection:
        connection.execute("UPDATE events SET occurred_at=?", (stamp(original),))
        connection.execute("UPDATE requests SET occurred_at=?", (stamp(original),))
    run_monitor()
    snapshot = queries.rows(
        "SELECT * FROM metric_snapshots WHERE model_version='__all__' ORDER BY window_start"
    )[0]
    assert json.loads(snapshot["metrics_json"])["quality"]["f1"] is None
    client.post(
        "/v1/labels", json={"event_id": event_id, "label": "billing", "external_id": "late-1"}
    )
    run_monitor()
    snapshot = queries.rows(
        "SELECT * FROM metric_snapshots WHERE model_version='__all__' ORDER BY window_start"
    )[0]
    assert json.loads(snapshot["metrics_json"])["quality"]["accuracy"] == 1
    assert json.loads(snapshot["metrics_json"])["quality"]["label_count"] == 1
    assert any(r["category"] == "freshness" for r in queries.rows("SELECT * FROM incidents"))


def test_monitor_failure_is_persisted_and_recovery_closes_pipeline_alert(
    store, monkeypatch, tmp_path
):
    path = tmp_path / "broken.json"
    path.write_text("broken")
    monkeypatch.setenv("ALERT_CONFIG", str(path))
    import pytest

    with pytest.raises(json.JSONDecodeError):
        run_monitor()
    assert queries.pipeline_health()[0]["status"] == "failed"
    assert queries.rows("SELECT * FROM incidents WHERE category='pipeline'")[0]["status"] == "open"
    monkeypatch.delenv("ALERT_CONFIG")
    run_monitor()
    assert queries.pipeline_health()[0]["status"] == "success"
    assert (
        queries.rows("SELECT * FROM incidents WHERE category='pipeline'")[0]["status"] == "resolved"
    )
