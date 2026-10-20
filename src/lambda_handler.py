
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

# action -> (required event fields, optional event fields); the action name is the client method
ACTIONS = {
    'get_status': ((), ()),
    'get_leagues': (('league',), ()),
    'get_teams': (('league', 'season'), ()),
    'get_squad': (('team',), ()),
    'get_team_players': (('team', 'season'), ()),
    'get_standings': (('league', 'season'), ()),
    'get_fixtures': (('league', 'season'), ('lookback_days',)),
    'get_fixture_details': (('ids', 'league', 'season'), ()),
}


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

def _is_valid_event(event: dict) -> bool:
    spec = ACTIONS.get(event.get('action'))
    if spec is None:
        return False
    required, _ = spec
    return all(event.get(field) for field in required)

def _build_kwargs(event: dict) -> dict:
    required, optional = ACTIONS[event['action']]
    kwargs = {field: event[field] for field in required}
    kwargs.update({field: event[field] for field in optional if event.get(field) is not None})

    if event['action'] == 'get_fixtures' and 'lookback_days' not in kwargs:
        # backfill re-details the whole season; regular runs only the recent window
        if event.get('mode') == 'backfill':
            kwargs['lookback_days'] = None
        else:
            kwargs['lookback_days'] = int(os.getenv('FIXTURE_LOOKBACK_DAYS', '10'))
    return kwargs

def lambda_handler(event: dict, context: dict) -> dict:
    # validate before calling Secrets Manager so bad events don't cost a secret lookup
    if not _is_valid_event(event):
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
    api_client.authenticate(api_key)
    api_client.set_run_context(run_id=event.get('run_id'), run_date=event.get('run_date'))

    action = event['action']
    result = getattr(api_client, action)(**_build_kwargs(event))

    # native JSON so Step Functions can map over lists without States.StringToJson
    return {
        'statusCode': 200,
        'action': action,
        'result': result
    }

if __name__ == "__main__":
    # For local testing purposes
    logging.basicConfig()
    test_event = {
        "action": "get_fixtures",
        "league": 239,
        "season": 2026,
        "lookback_days": 10
    }
    response = lambda_handler(test_event, None)
    print(response)
