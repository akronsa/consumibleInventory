# api/app.py
import os
import time
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple

import logging
import requests
from requests.adapters import HTTPAdapter
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("consumibles")

GLPI_BASE_URL   = os.environ.get("GLPI_BASE_URL", "").rstrip("/")
GLPI_APP_TOKEN  = os.environ.get("GLPI_APP_TOKEN", "")
GLPI_USER_TOKEN = os.environ.get("GLPI_USER_TOKEN", "")
PORT            = int(os.environ.get("PORT", "3000"))
API_KEY         = os.environ.get("API_KEY", "")

if not GLPI_BASE_URL:
    raise RuntimeError("Falta GLPI_BASE_URL")
if not GLPI_APP_TOKEN:
    raise RuntimeError("Falta GLPI_APP_TOKEN")
if not GLPI_USER_TOKEN:
    raise RuntimeError("Falta GLPI_USER_TOKEN")
if not API_KEY:
    raise RuntimeError("Falta API_KEY")

API = f"{GLPI_BASE_URL}/apirest.php"

COMPANIES = {"Akron", "Mojon Uno", "Tekron", "Terraplane", "Fundacion Akron"}

# ---------- HTTP session (connection pooling) ----------
_http_session = requests.Session()
_adapter = HTTPAdapter(pool_connections=1, pool_maxsize=10, max_retries=0)
_http_session.mount("https://", _adapter)
_http_session.mount("http://", _adapter)


# ---------- Auth ----------
def require_api_key(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail={"error": "API key inválida"})

def _get_client_ip(request: Request) -> str:
    return (
        request.headers.get("x-real-ip")
        or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or request.client.host
    )

limiter = Limiter(key_func=_get_client_ip)
app = FastAPI(title="GLPI Consumibles Proxy (Legacy)")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---------- Session cache ----------
_session_lock = threading.Lock()
_session_token: Optional[str] = None
_session_obtained_at: float = 0.0
SESSION_MAX_AGE_SEC = int(os.environ.get("SESSION_MAX_AGE_SEC", str(8 * 60 * 60)))
GLPI_TIMEOUT_SEC   = int(os.environ.get("GLPI_TIMEOUT_SEC", "30"))

def _init_session() -> str:
    """Init legacy session usando App-Token + Authorization: user_token"""
    url = f"{API}/initSession"
    headers = {
        "App-Token": GLPI_APP_TOKEN,
        "Authorization": f"user_token {GLPI_USER_TOKEN}",
    }
    r = _http_session.post(url, headers=headers, timeout=GLPI_TIMEOUT_SEC)
    if not r.ok:
        raise RuntimeError(f"initSession failed {r.status_code}: {r.text}")

    j = r.json()
    token = j.get("session_token") or j.get("sessionToken")
    if not token:
        raise RuntimeError(f"initSession: no session_token en respuesta: {j}")
    log.info("GLPI session iniciada")
    return token

def get_session_token(force_refresh: bool = False) -> str:
    global _session_token, _session_obtained_at
    with _session_lock:
        now = time.time()
        if not force_refresh and _session_token and (now - _session_obtained_at) < SESSION_MAX_AGE_SEC:
            return _session_token
        if force_refresh and _session_token and (now - _session_obtained_at) < 10:
            return _session_token
        log.info("Renovando session GLPI (force=%s)", force_refresh)
        token = _init_session()
        _session_token = token
        _session_obtained_at = now
        return token

