# api/Dockerfile
FROM python:3.12-slim

# El certificado CA se monta como volumen en /certs/akronca.crt
ENV REQUESTS_CA_BUNDLE=/certs/akronca.crt
ENV SSL_CERT_FILE=/certs/akronca.crt

WORKDIR /app
COPY requirements.txt /app/requirements.txt
COPY app.py /app/app.py

RUN REQUESTS_CA_BUNDLE= SSL_CERT_FILE= pip install --no-cache-dir -r requirements.txt

EXPOSE 3000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3000"]