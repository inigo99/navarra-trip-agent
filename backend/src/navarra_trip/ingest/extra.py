"""Conjuntos añadidos tras la v1: agroturismos, empresas de actividades, oficinas de turismo,
aves (turismo ornitológico) y afluencia de visitantes. Más el municipio que les falta a
algunos recursos (sierras, miradores), sacado de los límites municipales de IDENA."""

import csv
import re
from datetime import date
from pathlib import Path

from pyproj import Transformer

from navarra_trip.ingest.wikidata import Cliente

AFLUENCIA_MANUAL = Path(__file__).with_name("afluencia_recursos.csv")
WFS_MUNICIPIOS = "https://idena.navarra.es/ogc/wfs"
_A_UTM = Transformer.from_crs("EPSG:4326", "EPSG:25830", always_xy=True)


def agroturismos_como_alojamientos(raw: dict) -> dict:
    """Mismo formato que 'Alojamientos inscritos' (allí no vienen los agroturismos)."""
    regs = [
        r | {"COD_INSCRIPCION": r["COD_REGISTRO"], "SUB_ZONA": r["SUBZONA"], "PLAZAS": None}
        for r in raw["registros"]
    ]
    return raw | {"registros": regs}


def actividades_extra(raw_reg: dict) -> dict:
    tipo = raw_reg["TIPO"]
    acts = raw_reg["ACTIVIDADES"]
    return {
        "modalidad": raw_reg["MODALIDAD"],
        "tipo": None if tipo == "Desconocido" else tipo,
        "actividades": None if acts == "Desconocido" else [a.strip() for a in acts.split(",")],
    }


def oficinas(raw: dict) -> list[dict]:
    filas = []
    for i, f in enumerate(raw["registros"], 1):
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        filas.append(
            {
                "id": f"ofi:{i}",
                "nombre": p["RECTUR"],
                "zona": p["ZONATUR"],
                "direccion": p["DIRECCION"],
                "localidad": p["POBLACION"],
                "telefono": str(p["TELEFONO"]) if p["TELEFONO"] else None,
                "email": p["EMAIL"] or None,
                "web": p["URL"] or None,
                "lon": lon,
                "lat": lat,
                "url_fuente": raw["url_fuente"],
                "licencia": raw["licencia"],
            }
        )
    return filas


def aves(raw: dict) -> list[dict]:
    """Especies (sin coordenadas: el conjunto no trae lugares). Zonas y época, de su texto."""
    filas = []
    for r in raw["registros"]:
        m = re.match(r"(.+?)\s*\((.+)\)$", r["Nombre"].strip())
        texto = (r["DescripImpresion"] or "").replace("\\n", "\n")
        texto = re.sub(r"<br\s*/?>|</?p[^>]*>|</?div[^>]*>", "\n", texto)  # viene con HTML
        texto = re.sub(r"<[^>]+>|&nbsp;", " ", texto)
        texto = re.sub(r"[ \t]+", " ", re.sub(r"\s*\n\s*", "\n", texto)).strip()
        zonas = re.search(r"Zonas?:\s*(.+?)\.?\s*$", texto, re.M)
        desc = re.sub(r"Zonas?:.*", "", texto).strip()
        filas.append(
            {
                "id": f"ave:{r['CodRecurso']}",
                "nombre": m[1] if m else r["Nombre"],
                "cientifico": m[2] if m else None,
                "presencia": r["Caracter"].rstrip("."),  # residente, estival, invernante...
                "observacion": r["Observacion"].rstrip("."),
                "zonas": [z[0].upper() + z[1:] for z in re.split(r",\s*|\s+y\s+", zonas[1])]
                if zonas
                else [],
                "descripcion": desc,
                "url_fuente": raw["url_fuente"],
                "licencia": raw["licencia"],
            }
        )
    return list({f["id"]: f for f in filas}.values())


def leer_afluencia_manual(ruta: Path = AFLUENCIA_MANUAL) -> dict[str, str]:
    """Nombre en el conjunto de afluencia -> id del recurso (vacío = no es uno de los nuestros)."""
    with ruta.open(encoding="utf-8") as f:
        return {
            r["nombre_afluencia"]: r["recurso_id"] for r in csv.DictReader(f) if r["recurso_id"]
        }


def afluencia(raw: dict, mapa: dict[str, str]) -> list[dict]:
    """Visitantes por recurso y mes. Varios contadores de un recurso se suman por mes."""
    suma: dict[tuple[str, str], int] = {}
    for r in raw["registros"]:
        if (rid := mapa.get(r["RECURSO"])) and r["NUM_VISITANTES"]:
            clave = (rid, r["MES"][:10])
            suma[clave] = suma.get(clave, 0) + int(float(r["NUM_VISITANTES"]))
    return [
        {"recurso_id": rid, "mes": date.fromisoformat(mes), "visitantes": v}
        for (rid, mes), v in sorted(suma.items())
    ]


def visitantes_12m(recursos: list[dict], filas: list[dict]) -> None:
    """recurso['visitantes_12m'] = visitantes de los 12 últimos meses con datos (o None)."""
    ultimo: dict[str, date] = {}
    for f in filas:
        ultimo[f["recurso_id"]] = max(ultimo.get(f["recurso_id"], f["mes"]), f["mes"])
    tot: dict[str, int] = {}
    for f in filas:
        u = ultimo[f["recurso_id"]]
        if (u.year - f["mes"].year) * 12 + u.month - f["mes"].month < 12:
            tot[f["recurso_id"]] = tot.get(f["recurso_id"], 0) + f["visitantes"]
    for r in recursos:
        r["visitantes_12m"] = tot.get(r["id"])


def municipio(cli: Cliente, lon: float, lat: float) -> str | None:
    """Municipio que contiene el punto, según los límites de IDENA (WFS en EPSG:25830)."""
    x, y = _A_UTM.transform(lon, lat)
    j = cli.get(
        WFS_MUNICIPIOS,
        service="WFS",
        version="2.0.0",
        request="GetFeature",
        typeNames="IDENA:CATAST_Pol_Municipio",
        outputFormat="application/json",
        propertyName="MUNICIPIO",
        cql_filter=f"CONTAINS(the_geom,POINT({x:.0f} {y:.0f}))",
    )
    return j["features"][0]["properties"]["MUNICIPIO"] if j["features"] else None


def completar_municipios(recursos: list[dict], cli: Cliente) -> int:
    n = 0
    for r in recursos:
        if (
            not r["municipio"]
            and r["lon"] is not None
            and (m := municipio(cli, r["lon"], r["lat"]))
        ):
            r["municipio"] = m
            n += 1
    return n
