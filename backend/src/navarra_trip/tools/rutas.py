"""Tiempos y rutas reales con OSRM (infra/docker-compose.yml: coche :5000, a pie :5001).

Sin servidor OSRM (la API en Vercel), con NAVARRA_TIEMPOS=data/tiempos.npz: tiempos y distancias
precalculados con `navarra-tiempos` entre todos los recursos y pueblos base, y la línea del mapa
del OSRM público de FOSSGIS (si no responde, el plan sale igual, sin la línea).
"""

import math
import os
import sys
import time
from functools import cache
from pathlib import Path

import httpx
import numpy as np

from navarra_trip.ingest.geocode import km

SERVIDOR = {
    "coche": os.environ.get("OSRM_COCHE", "http://localhost:5000"),
    "pie": os.environ.get("OSRM_PIE", "http://localhost:5001"),
}
PERFIL = {
    "coche": "driving",
    "pie": "foot",
}  # osrm-routed ignora el perfil de la URL; es informativo
TIEMPOS = os.environ.get("NAVARRA_TIEMPOS")  # None: OSRM local
# uso moderado y con identificación: https://routing.openstreetmap.de/about.html
PUBLICO = {
    "coche": "https://routing.openstreetmap.de/routed-car",
    "pie": "https://routing.openstreetmap.de/routed-foot",
}
AGENTE = {"User-Agent": "navarra-trip-agent (https://github.com/inigo99/navarra-trip-agent)"}

Punto = tuple[float, float]  # (lat, lon), como en el resto del proyecto


def _url(servicio: str, puntos: list[Punto], modo: str, servidor: str | None = None) -> str:
    coords = ";".join(f"{lon},{lat}" for lat, lon in puntos)  # OSRM usa lon,lat
    return f"{servidor or SERVIDOR[modo]}/{servicio}/v1/{PERFIL[modo]}/{coords}"


def _get(url: str, params: dict, client: httpx.Client | None, timeout: float = 30) -> dict:
    r = (client or httpx).get(url, params=params, timeout=timeout, headers=AGENTE)
    r.raise_for_status()
    j = r.json()
    if j.get("code") != "Ok":
        raise ValueError(f"OSRM: {j.get('code')} {j.get('message', '')}")
    return j


def ruta(
    puntos: list[Punto],
    modo: str = "coche",
    geometria: bool = False,
    client: httpx.Client | None = None,
) -> dict:
    """Ruta en el orden dado. km y minutos totales y por tramo; GeoJSON si geometria=True."""
    if TIEMPOS:
        return _ruta_precalculada(puntos, modo, geometria, client)
    params = {"overview": "full" if geometria else "false", "geometries": "geojson"}
    r = _get(_url("route", puntos, modo), params, client)["routes"][0]
    out = {
        "km": round(r["distance"] / 1000, 1),
        "minutos": round(r["duration"] / 60),
        "tramos": [
            {"km": round(t["distance"] / 1000, 1), "minutos": round(t["duration"] / 60)}
            for t in r["legs"]
        ],
    }
    if geometria:
        out["geometria"] = r["geometry"]
    return out


def matriz(
    puntos: list[Punto], modo: str = "coche", client: httpx.Client | None = None
) -> list[list[float | None]]:
    """Minutos de cada punto a cada otro (None si no hay camino). Para ordenar las paradas."""
    if TIEMPOS:
        return [[_redondo(_par(a, b, modo)[0]) for b in puntos] for a in puntos]
    j = _get(_url("table", puntos, modo), {"annotations": "duration"}, client)
    return [[None if s is None else round(s / 60, 1) for s in fila] for fila in j["durations"]]


# ---------- tiempos precalculados (sin OSRM) ----------

DESVIO = {"coche": 1.35, "pie": 1.25}  # carretera / línea recta, para puntos fuera de la tabla
KMH = {"coche": 45, "pie": 4.5}


def _clave(p: Punto) -> tuple[float, float]:
    return round(float(p[0]), 5), round(float(p[1]), 5)  # ~1 m: lo que da DuckDB, sin ruido


@cache
def _tablas(archivo: str) -> tuple[dict, dict]:
    with np.load(archivo) as z:
        indice = {_clave(p): i for i, p in enumerate(z["puntos"])}
        return indice, {m: (z[f"{m}_min"], z[f"{m}_km"]) for m in ("coche", "pie")}


