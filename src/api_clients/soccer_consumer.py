from api_clients.base_api_consumer import base_api_consumer
import requests
import boto3
import os
import io
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv('LOG_LEVEL', 'INFO'))

REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2
# API-Sports per-minute quota resets on a rolling minute
RATE_LIMIT_WAIT_SECONDS = 60
# fixtures?ids= accepts at most 20 ids per request
FIXTURE_IDS_PER_REQUEST = 20
FINISHED_STATUSES = ('FT', 'AET', 'PEN')
DEFAULT_FIXTURE_LOOKBACK_DAYS = 10
RAW_PREFIX = 'raw'

# boto3 clients are reused across warm Lambda invocations
_s3_client = None


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client('s3')
    return _s3_client


class soccer_api_error(RuntimeError):
    """Raised when API-Sports returns HTTP 200 with a populated 'errors' field."""


def _header_int(response, name: str) -> int | None:
    value = response.headers.get(name)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_rate_limit_error(errors) -> bool:
    return isinstance(errors, dict) and 'rateLimit' in errors


class soccer_consumer(base_api_consumer):
    def __init__(self, api_key: str = None):
        self.base_url = "https://v3.football.api-sports.io"
        self.api_key = api_key
        self.bucket_name = os.getenv('TARGET_BUCKET', 'soccer-api-raw-data')
        # free plan rejects page > 3; unset means no cap
        max_pages = os.getenv('API_MAX_PAGES')
        self.max_pages = int(max_pages) if max_pages else None
        self.daily_remaining = None
        self.set_run_context()

    def authenticate(self, api_key: str) -> None:
        """
        Authenticate the Soccer API client using the provided API key.
        """
        self.headers = {
            "x-apisports-key": api_key
        }

    def set_run_context(self, run_id: str = None, run_date: str = None) -> None:
        """
        Identify the pipeline run that writes the raw files. Step Functions passes the
        execution name and start date so every invocation of a run shares one ingest_date.
        """
        self.run_id = run_id or uuid.uuid4().hex
        self.run_date = run_date or datetime.now(timezone.utc).strftime('%Y-%m-%d')

    # ------------------------------------------------------------------
    # S3
    # ------------------------------------------------------------------

    def _generate_hadoop_partition_path(self, **kwargs) -> str:
        """
        Generate Hadoop-style partition path based on the kwargs provided.
        """
        key_suffix = ""
        for key, value in kwargs.items():
            key_suffix += f"{key}={value}/"

        return key_suffix.rstrip('/')

    def _save_raw_response_to_s3(self, response_content: io.BytesIO | io.StringIO,
                                 prefix: str, filename: str, **kwargs) -> str:
        """
        Save content to the S3 bucket with Hadoop-style partitioning.
        Path format: s3://bucket/prefix/key=value/.../filename
        """
        body = response_content.getvalue()
        if isinstance(body, str):
            # need to encode string buffer to bytes
            body = body.encode('utf-8')

        partition_path = self._generate_hadoop_partition_path(**kwargs)
        s3_key = "/".join(part for part in (prefix, partition_path, filename) if part)
        try:
            response = _get_s3_client().put_object(
                Bucket = self.bucket_name,
                Key = s3_key,
                Body = body,
                ContentType = 'application/json'
            )

            if response['ResponseMetadata']['HTTPStatusCode'] != 200:
                raise Exception(f"Failed to save data to S3: {response}")

            logger.info("Saved raw response to s3://%s/%s", self.bucket_name, s3_key)
        except ClientError as e:
            logger.error("Error saving to S3: %s", e)
            raise e
        return s3_key

    def _save_raw_json(self, entity: str, envelope: dict, filename: str, **partitions) -> str:
        """
        Write one API page as an append-only bronze file:
        raw/{entity}/{partitions}/ingest_date={run_date}/{filename}
        """
        body = json.dumps(envelope, ensure_ascii=False).encode('utf-8')
        return self._save_raw_response_to_s3(io.BytesIO(body), f"{RAW_PREFIX}/{entity}", filename,
                                             **partitions, ingest_date=self.run_date)

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _throttle(self, response) -> None:
        """
        Track the quota headers and pause when the per-minute allowance is used up.
        """
        daily_remaining = _header_int(response, 'x-ratelimit-requests-remaining')
        if daily_remaining is not None:
            self.daily_remaining = daily_remaining

        minute_remaining = _header_int(response, 'X-RateLimit-Remaining')
        if minute_remaining is not None and minute_remaining <= 1:
            logger.info("Per-minute API quota exhausted, waiting %ss (daily remaining: %s)",
                        RATE_LIMIT_WAIT_SECONDS, self.daily_remaining)
            time.sleep(RATE_LIMIT_WAIT_SECONDS)

    def _wait_before_retry(self, attempt: int, endpoint: str, reason, rate_limited: bool) -> None:
        wait = RATE_LIMIT_WAIT_SECONDS if rate_limited else BACKOFF_BASE_SECONDS * 2 ** attempt
        logger.warning("Retrying %s in %ss (attempt %s/%s): %s",
                       endpoint, wait, attempt + 1, MAX_RETRIES, reason)
        time.sleep(wait)

    def _make_request(self, endpoint: str, **kwargs) -> dict:
        """
        Make a GET request to the specified Soccer API endpoint.
        Retries rate limits (HTTP 429 or a 'rateLimit' error body), 5xx and network errors.
        """
        url = f"{self.base_url}/{endpoint}"
        for attempt in range(MAX_RETRIES + 1):
            can_retry = attempt < MAX_RETRIES
            try:
                response = requests.get(url, headers=self.headers, params=kwargs,
                                        timeout=REQUEST_TIMEOUT_SECONDS)
            except (requests.ConnectionError, requests.Timeout) as e:
                if not can_retry:
                    raise
                self._wait_before_retry(attempt, endpoint, e, rate_limited=False)
                continue

            self._throttle(response)

            if response.status_code == 429 or response.status_code >= 500:
                if can_retry:
                    self._wait_before_retry(attempt, endpoint, f"HTTP {response.status_code}",
                                            rate_limited=response.status_code == 429)
                    continue
                response.raise_for_status()
            if response.status_code != 200:
                response.raise_for_status()

            data = response.json()
            # API-Sports reports bad keys, rate limits, etc. as HTTP 200 with 'errors' set
            errors = data.get('errors')
            if errors:
                if _is_rate_limit_error(errors) and can_retry:
                    self._wait_before_retry(attempt, endpoint, errors, rate_limited=True)
                    continue
                raise soccer_api_error(f"API error for {endpoint} with parameters {kwargs}: {errors}")

            return data

    def _iter_pages(self, endpoint: str, **kwargs):
        """
        Yield the raw response of every page of a request, honouring API_MAX_PAGES.
        """
        data = self._make_request(endpoint=endpoint, **kwargs)
        yield data

        total_pages = (data.get('paging') or {}).get('total', 1) or 1
        last_page = total_pages
        if self.max_pages and total_pages > self.max_pages:
            last_page = self.max_pages
            logger.warning("Truncating %s results to %s of %s pages (API_MAX_PAGES) for parameters: %s",
                           endpoint, last_page, total_pages, kwargs)

        for page in range(2, last_page + 1):
            yield self._make_request(endpoint=endpoint, **kwargs, page=page)

    def fetch_and_store(self, endpoint: str, entity: str, partitions: dict,
                        file_tag: str = None, **params) -> list:
        """
        Fetch every page of an endpoint, write each page unmodified to the raw zone
        and return the aggregated 'response' items.
        """
        file_tag = file_tag or entity
        items = []
        for page_number, data in enumerate(self._iter_pages(endpoint, **params), start=1):
            response = data.get('response')
            envelope = {
                'ingest_ts': datetime.now(timezone.utc).isoformat(),
                'run_id': self.run_id,
                'endpoint': endpoint,
                'params': {**params, 'page': page_number},
                'paging': data.get('paging'),
                'results': data.get('results'),
                'response': response,
            }
            self._save_raw_json(entity, envelope,
                                f"{self.run_id}_{file_tag}_p{page_number:03d}.json", **partitions)
            if isinstance(response, list):
                items.extend(response)
            elif response:
                items.append(response)

        logger.info("Fetched %s %s items for parameters: %s", len(items), entity, params)
        return items

    def fetch_data(self, endpoint: str, **kwargs) -> dict:
        """
        Fetch the raw JSON response of a single request to an API-Sports endpoint.
        Args:
            endpoint: API endpoint to fetch from (e.g. 'teams')
            **kwargs: Query parameters for the request
        """
        return self._make_request(endpoint, **kwargs)

    # ------------------------------------------------------------------
    # Entities
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """
        Account quota for today. /status does not count against the daily quota.
        """
        response = self._make_request('status').get('response') or {}
        requests_info = response.get('requests') or {}
        current = requests_info.get('current') or 0
        limit_day = requests_info.get('limit_day') or 0
        subscription = response.get('subscription') or {}
        return {
            'plan': subscription.get('plan'),
            'subscription_active': subscription.get('active'),
            'requests_current': current,
            'requests_limit_day': limit_day,
            'requests_remaining': max(limit_day - current, 0),
        }

    def get_leagues(self, league: int) -> dict:
        """
        Store league metadata and return its current season and per-season coverage,
        used by the orchestration to skip endpoints a season does not cover.
        """
        items = self.fetch_and_store('leagues', 'leagues', {'league': league}, id=league)
        if not items:
            return {'league': league, 'current_season': None, 'coverage': {}}

        item = items[0]
        seasons = item.get('seasons') or []
        current = next((s.get('year') for s in seasons if s.get('current')), None)
        coverage = {}
        for season in seasons:
            season_coverage = season.get('coverage') or {}
            fixtures_coverage = season_coverage.get('fixtures') or {}
            coverage[str(season.get('year'))] = {
                'fixture_details': bool(fixtures_coverage.get('statistics_fixtures')
                                        or fixtures_coverage.get('events')),
                'players': bool(season_coverage.get('players')),
                'standings': bool(season_coverage.get('standings')),
            }

        return {
            'league': league,
            'type': (item.get('league') or {}).get('type'),
            'current_season': current,
            'coverage': coverage,
        }

    def get_teams(self, league: int, season: int) -> list:
        """
        Store the teams of a league-season and return [{team, league, season}] for orchestration.
        """
        items = self.fetch_and_store('teams', 'teams', {'league': league, 'season': season},
                                     league=league, season=season)
        return [{'team': item['team']['id'], 'league': league, 'season': season}
                for item in items if (item.get('team') or {}).get('id')]

    def get_squad(self, team: int) -> dict:
        """
        Store the current squad (roster and bio) of a team.
        """
        items = self.fetch_and_store('players/squads', 'squads', {'team': team}, team=team)
        return {'entity': 'squads', 'team': team, 'items': len(items)}

    def get_team_players(self, team: int, season: int) -> dict:
        """
        Store player season statistics of a team. Without a 'league' filter the API
        returns the statistics of every competition the team played that season.
        """
        items = self.fetch_and_store('players', 'players', {'season': season, 'team': team},
                                     team=team, season=season)
        return {'entity': 'players', 'team': team, 'season': season, 'items': len(items)}

    def get_standings(self, league: int, season: int) -> dict:
        """
        Store the standings of a league-season (all groups, e.g. Apertura/Clausura).
        """
        items = self.fetch_and_store('standings', 'standings', {'league': league, 'season': season},
                                     league=league, season=season)
        return {'entity': 'standings', 'league': league, 'season': season, 'items': len(items)}

    def get_fixtures(self, league: int, season: int, lookback_days: int | None = None,
                     now: datetime = None) -> list:
        """
        Store the fixture list of a league-season and return the finished fixtures to detail,
        chunked for fixtures?ids=. lookback_days=None selects every finished fixture (backfill).
        Returns: [{'ids': [...], 'league', 'season'}]
        """
        items = self.fetch_and_store('fixtures', 'fixtures', {'league': league, 'season': season},
                                     league=league, season=season)

        since = None
        if lookback_days is not None:
            since = (now or datetime.now(timezone.utc)) - timedelta(days=lookback_days)

        fixture_ids = []
        for item in items:
            fixture = item.get('fixture') or {}
            if (fixture.get('status') or {}).get('short') not in FINISHED_STATUSES:
                continue
            if since is not None:
                played_at = fixture.get('date')
                if not played_at or datetime.fromisoformat(played_at) < since:
                    continue
            fixture_ids.append(fixture['id'])

        logger.info("%s finished fixtures to detail for league %s season %s (lookback_days=%s)",
                    len(fixture_ids), league, season, lookback_days)
        return [{'ids': fixture_ids[i:i + FIXTURE_IDS_PER_REQUEST], 'league': league, 'season': season}
                for i in range(0, len(fixture_ids), FIXTURE_IDS_PER_REQUEST)]

    def get_fixture_details(self, ids: list, league: int, season: int) -> dict:
        """
        Store full match details (events, lineups, statistics, players) for up to 20 fixtures
        with a single fixtures?ids= request.
        """
        if not ids or len(ids) > FIXTURE_IDS_PER_REQUEST:
            raise ValueError(f"get_fixture_details needs 1-{FIXTURE_IDS_PER_REQUEST} ids, got {len(ids or [])}")

        items = self.fetch_and_store('fixtures', 'fixture_details',
                                     {'league': league, 'season': season},
                                     file_tag=f"fixtures_{ids[0]}-{ids[-1]}",
                                     ids='-'.join(str(fixture_id) for fixture_id in ids))
        return {'entity': 'fixture_details', 'league': league, 'season': season,
                'requested': len(ids), 'items': len(items)}


if __name__ == "__main__":
    #test the soccer consumer
    logging.basicConfig()
    api_key = os.getenv('SOCCER_API_KEY', None)
    soccer_client = soccer_consumer(api_key=api_key)
    soccer_client.authenticate(api_key)
    print(soccer_client.get_status())
