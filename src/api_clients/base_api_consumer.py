from abc import ABC, abstractmethod

class base_api_consumer(ABC):

    @abstractmethod
    def authenticate(self, api_key: str) -> None:
        """
        Authenticate the API client using the provided API key.
        """
        pass

    @abstractmethod
    def fetch_data(self, endpoint: str) -> dict:
        """
        Fetch data from the specified API endpoint.
        """
        pass