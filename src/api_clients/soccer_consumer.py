from API import APIClient

class soccer_consumer(APIClient):
    def __init__(self, api_key: str = None):
        self.base_url = "https://api-football-v1.p.rapidapi.com/v3"
        self.headers = {
            "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com",
            "X-RapidAPI-Key": api_key
        }
    
    def authenticate(self, api_key: str) -> None:
        """
        Authenticate the Soccer API client using the provided API key.
        """
        ##self.headers["X-RapidAPI-Key"] = api_key
        pass



    