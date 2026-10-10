import json
import os
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def database_path() -> Path:
    return Path(os.getenv("DATABASE_PATH", str(ROOT / "data/runtime/signaldesk.db")))


def model_dir() -> Path:
    return Path(os.getenv("MODEL_DIR", str(ROOT / "artifacts/models")))


def thresholds() -> dict:
    path = Path(os.getenv("ALERT_CONFIG", str(ROOT / "config/alerts.json")))
    config = json.loads(path.read_text())
    if config["window_minutes"] != 60:
        raise ValueError("This schema uses hourly windows; window_minutes must be 60")
    for key in ("minimum_requests", "minimum_labels", "latency_p95_ms", "freshness_minutes"):
        if config[key] <= 0:
            raise ValueError(f"{key} must be positive")
    for key in (
        "error_rate",
        "macro_f1_floor",
        "low_confidence_rate",
        "f1_drop",
        "low_confidence_threshold",
        "overconfidence_error_rate",
    ):
        if not 0 <= config[key] <= 1:
            raise ValueError(f"{key} must be between 0 and 1")
    if config["psi"] < 0 or config["latency_multiplier"] <= 1:
        raise ValueError("PSI must be nonnegative and latency_multiplier must exceed 1")
    if config["estimated_cpu_usd_per_hour"] < 0:
        raise ValueError("Compute price must be nonnegative")
    return config


def now() -> datetime:
    return datetime.now(UTC)


def stamp(value: datetime | None = None) -> str:
    return (value or now()).astimezone(UTC).isoformat(timespec="microseconds")
