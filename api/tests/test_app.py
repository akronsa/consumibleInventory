import importlib.util
import sys
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient


API_DIR = Path(__file__).resolve().parents[1]
APP_PATH = API_DIR / "app.py"


def load_app_module(tmp_path, monkeypatch):
    db_path = tmp_path / "notebooks.db"
    monkeypatch.setenv("GLPI_BASE_URL", "https://glpi.example.com")
    monkeypatch.setenv("GLPI_APP_TOKEN", "app-token")
    monkeypatch.setenv("GLPI_USER_TOKEN", "user-token")
    monkeypatch.setenv("API_KEY", "test-api-key")
    monkeypatch.setenv("NOTEBOOKS_DB_PATH", str(db_path))

    module_name = f"test_api_app_{db_path.stem}_{len(sys.modules)}"
    spec = importlib.util.spec_from_file_location(module_name, APP_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(module_name, None)


@pytest.fixture
def api_module(tmp_path, monkeypatch):
    return load_app_module(tmp_path, monkeypatch)


@pytest.fixture
def client(api_module):
    with TestClient(api_module.app) as test_client:
        yield test_client


@pytest.fixture
def api_headers(api_module):
    return {"X-Api-Key": api_module.API_KEY}


def test_notebooks_flow_save_lookup_entry_and_exit(client, api_headers):
    save_response = client.post(
        "/api/notebooks/products",
        headers=api_headers,
        json={"barcode": " NB-001 \n", "name": "ThinkPad E14", "brand": "Lenovo"},
    )
    assert save_response.status_code == 200

    entry_response = client.post(
        "/api/notebooks/entry",
        headers=api_headers,
        json={"barcode": "NB-001", "company": "Akron"},
    )
    assert entry_response.status_code == 200
    assert entry_response.json()["quantity"] == 1

    lookup_response = client.get("/api/notebooks/lookup/NB-001", headers=api_headers)
    assert lookup_response.status_code == 200
    assert lookup_response.json() == {
        "barcode": "NB-001",
        "name": "ThinkPad E14",
        "brand": "Lenovo",
        "stock": [{"company": "Akron", "quantity": 1}],
    }

    exit_response = client.post(
        "/api/notebooks/exit",
        headers=api_headers,
        json={"barcode": "NB-001", "company": "Akron"},
    )
    assert exit_response.status_code == 200
    assert exit_response.json()["quantity"] == 0

    lookup_after_exit = client.get("/api/notebooks/lookup/%20NB-001%20", headers=api_headers)
    assert lookup_after_exit.status_code == 200
    assert lookup_after_exit.json()["stock"] == []


def test_notebooks_exit_without_stock_returns_conflict(client, api_headers):
    client.post(
        "/api/notebooks/products",
        headers=api_headers,
        json={"barcode": "NB-002", "name": "Latitude 5440", "brand": "Dell"},
    )

    exit_response = client.post(
        "/api/notebooks/exit",
        headers=api_headers,
        json={"barcode": "NB-002", "company": "Akron"},
    )

    assert exit_response.status_code == 409
    assert exit_response.json()["detail"] == {"error": "Sin stock de este equipo en Akron"}


def test_notebooks_exit_removes_zero_stock_row_from_database(client, api_headers, api_module):
    client.post(
        "/api/notebooks/products",
        headers=api_headers,
        json={"barcode": "NB-003", "name": "EliteBook 840", "brand": "HP"},
    )
    client.post(
        "/api/notebooks/entry",
        headers=api_headers,
        json={"barcode": "NB-003", "company": "Akron"},
    )

    exit_response = client.post(
        "/api/notebooks/exit",
        headers=api_headers,
        json={"barcode": "NB-003", "company": "Akron"},
    )

    assert exit_response.status_code == 200

    with api_module._db_lock:
        conn = api_module._db_connect()
        row = conn.execute(
            "SELECT quantity FROM nb_stock WHERE barcode = ? AND company = ?",
            ("NB-003", "Akron"),
        ).fetchone()
        conn.close()

    assert row is None


def test_disk_lookup_returns_expanded_user_name(client, api_headers, api_module, monkeypatch):
    def fake_glpi_request(method, path, **kwargs):
        if path == "/search/Item_DeviceHardDrive":
            return ({"data": [{"4": "Samsung SSD", "5": 77, "6": "Computer"}]}, {})
        if path == "/Computer/77":
            return (
                {
                    "name": "AKR-WS-77",
                    "users_id_dropdown": {"name": "Juan Perez"},
                    "users_id": 101,
                },
                {},
            )
        raise AssertionError(f"Unexpected GLPI call: {method} {path}")

    monkeypatch.setattr(api_module, "glpi_request", fake_glpi_request)

    response = client.get("/api/disk/SN123", headers=api_headers)

    assert response.status_code == 200
    assert response.json() == {
        "serial": "SN123",
        "installed": True,
        "disk": "Samsung SSD",
        "computer": {"id": 77, "name": "AKR-WS-77", "user": "Juan Perez"},
    }


def test_disk_lookup_reports_not_installed_when_item_is_not_a_computer(client, api_headers, api_module, monkeypatch):
    def fake_glpi_request(method, path, **kwargs):
        if path == "/search/Item_DeviceHardDrive":
            return ({"data": [{"4": "WD Blue", "5": 55, "6": "Monitor"}]}, {})
        raise AssertionError(f"Unexpected GLPI call: {method} {path}")

    monkeypatch.setattr(api_module, "glpi_request", fake_glpi_request)

    response = client.get("/api/disk/SN404", headers=api_headers)

    assert response.status_code == 200
    assert response.json() == {"serial": "SN404", "installed": False, "disk": "WD Blue"}


def test_glpi_request_timeout_returns_gateway_timeout(api_module, monkeypatch):
    monkeypatch.setattr(api_module, "get_session_token", lambda force_refresh=False: "session-token")

    def fake_request(*args, **kwargs):
        raise requests.Timeout("timeout")

    monkeypatch.setattr(api_module._http_session, "request", fake_request)

    with pytest.raises(api_module.HTTPException) as exc_info:
        api_module.glpi_request("GET", "/Computer/1")

    assert exc_info.value.status_code == 504
    assert exc_info.value.detail == {"error": "GLPI no respondio a tiempo", "code": "glpi_timeout"}


def test_glpi_request_auth_failure_returns_stable_error(api_module, monkeypatch):
    monkeypatch.setattr(api_module, "get_session_token", lambda force_refresh=False: "session-token")

    class FakeResponse:
        ok = False
        status_code = 401
        text = "unauthorized"
        headers = {}

    monkeypatch.setattr(api_module._http_session, "request", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(api_module.HTTPException) as exc_info:
        api_module.glpi_request("GET", "/Computer/1")

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == {
        "error": "Autenticacion con GLPI rechazada",
        "code": "glpi_auth_failed",
        "upstream_status": 401,
    }


def test_glpi_request_invalid_json_returns_bad_gateway(api_module, monkeypatch):
    monkeypatch.setattr(api_module, "get_session_token", lambda force_refresh=False: "session-token")

    class FakeResponse:
        ok = True
        status_code = 200
        text = "not-json"
        headers = {}

        def json(self):
            raise ValueError("invalid json")

    monkeypatch.setattr(api_module._http_session, "request", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(api_module.HTTPException) as exc_info:
        api_module.glpi_request("GET", "/Computer/1")

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == {
        "error": "GLPI devolvio una respuesta invalida",
        "code": "glpi_invalid_response",
        "upstream_status": 200,
    }


def test_get_model_stock_counts_ids_with_null_date_out(api_module, monkeypatch):
    def fake_glpi_request(method, path, **kwargs):
        if path == "/ConsumableItem/10/Consumable":
            return (
                [
                    {"id": 1, "items_id": "0", "itemtype": "", "date_out": "2026-04-07"},
                    {"id": 2, "items_id": "17", "itemtype": "User", "date_out": None},
                    {"id": 3, "items_id": "0", "itemtype": "", "date_out": ""},
                ],
                {},
            )
        raise AssertionError(f"Unexpected GLPI call: {method} {path}")

    monkeypatch.setattr(api_module, "glpi_request", fake_glpi_request)

    assert api_module.get_model_stock(10) == 2


def test_get_model_by_ref_exposes_instance_count_stock(api_module, monkeypatch):
    def fake_glpi_request(method, path, **kwargs):
        if path == "/ConsumableItem/":
            return (
                [
                    {
                        "id": 22,
                        "name": "Cartucho Cyan",
                        "ref": "CY-22",
                        "consumableitemtypes_id": "Toner",
                    }
                ],
                {},
            )
        if path == "/ConsumableItem/22/Consumable":
            return (
                [
                    {"id": 10, "date_out": None},
                    {"id": 11, "date_out": None},
                    {"id": 12, "date_out": "2026-04-07"},
                ],
                {},
            )
        raise AssertionError(f"Unexpected GLPI call: {method} {path}")

    monkeypatch.setattr(api_module, "glpi_request", fake_glpi_request)

    model = api_module.get_model_by_ref("CY-22", force_refresh=True)

    assert model == {
        "modelId": 22,
        "name": "Cartucho Cyan",
        "ref": "CY-22",
        "type": "Toner",
        "stock": 2,
    }


def test_consume_returns_remaining_from_model_stock(api_module, client, api_headers, monkeypatch):
    monkeypatch.setattr(
        api_module,
        "get_model_by_ref",
        lambda ref, force_refresh=False: {
            "modelId": 33,
            "name": "Toner 85A",
            "ref": ref,
            "type": "Toner",
            "stock": 4 if force_refresh else 5,
        },
    )

    calls = {"instances": 0}

    def fake_glpi_request(method, path, **kwargs):
        if method == "GET" and path == "/ConsumableItem/33/Consumable":
            calls["instances"] += 1
            if calls["instances"] == 1:
                return (
                    [
                        {"id": 99, "date_out": None},
                        {"id": 100, "date_out": None},
                        {"id": 101, "date_out": None},
                        {"id": 102, "date_out": None},
                        {"id": 103, "date_out": None},
                    ],
                    {},
                )
            return (
                [
                    {"id": 100, "date_out": None},
                    {"id": 101, "date_out": None},
                    {"id": 102, "date_out": None},
                    {"id": 103, "date_out": None},
                    {"id": 99, "date_out": "2026-04-07"},
                ],
                {},
            )
        if method == "PUT" and path == "/ConsumableItem/33/Consumable/99":
            return ({"ok": True}, {})
        raise AssertionError(f"Unexpected GLPI call: {method} {path}")

    monkeypatch.setattr(api_module, "glpi_request", fake_glpi_request)

    response = client.post(
        "/api/consume",
        headers=api_headers,
        json={"user_id": 123, "barcode": "TN-85A"},
    )

    assert response.status_code == 200
    assert response.json()["remaining"] == 4
    assert response.json()["model"] == {"id": 33, "name": "Toner 85A", "ref": "TN-85A", "stock": 4}