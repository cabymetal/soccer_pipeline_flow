"""
Class that provides factory methods to create API clients for different services.
"""
from abc import ABC, abstractmethod

from api_clients.base_api_consumer import base_api_consumer

class APIFactory(ABC):
    @abstractmethod
    def create_api_client(self) -> base_api_consumer:
        """
        Create and return an API client for the specified service.
        """
        pass

class SoccerAPIFactory(APIFactory):
    def __init__(self, api_key: str):
        self.api_key = api_key

    def create_api_client(self) -> base_api_consumer:
        from api_clients.soccer_consumer import soccer_consumer
        client = soccer_consumer(api_key=self.api_key)
        return client