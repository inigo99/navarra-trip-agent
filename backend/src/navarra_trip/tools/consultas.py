"""Consultas sobre data/navarra.duckdb que usan el agente y el servidor MCP.

Devuelven listas de dicts (JSON directo). Las distancias son en línea recta (haversine en SQL);
los tiempos reales por carretera los da OSRM (tools/rutas.py).
"""

import unicodedata
from pathlib import Path

import duckdb

DB = Path("data/navarra.duckdb")
RESUMEN = 300  # caracteres de descripción en los listados; la completa, con recurso()

# Distancia en km entre dos puntos. Sin extensión spatial: con unos miles de filas sobra y la
# CI no depende de descargar extensiones. ponytail: si hacen falta polígonos ("dentro de"),
# cargar spatial y usar ST_Contains.
_KM = """CREATE OR REPLACE TEMP MACRO km(lat1, lon1, lat2, lon2) AS
  12742 * asin(sqrt(pow(sin(radians(lat2 - lat1) / 2), 2)
    + cos(radians(lat1)) * cos(radians(lat2)) * pow(sin(radians(lon2 - lon1) / 2), 2)))"""


def preparar(con: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    con.execute(_KM)  # macro temporal: vale aunque la base sea de solo lectura
    return con


def conectar(ruta: Path = DB) -> duckdb.DuckDBPyConnection:
    return preparar(duckdb.connect(str(ruta), read_only=True))


def _filas(con: duckdb.DuckDBPyConnection, sql: str, params: list) -> list[dict]:
    rel = con.execute(sql, params)
    cols = [d[0] for d in rel.description]
    return [dict(zip(cols, f, strict=True)) for f in rel.fetchall()]


def _cerca(
    tabla: str,
    columnas: str,
    filtros: dict,
    lat: float,
    lon: float,
    radio_km: float,
    limite: int,
    con,
) -> list[dict]:
    where = ["lat IS NOT NULL", "km(?, ?, lat, lon) <= ?"]
    params: list = [lat, lon, radio_km]
    for col, valor in filtros.items():
        if valor is not None:
            where.append(
                f"{col} = ?" if col != "subcategoria" else "list_contains(subcategorias, ?)"
            )
            params.append(valor)
    sql = (
        f"SELECT {columnas}, round(km(?, ?, lat, lon), 2) AS km FROM {tabla} "
        f"WHERE {' AND '.join(where)} ORDER BY km LIMIT ?"
    )
    return _filas(con, sql, [lat, lon, *params, limite])


_COLS_RECURSO = (
    "id, nombre, categoria, subcategorias, estilo, municipio, zona, lat, lon, "
    f"left(descripcion, {RESUMEN}) AS descripcion, descripcion_fuente, horario, visitantes_12m, "
    "url_fuente"
)


def recursos_cerca(
    con,
    lat: float,
    lon: float,
    radio_km: float = 10,
    categoria: str | None = None,
    subcategoria: str | None = None,
    limite: int = 20,
) -> list[dict]:
    """Monumentos ('monumento') y espacios naturales ('natural') a menos de radio_km."""
    filtros = {"categoria": categoria, "subcategoria": subcategoria}
    return _cerca("recurso", _COLS_RECURSO, filtros, lat, lon, radio_km, limite, con)


def alojamientos_cerca(
    con, lat: float, lon: float, radio_km: float = 5, tipo: str | None = None, limite: int = 20
) -> list[dict]:
    """tipo: apartamento | rural | hotel | albergue | camping."""
    cols = (
        "id, nombre, modalidad, tipo, categoria, plazas, localidad, lat, lon, geo_precision, "
        "url_fuente"
    )
    return _cerca("alojamiento", cols, {"tipo": tipo}, lat, lon, radio_km, limite, con)


def restaurantes_cerca(
    con, lat: float, lon: float, radio_km: float = 5, limite: int = 20
) -> list[dict]:
    cols = "id, nombre, categoria, especialidad, localidad, lat, lon, geo_precision, url_fuente"
    return _cerca("restaurante", cols, {}, lat, lon, radio_km, limite, con)


def actividades_cerca(
    con, lat: float, lon: float, radio_km: float = 15, texto: str | None = None, limite: int = 20
) -> list[dict]:
    """Empresas de turismo activo y cultural. texto filtra por actividad ('kayak', 'bici')."""
    cols = "id, nombre, tipo, actividades, localidad, lat, lon, geo_precision, url_fuente"
    filas = _cerca("actividad", cols, {}, lat, lon, radio_km, 1000, con)
    if texto:
        t = _sin_tildes(texto)
        filas = [f for f in filas if any(t in _sin_tildes(a) for a in f["actividades"] or [])]
    return filas[:limite]


def oficinas_cerca(
    con, lat: float, lon: float, radio_km: float = 50, limite: int = 5
) -> list[dict]:
    """Oficinas de turismo (hay 9 en toda Navarra)."""
    cols = "id, nombre, zona, direccion, localidad, telefono, email, web, lat, lon, url_fuente"
    return _cerca("oficina", cols, {}, lat, lon, radio_km, limite, con)


def aves(con, zona: str | None = None, presencia: str | None = None) -> list[dict]:
    """Especies destacadas. zona: 'Montaña', 'Zona Media', 'Pamplona', 'Ribera' (también salen
    las de 'Todas las zonas'). presencia: 'residente', 'estival', 'invernante'…"""
    where, params = ["true"], []
    if zona:
        where.append("(list_contains(zonas, ?) OR list_contains(zonas, 'Todas las zonas'))")
        params.append(zona)
    if presencia:
        where.append("presencia LIKE '%' || ? || '%'")
        params.append(presencia)
    sql = f"SELECT * EXCLUDE (licencia) FROM ave WHERE {' AND '.join(where)} ORDER BY nombre"
    return _filas(con, sql, params)


def _sin_tildes(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def buscar_recursos(
    con,
    texto: str | None = None,
    categoria: str | None = None,
    municipio: str | None = None,
    zona: str | None = None,
    estilo: str | None = None,
    limite: int = 20,
) -> list[dict]:
    """Filtros exactos y texto en nombre/descripción, sin tildes ni mayúsculas.

    La búsqueda por significado ("castillos medievales") llega con los embeddings (S2.4).
    """
    where, params = ["true"], []
    if texto:
        where.append(
            "(strip_accents(lower(nombre)) LIKE '%' || strip_accents(lower(?)) || '%' "
            "OR strip_accents(lower(descripcion)) LIKE '%' || strip_accents(lower(?)) || '%')"
        )
        params += [texto, texto]
    for col, valor in (("categoria", categoria), ("zona", zona)):
        if valor:
            where.append(f"{col} = ?")
            params.append(valor)
    for col, valor in (("municipio", municipio), ("estilo", estilo)):
        if valor:  # 'Románico' encuentra 'Gótico, Románico'
            where.append(f"strip_accents(lower({col})) LIKE '%' || strip_accents(lower(?)) || '%'")
            params.append(valor)
    sql = f"SELECT {_COLS_RECURSO} FROM recurso WHERE {' AND '.join(where)} ORDER BY nombre LIMIT ?"
    return _filas(con, sql, [*params, limite])


def recurso(con, id: str) -> dict | None:
    """Ficha completa de un recurso (descripción entera, imagen, web, enlaces)."""
    filas = _filas(con, "SELECT * FROM recurso WHERE id = ?", [id])
    return filas[0] if filas else None
