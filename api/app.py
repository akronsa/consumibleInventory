# api/app.py
import os
import time
import threading
from typing import Any, Dict, List, Optional, Tuple

import requests
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

GLPI_BASE_URL = os.environ.get("GLPI_BASE_URL", "").rstrip("/")
GLPI_APP_TOKEN = os.environ.get("GLPI_APP_TOKEN", "")
GLPI_USER_TOKEN = os.environ.get("GLPI_USER_TOKEN", "")
PORT = int(os.environ.get("PORT", "3000"))

if not GLPI_BASE_URL:
    raise RuntimeError("Falta GLPI_BASE_URL")
if not GLPI_APP_TOKEN:
    raise RuntimeError("Falta GLPI_APP_TOKEN")
if not GLPI_USER_TOKEN:
    raise RuntimeError("Falta GLPI_USER_TOKEN")

API = f"{GLPI_BASE_URL}/apirest.php"

app = FastAPI(title="GLPI Consumibles Proxy (Legacy)")

# ---------- Session cache ----------
_session_lock = threading.Lock()
_session_token: Optional[str] = None
_session_obtained_at: float = 0.0
SESSION_MAX_AGE_SEC = 8 * 60 * 60  # rotación defensiva

def _init_session() -> str:
    """Init legacy session usando App-Token + Authorization: user_token"""
    url = f"{API}/initSession"
    headers = {
        "App-Token": GLPI_APP_TOKEN,
        "Authorization": f"user_token {GLPI_USER_TOKEN}",
    }
    r = requests.post(url, headers=headers, timeout=20)
    if not r.ok:
        raise RuntimeError(f"initSession failed {r.status_code}: {r.text}")

    j = r.json()
    token = j.get("session_token") or j.get("sessionToken")
    if not token:
        raise RuntimeError(f"initSession: no session_token en respuesta: {j}")
    return token

def get_session_token(force_refresh: bool = False) -> str:
    global _session_token, _session_obtained_at
    with _session_lock:
        now = time.time()
        if (not force_refresh and _session_token and (now - _session_obtained_at) < SESSION_MAX_AGE_SEC):
            return _session_token
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
        return requests.request(method, url, headers=headers, params=params, json=json_body, timeout=30)

    token = get_session_token()
    r = do(token)

    if r.status_code in (401, 403):
        token2 = get_session_token(force_refresh=True)
        r = do(token2)

    if not r.ok:
        raise HTTPException(status_code=502, detail=f"GLPI {r.status_code}: {r.text}")

    data = r.json() if r.text else None
    headers_out = {k.lower(): v for k, v in r.headers.items()}
    return data, headers_out

# ---------- Helpers ----------
def normalize_barcode(s: Any) -> str:
    return str(s or "").strip()

def is_available(c: Dict[str, Any]) -> bool:
    return (
        c.get("date_out") is None
        and (c.get("itemtype") is None or c.get("itemtype") == "")
        and str(c.get("items_id", "0")) in ("0", "0.0")
    )

def today_yyyy_mm_dd() -> str:
    import datetime as dt
    return dt.date.today().isoformat()

# ---------- Cache ref -> model ----------
_model_cache_lock = threading.Lock()
_model_cache: Dict[str, Dict[str, Any]] = {}
MODEL_CACHE_TTL_SEC = 10 * 60

def get_model_by_ref(ref: str) -> Optional[Dict[str, Any]]:
    now = time.time()
    with _model_cache_lock:
        cached = _model_cache.get(ref)
        if cached and (now - cached["ts"]) < MODEL_CACHE_TTL_SEC:
            return cached["val"]

    # Simple: listar modelos y filtrar por ref
    data, _ = glpi_request("GET", "/ConsumableItem/", range_header="0-9999")
    items = data if isinstance(data, list) else (data.get("data", []) if isinstance(data, dict) else [])

    found = next((it for it in items if str(it.get("ref", "")).strip() == ref), None)
    if not found:
        return None

    val = {"modelId": int(found["id"]), "name": found.get("name"), "ref": found.get("ref", ref)}
    with _model_cache_lock:
        _model_cache[ref] = {"ts": now, "val": val}
    return val

# ---------- API ----------
class ConsumeRequest(BaseModel):
    user_id: int
    barcode: str

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/api/users")
def users(q: str = Query(..., min_length=2)):
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

@app.post("/api/consume")
def consume(req: ConsumeRequest):
    barcode = normalize_barcode(req.barcode)
    if not barcode:
        raise HTTPException(status_code=400, detail="barcode requerido")

    model = get_model_by_ref(barcode)
    if not model:
        raise HTTPException(status_code=404, detail=f"No existe ConsumableItem con ref={barcode}")

    model_id = model["modelId"]

    data, _ = glpi_request("GET", f"/ConsumableItem/{model_id}/Consumable", params={"range": "0-200"})
    items: List[Dict[str, Any]] = data if isinstance(data, list) else (data.get("data", []) if isinstance(data, dict) else [])

    candidates = [c for c in items if is_available(c)]
    if not candidates:
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
            # --- Contar stock restante ---
            # Volvemos a pedir los consumibles para ver cuántos quedan disponibles
            data_stock, _ = glpi_request("GET", f"/ConsumableItem/{model_id}/Consumable", params={"range": "0-200"})
            all_items = data_stock if isinstance(data_stock, list) else data_stock.get("data", [])
            # Contamos los que NO tienen date_out y no están asignados
            remaining = len([i for i in all_items if is_available(i)])
            # -------------------------------------------
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

@app.get("/api/model/{barcode}")
def get_model_info(barcode: str):
    barcode = normalize_barcode(barcode)
    model = get_model_by_ref(barcode)
    if not model:
        raise HTTPException(status_code=404, detail="Modelo no encontrado")
    return model