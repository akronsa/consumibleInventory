from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from glpi_client import GLPIClient
import requests

# Inicializamos la aplicación de FastAPI
app = FastAPI(
    title="API Escáner Consumibles GLPI",
    description="Middleware para conectar el escáner Honeywell EDA51 con GLPI 11",
    version="1.0.0"
)

# Configuración CORS (Permite que tu App móvil/web se conecte sin bloqueos de seguridad)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # En producción, puedes restringir esto a la IP/dominio de tu App
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Instanciamos el cliente de GLPI que maneja la autenticación OAuth2
glpi = GLPIClient()

@app.get("/")
def read_root():
    """
    Endpoint de comprobación de estado (Health Check).
    """
    return {"status": "ok", "message": "API del escáner funcionando correctamente"}

@app.get("/usuarios")
def buscar_usuarios(query: str = ""):
    """
    Busca usuarios en GLPI. 
    Si se pasa el parámetro 'query', filtra los resultados.
    """
    headers = glpi.get_headers()
    if not headers:
        raise HTTPException(status_code=500, detail="Falló la autenticación con GLPI")

    # Endpoint correcto para GLPI 11
    url = f"{glpi.base_url}/api.php/Administration/User"
    
    # Parámetros: limitamos a 50 resultados para que el menú desplegable de la app no colapse
    params = {"range": "0-50"}
    if query:
        params["searchText"] = query 
        
    try:
        response = requests.get(url, headers=headers, params=params)
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error de conexión con GLPI: {str(e)}")
    
    # Si GLPI responde correctamente
    if response.status_code == 200:
        usuarios_glpi = response.json()
        
        lista_limpia = []
        for u in usuarios_glpi:
            # GLPI a veces devuelve listas vacías o estructuras raras si no hay resultados
            if not isinstance(u, dict):
                continue
            
            # Intentamos armar el Nombre + Apellido
            nombre_completo = f"{u.get('firstname', '')} {u.get('realname', '')}".strip()
            
            # Si el usuario no tiene nombre cargado, usamos su nombre de usuario (login)
            if not nombre_completo:
                nombre_completo = u.get('name', 'Usuario sin nombre')
                
            lista_limpia.append({
                "id": u["id"],
                "nombre": nombre_completo
            })
            
        return {"total": len(lista_limpia), "data": lista_limpia}
        
    else:
        # Si GLPI devuelve un error (ej. 401, 404, etc.)
        raise HTTPException(
            status_code=response.status_code, 
            detail=f"Error consultando GLPI: {response.text}"
        )