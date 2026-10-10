import hmac
import json
import logging
import os
import sqlite3
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from signaldesk import queries
from signaldesk.db import active_version, connect, initialize, insert
from signaldesk.model import load_model, predict, redact
from signaldesk.settings import now, stamp

logger = logging.getLogger("signaldesk.events")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    logger.addHandler(logging.StreamHandler(sys.stdout))
Label = Literal["account", "billing", "technical"]


def authorize(request: Request, x_api_key: Annotated[str | None, Header()] = None):
    if request.url.path in {"/healthz", "/readyz"}:
        return
    expected = os.getenv("API_KEY")
    if expected and not hmac.compare_digest(x_api_key or "", expected):
        raise HTTPException(401, "Invalid API key")


@asynccontextmanager
async def lifespan(app):
    initialize()
    with connect(readonly=True) as connection:
        version = active_version(connection)
    load_model(version)
    yield


app = FastAPI(
    title="SignalDesk inference & evidence API",
    version="1.0.0",
    lifespan=lifespan,
    dependencies=[Depends(authorize)],
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Ticket(StrictModel):
    text: str = Field(min_length=3, max_length=5000)
    language: Literal["en", "es", "fr", "de", "other"] = "en"
    channel: Literal["email", "web", "chat", "whatsapp"] = "web"


class Batch(StrictModel):
    tickets: list[Ticket] = Field(min_length=1, max_length=1000)


class GroundTruth(StrictModel):
    event_id: str
    label: Label
    source: str = Field(default="human-adjudicated", min_length=1, max_length=100)
    external_id: str = Field(min_length=1, max_length=150)


class Feedback(StrictModel):
    event_id: str
    corrected_label: Label | None = None
    comment: str = Field(default="", max_length=1000)
    external_id: str = Field(min_length=1, max_length=150)


class Deployment(StrictModel):
    model_version: str
    reason: str = Field(min_length=5, max_length=300)


@app.middleware("http")
async def request_evidence(request: Request, call_next):
    if request.url.path not in {"/v1/predict", "/v1/batch"}:
        return await call_next(request)
    began = time.perf_counter()
    request_id = str(uuid.uuid4())
    occurred = stamp()
    request.state.events = []
    request.state.item_count = 1
    request.state.invalid_items = 0
    request.state.model_version = None
    try:
        with connect(readonly=True) as connection:
            request.state.model_version = active_version(connection)
        response = await call_next(request)
    except Exception:
        logger.exception("inference_request_failed request_id=%s", request_id)
        response = JSONResponse(
            status_code=503, content={"detail": "Inference unavailable", "request_id": request_id}
        )
    latency = (time.perf_counter() - began) * 1000
    status = response.status_code
    record = {
        "id": request_id,
        "occurred_at": occurred,
        "route": request.url.path,
        "mode": "batch" if request.url.path == "/v1/batch" else "realtime",
        "model_version": request.state.model_version,
        "status_code": status,
        "latency_ms": latency,
        "item_count": request.state.item_count,
        "invalid_items": request.state.invalid_items,
        "error_category": "malformed_input"
        if status == 422
        else (
            "inference_failure" if status >= 500 else ("authorization" if status == 401 else None)
        ),
        "source": "live",
    }
    try:
        with connect() as connection:
            insert(connection, "requests", record)
            if status < 400:
                for event in request.state.events:
                    event["request_id"] = request_id
                    insert(connection, "events", event)
        logger.info(
            json.dumps(
                {
                    "kind": "inference_request",
                    **record,
                    "prediction_count": len(request.state.events) if status < 400 else 0,
                },
                allow_nan=False,
            )
        )
    except sqlite3.Error:
        logger.exception("evidence_write_failed request_id=%s", request_id)
        # Do not acknowledge a prediction whose evidence was not durably written.
        response = JSONResponse(
            status_code=503,
            content={"detail": "Evidence store unavailable", "request_id": request_id},
        )
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(RequestValidationError)
async def malformed(request: Request, exc: RequestValidationError):
    if request.url.path in {"/v1/predict", "/v1/batch"}:
        locations = {
            e["loc"][2]
            for e in exc.errors()
            if len(e["loc"]) > 2 and e["loc"][1] == "tickets" and isinstance(e["loc"][2], int)
        }
        request.state.invalid_items = max(1, len(locations))
        if isinstance(exc.body, dict) and isinstance(exc.body.get("tickets"), list):
            request.state.item_count = len(exc.body["tickets"])
    # Never echo raw invalid inputs into logs or responses.
    return JSONResponse(
        status_code=422,
        content={"detail": [{"location": list(e["loc"]), "type": e["type"]} for e in exc.errors()]},
    )


def infer(ticket: Ticket, request: Request):
    started = time.perf_counter()
    result = predict(ticket.text, request.state.model_version)
    elapsed = (time.perf_counter() - started) * 1000
    event_id = str(uuid.uuid4())
    event = {
        "id": event_id,
        "request_id": "",
        "occurred_at": stamp(),
        "model_version": request.state.model_version,
        "text": redact(ticket.text),
        "language": ticket.language,
        "channel": ticket.channel,
        "text_length": len(ticket.text),
        "oov_ratio": result["oov_ratio"],
        "prediction": result["prediction"],
        "confidence": result["confidence"],
        "probabilities_json": json.dumps(result["probabilities"]),
        "anomaly": int(result["anomaly"]),
        "inference_ms": elapsed,
        "source": "live",
    }
    request.state.events.append(event)
    return {"event_id": event_id, "model_version": request.state.model_version, **result}


@app.get("/healthz", include_in_schema=False)
def health():
    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False)
