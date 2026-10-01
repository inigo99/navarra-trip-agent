"""Planificador determinista: elige, agrupa por días y ordena las paradas.

El LLM solo interpreta la petición y redacta el resultado (agente.py); todo lo de aquí es código
reproducible y evaluable. Viaje con base fija: cada día sale de la base y vuelve a ella.
"""

import itertools
import math
from collections.abc import Callable
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from navarra_trip.tools import consultas, rutas, tiempo

PARADAS = {"relajado": 3, "normal": 4, "intenso": 6}
MAX_TRAYECTO = {"relajado": 120, "normal": 180, "intenso": 270}  # minutos al día
RADIO_KM = {"coche": 30, "pie": 4}  # con 40, Estella sumaba Lekunberri: días en zigzag
RADIO_COMIDA_KM = {"coche": 10, "pie": 1.5}
DURACION = {"monumento": 45, "natural": 90}  # ponytail: por categoría; afinar por subcategoría
INICIO, FIN = 10 * 60, 19 * 60
HORA_COMIDA, COMIDA, COMIDA_TOPE = 13 * 60 + 30, 90, 14 * 60 + 30
DIAS_PREVISION = 16  # Open-Meteo


class Requisitos(BaseModel):
    """Lo que el LLM extrae de la petición. Lo que no se diga queda vacío: no se inventa."""

    # 0 y "" en vez de null: con null en el esquema, qwen2.5:7b devolvía null aunque lo dijera
    dias: int = Field(0, ge=0, le=7, description="Número de días del viaje; 0 si no lo dice")
    base: str = Field(
        "",
        description="Pueblo o ciudad desde el que se hacen las excursiones ('3 días en Estella' "
        "-> 'Estella', 'desde Ochagavía' -> 'Ochagavía'); vacío si no nombra ninguno",
    )
    transporte: Literal["coche", "pie"] = "coche"
    intereses: list[str] = Field(
        default_factory=list,
        description="Temas en pocas palabras, p. ej. ['románico', 'cascadas', 'castillos']",
    )
    ritmo: Literal["relajado", "normal", "intenso"] = "normal"
    idioma: Literal["es", "en", "fr"] = Field("es", description="Idioma en que se escribe")
    fecha_inicio: date | None = Field(None, description="Primer día del viaje, si lo dice")
    alojamiento: Literal["hotel", "rural", "apartamento", "albergue", "camping"] | None = None


Similitud = Callable[[str], dict[str, float]]  # interés -> {id de recurso: puntuación}


def lugar(con, nombre: str) -> dict | None:
    """Coordenadas de un pueblo: mediana de sus alojamientos (o de sus recursos). 'Estella'
    encuentra 'Estella-Lizarra'; ante varios, el nombre exacto y luego el que más tiene."""
    x = consultas._sin_tildes(nombre.strip())
    sql = """SELECT {col} AS nombre, median(lat) AS lat, median(lon) AS lon FROM {tabla}
      WHERE lat IS NOT NULL AND strip_accents(lower({col})) LIKE '%' || ? || '%'
      GROUP BY {col} ORDER BY strip_accents(lower({col})) = ? DESC, count(*) DESC LIMIT 1"""
    for tabla, col in (("alojamiento", "localidad"), ("recurso", "municipio")):
        if f := consultas._filas(con, sql.format(tabla=tabla, col=col), [x, x]):
            return f[0]
    return None


def similitud_texto(con) -> Similitud:
    """Sin índice semántico: 1 si están todas las palabras del interés, 0,5 si alguna. Se
    compara la raíz (6 letras): 'naturaleza' ~ 'natural', 'cascadas' ~ 'cascada'."""
    filas = consultas._filas(
        con, "SELECT id, nombre, categoria, subcategorias, estilo, descripcion FROM recurso", []
    )
    textos = {
        f["id"]: consultas._sin_tildes(
            " ".join(
                [f["nombre"], f["categoria"], *f["subcategorias"], f["estilo"] or ""]
                + [f["descripcion"] or ""]
            )
        )
        for f in filas
    }

    def sim(interes: str) -> dict[str, float]:
        palabras = [p[:6] for p in consultas._sin_tildes(interes).split() if len(p) > 2]
        out = {}
        for id, t in textos.items():
            n = sum(p in t for p in palabras)
            out[id] = 1.0 if n == len(palabras) else 0.5 if n else 0.0
        return out

    return sim


