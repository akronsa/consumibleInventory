# Consumibles — Akron IT

Sistema interno para registrar la salida de consumibles y consultar el estado de discos rígidos, integrado con **GLPI** como backend de inventario.

---

## ¿Qué hace?

El sistema tiene dos módulos principales:

### Consumibles
Permite registrar la entrega de un consumible (tóner, cartuchos, etc.) a un usuario. El operador busca al usuario, escanea el código de barras del producto y confirma la salida. El stock se descuenta automáticamente en GLPI.

### Discos
Permite consultar si un disco rígido está instalado en alguna computadora, ingresando o escaneando el número de serie. Muestra el equipo asignado y el usuario correspondiente.

---

## Arquitectura

```
Browser
  │
  ▼
Nginx  ──► Sirve el frontend estático (HTML/CSS/JS)
  │        Inyecta la API key en los requests al backend
  │
  ▼
FastAPI (Python)  ──► Proxy hacia la API REST de GLPI
                       Maneja autenticación, caché y control de concurrencia
  │
  ▼
GLPI API
```

- **Frontend**: HTML + CSS + JavaScript vanilla. Sin frameworks ni build tools.
- **Backend**: Python 3.12 con FastAPI. Actúa como proxy/wrapper de la API de GLPI.
- **Nginx**: Sirve los archivos estáticos y hace de reverse proxy. Inyecta la `API_KEY` en cada request al backend (el browser nunca la ve).
- **GLPI**: Sistema de inventario externo. Toda la data vive ahí.

---

## Requisitos

- Docker y Docker Compose
- GLPI con API REST habilitada
- Certificado CA del servidor GLPI (si usa HTTPS con CA privada), montado en `/etc/akron/certs/akronca.crt` en el host
- Chrome actualizado (para el escáner de cámara — usa la [BarcodeDetector API](https://developer.mozilla.org/en-US/docs/Web/API/BarcodeDetector))

---

## Configuración

Copiá el archivo de ejemplo y completá las variables:

```bash
cp .env.example .env
```

| Variable | Descripción |
|---|---|
| `GLPI_APP_TOKEN` | Token de aplicación de la API de GLPI |
| `GLPI_USER_TOKEN` | Token de usuario de la API de GLPI |
| `API_KEY` | Clave interna entre Nginx y el backend (generá una con `openssl rand -hex 32`) |
| `GLPI_BASE_URL` | URL base de GLPI, por defecto `https://glpi.akron.com.ar` |

---

## Despliegue

```bash
docker compose up -d --build
```

El frontend queda disponible en el puerto **80**.

---

## Endpoints de la API

Todos los endpoints (excepto `/health`) requieren el header `X-Api-Key`, que Nginx inyecta automáticamente.

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/api/users?q={texto}` | Busca usuarios en GLPI |
| `GET` | `/api/model/{barcode}` | Obtiene info de un modelo de consumible por código de barras |
| `POST` | `/api/consume` | Registra la salida de un consumible a un usuario |
| `GET` | `/api/disk/{serial}` | Consulta el estado de un disco rígido por número de serie |

---

## Escáner de cámara

Ambas páginas incluyen un escáner de códigos de barras por cámara con las siguientes funciones:

- Soporte para múltiples cámaras (se recuerda la última usada)
- Linterna / antorcha (en dispositivos compatibles)
- Toque para enfocar
- Autoenfoque periódico cada 2.5 segundos
- Compatible con EAN-13, Code-128, QR, y otros formatos

> Requiere Chrome actualizado y permisos de cámara. También funciona con lectores de códigos de barras físicos (HID).

---

## Estructura del proyecto

```
├── api/
│   ├── app.py              # Backend FastAPI
│   ├── requirements.txt    # Dependencias Python con versiones fijadas
│   └── Dockerfile
├── frontend/
│   ├── index.html          # Módulo: Consumibles
│   ├── discos.html         # Módulo: Discos
│   └── logo.png
├── nginx/
│   ├── default.conf.template   # Config de Nginx (API key inyectada en startup)
│   └── Dockerfile
├── docker-compose.yml
└── .env.example
```
