"""Planificador determinista: elige, agrupa por días y ordena las paradas.

El LLM solo interpreta la petición y redacta el resultado (agente.py); todo lo de aquí es código
reproducible y evaluable. Viaje con base fija: cada día sale de la base y vuelve a ella.
"""

import difflib
import itertools
import math
import re
from collections.abc import Callable
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from navarra_trip.ingest.geocode import km
from navarra_trip.tools import consultas, rutas, tiempo

# tope; lo que manda es la hora de fin. Con 7, un día por Pamplona (iglesias de 30 min a 3 min
# unas de otras) acababa a las 13:30: las paradas se agotaban antes que el día
PARADAS = {"relajado": 6, "normal": 9, "intenso": 10}
# Monte: senderos y "Montes y sierras" (la Mesa de los Tres Reyes es una mañana entera). Tope
# de caminatas y de minutos andando al día: en Isaba juntaba Artikomendia, la Mesa y Dronda
MAX_RUTAS = {"relajado": 1, "normal": 2, "intenso": 2}
ESFUERZO_MIN = {"relajado": 240, "normal": 360, "intenso": 480}
# Paradas a menos de esto de la base son "del pueblo": se ven seguidas, sin salir y volver
# (Pamplona -> acueducto de Noáin -> Pamplona) ni repartidas entre días de excursión
# en línea recta: en coche, el casco viejo de Pamplona queda a más de 8 min de la base (calles
# peatonales y de sentido único) y no contaba como pueblo
EN_BASE_KM = 2.5
IDA_Y_VUELTA = 30  # penalización (solo para ordenar) por cada paso pueblo <-> fuera
MAX_DIAS = 7
MAX_TRAYECTO = {"relajado": 120, "normal": 180, "intenso": 270}  # minutos al día
RADIO_KM = {"coche": 30, "pie": 4}  # con 40, Estella sumaba Lekunberri: días en zigzag
RADIO_COMIDA_KM = {"coche": 10, "pie": 1.5}
# si el recurso no trae duracion_min
DURACION = {"monumento": 45, "natural": 90, "ruta": 180, "bodega": 90}
INICIO = 10 * 60
# Hasta qué hora se llena el día: con un n.º fijo de paradas, muchos días volvían a las 15:00
FIN = {"relajado": 17 * 60 + 30, "normal": 19 * 60, "intenso": 20 * 60}
PICNIC = 30  # comida en ruta: si un sendero pasa por la hora de comer, no hay restaurante
# lo que se pide para que entren senderos en el plan (raíces; 'Solo si se pide')
SENDERISMO = (
    "sender",
    "ruta",
    "monte",
    "montan",
    "cima",
    "cumbre",
    "trekk",
    "hik",
    "randon",
    "excursi",
    "mendi",
    "mountain",
    "peak",
    "summit",
    "montagne",
    "sommet",
    "walk",
    "caminar",
)
HORA_COMIDA, COMIDA, COMIDA_TOPE = 13 * 60 + 30, 90, 14 * 60 + 30
DIAS_PREVISION = 16  # Open-Meteo


class Requisitos(BaseModel):
    """Lo que el LLM extrae de la petición. Lo que no se diga queda vacío: no se inventa."""

    # 0 y "" en vez de null: con null en el esquema, qwen2.5:7b devolvía null aunque lo dijera
    # sin tope en el esquema: con le=7, "10 días" hacía fallar la validación; el agente lo recorta
    dias: int = Field(0, ge=0, description="Número de días del viaje; 0 si no lo dice")
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
    idioma: Literal["es", "en", "fr", "eu"] = Field("es", description="Idioma en que se escribe")
    fecha_inicio: date | None = Field(None, description="Primer día del viaje, si lo dice")
    alojamiento: Literal["hotel", "rural", "apartamento", "albergue", "camping"] | None = None


Similitud = Callable[[str], dict[str, float]]  # interés -> {id de recurso: puntuación}


ALIAS = {"pampelune": "pamplona", "pampeluna": "pamplona", "irunea": "pamplona", "tutera": "tudela"}


