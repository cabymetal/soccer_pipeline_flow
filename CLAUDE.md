# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture Overview

This project is a two-stage data pipeline:

1. **Ingestion (AWS)**: A Python Lambda function fetches data from the football API-Sports (`v3.football.api-sports.io`, paid plan) and writes every API page unmodified, append-only, to S3 (bucket from the `TF_VAR_target_bucket_name` environment variable) under `raw/{entity}/{partitions}/ingest_date=YYYY-MM-DD/{run_id}_{tag}_pNNN.json` (e.g. `raw/fixture_details/league=39/season=2026/ingest_date=2026-10-19/...`). Each file is an envelope (`ingest_ts`, `run_id`, `endpoint`, `params`, `paging`, `results`, `response`). Two Step Functions workflows, started by EventBridge Scheduler, orchestrate it over 11 competitions: weekly (matches + match details, standings, player season stats) and monthly (leagues, teams, squads).

2. **Processing (Databricks)**: A Databricks Asset Bundle reads the raw S3 data using Auto Loader (cloudFiles), transforms it, and writes Delta tables into the `soccer` Unity Catalog. The Databricks job has three sequential tasks: `team_ingestion` notebook → `playerwhl_task` Python wheel → `silver_layer_task` notebook.

### Lambda / Python code flow

```
lambda_handler.py         ← Lambda entry point; ACTIONS dispatch table by `action` field
  └── api_factory.py      ← APIFactory (ABC) → SoccerAPIFactory
        └── api_clients/
              ├── base_api_consumer.py  ← ABC: authenticate(), fetch_data()
              └── soccer_consumer.py    ← concrete impl; fetch_and_store (pagination +
                                           raw JSON writes), header-based rate limiting/retries
```

Adding a new API: subclass `base_api_consumer`, create a corresponding `XyzAPIFactory` in `api_factory.py`, and register it in the `factories` dict in `lambda_handler.py`.

### Step Functions workflow

Lambda actions: `get_status`, `get_leagues(league)`, `get_teams(league, season)`, `get_squad(team)`, `get_team_players(team, season)`, `get_standings(league, season)`, `get_fixtures(league, season[, lookback_days])` (returns finished fixture ids in chunks of 20), `get_fixture_details(ids, league, season)` (one `fixtures?ids=` call returns events, lineups, statistics and players for up to 20 matches). Results come back as native JSON in `result`.

Competitions come from the `competitions` Terraform variable (league id, `type` league|cup, `has_standings`, `season`). European competitions use the start year as season (bump in July); Colombia (239, 241) uses the calendar year (bump in January).

- **`soccer_weekly_workflow`** (`infra/definitions/soccer_weekly.json.tpl`): `get_status` pre-flight (fails `QuotaTooLow` below `min_quota`) → per competition-season, in parallel: fixtures → match-detail batches; standings (if `has_standings`); for `type == "league"`, teams → player season stats per team (no `league` filter, so cup/UCL stats are included). Regular runs detail fixtures finished in the last `FIXTURE_LOOKBACK_DAYS` (10); the overlap re-fetches late corrections and Databricks dedupes on fixture id + latest `ingest_ts`. Input overrides: `{"fixtures_only": true}`, `{"mode": "backfill", "seasons": [2024, 2025, 2026]}`, `{"competitions": [...]}`.
- **`soccer_monthly_workflow`** (`infra/definitions/soccer_monthly.json.tpl`): leagues (output reports `current_season` vs configured season) → teams → squads of league teams.
- Schedules (UTC): weekly Mon 06:00, fixtures-only Thu 06:00, monthly 1st 04:00; enabled only in prod (`schedules_enabled` in `locals.tf`).
- Failed Lambda calls are retried, then caught into a `"failed"` entry in the execution output so the other items continue.

### Infrastructure (Terraform)

Two Terraform workspaces control environment-specific config defined in `infra/locals.tf`:

| Workspace | Target | Notes |
|-----------|--------|-------|
| `test` | LocalStack (`http://localhost:4566`) | Dummy credentials, no Lambda layer |
| `prod` | AWS | Uses `AWSSDKPandas-Python312` managed layer — needed for `requests`, which the python3.12 runtime lacks |

