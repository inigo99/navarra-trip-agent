import duckdb
from test_planner import _rec

from navarra_trip import planner
from navarra_trip.ingest import bodegas
from navarra_trip.ingest.normaliza import guardar
from navarra_trip.planner import Requisitos
from navarra_trip.tools.consultas import preparar


def _e(id, nombre, **tags):
    return {
        "type": "way",
        "id": id,
        "center": {"lat": 42.48, "lon": -1.66},
        "tags": tags | {"name": nombre, "craft": "winery"},
    }


def test_una_por_bodega_y_sin_oficinas():
    fs = bodegas.filas(
        [
            _e(1, "Bodegas Marco Real"),
            _e(2, "Bodegas Marco Real", website="https://m.es", opening_hours="Mo-Fr 10:00-14:00"),
            _e(3, "Administracion", operator="Bodegas San Martin"),
            _e(4, "Grupo Aedil S.A."),
            _e(5, "Bodega Cooperativa"),
        ]
    )
    assert [(f["id"], f["web"]) for f in fs] == [("bod:w2", "https://m.es")]
    f = bodegas.completar(fs)[0]
    assert f["categoria"] == "bodega" and f["duracion_min"] == 90
    assert "Horario en OpenStreetMap: Mo-Fr 10:00-14:00" in f["descripcion"]


def test_bodegas_solo_si_se_pide_vino():
    con = preparar(duckdb.connect())
    guardar(
        con,
        "recurso",
        [
            _rec("mon:1", "Castillo", "monumento", 42.49, -1.65),
            _rec("bod:w1", "Bodegas Ochoa", "bodega", 42.485, -1.658),
        ],
    )
    base = {"nombre": "Olite", "lat": 42.48, "lon": -1.65}
    sim = planner.similitud_texto(con)

    def ids(intereses):
        req = Requisitos(dias=1, intereses=intereses)
        return {r["id"] for r in planner.candidatos(con, req, base, sim)}

    assert "bod:w1" not in ids(["castillos"])
    assert "bod:w1" in ids(["castillos", "vino"]) and "bod:w1" in ids(["bodegas"])
    assert planner.quiere_vino(Requisitos(intereses=["wine tasting"]))
    assert not planner.quiere_vino(Requisitos(intereses=["cuevas", "divertido"]))