def lugar(con, nombre: str) -> dict | None:
    """Coordenadas de un pueblo: mediana de sus alojamientos (o de sus recursos). 'Estella'
    encuentra 'Estella-Lizarra'; ante varios, el nombre exacto y luego el que más tiene. Si no
    aparece, el nombre más parecido: 'Sangúsa' (el LLM estropea la ü), 'Tuteratik' (euskera)."""
    x = consultas._sin_tildes(nombre.strip())
    sql = """SELECT {col} AS nombre, median(lat) AS lat, median(lon) AS lon FROM {tabla}
      WHERE lat IS NOT NULL AND strip_accents(lower({col})) LIKE '%' || ? || '%'
      GROUP BY {col} ORDER BY strip_accents(lower({col})) = ? DESC, count(*) DESC LIMIT 1"""
    for y in (ALIAS.get(x, x), _parecido(con, x)):
        for tabla, col in (("alojamiento", "localidad"), ("recurso", "municipio")):
            if y and (f := consultas._filas(con, sql.format(tabla=tabla, col=col), [y, y])):
                return f[0]
    return None


def _parecido(con, x: str) -> str | None:
    """Nombre más parecido; 'Pamplona / Iruña' cuenta como 'pamplona' e 'iruna'."""
    nombres = consultas._filas(
        con,
        "SELECT DISTINCT localidad AS n FROM alojamiento UNION SELECT municipio FROM recurso",
        [],
    )
    partes = {
        p.strip()
        for f in nombres
        if f["n"]
        for p in re.split(r"[/-]", consultas._sin_tildes(f["n"]))
    }
    x = re.sub(r"(tik|n)$", "", x)  # euskera: Tuteratik, Lizarran
    # con 0,8, 'Vitoria' daba 'Viloria' y 'Biarritz' 'Ciáurriz'
    m = difflib.get_close_matches(x, partes | set(ALIAS), n=1, cutoff=0.87)
    return m and ALIAS.get(m[0], m[0]) or None


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


def candidatos(
    con, req: Requisitos, base: dict, sim: Similitud, excluir: frozenset = frozenset()
) -> list[dict]:
    """Recursos alrededor de la base, alternando el ranking de cada interés para que no se los
    lleve todos el primero. Si no llegan, se completa con los más destacados (los que tienen
    artículo en Wikipedia) y cercanos."""
    cerca = consultas.recursos_cerca(
        con, base["lat"], base["lon"], RADIO_KM[req.transporte], limite=1000
    )
    # cerrados temporalmente y los que el usuario quitó al ajustar el plan
    cerca = [r for r in cerca if not r.get("cerrado") and r["id"] not in excluir]
    if not quiere_senderos(req):
        cerca = [r for r in cerca if r["categoria"] != "ruta"]
    if not quiere_vino(req):  # OSM no dice si se visitan: solo si se piden
        cerca = [r for r in cerca if r["categoria"] != "bodega"]
    n = (req.dias or 1) * PARADAS[req.ritmo] * 3  # con *2 se agotaban y sobraba tarde
    destacados = sorted(cerca, key=lambda r: (r["descripcion_fuente"] != "wikipedia", r["km"]))
    por_id = {r["id"]: r for r in cerca}
    rankings = []
    for interes in req.intereses:
        s = sim(ampliar(interes))
        rankings.append(sorted(((s.get(i, 0.0), i) for i in por_id), reverse=True))
    out, vistos = [], set()

    def nuevo(r: dict) -> bool:
        """Sin duplicados: 'Foz de Arbaiun' y 'Mirador de la Foz de Arbaiun' comparten Wikidata;
        'Molino' y 'Monasterio de Urdax', coordenadas."""
        claves = {r["id"], (round(r["lat"], 4), round(r["lon"], 4))}
        if r.get("wikidata_id"):
            claves.add(r["wikidata_id"])
        if claves & vistos:
            return False
        vistos.update(claves)
        return True

    for fila in itertools.zip_longest(*rankings):
        for k, par in enumerate(fila):
            if par and par[0] > 0 and len(out) < n and nuevo(por_id[par[1]]):
                out.append(por_id[par[1]] | {"puntos": par[0], "interes": k})
    relleno = (r | {"puntos": 0.01} for r in destacados if nuevo(r))
    return out + list(itertools.islice(relleno, n - len(out)))


