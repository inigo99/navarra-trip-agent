import itertools
import json
import math
from datetime import date

import duckdb
import pytest
from langchain_core.messages import AIMessage

from navarra_trip import agente, planner
from navarra_trip.ingest.normaliza import guardar
from navarra_trip.planner import Requisitos
from navarra_trip.tools.consultas import _filas, preparar, recursos_cerca

OLITE = {"nombre": "Olite/Erriberri", "lat": 42.48, "lon": -1.65}


def _rec(id, nombre, cat, lat, lon, estilo=None):
    return {
        "id": id,
        "nombre": nombre,
        "categoria": cat,
        "subcategorias": ["Iglesias y ermitas" if cat == "monumento" else "Cascadas"],
        "estilo": estilo,
        "municipio": "X",
        "zona": "Zona Media",
        "lat": lat,
        "lon": lon,
        "descripcion": f"{nombre}, en Navarra.",
        "descripcion_fuente": "wikipedia",
        "horario": None,
        "visitantes_12m": None,
        "url_fuente": "u",
    }


def _sitio(tipo, id, lat, lon, localidad="Olite/Erriberri"):
    extra = (
        {"modalidad": "Hotel", "tipo": "hotel", "categoria": "-", "plazas": 10}
        if tipo == "aloj"
        else {"categoria": "-", "especialidad": "Asador"}
    )
    return {
        "id": f"{tipo}:{id}",
        "nombre": f"{tipo} {id}",
        "localidad": localidad,
        "lat": lat,
        "lon": lon,
        "geo_precision": "direccion",
        "url_fuente": "u",
    } | extra


@pytest.fixture
def con():
    c = preparar(duckdb.connect())
    este = [  # dos grupos claros: al este (Ujué) y al oeste (Tafalla-Artajona)
        _rec(
            f"mon:e{i}",
            f"Iglesia románica este {i}",
            "monumento",
            42.50,
            -1.50 - i / 100,
            "Románico",
        )
        for i in range(3)
    ] + [_rec("esp:e", "Cascada este", "natural", 42.51, -1.49)]
    oeste = [
        _rec(
            f"mon:o{i}",
            f"Iglesia románica oeste {i}",
            "monumento",
            42.55,
            -1.80 + i / 100,
            "Románico",
        )
        for i in range(3)
    ] + [_rec("esp:o", "Cascada oeste", "natural", 42.56, -1.81)]
    lejos = [_rec("mon:lejos", "Catedral románica", "monumento", 43.30, -1.65, "Románico")]
    guardar(c, "recurso", este + oeste + lejos)
    guardar(
        c, "alojamiento", [_sitio("aloj", 1, 42.4801, -1.6501), _sitio("aloj", 2, 42.48, -1.65)]
    )
    guardar(
        c,
        "restaurante",
        [
            _sitio("rest", 1, 42.50, -1.50),
            _sitio("rest", 2, 42.50, -1.505),
            _sitio("rest", 3, 42.55, -1.80),
        ],
    )
    return c


def _km(a, b):
    return 111 * math.dist(
        (a[0], a[1] * math.cos(math.radians(42.7))), (b[0], b[1] * math.cos(math.radians(42.7)))
    )


def matriz(pts, modo):  # 1 km = 1 min
    return [[_km(a, b) for b in pts] for a in pts]


def ruta(pts, modo, geometria=False):
    tramos = [_km(a, b) for a, b in itertools.pairwise(pts)]
    return {"km": round(sum(tramos), 1), "minutos": round(sum(tramos)), "geometria": None}


def test_lugar_encuentra_nombre_bilingue(con):
    assert planner.lugar(con, "olite")["nombre"] == "Olite/Erriberri"
    assert planner.lugar(con, "Madrid") is None


def test_candidatos_alternan_intereses_y_respetan_el_radio(con):
    req = Requisitos(dias=1, base="Olite", intereses=["cascada", "románica"], ritmo="relajado")
    c = planner.candidatos(con, req, OLITE, planner.similitud_texto(con))
    ids = [r["id"] for r in c]
    assert ids[0].startswith("esp:") and ids[1].startswith("mon:")
    assert "mon:lejos" not in ids  # a 90 km


def test_agrupar_separa_por_zona_y_reparte(con):
    req = Requisitos(dias=2, base="Olite", intereses=["románica"], ritmo="normal")
    c = planner.candidatos(con, req, OLITE, planner.similitud_texto(con))
    grupos = planner.agrupar(OLITE, c, req, [None, None], matriz)
    zonas = [{r["id"][4] for r in g} for g in grupos]  # 'e' u 'o'
    assert sorted(map(sorted, zonas)) == [["e"], ["o"]]
    assert all(len(g) <= 4 for g in grupos)


def test_mal_tiempo_tira_de_monumentos(con):
    c = [r | {"puntos": 1.0} for r in recursos_cerca(con, 42.48, -1.65, 40, limite=50)]
    req = Requisitos(dias=2, base="Olite", ritmo="relajado")
    grupos = planner.agrupar(OLITE, c, req, [{"mal_tiempo": True}, None], matriz)
    assert grupos[0] and all(r["categoria"] == "monumento" for r in grupos[0])


