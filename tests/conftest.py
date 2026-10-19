import copy
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
        "player": {
            "id": player_id,
            "name": f"P. Player{player_id}",
            "age": 25,
            "firstname": "Player",
            "lastname": f"Player{player_id}",
        },
        "statistics": [{
            "games": {"appearences": 10, "minutes": 900},
            "goals": {"total": 1, "assists": 2},
            "passes": {"total": 300, "accuracy": 80},
            "tackles": {"total": 5},
            "cards": {"yellow": 1, "red": 0},
            "penalty": {"scored": 0, "missed": 0},
            "dribbles": {"attempts": 4, "success": 2},
            "fouls": {"drawn": 3, "committed": 2},
        }],
    }


def team_stats_item(team_id: int) -> dict:
    return {
        "team": {"id": team_id, "name": f"Team {team_id}"},
        "form": "WWDL",
        "fixtures": {
            "played": {"total": 38},
            "wins": {"total": 20},
            "draws": {"total": 10},
            "loses": {"total": 8},
        },
        "goals": {"for": {"total": {"total": 60}}, "against": {"total": {"total": 30}}},
        "biggest": {"wins": {"home": "4-0"}, "loses": {"away": "0-3"}},
        "clean_sheet": {"home": 8, "away": 5, "total": 13},
        "failed_to_score": {"home": 2, "away": 4, "total": 6},
        "penalty": {"scored": {"total": 5}, "missed": {"total": 1}},
        "lineups": [{"formation": "4-3-3", "played": 30}],
        "cards": {"yellow": {}, "red": {}},
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
    """The consumer sleeps 30s for rate limiting; tests must not wait."""
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
    return consumer


@pytest.fixture
def fake_api(mocker):
    """
    Patch requests.get to serve the given pages in order.
    The page is picked from params['page'] (default 1), mirroring API-Sports paging.
    Returns the mock so tests can inspect the calls made.
    """
    def _install(*pages):
        def _get(url, headers=None, params=None, **_):
            page = (params or {}).get("page", 1)
            response = MagicMock(status_code=200)
            response.json.return_value = copy.deepcopy(pages[page - 1])
            return response

        return mocker.patch("api_clients.soccer_consumer.requests.get", side_effect=_get)

    return _install


def list_keys(s3_client) -> list:
    return [obj["Key"] for obj in s3_client.list_objects_v2(Bucket=BUCKET).get("Contents", [])]
