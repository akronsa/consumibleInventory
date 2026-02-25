# api/Dockerfile
FROM python:3.12-slim

# Instalar herramientas de certificados
RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*

# Copiar el certificado que descargamos al directorio de confianza
COPY akronca.crt /usr/local/share/ca-certificates/akronca.crt

# Actualizar el almacén de certificados del sistema
RUN update-ca-certificates

# Establecer la variable de entorno para que las librerías de Python lo vean
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

WORKDIR /app
COPY app.py /app/app.py

RUN pip install --no-cache-dir fastapi uvicorn requests

EXPOSE 3000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3000"]