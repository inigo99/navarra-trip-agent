import httpx

from navarra_trip.ingest.ckan import descargar

REGISTROS = [{"_id": i, "Nombre": f"r{i}"} for i in range(2500)]


def _api(request: httpx.Request) -> httpx.Response:
    p = request.url.params
    if request.url.path.endswith("package_show"):
        result = {
            "title": "Arte y monumentos",
            "license_id": "CC-BY-4.0",
            "resources": [
                {"id": "xsd", "datastore_active": False},
                {"id": "csv", "datastore_active": True, "last_modified": "2023-11-16"},
            ],
        }
    else:
        assert p["resource_id"] == "csv"
        off, lim = int(p["offset"]), int(p["limit"])
        result = {"records": REGISTROS[off : off + lim], "total": len(REGISTROS)}
    return httpx.Response(200, json={"success": True, "result": result})


def test_descarga_pagina_todo_y_guarda_metadatos():
    with httpx.Client(transport=httpx.MockTransport(_api)) as c:
        d = descargar(c, "arte-y-monumentos")
    assert d["registros"] == REGISTROS
    assert d["licencia"] == "CC-BY-4.0"
    assert d["recurso_id"] == "csv"
    assert d["url_fuente"].endswith("/dataset/arte-y-monumentos")
