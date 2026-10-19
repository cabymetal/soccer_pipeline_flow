
import api_factory
import boto3
from botocore.exceptions import ClientError
import os
import logging
import json

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv('LOG_LEVEL', 'INFO'))

# cached for the lifetime of the Lambda container (warm invocations skip Secrets Manager)
_api_key = None


def get_client_secret():
    """Retrieve the secret data from secrets from a secure location."""

    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager',
        region_name=os.getenv('AWS_REGION', 'us-east-1')
    )

    # name of the Secrets Manager secret, not the key itself (set by Terraform)
    secret_id = os.getenv('API_SOCCER_API_KEY', 'prod/soccer/api')

    try:
        get_secret_value_response = client.get_secret_value(
            SecretId=secret_id
        )
        secret_string = get_secret_value_response['SecretString']
    except ClientError as e:
        logger.error("Unable to retrieve secret: %s", e)
        raise e

    return json.loads(secret_string)['SOCCER_API_TOKEN']

def _get_api_key() -> str:
    global _api_key
    if _api_key is None:
        _api_key = get_client_secret()
    return _api_key

def read_api_client(api_type: str):
    factories = {
        #this could be extended to other API factories
        "soccer": api_factory.SoccerAPIFactory
    }
    factory_class = factories.get(api_type)
    if not factory_class:
        raise ValueError(f"Unsupported API type: {api_type}")

    return factory_class

def _is_valid_event(action, league, season, teamid) -> bool:
    if not (league and season):
        return False
    if action == 'get_teams_from_league':
        return True
    return action in ('get_team_stats', 'get_team_players') and bool(teamid)

def lambda_handler(event: dict, context: dict) -> dict:
    #get payload from event if needed
    event_action = event.get('action', None)
    event_league = event.get('league', None)
    event_season = event.get('season', None)
    event_teamid = event.get('teamid', None)

    # validate before calling Secrets Manager so bad events don't cost a secret lookup
    if not _is_valid_event(event_action, event_league, event_season, event_teamid):
        logger.warning("Ignoring event with unrecognized action or missing parameters: %s", event)
        return {
            'statusCode': 204,
            **event,
            'body': json.dumps('not recognized action or missing parameters')
        }

    api_key = _get_api_key()
    #add selection logic for different API factories
    factory = read_api_client("soccer")
    api_client = factory(api_key=api_key).create_api_client()

    # Now you can use api_client to interact with the Soccer API
    api_client.authenticate(api_key)

    if event_action == 'get_teams_from_league':
        teams_from_league = api_client.get_teams_from_league(
                league = event_league,
                season = event_season
                )

        return {
            'statusCode': 200,
            'teams': json.dumps(teams_from_league)
        }

    if event_action == 'get_team_stats':
        message = api_client.get_team_stats(league=event_league,
                                            season=event_season,
                                            team=event_teamid)
    else:
        message = api_client.get_team_players(league=event_league,
                                              season=event_season,
                                              team=event_teamid)
    return {
        'statusCode': 200,
        **event,
        'body': json.dumps(message)
    }

if __name__ == "__main__":
    # For local testing purposes
    logging.basicConfig()
    test_event = {
       "action": "get_team_stats",
      "league": 140,
      "teamid": 536,
      "season": 2024
    }
    response = lambda_handler(test_event, None)
    print(response)
