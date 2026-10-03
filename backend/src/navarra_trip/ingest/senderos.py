"""Senderos homologados (SL-NA y PR-NA) de IDENA y cimas de OpenStreetMap -> recursos 'ruta'.

Los traza la Federación Navarra de Deportes de Montaña (CC-BY 4.0). Los GR no entran: son de
varias etapas y no caben en una excursión de día desde la base. Cada sendero es una capa del WFS
de IDENA; las capas se descubren en el GetCapabilities. Todo pasa por caché en disco.
"""

import json
import re
from pathlib import Path

import httpx
from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import Point, shape
from shapely.ops import transform

from navarra_trip.ingest.osm import _overpass
from navarra_trip.ingest.wikidata import Cliente

WFS = "https://idena.navarra.es/ogc/wfs"
CACHE = Path("data/raw/senderos_cache.json")
CAPA = re.compile(r"IDENA:(DOTACI_Lin_(?:SLNA|PRNA)\d+\w*)")
LICENCIA = "CC-BY 4.0 (Federación Navarra de Deportes de Montaña y Escalada / IDENA)"
CIMAS = (
    '[out:json][timeout:120];area["ISO3166-2"="ES-NA"]->.n;'
    "node(area.n)[natural=peak][name][ele];out;"
)
CIMA_M = 150  # una cima a menos de esto del trazado: el sendero pasa por ella
MAX_MIN = 6 * 60  # más, no cabe en un día con trayecto y comida
_UTM = Transformer.from_crs("EPSG:4326", "EPSG:25830", always_xy=True).transform
TIPO = {"SLNA": "Sendero local", "PRNA": "Pequeño recorrido"}


def capas(cli: Cliente) -> list[str]:
    clave = cli.clave(WFS, request="GetCapabilities")
    if clave not in cli.cache:
        r = cli.client.get(
            WFS, params={"service": "WFS", "request": "GetCapabilities"}, timeout=180
        )
        r.raise_for_status()
        cli.cache[clave] = sorted(set(CAPA.findall(r.text)))
    return cli.cache[clave]


def tramos(cli: Cliente, capa: str) -> list[dict]:
    """[] si la capa falla: un sendero roto no para la normalización."""
    try:
        return _tramos(cli, capa)
    except (httpx.HTTPError, KeyError, ValueError) as e:
        print(f"  aviso: {capa} sin datos ({e})")
        return []


def _tramos(cli: Cliente, capa: str) -> list[dict]:
    return cli.get(
        WFS,
        service="WFS",
        version="2.0.0",
        request="GetFeature",
        typeNames=f"IDENA:{capa}",
        outputFormat="application/json",
        srsName="EPSG:4326",
    )["features"]


def minutos(km: float, subida: float, bajada: float) -> int:
    """Método MIDE: 4 km/h en llano, 400 m/h subiendo, 600 m/h bajando; el mayor de los dos
    más la mitad del menor. Redondeado a 15 min."""
    h, v = km / 4, subida / 400 + bajada / 600
    return round((max(h, v) + min(h, v) / 2) * 60 / 15) * 15


