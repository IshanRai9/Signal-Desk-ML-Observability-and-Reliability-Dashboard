"""Deterministic historical fault replay. Never mutates existing live events."""

import json
import random
from datetime import datetime, timedelta

from signaldesk.db import connect, insert
from signaldesk.model import CLASSES, TEMPLATES, predict, train
from signaldesk.settings import ROOT, now, stamp


def seed(anchor: datetime | None = None):
    if anchor is not None and anchor.tzinfo is None:
        raise ValueError("Replay anchor must have an explicit timezone")
    train()
    config = json.loads((ROOT / "config/experiment.json").read_text())
    degradation_start, degradation_end = config["degradation_hours"]
    drift_start, drift_end = config["drift_hours"]
    latency_start, latency_end = config["latency_hours"]
    rng = random.Random(config["seed"])
    anchor = (anchor or now()).replace(minute=0, second=0, microsecond=0)
    start = anchor - timedelta(hours=config["seed_hours"])
    with connect() as connection:
        if connection.execute("SELECT 1 FROM requests WHERE source='seed' LIMIT 1").fetchone():
            return {"status": "already-seeded"}
        if connection.execute("SELECT 1 FROM requests LIMIT 1").fetchone():
            raise RuntimeError(
                "Seed requires a fresh database so historical deployments "
                "cannot overlap live traffic"
            )
        for offset, version, reason in [
            (0, "router-v1", "Initial baseline deployment"),
            (degradation_start, "router-v2-bad", "Fault replay: swapped training labels"),
            (degradation_end, "router-v1", "Rollback after macro F1 degradation"),
        ]:
            insert(
                connection,
                "deployments",
                {
                    "model_version": version,
                    "deployed_at": stamp(start + timedelta(hours=offset)),
                    "reason": reason,
                },
            )
        count = 0
        for hour in range(config["seed_hours"]):
            degraded = degradation_start <= hour < degradation_end
            drifted = drift_start <= hour < drift_end
            slow = latency_start <= hour < latency_end
            version = "router-v2-bad" if degraded else "router-v1"
            for i in range(config["events_per_hour"]):
                spacing = 3500 / config["events_per_hour"]
                moment = start + timedelta(hours=hour, seconds=i * spacing + rng.randint(0, 20))
                req_id, event_id = f"seed-r-{hour:02}-{i:03}", f"seed-e-{hour:02}-{i:03}"
                error = (slow and i % 10 == 0) or i == 99
                status = 503 if slow and i % 10 == 0 else (422 if i == 99 else 200)
                latency = rng.uniform(780, 2400) if slow else rng.uniform(28, 110)
                insert(
                    connection,
                    "requests",
                    {
                        "id": req_id,
                        "occurred_at": stamp(moment),
                        "route": "/v1/predict",
                        "mode": "realtime",
                        "model_version": version,
                        "status_code": status,
                        "latency_ms": latency,
                        "item_count": 1,
                        "invalid_items": int(status == 422),
                        "error_category": "inference_failure"
                        if status == 503
                        else ("malformed_input" if error else None),
                        "source": "seed",
                    },
                )
                if error:
                    continue
                label = CLASSES[i % 3] if not drifted else rng.choices(CLASSES, [0.1, 0.8, 0.1])[0]
                text = (
                    f"Hello support. {rng.choice(TEMPLATES[label])}. Thanks for looking into this."
                )
                language, channel = "en", rng.choice(["email", "web"])
                if drifted:
                    text = (
                        "Hola equipo, necesito ayuda urgente. Mi factura tiene un cobro duplicado "
                        "y el reembolso no aparece. "
                        "No puedo resolver esta situación desde el portal. "
                    ) * rng.randint(3, 6)
                    language, channel = "es", "whatsapp"
                prediction = predict(text, version)
                insert(
                    connection,
                    "events",
                    {
                        "id": event_id,
                        "request_id": req_id,
                        "occurred_at": stamp(moment),
                        "model_version": version,
                        "text": text,
                        "language": language,
                        "channel": channel,
                        "text_length": len(text),
                        "oov_ratio": prediction["oov_ratio"],
                        "prediction": prediction["prediction"],
                        "confidence": prediction["confidence"],
                        "probabilities_json": json.dumps(prediction["probabilities"]),
                        "anomaly": int(prediction["anomaly"]),
                        "inference_ms": rng.uniform(1, 7),
                        "source": "seed",
                    },
                )
                arrival = moment + timedelta(hours=rng.randint(1, 6))
                if i % 7 != 0 and arrival < anchor:
                    insert(
                        connection,
                        "labels",
                        {
                            "event_id": event_id,
                            "label": label,
                            "received_at": stamp(arrival),
                            "source": "synthetic-adjudication",
                            "external_id": f"label-{event_id}",
                        },
                    )
                if i % 9 == 0 and moment + timedelta(minutes=45) < anchor:
                    insert(
                        connection,
                        "feedback",
                        {
                            "event_id": event_id,
                            "corrected_label": label,
                            "comment": "Human review (synthetic)",
                            "received_at": stamp(moment + timedelta(minutes=45)),
                            "external_id": f"feedback-{event_id}",
                        },
                    )
                count += 1
        # Seed one failed ETL run as historical pipeline evidence, separate from service failures.
        insert(
            connection,
            "pipeline_runs",
            {
                "job": "label-import",
                "started_at": stamp(start + timedelta(hours=57)),
                "finished_at": stamp(start + timedelta(hours=57, minutes=2)),
                "status": "failed",
                "rows_processed": 0,
                "error": "Synthetic upstream label feed timeout; next run recovered",
            },
        )
        insert(
            connection,
            "pipeline_runs",
            {
                "job": "seed",
                "started_at": stamp(start),
                "finished_at": stamp(anchor),
                "status": "success",
                "rows_processed": count,
                "error": None,
            },
        )
    return {"status": "seeded", "events": count, "start": stamp(start), "end": stamp(anchor)}
