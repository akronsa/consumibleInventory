# 1. Usamos una imagen oficial de Python ligera
FROM python:3.11-slim

# 2. Le decimos a Docker que todo el trabajo se hará en la carpeta /app
WORKDIR /app
ENV CACHE_BUST=1
# 3. Copiamos PRIMERO el archivo de dependencias (esto optimiza el tiempo de build)
COPY requirements.txt .

# 4. Instalamos las librerías necesarias sin guardar caché para que la imagen pese menos
RUN pip install --no-cache-dir -r requirements.txt

# 5. Ahora copiamos todo el resto del código de tu repositorio al contenedor
COPY . .

# 6. Exponemos el puerto 5000 (el mismo que configuramos en el docker-compose.yml)
EXPOSE 5000

# 7. El comando para arrancar la API usando Uvicorn (el servidor para FastAPI)
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5000"]