def ruta(capa: str, fs: list[dict]) -> dict | None:
    """Un recurso por sendero: el tramo más largo es el principal; los cortos son variantes
    y enlaces (TRAMO no coincide con IDRUTA ni lleva un formato fijo). No todas las capas traen
    los mismos campos: PR-NA 210 tiene RUTA en vez de NOMBRE y ni lugares ni desniveles
    (entonces cuenta como circular y llano)."""
    f = max(fs, key=lambda f: f["properties"].get("GEOM_LONG") or 0)
    p, geom = f["properties"], shape(f["geometry"])
    circular = (p.get("LUGARINI") or "") == (p.get("LUGARFIN") or "")
    km = (p.get("GEOM_LONG") or 0) / 1000
    desnivel = p.get("DESNIPOSI")
    sube, baja = desnivel or 0, p.get("DESNINEGA") or 0
    if not circular:  # lineal: ida y vuelta por el mismo camino, con el coche en el inicio
        km, sube, baja = 2 * km, sube + baja, sube + baja
    mins = minutos(km, sube, baja)
    if not km or mins > MAX_MIN:
        return None
    lon, lat = geom.geoms[0].coords[0] if geom.geom_type == "MultiLineString" else geom.coords[0]
    tipo = TIPO[re.match(r"DOTACI_Lin_([A-Z]+)", capa)[1]]
    return {
        "id": f"ruta:{capa.removeprefix('DOTACI_Lin_').lower()}",
        "nombre": f"{p.get('NOMBRE') or p.get('RUTA')} ({p['IDRUTA']})",
        "categoria": "ruta",
        "subcategorias": [tipo, "Senderismo"],
        "estilo": None,
        "municipio": p.get("LUGARINI"),
        "zona": None,
        "lon": lon,
        "lat": lat,
        "url_fuente": f"{WFS}?service=WFS&request=GetFeature&typeNames=IDENA:{capa}",
        "licencia": LICENCIA,
        "web": p.get("URL") or None,
        "gpx": p.get("DESCARGA") or None,
        "duracion_min": mins,
        "longitud_km": round(km, 1),
        "desnivel_m": None if desnivel is None else round(sube),
        "altitud_max": p.get("ALTITUDMAX"),
        "circular": circular,
        "_geom": geom,
    }


def cimas_cerca(rutas: list[dict], picos: list[dict]) -> None:
    """Añade las cimas por las que pasa cada sendero (nombre y altitud de OSM)."""
    pts = [transform(_UTM, Point(c["lon"], c["lat"])) for c in picos]
    arbol = STRtree(pts)
    for r in rutas:
        linea = transform(_UTM, r.pop("_geom"))
        cerca = arbol.query(linea, predicate="dwithin", distance=CIMA_M)
        cs = sorted((picos[i] for i in cerca), key=lambda c: -_ele(c))
        r["cimas"] = ", ".join(f"{c['tags']['name']} ({_ele(c):.0f} m)" for c in cs) or None
        if cs:
            r["subcategorias"].append("Cimas")


def _ele(c: dict) -> float:
    try:
        return float(c["tags"]["ele"].replace(",", ".").split()[0])
    except ValueError:
        return 0.0


def ficha(r: dict) -> str:
    """Solo datos de fuente, como osm.ficha; lo que la capa no trae no se menciona."""
    forma = "circular" if r["circular"] else "lineal, ida y vuelta"
    desde = f" desde {r['municipio']}" if r["municipio"] else ""
    datos = [f"{r['longitud_km']} km"]
    if r["desnivel_m"] is not None:
        datos.append(f"{r['desnivel_m']} m de desnivel positivo")
    h, m = divmod(r["duracion_min"], 60)
    datos.append(f"unas {h} h {m:02d} min (método MIDE)")
    t = f"{r['nombre']}. {r['subcategorias'][0]} homologado, {forma}{desde}: {', '.join(datos)}."
    if r["altitud_max"]:
        t += f" Altitud máxima: {r['altitud_max']} m."
    t += " Ruta de senderismo y montaña."
    return t + (f" Pasa por la cima de {r['cimas']}." if r["cimas"] else "")


def ejecutar(client: httpx.Client) -> list[dict]:
    """Paso de `navarra-normalizar`. La 1.ª vez, una petición por sendero (~5 min)."""
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    cli = Cliente(client, cache, pausa=0.2)
    try:
        rutas = [r for c in capas(cli) if (fs := tramos(cli, c)) and (r := ruta(c, fs))]
        picos = _overpass(cli, CIMAS) or []
    finally:
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    cimas_cerca(rutas, picos)
    for r in rutas:
        r |= {"descripcion": ficha(r), "descripcion_fuente": "ficha", "url_descripcion": r["web"]}
    con_cima = sum(r["cimas"] is not None for r in rutas)
    print(f"senderos     {len(rutas)} SL/PR de un día | {con_cima} por cima | {len(picos)} cimas")
    return rutas