# El modelo de embeddings no sabe qué es una foz: con "foces", la Foz de Lumbier no salía entre
# las 5 primeras y el Palacio de los Mencos sí. Términos locales -> palabras que sí conoce.
SINONIMOS = {
    "foz": "foces cañones gargantas desfiladeros",
    "foces": "foces cañones gargantas desfiladeros",
    "arroila": "foces cañones gargantas desfiladeros",
    "nacedero": "nacederos manantiales ríos",
    "txoko": "pueblos con encanto",
}


def ampliar(interes: str) -> str:
    t = consultas._sin_tildes(interes)
    extra = [v for k, v in SINONIMOS.items() if re.search(rf"\b{k}", t)]
    return " ".join([interes, *extra])


def intereses_lejanos(con, req: Requisitos, base: dict, sim: Similitud, k: int = 15) -> list[str]:
    """Intereses sin nada en el radio a la altura de sus k recursos más parecidos (el Pirineo a
    pie desde Tudela) o sin nada en absoluto (playa): el plan se rellena con otra cosa."""
    radio = RADIO_KM[req.transporte]
    cerca = {
        r["id"] for r in consultas.recursos_cerca(con, base["lat"], base["lon"], radio, limite=1000)
    }
    out = []
    for i in req.intereses:
        if any(p in consultas._sin_tildes(i) for p in PINTXOS):
            continue  # los bares no son recursos: de eso avisa la ronda
        s = sim(ampliar(i))
        umbral = sorted(s.values(), reverse=True)[:k][-1:] or [0]  # con empates cuenta cualquiera
        if umbral[0] <= 0 or max((s.get(id, 0) for id in cerca), default=0) < umbral[0]:
            out.append(i)
    return out


HORA_CENA, FIN_CENA, HORA_RONDA = "21:00", "22:30", "20:30"
RADIO_CENA_KM, RADIO_CENA_MAX_KM = 2, 8  # la cena, en la base o cerca: se vuelve al acabar
PINTXOS = ("pintx", "tapa", "poteo", "txikit", "gastronom", "bares", "bar de")
RONDA, RONDA_MIN, RONDA_M = 4, 3, 300  # bares por ronda, mínimo, distancia entre ellos


def quiere_pintxos(req: Requisitos) -> bool:
    t = consultas._sin_tildes(" ".join(req.intereses))
    return any(p in t for p in PINTXOS)


def _elegir(filas: list[dict], usados: set[str], pedidos=frozenset()) -> dict | None:
    """El más cercano sin repetir; antes, el que pida la petición ("el Burger King"); y si no,
    que no sea comida rápida (salía Burger King para comer en Pamplona sin pedirlo). Si no
    queda otro, repite antes que dejar el día sin comida o cena."""
    for ok in (
        lambda r: r["id"] not in usados and r["id"] in pedidos,
        lambda r: r["id"] not in usados and r["especialidad"] != "Rápida",
        lambda r: r["id"] not in usados,
        lambda r: True,
    ):
        if r := next((r for r in filas if ok(r)), None):
            usados.add(r["id"])
            return r
    return None


RAPIDA = ("burger", "hamburgues", "comida rapida", "fast food", "mcdonald", "kebab", "pizza")


def restaurantes_pedidos(con, base: dict, peticion: str) -> set[str]:
    """Restaurantes que la petición nombra (o comida rápida, si la pide), a menos de 30 km."""
    t = consultas._sin_tildes(peticion)
    filas = consultas.restaurantes_cerca(con, base["lat"], base["lon"], 30, 5000)
    rapida = any(p in t for p in RAPIDA)

    def nombrado(r):
        n = consultas._sin_tildes(r["nombre"]).strip()
        return len(n) >= 4 and re.search(rf"\b{re.escape(n)}\b", t)

    nombrados = {r["id"] for r in filas if nombrado(r)}
    # "comer en el Burger King": ese, no la comida rápida más cercana (salía Domino's)
    return nombrados or {r["id"] for r in filas if rapida and r["especialidad"] == "Rápida"}


def cena(con, base: dict, usados: set[str], pedidos=frozenset()) -> dict | None:
    """Restaurante cerca de la base; en Isaba no quedaban sin repetir a 2 km y los días 2 y 3
    se quedaban sin cena: se amplía a RADIO_CENA_MAX_KM y, si hace falta, se repite."""
    filas = consultas.restaurantes_cerca(con, base["lat"], base["lon"], RADIO_CENA_KM, 50)
    if all(r["id"] in usados for r in filas):
        filas += consultas.restaurantes_cerca(con, base["lat"], base["lon"], RADIO_CENA_MAX_KM, 50)
    r = _elegir(filas, usados, pedidos)
    return r and r | {"hora": HORA_CENA, "fin": FIN_CENA}


