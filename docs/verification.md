# Verification evidence

Final local verification on Windows / Python 3.12:

- 20 unit/integration tests passed; 90% coverage of service and dashboard-query modules.
- Metric, alert, query and dashboard-data modules each reached 100% statement coverage.
- Ruff lint and formatting checks passed, `uv lock --check` passed, and
  `docker compose config --quiet` validated the Compose configuration.
- Metric arithmetic, unseen-category drift, five threshold types, no-label semantics and sparse-label
  gating were verified independently of charts.
- Fixed-anchor replay: 7,200 requests, 7,088 predictions; bad-model F1 0.333, language PSI 9.406,
  latency p95 2.28–2.35 seconds, and 11% incident-hour HTTP errors.
- API real-time and batch behavior, evidence idempotency, validation errors, inference failure,
  redaction, authentication, deployment version pinning, late labels and monitor recovery were tested.

- Chrome browser checks passed for all seven dashboard pages, actual live inference and review,
  and the mobile overview at 390×844 with no horizontal page overflow.
- The API and dashboard were run as local servers; the monitoring worker runs independently every
  60 seconds. The running HTTP inference → delayed label → monitoring → metrics API flow was checked.
- Real desktop screenshots for all pages and a mobile screenshot are in `docs/screenshots`.
  README images are application captures, not design mockups.

The test suite emits one upstream Starlette warning about future HTTP test-client dependencies;
it does not affect results. Coverage excludes the rendering-heavy Streamlit page module; that surface
was checked in Chrome. CLI commands were exercised by bootstrap, the worker and live smoke checks.

The simplification pass applied eight behavior-preserving reuse, quality and efficiency suggestions:
shared hash calculation, Pydantic-owned whitespace validation, normalized evidence reuse, registry
checksum reuse, an unused workbench parameter, one config read per anomaly filter, streamed JSONL
uploads, and avoiding already-registered model fitting. SQL-side incident sampling was deferred:
current bounded-window sorting is tested and acceptable at demonstration volume.

Docker CLI is installed on the development machine, but its engine was unavailable. The Docker image
and Compose stack could not be run locally. CI defines both image and Compose smoke tests; their
result requires executing the workflow on a repository runner. During the initial build, no GitHub remote or deployment target
was supplied, so no remote workflow or deployment is claimed.

Code review: skipped (ce-code-review unavailable). Its scope gate could not resolve a Git diff/base
in the initially empty, non-Git workspace. The `ce-simplify-code` skill performed three independent
read-only reviews, and implementation received an explicit manual review in addition to automated
verification. The initial build ended without a commit or publication. The subsequent Git handoff commits and pushes the source, tests, synthetic datasets, screenshots and documentation to the user-approved GitHub repository. No remote deployment is claimed.
