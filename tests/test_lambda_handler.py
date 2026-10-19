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
    """Patch the secret lookup and the soccer client methods."""
    secret = mocker.patch.object(handler, "get_client_secret", return_value="tok")
    from api_clients.soccer_consumer import soccer_consumer

    mocks = {
        "secret": secret,
        "teams": mocker.patch.object(soccer_consumer, "get_teams_from_league",
                                     return_value=[{"team": 1, "league": 39, "season": 2024}]),
        "stats": mocker.patch.object(soccer_consumer, "get_team_stats", return_value="stats ok"),
        "players": mocker.patch.object(soccer_consumer, "get_team_players", return_value="players ok"),
    }
    return mocks


def test_handler_get_teams_from_league(consumer):
    result = handler.lambda_handler(
        {"action": "get_teams_from_league", "league": 39, "season": 2024}, None)

    assert result["statusCode"] == 200
    assert json.loads(result["teams"]) == [{"team": 1, "league": 39, "season": 2024}]
    consumer["teams"].assert_called_once_with(league=39, season=2024)


@pytest.mark.parametrize("action, mock_name, body", [
    ("get_team_stats", "stats", "stats ok"),
    ("get_team_players", "players", "players ok"),
])
def test_handler_team_actions(consumer, action, mock_name, body):
    event = {"action": action, "league": 39, "season": 2024, "teamid": 33}

    result = handler.lambda_handler(event, None)

    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == body
    assert {k: result[k] for k in event} == event
    consumer[mock_name].assert_called_once_with(league=39, season=2024, team=33)


def test_handler_caches_api_key_across_invocations(consumer):
    event = {"action": "get_team_stats", "league": 39, "season": 2024, "teamid": 33}

    handler.lambda_handler(event, None)
    handler.lambda_handler(event, None)

    consumer["secret"].assert_called_once()


@pytest.mark.parametrize("event", [
    {},
    {"action": "unknown", "league": 39, "season": 2024, "teamid": 33},
    {"action": "get_teams_from_league", "league": 39},
    {"action": "get_team_stats", "league": 39, "season": 2024},
    {"action": "get_team_players", "season": 2024, "teamid": 33},
])
def test_handler_unrecognised_or_incomplete_returns_204(consumer, event):
    result = handler.lambda_handler(event, None)

    assert result["statusCode"] == 204
    consumer["secret"].assert_not_called()
