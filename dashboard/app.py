"""Run from repository root: streamlit run dashboard/app.py."""

import html
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard.data import episodes, load
from signaldesk import queries
from signaldesk.db import database_path
from signaldesk.metrics import CLASSES
from signaldesk.settings import now, stamp, thresholds

st.set_page_config(page_title="SignalDesk · ML reliability", page_icon="◈", layout="wide")
st.logo(
    str(Path(__file__).with_name("logo.svg")),
    size="large",
    icon_image=str(Path(__file__).with_name("icon.svg")),
)
px.defaults.color_discrete_sequence = ["#087f8c", "#e6a14a", "#ce5973", "#6579b8"]
st.markdown(
    """
<style>
.block-container {padding-top:2rem; padding-bottom:3rem; max-width:1600px}
h1 {font-size:2.15rem!important; letter-spacing:-1px; font-weight:750!important}
h2 {font-size:1.3rem!important; letter-spacing:-.4px}
h3 {font-size:1rem!important}
[data-testid="stSidebar"] {background:#101c31}
[data-testid="stSidebar"] * {color:#d7e2f2}
[data-testid="stSidebar"] [data-baseweb="select"] * {color:#15243b}
[data-testid="stSidebar"] [data-testid="stSelectbox"] * {color:#15243b}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] * {color:#d7e2f2}
[data-testid="stSidebar"] button * {color:#15243b}
[data-testid="stMetric"] {background:white;border:1px solid #e0e6ee;border-radius:12px;padding:16px}
[data-testid="stMetricLabel"] {color:#69798e;font-size:12px}
[data-testid="stMetricValue"] {font-size:1.85rem;font-weight:700}
[data-testid="stVerticalBlockBorderWrapper"] {border-radius:12px}
.eyebrow {color:#718197;letter-spacing:1.9px;font-size:11px;font-weight:650;margin:0 0 8px}
.status {display:inline-block;border-radius:20px;background:#dff3ec;color:#167251;
padding:5px 11px;font-size:12px;font-weight:600}
.status.stale {background:#fff0d7;color:#996018}
.note {color:#6a7b91;font-size:13px;line-height:1.6}
.banner {background:#fff3e5;border-left:3px solid #db8e30;padding:14px 18px;border-radius:7px;
font-size:13px;margin:10px 0 24px;color:#83571f}
#MainMenu {visibility:hidden} footer {visibility:hidden}
</style>
""",
    unsafe_allow_html=True,
)

if not database_path().exists():
    st.title("SignalDesk")
    st.info("Initialize the evidence store before opening the dashboard.")
    st.code("python -m signaldesk.cli bootstrap")
    st.stop()

with st.sidebar:
    st.caption("ENVIRONMENT")
    st.markdown("●  Local production simulation")
    st.divider()


def header(title, description):
    st.markdown(
        '<div class="eyebrow">SUPPORT ROUTING / RELIABILITY WORKSPACE</div>', unsafe_allow_html=True
    )
    st.title(title)
    st.caption(description)


def fmt(value, kind="number"):
    if value is None:
        return "—"
    if kind == "percent":
        return f"{value:.1%}"
    if kind == "ms":
        return f"{value:,.0f} ms"
    return f"{value:,.0f}"


def cards(items):
    for column, (label, value, help_text) in zip(st.columns(len(items)), items, strict=True):
        column.metric(label, value, help=help_text)


def chart(figure, height=300):
    figure.update_layout(
        height=height,
        margin=dict(l=15, r=15, t=25, b=20),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#53647d", size=11),
        colorway=["#087f8c", "#e6a14a", "#ce5973", "#6579b8"],
        legend=dict(orientation="h", y=1.13, x=0),
        hovermode="x unified",
    )
    figure.update_xaxes(gridcolor="#e8edf4", title=None)
    figure.update_yaxes(gridcolor="#e8edf4")
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})


def trend(history, columns, title, ytitle=""):
    st.subheader(title)
    if not history:
        st.info("No monitoring snapshots in this window. Run the monitoring job.")
        return
    frame = pd.DataFrame(
        [
            {"time": r["window_start"], **{name: r.get(key) for name, key in columns.items()}}
            for r in history
        ]
    )
    figure = px.line(frame, x="time", y=list(columns), labels={"value": ytitle, "variable": ""})
    chart(figure)


