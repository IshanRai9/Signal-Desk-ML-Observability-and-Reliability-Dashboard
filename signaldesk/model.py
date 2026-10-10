"""Versioned local ML artifacts. Joblib artifacts are loaded only from our build directory."""

import hashlib
import json
import re
from functools import lru_cache

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from signaldesk.db import connect, initialize, insert
from signaldesk.settings import ROOT, model_dir, stamp

CLASSES = ["account", "billing", "technical"]
TEMPLATES = {
    "account": [
        "I cannot login to my account after resetting my password",
        "Please reset my password and unlock my account",
        "The verification code for login never arrives",
        "I need to update the email address on my profile",
        "How can I delete my account and personal profile",
        "My two factor authentication code is invalid",
        "I forgot my username and cannot sign in",
        "My account is locked after too many login attempts",
        "Please change my profile name and account email",
        "I lost access to my authentication device",
        "Cannot access my account with my new password",
        "My account verification link has expired",
    ],
    "billing": [
        "I was charged twice for my monthly subscription",
        "Please refund the duplicate payment on my invoice",
        "The credit card payment was declined at checkout",
        "How do I cancel my paid subscription renewal",
        "My invoice total is wrong and includes an extra charge",
        "I need a receipt for my recent payment",
        "Please update the billing address on the invoice",
        "My refund has not appeared on my credit card",
        "The subscription price increased without notice",
        "Can I change my payment method for billing",
        "I paid the invoice but still see an unpaid balance",
        "Why was my credit card charged after cancellation",
    ],
    "technical": [
        "The app crashes every time I upload a file",
        "The API returns server error 500 during export",
        "My dashboard is blank and the page does not load",
        "The integration webhook is failing with a timeout",
        "The search feature returns no results after the update",
        "File downloads are broken in the latest app version",
        "The database sync is stuck and the service is slow",
        "The application freezes when opening the report",
        "I get a network connection error in the desktop app",
        "The API endpoint is timing out on every request",
        "Exported files are corrupted after processing",
        "The mobile app shows a blank screen and crashes",
    ],
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_datasets():
    directory = ROOT / "data/datasets"
    directory.mkdir(parents=True, exist_ok=True)
    training, reference = [], []
    for label, texts in TEMPLATES.items():
        for i, text in enumerate(texts):
            for prefix in ["Hello,", "Urgent:", "Please help:", "Hi support,"]:
                training.append({"text": f"{prefix} {text}.", "label": label})
            # Reference traffic is separate from the exact training strings, but shares templates.
            reference.append(
                {
                    "text": f"Good morning. {text}. Thank you for your help.",
                    "label": label,
                    "language": "en",
                    "channel": "email" if i % 2 else "web",
                }
            )
    for filename, rows in [("tickets-v1.json", training), ("reference-v1.json", reference)]:
        path = directory / filename
        content = json.dumps(rows, indent=2) + "\n"
        if path.exists() and path.read_text() != content:
            raise RuntimeError(f"Immutable dataset changed: {path}; bump the version")
        path.write_text(content)
    return training, reference


def train():
    initialize()
    training, _ = build_datasets()
    config_bytes = (ROOT / "config/experiment.json").read_bytes()
    config = json.loads(config_bytes)
    model_dir().mkdir(parents=True, exist_ok=True)
    for version, corrupt in [("router-v1", False), ("router-v2-bad", True)]:
        path = model_dir() / f"{version}.joblib"
        with connect(readonly=True) as reader:
            existing = reader.execute(
                "SELECT artifact_sha256 FROM models WHERE version=?", (version,)
            ).fetchone()
        if existing and path.exists():
            continue
        swapped = {"billing": "account", "account": "billing", "technical": "technical"}
        labels = [swapped[r["label"]] if corrupt else r["label"] for r in training]
        pipeline = Pipeline(
            [
                ("tfidf", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True)),
                (
                    "classifier",
                    LogisticRegression(
                        C=config["logistic_C"], max_iter=500, random_state=config["seed"]
                    ),
                ),
            ]
        )
        pipeline.fit([r["text"] for r in training], labels)
        with connect() as connection:
            joblib.dump(pipeline, path)
            metadata = {
                "algorithm": config["model"],
                "seed": config["seed"],
                "training_rows": len(training),
                "synthetic_fault": corrupt,
                "dataset_sha256": digest((ROOT / "data/datasets/tickets-v1.json").read_bytes()),
            }
            if existing:
                if digest(path.read_bytes()) != existing[0]:
                    raise RuntimeError(
                        "Rebuilt artifact differs from registered version; use a new version"
                    )
            else:
                insert(
                    connection,
                    "models",
                    {
                        "version": version,
                        "created_at": stamp(),
                        "dataset_version": config["dataset_version"],
                        "artifact_sha256": digest(path.read_bytes()),
                        "config_sha256": digest(config_bytes),
                        "prompt_version": config["prompt_version"],
                        "metadata_json": json.dumps(metadata),
                    },
                )


@lru_cache(maxsize=4)
def load_model(version: str):
    if version not in {"router-v1", "router-v2-bad"}:
        raise ValueError("Unregistered model artifact")
    path = model_dir() / f"{version}.joblib"
    with connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT artifact_sha256 FROM models WHERE version=?", (version,)
        ).fetchone()
    if not row or digest(path.read_bytes()) != row[0]:
        raise RuntimeError("Model checksum mismatch")
    return joblib.load(path)


def predict(text: str, version: str) -> dict:
    pipeline = load_model(version)
    probabilities = pipeline.predict_proba([text])[0]
    vectorizer = pipeline.named_steps["tfidf"]
    words = vectorizer.build_analyzer()(text)
    oov = sum(w not in vectorizer.vocabulary_ for w in words) / max(1, len(words))
    maximum = int(probabilities.argmax())
    return {
        "prediction": str(pipeline.classes_[maximum]),
        "confidence": float(probabilities[maximum]),
        "probabilities": dict(zip(pipeline.classes_.tolist(), probabilities.tolist(), strict=True)),
        "oov_ratio": oov,
        "anomaly": oov > 0.7 or len(text) > 800,
    }


def redact(text: str) -> str:
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email]", text)
    return re.sub(r"\b\d{6,}\b", "[number]", text)
