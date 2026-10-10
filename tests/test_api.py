import json

from signaldesk import queries


def test_realtime_batch_and_feedback_are_durable(client):
    response = client.post(
        "/v1/predict", json={"text": "Please refund the duplicate payment on my invoice"}
    )
    assert response.status_code == 200
    prediction = response.json()
    assert prediction["prediction"] == "billing"
    assert response.headers["X-Request-ID"]
    response = client.post(
        "/v1/batch",
        json={
            "tickets": [
                {"text": "I forgot my password and cannot sign in"},
                {"text": "The app crashes when I upload a file"},
            ]
        },
    )
    assert len(response.json()["predictions"]) == 2
    assert len(queries.rows("SELECT * FROM requests")) == 2
    assert len(queries.rows("SELECT * FROM events")) == 3
    payload = {"event_id": prediction["event_id"], "label": "billing", "external_id": "label-1"}
    assert client.post("/v1/labels", json=payload).json()["status"] == "recorded"
    assert client.post("/v1/labels", json=payload).json()["status"] == "already-recorded"
    assert client.post("/v1/labels", json={**payload, "label": "account"}).status_code == 409
    assert (
        client.post(
            "/v1/feedback",
            json={
                "event_id": prediction["event_id"],
                "corrected_label": "account",
                "external_id": "feedback-1",
            },
        ).status_code
        == 200
    )
    row = queries.rows("SELECT * FROM labeled_events WHERE id=?", (prediction["event_id"],))[0]
    assert row["expected_label"] == "billing"  # Feedback is never ground truth.


def test_malformed_inputs_and_invalid_batches_are_logged(client):
    assert client.post("/v1/predict", json={}).status_code == 422
    assert (
        client.post(
            "/v1/predict", content="{broken", headers={"Content-Type": "application/json"}
        ).status_code
        == 422
    )
    assert client.post("/v1/predict", json={"text": "   "}).status_code == 422
    assert (
        client.post("/v1/batch", json={"tickets": [{"text": "valid ticket"}, {}]}).status_code
        == 422
    )
    requests = queries.rows("SELECT * FROM requests")
    assert len(requests) == 4
    assert all(r["error_category"] == "malformed_input" for r in requests)
    assert sum(r["invalid_items"] for r in requests) == 4
    assert queries.rows("SELECT * FROM events") == []


def test_inference_failure_is_logged_and_no_partial_batch_commits(client, monkeypatch):
    def fail(*args):
        raise RuntimeError("Test fault")

    monkeypatch.setattr("signaldesk.api.predict", fail)
    response = client.post("/v1/batch", json={"tickets": [{"text": "test ticket"}]})
    assert response.status_code == 503
    assert queries.rows("SELECT * FROM requests")[0]["status_code"] == 503
    assert queries.rows("SELECT * FROM events") == []


def test_auth_and_unknown_references(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "test-secret")
    assert client.get("/v1/models").status_code == 401
    assert client.get("/healthz").status_code == 200
    assert client.get("/v1/models", headers={"X-API-Key": "test-secret"}).status_code == 200
    assert (
        client.post(
            "/v1/labels",
            headers={"X-API-Key": "test-secret"},
            json={"event_id": "unknown", "label": "billing", "external_id": "x"},
        ).status_code
        == 404
    )


def test_deployment_switch_pins_version_and_stores_manifest(client):
    assert (
        client.post(
            "/v1/deployments", json={"model_version": "unknown", "reason": "invalid model"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/v1/deployments",
            json={"model_version": "router-v2-bad", "reason": "Replay faulty release"},
        ).status_code
        == 201
    )
    response = client.post("/v1/predict", json={"text": "Please refund my duplicate payment"})
    assert response.json()["model_version"] == "router-v2-bad"
    assert response.json()["prediction"] == "account"
    models = client.get("/v1/models").json()["models"]
    assert all(len(m["artifact_sha256"]) == 64 for m in models)
    assert json.loads(models[1]["metadata_json"])["synthetic_fault"]


def test_read_api_validates_time_and_reports_missing_incident(client):
    assert (
        client.get("/v1/metrics?start=2026-01-01T00:00:00&end=2026-01-02T00:00:00").status_code
        == 422
    )
    assert client.get("/v1/incidents/unknown").status_code == 404


def test_event_text_is_redacted_but_feature_length_retained(client):
    text = "Please update my account email to person@example.com reference 12345678"
    response = client.post("/v1/predict", json={"text": text})
    assert response.status_code == 200
    event = queries.rows("SELECT * FROM events")[0]
    assert "person@example.com" not in event["text"]
    assert "12345678" not in event["text"]
    assert event["text_length"] == len(text)