def ronda(con, base: dict, usados: set[str]) -> list[dict]:
    """3-4 bares a menos de RONDA_M unos de otros, en la zona con más bares juntos (el casco
    viejo) y en orden de paseo. [] si no hay bastantes: se cena en un restaurante."""
    bares = [b for b in consultas.bares_cerca(con, base["lat"], base["lon"], 1, 300)]
    bares = [b for b in bares if b["id"] not in usados]

    def d(a, b):
        return km(a["lon"], a["lat"], b["lon"], b["lat"])

    def vecinos(b):
        return [o for o in bares if o is not b and d(b, o) * 1000 <= RONDA_M]

    if not bares:
        return []
    grupo = [max(bares, key=lambda b: len(vecinos(b)))]
    resto = vecinos(grupo[0])
    while resto and len(grupo) < RONDA:  # vecino más cercano: un paseo sin vueltas
        grupo.append(min(resto, key=lambda o: d(grupo[-1], o)))
        resto.remove(grupo[-1])
    if len(grupo) < RONDA_MIN:
        return []
    usados.update(b["id"] for b in grupo)
    return grupo


VINO = ("vin", "bodeg", "enotur", "wine", "ardo", "upategi")  # es, en, fr, eu (ardoa)


def quiere_vino(req: Requisitos) -> bool:
    palabras = consultas._sin_tildes(" ".join(req.intereses)).split()
    return any(w.startswith(VINO) for w in palabras)


def quiere_senderos(req: Requisitos) -> bool:
    t = consultas._sin_tildes(" ".join(req.intereses))
    return any(p in t for p in SENDERISMO)


def duracion(r: dict) -> int:
    return r.get("duracion_min") or DURACION[r["categoria"]]


def caminata(r: dict) -> bool:
    """Senderos y subidas a cimas (Mesa de los Tres Reyes, Ori: duración en el CSV manual). Urbasa
    o Aralar, también "Montes y sierras", se ven en coche en un rato: no cuentan."""
    montana = "Montes y sierras" in (r.get("subcategorias") or [])
    return r["categoria"] == "ruta" or (montana and duracion(r) >= 180)


def _en_base(pts: list[tuple[float, float]], modo: str) -> set[int]:
    """Índices de pts (0 = base) en el pueblo base. A pie todo lo es: no hay excursiones."""
    if modo == "pie":
        return set(range(len(pts)))
    (la, lo) = pts[0]
    return {i for i, (lat, lon) in enumerate(pts) if km(lo, la, lon, lat) <= EN_BASE_KM}


def _sin_idas_y_vueltas(m: list[list[float | None]], en_base: set[int]) -> list[list[float]]:
    """Matriz para ORDENAR (no para medir): cada paso entre una parada del pueblo y una de fuera
    cuesta IDA_Y_VUELTA más (salir y entrar de la base no cuenta). Ver el pueblo de seguido, al
    principio o al volver, son 1 paso; pueblo -> fuera -> pueblo son 2."""

    def frontera(a, b):
        return a and b and (a in en_base) != (b in en_base)

    return [
        [_w(m, a, b) + (IDA_Y_VUELTA if frontera(a, b) else 0) for b in range(len(m))]
        for a in range(len(m))
    ]


def prevision_viaje(
    base: dict, req: Requisitos, hoy: date, prevision=tiempo.prevision
) -> tuple[list[dict | None], str | None]:
    """Un dict de Open-Meteo por día (None si no hay fecha o cae fuera de los 16 días)."""
    dias = req.dias or 1
    if not req.fecha_inicio:
        return [None] * dias, None
    desde = (req.fecha_inicio - hoy).days
    if desde < 0:
        return [
            None
        ] * dias, "La fecha de inicio ya ha pasado: el plan no tiene en cuenta el tiempo."
    if desde + dias > DIAS_PREVISION:
        return [None] * dias, "Sin previsión del tiempo: las fechas caen fuera de los 16 días."
    return prevision(base["lat"], base["lon"], desde + dias)[desde:], None


