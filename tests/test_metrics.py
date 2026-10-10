import pytest

from signaldesk.metrics import classification_quality, distribution_drift, summarize


def test_quality_uses_fixed_class_order_and_macro_average():
    result = classification_quality(
        ["billing", "billing", "account", "technical"],
        ["billing", "account", "account", "technical"],
    )
    assert result["accuracy"] == 0.75
    assert result["precision"] == pytest.approx((0.5 + 1 + 1) / 3)
    assert result["recall"] == pytest.approx((1 + 0.5 + 1) / 3)
    assert result["f1"] == pytest.approx((2 / 3 + 2 / 3 + 1) / 3)
    assert result["confusion_matrix"] == [[1, 0, 0], [1, 1, 0], [0, 0, 1]]


def test_unlabeled_quality_is_unknown():
    assert classification_quality([], [])["f1"] is None


def test_drift_handles_unseen_categories_and_zero_bins():
    assert distribution_drift(["en"] * 100, ["en"] * 100)["psi"] == 0
    changed = distribution_drift(["en"] * 100, ["es"] * 100)
    assert changed["psi"] > 0.25
    assert changed["kl"] > 0


def test_http_request_denominator_and_elapsed_time():
    requests = [
        {"latency_ms": x, "status_code": s, "invalid_items": 0}
        for x, s in [(10, 200), (20, 200), (100, 500)]
    ]
    result = summarize(
        [],
        requests,
        [],
        [],
        [],
        duration_seconds=60,
        config={"low_confidence_threshold": 0.6, "estimated_cpu_usd_per_hour": 0.04},
    )
    assert result["request_count"] == 3
    assert result["throughput_rps"] == 0.05
    assert result["error_rate"] == pytest.approx(1 / 3)
    assert result["latency_p95_ms"] == pytest.approx(92)
    assert result["quality"]["f1"] is None
