"""Bares, pubs y cafeterías de OpenStreetMap (ODbL) para las rondas de pintxos.

El Registro de Turismo solo trae restaurantes; los bares no se inscriben. OSM no dice cuál es
mejor ni suele traer horario: el planificador elige por cercanía y pide confirmar horarios.
"""

import json
from pathlib import Path

import httpx

from navarra_trip.ingest.osm import _overpass
from navarra_trip.ingest.wikidata import Cliente

CACHE = Path("data/raw/bares_cache.json")
CONSULTA = (
    '[out:json][timeout:180];area["ISO3166-2"="ES-NA"]->.n;'
    'nwr(area.n)[amenity~"^(bar|pub|cafe)$"][name];out tags center;'
)
LICENCIA = "ODbL (© colaboradores de OpenStreetMap)"


def fila(e: dict) -> dict:
    t, c = e["tags"], e.get("center", e)
    return {
        "id": f"bar:{e['type'][0]}{e['id']}",  # n123, w456: el tipo de elemento de OSM
        "nombre": t["name"],
        "tipo": t["amenity"],
        "localidad": t.get("addr:city"),
        "lat": c["lat"],
        "lon": c["lon"],
        "horario": t.get("opening_hours"),
        "web": t.get("website") or t.get("contact:website"),
        "url_fuente": f"https://www.openstreetmap.org/{e['type']}/{e['id']}",
        "licencia": LICENCIA,
    }


def ejecutar(client: httpx.Client) -> list[dict]:
    """Paso de `navarra-normalizar`: una consulta a Overpass, con caché."""
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    try:
        elementos = _overpass(Cliente(client, cache), CONSULTA)
    finally:
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    if elementos is None:
        raise SystemExit("Overpass no responde: vuelve a lanzar navarra-normalizar más tarde")
    filas = [fila(e) for e in elementos]
    print(f"bar          {len(filas)} bares, pubs y cafeterías de OSM")
    return filas
