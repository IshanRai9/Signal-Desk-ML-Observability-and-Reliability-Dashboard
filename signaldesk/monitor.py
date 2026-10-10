import json
import logging
import time
from datetime import datetime, timedelta

from signaldesk import queries
from signaldesk.alerts import evaluate
from signaldesk.db import connect, initialize, insert
from signaldesk.metrics import classification_quality, summarize
from signaldesk.model import digest, predict
from signaldesk.settings import ROOT, now, stamp, thresholds

logger = logging.getLogger(__name__)


def reference_data():
    raw = json.loads((ROOT / "data/datasets/reference-v1.json").read_text())
    return [{**r, "text_length": len(r["text"]), **predict(r["text"], "router-v1")} for r in raw]


def persist_incident(connection, start, end, version, finding, computed_at):
    identity = digest(f"{start}:{version}:{finding['category']}".encode())[:20]
    connection.execute(
        "INSERT INTO incidents VALUES(?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET status='open',evidence_json=excluded.evidence_json,"
        "likely_cause=excluded.likely_cause,updated_at=excluded.updated_at",
        (
            identity,
            finding["category"],
            finding["severity"],
            start,
            end,
            version,
            "open",
            json.dumps(finding["evidence"]),
            finding["likely_cause"],
            computed_at,
            computed_at,
        ),
    )


