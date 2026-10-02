"""data/raw/*.json -> data/navarra.duckdb con el esquema común (ver 04-calidad-datos.md)."""

import csv
import json
from pathlib import Path

import duckdb
import httpx
from pyproj import Transformer
from shapely.geometry import Point, shape

from navarra_trip.ingest import ckan as extra_ckan
from navarra_trip.ingest import extra, osm, wikidata
from navarra_trip.ingest.ckan import RAW
from navarra_trip.ingest.geocode import Geocoder
from navarra_trip.ingest.wikidata import Cliente

DB = Path("data/navarra.duckdb")
GEOCACHE = Path("data/geocache.json")
_UTM = Transformer.from_crs("EPSG:25830", "EPSG:4326", always_xy=True)

TIPO_ALOJAMIENTO = {
    "Apartamento Turístico": "apartamento",
    "Apartamento": "apartamento",
    "Bloque apartamentos turísticos": "apartamento",
    "Hotel-apartamento": "apartamento",
    "Vivienda Turística": "apartamento",
    "Casa rural vivienda": "rural",
    "Casa rural habitaciones": "rural",
    "Apartamento Turístico Rural": "rural",
    "Vivienda Turística Rural": "rural",
    "Hotel rural": "rural",
    "Hotel": "hotel",
    "Hostal": "hotel",
    "Pensión": "hotel",
    "Albergue turístico": "albergue",
    "Camping": "camping",
    "Agroturismo": "rural",
}


def _unicos(registros: list[dict], clave: str) -> list[dict]:
    """Quita repetidos: la primera fila de cada id."""
    vistos: dict[str, dict] = {}
    for r in registros:
        vistos.setdefault(r[clave].strip().upper(), r)
    return list(vistos.values())


def _utm(x: str, y: str) -> tuple[float | None, float | None]:
    if not float(x) or not float(y):  # (0, 0) = sin coordenadas en origen
        return None, None
    return _UTM.transform(float(x), float(y))


def recursos(mon: dict, esp: dict) -> list[dict]:
    filas = []
    for raw, pref, cat, tipo in (
        (mon, "mon", "monumento", "Tipo"),
        (esp, "esp", "natural", "TIPO"),
    ):
        por_id: dict[str, dict] = {}
        for r in raw["registros"]:
            if (f := por_id.get(r["Codrecurso"])) is not None:  # mismo lugar, otro tipo
                f["subcategorias"].append(r[tipo])
                continue
            lon, lat = _utm(r["GEORR_X"], r["GEORR_Y"])
            por_id[r["Codrecurso"]] = {
                "id": f"{pref}:{r['Codrecurso']}",
                "nombre": r["Nombre"].strip(),
                "categoria": cat,
                "subcategorias": [r[tipo]],
                "estilo": r.get("ESTILO"),
                "municipio": (r["NombreLocalidad"] or "").title() or None,
                "zona": r["DescripZona"],
                "lon": lon,
                "lat": lat,
                "url_fuente": raw["url_fuente"],
                "licencia": raw["licencia"],
            }
        filas += por_id.values()
    return filas


MANUAL = Path(__file__).with_name("establecimientos_manual.csv")


def leer_manual(ruta: Path = MANUAL) -> dict[str, tuple[float, float]]:
    """id -> (lon, lat) revisadas a mano; mandan sobre la geolocalización."""
    with ruta.open(encoding="utf-8") as f:
        return {r["id"]: (float(r["lon"]), float(r["lat"])) for r in csv.DictReader(f)}


def establecimientos(raw: dict, pref: str, geo: Geocoder, manual: dict | None = None) -> list[dict]:
    manual = leer_manual() if manual is None else manual
    filas = []
    for r in _unicos(raw["registros"], "COD_INSCRIPCION"):
        id = f"{pref}:{r['COD_INSCRIPCION'].strip().upper()}"
        if id in manual:
            (lon, lat), precision = manual[id], "manual"
        else:
            lon, lat, precision = geo.geocodificar(
                r["DIRECCION"], r["LOCALIDAD"], r["MUNICIPIO"]
            ) or (None, None, None)
        f = {
            "id": id,
            "nombre": r["NOMBRE"].strip(),
            "categoria": r["CATEGORIA"],
            "direccion": r["DIRECCION"],
            "localidad": r["LOCALIDAD"],
            "municipio": r["MUNICIPIO"],
            "subzona": r["SUB_ZONA"],
            "lon": lon,
            "lat": lat,
            "geo_precision": precision,
            "url_fuente": raw["url_fuente"],
            "licencia": raw["licencia"],
        }
        if pref == "aloj":
            f |= {
                "modalidad": r["MODALIDAD"],
                "tipo": TIPO_ALOJAMIENTO.get(r["MODALIDAD"]),
                "plazas": int(r["PLAZAS"]) if r["PLAZAS"] else None,  # agroturismos: sin dato
            }
        elif pref == "rest":
            f["especialidad"] = None if r["Especialidad"] == "Desconocido" else r["Especialidad"]
        else:
            f |= extra.actividades_extra(r)
        filas.append(f)
    return filas


