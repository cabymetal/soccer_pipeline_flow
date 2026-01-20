from API import APIClient
import requests
import boto3
import pandas as pd
import os
import io
import logging
import time

class soccer_consumer(APIClient):
    def __init__(self, api_key: str = None):
        self.base_url = "https://v3.football.api-sports.io"
        self.api_key = api_key
        self.bucket_name = os.getenv('TARGET_BUCKET', 'soccer-api-raw-data')
    
    def authenticate(self, api_key: str) -> None:
        """
        Authenticate the Soccer API client using the provided API key.
        """
        self.headers = {
            "X-RapidAPI-Key": api_key
        }

    def _generate_hadoop_partition_path(self, **kwargs) -> str:
        """
        Generate Hadoop-style partition path based on the kwargs provided.
        """
        key_suffix = ""
        for key, value in kwargs.items():
            key_suffix += f"{key}={value}/"
        
        return key_suffix.rstrip('/')

    def _save_raw_response_to_s3(self, response_content: io.BytesIO, endpoint: str, **kwargs) -> None:
        """
        Save the raw API response content to an S3 bucket with Hadoop-style partitioning.
        Path format: s3://bucket/endpoint/parameters/data.parquet
        """
        s3_client = boto3.client('s3')
        
        
        # Generate Hadoop partition path
        partition_path = self._generate_hadoop_partition_path(**kwargs)
        
        # Construct S3 key with Hadoop partitioning
        s3_key = f"{endpoint.replace('/', '_')}/{partition_path}/data.json",

        s3_client.put_object( 
            Bucket = self.bucket_name,
            Key = s3_key,
            Body = response_content
        )
    
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

    def fetch_data(self, **kwargs: dict) -> dict:
        """
        Fetch data from the specified Soccer API endpoint.
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
        team_stats = []
        counter = 1
        for item in all_data:
            kwargs = {'team': item['team']['id'], **kwargs}
            team_stats.append(self.fetch_data_for_team(**kwargs))
            if counter % 5 == 0 and counter != 0:
                time.sleep(60)  # To respect API rate limits
            counter += 1

        df = pd.DataFrame(team_stats)
        kwargs.pop('team', None)
        self._save_raw_response_to_s3(self.generate_buffer_from_dataframe(df), endpoint, **kwargs)
        return

    def fetch_data_for_team(self, **kwargs: dict) -> dict:
        """
        Fetch data from the specified Soccer API endpoint.
        """
        endpoint = f"teams/statistics"
        data = self._make_request(endpoint=endpoint, **kwargs)

        #TODO handle cases where data is missing or incomplete
        #TODO add logging for missing data scenarios
        item = data.get('response', {})
        team_item = {
            'team_id' : item['team']['id'],
            'name': item['team']['name'],
            'form': item['form'],
            'fixtures_played': item['fixtures']['played'],
            'fixtures_wins': item['fixtures']['wins'],
            'fixtures_draws': item['fixtures']['draws'],
            'fixtures_losses': item['fixtures']['loses'],
            'goals_for': item['goals']['for'],
            'goals_against': item['goals']['against'],
            'biggest_wins': item['biggest']['wins'],
            'biggest_losses': item['biggest']['loses'],
            'clean_sheets_home': item['clean_sheet']['home'],
            'clean_sheets_away': item['clean_sheet']['away'],
            'clean_sheets_total': item['clean_sheet']['total'],
            'failed_to_score_home': item['failed_to_score']['home'],
            'failed_to_score_away': item['failed_to_score']['away'],
            'failed_to_score_total': item['failed_to_score']['total'],
            'penalties_scored': item['penalty']['scored'],
            'penalties_missed': item['penalty']['missed'],
            'lineups': item['lineups'],
            'yellow_cards': item['cards']['yellow'],
            'red_cards': item['cards']['red']
        }

        return team_item
    
    def generate_buffer_from_dataframe(self, df: pd.DataFrame) -> io.BytesIO:
        """
        Generate an in-memory buffer from a DataFrame.
        """
        buffer = io.BytesIO()
        df.to_parquet(buffer, index=False)
        return buffer



if __name__ == "__main__":
    #test the soccer consumer
    api_key = os.getenv('SOCCER_API_KEY', None)  
    soccer_client = soccer_consumer(api_key=api_key)
    soccer_client.authenticate(api_key)
    data = soccer_client.fetch_data(league=239, season=2024)
    print(data)
        




    