def _par(a: Punto, b: Punto, modo: str) -> tuple[float | None, float | None]:
    """(minutos, km) de a a b; (None, None) si no hay camino."""
    indice, tablas = _tablas(TIEMPOS)
    i, j = indice.get(_clave(a)), indice.get(_clave(b))
    if i is None or j is None:
        # ponytail: estimación en línea recta; si avisa a menudo, regenerar con navarra-tiempos
        print(f"aviso: {a if i is None else b} sin tiempo precalculado; se estima", file=sys.stderr)
        return _estimado(a, b, modo)
    minutos, kms = tablas[modo]
    m = float(minutos[i, j])
    return (None, None) if math.isnan(m) else (m, float(kms[i, j]))


def _estimado(a: Punto, b: Punto, modo: str) -> tuple[float, float]:
    d = km(a[1], a[0], b[1], b[0]) * DESVIO[modo]
    return d / KMH[modo] * 60, d


def _redondo(x: float | None, n: int = 1) -> float | None:
    return None if x is None else round(x, n)


def _ruta_precalculada(puntos, modo, geometria, client) -> dict:
    tramos = [
        p if (p := _par(a, b, modo))[0] is not None else _estimado(a, b, modo)
        for a, b in zip(puntos, puntos[1:], strict=False)
    ]
    out = {
        "km": round(sum(k for _, k in tramos), 1),
        "minutos": round(sum(m for m, _ in tramos)),
        "tramos": [{"km": round(k, 1), "minutos": round(m)} for m, k in tramos],
    }
    if geometria:
        out["geometria"] = None
        try:
            params = {"overview": "full", "geometries": "geojson"}
            url = _url("route", puntos, "coche", PUBLICO[modo])  # FOSSGIS: perfil "driving" fijo
            out["geometria"] = _get(url, params, client, timeout=10)["routes"][0]["geometry"]
        except (httpx.HTTPError, ValueError, KeyError) as e:
            print(f"aviso: sin línea de la ruta ({type(e).__name__})", file=sys.stderr)
    return out


def puntos_precalculo(con) -> list[Punto]:
    """Los recursos y todos los pueblos que puede devolver planner.lugar (misma mediana)."""
    filas = con.execute(
        """SELECT lat, lon FROM recurso WHERE lat IS NOT NULL
        UNION SELECT median(lat), median(lon) FROM alojamiento WHERE lat IS NOT NULL
          GROUP BY localidad
        UNION SELECT median(lat), median(lon) FROM recurso WHERE lat IS NOT NULL
          GROUP BY municipio"""
    ).fetchall()
    return list({_clave(p): p for p in filas}.values())


def precalcular(
    puntos: list[Punto], destino: Path, bloque: int = 100, client: httpx.Client | None = None
) -> None:
    """Matrices de minutos y km con el OSRM local, en bloques de bloque×bloque (el límite por
    defecto de osrm-routed es 100×100 por consulta)."""
    n = len(puntos)
    out = {}
    for modo in ("coche", "pie"):
        mins, kms = np.full((n, n), np.nan, np.float32), np.full((n, n), np.nan, np.float32)
        for i in range(0, n, bloque):
            for j in range(0, n, bloque):
                a, b = puntos[i : i + bloque], puntos[j : j + bloque]
                params = {
                    "sources": ";".join(map(str, range(len(a)))),
                    "destinations": ";".join(map(str, range(len(a), len(a) + len(b)))),
                    "annotations": "duration,distance",
                }
                t = _get(_url("table", a + b, modo), params, client, timeout=120)
                d = np.array(t["durations"], dtype=np.float64)  # None -> nan
                s = np.array(t["distances"], dtype=np.float64)
                mins[i : i + len(a), j : j + len(b)] = d / 60
                kms[i : i + len(a), j : j + len(b)] = s / 1000
        out[f"{modo}_min"], out[f"{modo}_km"] = mins, kms
    destino.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(destino, puntos=np.array(puntos, dtype=np.float64), **out)


def main() -> None:
    """`navarra-tiempos`: data/tiempos.npz para desplegar sin OSRM (OSRM local levantado)."""
    from navarra_trip.tools.consultas import conectar

    destino = Path("data/tiempos.npz")
    puntos = puntos_precalculo(conectar())
    t0 = time.perf_counter()
    precalcular(puntos, destino)
    mb = destino.stat().st_size / 1e6
    print(f"{len(puntos)} puntos, {time.perf_counter() - t0:.0f} s -> {destino} ({mb:.1f} MB)")
