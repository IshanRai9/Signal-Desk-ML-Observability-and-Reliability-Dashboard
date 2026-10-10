"""Threshold decisions are deterministic and independent of persistence."""

CAUSES = {
    "latency": "Service saturation or downstream timeout; inspect batch size and request failures.",
    "errors": "Malformed input or inference failure; compare validation errors with HTTP 5xx.",
    "quality": "Possible model regression or changed label semantics; "
    "compare versions and confusion pairs.",
    "drift": "Input population changed; inspect language, channel and text-length distributions.",
    "confidence": "Unfamiliar vocabulary or overconfident regression; "
    "compare OOV and labeled errors.",
}


def evaluate(metrics, baseline, config):
    findings = []
    if metrics["request_count"] < config["minimum_requests"]:
        return findings

    def add(category, evidence):
        findings.append(
            {
                "category": category,
                "severity": "critical"
                if category in {"quality", "latency", "errors"}
                else "warning",
                "evidence": evidence,
                "likely_cause": CAUSES[category],
            }
        )

    p95 = metrics["latency_p95_ms"]
    ref_latency = baseline.get("latency_p95_ms")
    if p95 is not None and (
        p95 > config["latency_p95_ms"]
        or (ref_latency and p95 > ref_latency * config["latency_multiplier"])
    ):
        add(
            "latency",
            {
                "p95_ms": p95,
                "baseline_p95_ms": ref_latency,
                "absolute_threshold_ms": config["latency_p95_ms"],
                "relative_multiplier": config["latency_multiplier"],
            },
        )
    if metrics["error_rate"] is not None and metrics["error_rate"] > config["error_rate"]:
        add("errors", {"error_rate": metrics["error_rate"], "threshold": config["error_rate"]})
    quality = metrics["quality"]
    baseline_f1 = baseline.get("f1")
    if quality["label_count"] >= config["minimum_labels"] and (
        quality["f1"] < config["macro_f1_floor"]
        or (baseline_f1 is not None and baseline_f1 - quality["f1"] > config["f1_drop"])
    ):
        add(
            "quality",
            {
                "macro_f1": quality["f1"],
                "baseline_f1": baseline_f1,
                "label_count": quality["label_count"],
                "floor": config["macro_f1_floor"],
            },
        )
    # Output drift is visualized but does not trigger an input-data drift alert by itself.
    shifted = {
        k: v["psi"]
        for k, v in metrics["drift"].items()
        if k != "prediction" and v["psi"] is not None and v["psi"] > config["psi"]
    }
    if metrics["prediction_count"] >= config["minimum_requests"] and shifted:
        add("drift", {"features": shifted, "threshold": config["psi"]})
    low = metrics["low_confidence_rate"]
    over = metrics["overconfidence_error_rate"]
    if (low is not None and low > config["low_confidence_rate"]) or (
        quality["label_count"] >= config["minimum_labels"]
        and over is not None
        and over > config["overconfidence_error_rate"]
    ):
        add(
            "confidence",
            {
                "low_confidence_rate": low,
                "confident_error_rate": over,
                "low_confidence_threshold": config["low_confidence_threshold"],
                "rate_threshold": config["low_confidence_rate"],
            },
        )
    return findings