def guardar(con: duckdb.DuckDBPyConnection, tabla: str, filas: list[dict]) -> None:
    con.execute(f"DROP TABLE IF EXISTS {tabla}")
    con.execute(
        f"CREATE TABLE {tabla} AS SELECT * FROM (SELECT unnest($f, recursive := true))",
        {"f": filas},
    )
    if "id" in filas[0]:
        con.execute(f"ALTER TABLE {tabla} ADD PRIMARY KEY (id)")


# El contorno del CKAN está simplificado (~150 vértices): sin margen, la Mesa de los Tres Reyes o
# las ventas de Dantxarinea salen "fuera". El margen solo busca errores gordos (otra provincia).
MARGEN_GRADOS = 0.05  # ~5 km


def fuera_de_navarra(filas: list[dict], contorno: dict) -> list[str]:
    poligono = shape(contorno).buffer(MARGEN_GRADOS)
    return [
        f["id"]
        for f in filas
        if f["lon"] is not None and not poligono.contains(Point(f["lon"], f["lat"]))
    ]


def main() -> None:
    """`navarra-normalizar`: la 1.ª vez geolocaliza (~30 min) y consulta Wikidata, OSM e IDENA
    (~10 min). Después todo sale de la caché.

    Mientras Claude Desktop tenga abierto el servidor MCP, la base está bloqueada: ciérralo antes.
    """
    cargar = lambda n: json.loads((RAW / f"{n}.json").read_text(encoding="utf-8"))  # noqa: E731
    mon, esp = cargar("arte-y-monumentos"), cargar("espacios-naturales")
    aloj = cargar("alojamientos-inscritos-en-el-registro-de-turismo-de-navarra")
    agro = extra.agroturismos_como_alojamientos(
        cargar("agroturismos-en-activo-del-registro-de-turismo-de-navarra")
    )
    rest = cargar("restaurantes-inscritos-en-el-registro-de-turismo-de-navarra")
    act = cargar("empresas-de-actividades-inscritas-en-el-registro-de-turismo-de-navarra")
    cache = json.loads(GEOCACHE.read_text(encoding="utf-8")) if GEOCACHE.exists() else {}
    with httpx.Client(timeout=30, headers={"User-Agent": "navarra-trip-agent"}) as client:
        geo = Geocoder(client, cache)
        try:
            manual = wikidata.leer_manual()
            tablas = {
                "recurso": [
                    r for r in recursos(mon, esp) if not manual.get(r["id"], {}).get("quitar")
                ],
                "alojamiento": establecimientos(aloj, "aloj", geo)
                + establecimientos(agro, "aloj", geo),
                "restaurante": establecimientos(rest, "rest", geo),
                "actividad": establecimientos(act, "act", geo),
            }
            n = extra.completar_municipios(tablas["recurso"], Cliente(client, cache))
            print(f"municipio    {n} recursos completados con los límites de IDENA")
        finally:
            GEOCACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        wikidata.ejecutar(tablas["recurso"], client)
        osm.ejecutar(tablas["recurso"], client)
    extra.aplicar_manual(tablas["recurso"], manual)
    print(f"zona         {extra.completar_zonas(tablas['recurso'])} recursos con la más cercana")
    tablas["oficina"] = extra.oficinas(cargar(extra_ckan.OFICINAS))
    tablas["ave"] = extra.aves(cargar("turismo-ornitol-gico"))
    tablas["afluencia"] = extra.afluencia(
        cargar("recursos-turisticos"), extra.leer_afluencia_manual()
    )
    extra.visitantes_12m(tablas["recurso"], tablas["afluencia"])
    with duckdb.connect(DB) as con:
        for tabla, filas in tablas.items():
            guardar(con, tabla, filas)
            linea = f"{tabla:12} {len(filas):5} filas"
            if "lon" in filas[0]:
                sin_geo = sum(f["lon"] is None for f in filas)
                fuera = fuera_de_navarra(filas, mon["spatial"])
                linea += f" | sin coords {sin_geo} | fuera de Navarra {fuera}"
            print(linea)
        for tabla in ("alojamiento", "restaurante", "actividad"):
            print(
                con.sql(f"SELECT geo_precision, count(*) n FROM {tabla} GROUP BY 1 ORDER BY n DESC")
            )