def incident_table(incident_rows):
    grouped = episodes(incident_rows)
    if not grouped:
        st.success("No threshold breaches in this selection.")
        return
    frame = pd.DataFrame(
        [
            {
                "Severity": r["severity"].upper(),
                "Finding": r["category"].title(),
                "From (UTC)": r["window_start"][:16].replace("T", " "),
                "To (UTC)": r["window_end"][:16].replace("T", " "),
                "Alert windows": r["windows"],
                "State": r["status"],
            }
            for r in grouped
        ]
    )
    st.dataframe(frame, hide_index=True, width="stretch")


def overview(data):
    header(
        "Service overview",
        "From inference to evidence. Track the health of every routing decision.",
    )
    summary = data["summary"]
    recent = queries.rows("SELECT MAX(computed_at) AS latest FROM metric_snapshots")[0]["latest"]
    monitor_age = (now() - datetime.fromisoformat(recent)).total_seconds() if recent else None
    st.markdown(
        f'<span class="status {"stale" if monitor_age is None or monitor_age >= 180 else ""}">'
        "MONITOR "
        f"{'CURRENT' if monitor_age is not None and monitor_age < 180 else 'STALE'}</span>"
        ' <span class="note">&nbsp; Last calculation: '
        f"{html.escape(recent[:19] if recent else 'never')} UTC</span>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="banner">Historical fault replay is active in this dataset. '
        "Quality, drift and latency incidents below come from persisted synthetic traffic; "
        "live API traffic is tagged separately.</div>",
        unsafe_allow_html=True,
    )
    cards(
        [
            ("HTTP REQUESTS", fmt(summary["request_count"]), "Each batch is one HTTP request."),
            (
                "P95 LATENCY",
                fmt(summary["latency_p95_ms"], "ms"),
                "API wall time before evidence commit; seeded timings are synthetic.",
            ),
            (
                "ERROR RATE",
                fmt(summary["error_rate"], "percent"),
                "HTTP 4xx and 5xx / inference HTTP requests.",
            ),
            (
                "MACRO F1",
                fmt(summary["quality"]["f1"], "percent"),
                "Latest available adjudicated labels only.",
            ),
            (
                "LABEL COVERAGE",
                fmt(summary["label_coverage"], "percent"),
                "Labeled predictions / successful predictions.",
            ),
        ]
    )
    left, right = st.columns([1.5, 1])
    with left, st.container(border=True):
        trend(
            data["history"], {"Requests / hour": "request_count"}, "Traffic over time", "requests"
        )
    with right, st.container(border=True):
        st.subheader("Prediction confidence")
        chart(
            px.bar(
                x=[f"{i / 10:.1f}–{(i + 1) / 10:.1f}" for i in range(10)],
                y=summary["confidence_histogram"],
                labels={"x": "confidence", "y": "predictions"},
            )
        )
    st.subheader("Detected incidents")
    incident_table(data["incidents"])
    with st.expander("Data freshness & pipeline health", expanded=False):
        latest = queries.rows("SELECT MAX(occurred_at) AS latest FROM events")[0]["latest"]
        st.write(f"Latest successful prediction: {latest or 'none'}")
        cards(
            [
                (
                    "THROUGHPUT",
                    f"{summary['throughput_rps']:.3f} req/s",
                    "Measured over the entire selected time interval.",
                ),
                (
                    "MALFORMED ITEMS",
                    fmt(summary["invalid_items"]),
                    "Validation rejects; invalid batches reject all items.",
                ),
                (
                    "LABEL DELAY P95",
                    f"{summary['label_delay_p95_minutes']:.0f} min"
                    if summary["label_delay_p95_minutes"] is not None
                    else "—",
                    "Arrival minus original prediction timestamp.",
                ),
            ]
        )
        st.dataframe(pd.DataFrame(queries.pipeline_health()), hide_index=True, width="stretch")


