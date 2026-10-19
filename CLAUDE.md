# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture Overview

This project is a two-stage data pipeline:

1. **Ingestion (AWS)**: A Python Lambda function fetches data from the football API-Sports (`v3.football.api-sports.io`) and writes raw data to S3 (`cmurillo-target-bucket`) with Hadoop-style partition paths (e.g., `players/league=39/season=2024/data.parquet`). AWS Step Functions orchestrates the Lambda invocations, iterating over league/season tuples and running team-stats and player-stats fetches in parallel per team.

2. **Processing (Databricks)**: A Databricks Asset Bundle reads the raw S3 data using Auto Loader (cloudFiles), transforms it, and writes Delta tables into the `soccer` Unity Catalog. The Databricks job has three sequential tasks: `team_ingestion` notebook → `playerwhl_task` Python wheel → `silver_layer_task` notebook.

### Lambda / Python code flow

```
lambda_handler.py         ← Lambda entry point; routes events by `action` field
  └── api_factory.py      ← APIFactory (ABC) → SoccerAPIFactory
        └── api_clients/
              ├── base_api_consumer.py  ← ABC: authenticate(), fetch_data()
              └── soccer_consumer.py    ← concrete impl; handles pagination,
                                           rate limiting, S3 writes
```

Adding a new API: subclass `base_api_consumer`, create a corresponding `XyzAPIFactory` in `api_factory.py`, and register it in the `factories` dict in `lambda_handler.py`.

### Step Functions workflow

The workflow (`infra/definitions/soccer_workflow.json.tpl`) accepts either an ad-hoc `{league, season}` payload or falls back to the `setup_array_tuple` Terraform variable. It maps over each tuple, fetches teams, then runs `get_team_stats` and `get_team_players` in parallel per team. Individual Lambda errors are caught and swallowed so the overall map iteration continues.

### Infrastructure (Terraform)

Two Terraform workspaces control environment-specific config defined in `infra/locals.tf`:

| Workspace | Target | Notes |
|-----------|--------|-------|
| `test` | LocalStack (`http://localhost:4566`) | Dummy credentials, no Lambda layer |
| `prod` | AWS | Uses `AWSSDKPandas-Python312` managed layer |

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
  --payload '{"action":"get_teams_from_league","league":239,"season":2024}' \
  response.json

# Start Step Function (LocalStack)
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:000000000000:stateMachine:soccer_data_workflow \
  --endpoint-url http://localhost:4566 \
  --input '{"league":239,"season":2024}'
```

### Databricks Asset Bundle

All commands run from `databricks_pipeline/soccer_api_consumer/`.

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
- **S3 bucket**: `cmurillo-target-bucket` — `TARGET_BUCKET` env var is set automatically by Terraform.
- **Databricks workspace**: `https://dbc-06090e42-ea4e.cloud.databricks.com`
- **Databricks cluster** (dev): update `cluster_id` in `databricks.yml` if the cluster changes.
- **Instance profile ARN** for Databricks→S3 access: configured in `databricks.yml` under `instance_profile_arn`.
- **Soccer API rate limit**: `soccer_consumer.py` sleeps 30 seconds every 5 teams to respect the free-tier rate limit.
- **`API_MAX_PAGES`** (Lambda env var, Terraform `api_max_pages`, default `"3"`): the API-Sports free plan rejects `page > 3` (returned as HTTP 200 with an `errors` body, raised as `soccer_api_error`). `_paginate_results` stops at this page and logs a `Truncating ... results` WARNING, so squads with more than 60 players are written partially. Set to `""` on a paid plan to remove the cap.
- **Free-plan limits**: 100 requests/day and seasons 2021–2023 only. `GET /status` does not count against the quota; use it to check usage before running live tests.

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
- LocalStack community does not support Lambda layers: bundle pandas/pyarrow/requests (`manylinux_2_28` wheels for pyarrow 23) directly into the function zip for local tests.
- Terraform has no workaround other than the exclusion; deploy to LocalStack with the `aws` CLI against `--endpoint-url http://localhost:4566` if needed.
