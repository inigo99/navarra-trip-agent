"""navarra-opendata-mcp: los datos abiertos de turismo de Navarra como herramientas MCP.

Arranque (stdio): `uv --directory <ruta>/backend run navarra-mcp`. Las rutas a data/ son
relativas, por eso el --directory.
"""

from functools import cache

from fastmcp import FastMCP

from navarra_trip.tools import consultas, rutas, tiempo

mcp = FastMCP(
    "navarra-opendata",
    instructions=(
        "Monumentos, espacios naturales, alojamientos y restaurantes reales de Navarra "
        "(datosabiertos.navarra.es, CC BY 4.0), con descripciones de Wikipedia/Wikidata, rutas "
        "OSRM y previsión de Open-Meteo. Coordenadas siempre en (lat, lon). Cita solo lugares "
        "devueltos por estas herramientas, por su id, y enlaza url_fuente."
    ),
)


# todas solo leen; solo la previsión sale a un servicio externo (Open-Meteo). Sin las cuatro,
# los clientes no pueden avisar antes de llamar (y el directorio de OpenAI rechaza la herramienta)
LECTURA = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
LOCAL, EXTERNA = LECTURA | {"openWorldHint": False}, LECTURA | {"openWorldHint": True}


@cache
def _db():
    return consultas.conectar()


def _con():
    return consultas.preparar(_db().cursor())  # un cursor por llamada: DuckDB no es thread-safe


@mcp.tool(annotations=LOCAL)
def buscar_recursos(
    consulta: str | None = None,
    categoria: str | None = None,
    municipio: str | None = None,
    estilo: str | None = None,
    limite: int = 10,
) -> list[dict]:
    """Busca monumentos y espacios naturales. visitantes_12m (cuando hay dato) sirve para
    proponer alternativas menos concurridas.

    - consulta: texto libre por significado ("castillos medievales", "cascadas para ir con
      niños"). Si se da, ordena por similitud.
    - categoria: 'monumento', 'natural', 'bodega' (OSM) o 'ruta' (senderos homologados con
      duración y desnivel). municipio y estilo ('románico', 'gótico'…) filtran
      sin tildes. Sin consulta, devuelve los que cumplen los filtros por orden alfabético.
    """
    if consulta and not (municipio or estilo):
        try:
            from navarra_trip.tools.semantica import buscar_semantica

            return buscar_semantica(_con(), consulta, k=limite, categoria=categoria)
        except (ImportError, ValueError, FileNotFoundError):
            pass  # sin el extra 'semantica' o sin índice: búsqueda por texto
    return consultas.buscar_recursos(
        _con(),
        texto=consulta,
        categoria=categoria,
        municipio=municipio,
        estilo=estilo,
        limite=limite,
    )


@mcp.tool(annotations=LOCAL)
def recursos_cerca(
    lat: float,
    lon: float,
    radio_km: float = 10,
    categoria: str | None = None,
    subcategoria: str | None = None,
    limite: int = 20,
) -> list[dict]:
    """Monumentos ('monumento') y espacios naturales ('natural') a menos de radio_km en línea
    recta, del más cercano al más lejano. subcategoria: p. ej. 'Iglesias y ermitas',
    'Castillos/Palacios', 'Miradores', 'Cuevas'."""
    return consultas.recursos_cerca(_con(), lat, lon, radio_km, categoria, subcategoria, limite)


@mcp.tool(annotations=LOCAL)
def alojamientos_cerca(
    lat: float, lon: float, radio_km: float = 5, tipo: str | None = None, limite: int = 20
) -> list[dict]:
    """Alojamientos del Registro de Turismo de Navarra. tipo: 'apartamento', 'rural', 'hotel',
    'albergue' o 'camping'. geo_precision 'localidad' = ubicación aproximada (centro del pueblo)."""
    return consultas.alojamientos_cerca(_con(), lat, lon, radio_km, tipo, limite)