def similitud_por_defecto(con) -> Similitud:
    try:
        from navarra_trip.tools.semantica import buscar_semantica

        buscar_semantica(con, "prueba", k=1)  # falla aquí si no hay extra o índice
        return lambda i: {r["id"]: r["similitud"] for r in buscar_semantica(con, i, k=500)}
    except (ImportError, ValueError, FileNotFoundError):
        return similitud_texto(con)


def candidatos(con, req: Requisitos, base: dict, sim: Similitud) -> list[dict]:
    """Recursos alrededor de la base, alternando el ranking de cada interés para que no se los
    lleve todos el primero. Si no llegan, se completa con los más destacados (los que tienen
    artículo en Wikipedia) y cercanos."""
    cerca = consultas.recursos_cerca(
        con, base["lat"], base["lon"], RADIO_KM[req.transporte], limite=1000
    )
    n = (req.dias or 1) * PARADAS[req.ritmo] * 2
    destacados = sorted(cerca, key=lambda r: (r["descripcion_fuente"] != "wikipedia", r["km"]))
    por_id = {r["id"]: r for r in cerca}
    rankings = []
    for interes in req.intereses:
        s = sim(interes)
        rankings.append(sorted(((s.get(i, 0.0), i) for i in por_id), reverse=True))
    out, vistos = [], set()
    for fila in itertools.zip_longest(*rankings):
        for puntos, id in filter(None, fila):
            if id not in vistos and puntos > 0 and len(out) < n:
                vistos.add(id)
                out.append(por_id[id] | {"puntos": puntos})
    relleno = (r | {"puntos": 0.01} for r in destacados if r["id"] not in vistos)
    return out + list(itertools.islice(relleno, n - len(out)))


def prevision_viaje(
    base: dict, req: Requisitos, hoy: date, prevision=tiempo.prevision
) -> tuple[list[dict | None], str | None]:
    """Un dict de Open-Meteo por día (None si no hay fecha o cae fuera de los 16 días)."""
    dias = req.dias or 1
    if not req.fecha_inicio:
        return [None] * dias, None
    desde = (req.fecha_inicio - hoy).days
    if desde < 0 or desde + dias > DIAS_PREVISION:
        return [None] * dias, "Sin previsión del tiempo: las fechas caen fuera de los 16 días."
    return prevision(base["lat"], base["lon"], desde + dias)[desde:], None


DESVIO_MAX = 25  # min que una parada puede alargar la vuelta del día (con 45, Estella juntaba
# Torres del Río y Puente la Reina, a lados opuestos de la base)


def agrupar(
    base: dict,
    cands: list[dict],
    req: Requisitos,
    prevision_dias: list[dict | None],
    matriz=rutas.matriz,
) -> list[list[dict]]:
    """Un día por vuelta desde la base, con tiempos reales (OSRM): la semilla es el mejor
    candidato sin usar que quepa, y se añaden los que menos alargan la vuelta (hasta DESVIO_MAX
    y el tope de trayecto). Con k-means en línea recta, en el Pirineo juntaba Irati, la Cascada
    del Cubo y Larra (tres valles distintos) y el día se quedaba en nada. Los días de mal tiempo
    tiran primero de monumentos."""
    paradas, tope = PARADAS[req.ritmo], MAX_TRAYECTO[req.ritmo]
    cands = sorted(cands, key=lambda r: -r["puntos"])
    if not cands:
        return [[] for _ in prevision_dias]
    m = matriz([(base["lat"], base["lon"])] + [(r["lat"], r["lon"]) for r in cands], req.transporte)
    libres = list(range(1, len(cands) + 1))  # índices en m (0 = base)
    grupos = []
    for p in prevision_dias:
        techo = [i for i in libres if cands[i - 1]["categoria"] == "monumento"]
        for pool in [techo, libres] if p and p["mal_tiempo"] else [libres]:
            dia = next(([i] for i in pool if _mejor_orden(m, [i])[1] <= tope), [])
            while dia and len(dia) < paradas:
                coste = _mejor_orden(m, dia)[1]
                # ponytail: fuerza bruta por opción; con 7 días intensos tarda unos segundos
                opciones = [
                    (_mejor_orden(m, [*dia, j])[1] - coste, -cands[j - 1]["puntos"], j)
                    for j in pool
                    if j not in dia
                ]
                opciones = [o for o in opciones if o[0] <= DESVIO_MAX and coste + o[0] <= tope]
                if not opciones:
                    break
                dia.append(min(opciones)[2])
            if dia:
                break
        libres = [i for i in libres if i not in dia]
        grupos.append([cands[i - 1] for i in dia])
    return grupos


