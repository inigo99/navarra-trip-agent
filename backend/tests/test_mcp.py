import asyncio

import duckdb
import pytest
from fastmcp import Client

from navarra_trip import mcp_server
from navarra_trip.ingest.normaliza import guardar

REC = {
    "id": "mon:3153",
    "nombre": "Palacio Real de Olite",
    "categoria": "monumento",
    "subcategorias": ["Castillos/Palacios"],
    "estilo": "Gótico",
    "municipio": "Olite",
    "zona": "Zona Media",
    "lat": 42.4815,
    "lon": -1.6497,
    "descripcion": "Palacio gótico.",
    "descripcion_fuente": "wikipedia",
    "horario": None,
    "visitantes_12m": None,
    "url_fuente": "https://datosabiertos.navarra.es/x",
}


@pytest.fixture(autouse=True)
def db(monkeypatch):
    con = duckdb.connect()
    guardar(con, "recurso", [REC])
    guardar(
        con,
        "alojamiento",
        [
            {
                "id": "aloj:1",
                "nombre": "Parador",
                "modalidad": "Hotel",
                "tipo": "hotel",
                "categoria": "4",
                "plazas": 80,
                "localidad": "Olite",
                "lat": 42.4812,
                "lon": -1.6496,
                "geo_precision": "direccion",
                "url_fuente": "u",
            }
        ],
    )
    guardar(
        con,
        "restaurante",
        [
            {
                "id": "rest:1",
                "nombre": "Asador",
                "categoria": "1",
                "especialidad": None,
                "localidad": "Olite",
                "lat": 42.481,
                "lon": -1.65,
                "geo_precision": "direccion",
                "url_fuente": "u",
            }
        ],
    )
    guardar(
        con,
        "actividad",
        [
            {
                "id": "act:1",
                "nombre": "Kayak Bidasoa",
                "tipo": "Turismo Activo",
                "actividades": ["Kayak", "Rafting"],
                "localidad": "Bera",
                "lat": 43.28,
                "lon": -1.68,
                "geo_precision": "direccion",
                "url_fuente": "u",
            }
        ],
    )
    guardar(
        con,
        "oficina",
        [
            {
                "id": "ofi:1",
                "nombre": "Oficina de Turismo de Olite",
                "zona": "Zona Media",
                "direccion": "Plaza",
                "localidad": "Olite",
                "telefono": "948",
                "email": "e",
                "web": "w",
                "lat": 42.48,
                "lon": -1.65,
                "url_fuente": "u",
            }
        ],
    )
    guardar(
        con,
        "ave",
        [
            {
                "id": "ave:1",
                "nombre": "Buitre leonado",
                "cientifico": "Gyps fulvus",
                "presencia": "residente",
                "observacion": "fácil",
                "zonas": ["Montaña"],
                "descripcion": "Rapaz.",
                "url_fuente": "u",
                "licencia": "CC-BY-4.0",
            },
            {
                "id": "ave:2",
                "nombre": "Abubilla",
                "cientifico": "Upupa epops",
                "presencia": "estival",
                "observacion": "fácil",
                "zonas": ["Todas las zonas"],
                "descripcion": "Cresta.",
                "url_fuente": "u",
                "licencia": "CC-BY-4.0",
            },
        ],
    )
    monkeypatch.setattr(mcp_server, "_db", lambda: con)


def _llamar(nombre, args):
    async def go():
        async with Client(mcp_server.mcp) as c:
            return (await c.call_tool(nombre, args)).structured_content["result"]

    return asyncio.run(go())


def test_expone_las_herramientas():
    async def go():
        async with Client(mcp_server.mcp) as c:
            return {t.name for t in await c.list_tools()}

    assert asyncio.run(go()) == {
        "buscar_recursos",
        "recursos_cerca",
        "alojamientos_cerca",
        "restaurantes_cerca",
        "recurso",
        "ruta",
        "prevision_tiempo",
        "conjuntos",
        "actividades_cerca",
        "oficinas_turismo",
        "aves",
    }


def test_recursos_cerca_y_ficha_por_mcp():
    r = _llamar("recursos_cerca", {"lat": 42.48, "lon": -1.65, "radio_km": 2})
    assert r[0]["id"] == "mon:3153"
    assert _llamar("recurso", {"id": "mon:3153"})["descripcion"] == "Palacio gótico."


def test_buscar_por_estilo_sin_indice_semantico():
    assert _llamar("buscar_recursos", {"estilo": "gotico"})[0]["id"] == "mon:3153"


def test_conjuntos():
    assert {c["tabla"]: c["filas"] for c in _llamar("conjuntos", {})} == {
        "recurso": 1,
        "alojamiento": 1,
        "restaurante": 1,
        "actividad": 1,
        "oficina": 1,
        "ave": 2,
    }


def test_actividades_oficinas_y_aves():
    kayak = _llamar("actividades_cerca", {"lat": 43.28, "lon": -1.68, "actividad": "kayak"})
    assert kayak[0]["id"] == "act:1"
    assert _llamar("actividades_cerca", {"lat": 43.28, "lon": -1.68, "actividad": "golf"}) == []
    assert _llamar("oficinas_turismo", {"lat": 42.48, "lon": -1.65})[0]["nombre"].endswith("Olite")
    assert {a["id"] for a in _llamar("aves", {"zona": "Montaña"})} == {"ave:1", "ave:2"}
    estival = _llamar("aves", {"zona": "Ribera", "presencia": "estival"})
    assert [a["id"] for a in estival] == ["ave:2"]


def test_consulta_sin_indice_cae_a_busqueda_por_texto(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # sin data/lancedb
    assert _llamar("buscar_recursos", {"consulta": "olite"})[0]["id"] == "mon:3153"