ROBO_MIN = 10  # min de desvío por debajo de los cuales no se mira si la parada es de otro grupo
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
    ventana = FIN[req.ritmo] - INICIO - COMIDA  # trayecto + visitas tienen que caber aquí
    cands = sorted(cands, key=lambda r: -r["puntos"])
    if not cands:
        return [[] for _ in prevision_dias]
    pts = [(base["lat"], base["lon"])] + [(r["lat"], r["lon"]) for r in cands]
    m = matriz(pts, req.transporte)
    libres = list(range(1, len(cands) + 1))  # índices en m (0 = base)
    grupos = []

    en_base = _en_base(pts, req.transporte)

    def cabe(d, coste):
        monte = [duracion(cands[j - 1]) for j in d if caminata(cands[j - 1])]
        visitas = sum(duracion(cands[j - 1]) for j in d)
        return (
            coste <= tope
            and coste + visitas <= ventana
            and len(monte) <= MAX_RUTAS[req.ritmo]
            and sum(monte) <= ESFUERZO_MIN[req.ritmo]
        )

    def del_pueblo_en_excursion(j, dia, ultimo):
        """Las paradas del pueblo no rellenan días de excursión (Pamplona repartida en 4 días,
        siempre con desvío ~0); van juntas en su día. El último día, lo que quede."""
        return not ultimo and j in en_base and any(k not in en_base for k in dia)

    def desvio(orden, j):
        """Lo que alarga la vuelta meter j en su mejor hueco (inserción más barata: con la
        fuerza bruta por opción y 7-8 paradas, agrupar tardaba demasiado)."""
        pasos = zip((0, *orden), (*orden, 0), strict=True)
        return min(_w(m, a, j) + _w(m, j, b) - _w(m, a, b) for a, b in pasos)

    def de_otro_grupo(j, desvio, dia, ultimo):
        """Con la tarde libre, un día al oeste se llevaba una iglesia del grupo del este
        (desvío 25 min) y partía ese grupo en dos días: no se roba si queda otro día y j tiene
        un candidato libre mucho más cerca que lo que cuesta meterlo aquí."""
        if ultimo or desvio <= ROBO_MIN:
            return False
        return any(
            m[j][k] is not None and m[j][k] < desvio / 2 for k in libres if k not in dia and k != j
        )

    n_intereses = max((r.get("interes") or 0) for r in cands) + 1

    def semillas(pool, turno):
        return sorted(pool, key=lambda i: cands[i - 1].get("interes") != turno)

    for n, p in enumerate(prevision_dias):
        ultimo = n == len(prevision_dias) - 1
        techo = [i for i in libres if cands[i - 1]["categoria"] in ("monumento", "bodega")]
        # semilla del día: por turnos, del interés n-ésimo (foces, monasterios, castillo…). Con la
        # mejor puntuación a secas, las foces (peor puntuadas) nunca abrían día y, a más de 25
        # min de desvío de los demás grupos, no entraba ni Arbayún ni Benasa
        turno = n % n_intereses
        for pool in [techo, libres] if p and p["mal_tiempo"] else [libres]:
            dia = next(
                ([i] for i in semillas(pool, turno) if cabe([i], _mejor_orden(m, [i])[1])), []
            )
            while dia and len(dia) < paradas:
                orden, coste = _mejor_orden(m, dia)
                opciones = [
                    (desvio(orden, j), -cands[j - 1]["puntos"], j) for j in pool if j not in dia
                ]
                opciones = [
                    o
                    for o in opciones
                    if o[0] <= DESVIO_MAX
                    and cabe([*dia, o[2]], coste + o[0])
                    and not de_otro_grupo(o[2], o[0], dia, ultimo)
                    and not del_pueblo_en_excursion(o[2], dia, ultimo)
                ]
                if not opciones:
                    break
                dia.append(min(opciones)[2])
            if dia:
                break
        libres = [i for i in libres if i not in dia]
        grupos.append([cands[i - 1] for i in dia])
    return grupos


def _w(m: list[list[float | None]], a: int, b: int) -> float:
    return math.inf if m[a][b] is None else m[a][b]


