"""Mapa de control: todos los puntos de data/navarra.duckdb en un HTML (Leaflet + OSM)."""

import json
from pathlib import Path

import duckdb

from navarra_trip.ingest.normaliza import DB

MAPA = Path("data/mapa.html")
CONSULTA = """
SELECT 'recurso' AS capa, id, nombre, categoria AS detalle, 'fuente' AS precision, lon, lat
FROM recurso
UNION ALL
SELECT 'alojamiento', id, nombre, modalidad, geo_precision, lon, lat FROM alojamiento
UNION ALL
SELECT 'restaurante', id, nombre, especialidad, geo_precision, lon, lat FROM restaurante
"""
# Sin coordenadas (2 espacios hasta S2) no se pintan
CONSULTA = f"SELECT * FROM ({CONSULTA}) WHERE lon IS NOT NULL"

HTML = """<!doctype html><meta charset="utf-8"><title>navarra-trip-agent · mapa de control</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>html,body,#m{height:100%;margin:0}</style><div id="m"></div><script>
const fc = __DATOS__;
const color = {recurso: "#d62728", alojamiento: "#1f77b4", restaurante: "#2ca02c"};
const m = L.map("m", {preferCanvas: true}).setView([42.7, -1.65], 9);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
  {maxZoom: 19, attribution: "© OpenStreetMap · datosabiertos.navarra.es (CC BY 4.0)"}).addTo(m);
const capas = {};
for (const f of fc.features) {
  const p = f.properties, [lon, lat] = f.geometry.coordinates;
  const exacto = p.precision !== "localidad" && p.precision !== "municipio";
  (capas[p.capa + (exacto ? "" : " (aprox.)")] ??= L.layerGroup().addTo(m)).addLayer(
    L.circleMarker([lat, lon], {radius: p.capa === "recurso" ? 6 : 4, color: color[p.capa],
      fillOpacity: exacto ? 0.8 : 0.2, weight: 1})
     .bindPopup(`<b>${p.nombre}</b><br>${p.detalle ?? ""}<br>${p.id} · ${p.precision}`));
}
L.control.layers(null, capas, {collapsed: false}).addTo(m);
</script>"""


def geojson(con: duckdb.DuckDBPyConnection) -> dict:
    filas = con.sql(CONSULTA).fetchall()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                "properties": {"capa": c, "id": i, "nombre": n, "detalle": d, "precision": p},
            }
            for c, i, n, d, p, lon, lat in filas
        ],
    }


def main() -> None:
    """`navarra-mapa`: escribe data/mapa.html; ábrelo en el navegador."""
    with duckdb.connect(DB, read_only=True) as con:
        fc = geojson(con)
    MAPA.write_text(HTML.replace("__DATOS__", json.dumps(fc, ensure_ascii=False)), encoding="utf-8")
    print(f"{len(fc['features'])} puntos -> {MAPA.resolve()}")