Terraform packages the entire `src/` directory into `lambda_function.zip` automatically via `archive_file`.

## Common Commands

### AWS Lambda / Terraform

```bash
# Install Python dependencies
pip install -r requirements.txt

# Unit tests (offline: moto for S3/Secrets Manager, mocked HTTP; config in pyproject.toml)
pip install -r requirements-dev.txt
pytest

# Local test of Lambda (uses SOCCER_API_KEY env var)
python src/lambda_handler.py

# Start LocalStack
docker-compose up -d localstack

# Bucket name comes from the environment, not from files
export TF_VAR_target_bucket_name=<your-bucket>

# Deploy to LocalStack (test workspace)
cd infra
terraform workspace select test
terraform apply

# Deploy to AWS (prod workspace)
cd infra
terraform workspace select prod
terraform apply

# Invoke Lambda locally against LocalStack
aws lambda invoke \
  --function-name api_consumer_lambda \
  --endpoint-url http://localhost:4566 \
  --payload '{"action":"get_fixtures","league":239,"season":2026,"lookback_days":10}' \
  response.json

# Start the weekly workflow (LocalStack), narrowed to one competition
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:000000000000:stateMachine:soccer_weekly_workflow \
  --endpoint-url http://localhost:4566 \
  --input '{"competitions":[{"league":239,"name":"Primera A","type":"league","has_standings":true,"season":2026}]}'

# One-time backfill (prod): whole seasons, all competitions (~1,250 requests)
aws stepfunctions start-execution \
  --state-machine-arn <weekly_state_machine_arn output> \
  --input '{"mode":"backfill","seasons":[2024,2025,2026]}'
```

### Databricks Asset Bundle

All commands run from `databricks_pipeline/soccer_api_consumer/`, after loading the repo-root `.env` (see Key Configuration).

```bash
# Install dev dependencies
uv sync --dev

# Run tests (requires Databricks Connect configured)
uv run pytest

# Run a single test file
uv run pytest tests/sample_taxis_test.py

# Build the Python wheel
uv build --wheel

# Deploy bundle to dev (default target)
databricks bundle deploy

# Deploy bundle to prod
databricks bundle deploy --target prod

# Run the soccer ingestion job
databricks bundle run soccer_api_consumer_job

# Run the DLT pipeline
databricks bundle run soccer_api_consumer_etl
```

## Key Configuration

- **Soccer API key** is read at Lambda runtime from AWS Secrets Manager (`prod/soccer/api`). The `API_SOCCER_API_KEY` Lambda env var is a fallback path, not the actual key value.
- **S3 bucket**: never hardcode the bucket name in repo files. Terraform reads it from `TF_VAR_target_bucket_name` (required variable `target_bucket_name`) and passes it to the Lambda as `TARGET_BUCKET`; the Databricks bundle reads it from `BUNDLE_VAR_source_path` (`s3://<bucket>`).
- **Databricks identifiers** are not stored in repo files. Load the gitignored repo-root `.env` (`set -a; . ./.env; set +a`) before running the CLI: `DATABRICKS_HOST` (workspace URL), `BUNDLE_VAR_cluster_id` (dev cluster), `BUNDLE_VAR_instance_profile_arn` (Databricks→S3 instance profile), `BUNDLE_VAR_source_path` (`s3://<bucket>`). The prod root path and owner use `${workspace.current_user.userName}`.
- **Soccer API rate limit**: header-based, no fixed sleeps. `_make_request` waits 60s when `X-RateLimit-Remaining` (per minute) reaches 1, retries HTTP 429 / `errors.rateLimit` (after 60s) and 5xx/network errors (2/4/8s), and raises `soccer_api_error` on other `errors` bodies (e.g. daily quota exhausted).
- **API plan / volume**: designed for the paid Pro tier (~7,500 requests/day). Estimated ~225 requests per weekly run, ~1,200/month, ~1,250 for the one-time 3-season backfill. `GET /status` does not count against the quota; the weekly workflow checks it first (`min_quota`, default 300).
- **`API_MAX_PAGES`** (Lambda env var, Terraform `api_max_pages`, default `""` = no cap): only for the free plan, which rejects `page > 3`; `_iter_pages` then stops at this page and logs a `Truncating ... results` WARNING.
- **Free-plan limits** (if ever downgraded): 100 requests/day, seasons 2021–2023 only, `page <= 3`; the weekly run (~225 requests) would fail the quota pre-flight.
- **`FIXTURE_LOOKBACK_DAYS`** (Terraform `fixture_lookback_days`, default 10): window of finished fixtures re-detailed by regular runs.

