import os
import requests
import urllib3
from dotenv import load_dotenv

# Desactivamos el warning molesto que sale al ignorar la verificación SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()

class GLPIClient:
    def __init__(self):
        self.base_url = os.getenv("GLPI_BASE_URL")
        self.client_id = os.getenv("GLPI_CLIENT_ID")
        self.client_secret = os.getenv("GLPI_CLIENT_SECRET")
        self.username = os.getenv("GLPI_USERNAME")
        self.password = os.getenv("GLPI_PASSWORD")
        self.access_token = None

    def authenticate(self):
        token_url = f"{self.base_url}/api.php/token"
        payload = {
            "grant_type": "password",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "username": self.username,
            "password": self.password,
            "scope": "api"
        }
        try:
            # AGREGAMOS verify=False para ignorar el error de SSL
            response = requests.post(token_url, json=payload, verify=False)
            response.raise_for_status()
            self.access_token = response.json().get("access_token")
            return self.access_token
        except requests.exceptions.RequestException as e:
            print(f"Error de Auth: {e}")
            return None

    def get_headers(self):
        if not self.access_token:
            self.authenticate()
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }