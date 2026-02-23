import os
import requests
from dotenv import load_dotenv

# Cargar variables de entorno (ideal para Docker)
load_dotenv()

class GLPIClient:
    def __init__(self):
        # Configuraciones desde variables de entorno
        self.base_url = os.getenv("GLPI_BASE_URL") # ej: https://tu-glpi.com
        self.client_id = os.getenv("GLPI_CLIENT_ID")
        self.client_secret = os.getenv("GLPI_CLIENT_SECRET")
        self.username = os.getenv("GLPI_USERNAME")
        self.password = os.getenv("GLPI_PASSWORD")
        
        self.access_token = None

    def authenticate(self):
        """
        Obtiene el token Bearer de GLPI 11 usando Password Grant.
        """
        token_url = f"{self.base_url}/api.php/token"
        
        # Parámetros exigidos por la documentación de GLPI 11
        payload = {
            "grant_type": "password",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "username": self.username,
            "password": self.password,
            "scope": "api" # Scope necesario para acceder a los endpoints
        }

        try:
            # Generalmente los endpoints OAuth2 esperan form-data, pero si GLPI 
            # exige JSON, puedes cambiar data=payload por json=payload
            response = requests.post(token_url, data=payload)
            response.raise_for_status() # Lanza error si el status no es 2xx
            
            token_data = response.json()
            self.access_token = token_data.get("access_token")
            
            print("Autenticación exitosa. Token obtenido.")
            return self.access_token
            
        except requests.exceptions.RequestException as e:
            print(f"Error al autenticar en GLPI: {e}")
            if response is not None:
                print("Detalle:", response.text)
            return None

    def get_headers(self):
        """
        Devuelve las cabeceras necesarias para hacer peticiones a la API.
        """
        if not self.access_token:
            self.authenticate()
            
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

# --- PRUEBA DEL CÓDIGO ---
if __name__ == "__main__":
    # Para probar este script localmente, asegúrate de tener un archivo .env
    # con las variables mencionadas arriba.
    glpi = GLPIClient()
    token = glpi.authenticate()
    
    if token:
        print(f"Cabeceras listas para usar: {glpi.get_headers()}")