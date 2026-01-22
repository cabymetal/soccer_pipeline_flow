
import api_factory
import boto3
from botocore.exceptions import ClientError
import os
import logging
import json


def get_client_secret():
    """Retrieve the secret data from secrets from a secure location."""

    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager',
        region_name=os.getenv('AWS_REGION', 'us-east-1')
    )

    #this is just a placeholder for the secret id
    secret_id = os.getenv('API_SOCCER_API_KEY', '/prod/soccer/api')

    try:
        get_secret_value_response = client.get_secret_value(
            SecretId='my_secret_id'
        )
        secret = get_secret_value_response['SecretString']
    except ClientError as e:
        logging.error(f"Unable to retrieve secret: {e}")
        raise e
    
    secret_dict = json.loads(secret)
    return secret_dict['api_key']

def read_api_client(api_type: str):
    factories = {
        #this could be extended to other API factories
        "soccer": api_factory.SoccerAPIFactory
    }
    factory_class = factories.get(api_type)
    if not factory_class:
        raise ValueError(f"Unsupported API type: {api_type}")
    
    client = factory_class.create_api_client()
    return client


def lambda_handler(event: dict, context: dict) -> dict:
    # Example usage of the SoccerAPIFactory
    api_key = get_client_secret()
    #add selection logic for different API factories
    factory = read_api_client("soccer")
    api_client = factory(api_key=api_key).create_api_client()

    #get payload from event if needed
    event_action = event.get('action', None)
    event_league = event.get('league', None)
    event_season = event.get('season', None)
    event_teamid = event.get('teamid', None)
    
    # Now you can use api_client to interact with the Soccer API
    api_client.authenticate(api_key)

    if event_action == 'get_teams_from_league' and event_league and event_season:
        teams_from_league = api_client.get_teams_from_league(
                league = event_league, 
                season = event_season
                )
        
        return {
            'statusCode': 200,
            'teams': json.dumps(teams_from_league)
        }
    if event_league and event_season and event_teamid:
        if event_action == 'get_team_stats':
            message = api_client.get_team_stats(league=event_league,
                                                season=event_season,
                                                team_id=event_teamid)
            return {
                'statusCode': 200,
                'body': json.dumps(message)
            }
        elif event_action == 'get_team_players':
            players = api_client.get_team_players(league=event_league,
                                                  season=event_season,
                                                  team_id=event_teamid)
            return {
                'statusCode': 200,
                'body': json.dumps(players)
            }

    return {
        'statusCode': 200,
        'body': json.dumps('API client created successfully!')
    }