def quality(data):
    header("Model quality", "Adjudicated outcomes, human corrections and version-level evidence.")
    summary, q = data["summary"], data["summary"]["quality"]
    cards(
        [
            (
                name.upper(),
                fmt(q[key], "percent"),
                "Macro average over the fixed three-class taxonomy."
                if key != "accuracy"
                else "Correct labeled predictions / all labeled predictions.",
            )
            for name, key in [
                ("Accuracy", "accuracy"),
                ("Precision", "precision"),
                ("Recall", "recall"),
                ("F1 score", "f1"),
            ]
        ]
    )
    st.caption(
        f"{q['label_count']:,} adjudicated labels · "
        f"{fmt(summary['label_coverage'], 'percent')} coverage · "
        "feedback never silently becomes ground truth"
    )
    left, right = st.columns([1.3, 1])
    with left, st.container(border=True):
        history = [
            {**r, "f1": r["quality"]["f1"], "accuracy": r["quality"]["accuracy"]}
            for r in data["history"]
        ]
        trend(
            history,
            {"Macro F1": "f1", "Accuracy": "accuracy"},
            "Quality through deployments",
            "score",
        )
    with right, st.container(border=True):
        st.subheader("Confusion matrix")
        if q["confusion_matrix"]:
            chart(
                px.imshow(
                    q["confusion_matrix"],
                    x=CLASSES,
                    y=CLASSES,
                    text_auto=True,
                    color_continuous_scale="Teal",
                    labels={"x": "predicted", "y": "expected"},
                )
            )
        else:
            st.info("No labels available. Quality is unknown.")
    st.dataframe(pd.DataFrame(q["per_class"]), hide_index=True, width="stretch")
    cards(
        [
            (
                "HUMAN FEEDBACK RATE",
                fmt(summary["feedback_rate"], "percent"),
                "Distinct reviewed events / successful predictions.",
            ),
            (
                "CORRECTION RATE",
                fmt(summary["correction_rate"], "percent"),
                "Reviewed events with a changed class / reviewed events.",
            ),
            (
                "CONFIDENT ERRORS",
                fmt(summary["overconfidence_error_rate"], "percent"),
                "Wrong predictions with confidence ≥0.8 / labeled predictions.",
            ),
        ]
    )
    st.subheader("Deployment history")
    deployment_rows = queries.deployments()
    st.dataframe(
        pd.DataFrame(deployment_rows)[
            ["deployed_at", "model_version", "reason", "dataset_version", "prompt_version"]
        ],
        hide_index=True,
        width="stretch",
    )
    with st.expander("Reproducibility manifests"):
        for row in queries.rows("SELECT * FROM models"):
            st.json(row)


