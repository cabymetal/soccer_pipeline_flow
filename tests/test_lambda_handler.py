import json

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

import api_factory
import lambda_handler as handler

from conftest import REGION


# ---------------------------------------------------------------------------
# get_client_secret
# ---------------------------------------------------------------------------

@pytest.fixture
def secrets():
    with mock_aws():
        yield boto3.client("secretsmanager", region_name=REGION)


def test_get_client_secret_returns_token(secrets):
    secrets.create_secret(Name="prod/soccer/api",
                          SecretString=json.dumps({"SOCCER_API_TOKEN": "tok-123"}))

    assert handler.get_client_secret() == "tok-123"


def test_get_client_secret_uses_env_var_secret_id(secrets, monkeypatch):
    monkeypatch.setenv("API_SOCCER_API_KEY", "test/soccer/api")
    secrets.create_secret(Name="test/soccer/api",
                          SecretString=json.dumps({"SOCCER_API_TOKEN": "tok-test"}))

    assert handler.get_client_secret() == "tok-test"


def test_get_client_secret_missing_raises(secrets):
    with pytest.raises(ClientError):
        handler.get_client_secret()


# ---------------------------------------------------------------------------
# read_api_client
# ---------------------------------------------------------------------------

def test_read_api_client_soccer():
    assert handler.read_api_client("soccer") is api_factory.SoccerAPIFactory


def test_read_api_client_unknown_raises():
    with pytest.raises(ValueError, match="spotify"):
        handler.read_api_client("spotify")


# ---------------------------------------------------------------------------
# lambda_handler routing
# ---------------------------------------------------------------------------

@pytest.fixture
def consumer(mocker):
    """Patch the secret lookup and every soccer client action."""
    from api_clients.soccer_consumer import soccer_consumer

    mocks = {"secret": mocker.patch.object(handler, "get_client_secret", return_value="tok")}
    for action in handler.ACTIONS:
        mocks[action] = mocker.patch.object(soccer_consumer, action, return_value={"ok": action})
    mocks["context"] = mocker.patch.object(soccer_consumer, "set_run_context",
                                           wraps=soccer_consumer.set_run_context, autospec=True)
    return mocks


@pytest.mark.parametrize("event, expected_kwargs", [
    ({"action": "get_status"}, {}),
    ({"action": "get_leagues", "league": 239}, {"league": 239}),
    ({"action": "get_teams", "league": 39, "season": 2026}, {"league": 39, "season": 2026}),
    ({"action": "get_squad", "team": 33}, {"team": 33}),
    ({"action": "get_team_players", "team": 33, "season": 2026}, {"team": 33, "season": 2026}),
    ({"action": "get_standings", "league": 239, "season": 2026}, {"league": 239, "season": 2026}),
    ({"action": "get_fixtures", "league": 39, "season": 2026, "lookback_days": 3},
     {"league": 39, "season": 2026, "lookback_days": 3}),
    ({"action": "get_fixture_details", "ids": [1, 2], "league": 39, "season": 2026},
     {"ids": [1, 2], "league": 39, "season": 2026}),
])
def test_handler_dispatches_actions(consumer, event, expected_kwargs):
    result = handler.lambda_handler({**event, "extra": "ignored"}, None)

    assert result == {"statusCode": 200, "action": event["action"],
                      "result": {"ok": event["action"]}}
    consumer[event["action"]].assert_called_once_with(**expected_kwargs)


def test_handler_returns_native_json_lists(consumer):
    consumer["get_teams"].return_value = [{"team": 1, "league": 39, "season": 2026}]

    result = handler.lambda_handler({"action": "get_teams", "league": 39, "season": 2026}, None)

    assert result["result"] == [{"team": 1, "league": 39, "season": 2026}]


def test_handler_fixtures_default_lookback_from_env(consumer, monkeypatch):
    monkeypatch.setenv("FIXTURE_LOOKBACK_DAYS", "7")

    handler.lambda_handler({"action": "get_fixtures", "league": 39, "season": 2026}, None)

    consumer["get_fixtures"].assert_called_once_with(league=39, season=2026, lookback_days=7)


def test_handler_fixtures_backfill_takes_whole_season(consumer):
    handler.lambda_handler({"action": "get_fixtures", "league": 39, "season": 2024,
                            "mode": "backfill"}, None)

    consumer["get_fixtures"].assert_called_once_with(league=39, season=2024, lookback_days=None)


def test_handler_passes_run_context(consumer):
    handler.lambda_handler({"action": "get_status", "run_id": "exec-1",
                            "run_date": "2026-10-19"}, None)

    _, kwargs = consumer["context"].call_args
    assert kwargs == {"run_id": "exec-1", "run_date": "2026-10-19"}


def test_handler_caches_api_key_across_invocations(consumer):
    event = {"action": "get_status"}

    handler.lambda_handler(event, None)
    handler.lambda_handler(event, None)

    consumer["secret"].assert_called_once()


@pytest.mark.parametrize("event", [
    {},
    {"action": "unknown", "league": 39, "season": 2026},
    {"action": "get_team_stats", "league": 39, "season": 2026, "teamid": 33},
    {"action": "get_teams", "league": 39},
    {"action": "get_team_players", "season": 2026},
    {"action": "get_fixture_details", "ids": [], "league": 39, "season": 2026},
    {"action": "get_leagues"},
])
def test_handler_unrecognised_or_incomplete_returns_204(consumer, event):
    result = handler.lambda_handler(event, None)

    assert result["statusCode"] == 204
    consumer["secret"].assert_not_called()
