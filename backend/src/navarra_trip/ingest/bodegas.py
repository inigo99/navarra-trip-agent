"""Bodegas de vino de OpenStreetMap (craft=winery, ODbL) -> recursos 'bodega'.

Ni el Registro de Turismo ni los monumentos traen bodegas, y "castillos y vino" acababa sin
ninguna. OSM no dice cuáles admiten visitas: el planificador solo las propone si se piden
(vino, bodegas...) y avisa de confirmar la visita en su web.
"""

import json
import re
from pathlib import Path

import httpx

from navarra_trip.ingest.osm import _overpass
from navarra_trip.ingest.wikidata import Cliente
from navarra_trip.tools.consultas import _sin_tildes

CACHE = Path("data/raw/bodegas_cache.json")
CONSULTA = (
    '[out:json][timeout:120];area["ISO3166-2"="ES-NA"]->.n;'
    "nwr(area.n)[craft=winery][name];out tags center;"
)
LICENCIA = "ODbL (© colaboradores de OpenStreetMap)"
DURACION = 90  # visita guiada con cata
# oficinas y sociedades ("Administracion", "Grupo Aedil S.A.") y nombres genéricos sin marca
# ("Bodega Cooperativa", en Tafalla): no se sabe cuál es ni cómo reservar
NO_VISITABLE = re.compile(
    r"^(administracion|oficinas?|grupo)\b|\bs\.? ?a\.?$|\bslu?$|^bodegas?( cooperativa)?$"
)


def fila(e: dict) -> dict:
    t, c = e["tags"], e.get("center", e)
    return {
        "id": f"bod:{e['type'][0]}{e['id']}",
        "nombre": t["name"],
        "categoria": "bodega",
        "subcategorias": ["Bodega", "Enoturismo"],
        "estilo": None,
        "municipio": None,  # lo pone completar_municipios con los límites de IDENA
        "zona": None,
        "lon": c["lon"],
        "lat": c["lat"],
        "url_fuente": f"https://www.openstreetmap.org/{e['type']}/{e['id']}",
        "licencia": LICENCIA,
        "web": t.get("website") or t.get("contact:website"),
        "horario": t.get("opening_hours"),
        "duracion_min": DURACION,
    }


def filas(elementos: list[dict]) -> list[dict]:
    """Una por bodega: OSM tiene Marco Real 3 veces (una por nave) y Piedemonte como nodo y
    edificio. Se queda la que trae más datos."""
    mejor: dict[str, dict] = {}
    for e in elementos:
        nombre = _sin_tildes(e["tags"]["name"]).strip()
        if NO_VISITABLE.search(nombre):
            continue
        if nombre not in mejor or len(e["tags"]) > len(mejor[nombre]["tags"]):
            mejor[nombre] = e
    return [fila(e) for e in mejor.values()]


def ficha(r: dict) -> str:
    """Solo datos de fuente, como osm.ficha."""
    t = f"{r['nombre']}. Bodega de vino en {r['municipio'] or 'Navarra'}: enoturismo, vino."
    return t + (f" Horario en OpenStreetMap: {r['horario']}." if r["horario"] else "")


def descargar(client: httpx.Client) -> list[dict]:
    """Paso de `navarra-normalizar`: una consulta a Overpass, con caché."""
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    try:
        elementos = _overpass(Cliente(client, cache), CONSULTA)
    finally:
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    if elementos is None:
        raise SystemExit("Overpass no responde: vuelve a lanzar navarra-normalizar más tarde")
    return filas(elementos)


def completar(bodegas: list[dict]) -> list[dict]:
    """Ficha cuando ya tienen municipio."""
    for r in bodegas:
        r |= {"descripcion": ficha(r), "descripcion_fuente": "ficha", "url_descripcion": r["web"]}
    print(f"bodega       {len(bodegas)} bodegas de OSM")
    return bodegas
