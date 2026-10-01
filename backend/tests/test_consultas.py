import duckdb
import pytest

from navarra_trip.ingest.normaliza import guardar
from navarra_trip.tools.consultas import (
    alojamientos_cerca,
    buscar_recursos,
    preparar,
    recurso,
    recursos_cerca,
    restaurantes_cerca,
)

OLITE = (42.4815, -1.6497)  # el propio palacio


def _rec(id, nombre, cat, sub, lat, lon, estilo=None, municipio="Olite", desc="..."):
    return {
        "id": id,
        "nombre": nombre,
        "categoria": cat,
        "subcategorias": sub,
        "estilo": estilo,
        "municipio": municipio,
        "zona": "Zona Media",
        "lat": lat,
        "lon": lon,
        "descripcion": desc,
        "descripcion_fuente": "wikipedia",
        "horario": None,
        "visitantes_12m": None,
        "url_fuente": "https://datosabiertos.navarra.es/x",
    }


@pytest.fixture
def con():
    c = preparar(duckdb.connect())
    guardar(
        c,
        "recurso",
        [
            _rec(
                "mon:3153",
                "Palacio Real de Olite",
                "monumento",
                ["Castillos/Palacios"],
                42.4815,
                -1.6497,
                estilo="Gótico",
                desc="El palacio real de Olite fue residencia de los reyes de Navarra" + "x" * 400,
            ),
            _rec(
                "mon:3147",
                "Iglesia de Santa María la Real",
                "monumento",
                ["Iglesias y ermitas"],
                42.4818,
                -1.6500,
                estilo="Gótico, Románico",
            ),
            _rec(
                "mon:4965",
                "Iglesia-fortaleza de Santa María de Ujué",
                "monumento",
                ["Iglesias y ermitas"],
                42.5031,
                -1.5007,
                estilo="Románico, Gótico",
                municipio="Ujué",
            ),
            _rec(
                "mon:3004",
                "Catedral de Santa María",
                "monumento",
                ["Iglesias y ermitas"],
                42.8199,
                -1.6426,
                municipio="Pamplona",
            ),
            _rec(
                "esp:5871",
                "Cascada del Cubo",
                "natural",
                ["Cascadas"],
                None,
                None,
                municipio="Ochagavía",
            ),
        ],
    )
    guardar(
        c,
        "alojamiento",
        [
            {
                "id": "aloj:1",
                "nombre": "Parador de Olite",
                "modalidad": "Hotel",
                "tipo": "hotel",
                "categoria": "4 Estrellas",
                "plazas": 87,
                "localidad": "Olite",
                "lat": 42.4812,
                "lon": -1.6496,
                "geo_precision": "direccion",
                "url_fuente": "u",
            },
            {
                "id": "aloj:2",
                "nombre": "Casa rural Ujué",
                "modalidad": "Casa rural vivienda",
                "tipo": "rural",
                "categoria": "-",
                "plazas": 6,
                "localidad": "Ujué",
                "lat": 42.5030,
                "lon": -1.5010,
                "geo_precision": "localidad",
                "url_fuente": "u",
            },
        ],
    )
    guardar(
        c,
        "restaurante",
        [
            {
                "id": "rest:1",
                "nombre": "Asador",
                "categoria": "1 tenedor",
                "especialidad": "Asador",
                "localidad": "Olite",
                "lat": 42.4810,
                "lon": -1.6500,
                "geo_precision": "direccion",
                "url_fuente": "u",
            },
        ],
    )
    return c


def test_recursos_cerca_ordena_por_distancia_y_respeta_el_radio(con):
    r = recursos_cerca(con, *OLITE, radio_km=5)
    assert [x["id"] for x in r] == ["mon:3153", "mon:3147"]
    assert r[0]["km"] < 0.2
    lejos = [x["id"] for x in recursos_cerca(con, *OLITE, radio_km=20)]
    assert "mon:4965" in lejos and "mon:3004" not in lejos  # Ujué ~13 km, Pamplona ~37 km


def test_recursos_cerca_filtra_por_subcategoria_y_resume_la_descripcion(con):
    r = recursos_cerca(con, *OLITE, radio_km=20, subcategoria="Iglesias y ermitas")
    assert [x["id"] for x in r] == ["mon:3147", "mon:4965"]
    palacio = recursos_cerca(con, *OLITE, radio_km=1, limite=1)[0]
    assert len(palacio["descripcion"]) == 300


def test_distancia_haversine_correcta(con):
    pamplona = recursos_cerca(con, *OLITE, radio_km=100, subcategoria="Iglesias y ermitas")[-1]
    assert pamplona["id"] == "mon:3004" and 37 < pamplona["km"] < 38


def test_alojamientos_y_restaurantes(con):
    assert [a["id"] for a in alojamientos_cerca(con, *OLITE, radio_km=20)] == ["aloj:1", "aloj:2"]
    assert [a["id"] for a in alojamientos_cerca(con, *OLITE, radio_km=20, tipo="rural")] == [
        "aloj:2"
    ]
    assert restaurantes_cerca(con, *OLITE)[0]["id"] == "rest:1"


def test_buscar_sin_tildes_y_por_estilo(con):
    assert [x["id"] for x in buscar_recursos(con, texto="ujue")] == ["mon:4965"]
    assert {x["id"] for x in buscar_recursos(con, estilo="romanico")} == {"mon:3147", "mon:4965"}
    assert [x["id"] for x in buscar_recursos(con, texto="reyes de navarra")] == ["mon:3153"]
    assert buscar_recursos(con, categoria="natural")[0]["id"] == "esp:5871"


def test_recurso_completo(con):
    assert len(recurso(con, "mon:3153")["descripcion"]) > 300
    assert recurso(con, "no:existe") is None


def test_conectar_en_solo_lectura(tmp_path):
    from navarra_trip.tools.consultas import conectar

    ruta = tmp_path / "n.duckdb"
    with duckdb.connect(str(ruta)) as c:
        guardar(c, "recurso", [_rec("mon:1", "A", "monumento", ["X"], 42.0, -1.6)])
    assert recursos_cerca(conectar(ruta), 42.0, -1.6, 1)[0]["id"] == "mon:1"
