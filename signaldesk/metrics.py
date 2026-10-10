"""Pure metric calculations; no I/O, no fabricated quality for unlabeled events."""

from collections import Counter

import numpy as np
from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support

from signaldesk.model import CLASSES

LENGTH_BINS = [0, 80, 120, 200, 400, 800, float("inf")]


def classification_quality(expected, predicted):
    if not expected:
        return {
            "accuracy": None,
            "precision": None,
            "recall": None,
            "f1": None,
            "label_count": 0,
            "classes": CLASSES,
            "confusion_matrix": [],
            "per_class": [],
        }
    precision, recall, f1, support = precision_recall_fscore_support(
        expected, predicted, labels=CLASSES, zero_division=0
    )
    return {
        "accuracy": float(accuracy_score(expected, predicted)),
        "precision": float(precision.mean()),
        "recall": float(recall.mean()),
        "f1": float(f1.mean()),
        "label_count": len(expected),
        "classes": CLASSES,
        "confusion_matrix": confusion_matrix(expected, predicted, labels=CLASSES).tolist(),
        "per_class": [
            {
                "class": c,
                "precision": float(p),
                "recall": float(r),
                "f1": float(f),
                "support": int(s),
            }
            for c, p, r, f, s in zip(CLASSES, precision, recall, f1, support, strict=True)
        ],
    }


def distribution_drift(reference, current, bins=None):
    if not reference or not current:
        return {"psi": None, "kl": None, "bins": [], "reference": [], "current": []}
    if bins is not None:
        ref = np.histogram(reference, bins=bins)[0].astype(float)
        cur = np.histogram(current, bins=bins)[0].astype(float)
        names = [f"{bins[i]}–{bins[i + 1]}" for i in range(len(bins) - 1)]
    else:
        names = sorted(set(reference) | set(current))
        rc, cc = Counter(reference), Counter(current)
        ref, cur = np.array([rc[k] for k in names], float), np.array([cc[k] for k in names], float)
    # Additive smoothing, then normalize. Finite for unseen categories and empty bins.
    ref = (ref + 0.5) / (ref.sum() + 0.5 * len(ref))
    cur = (cur + 0.5) / (cur.sum() + 0.5 * len(cur))
    return {
        "psi": float(np.sum((cur - ref) * np.log(cur / ref))),
        "kl": float(np.sum(cur * np.log(cur / ref))),
        "bins": names,
        "reference": ref.tolist(),
        "current": cur.tolist(),
    }


def summarize(events, requests, feedback, reference, labels, *, duration_seconds, config):
    successes = list(events)
    latency = [r["latency_ms"] for r in requests]
    n = len(successes)
    labeled = [e for e in successes if e.get("expected_label") is not None]
    quality = classification_quality(
        [e["expected_label"] for e in labeled], [e["prediction"] for e in labeled]
    )
    low = sum(e["confidence"] < config["low_confidence_threshold"] for e in successes)
    drift = {}
    for feature, bins in [
        ("text_length", LENGTH_BINS),
        ("language", None),
        ("channel", None),
        ("prediction", None),
    ]:
        drift[feature] = distribution_drift(
            [r[feature] for r in reference], [e[feature] for e in successes], bins
        )
    wrong_confident = sum(
        e["confidence"] >= 0.8 and e["prediction"] != e["expected_label"] for e in labeled
    )
    feedback_ids = {f["event_id"] for f in feedback}
    event_map = {e["id"]: e for e in successes}
    # Latest feedback per event is selected by the query layer.
    corrections = sum(
        f["corrected_label"] is not None
        and f["corrected_label"] != event_map[f["event_id"]]["prediction"]
        for f in feedback
        if f["event_id"] in event_map
    )
    total_ms = sum(e["inference_ms"] for e in successes)
    return {
        "request_count": len(requests),
        "prediction_count": n,
        "throughput_rps": len(requests) / max(1, duration_seconds),
        "prediction_throughput_rps": n / max(1, duration_seconds),
        "error_rate": sum(r["status_code"] >= 400 for r in requests) / len(requests)
        if requests
        else None,
        "invalid_items": sum(r["invalid_items"] for r in requests),
        **{
            f"latency_p{p}_ms": float(np.percentile(latency, p)) if latency else None
            for p in [50, 95, 99]
        },
        "low_confidence_rate": low / n if n else None,
        "overconfidence_error_rate": wrong_confident / len(labeled) if labeled else None,
        "anomaly_rate": sum(e["anomaly"] for e in successes) / n if n else None,
        "feedback_rate": len(feedback_ids) / n if n else None,
        "correction_rate": corrections / len(feedback_ids) if feedback_ids else None,
        "label_coverage": len(labeled) / n if n else None,
        "label_delay_p95_minutes": float(np.percentile(labels, 95)) if labels else None,
        "quality": quality,
        "drift": drift,
        "class_distribution": dict(Counter(e["prediction"] for e in successes)),
        "confidence_histogram": np.histogram(
            [e["confidence"] for e in successes], bins=np.linspace(0, 1, 11)
        )[0].tolist(),
        "estimated_compute_usd": total_ms / 3_600_000 * config["estimated_cpu_usd_per_hour"],
        "llm": {
            "applicable": False,
            "tokens": None,
            "estimated_cost_usd": None,
            "citation_failures": None,
            "hallucination_flags": None,
            "safety_violations": None,
        },
    }