def test_agrupar_por_tiempo_real_no_por_linea_recta(con):
    # en línea recta, este y oeste están cerca; por carretera (matriz falsa) no se juntan
    c = [r | {"puntos": 1.0} for r in recursos_cerca(con, 42.48, -1.65, 40, limite=50)]
    req = Requisitos(dias=2, base="Olite", ritmo="normal")

    def montaña(pts, modo):
        m = matriz(pts, modo)
        este = [i for i, p in enumerate(pts) if p[1] > -1.6]
        return [
            [
                x * 3 if (i in este) != (j in este) and 0 not in (i, j) else x
                for j, x in enumerate(f)
            ]
            for i, f in enumerate(m)
        ]

    for g in planner.agrupar(OLITE, c, req, [None, None], montaña):
        assert len({r["lon"] > -1.6 for r in g}) == 1


def test_ordenar_respeta_tope_de_conduccion(con):
    req = Requisitos(dias=1, base="Olite", ritmo="relajado")
    lejos = _filas(con, "SELECT * FROM recurso WHERE id IN ('mon:e0', 'mon:lejos')", [])
    lejos = [r | {"puntos": 1.0 if r["id"] == "mon:e0" else 0.1} for r in lejos]
    d = planner.ordenar(OLITE, lejos, req, matriz, ruta)
    assert [p["id"] for p in d["paradas"]] == ["mon:e0"]  # la catedral (180 min ida y vuelta) sale
    assert d["paradas"][0]["llegada"] == "10:12"  # 12 km


class FakeLLM:
    def __init__(self, req, textos):
        self.req, self.textos = req, list(textos)

    def with_structured_output(self, schema):
        return self

    def invoke(self, msgs):
        if isinstance(msgs[0][1], str) and msgs[0][1].startswith("Extraes"):
            return self.req
        return AIMessage(content=self.textos.pop(0))


def _grafo(con, req, textos):
    return agente.construir(
        FakeLLM(req, textos),
        con,
        sim=planner.similitud_texto(con),
        matriz=matriz,
        ruta=ruta,
        prevision=lambda lat, lon, dias: [],
        hoy=date(2026, 10, 2),
    )


def test_agente_pide_lo_que_falta(con):
    s = _grafo(con, Requisitos(base="Olite"), []).invoke({"peticion": "Escapada a Olite"})
    assert s["pregunta"] == "¿Cuántos días dura el viaje?"


def test_agente_plan_completo_y_corrige_ids_inventados(con):
    req = Requisitos(dias=2, base="Olite", intereses=["románica"])
    s = _grafo(con, req, ["Visita [mon:inventado]"] * 2).invoke({"peticion": "2 días en Olite"})
    plan = s["plan"]
    paradas = [p["id"] for d in plan["dias"] for p in d["paradas"]]
    assert len(paradas) == len(set(paradas)) == 8  # 6 románicas + 2 de relleno
    assert all(d["comida"] for d in plan["dias"])
    assert {a["id"] for a in plan["alojamientos"]} == {"aloj:1", "aloj:2"}
    # el LLM falla 2 veces: queda la plantilla, que solo cita ids del plan
    assert s["intentos"] == 2 and set(agente.CITA.findall(s["texto"])) <= planner.ids(plan)
    tipos = {f["properties"].get("tipo") for f in plan["geojson"]["features"]}
    assert {"base", "parada", "comida", "alojamiento"} <= tipos


def test_agente_acepta_texto_bien_citado(con):
    req = Requisitos(dias=1, base="Olite", intereses=["cascada"], ritmo="relajado")
    plan = None

    class Eco(FakeLLM):  # cita exactamente las paradas que recibe
        def invoke(self, msgs):
            if msgs[0][1].startswith("Extraes"):
                return self.req
            nonlocal plan
            plan = json.loads(msgs[1][1])
            return AIMessage(
                content=" ".join(f"[{p['id']}]" for d in plan["dias"] for p in d["agenda"])
            )

    g = agente.construir(
        Eco(req, []),
        con,
        sim=planner.similitud_texto(con),
        matriz=matriz,
        ruta=ruta,
        prevision=lambda *a: [],
        hoy=date(2026, 10, 2),
    )
    s = g.invoke({"peticion": "1 día"})
    assert s["intentos"] == 1 and not s.get("error")
    agenda = plan["dias"][0]["agenda"]
    assert "lat" not in agenda[0]  # al LLM no le llegan coordenadas
    # la cascada este + 2 iglesias a su lado; comida al acabar la última (13:40)
    assert [a["tipo"] for a in agenda] == ["parada", "parada", "parada", "comida"]


def test_fecha_relativa():
    viernes = date(2026, 10, 2)
    assert agente.fecha_relativa("empezando el sábado", viernes) == date(2026, 10, 3)
    assert agente.fecha_relativa("Ce week-end", viernes) == date(2026, 10, 3)
    assert agente.fecha_relativa("mañana", viernes) == date(2026, 10, 3)
    assert agente.fecha_relativa("el viernes", viernes) == viernes
    assert agente.fecha_relativa("del 12 al 14 de octubre", viernes) is None


def test_ordenar_quita_lo_que_mas_tiempo_ahorra(con):
    # la catedral lejana puntúa algo más, pero cuesta 180 min: salen las dos cercanas
    req = Requisitos(dias=1, base="Olite", ritmo="relajado")
    filas = _filas(con, "SELECT * FROM recurso WHERE id IN ('mon:e0', 'mon:e1', 'mon:lejos')", [])
    filas = [r | {"puntos": 0.85 if r["id"] == "mon:lejos" else 0.8} for r in filas]
    d = planner.ordenar(OLITE, filas, req, matriz, ruta)
    assert sorted(p["id"] for p in d["paradas"]) == ["mon:e0", "mon:e1"]
