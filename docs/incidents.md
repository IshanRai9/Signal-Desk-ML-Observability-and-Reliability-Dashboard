# Seeded incident report

This is a reproducible synthetic replay, not a report of real customer outages. The default run
contains 72 hourly cohorts, 100 attempts per cohort, 7,200 HTTP requests and 7,088 successful events.
`T0` is the replay start; bootstrap anchors it to the current UTC hour minus 72 hours. Fixed anchors
are supported for tests. Metrics below were queried from persisted hourly snapshots after replay.

## 1. Model degradation: T0+24h through +32h

**Detection:** deployment `router-v2-bad` changes macro F1 from **1.000 to 0.333**. It breaches the
0.80 floor and the 0.12 drop threshold. The confident-error rate rises to **66.7%**, despite no
low-confidence increase. Label arrival is delayed 1–6 hours; detection can only use arrived labels.
Historical recomputation fills in those earlier cohorts as labels become available.

**Diagnosis:** the quality page's confusion matrix shows account and billing classes exchanged,
while technical outcomes stay correct. Latency remains ~104–107ms p95 and language PSI is zero.
The deployment table pins the change to `router-v2-bad`; incident samples show high-confidence wrong
labels. Its artifact was actually trained with billing/account labels swapped, rather than having
the chart scores manually overwritten. This supports a label-mapping regression hypothesis.

**Recovery:** replay contains an explicit rollback deployment to `router-v1` at T0+32h. F1 recovers
to 1.000 in later healthy labeled cohorts. For a live rollback, POST `/v1/deployments` with the healthy
version and a reason. Check the next version-specific windows and keep the old incident evidence.

**Prevention:** verify taxonomy mappings before release; compare per-class confusion and quality
against the healthy version. Confidence alone would have missed this failure.

## 2. Input drift: T0+40h through +48h

**Detection:** language PSI reaches **9.406** and length PSI **8.27–8.43**, well above 0.25. Channel
changes from email/web to WhatsApp. **100%** of these predictions have confidence below 0.6.
The input metadata and OOV/length anomaly heuristics detect the unfamiliar population without labels.

**Diagnosis:** model version remains `router-v1`. Input samples contain long Spanish tickets with
mostly unseen vocabulary; class proportions and predictions differ substantially from the frozen
English reference. Later adjudication shows hourly macro F1 around **0.037–0.071**. Drift and quality
are separate signals: the first indicates a population shift; the second establishes observed
classification degradation within the labeled subset.

**Recovery:** the replay returns to its baseline English population at T0+48h. Input PSI and low
confidence return to healthy values in subsequent cohorts. For real traffic, route unsupported
language or low-confidence tickets to review, collect representative labeled examples, and validate
a new model on an independent multilingual holdout before deployment.

**Limits:** the replay's drift labels use a synthetic 80% billing mix, rather than independently
labeling every Spanish text. That makes it a fault scenario, not a multilingual benchmark. Synthetic
reference and training data share templates, so healthy F1 is deliberately optimistic.

## 3. Latency and availability: T0+56h through +60h

**Detection:** hourly HTTP p95 rises to **2,284–2,348ms**, compared with healthy p95 of ~106ms.
Both the 500ms absolute and 3× baseline rules trigger. Error rate rises from **1% to 11%**:
ten simulated 503s plus one validation rejection per 100 attempts.

**Diagnosis:** the latency page shows p50, p95 and p99 moving together. Incident request samples
prioritize the slow 503s; successful predictions retain healthy confidence and F1=1.000. Language
PSI stays zero, and there is no model deployment at the onset. The evidence is consistent with a
serving-path reliability problem rather than a new classifier-quality regression. The exact
infrastructure cause is unknown: the scenario injects timings and statuses into the historical log.

**Recovery:** p95 and error rate normalize after T0+60h in the replay. A historical failed label-feed
run at +57h is visible in pipeline health; it is separately identified and does not fabricate a model
quality score. In real operations, inspect saturation, queueing and upstream dependencies, restore
capacity, then verify latency/errors while labels continue to arrive.

**Limits:** this is not a CPU stress test, distributed trace or measured load benchmark. The live API
measures request time; only the historical incident timing is simulated. Likely-cause text remains
a heuristic and never claims an unobserved root cause.

## Automated evidence

`tests/test_monitoring.py` replays a fixed anchor and verifies bad-model F1 <0.5, language PSI >0.25,
latency p95 >2 seconds, per-version filtering, 503 drilldown samples, deterministic incident IDs,
idempotent seeding, historical label backfill, freshness and monitoring-failure recovery.
