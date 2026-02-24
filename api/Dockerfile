# api/Dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY app.py /app/app.py

RUN pip install --no-cache-dir fastapi uvicorn requests

EXPOSE 3000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3000"]