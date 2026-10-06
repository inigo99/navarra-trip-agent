import itertools
import json
import math
from datetime import date

import duckdb
import pytest

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
        "duracion_min": None,
        "longitud_km": None,
        "desnivel_m": None,
        "cimas": None,
        "gpx": None,
        "wikidata_id": None,
        "precio": None,
        "de_pago": None,
        "web": None,
        "revisado": None,
        "cerrado": False,
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
            _sitio("rest", 4, 42.4805, -1.6505)
            | {"especialidad": "Casera o regional, De tapas y raciones"},
        ],
    )
    bar = {"tipo": "bar", "localidad": "Olite", "horario": None, "web": None, "url_fuente": "u"}
    guardar(
        c,
        "bar",
        [  # tres juntos en el casco (más rest:4, con tapas) y uno suelto a 600 m
            bar | {"id": "bar:n1", "nombre": "Bar 1", "lat": 42.4810, "lon": -1.6500},
            bar | {"id": "bar:n2", "nombre": "Bar 2", "lat": 42.4812, "lon": -1.6510},
            bar | {"id": "bar:n3", "nombre": "Bar 3", "lat": 42.4800, "lon": -1.6515},
            bar | {"id": "bar:n4", "nombre": "Bar suelto", "lat": 42.4855, "lon": -1.6500},
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
    assert planner.lugar(con, "Erriberrin")["nombre"] == "Olite/Erriberri"  # euskera
    assert planner.lugar(con, "Eriberri")["nombre"] == "Olite/Erriberri"  # errata del LLM


def test_intereses_lejanos(con):
    base = planner.lugar(con, "olite")
    sims = {"catedral": {"mon:lejos": 1.0, "mon:e0": 0.2}, "románico": {"mon:e0": 1.0}}
    req = Requisitos(base="Olite", intereses=["catedral", "románico", "playa"])
    lejos = planner.intereses_lejanos(con, req, base, lambda i: sims.get(i, {}), k=1)
    assert lejos == ["catedral", "playa"]


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
    assert all(len(g) <= planner.PARADAS["normal"] for g in grupos)


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
    assert d["paradas"][0]["llegada"] == "10:15"  # 12 km = 10:12, al cuarto de hora


class FakeLLM:
    """textos: una entrada por llamada a redactar. str = esa frase para cada lugar pedido;
    dict = {id: frase}; Exception = el modelo falla (p. ej. JSON cortado)."""

    def __init__(self, req, textos):
        self.req, self.textos = req, list(textos)

    def with_structured_output(self, schema):
        return self

    def invoke(self, msgs):
        if isinstance(msgs[0][1], str) and msgs[0][1].startswith("Extraes"):
            return self.req
        t = self.textos.pop(0)
        if isinstance(t, Exception):
            raise t
        if isinstance(t, str):
            t = {lugar["id"]: t for lugar in json.loads(msgs[1][1])["lugares"]} if t else {}
        return agente.Redaccion(lugares=[agente.Frase(id=k, texto=v) for k, v in t.items()])


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


def test_sin_dias_en_la_peticion_pregunta_aunque_el_llm_los_ponga(con):
    s = _grafo(con, Requisitos(dias=3, base="Pamplona"), []).invoke(
        {"peticion": "Quiero ver Navarra desde Pamplona"}
    )
    assert s["pregunta"] == "¿Cuántos días dura el viaje?"


def test_frases_en_otro_idioma_o_en_bucle_fuera():
    assert agente._frase_valida("A Romanesque church with a carved portal.", "en")
    assert not agente._frase_valida(
        "Explore la capilla, un templo en el corazón de Pamplona.", "en"
    )
    assert not agente._frase_valida("Liédenan " * 60, "eu")


def test_agente_plan_completo_y_corrige_ids_inventados(con):
    req = Requisitos(dias=2, base="Olite", intereses=["románica"])
    s = _grafo(con, req, ["Visita [mon:inventado]"] * 2).invoke({"peticion": "2 días en Olite"})
    plan = s["plan"]
    paradas = [p["id"] for d in plan["dias"] for p in d["paradas"]]
    assert len(paradas) == len(set(paradas)) == 8  # 6 románicas + 2 de relleno
    assert all(d["comida"] for d in plan["dias"])
    assert {a["id"] for a in plan["alojamientos"]} == {"aloj:1", "aloj:2"}
    # el texto lo compone el código: solo cita ids del plan (el que el LLM metió en su frase
    # se quita) y cada parada lleva su frase
    assert s["intentos"] == 1 and set(agente.CITA.findall(s["texto"])) == planner.ids(plan)
    assert s["texto"].count("Visita") == len(paradas)
    tipos = {f["properties"].get("tipo") for f in plan["geojson"]["features"]}
    assert {"base", "parada", "comida", "alojamiento"} <= tipos


def test_agente_redacta_frases_y_el_codigo_las_horas(con):
    req = Requisitos(dias=1, base="Olite", intereses=["cascada"], ritmo="relajado")
    pedido = None

    class Eco(FakeLLM):
        def invoke(self, msgs):
            if msgs[0][1].startswith("Extraes"):
                return self.req
            nonlocal pedido
            pedido = json.loads(msgs[1][1])
            frases = [agente.Frase(id=x["id"], texto="Bonito.") for x in pedido["lugares"]]
            frases.append(agente.Frase(id="mon:inventado", texto="No existe."))
            return agente.Redaccion(lugares=frases)

    g = agente.construir(
        Eco(req, []),
        con,
        sim=planner.similitud_texto(con),
        matriz=matriz,
        ruta=ruta,
        prevision=lambda *a: [],
        hoy=date(2026, 10, 2),
    )
    s = g.invoke({"peticion": "1 día tranquilo"})
    assert s["intentos"] == 1 and "llegada" not in pedido["lugares"][0]  # al LLM, sin horas
    assert "mon:inventado" not in s["texto"]
    dia1 = s["texto"].split("## Where")[0].split("## Dónde dormir")[0]
    lineas = [x for x in dia1.splitlines() if x.startswith("- ")]
    tipos = ["parada" if "Bonito." in x else x.split("** ", 1)[1].split(":")[0] for x in lineas]
    # relajado: hasta 6 paradas; la comida entre ellas; vuelta antes de la cena, que cierra
    assert tipos.count("parada") >= 4 and "Comida" in tipos[1:-3]
    assert (
        tipos[-1] == "Cena"
        and tipos[-2].startswith("Tiempo libre")
        or tipos[-2].startswith("Vuelta")
    )


def test_fecha_relativa():
    viernes = date(2026, 10, 2)
    assert agente.fecha_relativa("empezando el sábado", viernes) == date(2026, 10, 3)
    assert agente.fecha_relativa("Ce week-end", viernes) == date(2026, 10, 3)
    assert agente.fecha_relativa("mañana", viernes) == date(2026, 10, 3)
    assert agente.fecha_relativa("el viernes", viernes) == viernes
    assert agente.fecha_relativa("del 12 al 14 de octubre", viernes) is None
    assert agente.fecha_relativa("dentro de dos meses", viernes) == date(2026, 12, 1)
    assert agente.fecha_relativa("in 2 weeks", viernes) == date(2026, 10, 16)
    assert agente.fecha_relativa("Pamplona in 3 days", viernes) is None  # duración
    assert agente.fecha_relativa("Un finde por el norte", viernes) == date(2026, 10, 3)
    assert agente.fecha_relativa("Larunbatean egun bat Garesen", viernes) == date(2026, 10, 3)
    assert agente.dias_euskera("Hiru egun Izaban") == 3
    assert agente.dias_euskera("Egun bat Lekunberrin") == 1
    assert agente.dias_euskera("Asteburua Erriberrin") == 2
    assert agente.dias_euskera("Nafarroa ikusi nahi dut") is None


def test_ordenar_quita_lo_que_mas_tiempo_ahorra(con):
    # la catedral lejana puntúa algo más, pero cuesta 180 min: salen las dos cercanas
    req = Requisitos(dias=1, base="Olite", ritmo="relajado")
    filas = _filas(con, "SELECT * FROM recurso WHERE id IN ('mon:e0', 'mon:e1', 'mon:lejos')", [])
    filas = [r | {"puntos": 0.85 if r["id"] == "mon:lejos" else 0.8} for r in filas]
    d = planner.ordenar(OLITE, filas, req, matriz, ruta)
    assert sorted(p["id"] for p in d["paradas"]) == ["mon:e0", "mon:e1"]


def test_candidatos_sin_duplicados(con):
    # mismo sitio con dos fichas (como Foz de Arbaiun y su mirador): solo entra una
    dup = _filas(con, "SELECT * FROM recurso WHERE id = 'mon:e0'", [])[0]
    filas = _filas(con, "SELECT * FROM recurso", []) + [dup | {"id": "esp:mirador"}]
    guardar(con, "recurso", filas)
    req = Requisitos(dias=2, base="Olite", intereses=["románica"])
    ids = [r["id"] for r in planner.candidatos(con, req, OLITE, planner.similitud_texto(con))]
    assert len({"mon:e0", "esp:mirador"} & set(ids)) == 1


def test_candidatos_sin_cerrados(con):
    filas = _filas(con, "SELECT * FROM recurso", [])
    guardar(con, "recurso", [r | {"cerrado": r["id"] == "mon:e0"} for r in filas])
    req = Requisitos(dias=2, base="Olite", intereses=["románica"])
    ids = [r["id"] for r in planner.candidatos(con, req, OLITE, planner.similitud_texto(con))]
    assert "mon:e0" not in ids and "mon:e1" in ids


def test_si_el_modelo_falla_el_plan_sale_igual(con):
    req = Requisitos(dias=1, base="Olite")
    fallos = [ValueError("JSON cortado"), ValueError("otra vez")]
    s = _grafo(con, req, fallos).invoke({"peticion": "1 día"})
    plan = s["plan"]
    assert s["intentos"] == 2 and all(
        f"[{p['id']}]" in s["texto"] for p in plan["dias"][0]["paradas"]
    )


def test_respaldar_transporte_y_ritmo():
    from navarra_trip import evaluacion as ev

    for f in ev.leer_csv("peticiones.csv"):  # lo que espera la batería sobrevive
        req = Requisitos(transporte=f["transporte"] or "coche", ritmo=f["ritmo"] or "normal")
        agente._respaldar(req, f["peticion"])
        assert (req.transporte, req.ritmo) == (
            f["transporte"] or "coche",
            f["ritmo"] or "normal",
        ), f["id"]
    req = Requisitos(transporte="pie", ritmo="relajado")
    agente._respaldar(req, "Hiru egun Izaban, mendia eta haranak")
    assert (req.transporte, req.ritmo) == ("coche", "normal")


def test_tarde_llena_hasta_la_hora_de_fin(con):
    c = [r | {"puntos": 1.0} for r in recursos_cerca(con, 42.48, -1.65, 40, limite=50)]
    for ritmo, n in (("relajado", 5), ("intenso", 8)):
        req = Requisitos(dias=1, base="Olite", ritmo=ritmo)
        dia = planner.ordenar(
            OLITE, planner.agrupar(OLITE, c, req, [None], matriz)[0], req, matriz, ruta
        )
        assert len(dia["paradas"]) >= n - 1 and dia["vuelta"] <= planner._hora(planner.FIN[ritmo])


def test_sendero_lleva_la_comida_dentro():
    paradas = [
        {"id": "mon:1", "categoria": "monumento"},
        {"id": "ruta:slna1", "categoria": "ruta", "duracion_min": 240},
    ]
    h = planner._horario(paradas, [10, 20, 30])
    # 10:10-10:55 iglesia; 11:15 empieza el sendero: picnic dentro, sin restaurante
    assert h["comida_tras"] == 1 and paradas[1]["picnic"]
    assert h["vuelta"] == (11 * 60 + 30) + 240 + planner.PICNIC + 30  # llegada 11:20 -> 11:30


def test_senderos_solo_si_se_piden():
    assert planner.quiere_senderos(Requisitos(intereses=["montaña", "valles"]))
    assert planner.quiere_senderos(Requisitos(intereses=["hiking"]))
    assert not planner.quiere_senderos(Requisitos(intereses=["románico", "castillos"]))


def test_ronda_de_pintxos_y_cena(con):
    assert planner.quiere_pintxos(Requisitos(intereses=["pintxos"]))
    assert not planner.quiere_pintxos(Requisitos(intereses=["castillos"]))
    usados = set()
    ronda = planner.ronda(con, OLITE, usados)
    assert {b["id"] for b in ronda} == {"bar:n1", "bar:n2", "bar:n3", "rest:4"}  # sin el suelto
    assert planner.ronda(con, OLITE, usados) == []  # 2.º día: solo queda el suelto
    # rest:4 ya está usado y el resto queda lejos: se repite antes que dejar el día sin cena
    assert planner.cena(con, OLITE, usados)["id"] == "rest:4"
    assert planner.cena(con, OLITE, set())["id"] == "rest:4"


def test_agente_pone_ronda_si_se_piden_pintxos(con):
    req = Requisitos(dias=2, base="Olite", intereses=["pintxos"])
    s = _grafo(con, req, ["x"] * 4).invoke({"peticion": "2 días en Olite de pintxos"})
    d1, d2 = s["plan"]["dias"]
    assert len(d1["ronda"]) == 4 and not d1.get("cena")
    assert d2["cena"]["id"] == "rest:4" and any(
        "ronda de pintxos" in a for a in s["plan"]["avisos"]
    )
    assert "bar:n1" in s["texto"]  # el texto cita los bares


def test_mejor_orden_exacto_y_tope_de_senderos():
    import random

    rnd = random.Random(1)
    m = [[0 if a == b else rnd.randint(1, 60) for b in range(8)] for a in range(8)]
    idx = list(range(1, 8))

    def coste(o):
        return sum(m[a][b] for a, b in zip((0, *o), (*o, 0), strict=True))

    orden, c = planner._mejor_orden(m, idx)
    assert sorted(orden) == idx and c == coste(orden)
    assert c == min(coste(o) for o in itertools.permutations(idx))
    rutas_ = [
        {
            "id": f"ruta:{i}",
            "categoria": "ruta",
            "lat": 42.48,
            "lon": -1.65 + i / 1000,
            "duracion_min": 60,
            "puntos": 1.0,
        }
        for i in range(5)
    ]
    for ritmo, n in (("relajado", 1), ("normal", 2)):
        req = Requisitos(dias=1, base="Olite", ritmo=ritmo)
        assert len(planner.agrupar(OLITE, rutas_, req, [None], matriz)[0]) == n


def test_picnic_solo_en_su_sendero_y_hay_que_citar_bares(con):
    req = Requisitos(dias=1, base="Olite", intereses=["pintxos"])
    s = _grafo(con, req, ["", ""]).invoke({"peticion": "1 día de pintxos"})
    assert s["intentos"] == 2  # sin frases: las pide otra vez y luego sale sin ellas
    assert all(f"[bar:n{i}]" in s["texto"] for i in (1, 2, 3))
    plan = {
        "base": {"nombre": "Isaba"},
        "alojamientos": [],
        "avisos": [],
        "dias": [
            {
                "dia": 1,
                "fecha": None,
                "tiempo": None,
                "vuelta": "18:00",
                "km": 1,
                "comida": None,
                "comida_tras": 1,
                "paradas": [
                    {"id": "mon:1", "llegada": "10:00", "duracion_min": 30},
                    {"id": "ruta:a", "llegada": "11:00", "duracion_min": 240, "picnic": True},
                ],
            }
        ],
    }
    agenda = agente._para_llm(plan)["dias"][0]["agenda"]
    assert [a["tipo"] for a in agenda] == ["parada", "parada", "comida", "vuelta a Isaba"]
    assert agenda[2]["nota"] == agente.PICNIC and "picnic" not in agenda[1]


def test_texto_en_su_idioma_y_sin_horas_inventadas(con):
    req = Requisitos(dias=1, base="Olite", idioma="en")
    s = _grafo(con, req, ["Opens at 09:00.", "Lovely church."]).invoke({"peticion": "1 day"})
    assert s["texto"].startswith("## Day 1") and "09:00" not in s["texto"]
    assert "Lovely church." in s["texto"] and "Dinner:" in s["texto"]
    assert "Where to stay" not in s["texto"]  # un día: sin alojamientos


def _r(id, lat, lon, cat="monumento", **kw):
    return {"id": id, "nombre": id, "categoria": cat, "lat": lat, "lon": lon, "puntos": 1.0} | kw


def test_el_pueblo_se_ve_seguido_y_en_su_dia():
    # base en (42.8, -1.64); a, b en el pueblo (~1-3 min); c y d fuera (~15-20 min)
    base = {"lat": 42.8, "lon": -1.64}
    a, b = _r("mon:a", 42.81, -1.64), _r("mon:b", 42.79, -1.64)
    c, d = _r("mon:c", 42.95, -1.64), _r("mon:d", 42.95, -1.62)
    req = Requisitos(dias=2, base="x")
    grupos = planner.agrupar(base, [a, c, b, d], req, [None, None], matriz)
    assert sorted(sorted(r["id"] for r in g) for g in grupos) == [
        ["mon:a", "mon:b"],
        ["mon:c", "mon:d"],
    ]
    # si van el mismo día, el pueblo de seguido: nunca a -> c -> b
    dia = planner.ordenar(base, [a, c, b], req, matriz, ruta)
    ids_ = [p["id"] for p in dia["paradas"]]
    assert ids_.index("mon:c") in (0, 2) and not dia["en_la_base"]
    assert planner.ordenar(base, [a, b], req, matriz, ruta)["en_la_base"]


def test_monte_con_tope_de_esfuerzo():
    base = {"lat": 42.9, "lon": -0.8}
    mesa = _r(
        "esp:7350", 42.91, -0.8, "natural", subcategorias=["Montes y sierras"], duracion_min=360
    )
    corto = _r("ruta:slna1", 42.91, -0.79, "ruta", duracion_min=60)
    for ritmo, n in (("normal", 1), ("intenso", 2)):
        req = Requisitos(dias=1, base="x", ritmo=ritmo)
        assert len(planner.agrupar(base, [mesa, corto], req, [None], matriz)[0]) == n
    paradas = [dict(mesa)]
    planner._horario(paradas, [10, 10])
    assert paradas[0]["picnic"]  # la Mesa pasa por las 13:30: comida en el monte


def test_comida_antes_de_una_visita_larga_que_empieza_a_mediodia():
    paradas = [_r("mon:1", 0, 0, duracion_min=160), _r("esp:2", 0, 0, "natural", duracion_min=120)]
    h = planner._horario(paradas, [5, 5, 5])  # 10:15-13:00; el valle acabaría a las 15:15
    assert h["comida_tras"] == 0 and paradas[0]["comida_despues"] == "13:00"


def test_restaurante_sin_comida_rapida_si_hay_otro():
    filas = [
        {"id": "rest:bk", "especialidad": "Rápida"},
        {"id": "rest:2", "especialidad": "Asador"},
    ]
    usados = set()
    assert planner._elegir(filas, usados)["id"] == "rest:2"
    assert planner._elegir(filas, usados)["id"] == "rest:bk"  # si no queda otro
    assert planner._elegir(filas, usados)["id"] == "rest:bk"  # y luego repite


def test_el_picnic_cuenta_en_la_hora_de_fin():
    p = {"id": "esp:7350", "nombre": "Mesa", "municipio": "Isaba", "llegada": "10:23"}
    dia = {
        "dia": 1,
        "fecha": None,
        "paradas": [p | {"duracion_min": 360, "picnic": True}],
        "comida": None,
        "comida_tras": 0,
        "vuelta": "17:00",
        "en_la_base": False,
    }
    plan = {
        "requisitos": {"idioma": "es"},
        "base": {"nombre": "Isaba"},
        "dias": [dia],
        "alojamientos": [],
    }
    assert "**10:23–16:53** **Mesa**" in agente.componer(plan, {}, [])


def test_horas_en_cuartos_y_visitas_alargadas_si_sobra_tarde():
    paradas = [_r("mon:1", 0, 0, duracion_min=40), _r("esp:2", 0, 0, "natural", duracion_min=60)]
    h = planner._horario(paradas, [7, 8, 9])
    assert [p["llegada"] for p in paradas] == ["10:15", "11:15"] and h["vuelta"] % 15 == 0
    h = planner._alargar(paradas, [7, 8, 9], h, planner.FIN["normal"])
    # monumento hasta +50 % (45 -> 75), espacio natural hasta el doble (60 -> 120)
    assert [p["duracion_min"] for p in paradas] == [75, 120]
    assert h["vuelta"] <= planner.FIN["normal"]


def test_restaurante_pedido_por_nombre(con):
    pedidos = planner.restaurantes_pedidos(con, OLITE, "Un día en Olite y cenar en el rest 4")
    assert pedidos == {"rest:4"}
    filas = [{"id": "rest:1", "especialidad": "Asador"}, {"id": "rest:4", "especialidad": "Rápida"}]
    assert planner._elegir(filas, set(), pedidos)["id"] == "rest:4"


def test_foces_se_amplian_y_cada_interes_abre_un_dia():
    assert "cañones" in planner.ampliar("foces") and planner.ampliar("castillos") == "castillos"
    base = {"lat": 42.8, "lon": -1.64}
    # el interés 1 (foces) puntúa peor y queda lejos del grupo del 0: igualmente abre el día 2
    a = _r("mon:a", 42.85, -1.64, interes=0, puntos=0.9)
    b = _r("mon:b", 42.86, -1.64, interes=0, puntos=0.9)
    foz = _r("esp:foz", 42.80, -1.30, "natural", interes=1, puntos=0.5)
    req = Requisitos(dias=2, base="x")
    grupos = planner.agrupar(base, [a, b, foz], req, [None, None], matriz)
    assert [sorted(r["id"] for r in g) for g in grupos] == [["mon:a", "mon:b"], ["esp:foz"]]


def test_opciones_de_lugares_sin_itinerario(con):
    req = Requisitos(tipo="opciones", base="Olite", intereses=["románica"])
    s = _grafo(con, req, ["Bonita."]).invoke(
        {"peticion": "Recomiéndame iglesias románicas en Olite"}
    )
    plan = s["plan"]
    assert plan["dias"] == [] and 0 < len(plan["opciones"]) <= planner.N_OPCIONES
    assert all("románica" in o["nombre"] for o in plan["opciones"])
    assert "Opciones cerca de Olite" in s["texto"] and "duración: 45 min" in s["texto"]
    assert "Bonita." in s["texto"] and "Vuelta" not in s["texto"]
    assert {o["id"] for o in plan["opciones"]} <= planner.ids(plan)


def test_dime_bares_da_opciones_sin_preguntar_dias(con):
    req = Requisitos(base="Olite", intereses=["pintxos"])  # el LLM dice plan y sin días
    s = _grafo(con, req, []).invoke({"peticion": "Dime bares de pintxos en Olite"})
    assert "pregunta" not in s
    ids = [o["id"] for o in s["plan"]["opciones"]]
    assert ids[0] == "rest:4" and {"bar:n1", "bar:n4"} <= set(ids)  # rest:4: de tapas, al lado
    assert "**Bar 1** [bar:n1], Olite · a 110 m" in s["texto"]  # en metros, no "a 0 km"


def test_opciones_sin_los_quitados_al_ajustar(con):
    req = Requisitos(base="Olite", intereses=["pintxos"])
    s = _grafo(con, req, []).invoke({"peticion": "Dime bares en Olite", "excluir": ["rest:4"]})
    assert "rest:4" not in {o["id"] for o in s["plan"]["opciones"]}


def test_duplicados_se_queda_la_visita_mas_larga():
    mirador = _rec("esp:m", "Miradores de las Bardenas", "natural", 42.2, -1.5) | {
        "duracion_min": 20
    }
    parque = _rec("esp:p", "Parque de las Bardenas", "natural", 42.2, -1.5) | {"duracion_min": 150}
    otro = _rec("mon:x", "Castillo", "monumento", 42.3, -1.6)
    ids = [r["id"] for r in planner._sin_duplicados([mirador, otro, parque])]
    assert sorted(ids) == ["esp:p", "mon:x"]
