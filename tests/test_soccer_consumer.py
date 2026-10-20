import io
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
import requests
from botocore.exceptions import ClientError

from conftest import (BUCKET, api_page, fixture_item, league_item, list_keys,
                      player_item, read_json, season_item, team_item)

NOW = datetime(2026, 10, 19, 6, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# authenticate / helpers
# ---------------------------------------------------------------------------

def test_authenticate_sets_api_sports_header(client):
    client.authenticate("secret-key")
    assert client.headers == {"x-apisports-key": "secret-key"}


def test_bucket_name_read_from_env(client):
    assert client.bucket_name == BUCKET


def test_run_context_defaults_to_new_id_and_today():
    from api_clients.soccer_consumer import soccer_consumer

    consumer = soccer_consumer(api_key="dummy")

    assert len(consumer.run_id) == 32
    assert consumer.run_date == datetime.now(timezone.utc).strftime("%Y-%m-%d")


@pytest.mark.parametrize("kwargs, expected", [
    ({"league": 39, "season": 2024, "team": 33}, "league=39/season=2024/team=33"),
    ({"season": 2024, "league": 39}, "season=2024/league=39"),
    ({"league": 39}, "league=39"),
    ({}, ""),
])
def test_generate_hadoop_partition_path(client, kwargs, expected):
    assert client._generate_hadoop_partition_path(**kwargs) == expected


# ---------------------------------------------------------------------------
# _make_request: errors, retries, rate limiting
# ---------------------------------------------------------------------------

def _response(status_code=200, body=None, headers=None):
    response = MagicMock(status_code=status_code, headers=headers or {})
    response.json.return_value = body if body is not None else api_page([])
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(str(status_code))
    return response


def test_make_request_builds_url_and_passes_params(client, fake_api):
    get = fake_api(api_page([team_item(1)]))
    data = client._make_request("teams", league=39, season=2024)

    assert data["response"] == [team_item(1)]
    args, kwargs = get.call_args
    assert args[0] == "https://v3.football.api-sports.io/teams"
    assert kwargs["headers"] == {"x-apisports-key": "dummy"}
    assert kwargs["params"] == {"league": 39, "season": 2024}
    assert kwargs["timeout"] > 0


def test_make_request_raises_on_4xx_without_retry(client, mocker):
    get = mocker.patch("api_clients.soccer_consumer.requests.get", return_value=_response(403))

    with pytest.raises(requests.HTTPError):
        client._make_request("teams", league=39)
    assert get.call_count == 1


def test_make_request_retries_5xx_with_backoff_then_succeeds(client, mocker, no_sleep):
    get = mocker.patch("api_clients.soccer_consumer.requests.get",
                       side_effect=[_response(502), _response(503), _response(200)])

    client._make_request("teams", league=39)

    assert get.call_count == 3
    assert [c.args[0] for c in no_sleep.call_args_list] == [2, 4]


def test_make_request_gives_up_after_max_retries(client, mocker):
    get = mocker.patch("api_clients.soccer_consumer.requests.get", return_value=_response(500))

    with pytest.raises(requests.HTTPError):
        client._make_request("teams", league=39)
    assert get.call_count == 4


def test_make_request_retries_network_errors(client, mocker):
    mocker.patch("api_clients.soccer_consumer.requests.get",
                 side_effect=[requests.ConnectionError("reset"), _response(200)])

    assert client._make_request("teams", league=39) == api_page([])


def test_make_request_retries_http_429_after_a_minute(client, mocker, no_sleep):
    mocker.patch("api_clients.soccer_consumer.requests.get",
                 side_effect=[_response(429), _response(200)])

    client._make_request("teams", league=39)

    no_sleep.assert_called_once_with(60)


def test_make_request_retries_rate_limit_error_body(client, mocker, no_sleep):
    """API-Sports reports per-minute rate limits as HTTP 200 with errors.rateLimit."""
    limited = _response(200, api_page([], errors={"rateLimit": "Too many requests"}))
    mocker.patch("api_clients.soccer_consumer.requests.get",
                 side_effect=[limited, _response(200, api_page([team_item(1)]))])

    assert client._make_request("teams", league=39)["response"] == [team_item(1)]
    no_sleep.assert_called_once_with(60)


def test_make_request_raises_on_other_api_errors_without_retry(client, fake_api):
    from api_clients.soccer_consumer import soccer_api_error

    get = fake_api(api_page([], errors={"requests": "You have reached the request limit for the day"}))
    with pytest.raises(soccer_api_error, match="request limit"):
        client._make_request("teams", league=39)
    assert get.call_count == 1


def test_make_request_tracks_daily_quota_header(client, fake_api, no_sleep):
    fake_api(api_page([]), headers={"x-ratelimit-requests-remaining": "7400",
                                    "X-RateLimit-Remaining": "250"})

    client._make_request("status")

    assert client.daily_remaining == 7400
    no_sleep.assert_not_called()


def test_make_request_waits_when_minute_quota_exhausted(client, fake_api, no_sleep):
    fake_api(api_page([]), headers={"X-RateLimit-Remaining": "1"})

    client._make_request("teams", league=39)

    no_sleep.assert_called_once_with(60)


def test_fetch_data_returns_raw_response(client, fake_api):
    get = fake_api(api_page([team_item(1)]))

    data = client.fetch_data("teams", league=39, season=2024)

    assert data == api_page([team_item(1)])
    assert get.call_args.kwargs["params"] == {"league": 39, "season": 2024}


# ---------------------------------------------------------------------------
# _iter_pages
# ---------------------------------------------------------------------------

def test_iter_pages_fetches_every_page(client, fake_api):
    pages = [api_page([team_item(i)], current=i, total=3) for i in (1, 2, 3)]
    get = fake_api(*pages)

    result = list(client._iter_pages("players", team=33))

    assert result == pages
    assert [c.kwargs["params"].get("page") for c in get.call_args_list] == [None, 2, 3]


def test_iter_pages_respects_max_pages(fake_api, monkeypatch, caplog):
    from api_clients.soccer_consumer import soccer_consumer

    monkeypatch.setenv("API_MAX_PAGES", "3")
    capped = soccer_consumer(api_key="dummy")
    capped.authenticate("dummy")
    get = fake_api(*[api_page([team_item(i)], current=i, total=5) for i in range(1, 6)])

    assert len(list(capped._iter_pages("players", team=33))) == 3
    assert get.call_count == 3
    assert "Truncating players results to 3 of 5 pages" in caplog.text


def test_iter_pages_no_cap_by_default(client, fake_api, caplog):
    fake_api(*[api_page([team_item(i)], current=i, total=5) for i in range(1, 6)])

    assert len(list(client._iter_pages("players", team=33))) == 5
    assert "Truncating" not in caplog.text


# ---------------------------------------------------------------------------
# S3 writes
# ---------------------------------------------------------------------------

def test_save_raw_json_adds_ingest_date_and_keeps_utf8(client, s3):
    key = client._save_raw_json("teams", {"name": "Atlético Nacional"}, "f.json",
                                league=239, season=2026)

    assert key == "raw/teams/league=239/season=2026/ingest_date=2026-10-19/f.json"
    assert list_keys(s3) == [key]
    assert read_json(s3, key) == {"name": "Atlético Nacional"}


def test_save_logs_at_info_without_extra_config(client, s3, caplog):
    """The module logger is INFO by default, so Lambda (root logger at WARNING) still emits it."""
    client._save_raw_response_to_s3(io.BytesIO(b"x"), "raw/teams", "f.json", league=39)

    assert "Saved raw response to s3://test-bucket/raw/teams/league=39/f.json" in caplog.text


def test_save_reraises_client_error_when_bucket_missing(client, s3):
    client.bucket_name = "does-not-exist"
    with pytest.raises(ClientError):
        client._save_raw_response_to_s3(io.BytesIO(b"x"), "raw/teams", "f.json", league=39)


def test_fetch_and_store_writes_one_envelope_per_page(client, fake_api, s3):
    pages = [api_page([player_item(1), player_item(2)], current=1, total=2),
             api_page([player_item(3)], current=2, total=2)]
    fake_api(*pages)

    items = client.fetch_and_store("players", "players", {"season": 2026, "team": 33},
                                   team=33, season=2026)

    assert [i["player"]["id"] for i in items] == [1, 2, 3]
    prefix = "raw/players/season=2026/team=33/ingest_date=2026-10-19"
    assert sorted(list_keys(s3)) == [f"{prefix}/run1_players_p001.json",
                                     f"{prefix}/run1_players_p002.json"]
    envelope = read_json(s3, f"{prefix}/run1_players_p002.json")
    assert envelope["run_id"] == "run1"
    assert envelope["endpoint"] == "players"
    assert envelope["params"] == {"team": 33, "season": 2026, "page": 2}
    assert envelope["paging"] == {"current": 2, "total": 2}
    # the response is stored unmodified: every competition's statistics survive
    assert envelope["response"] == [player_item(3)]
    assert len(envelope["response"][0]["statistics"]) == 2
    assert "ingest_ts" in envelope


def test_fetch_and_store_never_overwrites_other_runs(client, fake_api, s3):
    fake_api(api_page([team_item(1)]))

    client.fetch_and_store("teams", "teams", {"league": 39}, league=39)
    client.set_run_context(run_id="run2", run_date="2026-10-19")
    client.fetch_and_store("teams", "teams", {"league": 39}, league=39)

    assert len(list_keys(s3)) == 2


# ---------------------------------------------------------------------------
# entities
# ---------------------------------------------------------------------------

def test_get_status_reports_remaining_quota(client, fake_api, s3):
    fake_api(api_page({"subscription": {"plan": "Pro", "active": True},
                       "requests": {"current": 120, "limit_day": 7500}}))

    assert client.get_status() == {"plan": "Pro", "subscription_active": True,
                                   "requests_current": 120, "requests_limit_day": 7500,
                                   "requests_remaining": 7380}
    assert list_keys(s3) == []


def test_get_leagues_returns_current_season_and_coverage(client, fake_api, s3):
    fake_api(api_page([league_item(239, [
        season_item(2016, players=False, events=False),
        season_item(2026, current=True),
    ])]))

    result = client.get_leagues(league=239)

    assert result["current_season"] == 2026
    assert result["type"] == "League"
    assert result["coverage"]["2016"] == {"fixture_details": False, "players": False,
                                          "standings": True}
    assert result["coverage"]["2026"] == {"fixture_details": True, "players": True,
                                          "standings": True}
    assert list_keys(s3) == ["raw/leagues/league=239/ingest_date=2026-10-19/run1_leagues_p001.json"]


def test_get_teams_returns_orchestration_items(client, fake_api, s3):
    get = fake_api(api_page([team_item(1), team_item(2)]))

    assert client.get_teams(league=39, season=2026) == [
        {"team": 1, "league": 39, "season": 2026},
        {"team": 2, "league": 39, "season": 2026},
    ]
    assert get.call_args.kwargs["params"] == {"league": 39, "season": 2026}
    assert list_keys(s3) == ["raw/teams/league=39/season=2026/ingest_date=2026-10-19/run1_teams_p001.json"]


def test_get_squad(client, fake_api, s3):
    get = fake_api(api_page([{"team": {"id": 33}, "players": [{"id": 1}]}]))

    assert client.get_squad(team=33)["items"] == 1
    assert get.call_args.args[0].endswith("/players/squads")
    assert list_keys(s3) == ["raw/squads/team=33/ingest_date=2026-10-19/run1_squads_p001.json"]


def test_get_team_players_queries_without_league(client, fake_api, s3):
    get = fake_api(api_page([player_item(1)]))

    assert client.get_team_players(team=33, season=2026)["items"] == 1
    assert get.call_args.kwargs["params"] == {"team": 33, "season": 2026}


def test_get_standings(client, fake_api, s3):
    fake_api(api_page([{"league": {"id": 239, "standings": [[], []]}}]))

    assert client.get_standings(league=239, season=2026)["items"] == 1
    assert list_keys(s3) == ["raw/standings/league=239/season=2026/ingest_date=2026-10-19/run1_standings_p001.json"]


def test_get_fixtures_selects_recent_finished_fixtures(client, fake_api, s3):
    fake_api(api_page([
        fixture_item(1, "FT", "2026-10-18T15:00:00+00:00"),
        fixture_item(2, "AET", "2026-10-15T20:00:00+00:00"),
        fixture_item(3, "PEN", "2026-10-10T20:00:00+00:00"),
        fixture_item(4, "FT", "2026-10-01T15:00:00+00:00"),    # outside the window
        fixture_item(5, "NS", "2026-10-25T15:00:00+00:00"),    # not played yet
        fixture_item(6, "PST", "2026-10-17T15:00:00+00:00"),   # postponed
    ]))

    chunks = client.get_fixtures(league=239, season=2026, lookback_days=10, now=NOW)

    assert chunks == [{"ids": [1, 2, 3], "league": 239, "season": 2026}]
    assert len(list_keys(s3)) == 1


def test_get_fixtures_backfill_takes_every_finished_fixture_in_chunks_of_20(client, fake_api, s3):
    fake_api(api_page([fixture_item(i, "FT", "2024-01-01T15:00:00+00:00") for i in range(1, 46)]
                      + [fixture_item(99, "NS")]))

    chunks = client.get_fixtures(league=39, season=2024, lookback_days=None, now=NOW)

    assert [len(c["ids"]) for c in chunks] == [20, 20, 5]
    assert chunks[0]["ids"][0] == 1 and chunks[-1]["ids"][-1] == 45


def test_get_fixtures_none_finished_returns_empty_list(client, fake_api, s3):
    fake_api(api_page([fixture_item(1, "NS")]))

    assert client.get_fixtures(league=39, season=2026, lookback_days=10, now=NOW) == []


def test_get_fixture_details_batches_ids(client, fake_api, s3):
    get = fake_api(api_page([fixture_item(10), fixture_item(11), fixture_item(12)]))

    result = client.get_fixture_details(ids=[10, 11, 12], league=239, season=2026)

    assert result["items"] == 3 and result["requested"] == 3
    assert get.call_args.kwargs["params"] == {"ids": "10-11-12"}
    assert list_keys(s3) == [
        "raw/fixture_details/league=239/season=2026/ingest_date=2026-10-19/run1_fixtures_10-12_p001.json"]


@pytest.mark.parametrize("ids", [[], list(range(21))])
def test_get_fixture_details_rejects_bad_batch_size(client, ids):
    with pytest.raises(ValueError):
        client.get_fixture_details(ids=ids, league=39, season=2026)
