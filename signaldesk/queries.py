"""Read-only, parameterized queries shared by monitoring, API and dashboard."""

import json
from datetime import datetime

from signaldesk.db import connect


def rows(sql, params=()):
    with connect(readonly=True) as connection:
        return [dict(r) for r in connection.execute(sql, params).fetchall()]


def events(start, end, version=None):
    clause = " AND model_version=?" if version else ""
    return rows(
        "SELECT * FROM labeled_events WHERE occurred_at>=? AND occurred_at<?"
        + clause
        + " ORDER BY occurred_at",
        (start, end, version) if version else (start, end),
    )


def requests(start, end, version=None):
    clause = " AND model_version=?" if version else ""
    return rows(
        "SELECT * FROM requests WHERE occurred_at>=? AND occurred_at<?"
        + clause
        + " ORDER BY occurred_at",
        (start, end, version) if version else (start, end),
    )


def feedback_for(start, end, version=None):
    clause = " AND e.model_version=?" if version else ""
    return rows(
        "SELECT f.* FROM feedback f JOIN events e ON e.id=f.event_id "
        "WHERE e.occurred_at>=? AND e.occurred_at<?"
        + clause
        + " AND f.id=(SELECT id FROM feedback WHERE event_id=e.id "
        "ORDER BY received_at DESC,id DESC LIMIT 1)",
        (start, end, version) if version else (start, end),
    )


def label_delays(event_rows):
    return [
        (
            datetime.fromisoformat(e["label_received_at"])
            - datetime.fromisoformat(e["occurred_at"])
        ).total_seconds()
        / 60
        for e in event_rows
        if e["label_received_at"]
    ]


def snapshots(start, end, version="__all__"):
    data = rows(
        "SELECT * FROM metric_snapshots WHERE window_start>=? AND window_start<? "
        "AND model_version=? ORDER BY window_start",
        (start, end, version),
    )
    return [{**r, **json.loads(r["metrics_json"])} for r in data]


def incidents(start, end, version=None):
    clause = " AND model_version=?" if version else " AND model_version='__all__'"
    return rows(
        "SELECT * FROM incidents WHERE window_start<? AND window_end>?"
        + clause
        + " ORDER BY window_start DESC,category",
        (end, start, version) if version else (end, start),
    )


def incident_detail(incident_id):
    found = rows("SELECT * FROM incidents WHERE id=?", (incident_id,))
    if not found:
        return None
    incident = found[0]
    version = None if incident["model_version"] == "__all__" else incident["model_version"]
    samples = events(incident["window_start"], incident["window_end"], version)
    samples.sort(
        key=lambda e: (
            e["expected_label"] is not None and e["prediction"] != e["expected_label"],
            e["anomaly"],
            1 - e["confidence"],
        ),
        reverse=True,
    )
    failed = requests(incident["window_start"], incident["window_end"], version)
    failed.sort(key=lambda r: (r["status_code"] >= 400, r["latency_ms"]), reverse=True)
    return {"incident": incident, "samples": samples[:30], "requests": failed[:30]}


def pipeline_health():
    return rows("SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT 25")


def deployments():
    return rows(
        "SELECT d.*,m.dataset_version,m.artifact_sha256,m.prompt_version,m.metadata_json "
        "FROM deployments d JOIN models m ON m.version=d.model_version "
        "ORDER BY deployed_at DESC,d.id DESC"
    )