def _mejor_orden(m: list[list[float | None]], idx: list[int]) -> tuple[list[int], float]:
    """Ruta circular base -> paradas -> base más corta. ponytail: fuerza bruta, como mucho
    6! = 720 órdenes; con más paradas por día, vecino más cercano + 2-opt."""

    def coste(orden):
        pasos = zip((0, *orden), (*orden, 0), strict=True)
        return sum(math.inf if m[a][b] is None else m[a][b] for a, b in pasos)

    mejor = min(itertools.permutations(idx), key=coste, default=())
    return list(mejor), coste(mejor)


def _horario(paradas: list[dict], tramos: list[float]) -> dict:
    """tramos: minutos base->1.ª, ..., última->base. La comida va tras la parada que acaba
    pasadas las 13:30, o ya pasadas las 13:00 si la siguiente acabaría después de las 14:30 (si
    no, se comía a las 15:00); si el día acaba antes, a las 13:30 tras la última."""
    t, comida_tras = INICIO, None
    for r in paradas:
        r.pop("comida_despues", None)  # de un intento anterior con otras paradas
    for i, (r, tramo) in enumerate(zip(paradas, tramos, strict=False)):
        fin = t + tramo + DURACION[r["categoria"]]
        if comida_tras is None and i and t >= 13 * 60 and fin > COMIDA_TOPE:
            comida_tras, paradas[i - 1]["comida_despues"] = i - 1, _hora(t)
            t += COMIDA
        t += tramo
        r["llegada"], r["duracion_min"], r["trayecto_min"] = (
            _hora(t),
            DURACION[r["categoria"]],
            round(tramo),
        )
        t += DURACION[r["categoria"]]
        if comida_tras is None and t >= HORA_COMIDA:
            comida_tras, r["comida_despues"] = i, _hora(t)
            t += COMIDA
    if comida_tras is None and paradas:  # el día acaba antes: se come a las 13:30
        comida_tras, t = len(paradas) - 1, max(t, HORA_COMIDA)
        paradas[-1]["comida_despues"] = _hora(t)
        t += COMIDA
    return {"comida_tras": comida_tras, "vuelta": t + (tramos[-1] if tramos else 0)}


def _hora(minutos: float) -> str:
    return f"{int(minutos) // 60:02d}:{int(minutos) % 60:02d}"