def drift(data):
    header("Data drift", "Compare incoming traffic with the frozen reference-v1 population.")
    metrics = data["summary"]["drift"]
    cards(
        [
            (
                feature.upper().replace("_", " ") + " PSI",
                f"{values['psi']:.3f}" if values["psi"] is not None else "—",
                "Population Stability Index; alert threshold 0.25. Prediction PSI is output drift.",
            )
            for feature, values in metrics.items()
        ]
    )
    for pair in [("language", "channel"), ("text_length", "prediction")]:
        for column, feature in zip(st.columns(2), pair, strict=True):
            with column, st.container(border=True):
                st.subheader(feature.replace("_", " ").title() + " distribution")
                values = metrics[feature]
                frame = pd.DataFrame(
                    {
                        "bin": values["bins"],
                        "Reference": values["reference"],
                        "Observed": values["current"],
                    }
                )
                chart(
                    px.bar(
                        frame,
                        x="bin",
                        y=["Reference", "Observed"],
                        barmode="group",
                        labels={"value": "share", "variable": ""},
                    )
                )
                st.caption(
                    f"KL(current ‖ reference): {values['kl']:.3f}"
                    if values["kl"] is not None
                    else "No data"
                )
    st.caption(
        "PSI uses fixed numeric bins and additive 0.5 smoothing. "
        "Drift is a distribution-change heuristic, not proof of quality loss."
    )
    st.subheader("Low-confidence & anomalous predictions")
    low_confidence_threshold = thresholds()["low_confidence_threshold"]
    selected = [
        e for e in data["events"] if e["anomaly"] or e["confidence"] < low_confidence_threshold
    ]
    if selected:
        st.dataframe(
            pd.DataFrame(selected)[
                [
                    "id",
                    "occurred_at",
                    "model_version",
                    "text",
                    "language",
                    "confidence",
                    "oov_ratio",
                    "anomaly",
                    "expected_label",
                ]
            ],
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("No predictions meet the anomaly or low-confidence rules in this selection.")


def latency(data):
    header(
        "Latency & reliability",
        "HTTP wall time, service errors and throughput across the inference path.",
    )
    summary = data["summary"]
    cards(
        [
            (
                f"P{p} LATENCY",
                fmt(summary[f"latency_p{p}_ms"], "ms"),
                "All inference HTTP attempts, including errors.",
            )
            for p in [50, 95, 99]
        ]
    )
    with st.container(border=True):
        trend(
            data["history"],
            {"P50": "latency_p50_ms", "P95": "latency_p95_ms", "P99": "latency_p99_ms"},
            "Latency percentiles",
            "milliseconds",
        )
    left, right = st.columns(2)
    with left, st.container(border=True):
        trend(data["history"], {"HTTP error rate": "error_rate"}, "Failed requests", "fraction")
    with right, st.container(border=True):
        trend(
            data["history"],
            {
                "Requests / second": "throughput_rps",
                "Predictions / second": "prediction_throughput_rps",
            },
            "Throughput",
            "per second",
        )
    if data["requests"]:
        frame = pd.DataFrame(data["requests"])
        st.subheader("Request outcomes")
        st.dataframe(
            frame.groupby(["mode", "status_code", "error_category"], dropna=False)
            .agg(requests=("id", "count"), p95_ms=("latency_ms", lambda s: s.quantile(0.95)))
            .reset_index(),
            hide_index=True,
            width="stretch",
        )


def cost(data):
    header(
        "Cost & usage",
        "Transparent estimates for a local classifier. No LLM provider is configured.",
    )
    summary = data["summary"]
    cards(
        [
            (
                "ESTIMATED ACTIVE COMPUTE",
                f"${summary['estimated_compute_usd']:.6f}",
                "Inference wall time × assumed CPU-hour price; not billed cost.",
            ),
            (
                "PREDICTIONS",
                fmt(summary["prediction_count"]),
                "Successful per-ticket inference events.",
            ),
            ("LLM TOKENS", "N/A", "This model uses TF-IDF and logistic regression."),
            ("LLM COST", "N/A", "No language model calls or token charges."),
        ]
    )
    with st.container(border=True):
        trend(
            data["history"],
            {"Estimated USD": "estimated_compute_usd"},
            "Estimated active inference compute",
            "USD",
        )
    st.info(
        "Token usage, citation failures, hallucination flags and safety violations "
        "are not applicable to this classifier. They are stored as null, "
        "never reported as zero or presented as measured checks."
    )
    st.markdown(
        "The estimate excludes idle capacity, request overhead, storage, dashboard "
        "and monitoring. Wall time is a proxy for active compute, and does not "
        "measure CPU utilization. Replace the configured rate "
        "with your own cost model before budgeting."
    )
    with st.expander("Current alert thresholds & cost assumptions"):
        st.json(thresholds())


def incidents(data):
    header(
        "Incident investigation",
        "Move from a threshold breach to the requests and predictions behind it.",
    )
    incident_table(data["incidents"])
    choices = data["incidents"]
    if not choices:
        return
    selected = st.selectbox(
        "Investigate an alert window",
        choices,
        format_func=lambda r: (
            f"{r['category'].upper()} · {r['window_start'][:16]} UTC · {r['model_version']}"
        ),
    )
    details = queries.incident_detail(selected["id"])
    left, right = st.columns([1, 1.5])
    with left, st.container(border=True):
        st.subheader("Failure hypothesis")
        st.write(selected["likely_cause"])
        st.caption("Heuristic diagnosis. Confirm with source data and deployment evidence.")
        st.write(f"**Window:** {selected['window_start']} → {selected['window_end']}")
        st.write(f"**State:** {selected['status']} · **Severity:** {selected['severity']}")
    with right, st.container(border=True):
        st.subheader("Trigger evidence")
        st.json(json.loads(selected["evidence_json"]))
    st.subheader("Affected predictions")
    if details["samples"]:
        st.dataframe(
            pd.DataFrame(details["samples"])[
                [
                    "id",
                    "model_version",
                    "text",
                    "prediction",
                    "expected_label",
                    "confidence",
                    "oov_ratio",
                    "source",
                ]
            ],
            hide_index=True,
            width="stretch",
        )
    st.subheader("Slow or failed HTTP requests")
    st.dataframe(pd.DataFrame(details["requests"]), hide_index=True, width="stretch")
    st.caption(
        "Samples are bounded to 30 rows per alert window. Missing expected labels "
        "stay unknown. Closed historical windows are resolved; "
        "the report preserves their breach evidence."
    )


def live():
    header(
        "Inference workbench",
        "Send live traffic, then attach independent feedback or adjudicated ground truth.",
    )
    api_url = os.getenv("API_URL", "http://localhost:8000")
    headers = {"X-API-Key": os.getenv("API_KEY", "")}
    text = st.text_area(
        "Support ticket",
        "I was charged twice for my monthly subscription. Please refund the duplicate payment.",
    )
    if st.button("Classify ticket", type="primary"):
        try:
            response = httpx.post(
                f"{api_url}/v1/predict", json={"text": text}, headers=headers, timeout=30
            )
            response.raise_for_status()
            st.session_state["last_prediction"] = response.json()
        except httpx.HTTPError as exc:
            st.error(f"API request failed: {exc}")
    if "last_prediction" in st.session_state:
        result = st.session_state["last_prediction"]
        st.json(result)
        with st.form("review"):
            expected = st.selectbox("Reviewed class", CLASSES)
            evidence_type = st.radio(
                "Evidence type", ["Human feedback", "Adjudicated ground truth"]
            )
            evidence_id = st.text_input("Unique evidence ID", value=f"review-{result['event_id']}")
            submitted = st.form_submit_button("Record review")
        if submitted:
            route = "feedback" if evidence_type == "Human feedback" else "labels"
            field = "corrected_label" if route == "feedback" else "label"
            try:
                response = httpx.post(
                    f"{api_url}/v1/{route}",
                    json={
                        "event_id": result["event_id"],
                        field: expected,
                        "external_id": evidence_id,
                    },
                    headers=headers,
                    timeout=10,
                )
                response.raise_for_status()
                st.success(
                    f"Evidence {response.json()['status']}. "
                    "Monitoring incorporates it on the next run."
                )
            except httpx.HTTPError as exc:
                st.error(f"Evidence request failed: {exc}")


page = st.navigation(
    [
        st.Page(
            lambda: overview(DATA), title="Overview", icon=":material/dashboard:", default=True
        ),
        st.Page(
            lambda: quality(DATA),
            title="Model quality",
            icon=":material/verified:",
            url_path="quality",
        ),
        st.Page(lambda: drift(DATA), title="Data drift", icon=":material/waves:", url_path="drift"),
        st.Page(
            lambda: latency(DATA), title="Latency", icon=":material/speed:", url_path="latency"
        ),
        st.Page(
            lambda: cost(DATA), title="Cost & usage", icon=":material/payments:", url_path="cost"
        ),
        st.Page(
            lambda: incidents(DATA),
            title="Incidents",
            icon=":material/troubleshoot:",
            url_path="incidents",
        ),
        st.Page(
            live,
            title="Live inference",
            icon=":material/play_circle:",
            url_path="live",
        ),
    ]
)

with st.sidebar:
    st.divider()
    hours = st.selectbox(
        "Time range", [24, 48, 72, 168], index=2, format_func=lambda v: f"Last {v} hours"
    )
    versions = [r["version"] for r in queries.rows("SELECT version FROM models ORDER BY version")]
    selected_version = st.selectbox("Model version", ["All versions", *versions])
    st.button("Refresh evidence", width="stretch")
    st.caption(
        "UTC timestamps · read-only evidence store\n\n"
        "Monitoring runs every 60 seconds. Refresh to load new evidence."
    )

ending = now()
starting = (ending - timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)
DATA = load(
    stamp(starting), stamp(ending), None if selected_version == "All versions" else selected_version
)
page.run()
