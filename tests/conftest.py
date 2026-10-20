import copy
import json
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

BUCKET = "test-bucket"
REGION = "us-east-1"


# ---------------------------------------------------------------------------
# API-Sports payload builders (trimmed to the fields the consumer reads)
# ---------------------------------------------------------------------------

def api_page(response, current: int = 1, total: int = 1, errors=None) -> dict:
    return {
        "errors": errors if errors is not None else [],
        "paging": {"current": current, "total": total},
        "response": response,
    }


def team_item(team_id: int) -> dict:
    return {"team": {"id": team_id, "name": f"Team {team_id}"}, "venue": {}}


def player_item(player_id: int) -> dict:
    return {
        "player": {"id": player_id, "name": f"P. Player{player_id}"},
        "statistics": [
            {"league": {"id": 39}, "games": {"appearences": 10}},
            {"league": {"id": 45}, "games": {"appearences": 2}},
        ],
    }


def fixture_item(fixture_id: int, status: str = "FT",
                 date: str = "2026-10-18T15:00:00+00:00") -> dict:
    return {
        "fixture": {"id": fixture_id, "date": date, "status": {"short": status}},
        "league": {"id": 239, "season": 2026, "round": "Clausura - 15"},
    }


def league_item(league_id: int, seasons: list) -> dict:
    return {"league": {"id": league_id, "type": "League"}, "seasons": seasons}


def season_item(year: int, current: bool = False, players: bool = True,
                events: bool = True, standings: bool = True) -> dict:
    return {
        "year": year,
        "current": current,
        "coverage": {
            "fixtures": {"events": events, "lineups": events,
                         "statistics_fixtures": events, "statistics_players": events},
            "standings": standings,
            "players": players,
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    """Fake credentials so nothing can ever reach real AWS."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_REGION", REGION)
    monkeypatch.setenv("TARGET_BUCKET", BUCKET)
    monkeypatch.setenv("API_SOCCER_API_KEY", "prod/soccer/api")


@pytest.fixture(autouse=True)
def reset_module_caches(monkeypatch):
    """Module-level caches (S3 client, API key) must not leak between tests/moto mocks."""
    import lambda_handler
    from api_clients import soccer_consumer

    monkeypatch.setattr(soccer_consumer, "_s3_client", None)
    monkeypatch.setattr(lambda_handler, "_api_key", None)


@pytest.fixture(autouse=True)
def no_sleep(mocker):
    """The consumer sleeps on rate limits and retries; tests must not wait."""
    return mocker.patch("api_clients.soccer_consumer.time.sleep")


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(Bucket=BUCKET)
        yield client


@pytest.fixture
def client():
    from api_clients.soccer_consumer import soccer_consumer

    consumer = soccer_consumer(api_key="dummy")
    consumer.authenticate("dummy")
    consumer.set_run_context(run_id="run1", run_date="2026-10-19")
    return consumer


@pytest.fixture
def fake_api(mocker):
    """
    Patch requests.get to serve the given pages in order.
    The page is picked from params['page'] (default 1), mirroring API-Sports paging.
    Optional quota headers are returned on every response.
    Returns the mock so tests can inspect the calls made.
    """
    def _install(*pages, headers=None):
        def _get(url, headers=None, params=None, **_):
            page = (params or {}).get("page", 1)
            response = MagicMock(status_code=200, headers=dict(response_headers))
            response.json.return_value = copy.deepcopy(pages[page - 1])
            return response

        response_headers = headers or {}

        return mocker.patch("api_clients.soccer_consumer.requests.get", side_effect=_get)

    return _install


def list_keys(s3_client) -> list:
    return [obj["Key"] for obj in s3_client.list_objects_v2(Bucket=BUCKET).get("Contents", [])]


def read_json(s3_client, key) -> dict:
    return json.loads(s3_client.get_object(Bucket=BUCKET, Key=key)["Body"].read())
