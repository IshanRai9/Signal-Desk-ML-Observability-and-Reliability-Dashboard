from copy import deepcopy

from signaldesk.alerts import evaluate
from signaldesk.settings import thresholds


def healthy():
    return {
        "request_count": 100,
        "prediction_count": 100,
        "latency_p95_ms": 100,
        "error_rate": 0.01,
        "quality": {"label_count": 80, "f1": 0.99},
        "drift": {"language": {"psi": 0.01}},
        "low_confidence_rate": 0.02,
        "overconfidence_error_rate": 0.01,
    }


def test_healthy_and_minimum_sample_gates():
    config = thresholds()
    assert evaluate(healthy(), {"f1": 1, "latency_p95_ms": 100}, config) == []
    metrics = healthy()
    metrics.update(request_count=2, error_rate=1, latency_p95_ms=2000)
    assert evaluate(metrics, {}, config) == []


def test_each_requested_alert_category():
    metrics = healthy()
    metrics.update(latency_p95_ms=1800, error_rate=0.2, low_confidence_rate=0.5)
    metrics["quality"]["f1"] = 0.4
    metrics["drift"]["language"]["psi"] = 1.2
    findings = evaluate(metrics, {"f1": 1, "latency_p95_ms": 100}, thresholds())
    assert {f["category"] for f in findings} == {
        "latency",
        "errors",
        "quality",
        "drift",
        "confidence",
    }


def test_sparse_labels_do_not_generate_quality_or_overconfidence_alert():
    metrics = deepcopy(healthy())
    metrics["quality"] = {"label_count": 2, "f1": 0}
    metrics["overconfidence_error_rate"] = 1
    assert evaluate(metrics, {"f1": 1}, thresholds()) == []


def test_relative_latency_and_f1_drop_thresholds():
    metrics = healthy()
    metrics["latency_p95_ms"] = 400  # Below absolute 500ms, above 3x baseline.
    metrics["quality"]["f1"] = 0.85  # Above floor, but material regression from 0.99.
    assert {
        f["category"] for f in evaluate(metrics, {"f1": 0.99, "latency_p95_ms": 100}, thresholds())
    } == {"latency", "quality"}
