from api_clients.base_api_consumer import base_api_consumer
import requests
import boto3
import pandas as pd
import os
import io
import logging
import time
import copy
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv('LOG_LEVEL', 'INFO'))

REQUEST_TIMEOUT_SECONDS = 30

# boto3 clients are reused across warm Lambda invocations
_s3_client = None


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client('s3')
    return _s3_client


class soccer_api_error(RuntimeError):
    """Raised when API-Sports returns HTTP 200 with a populated 'errors' field."""


class soccer_consumer(base_api_consumer):
    def __init__(self, api_key: str = None):
        self.base_url = "https://v3.football.api-sports.io"
        self.api_key = api_key
        self.bucket_name = os.getenv('TARGET_BUCKET', 'soccer-api-raw-data')
        # free plan rejects page > 3; unset means no cap
        max_pages = os.getenv('API_MAX_PAGES')
        self.max_pages = int(max_pages) if max_pages else None

    def authenticate(self, api_key: str) -> None:
        """
        Authenticate the Soccer API client using the provided API key.
        """
        self.headers = {
            "x-apisports-key": api_key
        }

    def _generate_hadoop_partition_path(self, **kwargs) -> str:
        """
        Generate Hadoop-style partition path based on the kwargs provided.
        """
        key_suffix = ""
        for key, value in kwargs.items():
            key_suffix += f"{key}={value}/"

        return key_suffix.rstrip('/')

    def _save_raw_response_to_s3(self, response_content: io.BytesIO | io.StringIO,
                                 endpoint: str, filename: str, **kwargs) -> None:
        """
        Save the raw API response content to an S3 bucket with Hadoop-style partitioning.
        Path format: s3://bucket/endpoint/key=value/.../filename
        """
        body = response_content.getvalue()
        if isinstance(body, str):
            # need to encode string buffer to bytes
            body = body.encode('utf-8')

        # Generate Hadoop partition path
        partition_path = self._generate_hadoop_partition_path(**kwargs)

        # Construct S3 key with Hadoop partitioning
        s3_key = f"{endpoint.replace('/', '_')}/{partition_path}/{filename}"
        try:
            response = _get_s3_client().put_object(
                Bucket = self.bucket_name,
                Key = s3_key,
                Body = body
            )

            if response['ResponseMetadata']['HTTPStatusCode'] != 200:
                raise Exception(f"Failed to save data to S3: {response}")

            logger.info("Saved raw response to s3://%s/%s", self.bucket_name, s3_key)
        except ClientError as e:
            logger.error("Error saving to S3: %s", e)
            raise e


    def _make_request(self, endpoint: str, **kwargs) -> dict:
        """
        Make a GET request to the specified Soccer API endpoint.
        """
        url = f"{self.base_url}/{endpoint}"
        response = requests.get(url, headers=self.headers, params=kwargs,
                                timeout=REQUEST_TIMEOUT_SECONDS)
        if response.status_code != 200:
            response.raise_for_status()

        data = response.json()
        # API-Sports reports bad keys, rate limits, etc. as HTTP 200 with 'errors' set
        if data.get('errors'):
            raise soccer_api_error(f"API error for {endpoint} with parameters {kwargs}: {data['errors']}")

        return data

    def _paginate_results(self, endpoint: str, initial_data: dict, **kwargs) -> list:
        """
        Handle pagination logic and return all aggregated data from all pages.

        Args:
            endpoint: API endpoint to fetch from
            initial_data: Initial response data from first request
            **kwargs: Query parameters for the request

        Returns:
            list: Aggregated response data from all pages
        """
        total_pages = initial_data.get('paging', {}).get('total', 1)
        all_data = list(initial_data.get('response', []))

        last_page = total_pages
        if self.max_pages and total_pages > self.max_pages:
            last_page = self.max_pages
            logger.warning("Truncating %s results to %s of %s pages (API_MAX_PAGES) for parameters: %s",
                           endpoint, last_page, total_pages, kwargs)

        # Fetch remaining pages if total_pages > 1
        for page in range(2, last_page + 1):
            page_data = self._make_request(endpoint=endpoint, **kwargs, page=page)
            all_data.extend(page_data.get('response', []))
            if page % 5 == 0:
                time.sleep(30)  # To respect API rate limits

        return all_data

    def get_teams_from_league(self, **kwargs) -> list:
        """
        Retrieve all teams from a specified league and season.
        Args:
            **kwargs: Query parameters including 'league' and 'season'
        Returns:
            list: list of dictionary objects containing team id, league, and season
        """
        endpoint = f"teams"
        data = self._make_request(endpoint=endpoint, **kwargs)
        all_data = self._paginate_results(endpoint, data, **kwargs)
        logger.info("fetch_data completed for endpoint: %s with parameters: %s", endpoint, kwargs)
        logger.info("Total teams fetched: %s", len(all_data))

        teams_from_league = []
        counter = 1
        for item in all_data:
            team_dict = {'team': item['team']['id'], **kwargs}
            teams_from_league.append(copy.deepcopy(team_dict))
            team_dict.pop('team', None)
            # NOTE: no API call happens in this loop, so this does not throttle requests;
            # it only delays the response. Kept intentionally (see project guidance).
            if counter % 5 == 0 and counter != 0:
                time.sleep(30)  # To respect API rate limits
            counter += 1

        return teams_from_league

    def get_team_stats(self, **kwargs) -> str:
        """
        Fetch statistics for a team and save them to S3 as JSON.
        """
        endpoint = f"teams/statistics"
        data = self._make_request(endpoint=endpoint, **kwargs)

        item = data.get('response') or {}
        if isinstance(item, list):
            item = next((itm for itm in item
                         if itm.get('team', {}).get('id') == kwargs.get('team')), {})
        if not item:
            message = f"get_team_stats: no statistics returned for team_id: {kwargs.get('team')} with parameters: {kwargs}"
            logger.warning(message)
            return message

        team_item = {
            'team_id' : item.get('team', {}).get('id'),
            'name': item.get('team', {}).get('name'),
            'form': item.get('form'),
            'fixtures_played': item.get('fixtures', {}).get('played'),
            'fixtures_wins': item.get('fixtures', {}).get('wins'),
            'fixtures_draws': item.get('fixtures', {}).get('draws'),
            'fixtures_losses': item.get('fixtures', {}).get('loses'),
            'goals_for': item.get('goals', {}).get('for'),
            'goals_against': item.get('goals', {}).get('against'),
            'biggest_wins': item.get('biggest', {}).get('wins'),
            'biggest_losses': item.get('biggest', {}).get('loses'),
            'clean_sheets_home': item.get('clean_sheet', {}).get('home'),
            'clean_sheets_away': item.get('clean_sheet', {}).get('away'),
            'clean_sheets_total': item.get('clean_sheet', {}).get('total'),
            'failed_to_score_home': item.get('failed_to_score', {}).get('home'),
            'failed_to_score_away': item.get('failed_to_score', {}).get('away'),
            'failed_to_score_total': item.get('failed_to_score', {}).get('total'),
            'penalties_scored': item.get('penalty', {}).get('scored'),
            'penalties_missed': item.get('penalty', {}).get('missed'),
            'lineups': item.get('lineups'),
            'yellow_cards': item.get('cards', {}).get('yellow'),
            'red_cards': item.get('cards', {}).get('red')
        }

        team_id = team_item['team_id']
        df = pd.DataFrame([team_item])
        buffer = self.generate_buffer_from_dataframe(df, 'json')
        self._save_raw_response_to_s3(buffer, endpoint, f'{team_id}.json', **kwargs)
        message = f"get_team_stats completed for team_id: {team_id} with parameters: {kwargs}"
        logger.info(message)
        return message

    def get_team_players(self, **kwargs) -> str:
        """
        Fetch players for a specified team, league, and season and save them to S3 as parquet.
        """
        endpoint = f"players"
        page_data = self._make_request(endpoint=endpoint, **kwargs)
        data = self._paginate_results(endpoint, page_data, **kwargs)
        players_list = []
        for item in data:
            player = item.get('player', {})
            stats = (item.get('statistics') or [{}])[0]  # Stats for the specified season and league

            player_info = {
                'player_id': player.get('id'),
                'name': player.get('name'),
                'age': player.get('age'),
                'first_name': player.get('firstname'),
                'last_name': player.get('lastname'),
                'games': stats.get('games'),
                'goals': stats.get('goals'),
                'passes': stats.get('passes'),
                'tackles': stats.get('tackles'),
                'cards': stats.get('cards'),
                'penalty': stats.get('penalty'),
                'dribbles': stats.get('dribbles'),
                'fouls': stats.get('fouls')
            }
            players_list.append(copy.deepcopy(player_info))

        df = pd.DataFrame(players_list)
        buffer = self.generate_buffer_from_dataframe(df, 'parquet')
        self._save_raw_response_to_s3(buffer, endpoint, 'data.parquet', **kwargs)

        message = f"get_team_players completed for team_id: {kwargs.get('team')} with parameters: {kwargs}"
        logger.info(message)
        return message

    def generate_buffer_from_dataframe(self, df: pd.DataFrame,
                                       extension:str='parquet') -> io.BytesIO | io.StringIO:
        """
        Generate an in-memory buffer from a DataFrame.
        """
        if extension == 'parquet':
            buffer = io.BytesIO()
            df.to_parquet(buffer, index=False)
        elif extension == 'json':
            buffer = io.StringIO()
            df.to_json(buffer, orient='records', lines=True)
        else:
            raise ValueError(f"Unsupported extension: {extension}")
        buffer.seek(0)
        return buffer

    def fetch_data(self, endpoint: str, **kwargs) -> dict:
        """
        Fetch the raw JSON response of a single request to an API-Sports endpoint.
        Args:
            endpoint: API endpoint to fetch from (e.g. 'teams')
            **kwargs: Query parameters for the request
        """
        return self._make_request(endpoint, **kwargs)



if __name__ == "__main__":
    #test the soccer consumer
    logging.basicConfig()
    api_key = os.getenv('SOCCER_API_KEY', None)
    soccer_client = soccer_consumer(api_key=api_key)
    soccer_client.authenticate(api_key)
    data = soccer_client.get_teams_from_league(league=239, season=2024)
    print(data)