def run_monitor(start: datetime | None = None, end: datetime | None = None):
    initialize()
    began = stamp()
    end = end or now()
    # Full retained history is intentionally recomputed so arbitrarily delayed labels are included.
    first = queries.rows("SELECT MIN(occurred_at) AS first FROM requests")[0]["first"]
    start = start or (datetime.fromisoformat(first) if first else end - timedelta(hours=1))
    start = start.replace(minute=0, second=0, microsecond=0)
    count = 0
    try:
        config = thresholds()
        config_hash = digest(json.dumps(config, sort_keys=True).encode())
        reference = reference_data()
        quality = classification_quality(
            [r["label"] for r in reference], [r["prediction"] for r in reference]
        )
        # Operational latency baseline is a persisted healthy baseline from the seed/bootstrap.
        baseline_path = ROOT / "config/baseline.json"
        baseline = json.loads(baseline_path.read_text())
        baseline["f1"] = quality["f1"]
        computed_at = stamp()
        # Read all facts in one snapshot before calculating; avoid mixed label revisions.
        with connect(readonly=True) as reader:
            reader.execute("BEGIN")
            event_rows = [
                dict(r)
                for r in reader.execute(
                    "SELECT * FROM labeled_events WHERE occurred_at>=? AND occurred_at<?",
                    (stamp(start), stamp(end)),
                )
            ]
            request_rows = [
                dict(r)
                for r in reader.execute(
                    "SELECT * FROM requests WHERE occurred_at>=? AND occurred_at<?",
                    (stamp(start), stamp(end)),
                )
            ]
            feedback = [
                dict(r)
                for r in reader.execute(
                    "SELECT f.* FROM feedback f WHERE f.id=(SELECT id FROM feedback "
                    "WHERE event_id=f.event_id ORDER BY received_at DESC,id DESC LIMIT 1)"
                )
            ]
        feedback_map = {r["event_id"]: r for r in feedback}
        from collections import defaultdict

        by_hour_events, by_hour_requests = defaultdict(list), defaultdict(list)
        for row in event_rows:
            by_hour_events[row["occurred_at"][:13]].append(row)
        for row in request_rows:
            by_hour_requests[row["occurred_at"][:13]].append(row)
        snapshots, findings = [], []
        cursor = start
        while cursor < end:
            boundary = min(cursor + timedelta(hours=1), end)
            key = stamp(cursor)[:13]
            all_events, all_requests = by_hour_events[key], by_hour_requests[key]
            versions = sorted({r["model_version"] for r in all_requests if r["model_version"]})
            for version in ["__all__", *versions]:
                selected_events = [
                    r for r in all_events if version == "__all__" or r["model_version"] == version
                ]
                selected_requests = [
                    r for r in all_requests if version == "__all__" or r["model_version"] == version
                ]
                selected_feedback = [
                    feedback_map[r["id"]] for r in selected_events if r["id"] in feedback_map
                ]
                metrics = summarize(
                    selected_events,
                    selected_requests,
                    selected_feedback,
                    reference,
                    queries.label_delays(selected_events),
                    duration_seconds=(boundary - cursor).total_seconds(),
                    config=config,
                )
                metrics["partial_window"] = boundary - cursor < timedelta(hours=1)
                snapshots.append(
                    (
                        stamp(cursor),
                        stamp(boundary),
                        version,
                        computed_at,
                        config_hash,
                        json.dumps(metrics, allow_nan=False),
                    )
                )
                # Partial windows may alert once minimum sample counts are reached.
                findings.append(
                    (stamp(cursor), stamp(boundary), version, evaluate(metrics, baseline, config))
                )
                count += len(selected_events) if version == "__all__" else 0
            cursor += timedelta(hours=1)
        # No write lock is held during inference/reference preparation or aggregation.
        with connect() as connection:
            connection.executemany(
                "INSERT INTO metric_snapshots VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(window_start,model_version) "
                "DO UPDATE SET window_end=excluded.window_end,computed_at=excluded.computed_at,"
                "config_sha256=excluded.config_sha256,metrics_json=excluded.metrics_json",
                snapshots,
            )
            for window_start, window_end, version, detected in findings:
                connection.execute(
                    "UPDATE incidents SET status='resolved',updated_at=? "
                    "WHERE window_start=? AND model_version=? "
                    "AND category NOT IN ('freshness','pipeline')",
                    (computed_at, window_start, version),
                )
                for finding in detected:
                    persist_incident(
                        connection, window_start, window_end, version, finding, computed_at
                    )
            # Historical windows keep their evidence but are no longer ongoing breaches.
            connection.execute(
                "UPDATE incidents SET status='resolved' WHERE window_end<=? "
                "AND category NOT IN ('freshness','pipeline')",
                (stamp(end.replace(minute=0, second=0, microsecond=0)),),
            )
            # Freshness uses successful inference events, not rejected HTTP traffic.
            latest = connection.execute("SELECT MAX(occurred_at) FROM events").fetchone()[0]
            age = (end - datetime.fromisoformat(latest)).total_seconds() / 60 if latest else None
            connection.execute(
                "UPDATE incidents SET status='resolved',updated_at=? WHERE category='freshness'",
                (computed_at,),
            )
            if age is None or age > config["freshness_minutes"]:
                persist_incident(
                    connection,
                    stamp(end.replace(minute=0, second=0, microsecond=0)),
                    stamp(end),
                    "__all__",
                    {
                        "category": "freshness",
                        "severity": "warning",
                        "evidence": {
                            "age_minutes": age,
                            "threshold_minutes": config["freshness_minutes"],
                        },
                        "likely_cause": "No recent successful predictions; "
                        "check ingestion and service availability.",
                    },
                    computed_at,
                )
            connection.execute(
                "UPDATE incidents SET status='resolved',updated_at=? WHERE category='pipeline'",
                (computed_at,),
            )
            insert(
                connection,
                "pipeline_runs",
                {
                    "job": "monitor",
                    "started_at": began,
                    "finished_at": stamp(),
                    "status": "success",
                    "rows_processed": count,
                    "error": None,
                },
            )
        return {
            "windows": len(snapshots),
            "events": count,
            "alerts": sum(len(f[3]) for f in findings),
        }
    except Exception as exc:
        with connect() as connection:
            insert(
                connection,
                "pipeline_runs",
                {
                    "job": "monitor",
                    "started_at": began,
                    "finished_at": stamp(),
                    "status": "failed",
                    "rows_processed": 0,
                    "error": type(exc).__name__ + ": " + str(exc)[:300],
                },
            )
            persist_incident(
                connection,
                stamp(end.replace(minute=0, second=0, microsecond=0)),
                stamp(end),
                "__all__",
                {
                    "category": "pipeline",
                    "severity": "critical",
                    "evidence": {"error_type": type(exc).__name__},
                    "likely_cause": "Monitoring job failed. "
                    "Inspect pipeline runs and configuration.",
                },
                stamp(),
            )
        raise


def loop(interval=60):
    while True:
        try:
            logger.info("monitor_complete %s", run_monitor())
        except Exception:
            logger.exception("monitor_failed")
        time.sleep(interval)
