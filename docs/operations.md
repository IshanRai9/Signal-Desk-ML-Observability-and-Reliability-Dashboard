# Operations and deployment

## Configuration

Environment variables override paths and operational scheduling. `.env.example` is a template;
copy it to `.env` for Compose. The local Python process reads environment variables, not `.env`
automatically. Set them in the shell when using native commands.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_PATH` | data/runtime/signaldesk.db | SQLite database path |
| `MODEL_DIR` | artifacts/models | Trusted local joblib artifacts |
| `ALERT_CONFIG` | config/alerts.json | Monitoring threshold file |
| `API_URL` | http://localhost:8000 | Workbench's API destination |
| `API_KEY` | unset | Optional local API key; required by deployment job |
| `MONITOR_INTERVAL_SECONDS` | 60 | Worker schedule after each completed pass |
| `SEED_DEMO` | true in Compose | Seed historical demo; use false with fresh production storage |
| `IMAGE` | signaldesk:local | Immutable registry image in deployment |
| `BROWSER_CHANNEL` | bundled Chromium | Browser smoke can use `chrome` when installed |

Inspect current configuration before changing it. Keep edits versioned and preserve existing values
unless intentionally changing them. Thresholds reload each pass; artifact versions are immutable.

## Deployment pipeline

1. Push to your own GitHub repository. CI runs lint, formatting, tests, native browser verification,
   image build and a real Compose health smoke. Pull requests run these checks without publishing.
2. Main or manual runs publish `ghcr.io/<owner>/<repo>:<commit-sha>` with the workflow's package token.
3. Provision a **private**, trusted Linux self-hosted runner labeled `signaldesk` with Docker/Compose,
   network access to GHCR, and registry pull credentials for private images. Do not allow untrusted
   repositories to schedule jobs on this runner.
4. Create the GitHub `production` environment and its protection rules. Add secret
   `PRODUCTION_API_KEY` and repository variable `DEPLOY_ENABLED=true`.
5. The deploy job pulls the exact verified image, initializes clean storage (`SEED_DEMO=false`), and
   runs `docker compose up -d --no-build --wait api monitor dashboard` under a stable project name.
   Named volumes persist independently of runner checkouts. Readiness checks fail the job on failure.

The workflow is infrastructure-ready configuration, not a claim that a target was provisioned.
API and dashboard ports bind to localhost. For remote use, put an authenticated HTTPS reverse proxy
or private tunnel in front of them. The API supports a single shared key; it does not implement users,
roles, per-client quotas, distributed authorization or automatic key rotation. Dashboard access must
be restricted separately: its server-side API key is intentionally never shown in the UI.

Rollback application code by selecting the previously verified commit-tagged `IMAGE` and running
Compose with `--no-build`. Roll back model selection through `/v1/deployments`; this appends history
instead of overwriting it. There are no automatic production rollback or destructive cleanup steps.

## Availability and scaling boundaries

This is a single-host production-style system, not a highly available platform. Use one initialization
job, one API worker and one monitor per evidence volume. SQLite permits one writer; writes are short,
WAL allows concurrent readers, and the 30-second busy timeout handles brief contention. Batch requests
execute synchronously in a worker thread and can consume more latency than single-ticket requests.
There is no broker, durable asynchronous batch queue or distributed scheduler.

The dashboard loads selected event cohorts into memory. Monitoring recalculates all history, which
is appropriate for this demonstration's thousands of events. At sustained scale, add retention,
dirty-window aggregation, server-side sampling and PostgreSQL before increasing worker counts.
No automated retention deletes records. An external watchdog must detect stopped/dead monitoring
workers; the dashboard displays snapshot age, but cannot page anyone when it is itself unavailable.

If the evidence store fails during inference, the API returns 503 and rolls back request/prediction
writes. It does not acknowledge an unlogged prediction. A complete database outage cannot itself be
recorded in that database; collect stdout with an independent log sink in a real deployment.

## Backup and privacy

Use SQLite's online backup API or stop writers before copying the database. Do not copy just the
`.db` file from a live WAL database and assume recent transactions were included. Back up model
artifacts alongside their registered checksums and retain the matching config/dataset versions.
Test restoration to fresh storage before trusting a backup.

Only synthetic data is supplied. Basic email and long-number redaction protects some obvious fields,
but is not comprehensive PII protection. Names, addresses and other private text may still remain in
inputs or review comments. Before real customer traffic, add an approved data-minimization policy,
retention/access controls and encryption appropriate to your deployment. The current API key should
be supplied through secrets, never committed or embedded in request examples.

## Recreating the demo safely

Bootstrap is idempotent and never deletes events. A fresh `DATABASE_PATH` and `MODEL_DIR`, or a new
`COMPOSE_PROJECT_NAME`, creates a separate demo without modifying the original dataset. Demo seeding
rejects a database with live traffic to prevent mixing synthetic historical deployments into a real
service history. Repeating bootstrap on a seeded database keeps its original anchor.
