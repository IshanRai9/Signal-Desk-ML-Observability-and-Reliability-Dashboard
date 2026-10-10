import json

from signaldesk import queries
from signaldesk.metrics import summarize
from signaldesk.monitor import reference_data
from signaldesk.settings import thresholds


def load(start, end, version=None):
    event_rows = queries.events(start, end, version)
    request_rows = queries.requests(start, end, version)
    feedback = queries.feedback_for(start, end, version)
    from datetime import datetime

    seconds = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
    summary = summarize(
        event_rows,
        request_rows,
        feedback,
        reference_data(),
        queries.label_delays(event_rows),
        duration_seconds=seconds,
        config=thresholds(),
    )
    history = queries.snapshots(start, end, version or "__all__")
    incidents = queries.incidents(start, end, version)
    return {
        "events": event_rows,
        "requests": request_rows,
        "summary": summary,
        "history": history,
        "incidents": incidents,
        "feedback": feedback,
    }


def episodes(incidents):
    """Coalesce adjacent hourly alerts of the same category/model for display."""
    result = []
    for incident in sorted(
        incidents, key=lambda r: (r["category"], r["model_version"], r["window_start"])
    ):
        if (
            result
            and result[-1]["category"] == incident["category"]
            and result[-1]["model_version"] == incident["model_version"]
            and result[-1]["window_end"] == incident["window_start"]
        ):
            result[-1]["window_end"] = incident["window_end"]
            result[-1]["windows"] += 1
            result[-1]["status"] = incident["status"]
        else:
            result.append(
                {**incident, "windows": 1, "evidence": json.loads(incident["evidence_json"])}
            )
    return sorted(result, key=lambda r: r["window_start"], reverse=True)
