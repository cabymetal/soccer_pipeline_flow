import io
import json
from unittest.mock import MagicMock

import pandas as pd
import pytest
import requests
from botocore.exceptions import ClientError

from conftest import (BUCKET, api_page, list_keys, player_item,
                      team_item, team_stats_item)


# ---------------------------------------------------------------------------
# authenticate / helpers
# ---------------------------------------------------------------------------

def test_authenticate_sets_api_sports_header(client):
    client.authenticate("secret-key")
    assert client.headers == {"x-apisports-key": "secret-key"}


def test_bucket_name_read_from_env(client):
    assert client.bucket_name == BUCKET


@pytest.mark.parametrize("kwargs, expected", [
    ({"league": 39, "season": 2024, "team": 33}, "league=39/season=2024/team=33"),
    ({"season": 2024, "league": 39}, "season=2024/league=39"),
    ({"league": 39}, "league=39"),
    ({}, ""),
])
def test_generate_hadoop_partition_path(client, kwargs, expected):
    assert client._generate_hadoop_partition_path(**kwargs) == expected


def test_generate_buffer_parquet_roundtrip(client):
    df = pd.DataFrame([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    buffer = client.generate_buffer_from_dataframe(df, "parquet")
    assert isinstance(buffer, io.BytesIO)
    pd.testing.assert_frame_equal(pd.read_parquet(buffer), df)


def test_generate_buffer_json_lines(client):
    df = pd.DataFrame([{"a": 1}, {"a": 2}])
    buffer = client.generate_buffer_from_dataframe(df, "json")
    lines = buffer.getvalue().strip().splitlines()
    assert [json.loads(line) for line in lines] == [{"a": 1}, {"a": 2}]


def test_generate_buffer_unknown_extension_raises(client):
    with pytest.raises(ValueError, match="csv"):
        client.generate_buffer_from_dataframe(pd.DataFrame(), "csv")


# ---------------------------------------------------------------------------
# _make_request
# ---------------------------------------------------------------------------

def test_make_request_builds_url_and_passes_params(client, fake_api):
    get = fake_api(api_page([team_item(1)]))
    data = client._make_request("teams", league=39, season=2024)

    assert data["response"] == [team_item(1)]
    args, kwargs = get.call_args
    assert args[0] == "https://v3.football.api-sports.io/teams"
    assert kwargs["headers"] == {"x-apisports-key": "dummy"}
    assert kwargs["params"] == {"league": 39, "season": 2024}
    assert kwargs["timeout"] > 0


def test_make_request_raises_on_http_error(client, mocker):
    response = MagicMock(status_code=500)
    response.raise_for_status.side_effect = requests.HTTPError("500")
    mocker.patch("api_clients.soccer_consumer.requests.get", return_value=response)

    with pytest.raises(requests.HTTPError):
        client._make_request("teams", league=39)


def test_make_request_raises_on_api_errors_in_200_body(client, fake_api):
    """API-Sports reports bad keys / rate limits as HTTP 200 with an 'errors' object."""
    from api_clients.soccer_consumer import soccer_api_error

    fake_api(api_page([], errors={"rateLimit": "Too many requests"}))
    with pytest.raises(soccer_api_error, match="rateLimit"):
        client._make_request("teams", league=39)


def test_fetch_data_returns_raw_response(client, fake_api):
    get = fake_api(api_page([team_item(1)]))

    data = client.fetch_data("teams", league=39, season=2024)

    assert data == api_page([team_item(1)])
    assert get.call_args.kwargs["params"] == {"league": 39, "season": 2024}


# ---------------------------------------------------------------------------
# _paginate_results
# ---------------------------------------------------------------------------

def test_paginate_results_fetches_remaining_pages(client, fake_api):
    pages = [api_page([team_item(i)], current=i, total=3) for i in (1, 2, 3)]
    get = fake_api(*pages)

    result = client._paginate_results("teams", pages[0], league=39)

    assert [t["team"]["id"] for t in result] == [1, 2, 3]
    assert get.call_count == 2
    for call in get.call_args_list:
        assert call.args[0] == "https://v3.football.api-sports.io/teams"
    assert [c.kwargs["params"]["page"] for c in get.call_args_list] == [2, 3]


def test_paginate_results_respects_max_pages(fake_api, monkeypatch, caplog):
    from api_clients.soccer_consumer import soccer_consumer

    monkeypatch.setenv("API_MAX_PAGES", "3")
    capped = soccer_consumer(api_key="dummy")
    capped.authenticate("dummy")
    pages = [api_page([team_item(i)], current=i, total=5) for i in range(1, 6)]
    get = fake_api(*pages)

    result = capped._paginate_results("players", pages[0], league=39)

    assert [t["team"]["id"] for t in result] == [1, 2, 3]
    assert [c.kwargs["params"]["page"] for c in get.call_args_list] == [2, 3]
    assert "Truncating players results to 3 of 5 pages" in caplog.text


def test_paginate_results_no_cap_by_default(client, fake_api, caplog):
    pages = [api_page([team_item(i)], current=i, total=5) for i in range(1, 6)]
    fake_api(*pages)

    assert len(client._paginate_results("players", pages[0])) == 5
    assert "Truncating" not in caplog.text


def test_paginate_results_single_page_makes_no_requests(client, fake_api):
    get = fake_api()
    result = client._paginate_results("teams", api_page([team_item(1)]))
    assert result == [team_item(1)]
    get.assert_not_called()


# ---------------------------------------------------------------------------
# _save_raw_response_to_s3
# ---------------------------------------------------------------------------

def test_save_string_buffer_encoded_as_utf8(client, s3):
    buffer = io.StringIO('{"name":"Atlético"}\n')
    client._save_raw_response_to_s3(buffer, "teams/statistics", "33.json",
                                    league=39, season=2024, team=33)

    key = "teams_statistics/league=39/season=2024/team=33/33.json"
    assert list_keys(s3) == [key]
    body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    assert body.decode("utf-8") == '{"name":"Atlético"}\n'


def test_save_bytes_buffer_written_as_is(client, s3):
    buffer = client.generate_buffer_from_dataframe(pd.DataFrame([{"a": 1}]), "parquet")
    client._save_raw_response_to_s3(buffer, "players", "data.parquet",
                                    league=39, season=2024, team=33)

    key = "players/league=39/season=2024/team=33/data.parquet"
    assert list_keys(s3) == [key]
    body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    assert pd.read_parquet(io.BytesIO(body))["a"].tolist() == [1]


def test_save_logs_at_info_without_extra_config(client, s3, caplog):
    """The module logger is INFO by default, so Lambda (root logger at WARNING) still emits it."""
    client._save_raw_response_to_s3(io.BytesIO(b"x"), "players", "data.parquet", league=39)

    assert "Saved raw response to s3://test-bucket/players/league=39/data.parquet" in caplog.text


def test_save_reraises_client_error_when_bucket_missing(client, s3):
    client.bucket_name = "does-not-exist"
    with pytest.raises(ClientError):
        client._save_raw_response_to_s3(io.BytesIO(b"x"), "players", "data.parquet", league=39)


# ---------------------------------------------------------------------------
# get_teams_from_league
# ---------------------------------------------------------------------------

def test_get_teams_from_league_aggregates_pages(client, fake_api):
    fake_api(
        api_page([team_item(i) for i in range(1, 4)], current=1, total=2),
        api_page([team_item(i) for i in range(4, 7)], current=2, total=2),
    )

    teams = client.get_teams_from_league(league=39, season=2024)

    assert teams == [{"team": i, "league": 39, "season": 2024} for i in range(1, 7)]


def test_get_teams_from_league_keeps_rate_limit_sleep(client, fake_api, no_sleep):
    """Documents existing behaviour: one 30s sleep per 5 teams."""
    fake_api(api_page([team_item(i) for i in range(1, 12)]))

    client.get_teams_from_league(league=39, season=2024)

    assert no_sleep.call_count == 2
    no_sleep.assert_called_with(30)


# ---------------------------------------------------------------------------
# get_team_stats
# ---------------------------------------------------------------------------

def _read_json_lines(s3, key):
    body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode()
    return [json.loads(line) for line in body.strip().splitlines()]


def test_get_team_stats_dict_response(client, fake_api, s3):
    fake_api(api_page(team_stats_item(33)))

    message = client.get_team_stats(league=39, season=2024, team=33)

    key = "teams_statistics/league=39/season=2024/team=33/33.json"
    assert list_keys(s3) == [key]
    [record] = _read_json_lines(s3, key)
    assert record["team_id"] == 33
    assert record["form"] == "WWDL"
    assert record["fixtures_losses"] == {"total": 8}
    assert record["clean_sheets_total"] == 13
    assert "33" in message


def test_get_team_stats_list_response_picks_matching_team(client, fake_api, s3):
    fake_api(api_page([team_stats_item(1), team_stats_item(33)]))

    client.get_team_stats(league=39, season=2024, team=33)

    [record] = _read_json_lines(s3, "teams_statistics/league=39/season=2024/team=33/33.json")
    assert record["team_id"] == 33


@pytest.mark.parametrize("response", [[], [team_stats_item(1)], {}])
def test_get_team_stats_missing_data_writes_nothing(client, fake_api, s3, response):
    fake_api(api_page(response))

    message = client.get_team_stats(league=39, season=2024, team=33)

    assert list_keys(s3) == []
    assert "no statistics" in message


# ---------------------------------------------------------------------------
# get_team_players
# ---------------------------------------------------------------------------

def _read_players(s3):
    body = s3.get_object(Bucket=BUCKET,
                         Key="players/league=39/season=2024/team=33/data.parquet")["Body"].read()
    return pd.read_parquet(io.BytesIO(body))


def test_get_team_players_single_page(client, fake_api, s3):
    fake_api(api_page([player_item(i) for i in range(1, 4)]))

    client.get_team_players(league=39, season=2024, team=33)

    df = _read_players(s3)
    assert df["player_id"].tolist() == [1, 2, 3]
    assert set(df.columns) >= {"player_id", "name", "age", "first_name", "last_name",
                               "games", "goals", "passes", "cards"}


def test_get_team_players_all_pages_written(client, fake_api, s3):
    fake_api(
        api_page([player_item(1), player_item(2)], current=1, total=3),
        api_page([player_item(3), player_item(4)], current=2, total=3),
        api_page([player_item(5)], current=3, total=3),
    )

    message = client.get_team_players(league=39, season=2024, team=33)

    assert _read_players(s3)["player_id"].tolist() == [1, 2, 3, 4, 5]
    assert "page" not in message


def test_get_team_players_handles_missing_statistics(client, fake_api, s3):
    item = player_item(1)
    item["statistics"] = []
    fake_api(api_page([item]))

    client.get_team_players(league=39, season=2024, team=33)

    df = _read_players(s3)
    assert df["player_id"].tolist() == [1]


def test_get_team_players_sleeps_every_5_pages(client, fake_api, s3, no_sleep):
    pages = [api_page([player_item(i)], current=i, total=10) for i in range(1, 11)]
    fake_api(*pages)

    client.get_team_players(league=39, season=2024, team=33)

    assert no_sleep.call_count == 2