## Local Environment: Norton TLS interception (Windows dev machine)

Norton Antivirus "Web/Mail Shield" re-signs HTTPS traffic with its own root certificate (`CN=Norton Web/Mail Shield Root`). Windows trusts it, but tools with their own CA bundle don't. Symptoms:

- `terraform plan/validate`: `Failed to load plugin schemas ... x509: certificate signed by unknown authority`. Verified 2026-10-19: Norton intercepts TLS even on `127.0.0.1` (any port) and swaps self-signed certs for `CN=Norton Web/Mail Shield Self-signed Root`; Terraform pins each provider plugin's own cert, so the gRPC handshake always fails. A CA bundle cannot fix this. File-scan exclusions don't help; it must be excluded in Web/Mail Shield (SSL/TLS scanning) or the shield turned off.
- `aws` CLI: `SSL validation failed ... CERTIFICATE_VERIFY_FAILED`
- `pip` / `uv`: `invalid peer certificate: UnknownIssuer`
- LocalStack Lambdas calling external HTTPS APIs fail certificate verification

**Permanent fix (do this first)**: in Norton → Settings → Firewall/Antivirus → *Scans and Risks* → **Exclusions / Low Risks** → *Items to Exclude from Scans* (and, if present, Web/Mail Shield → *Exclusions* / disable "SSL/TLS scanning" for these apps), add:

- `C:\ProgramData\chocolatey\bin\terraform.exe` (and the real binary under `C:\ProgramData\chocolatey\lib\terraform\`)
- `<repo>\infra\.terraform\providers\` (provider plugin executables)
- `C:\Program Files\Amazon\AWSCLIV2\aws.exe`
- Python / uv executables used for this project

Verify with `cd infra && terraform validate` — it must not report "Failed to load plugin schemas".

**Workarounds without the exclusion** (verification stays on; never use `--no-verify-ssl`):

- uv: `uv pip install --native-tls ...` (or `UV_NATIVE_TLS=1`)
- aws CLI / boto3 / requests: build a bundle of certifi + the Norton root and point `AWS_CA_BUNDLE`, `REQUESTS_CA_BUNDLE` (and `curl --cacert`) at it:

  ```powershell
  $c = Get-ChildItem Cert:\LocalMachine\Root | Where-Object Subject -like '*Norton Web/Mail Shield Root*' | Select-Object -First 1
  $pem = "-----BEGIN CERTIFICATE-----`n" + [Convert]::ToBase64String($c.RawData, 'InsertLineBreaks') + "`n-----END CERTIFICATE-----`n"
  $certifi = python -c "import certifi; print(certifi.where())"
  New-Item -ItemType Directory -Force "$env:USERPROFILE\.certs" | Out-Null
  [IO.File]::WriteAllText("$env:USERPROFILE\.certs\norton-ca-bundle.pem", [IO.File]::ReadAllText($certifi) + "`n" + $pem)
  ```

- LocalStack smoke tests that call API-Sports: include that bundle in the test zip and set `REQUESTS_CA_BUNDLE=/var/task/ca-bundle.pem` on the function (LocalStack only — never ship it to AWS). In Git Bash set `MSYS_NO_PATHCONV=1` so `/var/task/...` isn't rewritten to a Windows path.
- LocalStack community does not support Lambda layers: bundle `requests` directly into the function zip for local tests (pandas/pyarrow are no longer used).
- pip in a fresh venv: `pip install --use-feature=truststore -r requirements-dev.txt` (uses the Windows store, which trusts Norton). The repo-root `.venv` is a dotenv file, not a virtualenv.
- Terraform has no workaround other than the exclusion; deploy to LocalStack with the `aws` CLI against `--endpoint-url http://localhost:4566` if needed.
