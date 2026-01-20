
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
    # Now you can use api_client to interact with the Soccer API
    api_client.authenticate(api_key)


    return {
        'statusCode': 200,
        'body': json.dumps('API client created successfully!')
    }