def glpi_headers(session_token: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    h = {"App-Token": GLPI_APP_TOKEN, "Session-Token": session_token, "Accept": "application/json"}
    if extra:
        h.update(extra)
    return h

def glpi_request(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    range_header: Optional[str] = None,
) -> Tuple[Any, Dict[str, str]]:
    url = f"{API}{path if path.startswith('/') else '/' + path}"

    def do(token: str):
        headers = glpi_headers(token)
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        if range_header:
            headers["Range"] = range_header
        return _http_session.request(method, url, headers=headers, params=params, json=json_body, timeout=GLPI_TIMEOUT_SEC)

    token = get_session_token()
    r = do(token)

    if r.status_code in (401, 403):
        token2 = get_session_token(force_refresh=True)
        r = do(token2)

    if not r.ok:
        log.error("GLPI error %s: %s", r.status_code, r.text[:200])
        raise HTTPException(status_code=502, detail={"error": f"GLPI {r.status_code}: {r.text}"})

    data = r.json() if r.text else None
    headers_out = {k.lower(): v for k, v in r.headers.items()}
    return data, headers_out

# ---------- Helpers ----------
def normalize_barcode(s: Any) -> str:
    return str(s or "").strip()

def get_dropdown_text(value: Any) -> Optional[str]:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, dict):
        for key in ("name", "completename", "text", "value"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                return text.strip()
    return None

def is_available(c: Dict[str, Any]) -> bool:
    date_out = c.get("date_out")
    items_id = c.get("items_id")
    itemtype = c.get("itemtype")
    return (
        (date_out is None or date_out == "") and
        (items_id is None or str(items_id) in ("0", "0.0", "")) and
        (itemtype is None or itemtype == "")
    )

def today_yyyy_mm_dd() -> str:
    import datetime as dt
    return dt.date.today().isoformat()

# ---------- Consume lock ----------
_consume_lock = threading.Lock()

# ---------- Cache ref -> model ----------
_model_cache_lock = threading.Lock()
_model_cache: Dict[str, Dict[str, Any]] = {}
MODEL_CACHE_TTL_SEC = int(os.environ.get("MODEL_CACHE_TTL_SEC", str(10 * 60)))

def get_model_by_ref(ref: str) -> Optional[Dict[str, Any]]:
    now = time.time()
    with _model_cache_lock:
        cached = _model_cache.get(ref)
        if cached and (now - cached["ts"]) < MODEL_CACHE_TTL_SEC:
            return cached["val"]

    data, _ = glpi_request("GET", "/ConsumableItem/", params={"expand_dropdowns": 1}, range_header="0-9999")
    items = data if isinstance(data, list) else (data.get("data", []) if isinstance(data, dict) else [])

    found = next((it for it in items if str(it.get("ref", "")).strip() == ref), None)
    if not found:
        return None

    val = {"modelId": int(found["id"]), "name": found.get("name"), "ref": found.get("ref", ref), "type": found.get("consumableitemtypes_id")}
    with _model_cache_lock:
        _model_cache[ref] = {"ts": now, "val": val}
    return val

# ==========================================================================
# Notebooks — SQLite
# ==========================================================================

DB_PATH = os.environ.get("NOTEBOOKS_DB_PATH", "/app/data/notebooks.db")
_db_lock = threading.Lock()

def _db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def _db_init():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _db_lock:
        conn = _db_connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS nb_products (
                barcode  TEXT PRIMARY KEY,
                name     TEXT,
                brand    TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS nb_stock (
                barcode  TEXT NOT NULL,
                company  TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (barcode, company)
            );
            CREATE TABLE IF NOT EXISTS nb_movements (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                barcode    TEXT NOT NULL,
                company    TEXT NOT NULL,
                direction  TEXT NOT NULL CHECK(direction IN ('+', '-')),
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        conn.commit()
        conn.close()

_db_init()

def _nb_get_product(barcode: str) -> Optional[Dict[str, str]]:
    """Devuelve el producto desde la DB local, o None si no existe."""
    with _db_lock:
        conn = _db_connect()
        row = conn.execute("SELECT barcode, name, brand FROM nb_products WHERE barcode = ?", (barcode,)).fetchone()
        conn.close()
    if row:
        return {"barcode": row["barcode"], "name": row["name"], "brand": row["brand"]}
    return None

def _nb_get_stock(barcode: str) -> List[Dict[str, Any]]:
    with _db_lock:
        conn = _db_connect()
        rows = conn.execute(
            "SELECT company, quantity FROM nb_stock WHERE barcode = ? AND quantity > 0 ORDER BY company",
            (barcode,),
        ).fetchall()
        conn.close()
    return [{"company": r["company"], "quantity": r["quantity"]} for r in rows]

# ---------- Notebooks request models ----------
class NbMoveRequest(BaseModel):
    barcode: str
    company: str

class NbProductRequest(BaseModel):
    barcode: str
    name: str
    brand: str = ""

# ---------- Notebooks endpoints ----------
@app.get("/api/notebooks/lookup/{barcode}", dependencies=[Depends(require_api_key)])
@limiter.limit("60/minute")
def nb_lookup(request: Request, barcode: str):
    barcode = normalize_barcode(barcode)
    if not barcode:
        raise HTTPException(status_code=400, detail={"error": "barcode requerido"})

    product = _nb_get_product(barcode)
    if not product:
        raise HTTPException(status_code=404, detail={"error": "Producto no encontrado"})

    stock = _nb_get_stock(barcode)
    return {**product, "stock": stock}

@app.post("/api/notebooks/products", dependencies=[Depends(require_api_key)])
@limiter.limit("30/minute")
def nb_save_product(request: Request, req: NbProductRequest):
    barcode = normalize_barcode(req.barcode)
    name = req.name.strip()
    if not barcode:
        raise HTTPException(status_code=400, detail={"error": "barcode requerido"})
    if not name:
        raise HTTPException(status_code=400, detail={"error": "name requerido"})

    with _db_lock:
        conn = _db_connect()
        conn.execute(
            "INSERT INTO nb_products (barcode, name, brand) VALUES (?, ?, ?) "
            "ON CONFLICT(barcode) DO UPDATE SET name = excluded.name, brand = excluded.brand",
            (barcode, name, req.brand.strip()),
        )
        conn.commit()
        conn.close()

    log.info("NB product saved manually barcode=%s name=%s", barcode, name)
    stock = _nb_get_stock(barcode)
    return {"barcode": barcode, "name": name, "brand": req.brand.strip(), "stock": stock}

@app.get("/api/notebooks/stock", dependencies=[Depends(require_api_key)])
@limiter.limit("60/minute")
def nb_stock_all(request: Request):
    with _db_lock:
        conn = _db_connect()
        rows = conn.execute("""
            SELECT s.barcode, p.name, p.brand, s.company, s.quantity
            FROM nb_stock s
            JOIN nb_products p ON p.barcode = s.barcode
            WHERE s.quantity > 0
            ORDER BY p.name, s.company
        """).fetchall()
        conn.close()
    return [{"barcode": r["barcode"], "name": r["name"], "brand": r["brand"],
             "company": r["company"], "quantity": r["quantity"]} for r in rows]

@app.post("/api/notebooks/entry", dependencies=[Depends(require_api_key)])
@limiter.limit("30/minute")
def nb_entry(request: Request, req: NbMoveRequest):
    barcode = normalize_barcode(req.barcode)
    company = req.company.strip()

    if not barcode:
        raise HTTPException(status_code=400, detail={"error": "barcode requerido"})
    if company not in COMPANIES:
        raise HTTPException(status_code=400, detail={"error": f"Empresa inválida. Opciones: {sorted(COMPANIES)}"})

    product = _nb_get_product(barcode)
    if not product:
        raise HTTPException(status_code=404, detail={"error": "Producto no encontrado"})

    with _db_lock:
        conn = _db_connect()
        conn.execute(
            "INSERT INTO nb_stock (barcode, company, quantity) VALUES (?, ?, 1) "
            "ON CONFLICT(barcode, company) DO UPDATE SET quantity = quantity + 1",
            (barcode, company),
        )
        conn.execute(
            "INSERT INTO nb_movements (barcode, company, direction) VALUES (?, ?, '+')",
            (barcode, company),
        )
        quantity = conn.execute(
            "SELECT quantity FROM nb_stock WHERE barcode = ? AND company = ?", (barcode, company)
        ).fetchone()["quantity"]
        conn.commit()
        conn.close()

    log.info("NB ENTRY barcode=%s company=%s quantity=%s", barcode, company, quantity)
    return {"ok": True, "product": product, "company": company, "quantity": quantity}

@app.post("/api/notebooks/exit", dependencies=[Depends(require_api_key)])
@limiter.limit("30/minute")
def nb_exit(request: Request, req: NbMoveRequest):
    barcode = normalize_barcode(req.barcode)
    company = req.company.strip()

    if not barcode:
        raise HTTPException(status_code=400, detail={"error": "barcode requerido"})
    if company not in COMPANIES:
        raise HTTPException(status_code=400, detail={"error": f"Empresa inválida. Opciones: {sorted(COMPANIES)}"})

    with _db_lock:
        conn = _db_connect()
        row = conn.execute(
            "SELECT quantity FROM nb_stock WHERE barcode = ? AND company = ?", (barcode, company)
        ).fetchone()

        if not row or row["quantity"] <= 0:
            conn.close()
            raise HTTPException(status_code=409, detail={"error": f"Sin stock de este equipo en {company}"})

        conn.execute(
            "UPDATE nb_stock SET quantity = quantity - 1 WHERE barcode = ? AND company = ?",
            (barcode, company),
        )
        conn.execute(
            "INSERT INTO nb_movements (barcode, company, direction) VALUES (?, ?, '-')",
            (barcode, company),
        )
        quantity = conn.execute(
            "SELECT quantity FROM nb_stock WHERE barcode = ? AND company = ?", (barcode, company)
        ).fetchone()["quantity"]
        conn.commit()
        conn.close()

    product = _nb_get_product(barcode)
    log.info("NB EXIT barcode=%s company=%s quantity=%s", barcode, company, quantity)
    return {"ok": True, "product": product, "company": company, "quantity": quantity}

# ==========================================================================
# API — GLPI endpoints
# ==========================================================================

class ConsumeRequest(BaseModel):
    user_id: int
    barcode: str

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/api/users", dependencies=[Depends(require_api_key)])
@limiter.limit("60/minute")
def users(request: Request, q: str = Query(..., min_length=2)):
    params = {
        "criteria[0][field]": 1,
        "criteria[0][searchtype]": "contains",
        "criteria[0][value]": q,
        "forcedisplay[0]": 2,
    }
    data, headers = glpi_request("GET", "/search/User", params=params, range_header="0-19")
    results = [{"name": row.get("1"), "id": int(row.get("2"))} for row in (data or {}).get("data", [])]
    return {
        "results": results,
        "contentRange": headers.get("content-range") or (data or {}).get("content-range"),
        "totalcount": (data or {}).get("totalcount"),
    }

@app.post("/api/consume", dependencies=[Depends(require_api_key)])
@limiter.limit("30/minute")
def consume(request: Request, req: ConsumeRequest):
    barcode = normalize_barcode(req.barcode)
    if not barcode:
        raise HTTPException(status_code=400, detail={"error": "barcode requerido"})

    model = get_model_by_ref(barcode)
    if not model:
        raise HTTPException(status_code=404, detail={"error": f"No existe ConsumableItem con ref={barcode}"})

    model_id = model["modelId"]

    with _consume_lock:
        data, _ = glpi_request("GET", f"/ConsumableItem/{model_id}/Consumable", params={"range": "0-999"})
        items: List[Dict[str, Any]] = data if isinstance(data, list) else (data.get("data", []) if isinstance(data, dict) else [])

        candidates = [c for c in items if is_available(c)]
        if not candidates:
            log.warning("Sin stock: model=%s id=%s", model.get("name"), model_id)
            raise HTTPException(status_code=409, detail={"error": "Sin stock", "modelId": model_id, "modelName": model.get("name")})

        date_out = today_yyyy_mm_dd()

        last_err = None
        for c in candidates:
            consumable_id = c.get("id")
            try:
                glpi_request(
                    "PUT",
                    f"/ConsumableItem/{model_id}/Consumable/{consumable_id}",
                    json_body={"input": {"items_id": str(req.user_id), "itemtype": "User", "date_out": date_out}},
                )
                data_stock, _ = glpi_request("GET", f"/ConsumableItem/{model_id}/Consumable", params={"range": "0-999"})
                all_items = data_stock if isinstance(data_stock, list) else data_stock.get("data", [])
                remaining = len([i for i in all_items if is_available(i)])
                log.info(
                    "CONSUME user_id=%s barcode=%s model=%s consumable_id=%s remaining=%s",
                    req.user_id, barcode, model.get("name"), consumable_id, remaining,
                )
                return {
                    "ok": True,
                    "model": {"id": model_id, "name": model.get("name"), "ref": model.get("ref")},
                    "consumable_id": consumable_id,
                    "date_out": date_out,
                    "remaining": remaining,
                }
            except HTTPException as e:
                last_err = e.detail
                continue

    raise HTTPException(status_code=409, detail={"error": "No se pudo asignar (concurrencia)", "last": last_err})

@app.get("/api/model/{barcode}", dependencies=[Depends(require_api_key)])
@limiter.limit("60/minute")
def get_model_info(request: Request, barcode: str):
    barcode = normalize_barcode(barcode)
    model = get_model_by_ref(barcode)
    if not model:
        raise HTTPException(status_code=404, detail={"error": "Modelo no encontrado"})
    return model

@app.get("/api/disk/{serial}", dependencies=[Depends(require_api_key)])
@limiter.limit("60/minute")
def get_disk_info(request: Request, serial: str):
    serial = normalize_barcode(serial)
    if not serial:
        raise HTTPException(status_code=400, detail={"error": "serial requerido"})

    params = {
        "criteria[0][field]": 10,
        "criteria[0][searchtype]": "equals",
        "criteria[0][value]": serial,
    }
    data, _ = glpi_request("GET", "/search/Item_DeviceHardDrive", params=params, range_header="0-4")
    results = (data or {}).get("data", [])

    if not results:
        raise HTTPException(status_code=404, detail={"error": f"No se encontró disco con serial {serial}"})

    row = results[0]
    computer_id = row.get("5")
    itemtype = row.get("6")
    disk_model = row.get("4")

    if not computer_id or itemtype != "Computer":
        return {"serial": serial, "installed": False, "disk": disk_model}

    comp, _ = glpi_request("GET", f"/Computer/{computer_id}", params={"expand_dropdowns": 1})
    user_name = (
        get_dropdown_text(comp.get("users_id_dropdown"))
        or get_dropdown_text(comp.get("_users_id"))
        or get_dropdown_text(comp.get("users_id"))
    )
    return {
        "serial": serial,
        "installed": True,
        "disk": disk_model,
        "computer": {
            "id": computer_id,
            "name": comp.get("name"),
            "user": user_name,
        },
    }