@mcp.tool(annotations=LOCAL)
def restaurantes_cerca(lat: float, lon: float, radio_km: float = 5, limite: int = 20) -> list[dict]:
    """Restaurantes del Registro de Turismo de Navarra, del más cercano al más lejano."""
    return consultas.restaurantes_cerca(_con(), lat, lon, radio_km, limite)


@mcp.tool(annotations=LOCAL)
def bares_cerca(lat: float, lon: float, radio_km: float = 1, limite: int = 50) -> list[dict]:
    """Bares, pubs y cafeterías (OpenStreetMap) y restaurantes con tapas y raciones (Registro de
    Turismo), del más cercano al más lejano. Para pintxos. Horarios casi nunca: confirmarlos."""
    return consultas.bares_cerca(_con(), lat, lon, radio_km, limite)


@mcp.tool(annotations=LOCAL)
def actividades_cerca(
    lat: float, lon: float, radio_km: float = 15, actividad: str | None = None, limite: int = 20
) -> list[dict]:
    """Empresas de turismo activo y cultural del Registro de Turismo. actividad filtra por lo que
    ofrecen ('kayak', 'bici', 'rutas', 'gastronómicas'…). Buenas alternativas si llueve."""
    return consultas.actividades_cerca(_con(), lat, lon, radio_km, actividad, limite)


@mcp.tool(annotations=LOCAL)
def oficinas_turismo(lat: float, lon: float, radio_km: float = 50) -> list[dict]:
    """Oficinas de turismo más cercanas (teléfono, email y web oficiales)."""
    return consultas.oficinas_cerca(_con(), lat, lon, radio_km)


@mcp.tool(annotations=LOCAL)
def aves(zona: str | None = None, presencia: str | None = None) -> list[dict]:
    """Aves destacadas de Navarra (turismo ornitológico): especie, cuándo está (presencia:
    'residente', 'estival', 'invernante'), dificultad de observación y zonas ('Montaña',
    'Zona Media', 'Pamplona', 'Ribera'). Para lugares, buscar_recursos('observatorio de aves')."""
    return consultas.aves(_con(), zona, presencia)


@mcp.tool(annotations=LOCAL)
def recurso(id: str) -> dict | None:
    """Ficha completa de un recurso por su id (p. ej. 'mon:3153'): descripción entera, fuente
    de la descripción, imagen, web, horario si lo hay (si no, 'consultar horario')."""
    return consultas.recurso(_con(), id)


@mcp.tool(annotations=LOCAL)
def ruta(puntos: list[list[float]], modo: str = "coche") -> dict:
    """Distancia y tiempo reales por carretera (OSRM) recorriendo los puntos en orden.
    puntos: [[lat, lon], ...], al menos 2. modo: 'coche' o 'pie'. Devuelve km, minutos y tramos."""
    return rutas.ruta([tuple(p) for p in puntos], modo)


@mcp.tool(annotations=EXTERNA)
def prevision_tiempo(lat: float, lon: float, dias: int = 7) -> list[dict]:
    """Previsión diaria (Open-Meteo, hasta 16 días): descripción, máx./mín., lluvia y
    mal_tiempo=true cuando conviene proponer planes bajo techo."""
    return tiempo.prevision(lat, lon, dias)


@mcp.tool(annotations=LOCAL)
def conjuntos() -> list[dict]:
    """Qué datos hay cargados: tabla, número de filas, fuente y licencia."""
    con = _con()
    return [
        {
            "tabla": t,
            "filas": con.execute(f"SELECT count(*) FROM {t}").fetchone()[0],
            "fuente": con.execute(f"SELECT any_value(url_fuente) FROM {t}").fetchone()[0],
            "licencia": "CC BY 4.0 (Gobierno de Navarra)",
        }
        for t in ("recurso", "alojamiento", "restaurante", "actividad", "oficina", "ave")
    ]


def main() -> None:
    mcp.run()  # stdio, lo que espera Claude Desktop
