from datetime import date

from test_planner import FakeLLM, _grafo, con  # noqa: F401  (con es un fixture)

from navarra_trip import evaluacion as ev
from navarra_trip.planner import Requisitos

VIERNES = date(2026, 10, 2)


def test_bateria_y_consultas_coherentes():
    filas = ev.leer_csv("peticiones.csv")
    assert len({f["id"] for f in filas}) == len(filas) >= 87
    assert {f["esperado"] for f in filas} <= {
        "plan",
        "pregunta",
        "pregunta_dias",
        "pregunta_base",
        "plan_con_aviso",
        "plan_sin_inventar",
    }
    assert {f["idioma"] for f in filas} == {"es", "en", "fr", "eu"}
    for c in ev.leer_csv("consultas_semanticas.csv"):
        assert all(i.split(":")[0] in ("mon", "esp") for i in c["relevantes"].split("|"))


def test_idioma_y_fechas():
    assert ev.idioma_de("Visita la iglesia y los claustros del monasterio") == "es"
    assert ev.idioma_de("Visit the castle and walk to the old town with your family") == "en"
    assert ev.idioma_de("Visitez le château et les églises avec vos enfants") == "fr"
    assert ev.idioma_de("Gaztelua eta eliza bisitatu, egun bat dago") == "eu"
    for f in ev.leer_csv("peticiones.csv"):  # el agente corrige con esto el idioma del LLM
        assert ev.idioma_de(f["peticion"]) in (f["idioma"], None), f["id"]
    # n57: texto en francés con nombres de lugar en español
    assert (
        ev.idioma_de(
            "## Jour 1\n- **Iglesia de Santiago el Mayor** [mon:3166], Puente La Reina. Une église romane avec un portail"
        )
        == "fr"
    )
    assert ev._fecha_ok("sabado", "2026-10-03", VIERNES)
    assert not ev._fecha_ok("sabado", "2026-10-07", VIERNES)
    assert ev._fecha_ok("pasada", "2020-01-01", VIERNES)
    assert ev._fecha_ok("", None, VIERNES) is None


def _fila(**kw):
    base = {
        "id": "t1",
        "tipo": "normal",
        "idioma": "es",
        "peticion": "2 días en Olite",
        "dias": "2",
        "base": "olite",
        "transporte": "coche",
        "ritmo": "",
        "intereses": "",
        "fecha": "",
        "esperado": "plan",
    }
    return base | kw


def test_validar_plan_y_pregunta(con):  # noqa: F811
    req = Requisitos(dias=2, base="Olite", intereses=["románica"])
    r = ev.ejecutar(_grafo(con, req, ["Visita [mon:inventado]"] * 2), _fila())
    v = ev.validar(_fila(), r, con, VIERNES)
    assert v["comportamiento_ok"] and v["dias_ok"] and v["base_ok"] and v["idioma_ok"]
    assert v["ids_ok"]  # el id que el LLM mete en su frase se quita
    assert v["sin_repetidas"] and v["paradas_ok"]

    r = ev.ejecutar(
        _grafo(con, Requisitos(base="Olite"), []), _fila(dias="", esperado="pregunta_dias")
    )
    v = ev.validar(_fila(dias="", esperado="pregunta_dias"), r, con, VIERNES)
    assert v["comportamiento_ok"] and "ids_ok" not in v


def test_mas_de_7_dias_se_recorta_con_aviso(con):  # noqa: F811
    r = ev.ejecutar(_grafo(con, Requisitos(dias=10, base="Olite"), ["x"] * 3), _fila(dias="7"))
    assert len(r["plan"]["dias"]) == 7
    assert any("7 días" in a for a in r["plan"]["avisos"])
