from api_clients.API import base_api_consumer
import requests
import boto3
import pandas as pd
import os
import io
import logging
import time
import copy
from botocore.exceptions import ClientError

class soccer_consumer(base_api_consumer):
    def __init__(self, api_key: str = None):
        self.base_url = "https://v3.football.api-sports.io"
        self.api_key = api_key
        self.bucket_name = os.getenv('TARGET_BUCKET', 'soccer-api-raw-data')
    
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

    def _save_raw_response_to_s3(self, response_content, endpoint: str, **kwargs) -> None:
        """
        Save the raw API response content to an S3 bucket with Hadoop-style partitioning.
        Path format: s3://bucket/endpoint/parameters/data.parquet
        """
        s3_client = boto3.client('s3')
        
        #obtain filename extension from buffer type
        team_id = kwargs.get('team_id', None)
        if team_id:
            filename = f'{team_id}.json'
            kwargs.pop('team_id', None)
            # need to encode string buffer to bytes
            response_content = response_content.getvalue().encode('utf-8')
        else:
            filename = 'data.parquet'
        
        # Generate Hadoop partition path
        partition_path = self._generate_hadoop_partition_path(**kwargs)
        
        # Construct S3 key with Hadoop partitioning
        s3_key = f"{endpoint.replace('/', '_')}/{partition_path}/{filename}"
        try:
            response = s3_client.put_object( 
                Bucket = self.bucket_name,
                Key = s3_key,
                Body = response_content
            )

            if response['ResponseMetadata']['HTTPStatusCode'] != 200:
                raise Exception(f"Failed to save data to S3: {response}")
            
            logging.info(f"Saved raw response to s3://{self.bucket_name}/{s3_key}")
        except ClientError as e:
            logging.error(f"Error saving to S3: {e}")
            raise e
        
    
    def _make_request(self, endpoint: str, **kwargs) -> dict:
        """
        Make a GET request to the specified Soccer API endpoint.
        """
        url = f"{self.base_url}/{endpoint}"
        response = requests.get(url, headers=self.headers, params=kwargs)
        if response.status_code != 200:
            response.raise_for_status()
        
        return response.json()

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
        all_data = initial_data.get('response', [])
        endpoint = f'{self.base_url}/{endpoint}'
        
        # Fetch remaining pages if total_pages > 1
        for page in range(2, total_pages + 1):
            kwargs['page'] = page
            page_data = self._make_request(endpoint=endpoint, **kwargs)
            all_data.extend(page_data.get('response', []))
        
        return all_data

    def get_teams_from_league(self, **kwargs: dict) -> list:
        """
        Retrieve all teams from a specified league and season.
        Args:
            **kwargs: Query parameters including 'league' and 'season'
        Returns:
            list: list of dictionary objects containing team id, league, and season
        """
        endpoint = f"teams"
        data = self._make_request(endpoint=endpoint, **kwargs)
        total_pages = data.get('paging', {}).get('total', 1)
        all_data = data.get('response', [])
        for page in range(2, total_pages + 1):
            kwargs['page'] = page
            page_data = self._make_request(endpoint=endpoint, **kwargs)
            all_data.extend(page_data.get('response', []))
        logging.info(f"fetch_data completed for endpoint: {endpoint} with parameters: {kwargs}")
        logging.info(f"Total teams fetched: {len(all_data)}")
        
        kwargs.pop('page', None)
        teams_from_league = []
        counter = 1
        for item in all_data:
            team_dict = {'team': item['team']['id'], **kwargs}
            teams_from_league.append(copy.deepcopy(team_dict))
            team_dict.pop('team', None)
            if counter % 5 == 0 and counter != 0:
                time.sleep(30)  # To respect API rate limits
            counter += 1
        
        return teams_from_league

    def get_team_stats(self, **kwargs: dict) -> dict:
        """
        Fetch data from the specified Soccer API endpoint.
        """
        endpoint = f"teams/statistics"
        data = self._make_request(endpoint=endpoint, **kwargs)

        #TODO handle cases where data is missing or incomplete
        #TODO add logging for missing data scenarios
        item = data.get('response', {})
        if isinstance(item, list):
            for itm in item:
                if itm.get('team', {}).get('id') == kwargs.get('team'):
                    item = itm
                    break

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

        df = pd.DataFrame([team_item])
        buffer = self.generate_buffer_from_dataframe(df, 'json')
        kwargs = {'team_id': item.get('team', {}).get('id'), **kwargs}
        self._save_raw_response_to_s3(buffer, endpoint, **kwargs)
        message = f"get_team_stats completed for team_id: {item.get('team', {}).get('id')} with parameters: {kwargs}"
        logging.info(message)
        return message
    
    def get_team_players(self, **kwargs: dict) -> None:
        """
        Fetch players for a specified team, league, and season.
        """
        endpoint = f"players"
        page_data = self._make_request(endpoint=endpoint, **kwargs)
        total_pages = page_data.get('paging', {}).get('total', 1)
        data = page_data.get('response', [])
        players_list = []
        for page in range(2, total_pages + 1):
            kwargs['page'] = page
            for item in data:
                player = item.get('player', {})
                stats = item.get('statistics', [{}])[0] # Stats for the specified season and league
                
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
            if page % 5 == 0 and page != 0:
                time.sleep(30)  # To respect API rate limits

            data = self._make_request(endpoint=endpoint, **kwargs).get('response', [])
        df = pd.DataFrame(players_list)
        buffer = self.generate_buffer_from_dataframe(df, 'parquet')
        kwargs.pop('page', None)
        self._save_raw_response_to_s3(buffer, endpoint, **kwargs)
        
        message = f"get_team_players completed for team_id: {kwargs.get('team')} with parameters: {kwargs}"
        logging.info(message)
        return message

    def generate_buffer_from_dataframe(self, df: pd.DataFrame,
                                       extension:str='parquet') -> io.BytesIO:
        """
        Generate an in-memory buffer from a DataFrame.
        """
        if extension == 'parquet':
            buffer = io.BytesIO()
            df.to_parquet(buffer, index=False)
        elif extension == 'json':
            buffer = io.StringIO()
            df.to_json(buffer, orient='records', lines=True)
        buffer.seek(0)
        return buffer
    
    def fetch_data(self, endpoint):
        """
        Placeholder method to satisfy abstract base class requirement
        TODO: Implement if needed
        Args:
            endpoint: API endpoint to fetch from
        """
        pass



if __name__ == "__main__":
    #test the soccer consumer
    api_key = os.getenv('SOCCER_API_KEY', None)  
    soccer_client = soccer_consumer(api_key=api_key)
    soccer_client.authenticate(api_key)
    data = soccer_client.fetch_data(league=239, season=2024)
    print(data)

    