def _mejor_orden(m: list[list[float | None]], idx: list[int]) -> tuple[list[int], float]:
    """Ruta circular base -> paradas -> base más corta, exacta (Held-Karp, O(n² 2ⁿ)): con 8
    paradas son ~16 000 pasos; la fuerza bruta (8! órdenes) ya no daba."""
    n = len(idx)
    if not n:
        return [], 0.0
    # mejor[(conjunto, última)] = (coste desde la base, penúltima)
    mejor = {(1 << k, k): (_w(m, 0, idx[k]), None) for k in range(n)}
    for conjunto in range(1, 1 << n):
        for ult in range(n):
            if (conjunto, ult) not in mejor:
                continue
            c = mejor[conjunto, ult][0]
            for sig in range(n):
                if conjunto & (1 << sig):
                    continue
                clave, nc = (conjunto | 1 << sig, sig), c + _w(m, idx[ult], idx[sig])
                if clave not in mejor or nc < mejor[clave][0]:
                    mejor[clave] = (nc, ult)
    todos = (1 << n) - 1
    ult = min(range(n), key=lambda k: mejor[todos, k][0] + _w(m, idx[k], 0))
    coste = mejor[todos, ult][0] + _w(m, idx[ult], 0)
    orden, conjunto = [], todos
    while ult is not None:
        orden.append(idx[ult])
        conjunto, ult = conjunto ^ (1 << ult), mejor[conjunto, ult][1]
    return orden[::-1], coste


def _q(minutos: float) -> int:
    """Hacia arriba al cuarto de hora: 10:13 -> 10:15. Las horas se leen mejor redondas."""
    return math.ceil(minutos / 15 - 1e-9) * 15


def _horario(paradas: list[dict], tramos: list[float]) -> dict:
    """tramos: minutos base->1.ª, ..., última->base. Llegadas, duraciones y comida, en cuartos
    de hora. La comida va tras la parada que acaba pasadas las 13:30, o ya pasadas las 12:45 si
    la siguiente acabaría después de las 14:30 (con las 13:00, el Valle de Roncal de 12:59 a
    14:59 dejaba la comida a las 15:00); si el día acaba antes, a las 13:30 tras la última. Una
    caminata que pasa por las 13:30 lleva la comida dentro (picnic)."""
    t, comida_tras = INICIO, None
    for r in paradas:
        r.pop("comida_despues", None)  # de un intento anterior con otras paradas
    for i, (r, tramo) in enumerate(zip(paradas, tramos, strict=False)):
        dur = _q(duracion(r))
        r.pop("picnic", None)
        fin = _q(t + tramo) + dur
        if comida_tras is None and i and t >= ANTES_DE_COMER and fin > COMIDA_TOPE:
            t = _q(t)
            comida_tras, paradas[i - 1]["comida_despues"] = i - 1, _hora(t)
            t += COMIDA
        t = _q(t + tramo)
        r["llegada"], r["duracion_min"], r["trayecto_min"] = _hora(t), dur, round(tramo)
        if comida_tras is None and caminata(r) and t < HORA_COMIDA < t + dur:
            comida_tras, r["picnic"] = i, True  # se come andando: sin restaurante
            t += PICNIC
        t += dur
        if comida_tras is None and t >= HORA_COMIDA:
            comida_tras, r["comida_despues"] = i, _hora(t)
            t += COMIDA
    if comida_tras is None and paradas:  # el día acaba antes: se come a las 13:30
        comida_tras, t = len(paradas) - 1, max(t, HORA_COMIDA)
        paradas[-1]["comida_despues"] = _hora(t)
        t += COMIDA
    return {"comida_tras": comida_tras, "vuelta": _q(t + (tramos[-1] if tramos else 0))}


ANTES_DE_COMER = 12 * 60 + 45


def _hora(minutos: float) -> str:
    return f"{int(minutos) // 60:02d}:{int(minutos) % 60:02d}"


