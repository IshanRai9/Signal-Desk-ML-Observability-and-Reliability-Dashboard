# Monitoring and alerting

Run `python -m signaldesk.cli worker` for a 60-second schedule, or `python -m signaldesk.cli monitor`
for a one-shot pass. `MONITOR_INTERVAL_SECONDS` configures the schedule. A pass recalculates every
retained hourly window so delayed/revised labels update original quality and incident evidence.
This trades efficiency for clear, deterministic correctness on a small local dataset.

## Metric definitions

- **Volume:** number of inference HTTP requests. **Prediction volume:** successful ticket events.
- **Throughput:** counts divided by elapsed seconds, including zero-traffic periods.
- **Latency:** NumPy linearly interpolated p50/p95/p99 across request wall times, including failures.
  Timing ends before the evidence-store commit and excludes transport/browser latency. Per-event
  inference timing is measured separately. Percentiles are not averaged across windows.
- **Error rate:** HTTP status >=400 divided by HTTP attempts. Invalid-input counts come from
  validation errors. Authorization failures are separately categorized but included in the denominator.
- **Quality:** accuracy and macro precision/recall/F1 across `account,billing,technical`, using only
  the latest adjudicated label for each prediction. Empty quality returns null and an empty matrix.
  Unsupported classes get zero per-class scores; all three classes remain in the macro denominator.
- **Label coverage:** labeled events / successful predictions. **Label delay p95:** arrival time
  minus event time in minutes. Feedback never silently becomes a ground-truth label.
- **Feedback rate:** distinct reviewed events / successful predictions. **Correction rate:** latest
  reviews whose non-null corrected class differs from the prediction / all reviewed events.
- **Low-confidence rate:** confidence <0.6 / successful predictions. **Confident-error rate:** wrong
  labeled events with confidence >=0.8 / all labeled events, not only confident labeled events.
- **Anomaly rate:** OOV unigram/bigram ratio >0.7 OR original text length >800 / successful events.
- **Estimated compute:** sum of inference milliseconds / 3,600,000 × assumed CPU-hour price.
  This is active wall-time cost, not CPU accounting or a bill. LLM metrics stay null/N/A.

## Drift

Reference: versioned `reference-v1.json`, scored with the healthy `router-v1` artifact.
Compare language, channel, input length and predicted-class histograms. Numeric length bin boundaries
are `[0,80,120,200,400,800,infinity]`; categorical bins use the union of observed and reference values.
Add 0.5 to each count and renormalize, including empty/unseen bins.

For smoothed reference proportions `p` and observed proportions `q`:

```text
PSI = Σ (q_i - p_i) × ln(q_i / p_i)
KL(current || reference) = Σ q_i × ln(q_i / p_i)
```

Input PSI triggers alerts; predicted-class PSI is shown as output drift. Drift signals indicate a
population change. They do not establish cause, semantic drift or business impact. Language/channel
are supplied metadata, not detected from text. The OOV rule provides separate vocabulary evidence.

## Configurable rules

Edit [config/alerts.json](../config/alerts.json). Existing state is read on each pass; no restart is
needed. Only hourly windows are supported and validated. Snapshot rows record the effective config
SHA-256; the JSON files are versioned for change review.

| Rule | Default | Gate / severity |
|---|---|---|
| Severe latency | p95 >500ms OR >3× healthy operational p95 (120ms) | ≥30 HTTP requests; critical |
| Errors | error rate >5% | ≥30 requests; critical |
| Quality degradation | macro F1 <0.80 OR >0.12 below healthy reference F1 | ≥30 requests and ≥30 labels; critical |
| Input drift | any input feature PSI >0.25 | ≥30 requests and ≥30 predictions; warning |
| Confidence | low-confidence rate >30% OR confident-error rate >10% | ≥30 requests; confident errors also need ≥30 labels; warning |
| Freshness | no successful prediction within 120 minutes | no sample minimum; warning |
| Pipeline | monitoring job exception | immediate critical incident; retry next schedule |

Minimum samples avoid turning one request into a production incident. A partial current-hour window
can alert once it passes the gate. The operational baseline in `config/baseline.json` is synthetic;
replace it with an approved measured baseline. Reference F1 is computed from the frozen healthy model
and reference labels. Thresholds are operational heuristics, not universal statistical significance.

## Incident lifecycle and evidence

An alert ID is deterministic over UTC window, model scope and category. Repeated monitoring updates
the same evidence record rather than creating duplicate alerts. A threshold breach in the current
hour is `open`; past closed windows become `resolved`, while retaining breach evidence. This state
means **ongoing window vs historical window**, not human acknowledgement or remediation approval.
The dashboard coalesces adjacent alert windows for display; drilldown keeps the exact hourly evidence.

Revised labels can resolve a quality rule on recomputation. Freshness incidents close after recent
successful traffic; pipeline incidents close after a successful monitoring run. Failed jobs also
remain in `pipeline_runs`. Historical label-feed failures are shown there, separate from HTTP errors.
An independent external watchdog is still needed to detect a worker that stops completely.

The incident view shows rule values, thresholds, likely failure category, model scope, input samples,
predictions, expected labels and slow/failed requests. Up to 30 prediction samples are prioritized by
wrong labeled outcomes, anomalies, then low confidence. Request samples prioritize HTTP errors,
then latency. An aggregate incident's samples retain real affected model versions.

Alerts are persisted and available through `/v1/incidents`; no email, Slack, paging or external
webhook is configured. Notification integrations should consume deterministic IDs so they can dedupe.
