import sqlite3

from signaldesk import queries
from signaldesk.db import insert


def test_evidence_write_failure_never_acknowledges_unlogged_prediction(client, monkeypatch):
    def fail_event(connection, table, row):
        if table == "events":
            raise sqlite3.OperationalError("Injected evidence-store failure")
        insert(connection, table, row)

    monkeypatch.setattr("signaldesk.api.insert", fail_event)
    result = client.post("/v1/predict", json={"text": "Please refund my duplicate payment"})
    assert result.status_code == 503
    assert result.json()["detail"] == "Evidence store unavailable"
    assert queries.rows("SELECT * FROM events") == []
    assert queries.rows("SELECT * FROM requests") == []


def test_latest_ground_truth_revision_wins_without_duplicate_join(client):
    result = client.post("/v1/predict", json={"text": "Please refund my duplicate payment"})
    event_id = result.json()["event_id"]
    for key, label in [("first", "account"), ("corrected", "billing")]:
        assert (
            client.post(
                "/v1/labels", json={"event_id": event_id, "label": label, "external_id": key}
            ).status_code
            == 200
        )
    events = queries.rows("SELECT * FROM labeled_events")
    assert len(events) == 1
    assert events[0]["expected_label"] == "billing"
    assert len(queries.rows("SELECT * FROM labels")) == 2