def ready():
    with connect(readonly=True) as connection:
        version = active_version(connection)
    load_model(version)
    return {"status": "ready", "model_version": version}


@app.post("/v1/predict")
def realtime(ticket: Ticket, request: Request):
    return infer(ticket, request)


@app.post("/v1/batch")
def batch(payload: Batch, request: Request):
    request.state.item_count = len(payload.tickets)
    return {"predictions": [infer(ticket, request) for ticket in payload.tickets]}


def ingest(table, payload):
    fields = payload.model_dump()
    received_at = stamp()
    if table == "feedback":
        fields["comment"] = redact(fields["comment"])
    with connect() as connection:
        # Serialize idempotency check + insert across concurrent evidence requests.
        connection.execute("BEGIN IMMEDIATE")
        event = connection.execute(
            "SELECT 1 FROM events WHERE id=?", (payload.event_id,)
        ).fetchone()
        if not event:
            raise HTTPException(404, "Unknown event_id")
        existing = connection.execute(
            f"SELECT * FROM {table} WHERE external_id=?", (payload.external_id,)
        ).fetchone()
        if existing:
            if any(existing[key] != value for key, value in fields.items()):
                raise HTTPException(409, "external_id already belongs to different evidence")
            return {"status": "already-recorded", "id": existing["id"]}
        insert(connection, table, {**fields, "received_at": received_at})
        return {
            "status": "recorded",
            "id": connection.execute("SELECT last_insert_rowid()").fetchone()[0],
        }


@app.post("/v1/labels")
def ground_truth(payload: GroundTruth):
    return ingest("labels", payload)


@app.post("/v1/feedback")
def human_feedback(payload: Feedback):
    return ingest("feedback", payload)


@app.get("/v1/models")
def models():
    return {"models": queries.rows("SELECT * FROM models"), "deployments": queries.deployments()}


@app.post("/v1/deployments", status_code=201)
def deploy(payload: Deployment):
    with connect() as connection:
        if not connection.execute(
            "SELECT 1 FROM models WHERE version=?", (payload.model_version,)
        ).fetchone():
            raise HTTPException(404, "Unknown model version")
        load_model(payload.model_version)
        insert(connection, "deployments", {**payload.model_dump(), "deployed_at": stamp()})
    return {"status": "deployed", **payload.model_dump()}


def time_bounds(start: datetime | None, end: datetime | None):
    end = end or now()
    start = start or end - timedelta(hours=72)
    if not start.tzinfo or not end.tzinfo:
        raise HTTPException(422, "Use timezone-aware ISO timestamps")
    if start >= end or end - start > timedelta(days=31):
        raise HTTPException(422, "Window must be positive and at most 31 days")
    return stamp(start), stamp(end)


@app.get("/v1/metrics")
def metric_history(
    start: datetime | None = None,
    end: datetime | None = None,
    model_version: str = Query(default="__all__", max_length=100),
):
    first, last = time_bounds(start, end)
    return {"snapshots": queries.snapshots(first, last, model_version)}


@app.get("/v1/incidents")
def incident_list(
    start: datetime | None = None,
    end: datetime | None = None,
    model_version: str | None = Query(default=None, max_length=100),
):
    first, last = time_bounds(start, end)
    return {"incidents": queries.incidents(first, last, model_version)}


@app.get("/v1/incidents/{incident_id}")
def detail(incident_id: str):
    result = queries.incident_detail(incident_id)
    if result is None:
        raise HTTPException(404, "Incident not found")
    return result


@app.get("/v1/pipeline-runs")
def pipeline_runs():
    return queries.pipeline_health()
