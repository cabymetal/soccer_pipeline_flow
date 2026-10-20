# API Consumer Project

## Overview

This project is a serverless data pipeline that extracts football data from [API-Football](https://www.api-football.com/documentation-v3) (API-Sports v3) and stores the raw responses in AWS S3. AWS Lambda does the API calls, AWS Step Functions orchestrates them, EventBridge Scheduler runs them on a schedule, and Terraform manages the infrastructure. A Databricks Asset Bundle (`databricks_pipeline/`) processes the raw data into Delta tables.

The extraction is designed for a **paid API-Football plan**: about 1,200 API requests and about $0.50 of AWS per month for 11 competitions.

## Project Structure

```text
api_consumer/
├── infra/
│   ├── main.tf                      # Main Terraform configuration
│   ├── variables.tf                 # Terraform variables (competitions, quota guard, ...)
│   ├── locals.tf                    # Per-workspace settings (test = LocalStack, prod = AWS)
│   └── definitions/                 # Step Functions JSON templates
│       ├── soccer_weekly.json.tpl   # Weekly: matches, standings, player stats (+ backfill)
│       └── soccer_monthly.json.tpl  # Monthly: leagues, teams, squads
│
├── databricks_pipeline/
│   └── soccer_api_consumer/         # Databricks Asset Bundle
│       ├── databricks.yml           # Bundle configuration
│       ├── resources/               # Job and Pipeline definitions
│       └── src/                     # Bundle source code
│
├── src/                             # Lambda code (zipped by Terraform)
│   ├── lambda_handler.py            # Lambda entry point (ACTIONS dispatch table)
│   ├── api_factory.py               # Factory pattern for API clients
│   └── api_clients/
│       ├── base_api_consumer.py     # Abstract base class for API clients
│       ├── soccer_consumer.py       # API-Football consumer
│       └── spotify_consumer.py      # Spotify consumer stub
│
├── tests/                           # Offline unit tests (pytest + moto)
├── requirements.txt                 # Lambda runtime dependencies (requests, boto3)
└── requirements-dev.txt             # Test dependencies
```

## Features

- **Raw, append-only history**: every API page is stored unmodified, so nothing is lost and runs never overwrite each other
- **Incremental match ingestion**: only matches finished in the last 10 days are re-fetched; a backfill mode loads whole seasons
- **Request-efficient**: full match details (events, lineups, statistics, player stats) for 20 matches per request
- **Quota-aware**: checks the remaining daily quota before every run and respects the per-minute rate limit
- **Scheduled**: weekly, mid-week and monthly runs via EventBridge Scheduler
- **Infrastructure as Code**: Terraform with a LocalStack (`test`) and an AWS (`prod`) workspace
- **Modular design**: factory pattern for adding other APIs

## What Is Ingested

### Competitions

Configured in the `competitions` Terraform variable (`infra/variables.tf`):

| Country | League (id) | Cup (id) |
| --- | --- | --- |
| England | Premier League (39) | FA Cup (45) |
| Spain | La Liga (140) | Copa del Rey (143) |
| Italy | Serie A (135) | Coppa Italia (137) |
| Germany | Bundesliga (78) | DFB-Pokal (81) |
| Colombia | Primera A (239) | Copa Colombia (241) |
| Europe | UEFA Champions League (2) | — |

Each competition has its own `season`. European competitions use the year the season starts (`2026` = 2026/27, update it in July). Colombia uses the calendar year (update it in January). The monthly run reports the API's current season next to the configured one, so you can see when an update is due.

### Endpoints and Cadence

| Data | API endpoint | When |
| --- | --- | --- |
| Quota check (free) | `/status` | Before every weekly run |
| Fixtures and results | `/fixtures?league&season` | Weekly + mid-week |
| Match details (events, lineups, statistics, players) | `/fixtures?ids=` (20 matches per request) | Weekly + mid-week |
| Standings | `/standings` | Weekly (leagues + Champions League) |
| Player season stats (all competitions) | `/players?team&season` | Weekly (league teams) |
| League metadata and coverage | `/leagues?id` | Monthly |
| Teams | `/teams?league&season` | Monthly (and weekly for league teams) |
| Squads | `/players/squads?team` | Monthly (league teams) |

Team season statistics are not fetched. They are derived from the match data in Databricks.

### Raw Data Layout (S3)

```text
s3://cmurillo-target-bucket/raw/{entity}/{partitions}/ingest_date=YYYY-MM-DD/{run_id}_{tag}_pNNN.json

raw/fixture_details/league=39/season=2026/ingest_date=2026-10-19/<run>_fixtures_1208021-1208030_p001.json
raw/players/season=2026/team=33/ingest_date=2026-10-19/<run>_players_p002.json
raw/leagues/league=239/ingest_date=2026-11-01/<run>_leagues_p001.json
```

Each file holds one API page:

```json
{"ingest_ts": "...", "run_id": "...", "endpoint": "fixtures", "params": {...},
 "paging": {...}, "results": 20, "response": [ /* unmodified API response */ ]}
```

Recent matches are fetched again on later runs to pick up late stat corrections. Downstream, deduplicate on the fixture id and keep the latest `ingest_ts`.

### Volume and Cost (estimates)

| | API requests | AWS cost |
| --- | --- | --- |
| Weekly run | ~225 | ~$0.02 |
| Mid-week matches-only run | ~22 | negligible |
| Monthly run | ~142 | negligible |
| **Per month** | **~1,200** | **~$0.50** (mostly the Secrets Manager secret) |
| One-time backfill, 3 seasons | ~1,250 | < $0.10 |

The cheapest paid API-Football tier (about 7,500 requests per day) is enough. The free plan is not: it allows only 100 requests per day, seasons 2021–2023, and 3 pages per request.

## Prerequisites

### Local Development

- Python 3.12+
- Terraform 1.5+
- AWS CLI configured with appropriate credentials
- Docker (for LocalStack)
- Git

### AWS Account

- Permissions for S3, Lambda, IAM, Step Functions, EventBridge Scheduler, CloudWatch Logs and Secrets Manager
- A Secrets Manager secret `prod/soccer/api` containing `{"SOCCER_API_TOKEN": "<your API-Football key>"}`. Terraform reads it and does not create it.

## Setup Instructions

### 1. Clone the Repository

```bash
git clone <repository-url>
cd api_consumer
```

### 2. Install Python Dependencies

```bash
pip install -r requirements.txt        # Lambda runtime: requests, boto3
pip install -r requirements-dev.txt    # + pytest, moto, pytest-mock
```

### 3. Run the Tests

The tests are fully offline. S3 and Secrets Manager are mocked with moto, and the HTTP calls are mocked.

```bash
pytest
```

### 4. Configure AWS Credentials

```bash
aws configure
```

### 5. Set Up Terraform Variables (optional)

All variables have defaults. To override them, create `infra/terraform.tfvars`:

```hcl
# narrow or extend the competitions
# competitions = [
#   { league = 239, name = "Primera A", type = "league", has_standings = true, season = 2026 },
# ]
# fixture_lookback_days = 10
# min_quota             = 300
```

**Variables:**

- `competitions`: competitions to ingest (see above). `type` is `league` (player stats and squads per team) or `cup` (matches only).
- `api_soccer_api_key`: the **name** of the Secrets Manager secret (default `prod/soccer/api`), not the key itself.
- `api_max_pages` (default `""`, no cap): set it to `"3"` only on the free API plan.
- `fixture_lookback_days` (default 10): how far back regular runs re-fetch finished matches.
- `min_quota` (default 300): the weekly run stops if fewer daily requests than this remain.

### 6. Deploy Infrastructure

```bash
cd infra
terraform init
terraform workspace select prod
terraform plan
terraform apply
```

On the first apply after this redesign, import the Lambda log group that AWS created automatically:

```bash
terraform import aws_cloudwatch_log_group.lambda_log_group /aws/lambda/api_consumer_lambda
```

## Project Components

### Lambda Function

The Lambda function (`lambda_handler.py`) is the entry point. It:

- Receives events from Step Functions (`action` and its parameters; see `ACTIONS` in `lambda_handler.py`)
- Rejects unknown or incomplete events with `204` before reading the API key
- Creates the API client using the factory pattern
- Writes each API page to the raw zone in S3
- Returns small JSON results (team lists, fixture-id batches) for the orchestration

| Action | Parameters | Returns |
| --- | --- | --- |
| `get_status` | — | remaining daily quota |
| `get_leagues` | `league` | current season, per-season coverage |
| `get_teams` | `league`, `season` | `[{team, league, season}]` |
| `get_squad` | `team` | item count |
| `get_team_players` | `team`, `season` | item count |
| `get_standings` | `league`, `season` | item count |
| `get_fixtures` | `league`, `season`, optional `lookback_days` or `mode: "backfill"` | finished fixture ids in batches of 20 |
| `get_fixture_details` | `ids` (1–20), `league`, `season` | item count |

### API Clients

**Base Client (`base_api_consumer.py`)**

- Abstract base class for all API consumers (`authenticate`, `fetch_data`)

**Soccer Consumer (`soccer_consumer.py`)**

- API-Football v3 consumer
- `fetch_and_store` fetches every page of a request and writes each one to S3
- Rate limiting is based on the API's response headers. It waits when the per-minute quota runs out and retries rate-limit, 5xx and network errors.

**Spotify Consumer (`spotify_consumer.py`)**

- Stub, not used by the pipeline

### Step Functions Workflows

Two workflows, started by EventBridge Scheduler. The schedules are enabled in the `prod` workspace only.

- **`soccer_weekly_workflow`** (`definitions/soccer_weekly.json.tpl`), Mondays 06:00 UTC:
  - checks the quota first
  - then, for each competition: fixtures and match details, standings, and player season stats
  - Thursdays 06:00 UTC it runs again with `{"fixtures_only": true}`
- **`soccer_monthly_workflow`** (`definitions/soccer_monthly.json.tpl`), 1st of the month 04:00 UTC: league metadata, teams and squads.
- Failed Lambda calls are retried, then recorded as `"failed"` entries in the execution output. The other competitions keep running.

## Configuration

### Environment Variables (Lambda)

Set by Terraform in `main.tf`:

```hcl
environment {
    variables = {
        TARGET_BUCKET         = aws_s3_bucket.target_bucket.bucket
        API_SOCCER_API_KEY    = var.api_soccer_api_key     # Secrets Manager secret name
        API_MAX_PAGES         = var.api_max_pages
        FIXTURE_LOOKBACK_DAYS = var.fixture_lookback_days
    }
}
```

The Lambda has 256 MB and a 600 s timeout. Its logs are kept for 30 days.

### S3 Storage

Data is stored in the `cmurillo-target-bucket` S3 bucket:

- Write-only from Lambda, read access for Databricks (instance profile)
- Private ACL
- Force destroy enabled for development

## Usage

### Start a Workflow Manually

```bash
# full weekly run with the configured competitions
aws stepfunctions start-execution \
  --state-machine-arn <weekly_state_machine_arn> \
  --input '{}'

# a single competition
aws stepfunctions start-execution \
  --state-machine-arn <weekly_state_machine_arn> \
  --input '{"competitions":[{"league":239,"name":"Primera A","type":"league","has_standings":true,"season":2026}]}'

# one-time backfill of whole seasons (about 1,250 requests for all competitions)
aws stepfunctions start-execution \
  --state-machine-arn <weekly_state_machine_arn> \
  --input '{"mode":"backfill","seasons":[2024,2025,2026]}'
```

The ARNs are Terraform outputs: `terraform output weekly_state_machine_arn` and `terraform output monthly_state_machine_arn`.

Recommended first runs after upgrading the API plan:

1. Run a single competition and compare the quota used (from `/status`) with the estimate (~20 requests).
2. Backfill one season of one league and check it.
3. Run the full backfill.

### Invoke the Lambda Directly

```bash
aws lambda invoke \
  --function-name api_consumer_lambda \
  --payload '{"action":"get_status"}' \
  --cli-binary-format raw-in-base64-out \
  response.json
```

### View Logs

```bash
aws logs tail /aws/lambda/api_consumer_lambda --follow
```

## LocalStack Configuration

For local development and testing, the project can be deployed to **LocalStack**, a local AWS cloud emulator.

### Prerequisites for LocalStack

- Docker and Docker Compose installed
- `docker-compose.yml` in the project root

### Starting LocalStack

```bash
docker-compose up -d localstack
curl http://localhost:4566/_localstack/health
```

### Terraform Workspaces

| Workspace | Target | Lambda layer | Schedules |
| --- | --- | --- | --- |
| `test` | LocalStack (`http://localhost:4566`) | none: bundle `requests` into the zip for local tests | created, disabled |
| `prod` | AWS `us-east-1` | `AWSSDKPandas-Python312` (provides `requests`, which the Python 3.12 runtime lacks) | enabled |

```bash
cd infra
terraform workspace select test   # or: terraform workspace new test
terraform apply
```

Workspace settings live in `infra/locals.tf` (`test_config` / `prod_config`). The AWS provider routes the `lambda`, `s3`, `secretsmanager`, `iam`, `stepfunctions`, `logs` and `scheduler` endpoints to LocalStack in the `test` workspace, using path-style S3 addressing.

### Testing with LocalStack

```bash
aws lambda invoke \
  --function-name api_consumer_lambda \
  --endpoint-url http://localhost:4566 \
  --payload '{"action":"get_fixtures","league":239,"season":2026,"lookback_days":10}' \
  --cli-binary-format raw-in-base64-out \
  response.json

aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:000000000000:stateMachine:soccer_weekly_workflow \
  --endpoint-url http://localhost:4566 \
  --input '{"competitions":[{"league":239,"name":"Primera A","type":"league","has_standings":true,"season":2026}]}'
```

These calls reach the real API-Football service and use your quota.

### Troubleshooting LocalStack

#### "dial tcp: lookup localstack: no such host"

- LocalStack is not running: `docker ps | grep localstack`
- Check the endpoint URL in `locals.tf`

#### "couldn't find resource" (Secrets Manager)

- Create the `prod/soccer/api` secret in LocalStack first

#### "Step Functions validation failed"

- Ensure the `stepfunctions` endpoint is configured in the provider
- Validate the JSON in `soccer_weekly.json.tpl` / `soccer_monthly.json.tpl`

#### TLS / certificate errors on Windows (Norton)

- See the Norton section in `CLAUDE.md`

## Development

### Adding New API Consumers

1. Create a new file in `src/api_clients/` (e.g., `new_api_consumer.py`)
2. Subclass `base_api_consumer` and implement `authenticate` and `fetch_data`
3. Add a `XyzAPIFactory` in `api_factory.py` and register it in the `factories` dict in `lambda_handler.py`
4. Add tests under `tests/`

### Local Testing Against the Real API

```bash
export SOCCER_API_KEY=<your key>
python src/api_clients/soccer_consumer.py   # prints get_status(); does not use quota
```

## Troubleshooting

### `QuotaTooLow` Execution Failure

The weekly workflow stopped because fewer than `min_quota` daily requests remained. Check the API plan, or lower `min_quota`.

### Lambda Timeout

- Increase `timeout` in `main.tf`
- Check API response times and rate-limit waits in the logs

### S3 Permission Denied

- Verify the IAM role policy includes the S3 bucket ARN

### Step Function Failures

- View the execution history in the AWS Console. Failed items appear as `"failed"` entries in the execution output.
- Check CloudWatch Logs for error details

## Cleanup

To destroy all AWS resources:

```bash
cd infra
terraform destroy
```

**Note**: `force_destroy = true` on the S3 bucket means all data will be deleted.

## License

not licence at the moment

## Contact

email: cabymetal / gmail