def ordenar(
    base: dict, grupo: list[dict], req: Requisitos, matriz=rutas.matriz, ruta=rutas.ruta
) -> dict:
    """Orden óptimo del día; si se pasa del tope de conducción o vuelve después de las 19:00,
    quita la parada que más tiempo ahorra por punto de interés y lo intenta otra vez (quitar
    solo por puntos dejaba Quinto Real, a 1 h, y quitaba Irati y la Cascada del Cubo)."""
    paradas = [dict(r) for r in grupo]
    if not paradas:
        return {"paradas": [], "km": 0, "trayecto_min": 0, "descartadas": []}
    pts = [(base["lat"], base["lon"])] + [(r["lat"], r["lon"]) for r in paradas]
    m = matriz(pts, req.transporte)
    idx = list(range(1, len(pts)))
    descartadas = []  # para depurar y evaluar: qué se quitó y por qué
    while idx:
        orden, minutos = _mejor_orden(m, idx)
        dia = [paradas[i - 1] for i in orden]
        tramos = [m[a][b] for a, b in zip((0, *orden), (*orden, 0), strict=True)]
        h = _horario(dia, tramos)
        if minutos <= MAX_TRAYECTO[req.ritmo] and h["vuelta"] <= FIN:
            break
        quitar = max(
            idx,
            key=lambda i: (
                (minutos - _mejor_orden(m, [j for j in idx if j != i])[1])
                / max(paradas[i - 1]["puntos"], 0.01)
            ),
        )
        idx.remove(quitar)
        descartadas.append(
            {
                "id": paradas[quitar - 1]["id"],
                "nombre": paradas[quitar - 1]["nombre"],
                "base_ida_vuelta_min": _suma(m[0][quitar], m[quitar][0]),
                "motivo": f"{_suma(minutos)} min de trayecto (tope {MAX_TRAYECTO[req.ritmo]})"
                if minutos > MAX_TRAYECTO[req.ritmo]
                else f"vuelta a las {_hora(h['vuelta'])}",
            }
        )
    else:
        return {"paradas": [], "km": 0, "trayecto_min": 0, "descartadas": descartadas}
    r = ruta([pts[0], *((p["lat"], p["lon"]) for p in dia), pts[0]], req.transporte, True)
    return {
        "paradas": dia,
        "comida_tras": h["comida_tras"],
        "vuelta": _hora(h["vuelta"]),
        "km": r["km"],
        "trayecto_min": r["minutos"],
        "geometria": r.get("geometria"),
        "descartadas": descartadas,
    }


def _suma(*minutos) -> float | None:
    """None (sin camino por carretera) o inf -> None, para que el JSON lo admita."""
    t = sum(math.inf if x is None else x for x in minutos)
    return None if math.isinf(t) else round(t)


def comida(con, parada: dict, modo: str, usados: set[str]) -> dict | None:
    """Restaurante más cercano a la parada de antes de comer, sin repetir entre días."""
    for r in consultas.restaurantes_cerca(
        con, parada["lat"], parada["lon"], RADIO_COMIDA_KM[modo], limite=20
    ):
        if r["id"] not in usados:
            usados.add(r["id"])
            return r
    return None


def alojamientos(con, base: dict, req: Requisitos, n: int = 3) -> list[dict]:
    """Opciones cerca de la base; primero las de dirección exacta."""
    filas = consultas.alojamientos_cerca(
        con, base["lat"], base["lon"], 3, tipo=req.alojamiento, limite=50
    )
    filas.sort(key=lambda a: (a["geo_precision"] != "direccion", a["km"]))
    return filas[:n]


def geojson(plan: dict) -> dict:
    """FeatureCollection para el mapa: base, paradas, comidas, alojamientos y rutas."""

    def punto(r, **props):
        return {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
            "properties": {"id": r.get("id"), "nombre": r["nombre"], **props},
        }

    fs = [punto(plan["base"], tipo="base")]
    fs += [punto(a, tipo="alojamiento") for a in plan["alojamientos"]]
    for d in plan["dias"]:
        fs += [
            punto(p, tipo="parada", dia=d["dia"], orden=i + 1) for i, p in enumerate(d["paradas"])
        ]
        if d.get("comida"):
            fs.append(punto(d["comida"], tipo="comida", dia=d["dia"]))
        if d.get("geometria"):
            fs.append(
                {"type": "Feature", "geometry": d["geometria"], "properties": {"dia": d["dia"]}}
            )
    return {"type": "FeatureCollection", "features": fs}


def ids(plan: dict) -> set[str]:
    """Todo lo que el texto puede citar."""
    out = {a["id"] for a in plan["alojamientos"]}
    for d in plan["dias"]:
        out |= {p["id"] for p in d["paradas"]}
        if d.get("comida"):
            out.add(d["comida"]["id"])
    return out
