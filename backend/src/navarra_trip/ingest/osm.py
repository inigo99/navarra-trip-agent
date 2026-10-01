"""Completa los recursos con OpenStreetMap (horario, web, pago, nombre en euskera) y, si no
tienen descripción de Wikipedia/Wikidata, les pone una ficha generada solo con datos de fuente."""

import json
from pathlib import Path

import httpx

from navarra_trip.ingest.geocode import km
from navarra_trip.ingest.wikidata import Cliente, emparejar

# Instancias públicas de Overpass: la principal se satura a menudo (504)
OVERPASS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
CACHE = Path("data/raw/osm_cache.json")
RADIO_M = {"monumento": 1000, "natural": 3000}
# Un filtro por clave: el regex sobre todas las claves daba 504 en Overpass
CLAVES = (
    "tourism",
    "natural",
    "historic",
    "leisure",
    "waterway",
    "man_made",
    "route",
    "amenity=place_of_worship",
)
NOMBRES = ("name:es", "name", "alt_name", "official_name")
CORTA = 60  # caracteres: por debajo, la descripción de Wikidata se completa con la ficha


def candidatos(cli: Cliente, r: dict) -> list[dict]:
    """Elementos con nombre cerca del recurso. Si ninguna instancia de Overpass responde, []
    sin cachear (se reintenta en la próxima ejecución) en vez de parar todo."""
    filtros = "".join(f"nwr.a[{k}];" for k in CLAVES)
    q = (
        f"[out:json][timeout:60];nwr(around:{RADIO_M[r['categoria']]},{r['lat']},{r['lon']})"
        f"[name]->.a;({filtros});out tags center;"
    )
    elementos = _overpass(cli, q)
    if elementos is None:
        print(
            f"  aviso: Overpass no responde para {r['id']}; se reintentará en la próxima ejecución"
        )
        return []
    out = []
    for e in elementos:
        c = e.get("center", e)
        out.append(
            {
                "q": f"{e['type']}/{e['id']}",
                "textos": [e["tags"][k] for k in NOMBRES if k in e["tags"]],
                "km": km(r["lon"], r["lat"], c["lon"], c["lat"]),
                "tags": e["tags"],
            }
        )
    return out


def _overpass(cli: Cliente, q: str) -> list | None:
    for url in OVERPASS:  # 1.º la caché, venga de la instancia que venga
        if (c := cli.cache.get(cli.clave(url, data=q))) is not None:
            return c["elements"]
    for url in OVERPASS:
        try:
            return cli.get(url, data=q)["elements"]
        except (httpx.HTTPStatusError, httpx.TransportError):
            continue
    return None


def ficha(r: dict) -> str:
    """Solo campos de fuente, sin redactar nada: así no se inventa."""
    partes = [r["nombre"] + ".", "Tipo: " + ", ".join(r["subcategorias"]) + "."]
    if r.get("estilo"):
        partes.append(f"Estilo: {r['estilo']}.")
    if r.get("municipio"):
        partes.append(f"Municipio: {r['municipio']}.")
    if r.get("zona"):
        partes.append(f"Zona: {r['zona']}.")
    if r.get("nombre_eu"):
        partes.append(f"En euskera: {r['nombre_eu']}.")
    return " ".join(partes)


def completar(recursos: list[dict], cli: Cliente) -> None:
    """Añade osm_id, horario, web, de_pago y nombre_eu; rellena descripcion con la ficha."""
    for r in recursos:
        r |= {"osm_id": None, "horario": None, "web": None, "de_pago": None, "nombre_eu": None}
        if r["lon"] is not None:
            cands = candidatos(cli, r)
            osm_id, _ = emparejar(r, cands)
            if osm_id:
                t = next(c["tags"] for c in cands if c["q"] == osm_id)
                r["osm_id"] = osm_id
                r["horario"] = t.get("opening_hours")  # formato OSM, p. ej. "Tu-Su 10:00-14:00"
                r["web"] = t.get("website") or t.get("contact:website")
                r["de_pago"] = {"yes": True, "no": False}.get(t.get("fee"))
                r["nombre_eu"] = t.get("name:eu")
        if r["descripcion_fuente"] == "wikidata" and len(r["descripcion"]) < CORTA:
            # "bien de interés cultural" sola no dice nada: ficha + esa línea
            r["descripcion"] = f"{ficha(r)} {r['descripcion'][0].upper()}{r['descripcion'][1:]}."
            r["descripcion_fuente"] = "ficha"
        elif not r["descripcion"]:
            r["descripcion"] = ficha(r)
            r["descripcion_fuente"] = "ficha"
            r["url_descripcion"] = (
                f"https://www.openstreetmap.org/{r['osm_id']}" if r["osm_id"] else r["url_fuente"]
            )


def ejecutar(recursos: list[dict], client: httpx.Client) -> None:
    """Paso de `navarra-normalizar`. Overpass pide no ir en paralelo: 1 consulta/s, con caché."""
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    try:
        completar(recursos, Cliente(client, cache))
    finally:
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    n = sum(r["osm_id"] is not None for r in recursos)
    h = sum(r["horario"] is not None for r in recursos)
    f = sum(r["descripcion_fuente"] == "ficha" for r in recursos)
    print(f"osm          {n}/{len(recursos)} enlazados | {h} con horario | {f} con ficha")