def ordenar(
    base: dict, grupo: list[dict], req: Requisitos, matriz=rutas.matriz, ruta=rutas.ruta
) -> dict:
    """Orden óptimo del día; si se pasa del tope de conducción o vuelve después de FIN,
    quita la parada que más tiempo ahorra por punto de interés y lo intenta otra vez (quitar
    solo por puntos dejaba Quinto Real, a 1 h, y quitaba Irati y la Cascada del Cubo)."""
    paradas = [dict(r) for r in grupo]
    if not paradas:
        return {"paradas": [], "km": 0, "trayecto_min": 0, "descartadas": []}
    pts = [(base["lat"], base["lon"])] + [(r["lat"], r["lon"]) for r in paradas]
    m = matriz(pts, req.transporte)
    en_base = _en_base(pts, req.transporte)
    mo = _sin_idas_y_vueltas(m, en_base)
    idx = list(range(1, len(pts)))
    descartadas = []  # para depurar y evaluar: qué se quitó y por qué
    while idx:
        orden, coste_orden = _mejor_orden(mo, idx)
        minutos = sum(_w(m, a, b) for a, b in zip((0, *orden), (*orden, 0), strict=True))
        dia = [paradas[i - 1] for i in orden]
        tramos = [m[a][b] for a, b in zip((0, *orden), (*orden, 0), strict=True)]
        h = _horario(dia, tramos)
        if minutos <= MAX_TRAYECTO[req.ritmo] and h["vuelta"] <= FIN[req.ritmo]:
            break
        quitar = max(
            idx,
            key=lambda i: (
                (coste_orden - _mejor_orden(mo, [j for j in idx if j != i])[1])
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
    h = _alargar(dia, tramos, h, FIN[req.ritmo])
    r = ruta([pts[0], *((p["lat"], p["lon"]) for p in dia), pts[0]], req.transporte, True)
    return {
        "paradas": dia,
        "comida_tras": h["comida_tras"],
        "vuelta": _hora(h["vuelta"]),
        "en_la_base": all(i in en_base for i in orden),  # sin salir del pueblo: "fin del recorrido"
        "km": r["km"],
        "trayecto_min": r["minutos"],
        "geometria": r.get("geometria"),
        "descartadas": descartadas,
    }


ALARGAR_MAX = {"monumento": 0.5, "natural": 1.0}  # cuánto puede crecer una visita (fracción)
HOLGURA = 30  # min antes de FIN que se dejan libres


def _alargar(dia: list[dict], tramos: list[float], h: dict, fin: int) -> dict:
    """Si el día acaba mucho antes de FIN (pocos lugares cerca), las visitas se alargan, por
    cuartos de hora y como mucho ALARGAR_MAX (un parque natural, el doble; las caminatas, no),
    en vez de dejar la tarde libre."""
    libre = fin - HOLGURA - h["vuelta"]
    if libre < 45:
        return h
    topes = {
        i: _q(duracion(p) * ALARGAR_MAX.get(p["categoria"], 0)) if not caminata(p) else 0
        for i, p in enumerate(dia)
    }
    extra = dict.fromkeys(topes, 0)
    while libre >= 15 and any(extra[i] < topes[i] for i in topes):
        for i in topes:  # por turnos: reparte entre todas en vez de alargar solo la primera
            if extra[i] < topes[i] and libre >= 15:
                extra[i] += 15
                libre -= 15
    base = [duracion(p) for p in dia]
    for p, b, i in zip(dia, base, extra, strict=True):
        p["duracion_min"] = b + extra[i]
    h = _horario(dia, tramos)
    for i in sorted(extra, key=lambda i: -extra[i]):  # por si los cuartos se pasan de FIN
        while h["vuelta"] > fin and extra[i]:
            extra[i] -= 15
            dia[i]["duracion_min"] -= 15
            h = _horario(dia, tramos)
    return h


def _suma(*minutos) -> float | None:
    """None (sin camino por carretera) o inf -> None, para que el JSON lo admita."""
    t = sum(math.inf if x is None else x for x in minutos)
    return None if math.isinf(t) else round(t)


def comida(con, parada: dict, modo: str, usados: set[str], pedidos=frozenset()) -> dict | None:
    """Restaurante más cercano a la parada de antes de comer, sin repetir entre días."""
    filas = consultas.restaurantes_cerca(
        con, parada["lat"], parada["lon"], RADIO_COMIDA_KM[modo], limite=20
    )
    return _elegir(filas, usados, pedidos)


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
    for d in plan["dias"]:
        if d.get("cena"):
            fs.append(punto(d["cena"], tipo="cena", dia=d["dia"]))
        fs += [punto(b, tipo="bar", dia=d["dia"]) for b in d.get("ronda") or []]
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
        if d.get("cena"):
            out.add(d["cena"]["id"])
        out |= {b["id"] for b in d.get("ronda") or []}
    return out
