"""Tiempos y rutas reales con OSRM (infra/docker-compose.yml: coche :5000, a pie :5001)."""

import os

import httpx

SERVIDOR = {
    "coche": os.environ.get("OSRM_COCHE", "http://localhost:5000"),
    "pie": os.environ.get("OSRM_PIE", "http://localhost:5001"),
}
PERFIL = {
    "coche": "driving",
    "pie": "foot",
}  # osrm-routed ignora el perfil de la URL; es informativo

Punto = tuple[float, float]  # (lat, lon), como en el resto del proyecto


def _url(servicio: str, puntos: list[Punto], modo: str) -> str:
    coords = ";".join(f"{lon},{lat}" for lat, lon in puntos)  # OSRM usa lon,lat
    return f"{SERVIDOR[modo]}/{servicio}/v1/{PERFIL[modo]}/{coords}"


def _get(url: str, params: dict, client: httpx.Client | None) -> dict:
    r = (client or httpx).get(url, params=params, timeout=30)
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
    j = _get(_url("table", puntos, modo), {"annotations": "duration"}, client)
    return [[None if s is None else round(s / 60, 1) for s in fila] for fila in j["durations"]]
